"""Capa de canal: hablar con el panel COUGAR CFV235 por /dev/hidraw.

Aqui vive la pregunta que el kit deja abierta y que este proyecto resuelve midiendo: **como
hay que escribirle al panel en Linux**.

    El descriptor HID del panel (legible en /sys) declara, SIN ningun item Report ID:
        Usage Page 0xFF00, Usage 1, Report Size 8, Report Count 0x400 -> Input   1024 B
        Usage Page 0xFF00, Usage 1, Report Size 8, Report Count 0x400 -> Output  1024 B

    Es decir: informes de 1024 bytes y sin byte de report ID.

    Pero la API HID de Windows exige anteponer SIEMPRE el byte de report ID (0x00 cuando el
    dispositivo no usa reportes numerados), de ahi los 1025 bytes que usan tanto el kit
    cfv-235 como cougarLCD.cpp (que ademas tolera que hidapi escriba 1024 en Linux:
    `written == size - 1`). En hidraw, escribir 1025 a un dispositivo de 1024 puede dar
    EINVAL -- que es justo el error que el kit documenta como misterio en Linux.

En vez de elegir a ciegas, `Canal.autonegociar()` manda un `conn` (inocuo) con cada variante
razonable y **se queda con la que el panel contesta**. El resultado se puede fijar con
`variante=` para no repetir el sondeo en cada arranque.
"""

from __future__ import annotations

import contextlib
import errno
import glob
import os
import select
import threading
import time
from dataclasses import dataclass

from . import protocolo as p


# ------------------------------------------------------------------ errores
class ErrorCanal(RuntimeError):
    """Problema de comunicacion con el panel."""


class SinPermisos(ErrorCanal):
    """El dispositivo existe pero no se puede abrir."""


class SinPanel(ErrorCanal):
    """No aparece ningun hidraw con el VID/PID del panel."""


class PanelOcupado(ErrorCanal):
    """Otro proceso de la app (o el editor de COUGAR) esta usando el panel."""


# ------------------------------------------------------------------ bloqueo exclusivo
def _ruta_bloqueo(dispositivo: str) -> str:
    base = os.path.basename(dispositivo).replace("/", "_")
    runtime = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    carpeta = os.path.join(runtime, "cfv235")
    try:
        os.makedirs(carpeta, mode=0o700, exist_ok=True)
    except OSError:
        carpeta = os.environ.get("TMPDIR", "/tmp")
    return os.path.join(carpeta, f"panel-{base}.lock")


# Bloqueos ya tomados por ESTE proceso: ruta del lock -> [fd, veces tomado].
# Hace falta porque `flock` cuenta los descriptores, no los procesos: si el mismo programa
# abre dos veces el panel (por ejemplo, un ayudante que se abre y se cierra), el segundo
# `flock` se bloquearia a si mismo y pareceria que "otro proceso" tiene el panel.
_locks_propios: dict[str, list] = {}


def tomar_bloqueo(dispositivo: str, esperar: float = 0.0):
    """Toma un bloqueo exclusivo del panel. Devuelve un testigo (o lanza PanelOcupado).

    El panel mantiene UNA sola sesion: si dos procesos le escriben a la vez, las respuestas
    se cruzan y todo parece "rechazar el handshake" sin motivo. El propio kit lo avisa
    ("solo un programa puede hablar con el panel a la vez"); aqui se garantiza.
    Suele ser el editor de COUGAR o un dashboard ya en marcha.

    Es reentrante dentro del mismo proceso: el segundo intento devuelve el mismo testigo.
    """
    import fcntl
    ruta = _ruta_bloqueo(dispositivo)
    propio = _locks_propios.get(ruta)
    if propio is not None:
        propio[1] += 1
        return ruta
    fd = os.open(ruta, os.O_RDWR | os.O_CREAT, 0o600)
    limite = time.time() + max(0.0, esperar)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            _locks_propios[ruta] = [fd, 1]
            return ruta
        except OSError:
            if time.time() >= limite:
                os.close(fd)
                dueno = ""
                try:
                    with open(ruta, encoding="utf-8") as fh:
                        dueno = fh.read().strip()
                except OSError:
                    pass
                raise PanelOcupado(
                    f"otro proceso esta usando {dispositivo}"
                    + (f" (pid {dueno})" if dueno else "")
                    + ". El panel solo admite una sesion: para el dashboard "
                      "(`systemctl --user stop cfv235-dashboard`), cierra el editor de "
                      "COUGAR, o espera a que termine."
                ) from None
            time.sleep(0.2)


