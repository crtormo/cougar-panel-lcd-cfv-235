"""Pruebas del motor de temas: validador, dibujo y seguridad al dibujar.

Estas pruebas cubren los defectos que tenía el motor del kit: un color de fondo inválido
abortaba el render, el validador aprobaba cosas que luego fallaban y los fallos de cada
widget se silenciaban (el PNG salía incompleto y las pruebas seguían en verde).
"""

import glob
import os
import sys
import threading
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import temas, widgets  # noqa: E402

KIT = "/home/maximo/cfv235/cfv-235-linux"
SALIDA = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-pruebas-temas")


class TestValidador(unittest.TestCase):

    def test_un_tema_correcto_no_da_problemas(self):
        self.assertEqual(temas.validar(temas.tema_por_defecto()), [])

    def test_detecta_color_invalido(self):
        problemas = temas.validar({"fondo": "#GGGGGG", "widgets": []})
        self.assertTrue(any("color" in x for x in problemas), problemas)

    def test_detecta_tamano_invalido(self):
        for valor in (-1, 0, "grande"):
            tema = {"widgets": [{"tipo": "texto", "texto": "x", "x": 10, "y": 10,
                                 "tamano": valor}]}
            problemas = temas.validar(tema)
            self.assertTrue(problemas, f"tamano={valor!r} deberia dar problemas")

    def test_detecta_widget_fuera_del_panel(self):
        tema = {"widgets": [{"tipo": "texto", "texto": "x", "x": 1900, "y": 10,
                             "tamano": 30, "ancho": 340}]}
        self.assertTrue(temas.validar(tema))

    def test_detecta_tipo_desconocido(self):
        tema = {"widgets": [{"tipo": "inventado", "x": 0, "y": 0}]}
        self.assertTrue(temas.validar(tema))

    def test_min_y_puntos_invalidos(self):
        tema = {"widgets": [{"tipo": "grafica", "fuente": "cpu_uso", "x": 0, "y": 0,
                             "ancho": 400, "alto": 200, "puntos": 0, "min": "bajo"}]}
        self.assertTrue(temas.validar(tema))


class TestDibujo(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.makedirs(SALIDA, exist_ok=True)

    def test_dibuja_el_tema_por_defecto(self):
        ruta = os.path.join(SALIDA, "defecto.png")
        temas.renderizar(temas.tema_por_defecto(), ruta)
        from PIL import Image
        with Image.open(ruta) as img:
            self.assertEqual(img.size, (temas.ANCHO, temas.ALTO))
        self.assertEqual(widgets.ultimos_fallos(), [])

    def test_fondo_invalido_no_aborta(self):
        """El kit abortaba el render entero con un color de fondo mal escrito."""
        ruta = os.path.join(SALIDA, "fondo_malo.png")
        tema = {"fondo": "#GGGGGG",
                "widgets": [{"tipo": "texto", "texto": "sigo aqui", "x": 20, "y": 20,
                             "tamano": 40}]}
        self.assertTrue(temas.validar(tema))
        temas.renderizar(tema, ruta)                 # no debe lanzar
        self.assertTrue(os.path.getsize(ruta) > 0)

    def test_los_fallos_de_widget_quedan_registrados(self):
        """Con `tamano: -1` el widget no se dibuja, pero el fallo NO se silencia."""
        ruta = os.path.join(SALIDA, "fallo.png")
        tema = {"widgets": [{"tipo": "texto", "texto": "malo", "x": 20, "y": 20,
                             "tamano": -1},
                            {"tipo": "texto", "texto": "bueno", "x": 20, "y": 200,
                             "tamano": 40}]}
        self.assertTrue(temas.validar(tema))
        temas.renderizar(tema, ruta)
        self.assertTrue(widgets.ultimos_fallos(), "el fallo del widget deberia registrarse")

    def test_renderizar_datos_no_deja_temporales(self):
        antes = set(glob.glob("/tmp/cfv235-render*"))
        datos = temas.renderizar_datos(temas.tema_por_defecto())
        self.assertTrue(datos.startswith(b"\x89PNG"))
        with self.assertRaises(ValueError):
            temas.renderizar_datos(temas.tema_por_defecto(), formato="NOEXISTE")
        despues = set(glob.glob("/tmp/cfv235-render*"))
        self.assertEqual(despues - antes, set(), "no deberia quedar ningun temporal")

    def test_renderizar_datos_desde_varios_hilos(self):
        errores = []

        def trabajar():
            try:
                for _ in range(5):
                    datos = temas.renderizar_datos(temas.tema_por_defecto())
                    if not datos.startswith(b"\x89PNG"):
                        errores.append("PNG invalido")
            except Exception as exc:                  # noqa: BLE001
                errores.append(repr(exc))

        hilos = [threading.Thread(target=trabajar) for _ in range(6)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(errores, [])


class TestNormalizar(unittest.TestCase):

    def test_basura_no_lanza(self):
        for valor in (None, "hola", 42, ["a"], {"widgets": "no-es-lista"}):
            tema = temas.normalizar(valor)
            self.assertIsInstance(tema, dict)
            self.assertIn("widgets", tema)
            self.assertIsInstance(tema["widgets"], list)


@unittest.skipUnless(os.path.isdir(KIT), "no esta el kit cfv-235")
class TestTemasDelKit(unittest.TestCase):
    """Los temas de ejemplo del kit deben seguir dibujandose (compatibilidad)."""

    def test_ejemplos(self):
        from PIL import Image
        rutas = sorted(glob.glob(os.path.join(KIT, "ejemplos", "*.json")))
        if not rutas:
            self.skipTest("no hay ejemplos en el kit")
        for ruta in rutas:
            with self.subTest(tema=os.path.basename(ruta)):
                tema = temas.cargar(ruta)
                self.assertEqual(temas.validar(tema), [])
                salida = os.path.join(SALIDA, os.path.basename(ruta) + ".png")
                temas.renderizar(tema, salida)
                with Image.open(salida) as img:
                    self.assertEqual(img.size, (temas.ANCHO, temas.ALTO))


if __name__ == "__main__":
    unittest.main(verbosity=2)
