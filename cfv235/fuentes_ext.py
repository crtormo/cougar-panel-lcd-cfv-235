"""Fuentes de datos que no son del PC: clima (Open-Meteo) y notificaciones.

Misma interfaz que `cfv235.sensores.Sensores`: `.muestra()` devuelve un dict de
claves planas (`clima_temp`, `clima_descripcion`...) que se mezclan con las del
equipo en el tema. La filosofia es la de `sensores`: nunca lanzar, degradar a
"--" o a no pintar. La descarga es inyectable (`descargador`) para los tests
(mismo patron que `video.FuentePantalla(capturador=...)`).
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

_log = logging.getLogger("cfv235.fuentes_ext")
_log.addHandler(logging.NullHandler())

# Catalogo WMO 4677-2 recortado (fuente: open-meteo.com/docs/weather-codes)
WMO = {
    0: "Despejado", 1: "Mayormente despejado", 2: "Parcialmente nublado",
    3: "Nublado", 45: "Niebla", 48: "Niebla con escarcha",
    51: "Llovizna leve", 53: "Llovizna", 55: "Llovizna intensa",
    61: "Lluvia leve", 63: "Lluvia", 65: "Lluvia intensa",
    66: "Lluvia helada", 67: "Lluvia helada intensa",
    71: "Nieve leve", 73: "Nieve", 75: "Nieve intensa", 77: "Granos de nieve",
    80: "Chubascos leves", 81: "Chubascos", 82: "Chubascos violentos",
    85: "Chubascos de nieve", 86: "Chubascos de nieve intensos",
    95: "Tormenta", 96: "Tormenta con granizo", 99: "Tormenta con granizo",
}

URL = ("https://api.open-meteo.com/v1/forecast"
       "?latitude={lat}&longitude={lon}"
       "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
       "weather_code,wind_speed_10m"
       "&timezone={tz}")

# Catalogo de las claves externas, con el MISMO formato que `sensores.CATALOGO`:
# (clave, etiqueta, unidad). Es lo que permite que las herramientas que documentan el
# panel (el generador de prompts) listen estas claves sin poder instanciarlas: pedir el
# clima o las notificaciones sacaria a la red o lanzaria procesos, y documentar no debe
# tener efectos secundarios. Se mantiene a mano al lado de `Clima.CLAVES` y
# `Notificaciones.CLAVES`: hay una prueba que comprueba que coinciden.
CATALOGO = (
    ("clima_temp", "Temperatura exterior", "C"),
    ("clima_sensacion", "Sensacion termica", "C"),
    ("clima_humedad", "Humedad exterior", "%"),
    ("clima_viento", "Viento", "km/h"),
    ("clima_codigo", "Codigo WMO", ""),
    ("clima_descripcion", "Cielo", ""),
    ("clima_icono", "Icono del cielo", ""),
    ("notif_cantidad", "Notificaciones", ""),
    ("notif_ultima", "Ultima notificacion", ""),
)


class Clima:
    """Lector del clima con cache. Interfaz publica: `.muestra()` / `.resumen()`.

    `descargador` es `(url, timeout) -> str` inyectable para los tests.

    La cache se sirve mientras tenga menos de `cache_segundos`. Si la descarga falla y
    la ultima muestra tiene menos de `GRACIA_SIN_RED` (2 h) se devuelve la vieja; pasada
    esa frontera `muestra()` devuelve `{}` (el panel se queda sin clima, no con un dato
    de hace medio dia). La gracia es un intervalo semiabierto: edad exactamente igual a
    `GRACIA_SIN_RED` ya NO se sirve. Cambiar `lat`/`lon` invalida la cache: el dato
    guardado es de otro sitio.
    """

    CLAVES = ("clima_temp", "clima_sensacion", "clima_humedad", "clima_viento",
              "clima_codigo", "clima_descripcion", "clima_icono")
    GRACIA_SIN_RED = 7200.0   # 2 h de cache vieja antes de rendirse

    def __init__(self, lat: float = -33.45, lon: float = -70.67,
                 tz: str = "America/Santiago", cache_segundos: float = 900.0,
                 descargador=None):
        self.lat, self.lon, self.tz = float(lat), float(lon), tz
        self.cache_segundos = float(cache_segundos)
        self._descargar = descargador or self._descargar_urllib
        self._stamp = 0.0
        self._valores: dict = {}
        # Ubicacion que produjo la cache. Si `lat`/`lon` cambian, la cache es de otro
        # sitio y no vale: se trata como vencida (ni siquiera se sirve por la gracia).
        self._stamp_ubicacion = (self.lat, self.lon)

    def muestra(self) -> dict:
        """Dict con las claves CLAVES, o {} si no hay datos usables."""
        ahora = time.time()
        ubicacion = (self.lat, self.lon)
        if (self._valores and ubicacion == self._stamp_ubicacion
                and ahora - self._stamp < self.cache_segundos):
            return dict(self._valores)
        try:
            crudo = self._descargar(self._url(), 6.0)
            self._guardar(self._mapear(json.loads(crudo)))
        except Exception as exc:                          # noqa: BLE001 (nunca lanza)
            _log.info("clima no renovado (%s)", exc)
            if (self._valores and ubicacion == self._stamp_ubicacion
                    and ahora - self._stamp < self.GRACIA_SIN_RED):
                return dict(self._valores)
            return {}
        return dict(self._valores)

    def resumen(self) -> list:
        """Snapshot de la cache actual (lista de {"clave", "valor"}).

        No descarga: si nunca se llamo a `muestra()` la cache esta vacia y devuelve [].
        Es simetria con `Sensores.resumen()`, no una fuente de datos por si misma.
        """
        return [{"clave": k, "valor": v} for k, v in self._valores.items()]

    @staticmethod
    def descripcion(codigo) -> str:
        return WMO.get(codigo, "Desconocido")

    def _url(self) -> str:
        return URL.format(lat=self.lat, lon=self.lon, tz=urllib.parse.quote(self.tz))

    @staticmethod
    def _descargar_urllib(url: str, timeout: float) -> str:
        peticion = urllib.request.Request(url, headers={"User-Agent": "cfv235/1.0"})
        with urllib.request.urlopen(peticion, timeout=timeout) as resp:
            return resp.read().decode("utf-8")

    def _guardar(self, valores: dict) -> None:
        self._valores = {k: valores.get(k) for k in self.CLAVES}
        self._stamp = time.time()
        self._stamp_ubicacion = (self.lat, self.lon)

    def _mapear(self, datos: dict) -> dict:
        actual = datos.get("current") or {}
        codigo = actual.get("weather_code")
        return {
            "clima_temp": actual.get("temperature_2m"),
            "clima_sensacion": actual.get("apparent_temperature"),
            "clima_humedad": actual.get("relative_humidity_2m"),
            "clima_viento": actual.get("wind_speed_10m"),
            "clima_codigo": codigo,
            "clima_descripcion": self.descripcion(codigo),
            "clima_icono": self._icono(codigo),
        }

    @staticmethod
    def _icono(codigo) -> str:
        if codigo is None:
            return "?"
        if codigo == 0:
            return "Sol"
        if codigo in (1, 2):
            return "Parcial"
        if codigo == 3 or 45 <= codigo <= 48:
            return "Nubes"
        if 51 <= codigo <= 67 or 80 <= codigo <= 82 or 95 <= codigo <= 99:
            return "Lluvia"
        if 71 <= codigo <= 77 or codigo in (85, 86):
            return "Nieve"
        return "?"


class Notificaciones:
    """Cuenta y resume las notificaciones del escritorio. Interfaz: `.muestra()`.

    PORTABILIDAD HONESTA (investigado en un VPS headless, 2026-09):

    * El estandar freedesktop (`org.freedesktop.Notifications` en D-Bus) define
      `Notify` para ENVIAR y `NotificationClosed`/`ActionInvoked` para AVISAR: no hay
      metodo ni propiedad que devuelva el historial. Por eso `notify-send` no sirve
      para leer y no existe API portable.
    * La API de D-Bus solo se puede *escuchar* (Connect + signal listener en vivo).
      Eso exigiria un hilo/bucle GLib pegado a un bus de sesion, y ademas solo veria
      lo notificado DESPUES de arrancar el panel: no da "ultima notificacion" al
      abrir. YAGNI.
    * GNOME Shell no expone su historial de ningun modo (ni D-Bus ni CLI).
    * Dunst si lo hace: `dunstctl history` imprime el historial en JSON, y
      `dunstctl count history` el total.

    Se implementa el minimo portable: si `dunstctl` existe en el PATH Y hay bus de
    sesion, se lee `dunstctl count history` (contador) mas `dunstctl history` (ultima,
    el historial viene en orden cronologico, el ultimo elemento es la mas reciente).
    En cualquier otro caso (GNOME, KDE, headless, dunst apagado) `muestra()` devuelve
    `{}` y el widget se auto-oculta, que es el patron del repo. No hay dependencias
    nuevas: `subprocess` de stdlib, ni siquiera el `gi` perezoso que ya usa la app GTK.

    `lector` es inyectable (`callable() -> dict` crudo) para los tests y recibe el
    bucket completo `{"history": [...], "count": int|None}`, no una notificacion
    suelta: asi el corte a 20 y la eleccion de la ultima se prueban sin dunst.

    Cachea `cache_segundos` para no lanzar dos procesos por fotograma. Ojo: en la
    ventana de cache, borrar una notificacion no se refleja hasta que expire.
    """

    CLAVES = ("notif_cantidad", "notif_ultima")
    LIMITE = 20          # notificaciones que se retienen en memoria
    CLAVES_TEXTO = ("summary", "appname", "body")   # orden de preferencia del texto

    def __init__(self, lector=None, cache_segundos: float = 5.0, limite: int = LIMITE):
        self._leer = lector or self._leer_dunst
        self.cache_segundos = float(cache_segundos)
        self.limite = max(0, int(limite))
        # Lector que no usa dunst: sin cache, para no esconder el estado real tras la
        # ventana (los tests inyectan historiales distintos y esperan verlos ya).
        self._cachear = lector is None
        self._stamp = 0.0
        self._valores: dict = {}

    def muestra(self) -> dict:
        """`{"notif_cantidad": int, "notif_ultima": str}`, o {} si no hay nada usable."""
        ahora = time.time()
        if (self._valores and self._cachear
                and ahora - self._stamp < self.cache_segundos):
            return dict(self._valores)
        try:
            valores = self._mapear(self._leer())
        except Exception as exc:                          # noqa: BLE001 (nunca lanza)
            _log.info("no se pudo leer las notificaciones (%s)", exc)
            return {}
        if valores:
            self._valores = valores
            self._stamp = ahora
        else:
            self._valores = {}
        return dict(valores)

    def _mapear(self, crudo) -> dict:
        """De `{"history": [...], "count": int|None}` a las claves planas, o {}.

        El historial se recorta a `limite` desde el final: es lo reciente lo que
        importa y no queremos retener en memoria un historial de miles de entradas.
        """
        if not isinstance(crudo, dict):
            return {}
        bruto = crudo.get("history")
        if not isinstance(bruto, list):
            bruto = []

        # El total se lee ANTES de recortar: `count` manda, y sin el el total real es
        # el historial completo (que luego no se retiene entero en memoria).
        cantidad = crudo.get("count")
        if not isinstance(cantidad, int) or isinstance(cantidad, bool):
            cantidad = len(bruto)
        if cantidad <= 0:
            cantidad = len(bruto)

        historial = bruto[-self.limite:] if self.limite else []
        ultima = self._texto(historial[-1]) if historial else ""
        if cantidad <= 0 and not ultima:
            # Ni contador ni texto: no hay nada que pintar, el widget se auto-oculta.
            return {}
        return {"notif_cantidad": int(cantidad), "notif_ultima": ultima}

    @classmethod
    def _texto(cls, entrada) -> str:
        """Texto legible de una entrada de `dunstctl history`.

        Cada campo viene como `{"type": "s", "data": "..."}`; el titulo ("summary")
        es lo util para un widget, con la app entre parentesis. Se limpia el marcado
        Pango (`<b>`, `<i>`...) porque el panel pinta texto plano.
        """
        if not isinstance(entrada, dict):
            return ""
        campos = {clave: cls._campo(entrada.get(clave)) for clave in cls.CLAVES_TEXTO}
        titulo = cls._limpiar(campos["summary"])
        cuerpo = cls._limpiar(campos["body"])
        app = cls._limpiar(campos["appname"])
        if not titulo:
            # Sin titulo: la primera linea del cuerpo hace de titular.
            titulo, _, resto = cuerpo.partition("\n")
            cuerpo = resto
        if not titulo:
            return app
        if app:
            return "{} ({})".format(titulo, app)
        return titulo

    @classmethod
    def _campo(cls, valor) -> str:
        """`{"data": "..."}` -> "...". Tolerante a que ya venga como cadena suelta."""
        if isinstance(valor, dict):
            valor = valor.get("data")
        return valor if isinstance(valor, str) else ""

    @staticmethod
    def _limpiar(texto: str) -> str:
        """Quita el marcado Pango y colapsa espacios/saltos a una sola linea."""
        import re

        texto = re.sub(r"<[^<>]{1,40}>", "", texto)
        return " ".join(texto.split())

    @classmethod
    def _leer_dunst(cls, timeout: float = 2.0) -> dict:
        """Camino real: `dunstctl count history` + `dunstctl history`.

        Devuelve `{}` sin intentarlo siquiera si no hay `dunstctl` o no hay bus de
        sesion (headless, systemd sin sesion, SSH sin DBUS_SESSION_BUS_ADDRESS): no
        vale la pena pagar dos procesos para que dunst conteste "no hay bus".
        """
        ruta = shutil.which("dunstctl")
        if not ruta or not cls._hay_bus():
            return {}
        return {"count": cls._contar_dunst(ruta, timeout),
                "history": cls._historial_dunst(ruta, timeout)}

    @staticmethod
    def _hay_bus() -> bool:
        """Heuristica de bus de sesion, sin abrir conexion: variable o socket."""
        import os

        if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return True
        return os.path.exists("/run/user/{}/bus".format(os.getuid()))

    @classmethod
    def _contar_dunst(cls, ruta: str, timeout: float):
        """`dunstctl count history` -> int, o None si no se pudo interpretar."""
        try:
            salida = cls._ejecutar([ruta, "count", "history"], timeout)
            return int((salida or "").strip())
        except Exception as exc:                          # noqa: BLE001 (nunca lanza)
            _log.info("dunstctl count history no respondio (%s)", exc)
            return None

    @classmethod
    def _historial_dunst(cls, ruta: str, timeout: float) -> list:
        """`dunstctl history` -> lista cruda (es JSON: viene en una sola linea larga)."""
        try:
            salida = cls._ejecutar([ruta, "history"], timeout)
            datos = json.loads(salida or "")
        except Exception as exc:                          # noqa: BLE001 (nunca lanza)
            _log.info("dunstctl history no devolvio JSON (%s)", exc)
            return []
        if isinstance(datos, dict):
            datos = datos.get("data", datos.get("history", []))
        return datos if isinstance(datos, list) else []

    @staticmethod
    def _ejecutar(orden: list, timeout: float) -> str:
        """Corre un comando con timeout corto. Cualquier fallo es cadena vacia."""
        try:
            proceso = subprocess.run(orden, capture_output=True, text=True,
                                     timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.info("%s no respondio (%s)", orden[0], exc)
            return ""
        if proceso.returncode != 0:
            _log.info("%s salio con %s", orden[0], proceso.returncode)
            return ""
        return proceso.stdout or ""


class Combinada:
    """Envuelve una fuente base (Sensores o dict) y anade fuentes externas.

    `.muestra()` fusiona; una fuente caida no tumba a ninguna de las otras. Es lo que
    recibe `widgets._Lector`, asi que nada mas cambia en el motor.
    """

    def __init__(self, base, clima=None, notificaciones=None):
        self._base = base
        self._clima = clima                 # inyectables para los tests; None -> sin fuente
        self._notificaciones = notificaciones

    @staticmethod
    def _externas(objeto):
        """Claves de una fuente externa (`muestra()`), o {} si esta caida.

        Cada fuente va en su propio try/except: que dunst no responda no puede dejar al
        panel sin clima, ni al reves.
        """
        if objeto is None:
            return {}
        try:
            return dict(objeto.muestra() or {})
        except Exception as exc:                          # noqa: BLE001 (nunca lanza)
            _log.info("una fuente externa no respondio (%s)", exc)
            return {}

    def muestra(self) -> dict:
        base = self._base
        if isinstance(base, Combinada):                   # nunca envolver dos veces
            base = base.muestra()
        try:
            valores = (dict(base.muestra()) if hasattr(base, "muestra")
                       else dict(base or {}))
        except Exception as exc:                          # noqa: BLE001 (nunca lanza)
            _log.info("la fuente base de datos no respondio (%s)", exc)
            valores = {}
        valores.update(self._externas(self._clima))
        valores.update(self._externas(self._notificaciones))
        return valores

    def _resumen_de(self, objeto) -> list:
        """Resumen de una fuente externa, o [] si no lo tiene o esta caida."""
        propio = getattr(objeto, "resumen", None)
        if objeto is None or not callable(propio):
            return []
        try:
            return list(propio() or ())
        except Exception as exc:                          # noqa: BLE001 (nunca lanza)
            _log.info("una fuente externa no respondio (%s)", exc)
            return []

    def resumen(self) -> list:
        """Resumen de la base (si lo tiene) mas el de las fuentes externas.

        El motor no lo usa (`_Lector` solo llama a `muestra()`): existe para que
        `Combinada` presente la misma interfaz que sus fuentes.
        """
        filas = []
        try:
            propio = getattr(self._base, "resumen", None)
            if callable(propio):
                filas.extend(propio() or ())
        except Exception as exc:                          # noqa: BLE001 (nunca lanza)
            _log.info("la fuente base de datos no respondio (%s)", exc)
        filas.extend(self._resumen_de(self._clima))
        filas.extend(self._resumen_de(self._notificaciones))
        return filas