def soltar_bloqueo(testigo) -> None:
    """Suelta el bloqueo. Solo se libera de verdad cuando ya nadie lo tiene tomado."""
    if testigo is None:
        return
    entrada = _locks_propios.get(testigo)
    if entrada is None:
        return
    entrada[1] -= 1
    if entrada[1] > 0:
        return
    fd = entrada[0]
    _locks_propios.pop(testigo, None)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


# ------------------------------------------------------------------ variantes de escritura
@dataclass(frozen=True)
class Variante:
    """Como se empaqueta una trama en un informe HID."""
    nombre: str
    informe: int          # tamano total del informe (0 = escribir la trama tal cual)
    prefijo: bool         # anteponer el byte de report ID 0x00
    relleno: bool         # rellenar con ceros hasta `informe`

    def describe(self) -> str:
        if self.informe == 0:
            return f"{self.nombre}: escritura exacta" + (" con 0x00 delante" if self.prefijo else "")
        return (f"{self.nombre}: {self.informe} B"
                + (" con 0x00 delante" if self.prefijo else " sin report ID")
                + ("" if self.relleno else " (sin relleno)"))


# Ordenadas por probabilidad en Linux: primero lo que dice el descriptor.
VARIANTES = (
    Variante("descriptor", p.INFORME_DATOS, False, True),        # 1024, sin report ID
    Variante("windows", p.INFORME_WINDOWS, True, True),          # 1025, 0x00 + 1024
    Variante("exacto", 0, False, False),                         # la trama, sin nada mas
    Variante("exacto-id", 0, True, False),                       # 0x00 + la trama
    Variante("bloque64", 64, False, True),                       # relleno a multiplo de 64
)


# ------------------------------------------------------------------ sysfs
def _hid_id(ruta: str) -> tuple[int | None, int | None, dict]:
    """(vid, pid, datos) de un /dev/hidrawN leyendo sysfs."""
    base = os.path.basename(ruta)
    datos: dict[str, str] = {}
    try:
        with open(f"/sys/class/hidraw/{base}/device/uevent", encoding="utf-8") as fh:
            for linea in fh:
                if "=" in linea:
                    clave, valor = linea.strip().split("=", 1)
                    datos[clave] = valor
    except OSError:
        return None, None, {}
    vid = pid = None
    if "HID_ID" in datos:
        partes = datos["HID_ID"].split(":")
        if len(partes) == 3:
            vid, pid = int(partes[1], 16), int(partes[2], 16)
    return vid, pid, datos


def listar(patron: str = "/dev/hidraw*") -> list[dict]:
    """Todos los hidraw del sistema con su VID/PID y su nombre."""
    salida = []
    for ruta in sorted(glob.glob(patron)):
        vid, pid, datos = _hid_id(ruta)
        salida.append({"ruta": ruta, "vid": vid, "pid": pid,
                       "nombre": datos.get("HID_NAME", ""),
                       "serie": datos.get("HID_UNIQ", ""),
                       "fisico": datos.get("HID_PHYS", "")})
    return salida


def buscar(vid: int = p.VID, pid: int = p.PID, patron: str = "/dev/hidraw*") -> str | None:
    """Ruta del hidraw del panel, o None."""
    for d in listar(patron):
        if d["vid"] == vid and d["pid"] == pid:
            return d["ruta"]
    return None


