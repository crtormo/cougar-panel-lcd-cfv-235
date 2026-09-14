"""Pruebas de regresion de los fallos que encontro la auditoria del nucleo.

Cada prueba corresponde a un bug concreto que se reprodujo y se arreglo; sirven para que no
vuelva. No necesitan panel: usan el simulador en un PTY y objetos sueltos.

    python3 -B tests/test_regresiones.py
"""

import contextlib
import os
import subprocess
import sys
import threading
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import canal as modulo_canal  # noqa: E402
from cfv235 import panel as modulo_panel  # noqa: E402
from cfv235 import patrones, protocolo as p, simulador  # noqa: E402

PRUEBAS = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-regresiones")


class SimuladorEnPTY:
    def __init__(self, **opciones):
        self.maestro, self.esclavo, self.nombre = simulador.abrir_pty()
        self.panel = simulador.PanelFalso(**opciones)
        self.parar = threading.Event()
        self.hilo = threading.Thread(target=simulador.servir,
                                     args=(self.maestro, self.panel, self.parar), daemon=True)
        self.hilo.start()

    def cerrar(self):
        self.parar.set()
        self.hilo.join(timeout=2.0)
        for fd in (self.maestro, self.esclavo):
            try:
                os.close(fd)
            except OSError:
                pass


class BaseSimulador(unittest.TestCase):
    def setUp(self):
        os.makedirs(PRUEBAS, exist_ok=True)
        self.sim = None

    def tearDown(self):
        if self.sim:
            self.sim.cerrar()

    def levantar(self, **opciones):
        self.sim = SimuladorEnPTY(**opciones)
        return self.sim


# ---------------------------------------------------------------- canal
class TestLecturaDelBuffer(unittest.TestCase):
    """Bug: una trama completa que ya estaba en el buffer no se devolvia nunca."""

    def test_extrae_tramas_ya_en_el_buffer(self):
        c = modulo_canal.Canal("/dev/null", timeout=0.2)
        c.fd = 1                                    # no leemos del descriptor
        trama_a, _ = p.build_request("conn", None, seq=1)
        trama_b, _ = p.build_request("power", {"event": "resume"}, seq=2)
        c._buffer += trama_a + trama_b
        primera = c.extraer_trama()
        segunda = c.extraer_trama()
        self.assertIsNotNone(primera, "la primera trama del buffer deberia salir")
        self.assertEqual(primera.payload, p.decode_frame(trama_a).payload)
        self.assertIsNotNone(segunda, "la segunda trama del buffer deberia salir")
        self.assertEqual(segunda.payload, p.decode_frame(trama_b).payload)
        self.assertIsNone(c.extraer_trama(), "ya no queda nada")

    def test_resincroniza_tras_basura(self):
        """Un escape roto no puede dejar el parser atascado para siempre."""
        c = modulo_canal.Canal("/dev/null", timeout=0.2)
        c.fd = 1
        trama, _ = p.build_request("conn", None, seq=3)
        c._buffer += bytes([p.START, 0x10, p.ESC, 0x00]) + trama   # basura delante
        info = c.extraer_trama()
        self.assertIsNotNone(info, "deberia resincronizar y encontrar la trama buena")
        self.assertEqual(info.payload, p.decode_frame(trama).payload)


class TestCanalCerrado(unittest.TestCase):
    """Bug: un Panel cerrado volvia a abrir el hidraw por detras de su dueño."""

    def test_no_resucita_un_canal_cerrado(self):
        c = modulo_canal.Canal("/dev/null", timeout=0.2)
        c.fd = os.open("/dev/null", os.O_RDWR)      # un descriptor de verdad
        c.cerrar()
        self.assertTrue(c._cerrado)
        with self.assertRaises(modulo_canal.ErrorCanal):
            c.asegurar_abierto()
        self.assertIsNone(c.fd, "no debe haber abierto nada")


class TestRutaAlReconectar(unittest.TestCase):
    """Bug: `reabrir()` olvidaba la ruta pedida y saltaba a otro dispositivo."""

    def test_conserva_la_ruta_pedida(self):
        panel = modulo_panel.Panel(dispositivo="/dev/pts/99", timeout=0.5)
        self.assertFalse(panel.reabrir())
        self.assertEqual(panel.canal.ruta, "/dev/pts/99")


