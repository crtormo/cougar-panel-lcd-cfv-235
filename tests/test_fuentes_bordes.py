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


def _campo(dato):
    """Envuelve un valor como lo hace dunst: `{"type": "s", "data": ...}`."""
    return {"type": "s", "data": dato}


def _entrada(titulo="", app="", cuerpo=""):
    """Entrada de `dunstctl history` con el envoltorio de tipo por campo."""
    return {"summary": _campo(titulo), "appname": _campo(app), "body": _campo(cuerpo)}


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


class TestNotificaciones(unittest.TestCase):
    """Lector de notificaciones: degrada a {} y no lanza sin dunst ni bus.

    La investigacion (ver el docstring de `fuentes_ext.Notificaciones`) concluyo que
    no hay API portable: el estandar freedesktop no expone historial y GNOME tampoco.
    Solo Dunst (`dunstctl`) lo da, asi que todo se prueba con `lector` inyectable.
    """

    def test_lector_inyectado_no_toca_dunst(self):
        """Con `lector` inyectado nunca se llama a dunstctl: se prueba en un headless."""
        llamadas = []

        def falso(orden, timeout):
            llamadas.append(orden)
            raise AssertionError("no debe ejecutarse nada con lector inyectado")

        original = fuentes_ext.Notificaciones._ejecutar
        fuentes_ext.Notificaciones._ejecutar = staticmethod(falso)
        try:
            muestra = fuentes_ext.Notificaciones(
                lector=lambda: {"history": [], "count": None}).muestra()
        finally:
            fuentes_ext.Notificaciones._ejecutar = original
        self.assertEqual(llamadas, [])
        self.assertIsInstance(muestra, dict)

    def test_sin_dunst_no_lanza_y_devuelve_vacio(self):
        """En este VPS no hay dunstctl ni bus: `{}` y sin excepcion."""
        self.assertEqual(fuentes_ext.Notificaciones().muestra(), {})

    def test_la_ultima_es_la_mas_reciente_del_historial(self):
        crudo = {"count": 3, "history": [_entrada("vieja", "app1"),
                                         _entrada("media", "app2"),
                                         _entrada("nueva", "app3")]}
        muestra = fuentes_ext.Notificaciones(lector=lambda: crudo).muestra()
        self.assertEqual(muestra, {"notif_cantidad": 3, "notif_ultima": "nueva (app3)"})

    def test_el_historial_se_recorta_al_limite(self):
        """Se retienen 20 entradas: la ultima del historial grande sigue siendo la ultima."""
        crudo = {"count": 500, "history": [_entrada("n%d" % i) for i in range(500)]}
        notificaciones = fuentes_ext.Notificaciones(lector=lambda: crudo)
        self.assertEqual(notificaciones.muestra()["notif_cantidad"], 500)
        self.assertEqual(notificaciones.muestra()["notif_ultima"], "n499")

    def test_historial_mas_largo_que_el_limite_sin_count_usa_el_total(self):
        crudo = {"history": [_entrada("n%d" % i) for i in range(50)]}
        self.assertEqual(fuentes_ext.Notificaciones(lector=lambda: crudo).muestra(),
                         {"notif_cantidad": 50, "notif_ultima": "n49"})

    def test_historial_vacio_no_pinta(self):
        self.assertEqual(fuentes_ext.Notificaciones(
            lector=lambda: {"history": [], "count": 0}).muestra(), {})

    def test_solo_count_sin_historial_si_pinta_el_contador(self):
        """`count` > 0 sin historial legible: queda el contador, sin texto de ultima."""
        self.assertEqual(fuentes_ext.Notificaciones(
            lector=lambda: {"history": [], "count": 3}).muestra(),
            {"notif_cantidad": 3, "notif_ultima": ""})

    def test_una_entrada_sin_texto_no_pinta_pero_el_contador_sobrevive(self):
        """Entrada vacia: no hay "ultima", pero el contador es un dato valido."""
        muestra = fuentes_ext.Notificaciones(lector=lambda: {"history": [{}],
                                                             "count": 1}).muestra()
        self.assertEqual(muestra, {"notif_cantidad": 1, "notif_ultima": ""})

    def test_entrada_plana_sin_envoltorio_de_tipo(self):
        """`{"summary": "hola"}` a secas tambien vale: no todo dunst envuelve."""
        muestra = fuentes_ext.Notificaciones(
            lector=lambda: {"history": [{"summary": "hola"}], "count": 1}).muestra()
        self.assertEqual(muestra["notif_ultima"], "hola")

    def test_usa_el_cuerpo_si_no_hay_titulo(self):
        entrada = {"body": {"type": "s", "data": "primera linea\nsegunda linea"}}
        muestra = fuentes_ext.Notificaciones(
            lector=lambda: {"history": [entrada], "count": 1}).muestra()
        self.assertEqual(muestra["notif_ultima"], "primera linea segunda linea")

    def test_limpia_el_marcado_pango(self):
        """El panel pinta texto plano: fuera `<b>` y compañia."""
        muestra = fuentes_ext.Notificaciones(
            lector=lambda: {"history": [_entrada("<b>Titulo</b>")], "count": 1}).muestra()
        self.assertEqual(muestra["notif_ultima"], "Titulo")

    def test_lector_roto_no_lanza(self):
        def roto():
            raise RuntimeError("dunst se cayo")

        self.assertEqual(fuentes_ext.Notificaciones(lector=roto).muestra(), {})

    def test_lector_que_devuelve_basura_no_lanza(self):
        for crudo in (None, [], "texto", 42, {"history": "no es lista", "count": "x"}):
            with self.subTest(crudo=crudo):
                self.assertEqual(
                    fuentes_ext.Notificaciones(lector=lambda c=crudo: c).muestra(), {})

    def test_las_claves_son_las_documentadas(self):
        crudo = {"history": [_entrada("algo")], "count": 1}
        muestra = fuentes_ext.Notificaciones(lector=lambda: crudo).muestra()
        self.assertEqual(set(muestra), set(fuentes_ext.Notificaciones.CLAVES))
        self.assertIsInstance(muestra["notif_cantidad"], int)
        self.assertIsInstance(muestra["notif_ultima"], str)

    def test_dunstctl_ausente_no_ejecuta_procesos(self):
        """Sin `dunstctl` en el PATH, `_leer_dunst` ni lo intenta (no hay subprocess)."""
        original = fuentes_ext.shutil.which
        fuentes_ext.shutil.which = lambda nombre: None
        try:
            self.assertEqual(fuentes_ext.Notificaciones._leer_dunst(), {})
        finally:
            fuentes_ext.shutil.which = original

    def test_sin_bus_de_sesion_dunst_no_se_ejecuta(self):
        """Con dunstctl presente pero sin bus (headless) tampoco se lanzan procesos."""
        llamadas = []

        def falso(orden, timeout):
            llamadas.append(orden)
            return ""

        originales = (fuentes_ext.shutil.which, fuentes_ext.Notificaciones._ejecutar,
                      fuentes_ext.Notificaciones._hay_bus)
        fuentes_ext.shutil.which = lambda nombre: "/usr/bin/dunstctl"
        fuentes_ext.Notificaciones._ejecutar = staticmethod(falso)
        fuentes_ext.Notificaciones._hay_bus = staticmethod(lambda: False)
        try:
            self.assertEqual(fuentes_ext.Notificaciones._leer_dunst(), {})
        finally:
            (fuentes_ext.shutil.which, fuentes_ext.Notificaciones._ejecutar,
             fuentes_ext.Notificaciones._hay_bus) = originales
        self.assertEqual(llamadas, [])

    def test_historial_de_dunst_se_parsea_del_json_del_comando(self):
        """`dunstctl history` real: JSON de una linea con el historial bajo `data`."""
        historial = json.dumps({"type": "aa{sv}", "data": [_entrada("hola", "app")]})

        def falso(orden, timeout):
            return "1\n" if orden[-1] == "history" and "count" in orden else historial

        original = fuentes_ext.Notificaciones._ejecutar
        fuentes_ext.Notificaciones._ejecutar = staticmethod(falso)
        try:
            crudo = {"count": fuentes_ext.Notificaciones._contar_dunst("dunstctl", 2.0),
                     "history": fuentes_ext.Notificaciones._historial_dunst("dunstctl", 2.0)}
        finally:
            fuentes_ext.Notificaciones._ejecutar = original
        self.assertEqual(crudo["count"], 1)
        self.assertEqual(fuentes_ext.Notificaciones(
            lector=lambda: crudo).muestra()["notif_ultima"], "hola (app)")

    def test_un_comando_que_falla_no_lanza(self):
        original = fuentes_ext.Notificaciones._ejecutar

        def falso(orden, timeout):
            raise FileNotFoundError("dunstctl se fue")

        fuentes_ext.Notificaciones._ejecutar = staticmethod(falso)
        try:
            with self.assertRaises(FileNotFoundError):
                fuentes_ext.Notificaciones._ejecutar(["dunstctl"], 2.0)
            self.assertEqual(fuentes_ext.Notificaciones._contar_dunst("dunstctl", 2.0),
                             None)
            self.assertEqual(fuentes_ext.Notificaciones._historial_dunst("dunstctl", 2.0),
                             [])
        finally:
            fuentes_ext.Notificaciones._ejecutar = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
