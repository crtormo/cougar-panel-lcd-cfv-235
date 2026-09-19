"""Tests de las fuentes de datos externas (clima)."""
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


class TestClima(unittest.TestCase):
    def test_mapeo_de_respuesta(self):
        fuente = fuentes_ext.Clima(descargador=lambda url, timeout: json.dumps(RESPUESTA_OM))
        valores = fuente.muestra()
        self.assertEqual(valores["clima_temp"], 14.3)
        self.assertEqual(valores["clima_sensacion"], 12.8)
        self.assertEqual(valores["clima_humedad"], 62)
        self.assertEqual(valores["clima_viento"], 11.2)
        self.assertEqual(valores["clima_descripcion"], "Nublado")  # codigo WMO 3
        self.assertEqual(valores["clima_icono"], "Nubes")

    def test_cache_no_repite_descarga(self):
        llamadas = []

        def falso_get(url, timeout):
            llamadas.append(url)
            return json.dumps(RESPUESTA_OM)

        fuente = fuentes_ext.Clima(cache_segundos=900, descargador=falso_get)
        fuente.muestra()
        fuente.muestra()
        self.assertEqual(len(llamadas), 1)

    def test_sin_red_degrada_a_cache_vieja(self):
        # Fuente que ya tiene datos buenos en cache: se le envejece la marca a mano.
        fuente = fuentes_ext.Clima(cache_segundos=1,
                                   descargador=lambda u, t: json.dumps(RESPUESTA_OM))
        fuente.muestra()
        self.assertEqual(fuente.muestra().get("clima_temp"), 14.3)

        # Ahora la red falla. Dentro de la gracia (1 h) se sirve la cache vieja.
        fuente._descargar = _roto
        fuente._stamp -= 3600
        self.assertEqual(fuente.muestra().get("clima_temp"), 14.3)

        # Fuera de la gracia (3 h) ya no hay nada que servir.
        fuente._stamp -= 3 * 3600
        self.assertEqual(fuente.muestra(), {})

    def test_descripcion_cubre_codigos_wmo(self):
        self.assertEqual(fuentes_ext.Clima.descripcion(0), "Despejado")
        self.assertEqual(fuentes_ext.Clima.descripcion(61), "Lluvia leve")
        self.assertEqual(fuentes_ext.Clima.descripcion(71), "Nieve leve")
        self.assertEqual(fuentes_ext.Clima.descripcion(999), "Desconocido")


def _roto(url, timeout):
    """Descargador que siempre falla (simula estar sin red)."""
    raise OSError("sin red")


class TestCombinada(unittest.TestCase):
    def test_claves_visibles_en_catalogo(self):
        from cfv235 import temas
        propias = temas.fuentes_disponibles(instanciar=False)["propias"]
        for clave in fuentes_ext.Clima.CLAVES:
            self.assertIn(clave, propias)

    def test_combinada_fusiona_base_y_clima(self):
        base = {"cpu_uso": 20, "ram_uso": 50}
        combinada = fuentes_ext.Combinada(base, clima=fuentes_ext.Clima(
            descargador=lambda u, t: json.dumps(RESPUESTA_OM)))
        muestra = combinada.muestra()
        self.assertEqual(muestra["cpu_uso"], 20)
        self.assertEqual(muestra["ram_uso"], 50)
        self.assertEqual(muestra["clima_temp"], 14.3)
        self.assertEqual(muestra["clima_descripcion"], "Nublado")

    def test_clima_sin_datos_no_pisa_la_base(self):
        base = {"cpu_uso": 20}
        combinada = fuentes_ext.Combinada(base, clima=None)
        self.assertEqual(combinada.muestra(), {"cpu_uso": 20})

    def test_base_rota_no_tumba_al_clima(self):
        class BaseRota:
            def muestra(self):
                raise RuntimeError("sin /proc")

        combinada = fuentes_ext.Combinada(BaseRota(), clima=fuentes_ext.Clima(
            descargador=lambda u, t: json.dumps(RESPUESTA_OM)))
        self.assertEqual(combinada.muestra()["clima_temp"], 14.3)


class TestIntegracion(unittest.TestCase):
    """El motor ya pinta el clima: no hizo falta codigo nuevo en `widgets`."""

    def test_widget_texto_con_marcador_clima(self):
        """El motor resuelve {clima_*} via _Lector sin codigo nuevo."""
        from cfv235 import widgets
        lector = widgets._Lector({"clima_temp": 14.3, "clima_descripcion": "Nublado"})
        texto = widgets.texto_con_marcadores(
            "Afueras: {clima_temp}°, {clima_descripcion}", lector)
        self.assertIn("14.3", texto)
        self.assertIn("Nublado", texto)

    def test_marcador_clima_formateado(self):
        """`{clima_temp:.0f}` y `{clima_humedad}%` tambien salen."""
        from cfv235 import widgets
        lector = widgets._Lector({"clima_temp": 14.3, "clima_humedad": 62})
        self.assertEqual(widgets.texto_con_marcadores("{clima_temp:.0f} C", lector), "14 C")
        self.assertEqual(widgets.texto_con_marcadores("{clima_humedad}% hum", lector), "62% hum")

    def test_combinada_alimenta_el_motor(self):
        """La cadena completa: Sensores+Clima -> _Lector -> texto con marcadores."""
        from cfv235 import fuentes_ext, widgets
        combinada = fuentes_ext.Combinada(
            {"cpu_uso": 20},
            clima=fuentes_ext.Clima(descargador=lambda u, t: json.dumps(RESPUESTA_OM)))
        lector = widgets._Lector(combinada)
        texto = widgets.texto_con_marcadores("{clima_temp}°, {clima_descripcion} · "
                                             "{cpu_uso}% cpu", lector)
        self.assertIn("14.3", texto)
        self.assertIn("Nublado", texto)
        self.assertIn("20% cpu", texto)

    def test_sin_clima_el_marcador_degrada(self):
        """Sin fuente de clima el marcador pinta '--', no rompe el tema."""
        from cfv235 import widgets
        lector = widgets._Lector({"cpu_uso": 20})
        self.assertEqual(widgets.texto_con_marcadores("{clima_temp}°", lector), "--°")


if __name__ == "__main__":
    unittest.main(verbosity=2)
