"""Pruebas del modulo de video (GIF, secuencia, video y reproduccion en el panel).

    python3 -B tests/test_video.py
    bash tests/ejecutar.sh

No hace falta panel real: la reproduccion se prueba contra el **panel simulado** en un PTY,
igual que `tests/test_simulador.py`. Las pruebas de video con GStreamer se saltan solas si no
hay GStreamer o no hay codificador H.264; nunca instalan nada.
"""

import os
import subprocess
import sys
import threading
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from cfv235 import panel as modulo_panel        # noqa: E402
from cfv235 import simulador                    # noqa: E402
from cfv235 import video                        # noqa: E402
from cfv235.panel import ResultadoSubida        # noqa: E402

PRUEBAS = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cfv235-pruebas-video")
ANCHO, ALTO = 1920, 462

try:
    from PIL import Image
    HAY_PIL = True
except ImportError:                             # pragma: no cover
    HAY_PIL = False

# Colores distinguibles, uno por fotograma, para poder comprobar CUAL se subio.
COLORES = [(220, 40, 40), (40, 220, 60), (50, 90, 230), (230, 200, 40), (200, 60, 200),
           (30, 200, 200), (250, 130, 30), (140, 140, 140), (90, 30, 160), (10, 120, 60)]


def _preparar() -> None:
    os.makedirs(PRUEBAS, exist_ok=True)


def _ruta(nombre: str) -> str:
    _preparar()
    return os.path.join(PRUEBAS, nombre)


# ------------------------------------------------------------------ fuentes de prueba
def gif_de_prueba(nombre: str = "animacion.gif", fotogramas: int = 10,
                  ancho: int = ANCHO, alto: int = ALTO, duracion: int = 100) -> str:
    """GIF animado de verdad, con `fotogramas` fotogramas de color plano y distinto."""
    if not HAY_PIL:
        raise unittest.SkipTest("hace falta Pillow")
    ruta = _ruta(nombre)
    cuadros = [Image.new("RGB", (ancho, alto), COLORES[i % len(COLORES)])
               for i in range(fotogramas)]
    cuadros[0].save(ruta, save_all=True, append_images=cuadros[1:], duration=duracion,
                    loop=0)
    return ruta


def carpeta_de_prueba(nombre: str = "secuencia", cuantos: int = 5,
                      ancho: int = ANCHO, alto: int = ALTO) -> str:
    """Carpeta con `cuantos` PNG, cada uno de un color distinto."""
    if not HAY_PIL:
        raise unittest.SkipTest("hace falta Pillow")
    carpeta = _ruta(nombre)
    os.makedirs(carpeta, exist_ok=True)
    for i in range(cuantos):
        Image.new("RGB", (ancho, alto), COLORES[i % len(COLORES)]).save(
            os.path.join(carpeta, f"cuadro{i + 1:02d}.png"))
    return carpeta


def hay_comando(nombre: str) -> bool:
    from shutil import which
    return which(nombre) is not None


