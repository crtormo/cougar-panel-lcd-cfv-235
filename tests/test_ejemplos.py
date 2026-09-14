"""Valida los temas JSON de `ejemplos/`.

Son 40 temas (los del banco de pruebas de Windows) y conviene que sigan siendo validos y
dibujables: si un cambio en el motor de temas los rompe, se entera aqui y no al subirlos al
panel.

    python3 -B tests/test_ejemplos.py
"""

import glob
import json
import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import temas  # noqa: E402

CARPETA = os.path.join(RAIZ, "ejemplos")


class TestEjemplos(unittest.TestCase):

    def setUp(self):
        self.rutas = sorted(glob.glob(os.path.join(CARPETA, "*.json")))

    def test_hay_ejemplos(self):
        self.assertGreaterEqual(len(self.rutas), 40,
                                f"solo encuentro {len(self.rutas)} ejemplos en ejemplos/")

    def test_todos_son_json_valido(self):
        for ruta in self.rutas:
            with self.subTest(ejemplo=os.path.basename(ruta)):
                with open(ruta, encoding="utf-8") as fh:
                    tema = json.load(fh)
                self.assertIsInstance(tema, dict, "un tema es un objeto JSON")
                self.assertIn("widgets", tema, "un tema lleva la lista 'widgets'")
                self.assertIsInstance(tema["widgets"], list)

    def test_todos_pasan_el_validador(self):
        for ruta in self.rutas:
            with self.subTest(ejemplo=os.path.basename(ruta)):
                with open(ruta, encoding="utf-8") as fh:
                    tema = json.load(fh)
                problemas = temas.validar(tema)
                self.assertEqual(problemas, [],
                                 f"{os.path.basename(ruta)}: {problemas}")

    def test_todos_se_dibujan(self):
        """Dibujar es lo que de verdad importa: el validador no ve todo."""
        for ruta in self.rutas:
            with self.subTest(ejemplo=os.path.basename(ruta)):
                with open(ruta, encoding="utf-8") as fh:
                    tema = json.load(fh)
                try:
                    datos = temas.renderizar_datos(tema)
                except Exception as exc:              # noqa: BLE001
                    self.fail(f"{os.path.basename(ruta)} no se dibuja: "
                              f"{type(exc).__name__}: {exc}")
                self.assertTrue(datos.startswith(b"\x89PNG"),
                                "el resultado tiene que ser un PNG")
                self.assertGreater(len(datos), 1000, "un PNG vacio no sirve")


if __name__ == "__main__":
    unittest.main(verbosity=2)
