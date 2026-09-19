"""Bordes de `fuentes_ext`: frontera de la gracia sin red, ubicacion y `resumen()`."""
import json
import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)          # ejecutable tambien a mano: python3 -B tests/...

from cfv235 import fuentes_ext

RESPUESTA_OM = {
    "latitude": -33.45, "longitude": -70.67,
    "current": {
        "time": "2026-09-19T18:00", "temperature_2m": 14.3,
        "apparent_temperature": 12.8, "relative_humidity_2m": 62,
        "weather_code": 3, "wind_speed_10m": 11.2,
    },
}


def _roto(url, timeout):
    """Descargador que siempre falla (simula estar sin red)."""
    raise OSError("sin red")


def _falso(url, timeout):
    return json.dumps(RESPUESTA_OM)


class TestFronteraDeLaGracia(unittest.TestCase):
    """`GRACIA_SIN_RED` = 7200 s. El limite es exclusivo: a 7200 s exactos ya no vale."""

    GRACIA = fuentes_ext.Clima.GRACIA_SIN_RED

    def _clima_vencido(self, edad):
        """Clima con cache de un dato bueno envejecida exactamente `edad` segundos."""
        self.assertEqual(self.GRACIA, 7200.0, "la frontera de la gracia cambio")
        clima = fuentes_ext.Clima(cache_segundos=1.0, descargador=_falso)
        clima.muestra()
        self.assertEqual(clima._valores.get("clima_temp"), 14.3)
        clima._descargar = _roto
        clima._stamp = clima._stamp - edad
        return clima

    def test_7199_segundos_sirve_la_cache(self):
        self.assertEqual(self._clima_vencido(7199.0).muestra().get("clima_temp"), 14.3)

    def test_7200_segundos_justos_ya_no_sirve(self):
        self.assertEqual(self._clima_vencido(7200.0).muestra(), {})

    def test_7201_segundos_tampoco(self):
        self.assertEqual(self._clima_vencido(7201.0).muestra(), {})

    def test_sin_cache_previa_no_hay_nada_que_servir(self):
        clima = fuentes_ext.Clima(descargador=_roto)
        self.assertEqual(clima.muestra(), {})


class TestInvalidacionPorUbicacion(unittest.TestCase):
    """Mover las coordenadas invalida la cache: el dato es de otro sitio."""

    def test_cambiar_lat_o_lon_redescarga(self):
        urls = []

        def falso(url, timeout):
            urls.append(url)
            return json.dumps(RESPUESTA_OM)

        clima = fuentes_ext.Clima(cache_segundos=900, descargador=falso)
        clima.muestra()
        self.assertEqual(len(urls), 1)

        clima.lat, clima.lon = -40.0, -73.0
        clima.muestra()
        self.assertEqual(len(urls), 2)
        # La URL lleva las coordenadas nuevas (Open-Meteo no usa notacion exponencial).
        self.assertIn("-40", urls[1])
        self.assertIn("-73", urls[1])
        self.assertNotIn("-33.45", urls[1])

    def test_misma_ubicacion_sigue_usando_la_cache(self):
        urls = []

        def falso(url, timeout):
            urls.append(url)
            return json.dumps(RESPUESTA_OM)

        clima = fuentes_ext.Clima(cache_segundos=900, descargador=falso)
        clima.muestra()
        clima.lat, clima.lon = clima.lat, clima.lon      # reasignar no invalida
        clima.muestra()
        self.assertEqual(len(urls), 1)

    def test_la_cache_vencida_por_ubicacion_no_se_sirve_sin_red(self):
        """Tras mover la ubicacion, la cache vieja es de otro punto: no vale de gracia."""
        clima = fuentes_ext.Clima(cache_segundos=1.0, descargador=_falso)
        clima.muestra()
        clima._descargar = _roto
        clima.lat = -40.0
        self.assertEqual(clima.muestra(), {})


class TestResumen(unittest.TestCase):
    def test_resumen_es_un_snapshot_de_la_cache(self):
        clima = fuentes_ext.Clima(descargador=_falso)
        self.assertEqual(clima.resumen(), [], "sin muestra() no hay nada que resumir")
        clima.muestra()
        resumen = clima.resumen()
        valores = {fila["clave"]: fila["valor"] for fila in resumen}
        self.assertEqual(valores["clima_temp"], 14.3)
        self.assertEqual(set(valores), set(fuentes_ext.Clima.CLAVES))

    def test_combinada_resumen_delega_en_la_base(self):
        class Base:
            def muestra(self):
                return {"cpu_uso": 20}

            def resumen(self):
                return [{"clave": "cpu_uso", "valor": 20}]

        combinada = fuentes_ext.Combinada(Base(), clima=fuentes_ext.Clima(descargador=_falso))
        combinada.muestra()
        resumen = {fila["clave"]: fila["valor"] for fila in combinada.resumen()}
        self.assertEqual(resumen["cpu_uso"], 20)
        self.assertEqual(resumen["clima_temp"], 14.3)

    def test_combinada_resumen_sin_clima_es_el_de_la_base(self):
        combinada = fuentes_ext.Combinada({"cpu_uso": 20}, clima=None)
        self.assertEqual(combinada.resumen(), [])

    def test_combinada_resumen_de_una_base_sin_resumen(self):
        combinada = fuentes_ext.Combinada({"cpu_uso": 20}, clima=fuentes_ext.Clima(
            descargador=_falso))
        combinada.muestra()
        claves = {fila["clave"] for fila in combinada.resumen()}
        self.assertEqual(claves, set(fuentes_ext.Clima.CLAVES))

    def test_combinada_resumen_con_la_base_rota_no_lanza(self):
        class BaseRota:
            def muestra(self):
                raise RuntimeError("sin /proc")

            def resumen(self):
                raise RuntimeError("sin /proc")

        combinada = fuentes_ext.Combinada(BaseRota(), clima=fuentes_ext.Clima(
            descargador=_falso))
        self.assertIsInstance(combinada.resumen(), list)


class TestClimaQueDevuelveNone(unittest.TestCase):
    """`Clima.muestra()` puede devolver {} (sin red): la base no se pisa."""

    def test_una_fuente_de_clima_vacia_no_borra_la_base(self):
        base = {"cpu_uso": 20, "ram_uso": 50}
        combinada = fuentes_ext.Combinada(base, clima=fuentes_ext.Clima(descargador=_roto))
        self.assertEqual(combinada.muestra(), base)

    def test_la_base_sigue_entera_en_el_siguiente_fotograma(self):
        combinatoria = fuentes_ext.Combinada({"cpu_uso": 20},
                                             clima=fuentes_ext.Clima(descargador=_roto))
        for _ in range(3):
            self.assertEqual(combinatoria.muestra(), {"cpu_uso": 20})


if __name__ == "__main__":
    unittest.main(verbosity=2)
