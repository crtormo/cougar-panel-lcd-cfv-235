"""El panel CFV235 como objeto: abrir, preguntar, mandar y subir imagenes.

Es la capa de E/S que estaba dentro del cliente verificado (`referencia/cougar_panel.py`)
puesta en una clase, para que tu editor la use sin copiar nada. Las tramas las construye
`cougar.protocolo`; aqui solo se escribe y se lee por el descriptor.

    from cougar import Panel

    with Panel() as panel:
        print(panel.propiedades()["bootFinish"])
        panel.no_dormir()                       # que no se apague sin trafico
        panel.subir("mi_fondo.png")             # capa de fondo (por defecto)
        panel.subir("mi_fondo.png", capa="osd") # capa OSD (encima, reutiliza hueco)

Aviso importante: **solo un programa puede hablar con el panel a la vez**. Si tienes el
editor de COUGAR abierto, cierralo.
"""

import glob
import json
import os
import select
import sys
import time

from . import protocolo as p


# --------------------------------------------------------------------------- descubrir
def _hid_id(ruta):
    """(vid, pid) de un /dev/hidrawN leyendo sysfs."""
    base = os.path.basename(ruta)
    try:
        with open(f"/sys/class/hidraw/{base}/device/uevent") as fh:
            for linea in fh:
                if linea.startswith("HID_ID="):
                    _, vid, pid = linea.strip().split("=")[1].split(":")
                    return int(vid, 16), int(pid, 16)
    except OSError:
        pass
    return None, None


def listar_dispositivos(patron="/dev/hidraw*"):
    """[(ruta, vid, pid)] de los dispositivos hidraw del sistema."""
    return [(ruta, *_hid_id(ruta)) for ruta in sorted(glob.glob(patron))]


def buscar_panel(vid=p.VID, pid=p.PID, patron="/dev/hidraw*"):
    """Ruta del hidraw del panel, o None."""
    for ruta, v, pid_leido in listar_dispositivos(patron):
        if v == vid and pid_leido == pid:
            return ruta
    return None


# --------------------------------------------------------------------------- respuesta
class Respuesta:
    """Una respuesta del panel: codigo, cabecera, cuerpo y la trama cruda."""

    def __init__(self, code, cabecera, cuerpo, crudo=b"", ack=None, checksum_ok=True):
        self.code = code
        self.cabecera = cabecera
        self.cuerpo = cuerpo
        self.crudo = crudo
        self.ack = ack
        self.checksum_ok = checksum_ok

    @property
    def ok(self):
        return self.code == 200

    def json(self):
        try:
            return json.loads(self.cuerpo)
        except ValueError:
            return None

    def __repr__(self):
        return f"<Respuesta {self.code} ack={self.ack} {len(self.cuerpo)} B>"

    def __bool__(self):
        return self.code is not None


# --------------------------------------------------------------------------- telemetria
TELEMETRIA_DEMO = {
    "network": {"upload": 0, "download": 1},
    "memory": {"total": 32675, "used": 11028, "load": 33, "temperature": 0, "speed": 1065},
    "cpu": {"load": 11, "temperature": 62, "speedAverage": 3775,
            "power": 46, "voltage": 1.273, "usage": 8},
    "gpu": {"load": 3, "temperature": 29, "fan": 0, "speed": 76, "power": 0, "voltage": 0.664},
    "disk": {"total": 465, "used": 50, "load": 10, "activity": 0, "temperature": 0,
             "readSpeed": 0, "writeSpeed": 0},
    "fans": [{"onBoard": True, "type": "Pump", "name": "Fan AIO Pump", "value": 2566},
             {"onBoard": True, "type": "Fan", "name": "Fan CPU", "value": 1323},
             {"onBoard": True, "type": "Chassis", "name": "Fan Chassis3", "value": 897}],
    "motherboard": {"temperature": 25},
}


def telemetria_demo():
    """Un cuerpo `STATE all` de ejemplo. El panel usa esto para su reloj de sistema."""
    datos = json.loads(json.dumps(TELEMETRIA_DEMO))
    datos["timestamp"] = int(time.time() * 1000)
    return datos


