"""El panel COUGAR CFV235 como objeto: estado, control y subida de imagenes.

Se apoya en `canal.Canal`, que es quien resuelve como escribirle al panel en Linux. Aqui
estan las ordenes y, sobre todo, la **subida**, que es la parte que mas veces se hace mal.

Las tres cosas que hay que respetar en una subida (medidas entre el kit cfv-235 y el
proyecto independiente cougarLCD.cpp, que coinciden):

1. **La sesion de transferencia caduca en menos de un segundo.** Al contestar al `transport`
   el panel abre una sesion; los bloques van ~87 ms despues y el cierre ~6 ms despues del
   ultimo bloque. Por eso no se mete ninguna pausa.
2. **Hay que esperar el acuse de los bloques antes de cerrar.** cougarLCD.cpp hace
   `wait_for_response` justo despues de mandar los bloques y antes del `transported`; el kit
   cfv-235 no lo espera. El acuse es `1 200 AckNumber=0` si se aceptaron y
   `1 400 AckNumber=0` si se rechazaron (sesion caducada).
3. **`transported` con 200 y cuerpo vacio significa "no aceptado"**, no exito. Con exito
   devuelve `{"state":"success"}`.

Ademas, antes de subir se comprueba el **espacio libre** (`space`): el propio README del kit
documenta que un fichero mayor que la memoria del panel lo deja atascado con
`bootFinish: 0`, y recuperarlo exige cortarle la alimentacion.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from . import protocolo as p
from .canal import Canal, ErrorCanal, SinPanel, SinPermisos, Variante, info_dispositivo

# Margen de seguridad al comprobar el espacio: no llenar el panel hasta el ultimo KB.
MARGEN_ESPACIO = 0.05

# Cuanto se espera el acuse de los bloques. cougarLCD.cpp espera hasta 10 s, pero el
# acuse, cuando llega, llega en milisegundos; si no llega, se continua (hay firmwares
# que no lo mandan) en vez de abortar una subida que puede ser correcta.
MS_ACUSE_BLOQUES = 1500


@dataclass
class ResultadoSubida:
    """Todo lo que paso en una subida, para poder contarlo en la interfaz."""
    ok: bool = False
    motivo: str = ""
    nombre: str = ""
    capa: str = ""
    bytes: int = 0
    bloques: int = 0
    espacio_antes_kb: int | None = None
    transport: p.Respuesta | None = None
    acuse_code: int | None = None
    acuse_llego: bool = False
    transported: p.Respuesta | None = None
    ms_total: float = 0.0
    avisos: list[str] = field(default_factory=list)

    def resumen(self) -> str:
        if self.ok:
            return (f"{self.nombre}: subida correcta ({self.bytes} B, "
                    f"{self.bloques} bloques, {self.ms_total:.0f} ms)")
        return f"{self.nombre}: {self.motivo}"


class Panel:
    """Cliente del panel. Usar como gestor de contexto."""

    def __init__(self, canal: Canal | None = None, dispositivo: str | None = None,
                 timeout: float = 3.0, verboso: bool = False,
                 variante: Variante | None = None):
        # Se recuerda la ruta que pidio el usuario: al reconectar hay que respetarla. Si no,
        # un `--device /dev/pts/3` (el simulador) acababa saltando al panel REAL por la
        # busqueda de VID/PID, y encima chocando con quien lo tuviera tomado.
        self.ruta_pedida = dispositivo
        self.canal = canal or Canal(dispositivo, variante=variante, timeout=timeout,
                                    verboso=verboso)
        self.timeout = timeout
        self.verboso = verboso

    # -------------------------------------------------------------- abrir/cerrar
    def abrir(self) -> "Panel":
        self.canal.abrir()
        return self

    def cerrar(self) -> None:
        self.canal.cerrar()

    def __enter__(self) -> "Panel":
        return self.abrir()

    def __exit__(self, *_) -> None:
        self.cerrar()

    @property
    def dispositivo(self) -> str | None:
        return self.canal.ruta

    # -------------------------------------------------------------- estado
    def propiedades(self, timeout: float | None = None) -> dict:
        """`POST conn`: versiones, espacio, brillo, capas, bootFinish..."""
        respuesta = self.canal.peticion("conn", None, timeout=timeout or 4.0,
                                        aceptar_vacio=False)
        return respuesta.json() or {}

    def propiedades_seguras(self) -> dict:
        """Como `propiedades()` pero sin lanzar: {} si el panel no contesta."""
        try:
            return self.propiedades()
        except ErrorCanal:
            return {}

    def espacio_libre_kb(self) -> int | None:
        valor = self.propiedades_seguras().get("space")
        return valor if isinstance(valor, int) else None

    def listo(self) -> bool:
        """True si el panel ha terminado de arrancar (bootFinish: 1)."""
        return self.propiedades_seguras().get("bootFinish") == 1

    def reabrir(self) -> bool:
        """Cierra y vuelve a abrir el panel, respetando la ruta que se pidio.

        Hace falta cuando el panel se cae o se reinicia (`recovery`): desaparece del USB y
        puede volver **con otro /dev/hidrawN**, y mientras tanto las lecturas dan EIO.

        El orden de intentos importa:

        1. La ruta que pidio el usuario, si la hay.
        2. Solo si esa ruta era un `/dev/hidraw*` (o si no se pidio ninguna), se busca el
           panel por su VID/PID, porque puede haber cambiado de numero al reconectarse.

        Si se pidio una ruta que no es un hidraw (un `/dev/pts/N` del simulador, o un aparato
        concreto) y esa ruta ya no esta, **no** se cambia a otro dispositivo: seria hablarle a
        un panel distinto del que se eligio.
        """
        pedida = self.ruta_pedida
        candidatas: list[str | None] = []
        if pedida:
            candidatas.append(pedida)
            if pedida.startswith("/dev/hidraw"):
                candidatas.append(None)              # puede haber cambiado de numero
        else:
            candidatas.append(None)                  # autodetectar
        self.canal.cerrar()
        self.canal._buffer.clear()
        for ruta in candidatas:
            self.canal.ruta = ruta
            try:
                self.canal.abrir()
                self.canal.autonegociar(timeout=min(2.5, self.timeout))
                return True
            except (SinPanel, SinPermisos, ErrorCanal):
                self.canal.cerrar()
        self.canal.ruta = pedida
        return False

    def esperar_arranque(self, max_segundos: float = 120, cada: float = 6.0,
                         avisar=None) -> bool:
        """Sondea `conn` hasta ver bootFinish=1, tolerando que el panel se reinicie.

        El panel tarda 60-80 s en completar el arranque tras un corte de alimentacion (o un
        `recovery`), y mientras tanto contesta con bootFinish=0 o da EIO. Devuelve True si
        arranco.
        """
        limite = time.time() + max_segundos
        while time.time() < limite:
            try:
                props = self.propiedades()
            except (SinPanel, SinPermisos, ErrorCanal):
                props = {}
                self.reabrir()                        # puede estar reiniciandose
            if props.get("bootFinish") == 1:
                return True
            if avisar:
                avisar(props.get("bootFinish"))
            time.sleep(cada)
        return False

    # -------------------------------------------------------------- ordenes
    def brillo(self, valor: int) -> p.Respuesta:
        return self.canal.peticion("brightness", {"value": max(0, min(100, int(valor)))})

    def girar(self, grados: int) -> p.Respuesta:
        if grados not in (0, 90, 180, 270):
            raise ValueError("el panel solo acepta 0, 90, 180 o 270 grados")
        return self.canal.peticion("rotate", {"degree": int(grados)})

    def power(self, evento: str = "resume") -> p.Respuesta:
        """Solo `resume` es valido: restart/reboot/reset/reload dan 400."""
        return self.canal.peticion("power", {"event": evento})

    def recovery(self) -> p.Respuesta:
        """Reset: REINICIA el panel, borra los medios y deja osdState en 0.

        El dispositivo desaparece del USB unos segundos; cualquier escritura inmediata
        falla con "no puedo escribir", y eso es normal.
        """
        return self.canal.peticion("recovery", {"enable": True}, timeout=8.0)

    def no_dormir(self, activo: bool = True) -> p.Respuesta:
        """`displayInSleep`: con activo=True el panel sigue mostrando en reposo.

        Medido en el panel real (docs/CANAL.md 7.bis): `enable=true` deja `displayInSleep` en
        **1** y el panel aguanta encendido sin trafico; con **0** se apaga en menos de dos
        minutos. Antes se enviaba `not activo`, asi que activar "no dormir" ponia justo el
        valor que PERMITE el apagado.
        """
        return self.canal.peticion("displayInSleep", {"enable": bool(activo)}, timeout=6.0)

    def telemetria(self, datos: dict | None = None) -> p.Respuesta:
        """`STATE all`: las metricas que el panel muestra en su capa de sistema."""
        return self.canal.peticion("all", datos or telemetria_demo(), metodo="STATE",
                                   timeout=4.0)

    def realtime(self, activo: bool = True) -> p.Respuesta:
        return self.canal.peticion("realtimeDisplay", {"enable": bool(activo)})

    def modo(self, valor: int) -> p.Respuesta:
        """`POST mode {"value":N}` — comando **no documentado** por el kit cfv-235.

        Medido en el panel real: existe, responde 200 y acepta los valores 0..3 (el estado
        los reporta tal cual). No cambia `logo`, `osdState`, `background` ni `brightness`,
        asi que su efecto visible no esta determinado; se expone para poder investigarlo.

        AVISO: hay que mandar SIEMPRE `{"value": <entero>}`. Con otro cuerpo (por ejemplo
        `{"mode":1}` o `{"enable":true}`) el firmware contesta 200 pero deja `mode` en
        **2147483647** (0x7FFFFFFF): un campo sin inicializar. Esta validacion evita que
        cualquiera lo provoque por accidente.
        """
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValueError("el modo tiene que ser un entero (0..3)")
        if not 0 <= valor <= 3:
            raise ValueError("los modos que acepta el panel son 0, 1, 2 y 3")
        return self.canal.peticion("mode", {"value": int(valor)}, timeout=5.0)

    # -------------------------------------------------------------- subida
    def subir_archivo(self, ruta: str, capa: str = "fondo", forzar: bool = False,
                      nombre: str | None = None) -> ResultadoSubida:
        """Sube un fichero del disco, comprobando su tamano ANTES de leerlo.

        Antes se leia el fichero entero y despues se validaba: con un fichero de 400 MB eso
        eran 400 MB de memoria para acabar rechazandolo.
        """
        import os
        res = ResultadoSubida(nombre=nombre or os.path.basename(ruta), capa=capa)
        try:
            tamano = os.path.getsize(ruta)
        except OSError as exc:
            res.motivo = f"no puedo leer {ruta}: {exc}"
            return res
        maximo = p.MEDIA_MAX_BLOQUES * p.MEDIA_TROZO
        if tamano > maximo:
            res.motivo = (f"el fichero ocupa {tamano / 1048576:.1f} MB y el protocolo no pasa "
                          f"de {maximo / 1048576:.1f} MB")
            return res
        if tamano == 0:
            res.motivo = "el fichero esta vacio"
            return res
        # firma: basta con los primeros bytes para saber si es una imagen que el panel acepta
        try:
            with open(ruta, "rb") as fh:
                cabecera = fh.read(8)
                if p.tipo_de_imagen(cabecera) is None and p.tipo_de_imagen(cabecera[:3]) is None:
                    res.motivo = (f"el fichero no es PNG, JPEG ni GIF "
                                  f"(empieza por {cabecera[:8].hex()})")
                    return res
                fh.seek(0)
                datos = fh.read()
        except OSError as exc:
            res.motivo = f"no puedo leer {ruta}: {exc}"
            return res
        res.bytes = len(datos)
        return self.subir_datos(datos, nombre or os.path.basename(ruta), capa=capa,
                                forzar=forzar)

    def subir_datos(self, datos: bytes, nombre: str, capa: str = "fondo",
                    forzar: bool = False, esperar_acuse: bool = True,
                    mensaje: int | None = None) -> ResultadoSubida:
        """Sube un PNG/JPEG/GIF. Devuelve un `ResultadoSubida`: **nunca lanza**.

        Toda la operacion (transporte, bloques, acuse y cierre) va bajo el candado del canal:
        el panel mantiene una sola sesion y no puede colarse otra peticion en medio.
        """
        t0 = time.time()
        res = ResultadoSubida(nombre=nombre, capa=capa, bytes=len(datos))
        try:
            return self._subir_datos(res, datos, nombre, capa, forzar, esperar_acuse, mensaje)
        except (ErrorCanal, OSError, ValueError, TypeError) as exc:
            # El docstring promete no lanzar: cualquier fallo del panel o de los datos se
            # cuenta como subida fallida, no como traza de Python.
            res.motivo = res.motivo or f"{type(exc).__name__}: {exc}"
            res.ms_total = (time.time() - t0) * 1000
            return res

    def _subir_datos(self, res: ResultadoSubida, datos: bytes, nombre: str, capa: str,
                     forzar: bool, esperar_acuse: bool,
                     mensaje: int | None) -> ResultadoSubida:
        t0 = time.time()

        # --- validaciones previas (antes de tocar el panel)
        tipo = p.tipo_de_imagen(datos)
        if tipo is None:
            res.motivo = (f"el fichero no es PNG, JPEG ni GIF "
                          f"(empieza por {datos[:8].hex()})")
            return res
        try:
            nombre = p.nombre_seguro(nombre)
        except ValueError as exc:
            res.motivo = str(exc)
            return res
        res.nombre = nombre
        if tipo == "gif":
            res.avisos.append("el panel acepta GIF pero lo muestra en blanco: usa PNG")
        bloques = p.bloques_de(len(datos))
        if bloques == 0:
            res.motivo = "el fichero esta vacio"
            return res
        if bloques > p.MEDIA_MAX_BLOQUES:
            res.motivo = (f"demasiado grande para el protocolo: {bloques} bloques "
                          f"(maximo {p.MEDIA_MAX_BLOQUES})")
            return res

        es_fondo = str(capa).lower() not in ("osd", "1", "0x01")

        with self.canal.operacion():
            # --- espacio libre
            libre_kb = self.espacio_libre_kb()
            res.espacio_antes_kb = libre_kb
            necesita_kb = math.ceil(len(datos) / 1024) * (1 + MARGEN_ESPACIO)
            if libre_kb is None:
                # No se pudo leer el espacio: no se puede dar por bueno en silencio. Si el
                # fichero es grande se aborta (es la unica defensa contra atascar el panel);
                # si es pequeno, se avisa y se sigue.
                res.avisos.append("no se pudo leer el espacio libre del panel")
                if necesita_kb > 4096 and not forzar:
                    res.motivo = ("no se pudo comprobar el espacio libre del panel y el fichero "
                                  "no es pequeno: usa `forzar` si sabes que cabe")
                    return res
            elif necesita_kb > libre_kb:
                if es_fondo and not forzar:
                    res.motivo = (f"no cabe: el fichero ocupa {len(datos)/1024:.0f} KB y al panel "
                                  f"le quedan {libre_kb} KB. Subelo a la capa OSD (no acumula) "
                                  f"o usa recovery para liberar espacio")
                    return res
                res.avisos.append(f"espacio justo: {libre_kb} KB libres")

            # --- fase 1: anunciar
            cuerpo = {"type": "media", "fileSize": len(datos), "fileName": nombre}
            transport = self.canal.peticion("transport", cuerpo, timeout=6.0,
                                            aceptar_vacio=False)
            res.transport = transport
            if transport.code != 200 or "blockMaxSize" not in transport.cuerpo:
                res.motivo = (
                    f"el panel rechazo el handshake `transport` (code={transport.code}). "
                    "Causas tipicas: no ha terminado de arrancar (bootFinish debe ser 1), no "
                    "queda espacio, o hay otro programa usando el panel."
                )
                return res
            tam_bloque = p.MEDIA_TROZO
            try:
                bruto = transport.json() or {}
                ancho = int(bruto.get("blockMaxSize") or p.MEDIA_TROZO)
                if 0 < ancho <= p.MEDIA_TROZO:
                    tam_bloque = ancho
                elif ancho > p.MEDIA_TROZO:
                    tam_bloque = p.MEDIA_TROZO
                else:
                    res.avisos.append(f"el panel dijo blockMaxSize={ancho}: se usa "
                                      f"{p.MEDIA_TROZO}")
            except (TypeError, ValueError):
                pass

            # --- fase 2: los bloques, sin pausas
            tipo_capa = p.MEDIA_TIPO_OSD if not es_fondo else p.MEDIA_TIPO_FONDO
            valor_mensaje = p.MEDIA_MENSAJE if mensaje is None else mensaje
            for indice in range(bloques):
                trozo = datos[indice * tam_bloque:(indice + 1) * tam_bloque]
                try:
                    self.canal.enviar_informe(
                        p.build_media_report(indice, bloques, trozo, tipo_capa, valor_mensaje))
                except ErrorCanal as exc:
                    res.motivo = f"fallo al enviar el bloque {indice + 1}/{bloques}: {exc}"
                    return res
            res.bloques = bloques

            # --- fase 2.b: esperar el acuse de los bloques (lo que el kit no hace)
            if esperar_acuse:
                self._leer_acuse(res)

            # --- fase 3: cerrar
            # Se acepta el 200 vacio a proposito: en este firmware NO significa exito, significa
            # "no aceptado" (sesion caducada). Descartarlo solo cambiaria un mensaje claro por un
            # timeout; el emparejado por AckNumber ya evita confundirlo con una respuesta vieja.
            transported = self.canal.peticion("transported", {"md5": "todo", "fileName": nombre},
                                              timeout=8.0, aceptar_vacio=True)
            res.transported = transported
            if transported.code == 200 and '"success"' in transported.cuerpo:
                res.ok = True
            elif transported.code == 200:
                res.motivo = ("`transported` contesto 200 pero SIN cuerpo: el panel no acepto la "
                              "subida (casi siempre por enviar los bloques demasiado tarde)")
            else:
                res.motivo = f"`transported` no confirmo la subida (code={transported.code})"
        res.ms_total = (time.time() - t0) * 1000
        return res

    def _leer_acuse(self, res: ResultadoSubida) -> None:
        """Lee el acuse de los bloques: `1 200 AckNumber=0` aceptados, `1 400` rechazados."""
        limite = MS_ACUSE_BLOQUES / 1000.0
        try:
            trama = self.canal.leer_trama(limite)
        except ErrorCanal as exc:
            res.avisos.append(f"no se pudo leer el acuse: {exc}")
            return
        if trama is None:
            res.avisos.append("el panel no mando el acuse de los bloques (se continua)")
            return
        code, ack, _, _, _ = p.parse_respuesta(trama.payload)
        res.acuse_code = code
        res.acuse_llego = True
        if code == 400:
            res.avisos.append("el panel ACUSO LOS BLOQUES COMO RECHAZADOS (1 400)")

    # -------------------------------------------------------------- diagnostico
    def diagnostico(self, negociar: bool = True) -> dict:
        """Todo lo que se puede saber del panel y del canal, sin cambiar nada."""
        info = {"dispositivo": self.canal.ruta}
        if self.canal.ruta:
            info["hardware"] = info_dispositivo(self.canal.ruta)
        try:
            self.canal.abrir()
            info["abierto"] = True
        except (SinPanel, SinPermisos, ErrorCanal) as exc:
            info["abierto"] = False
            info["error_apertura"] = str(exc)
            return info
        if negociar:
            try:
                variante = self.canal.autonegociar()
                info["variante"] = variante.describe()
                info["negociacion"] = list(self.canal.negociacion)
            except ErrorCanal as exc:
                info["error_negociacion"] = str(exc)
                info["negociacion"] = list(self.canal.negociacion)
                return info
        t0 = time.time()
        props = self.propiedades_seguras()
        info["latencia_conn_ms"] = round((time.time() - t0) * 1000, 1)
        info["propiedades"] = props
        return info


# ------------------------------------------------------------------ telemetria de ejemplo
TELEMETRIA_DEMO = {
    "network": {"upload": 0, "download": 1},
    "memory": {"total": 32675, "used": 11028, "load": 33, "temperature": 0, "speed": 3200},
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


def telemetria_demo() -> dict:
    """Un cuerpo `STATE all` de ejemplo, con la marca de tiempo al dia."""
    datos = {k: (dict(v) if isinstance(v, dict) else
                 [dict(x) for x in v] if isinstance(v, list) else v)
             for k, v in TELEMETRIA_DEMO.items()}
    datos["timestamp"] = int(time.time() * 1000)
    return datos


def telemetria_desde_sensores(valores: dict) -> dict:
    """Convierte la muestra de `sensores.Sensores` en un cuerpo `STATE all`."""
    def num(clave):
        v = valores.get(clave)
        return round(v, 1) if isinstance(v, (int, float)) else 0

    datos = telemetria_demo()
    datos["cpu"] = {"load": int(num("cpu_uso")), "temperature": num("cpu_temp"),
                    "speedAverage": num("cpu_mhz"), "power": 0, "voltage": 0,
                    "usage": int(num("cpu_uso"))}
    ram_total = valores.get("ram_total_gb") or 0
    ram_usado = valores.get("ram_usado_gb") or 0
    datos["memory"] = {"total": int(ram_total * 1024 * 1024), "used": int(ram_usado * 1024 * 1024),
                       "load": int(num("ram_uso")), "temperature": 0,
                       "speed": int(num("ram_velocidad"))}
    datos["gpu"] = {"load": int(num("gpu_uso")), "temperature": num("gpu_temp"), "fan": 0,
                    "speed": num("gpu_mhz"), "power": 0, "voltage": 0}
    datos["disk"] = {"total": int((valores.get("disco_total_gb") or 0) * 1024),
                     "used": int((valores.get("disco_usado_gb") or 0) * 1024),
                     "load": int(num("disco_uso")), "activity": 0, "temperature": 0,
                     "readSpeed": num("disco_lectura_mb"), "writeSpeed": num("disco_escritura_mb")}
    datos["network"] = {"upload": num("red_subida_mb"), "download": num("red_bajada_mb")}
    ventiladores = []
    for clave, tipo in (("cpu_vent", "Fan"), ("bomba_vent", "Pump"), ("gpu_vent", "Fan")):
        valor = valores.get(clave)
        if valor:
            ventiladores.append({"onBoard": False, "type": tipo, "name": clave,
                                 "value": int(valor)})
    # SIEMPRE se sobreescribe: antes, si el equipo no exponia sensores de ventilador, se
    # quedaban los de ejemplo y el panel mostraba "Fan AIO Pump 2566 rpm" inventados.
    datos["fans"] = ventiladores
    datos["motherboard"] = {"temperature": num("chipset_temp")}
    datos["timestamp"] = int(time.time() * 1000)
    return datos
