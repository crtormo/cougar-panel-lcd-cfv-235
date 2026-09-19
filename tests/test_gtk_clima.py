"""Pruebas del interruptor del clima y sus coordenadas en la pagina Estado (GTK).

    python3 -B tests/test_gtk_clima.py

Vive en su propio archivo a proposito: importar `cfv235_gtk.ventana` carga GTK/Pango en el
proceso, y el motor de dibujo (`Pillow` con su layout por HarfBuzz) calcula mal los anchos de
texto cuando Pango ya esta cargado — se midio: el patron "texto" revienta con
`DecompressionBombError` (un ancho de 32 millones de px) por la convivencia de las dos
libereas. Como `ejecutar.sh` corre cada archivo en su propio proceso, aqui el efecto se queda
encerrado y no ensucia a `test_regresiones.py`.
"""

import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

class TestInterruptorDelClima(unittest.TestCase):
    """El interruptor del clima y sus coordenadas se guardan en las preferencias.

    No hace falta pantalla: las tres cosas que pueden romperse son la lectura del valor
    guardado (`_numero_guardado`/`_texto_guardado`), el texto con el que se rellena la fila
    (`_texto_de_coordenada`) y lo que pasa al aplicar un valor mal escrito
    (`_aplicar_coordenada`, que debe DEJAR el valor anterior y avisar). Se llaman los tres
    como funciones sueltas, con un doble de la ventana que apunta lo que se guarda.

    Lo que NO cubre esta clase es el dialogo real de la pagina Estado: eso necesita GTK y
    un display (ver `TestDialogoDeFicheros` para el patron con `subprocess`).
    """

    @staticmethod
    def _clase():
        """`VentanaPrincipal` importada sola, sin construir la ventana.

        `ventana.py` importa `gi`/`Gtk`/`Adw` arriba del todo, asi que si el equipo no trae
        los typelibs el import falla y la prueba se SALTA en vez de romper.
        """
        try:
            from cfv235_gtk import ventana as modulo
        except Exception as exc:                  # noqa: BLE001  (sin gi: no hay prueba)
            raise unittest.SkipTest("hace falta GTK4/Adw para probar ventana.py (%s)" % exc)
        return modulo.VentanaPrincipal

    @staticmethod
    def _config_con(clase, datos):
        """Doble de ventana: `_config`, `_guardar`, `avisar_error` y los helpers reales."""
        class Doble:
            pass

        falso = Doble()
        falso._config = dict(datos)
        falso.guardado = []
        falso.avisos = []

        def guardar(**cambios):
            falso._config.update(cambios)
            falso.guardado.append(cambios)

        falso._guardar = guardar
        falso.avisar_error = falso.avisos.append
        falso.avisar = falso.avisos.append
        falso._refrescar_clima = lambda: None
        for nombre in ("_numero_guardado", "_texto_guardado", "_texto_de_coordenada",
                       "_aplicar_coordenada"):
            original = getattr(clase, nombre)
            setattr(falso, nombre, original.__get__(falso, type(falso)))
        return falso

    @staticmethod
    def _fila(texto, valor):
        """Doble de `Adw.EntryRow`: solo lo que usa `_aplicar_coordenada`."""
        class Fila:
            def __init__(self):
                self._texto = texto

            def get_text(self):
                return self._texto

            def set_text(self, nuevo):
                self._texto = nuevo

        return Fila()

    def test_los_defectos_estan_en_la_config(self):
        from cfv235 import config
        for clave, tipo in (("clima", bool), ("clima_lat", float), ("clima_lon", float),
                            ("clima_tz", str)):
            self.assertIn(clave, config.DEFECTOS)
            self.assertIsInstance(config.DEFECTOS[clave], tipo)
        self.assertFalse(config.DEFECTOS["clima"], "el clima es opt-in: por defecto apagado")

    def test_una_coordenada_ilegible_no_se_guarda(self):
        """Lo que no parsea deja el valor anterior y avisa: no puede quedar en el config."""
        clase = self._clase()
        falso = self._config_con(clase, {})
        fila = self._fila("Santiago", -33.45)
        falso._aplicar_coordenada(fila, {"clave": "clima_lat", "valor": -33.45})
        self.assertEqual(falso.guardado, [])
        self.assertEqual(falso._config, {})
        self.assertEqual(fila.get_text(), "-33.45", "el campo vuelve al valor bueno")
        self.assertEqual(len(falso.avisos), 1)
        self.assertIn("clima_lat", falso.avisos[0])

    def test_una_latitud_fuera_de_rango_no_se_guarda(self):
        """200 grados de latitud no existen: Open-Meteo responderia con un error."""
        clase = self._clase()
        falso = self._config_con(clase, {})
        fila = self._fila("200", -33.45)
        falso._aplicar_coordenada(fila, {"clave": "clima_lat", "valor": -33.45})
        self.assertEqual(falso.guardado, [])
        self.assertEqual(fila.get_text(), "-33.45", "el campo vuelve al valor bueno")
        self.assertEqual(len(falso.avisos), 1)
        self.assertIn("clima_lat", falso.avisos[0])
        self.assertIn("-33.45", falso.avisos[0])

    def test_una_coordenada_buena_se_guarda_y_reformatea(self):
        clase = self._clase()
        falso = self._config_con(clase, {})
        fila = self._fila("-33,45", -33.45)       # coma: teclado en espanol
        falso._aplicar_coordenada(fila, {"clave": "clima_lat", "valor": -33.45})
        # -33,45 es el mismo valor que -33.45: no se reescribe nada.
        self.assertEqual(falso.guardado, [])
        self.assertEqual(fila.get_text(), "-33.45")

    def test_el_valor_nuevo_se_guarda_como_float(self):
        clase = self._clase()
        falso = self._config_con(clase, {})
        fila = self._fila("-33.6", -33.45)
        estado = {"clave": "clima_lat", "valor": -33.45}
        falso._aplicar_coordenada(fila, estado)
        self.assertEqual(falso.guardado, [{"clima_lat": -33.6}])
        self.assertIsInstance(falso.guardado[0]["clima_lat"], float)
        self.assertEqual(estado["valor"], -33.6)
        self.assertEqual(falso.avisos, [])

    def test_la_zona_horaria_se_guarda_como_texto(self):
        """`clima_tz` no se parsea: es texto, vaya lo que vaya escrito."""
        clase = self._clase()
        falso = self._config_con(clase, {})
        fila = self._fila("Europe/Madrid", "America/Santiago")
        falso._aplicar_coordenada(fila, {"clave": "clima_tz", "valor": "Europe/Berlin"})
        self.assertEqual(falso.guardado, [{"clima_tz": "Europe/Madrid"}])

    def test_el_texto_de_una_coordenada_no_lleva_ceros_de_relleno(self):
        falso = self._config_con(self._clase(), {})
        self.assertEqual(falso._texto_de_coordenada(-33.45), "-33.45")
        self.assertEqual(falso._texto_de_coordenada(-33.0), "-33")
        self.assertEqual(falso._texto_de_coordenada(0.0), "0")

    def test_lo_guardado_manda_sobre_los_defectos(self):
        clase = self._clase()
        falso = self._config_con(clase, {"clima_lat": -41.47, "clima_tz": "America/Punta_Arenas",
                                        "clima_lon": "no soy un numero", "clima": True})
        self.assertEqual(falso._numero_guardado("clima_lat", -33.45), -41.47)
        # Un texto donde se espera un numero se cae al defecto, no a None/0.
        self.assertEqual(falso._numero_guardado("clima_lon", -70.67), -70.67)
        self.assertEqual(falso._texto_guardado("clima_tz", "America/Santiago"),
                         "America/Punta_Arenas")
        self.assertTrue(falso._config["clima"])

    def test_el_interruptor_guarda_el_clima_como_bool(self):
        """`_cambiar_clima` escribe la clave `clima` con un bool, igual que el keepalive."""
        clase = self._clase()
        import ast
        fuente = open(os.path.join(RAIZ, "cfv235_gtk", "ventana.py"), encoding="utf-8").read()
        arbol = ast.parse(fuente)
        ventana = next(n for n in ast.walk(arbol)
                       if isinstance(n, ast.ClassDef) and n.name == "VentanaPrincipal")
        metodos = {n.name: n for n in ventana.body if isinstance(n, ast.FunctionDef)}
        cuerpo = ast.unparse(metodos["_cambiar_clima"])
        # Espejo del keepalive: `_guardar(clima=...)` y nada de escribir la config a mano.
        self.assertIn("clima=activo", cuerpo)
        self.assertIn("bool(fila.get_active())", cuerpo)
        self.assertNotIn("config.escribir", cuerpo)
        # El keepalive sigue existiendo: el clima se copio de el, no lo sustituye.
        cuerpo_keepalive = ast.unparse(metodos["_cambiar_keepalive"])
        self.assertIn("keepalive=bool(activo)", cuerpo_keepalive)


if __name__ == "__main__":
    unittest.main(verbosity=2)