# --------------------------------------------------------------------------- panel
class Panel:
    """Cliente del panel. Se puede usar como gestor de contexto (`with Panel() as p:`).

    Parametros utiles si el kernel se pone tonto (los tres se midieron y los tres
    funcionaron en el banco de pruebas, asi que lo normal es no tocar nada):

        informe   tamano de la escritura, con el byte de report ID (1025 por defecto)
        prefijo   anteponer el byte de report ID 0x00 (si da EINVAL, pruebalo a False)
        exacto    una sola escritura 0x00+trama, sin rellenar a 1025 (es lo que hace el
                  editor de COUGAR para las tramas cortas)
    """

    def __init__(self, dispositivo=None, vid=p.VID, pid=p.PID, informe=p.REPORT_SIZE,
                 prefijo=True, exacto=False, timeout=3.0, verboso=False, patron="/dev/hidraw*"):
        self.dispositivo = dispositivo
        self.vid, self.pid = vid, pid
        self.informe, self.prefijo, self.exacto = informe, prefijo, exacto
        self.timeout, self.verboso = timeout, verboso
        self.patron = patron
        self.fd = None
        self._envios = 0
        self._ultima_trama = None
        self._envios_ultima = -1
        self._buffer = bytearray()

    # ------------------------------------------------------------------ abrir/cerrar
    def abrir(self):
        if self.fd is not None:
            return self
        ruta = self.dispositivo or buscar_panel(self.vid, self.pid, self.patron)
        if not ruta:
            raise FileNotFoundError(
                f"no encuentro el panel ({self.vid:#06x}:{self.pid:#06x}). "
                "Mira 'cougar listar', o pasa --device /dev/hidrawN."
            )
        self.dispositivo = ruta
        try:
            self.fd = os.open(ruta, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            raise PermissionError(
                f"no puedo abrir {ruta}: {exc}. Prueba con sudo o instala la regla udev."
            ) from exc
        return self

    def cerrar(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    def __enter__(self):
        return self.abrir()

    def __exit__(self, *_):
        self.cerrar()

    # ------------------------------------------------------------------ escribir/leer
    def enviar(self, trama: bytes):
        """Escribe una trama troceada en informes con el byte de report ID delante."""
        if self.fd is None:
            self.abrir()
        self._envios += 1
        prefijo = b"\x00" if self.prefijo else b""
        if self.exacto:
            os.write(self.fd, prefijo + trama)
            return
        util = self.informe - len(prefijo)
        for i in range(0, len(trama), util):
            trozo = trama[i:i + util]
            os.write(self.fd, prefijo + trozo + b"\x00" * (util - len(trozo)))

    def enviar_informe(self, informe: bytes):
        """Escribe un informe de medios TAL CUAL (sin relleno: el ultimo bloque va corto)."""
        if self.fd is None:
            self.abrir()
        os.write(self.fd, (b"\x00" if self.prefijo else b"") + informe)

    def _leer_reporte(self, timeout):
        listos, _, _ = select.select([self.fd], [], [], timeout)
        if not listos:
            return None
        try:
            return os.read(self.fd, p.READ_SIZE)
        except (BlockingIOError, OSError):
            return None

    def leer_trama(self, timeout=None):
        """Acumula informes hasta tener una trama completa. None si se agota el tiempo."""
        timeout = self.timeout if timeout is None else timeout
        buffer = self._buffer
        limite = time.time() + timeout
        while time.time() < limite:
            trozo = self._leer_reporte(max(0.05, limite - time.time()))
            if trozo is None:
                continue
            if self.verboso:
                print(f"    rx {len(trozo)} B: {trozo[:16].hex()}...")
            buffer += trozo
            while buffer and buffer[0] != p.START:
                buffer.pop(0)
            info = p.decode_frame(bytes(buffer))
            if info is None:
                continue
            trama = bytes(buffer[:info["consumidos"]])
            del buffer[:info["consumidos"]]
            # El panel RETRANSMITE respuestas identicas (medido: el mismo
            # "1 400 AckNumber=1" llego dos veces con 18 s de diferencia). Solo se
            # descarta si no se ha enviado nada nuevo desde que llego.
            if trama == self._ultima_trama and self._envios == self._envios_ultima:
                if self.verboso:
                    print("    (retransmision descartada)")
                continue
            self._ultima_trama, self._envios_ultima = trama, self._envios
            return trama
        return None

    # ------------------------------------------------------------------ peticiones
    def peticion(self, cmd, cuerpo=None, method="POST", timeout=None, cabeceras=None,
                 cuerpo_bruto=None, aceptar_vacio=True):
        """Envia una peticion y devuelve su `Respuesta` (code None si no hay respuesta).

        `aceptar_vacio=False` sigue esperando si llega un 200 SIN cuerpo: con este
        firmware eso es una respuesta vieja o un rechazo (`transported` contesta 200 con
        ContentLength=0 cuando NO acepta la subida).
        """
        trama, seq = p.build_request(cmd, cuerpo, method,
                                     extra_headers=cabeceras, raw_body=cuerpo_bruto)
        if self.verboso:
            print(f"--> {method} {cmd}  seq={seq}  {len(trama)} B")
        self.enviar(trama)
        limite = time.time() + (self.timeout if timeout is None else timeout)
        while True:
            restante = limite - time.time()
            if restante <= 0:
                return Respuesta(None, "", "")
            trama_rx = self.leer_trama(restante)
            if trama_rx is None:
                return Respuesta(None, "", "")
            payload, ok = p.parse_frame(trama_rx)
            code, cabecera, texto = p.parse_response(payload)
            ack = p.parse_ack(cabecera)
            if not ok:
                print("!! checksum de la respuesta NO cuadra", file=sys.stderr)
            if not aceptar_vacio and code == 200 and not texto.strip():
                continue                      # respuesta vieja: se sigue esperando
            return Respuesta(code, cabecera, texto, trama_rx, ack, ok)

    # ------------------------------------------------------------------ ordenes
    def propiedades(self):
        """`POST conn`: versiones, espacio, brillo, capas, telemetria de estado..."""
        return self.peticion("conn", None, timeout=4.0, aceptar_vacio=False).json() or {}

    def telemetria(self, datos=None):
        """`STATE all` con las metricas del PC (es lo que muestra el panel)."""
        return self.peticion("all", datos or telemetria_demo(), method="STATE", timeout=4.0)

    def power(self, evento="resume"):
        return self.peticion("power", {"event": evento})

    def brillo(self, valor):
        return self.peticion("brightness", {"value": int(valor)})

    def girar(self, grados):
        return self.peticion("rotate", {"degree": int(grados)})

    def recovery(self):
        """Reset del panel: REINICIA, borra los medios y deja `osdState: 0`."""
        return self.peticion("recovery", {"enable": True}, timeout=8.0)

    def no_dormir(self, activo=True):
        """`displayInSleep`. OJO: no hace lo que parecia.

        Se creyo medido que con este campo a 0 el panel aguantaba encendido mas de 6
        minutos sin trafico. **Es falso**: aquella medida se tomo con el editor de COUGAR
        abierto, y mientras hay una aplicacion usandolo el panel no se duerme. Cerrado el
        editor, la pantalla se apago **~1 minuto** despues del ultimo trafico (el perfil
        del panel trae `timeout: 60`). Lo que lo mantiene despierto es el TRAFICO; el
        efecto real de este campo sigue sin determinar (ver PENDIENTE.md).
        """
        return self.peticion("displayInSleep", {"enable": not activo}, timeout=6.0)

    def avisar_si_hay_osd(self):
        """True si hay una capa OSD activa: se dibujaria ENCIMA de lo que subas."""
        props = self.propiedades()
        return props.get("osdState") == 1

    # ------------------------------------------------------------------ subir imagenes
    def subir_datos(self, datos: bytes, nombre="imagen.png", capa="fondo", verboso=False):
        """Sube un PNG/JPEG/GIF ya en memoria. Devuelve `Respuesta` de la confirmacion.

        LO CRITICO ES EL TIEMPO: el panel abre una sesion de transferencia al contestar al
        `transport` y **caduca en menos de un segundo**. El editor manda los bloques 87 ms
        despues y el `transported` 6 ms despues del ultimo bloque. Por eso aqui NO hay
        ninguna pausa entre las tres fases, y tampoco se espera el acuse de cada bloque.
        """
        if not (datos[:8] == b"\x89PNG\r\n\x1a\n"                       # PNG
                or datos[:2] == b"\xff\xd8"                             # JPEG
                or datos[:6] in (b"GIF87a", b"GIF89a")):                # GIF
            raise ValueError(f"el fichero no es PNG, JPEG ni GIF (empieza por {datos[:8].hex()})")
        tipo = p.MEDIA_TIPO_OSD if str(capa).lower() in ("osd", "1", "0x01") else p.MEDIA_TIPO_FONDO
        bloques = p.bloques_de(len(datos))
        if bloques > 0xFFFF:
            raise ValueError("demasiado grande para el protocolo (>65535 bloques)")

        # Fase 1: anunciar. El panel contesta 200 con "blockMaxSize".
        primera = self.peticion("transport",
                               {"type": "media", "fileSize": len(datos), "fileName": nombre},
                               timeout=6.0, aceptar_vacio=False)
        if primera.code != 200 or "blockMaxSize" not in primera.cuerpo:
            raise RuntimeError(
                f"el panel rechazo el handshake 'transport' (code={primera.code}). "
                'Se esperaba 200 con "blockMaxSize":1024. Causas tipicas: el panel no ha '
                "terminado de arrancar (conn: bootFinish debe ser 1), no queda espacio, o "
                "hay otro programa usando el panel."
            )
        if verboso:
            print(f"handshake OK: {primera.cuerpo.strip()}")

        # Fase 2: los bloques, SIN PAUSA (sin escapes ni checksum, ultimo bloque corto).
        for indice in range(bloques):
            trozo = datos[indice * p.MEDIA_TROZO:(indice + 1) * p.MEDIA_TROZO]
            self.enviar_informe(p.build_media_report(indice, bloques, trozo, tipo))
        if verboso:
            print(f"  {bloques} bloques enviados")

        # Fase 3: confirmar, tambien SIN PAUSA.
        ultima = self.peticion("transported", {"md5": "todo", "fileName": nombre},
                               timeout=6.0, aceptar_vacio=False)
        if ultima.code == 200 and '"success"' not in ultima.cuerpo:
            raise RuntimeError(
                "'transported' contesto 200 pero SIN cuerpo: el panel no acepto la subida "
                "(casi siempre por enviar los bloques demasiado tarde)."
            )
        return ultima

    def subir(self, ruta, capa="fondo", verboso=False):
        """Sube un fichero del disco. Devuelve la `Respuesta` de la confirmacion."""
        with open(ruta, "rb") as fh:
            datos = fh.read()
        return self.subir_datos(datos, os.path.basename(ruta), capa, verboso)
