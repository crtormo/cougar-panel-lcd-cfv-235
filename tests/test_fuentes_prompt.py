"""Catalogo de fuentes externas y su reflejo en `docs/PROMPT_TEMAS.md`.

`herramientas/generar_prompt_temas.py` armaba su tabla de datos solo con
`Sensores().resumen()`, asi que las claves `clima_*` (y las `notif_*`) no llegaban nunca
al prompt: la IA que genera temas no sabia que existen. Aqui se prueba el catalogo nuevo
de `fuentes_ext` y que el generador lo fusiona.

    python3 -B tests/test_fuentes_prompt.py
"""
import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)          # ejecutable tambien a mano: python3 -B tests/...

from cfv235 import fuentes_ext, sensores  # noqa: E402

sys.path.insert(0, os.path.join(RAIZ, "herramientas"))
import generar_prompt_temas as generador  # noqa: E402


class TestCatalogoDeFuentesExternas(unittest.TestCase):
    """`fuentes_ext.CATALOGO` con el MISMO formato que `sensores.CATALOGO`."""

    def test_tiene_el_mismo_formato_que_el_de_sensores(self):
        for fila in fuentes_ext.CATALOGO:
            with self.subTest(fila=fila):
                self.assertIsInstance(fila, tuple)
                self.assertEqual(len(fila), 3, "cada fila es (clave, etiqueta, unidad)")
                for campo in fila:
                    self.assertIsInstance(campo, str)

    def test_es_una_tupla_de_tuplas(self):
        """Inmutable como el de `sensores`: no es un diccionario de modulo que se muta."""
        self.assertIsInstance(fuentes_ext.CATALOGO, tuple)
        self.assertEqual(
            type(fuentes_ext.CATALOGO), type(sensores.CATALOGO),
            "mismo tipo que el catalogo del equipo")

    def test_las_claves_son_unicas(self):
        claves = [fila[0] for fila in fuentes_ext.CATALOGO]
        self.assertEqual(len(claves), len(set(claves)), "clave repetida en CATALOGO")

    def test_coincide_con_las_claves_de_clima_y_notificaciones(self):
        """El catalogo documenta EXACTAMENTE lo que las clases devuelven."""
        self.assertEqual(set(fila[0] for fila in fuentes_ext.CATALOGO),
                         set(fuentes_ext.Clima.CLAVES) | set(fuentes_ext.Notificaciones.CLAVES))

    def test_conserva_las_siete_claves_del_clima(self):
        claves = set(fila[0] for fila in fuentes_ext.CATALOGO)
        for clave in fuentes_ext.Clima.CLAVES:
            self.assertIn(clave, claves)

    def test_conserva_las_dos_claves_de_notificaciones(self):
        claves = set(fila[0] for fila in fuentes_ext.CATALOGO)
        for clave in fuentes_ext.Notificaciones.CLAVES:
            self.assertIn(clave, claves)

    def test_no_pide_datos_a_la_red_ni_lanza_procesos(self):
        """El catalogo es una constante: importarlo no descarga clima ni llama a dunst."""
        self.assertEqual(fuentes_ext.Notificaciones._leer_dunst(), {})

    def test_las_etiquetas_son_legibles(self):
        for clave, etiqueta, _unidad in fuentes_ext.CATALOGO:
            with self.subTest(clave=clave):
                self.assertTrue(etiqueta.strip(), "sin etiqueta no se puede documentar")
                self.assertNotEqual(etiqueta, clave, "la etiqueta no es la clave repetida")


class TestGeneradorDePrompts(unittest.TestCase):
    """El generador fusiona el catalogo externo en su tabla de datos."""

    def setUp(self):
        self.filas = generador.filas_datos()

    def test_incluye_las_claves_del_equipo(self):
        claves = [fila[0] for fila in self.filas]
        self.assertIn("cpu_temp", claves)
        self.assertIn("ram_uso", claves)

    def test_incluye_las_claves_del_clima(self):
        claves = [fila[0] for fila in self.filas]
        self.assertIn("clima_temp", claves)
        self.assertIn("clima_descripcion", claves)
        self.assertIn("clima_icono", claves)

    def test_incluye_las_claves_de_notificaciones(self):
        claves = [fila[0] for fila in self.filas]
        self.assertIn("notif_cantidad", claves)
        self.assertIn("notif_ultima", claves)

    def test_no_duplica_ninguna_clave(self):
        claves = [fila[0] for fila in self.filas]
        self.assertEqual(len(claves), len(set(claves)))

    def test_las_filas_van_primero_las_del_equipo(self):
        """El orden del panel manda: lo del equipo delante, lo externo detras."""
        claves = [fila[0] for fila in self.filas]
        self.assertLess(claves.index("cpu_temp"), claves.index("clima_temp"))

    def test_las_filas_traen_unidad_o_un_guion(self):
        for clave, etiqueta, unidad in self.filas:
            with self.subTest(clave=clave):
                self.assertTrue(etiqueta)
                self.assertTrue(unidad, "la columna unidad nunca queda vacia")

    def test_la_tabla_markdown_lleva_las_claves_del_clima(self):
        tabla = generador.tabla_datos()
        self.assertIn("| `clima_temp` |", tabla)
        self.assertIn("| `clima_descripcion` |", tabla)
        self.assertIn("`notif_cantidad`", tabla)
        self.assertEqual(tabla.splitlines()[0].count("|"), 4, "3 columnas")

    def test_sin_fuentes_externas_sigue_funcionando(self):
        """Import perezoso y tolerante: sin `fuentes_ext` el generador no se cae."""
        original = generador._filas_externas
        generador._filas_externas = lambda: []
        try:
            filas = generador.filas_datos()
        finally:
            generador._filas_externas = original
        claves = [fila[0] for fila in filas]
        self.assertIn("cpu_temp", claves)
        self.assertNotIn("clima_temp", claves)

    def test_el_fichero_generado_lleva_las_claves_del_clima(self):
        """El documento real (no una funcion suelta) tiene que traerlas."""
        ruta = os.path.join(RAIZ, "docs", "PROMPT_TEMAS.md")
        with open(ruta, encoding="utf-8") as fh:
            documento = fh.read()
        self.assertIn("`clima_temp`", documento)
        self.assertIn("`clima_descripcion`", documento)
        self.assertIn("`notif_cantidad`", documento)


class TestTablaDatosEnElDocumento(unittest.TestCase):
    def test_el_documento_esta_al_dia(self):
        """`docs/PROMPT_TEMAS.md` se regenera desde el codigo: si cambio, hay que correrlo."""
        ruta = os.path.join(RAIZ, "docs", "PROMPT_TEMAS.md")
        with open(ruta, encoding="utf-8") as fh:
            documento = fh.read()
        self.assertIn(generador.tabla_datos(), documento,
                      "docs/PROMPT_TEMAS.md esta desfasado: correr "
                      "python3 herramientas/generar_prompt_temas.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