def hay_encoder_h264() -> bool:
    """True si GStreamer tiene x264enc (lo unico que hace falta para generar un mp4)."""
    if not hay_comando("gst-launch-1.0"):
        return False
    try:
        proceso = subprocess.run(["gst-inspect-1.0", "x264enc"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):    # pragma: no cover
        return False
    return proceso.returncode == 0


def video_de_prueba(nombre: str = "prueba.mp4", fotogramas: int = 12, ancho: int = 640,
                    alto: int = 360, fps: int = 15):
    """Genera un mp4 con GStreamer (videotestsrc + x264enc). None si no se puede."""
    if not hay_encoder_h264():
        return None
    ruta = _ruta(nombre)
    orden = ["gst-launch-1.0", "-q",
             "videotestsrc", f"num-buffers={fotogramas}", "pattern=ball", "!",
             f"video/x-raw,width={ancho},height={alto},framerate={fps}/1", "!",
             "x264enc", "speed-preset=ultrafast", "key-int-max=15", "!",
             "mp4mux", "!",
             # `location=...` va como argumento aparte: gst-launch no parte los argumentos
             # que llevan un espacio dentro.
             "filesink", f"location={ruta}"]
    try:
        subprocess.run(orden, check=True, timeout=120, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):    # pragma: no cover
        return None
    return ruta if os.path.isfile(ruta) and os.path.getsize(ruta) > 0 else None


def color_de(ruta: str):
    """Color del pixel (0,0) de un PNG, para saber QUE fotograma llego al panel."""
    with Image.open(ruta) as im:
        return im.convert("RGB").getpixel((0, 0))


def tamano_de(ruta: str):
    with Image.open(ruta) as im:
        return im.size


# ------------------------------------------------------------------ panel simulado
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


class PanelQueFalla:
    """Panel de mentira para probar que los fallos de subida no matan el bucle."""

    def __init__(self, excepcion: bool = False):
        self.excepcion = excepcion
        self.llamadas = 0
        self.nombres = []

    def subir_datos(self, datos, nombre, capa="fondo"):
        self.llamadas += 1
        self.nombres.append((nombre, capa))
        if self.excepcion:
            raise RuntimeError("el canal se ha caido (prueba)")
        return ResultadoSubida(ok=False, motivo="el panel dijo que no (prueba)")


class PanelLento:
    """Panel de mentira que tarda lo que tarda el panel real en cada subida (~160 ms)."""

    def __init__(self, retardo: float = 0.16):
        self.retardo = retardo
        self.llamadas = 0

    def subir_datos(self, datos, nombre, capa="fondo"):
        self.llamadas += 1
        time.sleep(self.retardo)
        return ResultadoSubida(ok=True, nombre=nombre, capa=capa, bytes=len(datos))


@unittest.skipUnless(HAY_PIL, "hace falta Pillow")
class TestInspeccion(unittest.TestCase):
    """El informe de la fuente: `inspeccionar()` no necesita panel."""

    def test_gif_de_10_fotogramas(self):
        ruta = gif_de_prueba("info10.gif", fotogramas=10)
        informe = video.inspeccionar(ruta)
        self.assertEqual(informe["tipo"], "gif")
        self.assertEqual(informe["fotogramas"], 10)
        self.assertEqual(informe["tamano"], (ANCHO, ALTO))
        self.assertFalse(informe["necesita_gstreamer"])
        self.assertEqual(informe["error"], "")
        self.assertEqual(informe["ruta"], os.path.abspath(ruta))
        self.assertTrue(0 < informe["fps"] <= video.FPS_MAXIMO_REALISTA)
        self.assertAlmostEqual(informe["duracion_s"], 1.0, places=2)
        self.assertIn("GIF", video.informe_legible(informe))

    def test_carpeta_de_5_png(self):
        carpeta = carpeta_de_prueba("info5", cuantos=5)
        informe = video.inspeccionar(carpeta)
        self.assertEqual(informe["tipo"], "secuencia")
        self.assertEqual(informe["fotogramas"], 5)
        self.assertEqual(informe["archivos"], 5)
        self.assertEqual(informe["tamano"], (ANCHO, ALTO))
        self.assertFalse(informe["necesita_gstreamer"])
        self.assertEqual(informe["error"], "")

    def test_imagen_suelta_es_secuencia_de_uno(self):
        ruta = _ruta("suelta.png")
        Image.new("RGB", (ANCHO, ALTO), COLORES[0]).save(ruta)
        informe = video.inspeccionar(ruta)
        self.assertEqual(informe["tipo"], "secuencia")
        self.assertEqual(informe["fotogramas"], 1)

    def test_ruta_inexistente_no_lanza(self):
        informe = video.inspeccionar(_ruta("no-existe.gif"))
        self.assertEqual(informe["tipo"], "desconocido")
        self.assertIn("no existe", informe["error"])
        self.assertIn("ERROR", video.informe_legible(informe))

    def test_basura_con_extension_de_video(self):
        ruta = _ruta("basura.mp4")
        with open(ruta, "wb") as fh:
            fh.write(b"esto no es un video" * 100)
        informe = video.inspeccionar(ruta)
        self.assertEqual(informe["tipo"], "video")
        self.assertTrue(informe["necesita_gstreamer"])
        # Sin GStreamer el motivo es el suyo; con GStreamer, el fallo de decodificacion.
        self.assertTrue(informe["error"], "un fichero ilegible deberia traer un error claro")

    def test_video_de_verdad(self):
        ruta = video_de_prueba("info.mp4", fotogramas=12)
        if ruta is None:
            self.skipTest("no hay x264enc para generar un mp4 de prueba")
        if not video.gstreamer_disponible():
            self.skipTest("no hay GStreamer: se prueba aparte con _GST_ERROR")
        informe = video.inspeccionar(ruta)
        self.assertEqual(informe["tipo"], "video")
        self.assertTrue(informe["necesita_gstreamer"])
        self.assertEqual(informe["tamano"], (640, 360))
        self.assertGreater(informe["fps"], 1.0)
        self.assertGreater(informe["fotogramas"] or 0, 0)
        self.assertTrue(informe["fotogramas_estimados"])
        self.assertEqual(informe["error"], "")


@unittest.skipUnless(HAY_PIL, "hace falta Pillow")
class TestCarga(unittest.TestCase):
    """Carga de fotogramas: tamaños, orden, ajustes y errores legibles."""

    def test_gif_carga_los_10_fotogramas(self):
        ruta = gif_de_prueba("carga10.gif", fotogramas=10)
        fuente = video.abrir_fuente(ruta)
        self.assertIsInstance(fuente, video.FuenteGIF)
        self.assertEqual(fuente.fotogramas_conocidos, 10)
        vistos = 0
        duraciones = []
        colores = []
        while True:
            cuadro = fuente.siguiente()
            if cuadro is None:
                break
            imagen, duracion = cuadro
            self.assertEqual(imagen.size, (ANCHO, ALTO))
            self.assertEqual(imagen.mode, "RGB")
            colores.append(imagen.getpixel((0, 0)))
            duraciones.append(duracion)
            vistos += 1
        fuente.cerrar()
        self.assertEqual(vistos, 10)
        self.assertEqual(colores, COLORES[:10])
        self.assertTrue(all(d == 100 for d in duraciones), duraciones)

    def test_secuencia_de_carpeta_ordenada(self):
        carpeta = carpeta_de_prueba("orden", cuantos=5)
        fuente = video.abrir_fuente(carpeta)
        self.assertIsInstance(fuente, video.FuenteSecuencia)
        self.assertEqual(fuente.fotogramas_conocidos, 5)
        colores = []
        while True:
            cuadro = fuente.siguiente()
            if cuadro is None:
                break
            colores.append(cuadro[0].getpixel((0, 0)))
        fuente.cerrar()
        self.assertEqual(colores, COLORES[:5])

    def test_orden_natural_2_antes_que_10(self):
        # Con el orden alfabetico normal, cuadro10.png iria antes que cuadro2.png.
        carpeta = _ruta("natural")
        os.makedirs(carpeta, exist_ok=True)
        for numero, color in ((1, COLORES[0]), (2, COLORES[1]), (10, COLORES[2])):
            Image.new("RGB", (ANCHO, ALTO), color).save(
                os.path.join(carpeta, f"cuadro{numero}.png"))
        fuente = video.abrir_fuente(carpeta)
        colores = [cuadro[0].getpixel((0, 0)) for cuadro in fuente]
        fuente.cerrar()
        self.assertEqual(colores, [COLORES[0], COLORES[1], COLORES[2]])

    def test_lista_de_rutas_respeta_el_orden_dado(self):
        rutas = []
        for i in range(3):
            ruta = _ruta(f"lista{i}.png")
            Image.new("RGB", (ANCHO, ALTO), COLORES[i]).save(ruta)
            rutas.append(ruta)
        fuente = video.abrir_fuente(list(reversed(rutas)))
        colores = [cuadro[0].getpixel((0, 0)) for cuadro in fuente]
        fuente.cerrar()
        self.assertEqual(colores, [COLORES[2], COLORES[1], COLORES[0]])

    def test_ajustes(self):
        origen = Image.new("RGB", (640, 360), COLORES[3])
        # ajustar (letterbox): la imagen entera, con bandas negras arriba y abajo
        ajustada = video.ajustar_imagen(origen, "ajustar")
        self.assertEqual(ajustada.size, (ANCHO, ALTO))
        self.assertEqual(ajustada.getpixel((ANCHO // 2, ALTO // 2)), COLORES[3])
        self.assertEqual(ajustada.getpixel((0, 0)), (0, 0, 0))
        # recortar: llena el panel recortando lo que sobra
        recortada = video.ajustar_imagen(origen, "recortar")
        self.assertEqual(recortada.size, (ANCHO, ALTO))
        self.assertEqual(recortada.getpixel((0, 0)), COLORES[3])
        # estirar: ocupa todo, deformando
        estirada = video.ajustar_imagen(origen, "estirar")
        self.assertEqual(estirada.size, (ANCHO, ALTO))
        self.assertEqual(estirada.getpixel((ANCHO - 1, ALTO - 1)), COLORES[3])
        # una imagen que ya es del tamano del panel se deja igual
        igual = video.ajustar_imagen(Image.new("RGB", (ANCHO, ALTO), COLORES[1]), "ajustar")
        self.assertEqual(igual.size, (ANCHO, ALTO))
        self.assertEqual(igual.getpixel((0, 0)), COLORES[1])
        # con transparencia (RGBA) se convierte a RGB para poder pegarla
        rgba = Image.new("RGBA", (100, 100), (10, 20, 30, 128))
        self.assertEqual(video.ajustar_imagen(rgba, "ajustar").mode, "RGB")

    def test_ajuste_desconocido(self):
        with self.assertRaises(video.ErrorVideo) as caso:
            video.ajustar_imagen(Image.new("RGB", (10, 10)), "inventado")
        self.assertIn("ajuste desconocido", str(caso.exception))

    def test_fuente_inexistente(self):
        with self.assertRaises(video.ErrorVideo) as caso:
            video.abrir_fuente(_ruta("no-existe-tampoco.gif"))
        self.assertIn("no existe", str(caso.exception))

    def test_video_carga_fotogramas(self):
        ruta = video_de_prueba("carga.mp4", fotogramas=12)
        if ruta is None:
            self.skipTest("no hay x264enc para generar un mp4 de prueba")
        if not video.gstreamer_disponible():
            self.skipTest("no hay GStreamer")
        fuente = video.FuenteVideo(ruta, ajuste="ajustar")
        self.assertEqual(fuente.tamano_original, (640, 360))
        vistos = 0
        while True:
            cuadro = fuente.siguiente()
            if cuadro is None:
                break
            self.assertEqual(cuadro[0].size, (ANCHO, ALTO))
            self.assertEqual(cuadro[0].mode, "RGB")
            self.assertGreater(cuadro[1], 0)
            vistos += 1
        self.assertTrue(fuente.agotado, "al leer todo el video deberia marcar el final")
        fuente.cerrar()
        self.assertEqual(vistos, 12)
        # volver a empezar tras agotarlo tambien funciona (es lo que hace el bucle)
        fuente.reiniciar()
        self.assertIsNotNone(fuente.siguiente())
        fuente.cerrar()

    def test_video_inexistente_da_error_claro(self):
        with self.assertRaises(video.ErrorVideo) as caso:
            video.FuenteVideo(_ruta("no-existe.mp4"))
        self.assertIn("no existe el video", str(caso.exception))

    def test_video_ilegible_da_error_legible(self):
        if not video.gstreamer_disponible():
            self.skipTest("no hay GStreamer")
        ruta = _ruta("roto.mp4")
        with open(ruta, "wb") as fh:
            fh.write(b"\x00\x01\x02\x03" * 512)
        fuente = video.FuenteVideo(ruta, timeout_s=1.0)
        with self.assertRaises(video.ErrorVideo) as caso:
            fuente.siguiente()
        fuente.cerrar()
        mensaje = str(caso.exception)
        self.assertTrue("no pudo leer" in mensaje or "no entrega fotogramas" in mensaje
                        or "no reconocio" in mensaje, mensaje)

    def test_sin_gstreamer_el_error_lo_dice(self):
        """La importacion de GStreamer es perezosa: si no esta, se avisa con texto claro."""
        ruta = _ruta("dummy.mp4")
        with open(ruta, "wb") as fh:
            fh.write(b"x" * 100)
        guardado = (video._GST, video._GST_ERROR)
        video._GST, video._GST_ERROR = None, "GStreamer no esta (prueba)"
        try:
            self.assertFalse(video.gstreamer_disponible())
            self.assertIn("GStreamer no esta", video.motivo_sin_gstreamer())
            informe = video.inspeccionar(ruta)
            self.assertTrue(informe["necesita_gstreamer"])
            self.assertIn("GStreamer no esta", informe["error"])
            fuente = video.FuenteVideo(ruta)          # construir no necesita GStreamer
            with self.assertRaises(video.ErrorVideo) as caso:
                fuente.siguiente()
            self.assertIn("GStreamer no esta", str(caso.exception))
        finally:
            video._GST, video._GST_ERROR = guardado


@unittest.skipUnless(HAY_PIL, "hace falta Pillow")
class TestReproductor(unittest.TestCase):
    """Reproduccion contra el panel simulado y contadores del bucle."""

    def setUp(self):
        _preparar()
        self.sim = None

    def tearDown(self):
        if self.sim:
            self.sim.cerrar()

    def levantar(self, **opciones):
        self.sim = SimuladorEnPTY(**opciones)
        return self.sim

    def test_periodo_respeta_fps_y_duracion_de_la_fuente(self):
        reproductor = video.Reproductor(PanelQueFalla(), video.abrir_fuente(
            gif_de_prueba("periodo.gif", fotogramas=2)), fps=4)
        # 4 fps manda sobre un fotograma de 50 ms; una duracion mayor manda sobre los 4 fps
        self.assertAlmostEqual(reproductor.periodo(50), 0.25, places=3)
        self.assertAlmostEqual(reproductor.periodo(1000), 1.0, places=3)
        self.assertAlmostEqual(reproductor.periodo(None), 0.25, places=3)

    def test_sube_n_fotogramas_y_el_ultimo_se_reconstruye(self):
        """El caso completo: fuente -> PNG -> subida -> fichero reconstruido por el panel."""
        salida = _ruta("recibido.png")
        carpeta = carpeta_de_prueba("reproduce", cuantos=5)
        sim = self.levantar(salida=salida)
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            fuente = video.abrir_fuente(carpeta)
            reproductor = video.Reproductor(panel, fuente, fps=4, bucle=False)
            avisos = []
            subidos = reproductor.reproducir(avisar=lambda n, ok, e, ms: avisos.append(
                (n, ok, ms)))
        # 5 fotogramas de la secuencia, 5 subidas aceptadas por el panel simulado
        self.assertEqual(subidos, 5)
        self.assertEqual(reproductor.fotogramas, 5)
        self.assertEqual(len(avisos), 5)
        self.assertTrue(all(ok for _, ok, _ in avisos))
        self.assertEqual(reproductor.saltados, 0, "no deberia hacer falta saltar fotogramas")
        self.assertEqual(sim.panel.subidas_ok, 5)
        self.assertEqual(sim.panel.subidas_fallidas, 0)
        # el ultimo fotograma subido es el que el panel reconstruyo, byte a byte
        with open(salida, "rb") as fh:
            recibido = fh.read()
        self.assertEqual(recibido, reproductor.ultimo_png)
        self.assertEqual(tamano_de(salida), (ANCHO, ALTO))
        self.assertEqual(color_de(salida), COLORES[4], "el ultimo fotograma es el 5 de 5")
        self.assertEqual(sim.panel.medios["nombre"], video.NOMBRE)
        self.assertEqual(sim.panel.perfil["osdState"], 1, "la capa por defecto es la OSD")

    def test_nombre_fijo_y_reutiliza_el_hueco(self):
        """Todos los fotogramas van con el MISMO nombre: el panel reescribe el mismo hueco."""
        sim = self.levantar()
        gif = gif_de_prueba("mismo-nombre.gif", fotogramas=4)
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            reproductor = video.Reproductor(panel, video.abrir_fuente(gif), fps=8)
            reproductor.reproducir(max_fotogramas=4)
        self.assertEqual(sim.panel.subidas_ok, 4)
        self.assertEqual(sim.panel.medios["nombre"], video.NOMBRE)
        # el nombre lleva el nombre fijo, no el de la fuente
        self.assertNotIn("gif", video.NOMBRE)

    def test_bucle_vuelve_al_principio(self):
        sim = self.levantar(salida=_ruta("bucle.png"))
        gif = gif_de_prueba("bucle.gif", fotogramas=3)
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            reproductor = video.Reproductor(panel, video.abrir_fuente(gif), fps=8, bucle=True)
            reproductor.reproducir(max_fotogramas=4)   # 3 + 1: el cuarto es el primero otra vez
        self.assertEqual(reproductor.fotogramas, 4)
        self.assertEqual(color_de(_ruta("bucle.png")), COLORES[0])

    def test_parar_desde_otro_hilo(self):
        """`parar` es un threading.Event: el bucle se corta y termina limpio."""
        sim = self.levantar()
        gif = gif_de_prueba("parar.gif", fotogramas=10)
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            reproductor = video.Reproductor(panel, video.abrir_fuente(gif), fps=8)
            parar = threading.Event()
            resultado = {}

            def correr():
                resultado["subidos"] = reproductor.reproducir(parar=parar)

            hilo = threading.Thread(target=correr)
            hilo.start()
            time.sleep(0.7)
            parar.set()
            hilo.join(timeout=5.0)
            self.assertFalse(hilo.is_alive(), "el bucle no se paro al activar `parar`")
            subidos = resultado["subidos"]
        self.assertGreater(subidos, 0)
        self.assertLess(subidos, 10, "deberia haberse cortado antes de acabar el GIF")

    def test_sin_bucle_termina_solo(self):
        sim = self.levantar()
        carpeta = carpeta_de_prueba("sin-bucle", cuantos=3)
        with modulo_panel.Panel(dispositivo=sim.nombre, timeout=3.0) as panel:
            panel.canal.autonegociar(timeout=2.0)
            reproductor = video.Reproductor(panel, video.abrir_fuente(carpeta), fps=10,
                                            bucle=False)
            inicio = time.time()
            subidos = reproductor.reproducir()
            transcurrido = time.time() - inicio
        self.assertEqual(subidos, 3)
        self.assertTrue(reproductor.fin, "con bucle=False el bucle debe marcar el final")
        self.assertLess(transcurrido, 5.0, "no debe quedarse dando vueltas")
        self.assertEqual(sim.panel.subidas_ok, 3)

    def test_los_fallos_se_cuentan_y_no_matan_el_bucle(self):
        panel = PanelQueFalla()
        gif = gif_de_prueba("falla.gif", fotogramas=5)
        reproductor = video.Reproductor(panel, video.abrir_fuente(gif), fps=20)
        subidos = reproductor.reproducir(max_fotogramas=3)
        self.assertEqual(subidos, 0)
        self.assertEqual(reproductor.intentos, 3)
        self.assertEqual(reproductor.fallos, 3)
        self.assertEqual(panel.llamadas, 3)
        self.assertIn("el panel dijo que no", reproductor.ultimo_error)
        self.assertEqual(panel.nombres[0], (video.NOMBRE, "osd"))

    def test_una_excepcion_del_panel_no_rompe_el_bucle(self):
        panel = PanelQueFalla(excepcion=True)
        gif = gif_de_prueba("excepcion.gif", fotogramas=5)
        reproductor = video.Reproductor(panel, video.abrir_fuente(gif), fps=20)
        subidos = reproductor.reproducir(max_fotogramas=2)
        self.assertEqual(subidos, 0)
        self.assertEqual(reproductor.fallos, 2)
        self.assertIn("se ha caido", reproductor.ultimo_error)

    def test_salta_fotogramas_si_la_subida_tarda_mas_que_el_periodo(self):
        """Con `fps` por encima de lo que aguanta el panel se saltan fotogramas, sin colgarse.

        Era un fallo real: la condicion de "voy tarde" se medía contra un instante fijo, asi
        que con el material en bucle el bucle de saltos no terminaba nunca.
        """
        panel = PanelLento(0.05)                       # 50 ms por subida
        gif = gif_de_prueba("rapido.gif", fotogramas=10, duracion=20)
        reproductor = video.Reproductor(panel, video.abrir_fuente(gif), fps=50)  # periodo 20 ms
        inicio = time.time()
        subidos = reproductor.reproducir(max_fotogramas=3)
        transcurrido = time.time() - inicio
        self.assertEqual(subidos, 3)
        self.assertGreater(reproductor.saltados, 0, "deberia saltarse fotogramas")
        self.assertLess(transcurrido, 5.0, "no debe quedarse dando vueltas")

    def test_saltar_no_se_pasa_de_la_fuente_sin_bucle(self):
        """Al saltar con `bucle=False` se termina cuando se acaba el material, sin colgarse."""
        panel = PanelLento(0.05)
        carpeta = carpeta_de_prueba("saltar-fin", cuantos=4)
        reproductor = video.Reproductor(panel, video.abrir_fuente(carpeta), fps=100,
                                        bucle=False)
        inicio = time.time()
        subidos = reproductor.reproducir()
        transcurrido = time.time() - inicio
        self.assertLessEqual(subidos, 4)
        self.assertTrue(reproductor.fin)
        self.assertGreater(reproductor.saltados + subidos, 1)
        self.assertLess(transcurrido, 5.0)

    def test_cli_reproduce_contra_el_simulador(self):
        """El CLI completo, con --device /dev/pts/N (sin tocar el panel real)."""
        salida = _ruta("cli.png")
        sim = self.levantar(salida=salida)
        gif = gif_de_prueba("cli.gif", fotogramas=5)
        orden = [sys.executable, "-B", "-m", "cfv235", "--device", sim.nombre,
                 "video", gif, "--max", "4", "--fps", "8", "--sin-bucle"]
        proceso = subprocess.run(orden, cwd=RAIZ, capture_output=True, text=True, timeout=120)
        self.assertEqual(proceso.returncode, 0, proceso.stderr)
        self.assertIn("fotogramas subidos", proceso.stdout)
        self.assertEqual(sim.panel.subidas_ok, 4)
        self.assertEqual(sim.panel.medios["nombre"], video.NOMBRE)
        self.assertEqual(tamano_de(salida), (ANCHO, ALTO))

    def test_cli_ver_no_necesita_panel(self):
        """`cfv235 video ver RUTA` solo informa: sin panel y sin tocar el dispositivo."""
        gif = gif_de_prueba("cli-ver.gif", fotogramas=7, duracion=50)
        orden = [sys.executable, "-B", "-m", "cfv235", "video", "ver", gif]
        proceso = subprocess.run(orden, cwd=RAIZ, capture_output=True, text=True, timeout=60)
        self.assertEqual(proceso.returncode, 0, proceso.stderr)
        self.assertIn("tipo          : gif", proceso.stdout)
        self.assertIn("fotogramas    : 7", proceso.stdout)

    def test_cli_ver_fichero_inexistente(self):
        orden = [sys.executable, "-B", "-m", "cfv235", "video", "ver",
                 _ruta("no-existe-cli.gif")]
        proceso = subprocess.run(orden, cwd=RAIZ, capture_output=True, text=True, timeout=60)
        self.assertEqual(proceso.returncode, 2)
        self.assertIn("ERROR", proceso.stdout)


class TestFuentePantalla(unittest.TestCase):
    """La fuente de reflejo de pantalla, con capturador inyectado (sin portal)."""

    @staticmethod
    def _png_bytes(color=(30, 180, 120), ancho=ANCHO, alto=ALTO):
        if not HAY_PIL:
            raise unittest.SkipTest("hace falta Pillow")
        import io
        memoria = io.BytesIO()
        Image.new("RGB", (ancho, alto), color).save(memoria, format="PNG")
        return memoria.getvalue()

    def test_abrir_fuente_reconoce_pantalla(self):
        for clave in ("pantalla", "stream", "PANTALLA", "pantalla:"):
            fuente = video.abrir_fuente(clave)
            self.assertIsInstance(fuente, video.FuentePantalla, clave)
            fuente.cerrar()

    def test_entrega_fotogramas_ajustados_sin_acabarse(self):
        fuente = video.FuentePantalla(capturador=self._png_bytes, ajuste="estirar")
        try:
            for _ in range(3):                    # nunca se acaba: tres fotogramas seguidos
                cuadro = fuente.siguiente()
                self.assertIsNotNone(cuadro)
                imagen, duracion = cuadro
                self.assertEqual(imagen.size, (ANCHO, ALTO))
                self.assertGreater(duracion, 0)
        finally:
            fuente.cerrar()

    def test_el_error_del_capturador_se_convierte_en_error_video(self):
        def roto():
            raise RuntimeError("no hay portal")

        fuente = video.FuentePantalla(capturador=roto)
        try:
            with self.assertRaises(video.ErrorVideo):
                fuente.siguiente()
        finally:
            fuente.cerrar()


if __name__ == "__main__":
    unittest.main(verbosity=2)
