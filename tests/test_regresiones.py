"""Pruebas de regresion de los fallos que encontro la auditoria del nucleo.

Cada prueba corresponde a un bug concreto que se reprodujo y se arreglo; sirven para que no
vuelva. No necesitan panel: usan el simulador en un PTY y objetos sueltos.

    python3 -B tests/test_regresiones.py
"""

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

    `Gtk.FileDialog` le pide el dialogo al portal del escritorio, y el portal necesita el
    token de activacion que solo existe si la app se lanzo desde el escritorio. Sin el, el
    portal falla en silencio:

        xdg-desktop-portal-gnome: Failed to associate portal window with parent window ''

    El boton parece no hacer nada, sin ningun error. La app usa el dialogo propio de GTK
    poniendo `GTK_USE_PORTAL=0` antes de inicializar GTK.
    """

    def _entorno_al_importar(self, **extra):
        """Devuelve el GTK_USE_PORTAL que queda tras importar el punto de entrada."""
        codigo = (
            "import os, sys;"
            f"sys.path.insert(0, {RAIZ!r});"
            "os.environ.pop('XDG_ACTIVATION_TOKEN', None);"
            "os.environ.pop('DESKTOP_STARTUP_ID', None);"
            "os.environ.pop('GTK_USE_PORTAL', None);"
            "import cfv235_gtk.app;"
            "print(os.environ.get('GTK_USE_PORTAL'))"
        )
        entorno = dict(os.environ)
        entorno.update(extra)
        salida = subprocess.run([sys.executable, "-B", "-c", codigo], capture_output=True,
                                text=True, timeout=120, env=entorno)
        return salida.stdout.strip(), salida.stderr

    def test_por_defecto_usa_el_dialogo_de_gtk(self):
        valor, errores = self._entorno_al_importar()
        self.assertEqual(valor, "0",
                         "sin GTK_USE_PORTAL=0 el dialogo del portal no se ve: " + errores[-300:])

    def test_con_la_variable_puesta_usa_el_portal(self):
        valor, _ = self._entorno_al_importar(CFV235_DIALOGO_PORTAL="1")
        self.assertNotEqual(valor, "0", "CFV235_DIALOGO_PORTAL=1 deberia dejar el portal")


if __name__ == "__main__":
    unittest.main(verbosity=2)