# ---------------------------------------------------------------- panel
class TestSubidaRobusta(BaseSimulador):

    def test_no_lanza_si_el_panel_no_esta(self):
        """El docstring promete no lanzar: un panel muerto debe dar motivo, no traza."""
        panel = modulo_panel.Panel(dispositivo="/dev/pts/99", timeout=0.4)
        resultado = panel.subir_datos(b"\x89PNG\r\n\x1a\n" + b"x" * 3000, "x.png",
                                      capa="osd")
        self.assertFalse(resultado.ok)
        self.assertTrue(resultado.motivo, "deberia explicar por que fallo")

    def test_rechaza_fichero_enorme_sin_leerlo(self):
        ruta = os.path.join(PRUEBAS, "enorme.png")
        with open(ruta, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
            fh.truncate(80 * 1024 * 1024)            # 80 MB > maximo del protocolo
        panel = modulo_panel.Panel(dispositivo="/dev/pts/99", timeout=0.4)
        resultado = panel.subir_archivo(ruta)
        self.assertFalse(resultado.ok)
        self.assertIn("MB", resultado.motivo)

    def test_espacio_desconocido_avisa(self):
        """El control de espacio no puede fallar en abierto y callarse."""
        sim = self.levantar()
        panel = modulo_panel.Panel(dispositivo=sim.nombre, timeout=2.0)
        panel.abrir()
        panel.canal.autonegociar(timeout=2.0)
        # conn sin JSON: el panel "no acepta", asi que no se sabe el espacio
        original = panel.propiedades_seguras

        def sin_espacio():
            return {}

        panel.propiedades_seguras = sin_espacio
        datos = b"\x89PNG\r\n\x1a\n" + os.urandom(5 * 1024 * 1024)   # 5 MB, "grande"
        resultado = panel.subir_datos(datos, "x.png", capa="fondo")
        panel.propiedades_seguras = original
        self.assertFalse(resultado.ok)
        self.assertIn("espacio", resultado.motivo.lower())
        panel.cerrar()

    def test_subida_con_panel_que_retransmite(self):
        """Bug: con un panel que repite respuestas, TODA subida fallaba."""
        sim = self.levantar(retransmitir=True)
        panel = modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0)
        panel.abrir()
        panel.canal.autonegociar(timeout=2.0)
        datos = b"\x89PNG\r\n\x1a\n" + os.urandom(5000)
        resultado = panel.subir_datos(datos, "repetido.png", capa="osd")
        self.assertTrue(resultado.ok, resultado.motivo)
        self.assertEqual(sim.panel.subidas_ok, 1)
        panel.cerrar()

    def test_dos_hilos_no_se_cruzan(self):
        """Sin candado, dos hilos se adjudicaban respuestas cruzadas."""
        sim = self.levantar()
        panel = modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0)
        panel.abrir()
        panel.canal.autonegociar(timeout=2.0)
        fallos = []

        def trabajar(brillo):
            try:
                for _ in range(8):
                    r = panel.brillo(brillo)
                    if not r.ok:
                        fallos.append(f"brillo {brillo}: code={r.code}")
            except Exception as exc:                  # noqa: BLE001
                fallos.append(repr(exc))

        hilos = [threading.Thread(target=trabajar, args=(v,)) for v in (10, 90)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(fallos, [])
        panel.cerrar()


# ---------------------------------------------------------------- telemetria y patrones
class TestTelemetria(unittest.TestCase):

    def test_no_inventa_ventiladores(self):
        """Bug: sin sensores de ventilador se mandaban los de ejemplo (2566 rpm...)."""
        datos = modulo_panel.telemetria_desde_sensores({"cpu_temp": 50.0, "gpu_temp": 40.0})
        self.assertEqual(datos["fans"], [],
                         "no debe inventar ventiladores que el equipo no tiene")

    def test_con_ventiladores_los_manda(self):
        datos = modulo_panel.telemetria_desde_sensores({"cpu_temp": 50.0, "cpu_vent": 1200.0,
                                                        "bomba_vent": 2100.0})
        nombres = [f["name"] for f in datos["fans"]]
        self.assertIn("cpu_vent", nombres)
        self.assertIn("bomba_vent", nombres)


class TestPatrones(unittest.TestCase):
    """Bug: `cfv235 patron` estaba roto (ImportError del modulo)."""

    def test_genera_todos(self):
        from PIL import Image
        carpeta = os.path.join(PRUEBAS, "patrones")
        rutas = patrones.generar_todos(carpeta)
        self.assertEqual(len(rutas), len(patrones.CATALOGO))
        for ruta in rutas:
            with Image.open(ruta) as img:
                self.assertEqual(img.size, (patrones.ANCHO, patrones.ALTO))

    def test_nombre_desconocido(self):
        with self.assertRaises(ValueError):
            patrones.generar("inventado", os.path.join(PRUEBAS, "x.png"))

    def test_lado_invalido(self):
        with self.assertRaises(ValueError):
            patrones.generar("cuadros", os.path.join(PRUEBAS, "x.png"), lado=0)



class TestDialogoDeFicheros(unittest.TestCase):
    """El dialogo de ficheros tiene que VERSE.

    Ni `Gtk.FileDialog` ni `Gtk.FileChooserNative` valen en GTK 4.22: los dos acaban en el
    portal del escritorio, que necesita el token de activacion de la app (solo existe si la
    lanzo el escritorio). Sin el, el portal falla en silencio —

        xdg-desktop-portal-gnome: Failed to associate portal window with parent window ''

    — y el boton "Elegir imagen..." parece no hacer nada. `GTK_USE_PORTAL=0` **ya no lo
    evita**. La app usa `Gtk.FileChooserDialog`, que GTK dibuja en el propio proceso.
    """

    def test_el_dialogo_se_abre_visible(self):
        """Abre el dialogo con una ventana de prueba y mira que exista y se vea."""
        codigo = f"""
import os, sys
sys.path.insert(0, {RAIZ!r})
os.environ["CFV235_PANEL"] = "/dev/pts/99"
os.environ["CFV235_ID_APLICACION"] = "t.dialogo.prueba"
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
from cfv235_gtk import ventana as mv

app = Adw.Application(application_id="t.dialogo.prueba")

def activar(a):
    v = Adw.ApplicationWindow(application=a)
    v.present()
    mv.abrir_dialogo_fichero(v, "Elegir imagen", lambda r: None)
    d = v._dialogo_abierto
    print("RESULTADO", type(d).__name__ if d is not None else "None",
          bool(d is not None and d.get_visible()),
          bool(d is not None and d.get_modal()))
    a.quit()

app.connect("activate", activar)
import threading
threading.Timer(20, lambda: GLib.idle_add(app.quit)).start()
app.run([])
"""
        salida = subprocess.run([sys.executable, "-B", "-c", codigo], capture_output=True,
                                text=True, timeout=120)
        self.assertIn("RESULTADO", salida.stdout,
                      f"no se construyo el dialogo: {salida.stderr[-400:]}")
        linea = [l for l in salida.stdout.splitlines() if l.startswith("RESULTADO")][0]
        self.assertIn("FileChooserDialog", linea,
                      "debe ser Gtk.FileChooserDialog (el unico que no pasa por el portal)")
        self.assertIn("True True", linea,
                      "el dialogo se creo pero no se ve: es el fallo que reporto el usuario")

    def test_el_ayudante_no_usa_la_api_del_portal(self):
        """Guardia contra volver a `Gtk.FileDialog`/`FileChooserNative` sin querer.

        Se miran las LLAMADAS del codigo (con `ast`), no el texto: el docstring los menciona a
        proposito, para explicar por que no se usan.
        """
        import ast
        fuente = open(os.path.join(RAIZ, "cfv235_gtk", "ventana.py"), encoding="utf-8").read()
        arbol = ast.parse(fuente)
        objetivo = None
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and nodo.name == "abrir_dialogo_fichero":
                objetivo = nodo
                break
        self.assertIsNotNone(objetivo, "no encuentro abrir_dialogo_fichero")
        llamadas = set()
        for sub in ast.walk(objetivo):
            if isinstance(sub, ast.Call):
                with contextlib.suppress(Exception):
                    llamadas.add(ast.unparse(sub.func))
        self.assertIn("Gtk.FileChooserDialog", llamadas)
        self.assertNotIn("Gtk.FileDialog", llamadas)
        self.assertNotIn("Gtk.FileChooserNative.new", llamadas)


class TestAjusteDeImagenAlPanel(unittest.TestCase):
    """Una imagen que no es 1920x462 tiene que escalarse antes de subirla.

    El panel **no escala**: dibuja la imagen a su tamano y repite lo que falta en mosaico. Una
    foto de 1024x240 en una pantalla de 1920x462 sale 4 veces (2x2) y con la ultima cortada.
    """

    def _imagen_de_prueba(self, ancho, alto):
        from PIL import Image
        os.makedirs(PRUEBAS, exist_ok=True)
        ruta = os.path.join(PRUEBAS, "pequena-%dx%d.png" % (ancho, alto))
        Image.new("RGB", (ancho, alto), (30, 90, 160)).save(ruta)
        return ruta

    def test_los_tres_modos_dan_el_tamano_del_panel(self):
        from cfv235 import temas, video
        from PIL import Image
        ruta = self._imagen_de_prueba(1024, 240)
        with Image.open(ruta) as original:
            for modo in video.AJUSTES:
                with self.subTest(modo=modo):
                    ajustada = video.ajustar_imagen(original, modo)
                    self.assertEqual(ajustada.size, (temas.ANCHO, temas.ALTO))

    def test_una_imagen_del_tamano_exacto_no_se_toca(self):
        from cfv235 import temas, video
        from PIL import Image
        ruta = self._imagen_de_prueba(temas.ANCHO, temas.ALTO)
        with Image.open(ruta) as original:
            ajustada = video.ajustar_imagen(original, "ajustar")
            self.assertEqual(ajustada.size, (temas.ANCHO, temas.ALTO))

    def test_el_ajuste_se_guarda_en_las_preferencias(self):
        from cfv235 import config
        # OJO: se miran los DEFECTOS, no `leer()`, porque leer() mezcla el config que el
        # usuario tenga guardado en el equipo (y puede haberlo cambiado). El defecto es lo
        # que hay que proteger.
        defectos = config.DEFECTOS
        self.assertIn("ajustar_imagen", defectos)
        self.assertIn("ajuste_imagen", defectos)
        self.assertTrue(defectos["ajustar_imagen"],
                        "por defecto hay que ajustar: el panel no escala")


if __name__ == "__main__":
    unittest.main(verbosity=2)