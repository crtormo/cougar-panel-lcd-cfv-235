"""Notificaciones como fuente opt-in: `config.notificaciones` y `Combinada`.

`fuentes_ext.Notificaciones` funcionaba pero no estaba en la cadena de datos: nadie la
instanciaba. Aqui se prueba el opt-in (apagado por defecto: ni un subprocess) y que, con
el lector roto, la base (el equipo) sigue intacta.

    python3 -B tests/test_fuentes_notif.py
"""
import os
import shutil
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)          # ejecutable tambien a mano: python3 -B tests/...

from cfv235 import config, fuentes_ext, widgets  # noqa: E402


def _campo(dato):
    """Envuelve un valor como lo hace dunst: `{"type": "s", "data": ...}`."""
    return {"type": "s", "data": dato}


def _historial(titulos):
    return {"count": len(titulos),
            "history": [{"summary": _campo(t), "appname": _campo("app")}
                        for t in titulos]}


class BaseConConfig(unittest.TestCase):
    """Aisla la configuracion en un directorio temporal (nunca toca la del usuario)."""

    def setUp(self):
        self._previo = os.environ.get("CFV235_CONFIG_DIR")
        self._carpeta = tempfile.mkdtemp(prefix="cfv235-notif-")
        os.environ["CFV235_CONFIG_DIR"] = self._carpeta
        widgets._reset_fuentes()

    def tearDown(self):
        widgets._reset_fuentes()
        if self._previo is None:
            os.environ.pop("CFV235_CONFIG_DIR", None)
        else:
            os.environ["CFV235_CONFIG_DIR"] = self._previo
        shutil.rmtree(self._carpeta, ignore_errors=True)


class TestClaveDeConfig(BaseConConfig):
    def test_por_defecto_apagada(self):
        self.assertIs(config.DEFECTOS["notificaciones"], False)

    def test_el_tipo_se_comprueba_al_leer(self):
        self.assertIs(config._TIPOS["notificaciones"], bool)

    def test_un_valor_no_booleano_se_ignora(self):
        """Igual que `clima`: un `notificaciones: "si"` no enciende nada."""
        config.escribir(notificaciones=True)
        self.assertTrue(config.obtener("notificaciones"))
        config.escribir_todo(dict(config.DEFECTOS, notificaciones="si"))
        self.assertIs(config.obtener("notificaciones"), False)


class TestNotificacionesDeConfig(BaseConConfig):
    def test_apagado_devuelve_none_sin_instanciar(self):
        config.escribir(notificaciones=False)
        self.assertIsNone(widgets._notificaciones_de_config())

    def test_encendido_devuelve_un_lector(self):
        config.escribir(notificaciones=True)
        lector = widgets._notificaciones_de_config()
        self.assertIsInstance(lector, fuentes_ext.Notificaciones)

    def test_config_ilegible_no_lanza(self):
        self.assertIsInstance(widgets._notificaciones_de_config(), (type(None),
                                                                   fuentes_ext.Notificaciones))


class TestOptInDeLaCadena(BaseConConfig):
    def test_apagado_no_instancia_ni_un_subprocess(self):
        """Sin notificaciones la fuente combinada ni siquiera existe."""
        config.escribir(notificaciones=False, clima=False)
        fuentes = widgets.crear_fuentes()
        self.assertFalse(isinstance(fuentes, fuentes_ext.Combinada))

    def test_el_lector_real_no_se_llama_si_esta_apagado(self):
        """Apagado es apagado: no se ejecuta dunstctl ni se lee el bus."""
        llamadas = []
        original = fuentes_ext.Notificaciones._leer_dunst
        fuentes_ext.Notificaciones._leer_dunst = staticmethod(
            lambda *a, **k: llamadas.append(a) or {})
        try:
            config.escribir(notificaciones=False)
            widgets.crear_fuentes().muestra()
        finally:
            fuentes_ext.Notificaciones._leer_dunst = original
        self.assertEqual(llamadas, [])

    def test_encendido_las_claves_llegan_a_la_muestra(self):
        """Con el lector inyectado (sin dunst en el VPS) las claves llegan al panel."""
        config.escribir(notificaciones=True)
        lector = fuentes_ext.Notificaciones(lector=lambda: _historial(["hola"]))

        def falso():
            return lector

        original = widgets._notificaciones_de_config
        widgets._notificaciones_de_config = falso
        try:
            widgets._reset_fuentes()
            fuentes = widgets.crear_fuentes()
        finally:
            widgets._notificaciones_de_config = original
        self.assertIsInstance(fuentes, fuentes_ext.Combinada)
        muestra = fuentes.muestra()
        self.assertEqual(muestra["notif_cantidad"], 1)
        self.assertEqual(muestra["notif_ultima"], "hola (app)")

    def test_encendido_envuelve_aunque_no_haya_dunst(self):
        """Sin dunst la combinada existe y las del equipo siguen saliendo."""
        config.escribir(notificaciones=True)
        fuentes = widgets.crear_fuentes()
        self.assertIsInstance(fuentes, fuentes_ext.Combinada)
        self.assertIn("cpu_uso", fuentes.muestra())

    def test_lector_roto_deja_la_base_intacta(self):
        """Dunst caido no puede llevarse por delante los datos del equipo."""
        config.escribir(notificaciones=True)

        def roto():
            raise RuntimeError("dunst se cayo")

        original = fuentes_ext.Notificaciones
        fuentes_ext.Notificaciones = lambda *a, **k: original(lector=roto)
        try:
            widgets._reset_fuentes()
            fuentes = widgets.crear_fuentes()
        finally:
            fuentes_ext.Notificaciones = original
        muestra = fuentes.muestra()
        self.assertIn("cpu_uso", muestra)
        self.assertNotIn("notif_cantidad", muestra)

    def test_cambiarlo_rehace_la_fuente(self):
        config.escribir(notificaciones=False)
        primera = widgets.crear_fuentes()
        config.escribir(notificaciones=True)
        self.assertIsNot(widgets.crear_fuentes(), primera)