def analizar_descriptor(datos: bytes) -> dict:
    """Mini-parser del report descriptor HID: usage page, report IDs y tamanos."""
    info = {"bytes": len(datos), "usage_page": None, "usage": None,
            "report_ids": [], "informes": []}
    i = 0
    tam = cuenta = None
    while i < len(datos):
        b = datos[i]
        if b == 0xFE:                                        # long item
            largo = datos[i + 1] if i + 1 < len(datos) else 0
            i += 3 + largo
            continue
        n = b & 0x03
        n = 4 if n == 3 else n
        tipo = (b >> 2) & 0x03                               # 1 global, 2 local, 0 main
        etiqueta = (b >> 4) & 0x0F
        crudo = datos[i + 1:i + 1 + n]
        valor = int.from_bytes(crudo, "little") if crudo else 0
        if tipo == 1:
            if etiqueta == 0x0:
                info["usage_page"] = valor
            elif etiqueta == 0x7:
                tam = valor
            elif etiqueta == 0x8:
                info["report_ids"].append(valor)
            elif etiqueta == 0x9:
                cuenta = valor
        elif tipo == 2 and etiqueta == 0x0:
            info["usage"] = valor
        elif tipo == 0:
            clase = {0x8: "entrada", 0x9: "salida", 0xB: "feature"}.get(etiqueta)
            if clase and tam and cuenta:
                info["informes"].append({"clase": clase, "bytes": tam * cuenta // 8})
            if clase:
                cuenta = None
        i += 1 + n
    return info


def leer_descriptor(ruta: str) -> bytes | None:
    """Report descriptor del dispositivo, si el sistema lo deja leer."""
    base = os.path.basename(ruta)
    for candidata in (f"/sys/class/hidraw/{base}/device/report_descriptor",
                      f"/sys/class/hidraw/{base}/device/../report_descriptor"):
        try:
            with open(candidata, "rb") as fh:
                datos = fh.read()
            if datos:
                # sysfs lo rellena con ceros hasta 4096: recortamos al descriptor real
                fin = len(datos)
                while fin > 0 and datos[fin - 1] == 0:
                    fin -= 1
                return datos[:fin]
        except OSError:
            continue
    return None


def info_dispositivo(ruta: str) -> dict:
    """Todo lo que se puede saber del panel sin escribirle nada."""
    vid, pid, datos = _hid_id(ruta)
    descriptor = leer_descriptor(ruta)
    info = {
        "ruta": ruta, "vid": vid, "pid": pid,
        "nombre": datos.get("HID_NAME", ""),
        "serie": datos.get("HID_UNIQ", ""),
        "fisico": datos.get("HID_PHYS", ""),
        "driver": datos.get("DRIVER", ""),
        "descriptor": descriptor.hex() if descriptor else None,
        "descriptor_analizado": analizar_descriptor(descriptor) if descriptor else None,
    }
    # datos del dispositivo USB: desde el nodo HID se sube por la interfaz hasta el
    # dispositivo USB, que es donde viven idVendor, manufacturer, bcdDevice, bMaxPower...
    try:
        base = os.path.realpath(f"/sys/class/hidraw/{os.path.basename(ruta)}/device")
        usb = base
        for _ in range(5):                   # .../1-7.1:1.0/0003:VID:PID.N -> .../1-7.1
            usb = os.path.dirname(usb)
            if os.path.exists(os.path.join(usb, "idVendor")):
                break
        info["usb_ruta"] = usb
        for clave, fichero in (("usb_vid", "idVendor"), ("usb_pid", "idProduct"),
                               ("usb_manufacturer", "manufacturer"),
                               ("usb_product", "product"),
                               ("usb_serial", "serial"),
                               ("usb_version", "version"),
                               ("usb_bcddevice", "bcdDevice"),
                               ("usb_maxpower", "bMaxPower"),
                               ("usb_puertos", "bNumInterfaces")):
            try:
                with open(os.path.join(usb, fichero), encoding="utf-8") as fh:
                    info[clave] = fh.read().strip()
            except OSError:
                pass
    except OSError:
        pass
    return info


# ------------------------------------------------------------------ canal
class Canal:
    """Descriptor abierto del panel, con la variante de escritura ya negociada."""

    def __init__(self, ruta: str | None = None, variante: Variante | None = None,
                 timeout: float = 3.0, verboso: bool = False, sin_bloqueo: bool = False,
                 esperar_bloqueo: float = 0.0):
        self.ruta = ruta
        self.variante = variante
        self.timeout = timeout
        self.verboso = verboso
        self.sin_bloqueo = sin_bloqueo
        self.esperar_bloqueo = esperar_bloqueo
        self.fd: int | None = None
        self._lock = None
        self._cerrado = False
        # El bloqueo del panel (flock) es ENTRE procesos y reentrante dentro de uno, asi que
        # no serializa hilos del mismo programa: de eso se encarga este candado. Sin el, dos
        # hilos pueden entrelazar peticiones y adjudicarse respuestas cruzadas.
        self._mutex = threading.RLock()
        self._buffer = bytearray()
        self._envios = 0
        self._ultima = b""
        self._ultima_envios = -1
        self.negociacion: list[dict] = []

    # -------------------------------------------------------------- abrir/cerrar
    def asegurar_abierto(self) -> None:
        """Abre el canal si nunca se abrio, pero NO resucita uno cerrado a proposito.

        Antes, `_escribir`/`leer_trama` abrian el descriptor si estaba a `None`. Eso hacia
        que un `Panel` ya cerrado volviera a abrir el hidraw por detras de quien lo posee
        (el dueño compartido de la app), con dos sesiones sobre el mismo panel: justo lo que
        el bloqueo exclusivo existe para evitar. Ahora cerrar es cerrar.
        """
        if self.fd is not None:
            return
        if self._cerrado:
            raise ErrorCanal(
                "el canal esta cerrado; vuelve a llamar a abrir() antes de usarlo"
            )
        self.abrir()

    def abrir(self) -> "Canal":
        if self.fd is not None:
            return self
        ruta = self.ruta or buscar()
        if not ruta:
            raise SinPanel(f"no aparece el panel {p.VID:#06x}:{p.PID:#06x}. "
                           f"¿esta conectado y con el cable de datos?")
        self.ruta = ruta
        try:
            self.fd = os.open(ruta, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as exc:
            raise SinPermisos(
                f"no puedo abrir {ruta}: {exc}. Sin sudo: instala la regla udev\n"
                f"  sudo install -Dm644 udev/90-cougar-lcd.rules /etc/udev/rules.d/"
                f"90-cougar-lcd.rules\n"
                f"  sudo udevadm control --reload-rules && sudo udevadm trigger"
            ) from exc
        except OSError as exc:
            raise ErrorCanal(f"no puedo abrir {ruta}: {exc}") from exc
        if not self.sin_bloqueo:
            try:
                self._lock = tomar_bloqueo(ruta, self.esperar_bloqueo)
            except ErrorCanal:
                os.close(self.fd)
                self.fd = None
                raise
        self._cerrado = False
        return self

    def cerrar(self) -> None:
        soltar_bloqueo(self._lock)
        self._lock = None
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None
        self._cerrado = True

    def __enter__(self) -> "Canal":
        return self.abrir()

    def __exit__(self, *_) -> None:
        self.cerrar()

    # -------------------------------------------------------------- escritura
    def _empaquetar(self, trama: bytes, variante: Variante | None = None) -> bytes:
        v = variante or self.variante
        if v is None:
            raise ErrorCanal("no hay variante de escritura negociada; llama a autonegociar()")
        datos = (b"\x00" + trama) if v.prefijo else trama
        if v.informe:
            if len(datos) > v.informe:
                raise ErrorCanal(f"la trama ({len(datos)} B) no cabe en el informe "
                                 f"de {v.informe} B")
            if v.relleno and len(datos) < v.informe:
                datos = datos + b"\x00" * (v.informe - len(datos))
        return datos

    def _escribir(self, datos: bytes) -> int:
        """Escribe todo `datos`, reintentando si el descriptor esta lleno.

        El descriptor se abre en O_NONBLOCK, asi que `os.write` puede devolver EAGAIN
        (BlockingIOError) o una escritura corta. Con el panel real no se nota, pero el PTY
        del simulador tiene poco buffer: al mandar un informe de medios grande se pierde la
        subida. Aqui se espera con `select` a que se pueda escribir y se continua donde se
        quedo; si se agota el tiempo, se avisa con un error claro.
        """
        self.asegurar_abierto()
        pendiente = datos
        limite = time.time() + max(self.timeout, 2.0)
        while pendiente:
            try:
                escritos = os.write(self.fd, pendiente)
            except BlockingIOError:
                restante = limite - time.time()
                if restante <= 0:
                    raise ErrorCanal(
                        f"el panel no acepta la escritura ({len(datos)} B): el descriptor "
                        f"sigue lleno despues de {max(self.timeout, 2.0):.0f} s"
                    ) from None
                _, listos, _ = select.select([], [self.fd], [], restante)
                if not listos:
                    raise ErrorCanal("tiempo agotado esperando a poder escribir en el panel")
                continue
            except OSError as exc:
                if exc.errno == errno.EINVAL:
                    raise ErrorCanal(
                        f"el kernel rechaza una escritura de {len(datos)} B (EINVAL). "
                        f"El descriptor del panel declara informes de "
                        f"{p.INFORME_DATOS} B sin report ID: prueba la variante 'descriptor'."
                    ) from exc
                if exc.errno in (errno.ENODEV, errno.EIO, errno.ESHUTDOWN):
                    raise ErrorCanal(
                        f"el panel se ha desconectado o se esta reiniciando ({exc})") from exc
                raise
            if escritos <= 0:
                raise ErrorCanal(f"escritura nula ({escritos} B) al panel")
            pendiente = pendiente[escritos:]
        self._envios += 1
        if self.verboso:
            print(f"    tx {len(datos)} B: {datos[:12].hex()}...")
        return len(datos)

    def enviar(self, trama: bytes) -> None:
        """Escribe una trama de control con la variante negociada."""
        self._escribir(self._empaquetar(trama))

    def enviar_informe(self, informe: bytes) -> None:
        """Escribe un informe de medios TAL CUAL: sin relleno (el ultimo bloque va corto)."""
        v = self.variante
        if v is None:
            raise ErrorCanal("no hay variante negociada")
        datos = (b"\x00" + informe) if v.prefijo else informe
        self._escribir(datos)

    # -------------------------------------------------------------- lectura
    def _leer(self, timeout: float) -> bytes | None:
        """Lee del descriptor. None si no hay nada; b"" significa desconexion (EOF)."""
        listos, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
        if not listos:
            return None
        try:
            datos = os.read(self.fd, p.READ_SIZE)
        except BlockingIOError:
            return None
        except OSError as exc:
            # ENODEV: se ha ido. EIO/ESHUTDOWN: sigue presente pero se esta reiniciando
            # (pasa justo despues de `recovery`, que reinicia el panel).
            if exc.errno in (errno.ENODEV, errno.EIO, errno.ESHUTDOWN):
                raise ErrorCanal(f"el panel se ha desconectado o se esta reiniciando ({exc})"
                                 ) from exc
            raise
        if not datos:
            # EOF: el descriptor esta cerrado. Sin esto, el bucle de lectura giraba al 100 %
            # de CPU sin avanzar nunca.
            raise ErrorCanal("el panel ha cerrado la conexion (lectura vacia)")
        return datos

    def extraer_trama(self) -> p.Trama | None:
        """Saca una trama COMPLETA de lo que ya hay en el buffer, sin leer del descriptor.

        Hace falta mirar el buffer primero: una sola lectura puede traer varias tramas (o una
        respuesta y su retransmision), y si solo se decodificara despues de leer bytes nuevos,
        lo que ya estaba en el buffer no se devolvia nunca.

        Si lo que hay al principio no es una trama valida (por ejemplo un escape roto) se
        resincroniza saltando al siguiente 0x5A; si es una trama a medias, devuelve None para
        que el llamante lea mas.
        """
        while True:
            while self._buffer and self._buffer[0] != p.START:
                self._buffer.pop(0)
            if not self._buffer:
                return None
            trama = p.decode_frame(bytes(self._buffer))
            if trama is not None:
                crudo = bytes(self._buffer[:trama.consumidos])
                del self._buffer[:trama.consumidos]
                # El panel retransmite respuestas identicas: solo se descarta si no se ha
                # enviado nada nuevo desde que llego.
                if crudo == self._ultima and self._envios == self._ultima_envios:
                    if self.verboso:
                        print("    (retransmision descartada)")
                    continue
                self._ultima, self._ultima_envios = crudo, self._envios
                return trama
            # no decodifica: ¿hay otra trama mas adelante? entonces esto era basura
            siguiente = self._buffer.find(bytes([p.START]), 1)
            if siguiente > 0:
                if self.verboso:
                    print(f"    (basura de {siguiente} B descartada antes de una trama)")
                del self._buffer[:siguiente]
                continue
            return None                              # trama a medias: hay que leer mas

    def leer_trama(self, timeout: float | None = None) -> p.Trama | None:
        """Devuelve la siguiente trama completa. None si se agota el tiempo."""
        self.asegurar_abierto()
        limite = time.time() + (self.timeout if timeout is None else timeout)
        while True:
            trama = self.extraer_trama()             # primero, lo que ya esta en el buffer
            if trama is not None:
                return trama
            restante = limite - time.time()
            if restante <= 0:
                return None
            trozo = self._leer(restante)
            if not trozo:
                continue                             # None = sin datos; b"" lanza ErrorCanal
            if self.verboso:
                print(f"    rx {len(trozo)} B: {trozo[:16].hex()}...")
            self._buffer += trozo

    def drenar(self, segundos: float = 0.2) -> None:
        """Descarta lo que quede pendiente en el descriptor."""
        if self.fd is None:
            self._buffer.clear()
            return
        fin = time.time() + segundos
        while time.time() < fin:
            try:
                if self._leer(max(0.0, fin - time.time())) is None:
                    break
            except ErrorCanal:
                break
        self._buffer.clear()

    # -------------------------------------------------------------- peticiones
    @contextlib.contextmanager
    def operacion(self):
        """Serializa una operacion completa (varias peticiones) frente a otros hilos.

        Se usa para la subida de una imagen: entre el `transport` y el `transported` no puede
        colarse una peticion de otro hilo, porque el panel mantiene una sola sesion.
        """
        with self._mutex:
            yield self

    def peticion(self, cmd: str, cuerpo=None, metodo: str = "POST",
                 timeout: float | None = None, cabeceras=None, cuerpo_bruto=None,
                 aceptar_vacio: bool = True, emparejar: bool = True,
                 seq: int | None = None) -> p.Respuesta:
        """Envia una peticion y devuelve su respuesta.

        `aceptar_vacio=False` sigue esperando si llega un 200 SIN cuerpo: en este firmware
        eso significa "no aceptado" o una respuesta vieja, no exito.

        `emparejar=True` descarta las respuestas cuyo `AckNumber` no corresponda a esta
        peticion: el panel retransmite y manda avisos con `AckNumber=0`, asi que emparejar
        por orden de llegada atribuye respuestas a comandos equivocados (el kit lo documenta
        como una de sus trampas).

        Todo el ciclo va bajo el candado del canal: el bloqueo del panel es entre procesos,
        asi que dos hilos del mismo programa se entrelazarian sin el.

        Antes de enviar se descarta lo que quede en el buffer: con un panel que retransmite,
        la copia repetida de la respuesta anterior se leia como respuesta de esta peticion
        (era el motivo de que toda subida fallara con el simulador en modo `--retransmitir`).
        """
        with self._mutex:
            trama, seq = p.build_request(cmd, cuerpo, metodo, seq=seq, cabeceras=cabeceras,
                                         cuerpo_bruto=cuerpo_bruto)
            if self._buffer:
                if self.verboso:
                    print(f"    ({len(self._buffer)} B viejos descartados antes de {cmd})")
                self._buffer.clear()
            self.enviar(trama)
            return self.esperar_respuesta(seq if emparejar else None,
                                          self.timeout if timeout is None else timeout,
                                          aceptar_vacio)

    def esperar_respuesta(self, seq: int | None, timeout: float,
                          aceptar_vacio: bool = True) -> p.Respuesta:
        """Espera una respuesta ya pedida (usado tambien por la autonegociacion)."""
        with self._mutex:
            return self._esperar_respuesta(seq, timeout, aceptar_vacio)

    def _esperar_respuesta(self, seq: int | None, timeout: float,
                           aceptar_vacio: bool = True) -> p.Respuesta:
        limite = time.time() + timeout
        while True:
            restante = limite - time.time()
            if restante <= 0:
                return p.Respuesta(None, None, "", "")
            recibida = self.leer_trama(restante)
            if recibida is None:
                return p.Respuesta(None, None, "", "")
            code, ack, cabecera, texto, campos = p.parse_respuesta(recibida.payload)
            # Se empareja por AckNumber. NO se acepta ack=0: ese es el que llevan los acuses
            # de bloque y los avisos no pedidos, y colarlo hacia que un acuse duplicado se
            # leyera como respuesta de la peticion siguiente (una subida fallaba con "200 sin
            # cuerpo"). El panel contesta a una peticion con seq+1 y, medido, a veces con seq.
            if seq is not None and ack not in (None, seq, seq + 1):
                if self.verboso:
                    print(f"    (respuesta de otra peticion: ack={ack}, esperaba {seq})")
                continue
            if not recibida.checksum_ok and self.verboso:
                print("    !! checksum de la respuesta NO cuadra")
            if not aceptar_vacio and code == 200 and not texto.strip():
                continue
            return p.Respuesta(code, ack, cabecera, texto, bytes(recibida.payload),
                               recibida.checksum_ok, campos)

    # -------------------------------------------------------------- autonegociacion
    def autonegociar(self, timeout: float = 1.5) -> Variante:
        """Descubre como escribirle al panel probando variantes con un `conn` inocuo.

        Devuelve la variante que el panel contesta y la deja fijada. Si ya hay una fijada,
        la comprueba y solo busca otra si deja de funcionar.
        """
        with self._mutex:
            return self._autonegociar(timeout)

    def _autonegociar(self, timeout: float = 1.5) -> Variante:
        if self.fd is None:
            self.abrir()
        if self.variante is not None:
            self.drenar()
            if self.peticion("conn", None, timeout=timeout, aceptar_vacio=False).code == 200:
                return self.variante
            if self.verboso:
                print("    la variante fijada no responde; se vuelve a negociar")
            self.variante = None
        self.negociacion = []
        for v in VARIANTES:
            self.drenar()
            trama, _ = p.build_request("conn", None, seq=1)
            intento = {"variante": v.nombre, "descripcion": v.describe()}
            self.variante = v
            try:
                self._escribir(self._empaquetar(trama))
            except ErrorCanal as exc:
                intento.update(ok=False, error=str(exc))
                self.negociacion.append(intento)
                if self.verboso:
                    print(f"    {v.describe()} -> {exc}")
                continue
            respuesta = self.esperar_respuesta(1, timeout, aceptar_vacio=False)
            ok = respuesta.code == 200 and bool(respuesta.cuerpo.strip())
            intento.update(ok=ok, code=respuesta.code, ack=respuesta.ack,
                           campos=len(respuesta.json() or {}))
            self.negociacion.append(intento)
            if self.verboso:
                print(f"    {v.describe()} -> {'OK' if ok else 'sin respuesta'}"
                      + (f" (code={respuesta.code})" if respuesta.code else ""))
            if ok:
                return v
        self.variante = None
        raise ErrorCanal(
            "el panel no responde a ninguna variante de escritura. Comprueba que no lo este "
            "usando otro programa (el editor de COUGAR ocupa el HID en exclusiva) y que el "
            "panel haya arrancado (bootFinish=1)."
        )
