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


class Combinada:
    """Envuelve una fuente base (Sensores o dict) y anade fuentes externas.

    `.muestra()` fusiona; una fuente caida no tumba a la otra. Es lo que recibe
    `widgets._Lector`, asi que nada mas cambia en el motor.
    """

    def __init__(self, base, clima=None):
        self._base = base
        self._clima = clima   # inyectable para tests; None -> sin clima

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
        if self._clima is not None:
            try:
                valores.update(self._clima.muestra() or {})
            except Exception as exc:                      # noqa: BLE001
                _log.info("la fuente de clima no respondio (%s)", exc)
        return valores

    def resumen(self) -> list:
        """Resumen de la base (si lo tiene) mas las claves del clima, si las hay.

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
        if self._clima is not None:
            try:
                filas.extend(self._clima.resumen() or ())
            except Exception as exc:                      # noqa: BLE001
                _log.info("la fuente de clima no respondio (%s)", exc)
        return filas
