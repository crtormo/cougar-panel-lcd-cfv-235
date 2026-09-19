"""`widgets.crear_fuentes()` y su lectura de la configuracion (clima opt-in).

Estos tests **no tocan la red**: el descargador real de `Clima` se sustituye por un
monkeypatch de `Clima._descargar_urllib`, y la configuracion se escribe en un directorio
temporal (`CFV235_CONFIG_DIR`) que se restaura siempre en `tearDown`.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)          # ejecutable tambien a mano: python3 -B tests/...

from cfv235 import config, fuentes_ext, widgets

RESPUESTA_OM = {
    "latitude": -33.45, "longitude": -70.67,
    "current": {
        "time": "2026-09-19T18:00", "temperature_2m": 14.3,
        "apparent_temperature": 12.8, "relative_humidity_2m": 62,
        "weather_code": 3, "wind_speed_10m": 11.2,
    },
}


def _descarga_falsa(url, timeout):
    return json.dumps(RESPUESTA_OM)


class BaseConDescargaFalsa(unittest.TestCase):
    """Aisla la configuracion (dir temporal) y la red (descargador inyectado)."""

    def _clima(self):
        """`_clima_de_config()` comprobando que hay clima (para el linter, no para el test)."""
        clima = widgets._clima_de_config()
        assert clima is not None, "la configuracion pide clima"
        return clima

    def setUp(self):
        self._previo = os.environ.get("CFV235_CONFIG_DIR")
        self._carpeta = tempfile.mkdtemp(prefix="cfv235-config-")
        os.environ["CFV235_CONFIG_DIR"] = self._carpeta
        self._descargar_real = fuentes_ext.Clima._descargar_urllib
        fuentes_ext.Clima._descargar_urllib = staticmethod(_descarga_falsa)
        widgets._reset_fuentes()

    def tearDown(self):
        fuentes_ext.Clima._descargar_urllib = self._descargar_real
        widgets._reset_fuentes()
        if self._previo is None:
            os.environ.pop("CFV235_CONFIG_DIR", None)
        else:
            os.environ["CFV235_CONFIG_DIR"] = self._previo
        shutil.rmtree(self._carpeta, ignore_errors=True)


class TestClimaDeConfig(BaseConDescargaFalsa):
    def test_apagado_devuelve_none(self):
        config.escribir(clima=False)
        self.assertIsNone(widgets._clima_de_config())

    def test_encendido_usa_los_valores_de_la_configuracion(self):
        config.escribir(clima=True, clima_lat=-33.45, clima_lon=-70.67,
                        clima_tz="America/Santiago", clima_cache_min=30)
        clima = self._clima()
        self.assertAlmostEqual(clima.lat, -33.45)
        self.assertAlmostEqual(clima.lon, -70.67)
        self.assertEqual(clima.tz, "America/Santiago")
        self.assertEqual(clima.cache_segundos, 30 * 60.0)

    def test_cache_negativa_se_ignora(self):
        """Un `clima_cache_min` de -5 daba cache de -300 s: descarga en cada llamada."""
        config.escribir(clima=True, clima_cache_min=-5)
        self.assertEqual(self._clima().cache_segundos, 15 * 60.0)

    def test_cache_cero_se_ignora(self):
        config.escribir(clima=True, clima_cache_min=0)
        self.assertEqual(self._clima().cache_segundos, 15 * 60.0)


class TestCrearFuentesOptIn(BaseConDescargaFalsa):
    def test_sin_clima_no_descarga_nada(self):
        """Con `clima=False` no se crea ni un `Clima`: cero descargas."""
        config.escribir(clima=False)
        fuentes = widgets.crear_fuentes()
        self.assertFalse(isinstance(fuentes, fuentes_ext.Combinada))

    def test_con_clima_devuelve_combinada_con_las_claves_del_tiempo(self):
        config.escribir(clima=True)
        fuentes = widgets.crear_fuentes()
        self.assertIsInstance(fuentes, fuentes_ext.Combinada)
        self.assertEqual(fuentes.muestra().get("clima_temp"), 14.3)

    def test_con_descarga_rota_devuelve_combinada_sin_datos_de_clima(self):
        """El clima caido no puede tumbar las fuentes del equipo."""
        config.escribir(clima=True)

        def roto(url, timeout):
            raise OSError("sin red")

        fuentes_ext.Clima._descargar_urllib = staticmethod(roto)
        fuentes = widgets.crear_fuentes()
        self.assertIsInstance(fuentes, fuentes_ext.Combinada)
        self.assertIsNone(fuentes.muestra().get("clima_temp"))


class TestMemoizacion(BaseConDescargaFalsa):
    """La instancia sobrevive a los fotogramas: es lo que hace util la cache."""

    def _contar(self):
        llamadas = []

        def falso(url, timeout):
            llamadas.append(url)
            return json.dumps(RESPUESTA_OM)

        fuentes_ext.Clima._descargar_urllib = staticmethod(falso)
        return llamadas

    def test_la_misma_instancia_entre_llamadas(self):
        config.escribir(clima=True)
        primera = widgets.crear_fuentes()
        segunda = widgets.crear_fuentes()
        self.assertIs(primera, segunda)

    def test_cinco_fotogramas_una_sola_descarga(self):
        """El dashboard redibuja cada 2 s: sin memoizar eran 5 descargas."""
        config.escribir(clima=True, clima_cache_min=15)
        llamadas = self._contar()
        for _ in range(5):
            widgets.crear_fuentes().muestra()
        self.assertEqual(len(llamadas), 1)

    def test_cambiar_la_configuracion_renueva_la_instancia(self):
        config.escribir(clima=True, clima_lat=-33.45, clima_lon=-70.67)
        primera = widgets.crear_fuentes()
        config.escribir(clima_lat=-40.0, clima_lon=-73.0)
        segunda = widgets.crear_fuentes()
        self.assertIsNot(segunda, primera)
        clima = self._clima()
        self.assertAlmostEqual(clima.lat, -40.0)
        self.assertAlmostEqual(clima.lon, -73.0)

    def test_reset_fuentes_permite_limpiar(self):
        config.escribir(clima=True)
        primera = widgets.crear_fuentes()
        widgets._reset_fuentes()
        self.assertIsNot(widgets.crear_fuentes(), primera)

    def test_sin_configuracion_de_clima_no_memoiza_el_clima(self):
        """Apagado sigue dando la fuente de sensores pelada, llamada a llamada."""
        config.escribir(clima=False)
        self.assertFalse(isinstance(widgets.crear_fuentes(), fuentes_ext.Combinada))
        widgets._clima_de_config()   # no deja basura para la siguiente


if __name__ == "__main__":
    unittest.main(verbosity=2)