class TestCombinadaConNotificaciones(unittest.TestCase):
    def test_acepta_notificaciones_none_por_defecto(self):
        combinada = fuentes_ext.Combinada({"cpu_uso": 20})
        self.assertEqual(combinada.muestra(), {"cpu_uso": 20})

    def test_fusiona_las_claves_de_notificaciones(self):
        combinada = fuentes_ext.Combinada(
            {"cpu_uso": 20},
            notificaciones=fuentes_ext.Notificaciones(lector=lambda: _historial(["hola"])))
        muestra = combinada.muestra()
        self.assertEqual(muestra["cpu_uso"], 20)
        self.assertEqual(muestra["notif_cantidad"], 1)
        self.assertEqual(muestra["notif_ultima"], "hola (app)")

    def test_clima_y_notificaciones_a_la_vez(self):
        import json

        respuesta = {"current": {"temperature_2m": 14.3, "weather_code": 3}}
        combinada = fuentes_ext.Combinada(
            {"cpu_uso": 20},
            clima=fuentes_ext.Clima(descargador=lambda u, t: json.dumps(respuesta)),
            notificaciones=fuentes_ext.Notificaciones(lector=lambda: _historial(["ya"])))
        muestra = combinada.muestra()
        self.assertEqual(muestra["clima_temp"], 14.3)
        self.assertEqual(muestra["notif_ultima"], "ya (app)")
        self.assertEqual(muestra["cpu_uso"], 20)

    def test_lector_roto_no_tumba_la_base(self):
        def roto():
            raise RuntimeError("dunst se cayo")

        combinada = fuentes_ext.Combinada(
            {"cpu_uso": 20}, notificaciones=fuentes_ext.Notificaciones(lector=roto))
        self.assertEqual(combinada.muestra(), {"cpu_uso": 20})

    def test_una_fuente_caida_no_tumba_a_la_otra(self):
        """Notificaciones caidas no pueden llevarse por delante al clima."""
        import json

        respuesta = {"current": {"temperature_2m": 14.3, "weather_code": 3}}
        combinada = fuentes_ext.Combinada(
            {"cpu_uso": 20},
            clima=fuentes_ext.Clima(descargador=lambda u, t: json.dumps(respuesta)),
            notificaciones=fuentes_ext.Notificaciones(
                lector=lambda: (_ for _ in ()).throw(RuntimeError("sin dunst"))))
        self.assertEqual(combinada.muestra()["clima_temp"], 14.3)

    def test_el_clima_caido_no_tumba_a_las_notificaciones(self):
        def roto(url, timeout):
            raise OSError("sin red")

        combinada = fuentes_ext.Combinada(
            {"cpu_uso": 20},
            clima=fuentes_ext.Clima(descargador=roto),
            notificaciones=fuentes_ext.Notificaciones(lector=lambda: _historial(["ya"])))
        self.assertEqual(combinada.muestra()["notif_ultima"], "ya (app)")

    def test_notificaciones_vacias_no_pisan_la_base(self):
        combinada = fuentes_ext.Combinada(
            {"cpu_uso": 20}, notificaciones=fuentes_ext.Notificaciones())
        self.assertEqual(combinada.muestra(), {"cpu_uso": 20})

    def test_resumen_incluye_notificaciones(self):
        class Lector:
            def muestra(self):
                return {"notif_cantidad": 1, "notif_ultima": "hola"}

            def resumen(self):
                return [{"clave": "notif_cantidad", "valor": 1},
                        {"clave": "notif_ultima", "valor": "hola"}]

        combinada = fuentes_ext.Combinada({"cpu_uso": 20}, notificaciones=Lector())
        claves = {fila["clave"] for fila in combinada.resumen()}
        self.assertIn("notif_cantidad", claves)

    def test_resumen_con_notificaciones_rotas_no_lanza(self):
        class Rota:
            def muestra(self):
                raise RuntimeError("sin dunst")

            def resumen(self):
                raise RuntimeError("sin dunst")

        combinada = fuentes_ext.Combinada({"cpu_uso": 20}, notificaciones=Rota())
        self.assertIsInstance(combinada.resumen(), list)


class TestMarcadoresDelMotor(unittest.TestCase):
    def test_el_motor_pinta_las_claves_de_notificaciones(self):
        lector = widgets._Lector({"notif_cantidad": 3, "notif_ultima": "hola (app)"})
        texto = widgets.texto_con_marcadores("{notif_cantidad} avisos: {notif_ultima}",
                                             lector)
        self.assertIn("3 avisos", texto)
        self.assertIn("hola (app)", texto)


if __name__ == "__main__":
    unittest.main(verbosity=2)
