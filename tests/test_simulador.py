"""Pruebas del canal y de la subida, contra el panel simulado (sin hardware).

    python3 -B tests/test_simulador.py
    bash tests/ejecutar.sh

Comprueba el camino completo: autonegociacion del tamano de informe, peticiones, subida de
una imagen y reconstruccion byte a byte del fichero, y que el fallo por caducidad de la
sesion se comporta como en el panel real (acuse `1 400` y `transported` con cuerpo vacio).
"""

import os
import sys
import threading
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import canal as modulo_canal  # noqa: E402
from cfv235 import panel as modulo_panel  # noqa: E402
from cfv235 import protocolo as p        # noqa: E402
from cfv235 import simulador             # noqa: E402

PRUEBAS = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-pruebas")


def png_de_prueba(ruta: str, ancho: int = 1920, alto: int = 462) -> bytes:
    """Un PNG valido de verdad (si hay Pillow); si no, uno minimo dibujado a mano."""
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (ancho, alto), (10, 20, 40))
        d = ImageDraw.Draw(img)
        for i in range(0, ancho, 120):
            d.rectangle([i, 0, i + 60, alto], fill=(i % 255, 90, 160))
        img.save(ruta)
    except ImportError:
        # PNG 1x1 valido, escrito a mano (por si no hay Pillow)
        import struct
        import zlib
        def trozo(tipo, datos):
            return (struct.pack(">I", len(datos)) + tipo + datos
                    + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF))
        crudo = b"\x00" + b"\x20\x40\x60"
        datos = (b"\x89PNG\r\n\x1a\n"
                 + trozo(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                 + trozo(b"IDAT", zlib.compress(crudo))
                 + trozo(b"IEND", b""))
        with open(ruta, "wb") as fh:
            fh.write(datos)
    with open(ruta, "rb") as fh:
        return fh.read()


class SimuladorEnPTY:
    """Levanta un panel simulado en un PTY y lo apaga de forma limpia al salir."""

    def __init__(self, **opciones):
        self.maestro, self.esclavo, self.nombre = simulador.abrir_pty()
        self.panel = simulador.PanelFalso(**opciones)
        self.parar = threading.Event()
        self.hilo = threading.Thread(target=simulador.servir,
                                     args=(self.maestro, self.panel, self.parar), daemon=True)
        self.hilo.start()

    def cerrar(self):
        # Primero se para el hilo y se espera a que salga; si no, el descriptor se cierra
        # mientras el hilo sigue vivo y el numero se reutiliza para otro PTY.
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


class TestCanal(BaseSimulador):

    def test_autonegociacion(self):
        sim = self.levantar()
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            variante = panel.canal.autonegociar(timeout=2.0)
            self.assertIsNotNone(variante)
            self.assertTrue(panel.canal.negociacion)
            self.assertTrue(any(i.get("ok") for i in panel.canal.negociacion))

    def test_ejecuta_comandos(self):
        sim = self.levantar()
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            props = panel.propiedades()
            self.assertEqual(props.get("bootFinish"), 1)
            self.assertIn("space", props)

            self.assertTrue(panel.brillo(40).ok)
            self.assertEqual(sim.panel.perfil["brightness"], 40)

            self.assertTrue(panel.girar(180).ok)
            self.assertEqual(sim.panel.perfil["degree"], 180)

            self.assertTrue(panel.no_dormir(True).ok)
            self.assertEqual(sim.panel.perfil["displayInSleep"], 0)

            self.assertTrue(panel.power("resume").ok)
            self.assertTrue(panel.realtime(True).ok)
            self.assertEqual(sim.panel.perfil["osdState"], 1)

    def test_ack_es_seq_mas_uno(self):
        sim = self.levantar()
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            respuesta = panel.canal.peticion("conn", None, seq=41, timeout=2.0)
            self.assertEqual(respuesta.code, 200)
            self.assertEqual(respuesta.ack, 42)

    def test_panel_que_no_arranca(self):
        sim = self.levantar(boot_finish=0)
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=2.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            props = panel.propiedades()
            self.assertEqual(props.get("bootFinish"), 0)
            self.assertFalse(panel.listo())


class TestSubida(BaseSimulador):

    def test_subida_completa_y_reconstruccion(self):
        salida = os.path.join(PRUEBAS, "recibido.png")
        sim = self.levantar(salida=salida)
        original = png_de_prueba(os.path.join(PRUEBAS, "origen.png"))
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            resultado = panel.subir_datos(original, "origen.png", capa="osd")
            self.assertTrue(resultado.ok, resultado.motivo)
            self.assertEqual(resultado.transport.code, 200)
            self.assertIn("blockMaxSize", resultado.transport.cuerpo)
            self.assertTrue(resultado.acuse_llego, "no llego el acuse de los bloques")
            self.assertEqual(resultado.acuse_code, 200)
            self.assertIn('"success"', resultado.transported.cuerpo)
            self.assertEqual(resultado.bloques, p.bloques_de(len(original)))
        # el fichero reconstruido por el panel simulado es identico al enviado
        self.assertTrue(os.path.isfile(salida))
        with open(salida, "rb") as fh:
            self.assertEqual(fh.read(), original)
        self.assertEqual(sim.panel.subidas_ok, 1)
        self.assertEqual(sim.panel.subidas_fallidas, 0)

    def test_subida_tardia_falla_como_el_panel_real(self):
        # La sesion se abre al contestar al `transport` y caduca en 1 ms; el simulador
        # retrasa el primer bloque 50 ms, asi que llega fuera de plazo.
        sim = self.levantar(caduca_ms=1.0, tarde_ms=50)
        original = png_de_prueba(os.path.join(PRUEBAS, "tarde.png"))
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            resultado = panel.subir_datos(original, "tarde.png", capa="fondo")
        self.assertFalse(resultado.ok)
        self.assertTrue(resultado.acuse_llego)
        self.assertEqual(resultado.acuse_code, 400, "deberia acusar 400")
        self.assertEqual(resultado.transported.code, 200)
        self.assertEqual(resultado.transported.cuerpo.strip(), "",
                         "con la sesion caducada el cuerpo va vacio")
        self.assertEqual(sim.panel.subidas_fallidas, 1)

    def test_no_sube_si_no_cabe(self):
        sim = self.levantar()
        original = png_de_prueba(os.path.join(PRUEBAS, "grande.png"))
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            sim.panel.perfil["space"] = 1            # 1 KB libre
            resultado = panel.subir_datos(original, "grande.png", capa="fondo")
            self.assertFalse(resultado.ok)
            self.assertIn("no cabe", resultado.motivo)
            self.assertEqual(sim.panel.subidas_ok, 0,
                             "no deberia haber empezado la subida")

    def test_rechaza_lo_que_no_es_imagen(self):
        sim = self.levantar()
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            resultado = panel.subir_datos(b"no soy un png", "x.png", capa="fondo")
            self.assertFalse(resultado.ok)
            self.assertIn("no es PNG", resultado.motivo)

    def test_avisa_del_gif(self):
        sim = self.levantar()
        gif = b"GIF89a" + b"\x00" * 2000
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            resultado = panel.subir_datos(gif, "x.gif", capa="osd")
            self.assertTrue(any("blanco" in a for a in resultado.avisos))

    def test_subida_grande_no_pierde_escrituras(self):
        """Un fichero mucho mayor que el buffer del PTY no debe perder escrituras.

        El descriptor se abre en O_NONBLOCK: sin reintentar EAGAIN, `os.write` falla en
        cuanto el buffer se llena y la subida se pierde de forma intermitente (era un bug
        real de `canal._escribir`, que solo se veia con el simulador).
        """
        try:
            import random

            from PIL import Image
        except ImportError:
            self.skipTest("hace falta Pillow")
        random.seed(11)
        img = Image.new("RGB", (500, 300))
        img.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                     for _ in range(500 * 300)])
        ruta = os.path.join(PRUEBAS, "grande_ruido.png")
        img.save(ruta)
        with open(ruta, "rb") as fh:
            original = fh.read()
        self.assertGreater(len(original), 100_000, "el PNG deberia superar el buffer del PTY")

        salida = os.path.join(PRUEBAS, "grande_recibido.png")
        sim = self.levantar(salida=salida)
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=6.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            for intento in range(3):                 # varias pasadas: el fallo era intermitente
                resultado = panel.subir_datos(original, f"ruido{intento}.png", capa="osd")
                self.assertTrue(resultado.ok, resultado.motivo)
                self.assertEqual(resultado.acuse_code, 200)
        with open(salida, "rb") as fh:
            self.assertEqual(fh.read(), original)
        self.assertEqual(sim.panel.subidas_ok, 3)
        self.assertEqual(sim.panel.subidas_fallidas, 0)

    def test_bloqueo_reentrante_y_liberacion(self):
        """El bloqueo del panel es exclusivo entre procesos pero reentrante dentro de uno.

        `flock` cuenta descriptores, no procesos: sin el contador interno, abrir dos veces el
        panel en el mismo programa parecia "otro proceso lo tiene".
        """
        sim = self.levantar()
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as primero:
            primero.canal.autonegociar(timeout=2.0)
            self.assertIsNotNone(primero.canal._lock)
            with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as segundo:
                segundo.canal.autonegociar(timeout=2.0)
                self.assertTrue(segundo.propiedades(), "el segundo panel deberia funcionar")
        # al cerrar los dos, el panel queda libre para otra sesion
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as tercero:
            tercero.canal.autonegociar(timeout=2.0)
            self.assertTrue(tercero.propiedades())

    def test_reabrir_respeta_la_ruta_pedida(self):
        """Al reconectar NO debe cambiarse a otro dispositivo distinto del elegido.

        Antes, `reabrir()` olvidaba la ruta y buscaba cualquier hidraw con el VID/PID del
        panel: con `--device /dev/pts/N` (el simulador) acababa abriendo el PANEL REAL.
        """
        sim = self.levantar()
        panel = modulo_panel.Panel(dispositivo=sim.nombre, timeout=2.0)
        panel.abrir()
        panel.canal.autonegociar(timeout=2.0)
        self.assertEqual(panel.canal.ruta, sim.nombre)
        self.assertTrue(panel.propiedades())
        panel.cerrar()

        # con el simulador ya cerrado, reabrir no puede irse a otro dispositivo
        self.sim.cerrar()
        self.sim = None
        huerfano = modulo_panel.Panel(dispositivo=sim.nombre, timeout=1.0)
        try:
            self.assertFalse(huerfano.reabrir(),
                             "no deberia encontrar otro panel si se pidio ese dispositivo")
            self.assertEqual(huerfano.canal.ruta, sim.nombre,
                             "deberia conservar la ruta que se pidio")
        finally:
            huerfano.cerrar()

    def test_recovery_reinicia(self):
        sim = self.levantar()
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            self.assertTrue(panel.recovery().ok)
        self.assertIsNone(sim.panel.medios["nombre"])
        self.assertEqual(sim.panel.perfil["osdState"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
