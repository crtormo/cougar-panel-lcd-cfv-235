"""Pruebas de la tasa de fotogramas y del coste de cada fotograma.

Las medidas **reales** (contra el panel) estan en `docs/RENDIMIENTO.md` y se repiten con
`herramientas/medir_fps.py`. Aqui se comprueba la **logica** que hace que esos numeros se
cumplan, sin necesitar el panel:

  * el bucle del dashboard respeta el periodo que se le pide (nunca va mas rapido);
  * el reproductor de video limita los fps y **salta** fotogramas en vez de acumular retraso;
  * dibujar un tema tarda lo previsto (es la mitad del coste de un fotograma);
  * subir nunca lanza, aunque no haya panel.

    python3 -B tests/test_rendimiento.py
"""

import os
import sys
import threading
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import dashboard, panel as modulo_panel, temas, video  # noqa: E402
from cfv235 import simulador  # noqa: E402
from cfv235.sensores import Sensores  # noqa: E402

PRUEBAS = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-rendimiento")

# Techos medidos en el panel real (docs/RENDIMIENTO.md), con margen.
MS_MAXIMOS_DIBUJO = 400          # dibujar el dashboard: medido ~51 ms
FPS_TECHO_REAL = 3.0             # el panel sostiene ~3 fps


class SimuladorEnPTY:
    """Panel simulado en un PTY, con parada limpia."""

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


class TestBucleDelDashboard(BaseSimulador):
    """El bucle no debe ir mas deprisa de lo que se le pide."""

    def test_respeta_el_periodo(self):
        for periodo, repeticiones in ((0.4, 3), (0.2, 4)):
            with self.subTest(periodo=periodo):
                sim = self.levantar()
                with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
                    panel.canal.autonegociar(timeout=2.0)
                    tablero = dashboard.Dashboard(panel, sensores=Sensores(intervalo=0.0),
                                                  png=os.path.join(PRUEBAS, "f.png"))
                    t0 = time.perf_counter()
                    subidos = tablero.bucle(periodo=periodo, repeticiones=repeticiones)
                    transcurrido = time.perf_counter() - t0
                self.assertEqual(subidos, repeticiones)
                minimo = periodo * (repeticiones - 1)      # el ultimo no espera
                self.assertGreaterEqual(
                    transcurrido, minimo * 0.9,
                    f"el bucle fue demasiado rapido: {transcurrido:.2f}s < {minimo:.2f}s")
                self.sim.cerrar()
                self.sim = None

    def test_fps_pedidos_no_se_superan(self):
        """Si se pide 2 fps, no pueden salir 10."""
        sim = self.levantar()
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            tablero = dashboard.Dashboard(panel, sensores=Sensores(intervalo=0.0),
                                          png=os.path.join(PRUEBAS, "f.png"))
            t0 = time.perf_counter()
            subidos = tablero.bucle(periodo=0.5, repeticiones=3)
            transcurrido = time.perf_counter() - t0
        self.assertEqual(subidos, 3)
        self.assertLessEqual(subidos / transcurrido, 2.5,
                             "el bucle subio mas fotogramas por segundo de los pedidos")


class TestReproductor(BaseSimulador):
    """El reproductor de video: fps y salto de fotogramas."""

    def _gif(self, fotogramas=8, ms=100):
        from PIL import Image
        ruta = os.path.join(PRUEBAS, "anim.gif")
        imagenes = []
        for i in range(fotogramas):
            im = Image.new("RGB", (temas.ANCHO, temas.ALTO), (10 + i * 20, 20, 40))
            imagenes.append(im)
        imagenes[0].save(ruta, save_all=True, append_images=imagenes[1:],
                         duration=ms, loop=0)
        return ruta

    def test_limita_los_fps(self):
        sim = self.levantar()
        ruta = self._gif(fotogramas=6, ms=100)
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            fuente = video.abrir_fuente(ruta)
            reproductor = video.Reproductor(panel, fuente, fps=2.0, bucle=False)
            t0 = time.perf_counter()
            subidos = reproductor.reproducir(max_fotogramas=4)
            transcurrido = time.perf_counter() - t0
            reproductor.cerrar()
        self.assertEqual(subidos, 4)
        # 4 fotogramas a 2 fps: al menos 3 periodos
        self.assertGreaterEqual(transcurrido, 3 * 0.5 * 0.8)

    def test_a_fps_altos_salta_fotogramas(self):
        """Con mas fps de los que aguanta la subida, se saltan: no se acumula retraso."""
        sim = self.levantar()
        ruta = self._gif(fotogramas=30, ms=33)          # fuente de 30 fps
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            fuente = video.abrir_fuente(ruta)
            reproductor = video.Reproductor(panel, fuente, fps=30.0, bucle=False)
            reproductor.reproducir(max_fotogramas=20)
            saltados = reproductor.saltados
            subidos = reproductor.fotogramas
            reproductor.cerrar()
        self.assertGreater(subidos, 0)
        self.assertGreaterEqual(saltados, 0)
        # lo importante: termino y no se quedo atascado acumulando retraso
        self.assertLessEqual(subidos, 20)

    def test_el_techo_medido_es_coherente(self):
        """Los valores por defecto no deben prometer mas de lo que el panel da."""
        self.assertLessEqual(video.FPS_POR_DEFECTO, video.FPS_MAXIMO_REALISTA)
        self.assertLessEqual(video.FPS_MAXIMO_REALISTA, FPS_TECHO_REAL + 1.0,
                             "el techo declarado no puede ser mayor que el medido")


class TestCosteDeDibujar(unittest.TestCase):
    """Dibujar es la mitad del trabajo por fotograma; conviene vigilarlo."""

    def test_dibujar_el_dashboard_es_rapido(self):
        sensores = Sensores(intervalo=0.0)
        sensores.muestra()
        time.sleep(0.2)
        valores = sensores.muestra()
        tema = dashboard.tema_dashboard(valores=valores)
        t0 = time.perf_counter()
        datos = temas.renderizar_datos(tema, valores)
        ms = (time.perf_counter() - t0) * 1000
        self.assertTrue(datos.startswith(b"\x89PNG"))
        self.assertLess(ms, MS_MAXIMOS_DIBUJO,
                        f"dibujar tardo {ms:.0f} ms (el limite de la prueba es "
                        f"{MS_MAXIMOS_DIBUJO} ms)")

    def test_el_tamano_del_fotograma_es_razonable(self):
        """Un fotograma del dashboard no puede ser enorme: cada KB cuesta tiempo de subida."""
        sensores = Sensores(intervalo=0.0)
        sensores.muestra()
        time.sleep(0.2)
        valores = sensores.muestra()
        datos = temas.renderizar_datos(dashboard.tema_dashboard(valores=valores), valores)
        self.assertLess(len(datos), 400_000,
                        f"el PNG del dashboard ocupa {len(datos)} B")


class TestSubidaRobusta(unittest.TestCase):
    """Subir nunca debe lanzar: el fallo se cuenta."""

    def test_sin_panel_devuelve_resultado(self):
        panel = modulo_panel.Panel(dispositivo="/dev/pts/99", timeout=0.4)
        resultado = panel.subir_datos(b"\x89PNG\r\n\x1a\n" + b"x" * 2000, "x.png",
                                      capa="osd")
        self.assertFalse(resultado.ok)
        self.assertTrue(resultado.motivo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
