"""video.py - video y animacion en el panel CFV235 (1920x462).

AVISO IMPORTANTE (medido en el panel real)
------------------------------------------
El panel **solo muestra PNG**. Acepta un JPEG o un GIF como fichero, pero el GIF sale en
blanco; no hay ninguna forma de mandarle un fichero que el panel anime por su cuenta.
"Reproducir video" aqui significa, por tanto, **subir fotogramas PNG uno detras de otro**.

Cada fotograma se sube con `panel.subir_datos(png, NOMBRE, capa="osd")` y tarda **~160 ms**
con un PNG de 1920x462. El panel admite **una sola sesion** y su ventana de subida dura
**menos de un segundo**, asi que:

    el techo realista son 3-5 fotogramas por segundo.

Con un `fps` mas alto el reproductor no acelera (el panel no da mas de si): acumula retraso y
va **saltando fotogramas** de la fuente para no quedarse atras. El video se ve **a saltos** y
**sin sonido** (el panel no tiene altavoces). Por encima de ~3 fps solo se gastan CPU
y saltos.

La capa OSD **reutiliza su hueco** en la memoria del panel (~4 KB por fotograma) y el fondo
**acumula**, asi que aqui se usa la capa OSD por defecto y un **nombre de fichero fijo**
(`NOMBRE`): el panel reconoce el nombre y reescribe el mismo hueco, de modo que un video de
horas no llena su memoria.

FUENTES
-------
    GIF animado     Pillow + ImageSequence, respetando la duracion de cada fotograma
    secuencia      una carpeta con PNG/JPG (orden natural) o una lista de rutas
    video          mp4/mkv/webm/avi/mov con GStreamer (mp4, mkv, webm, avi, mov)

Los tres modos de ajuste a 1920x462 son `ajustar` (letterbox, por defecto), `recortar`
(recorte centrado) y `estirar`.

DEPENDENCIAS
------------
Biblioteca estandar + Pillow. GStreamer (PyGObject) **solo** hace falta para leer video y se
importa de forma perezosa: `import cfv235.video` funciona aunque GStreamer no este instalado;
el error claro aparece al abrir el video.

USO
---
    from cfv235 import Panel, video

    panel = Panel().abrir()
    panel.canal.autonegociar()
    fuente = video.abrir_fuente("/tmp/animacion.gif", ajuste="ajustar")
    reproductor = video.Reproductor(panel, fuente, fps=4, bucle=True)
    subidos = reproductor.reproducir(max_fotogramas=40)
    print(subidos, reproductor.fallos, reproductor.saltados)

    # sin panel, solo el informe:
    print(video.informe_legible(video.inspeccionar("/tmp/video.mp4")))
"""

from __future__ import annotations

import io
import os
import re
import threading
import time

from . import temas

# Tamano del panel: se toma de `temas` para no repetir el numero en dos sitios.
ANCHO, ALTO = temas.ANCHO, temas.ALTO

# Nombre FIJO del fichero en el panel. Es lo que hace que la capa OSD reutilice su hueco en
# vez de acumular copias (medido: ~4 KB por fotograma de 1920x462 en la capa OSD).
NOMBRE = "cfv235_video.png"

# Modos de ajuste de la imagen original a los 1920x462 del panel.
AJUSTES = ("ajustar", "recortar", "estirar")
AJUSTE_POR_DEFECTO = "ajustar"

# Ritmo. Medido: ~160 ms por subida de un PNG de 1920x462, o sea que por encima de 5 fps lo
# unico que se consigue es saltarse fotogramas.
# Medido en el panel real (docs/RENDIMIENTO.md): subir un fotograma del dashboard tarda
# entre ~100 y ~440 ms (mediana ~250) y el bucle sostiene **3,1 fps** como maximo; a 4 fps
# pedidos se consiguen 2,5. Por eso el valor por defecto es 2 fps (va sin saltos) y el techo
# declarado son 3.
FPS_POR_DEFECTO = 2.0
FPS_MAXIMO_REALISTA = 3.0
FPS_MINIMO = 0.1

# Duracion que se supone a un fotograma cuando la fuente no la dice.
DURACION_POR_DEFECTO_MS = 100.0
# Duracion que se supone a cada PNG/JPG de una secuencia de imagenes.
DURACION_SECUENCIA_MS = 1000.0 / FPS_POR_DEFECTO

EXTENSIONES_IMAGEN = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")
EXTENSIONES_VIDEO = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".mpg", ".mpeg",
                     ".wmv", ".flv", ".ts")

# Reintento de lectura del PNG: si Pillow no esta, el error se da al usarlo, no al importar.
_PIL_ERROR = ""
try:
    from PIL import Image, ImageOps
    _REESCALADO = getattr(Image, "Resampling", Image).LANCZOS
except Exception as _exc:                              # noqa: BLE001
    Image = ImageOps = None                            # type: ignore[assignment]
    _REESCALADO = None
    _PIL_ERROR = str(_exc)


class ErrorVideo(Exception):
    """Fallo legible al leer una fuente o al reproducirla (siempre con texto en espanol)."""


# ------------------------------------------------------------------ GStreamer (perezoso)
# Se importa SOLO cuando hace falta leer un video. `import cfv235.video` (y todo el informe
# de GIF y secuencias) funciona sin GStreamer instalado.
_GST = None                    # (Gst, GstApp o None, GstPbutils o None)
_GST_ERROR = ""                # motivo por el que no se pudo cargar (se recuerda)
_GST_CANDADO = threading.Lock()


def _cargar_gstreamer():
    """(Gst, GstApp|None, GstPbutils|None), importando PyGObject la primera vez.

    Lanza `ErrorVideo` con un texto claro si GStreamer no esta. El resultado (o el fallo) se
    recuerda para no repetir el `import` en cada fotograma.
    """
    global _GST, _GST_ERROR
    if _GST is not None:
        return _GST
    if _GST_ERROR:
        raise ErrorVideo(_GST_ERROR)
    with _GST_CANDADO:
        if _GST is not None:
            return _GST
        if _GST_ERROR:
            raise ErrorVideo(_GST_ERROR)
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except Exception as exc:                       # noqa: BLE001
            _GST_ERROR = ("para leer video hace falta GStreamer con PyGObject "
                          "(gi.require_version('Gst', '1.0')) y no se pudo cargar: "
                          f"{exc}. Convierte el video a GIF o a una secuencia de PNG, o "
                          "instala python3-gi + gstreamer1.0-plugins-good/bad/ugly.")
            raise ErrorVideo(_GST_ERROR) from exc

        # GstApp expone AppSink.try_pull_sample(); sin el, se usa emit("try-pull-sample").
        GstApp = None
        try:
            gi.require_version("GstApp", "1.0")
            from gi.repository import GstApp as _GstApp
            GstApp = _GstApp
        except Exception:                              # noqa: BLE001
            GstApp = None

        # GstPbutils descubre duracion y tamano sin decodificar el video entero.
        GstPbutils = None
        try:
            gi.require_version("GstPbutils", "1.0")
            from gi.repository import GstPbutils as _GstPbutils
            GstPbutils = _GstPbutils
        except Exception:                              # noqa: BLE001
            GstPbutils = None

        try:
            Gst.init(None)
        except Exception as exc:                       # noqa: BLE001
            _GST_ERROR = f"GStreamer esta instalado pero no se pudo inicializar: {exc}"
            raise ErrorVideo(_GST_ERROR) from exc

        _GST = (Gst, GstApp, GstPbutils)
        return _GST


def gstreamer_disponible() -> bool:
    """True si se puede leer video (GStreamer + PyGObject cargables)."""
    try:
        _cargar_gstreamer()
        return True
    except ErrorVideo:
        return False


def motivo_sin_gstreamer() -> str:
    """Explicacion del fallo de GStreamer, o "" si esta disponible."""
    try:
        _cargar_gstreamer()
        return ""
    except ErrorVideo as exc:
        return str(exc)


# ------------------------------------------------------------------ ayudas
def _exigir_pillow() -> None:
    if Image is None:
        raise ErrorVideo("hace falta Pillow para leer imagenes y video "
                         f"(no se pudo importar PIL: {_PIL_ERROR})")


def _clave_natural(ruta: str):
    """Clave de orden que numera como un humano: foto2.png antes que foto10.png."""
    texto = os.path.basename(ruta).lower()
    # `re.split` con grupo de captura deja los tramos alternos: texto, numero, texto...
    return [int(trozo) if trozo.isdigit() else trozo
            for trozo in re.split(r"(\d+)", texto)]


def _extension(ruta: str) -> str:
    return os.path.splitext(ruta)[1].lower()


def _es_video(ruta: str) -> bool:
    return _extension(ruta) in EXTENSIONES_VIDEO


def _es_imagen(ruta: str) -> bool:
    return _extension(ruta) in EXTENSIONES_IMAGEN


def _citar_gst(ruta: str) -> str:
    """Entrecomilla una ruta para el parser de `gst_parse_launch` (espacios, comillas...)."""
    return '"' + ruta.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ajustar_imagen(imagen, ajuste: str = AJUSTE_POR_DEFECTO, ancho: int = ANCHO,
                   alto: int = ALTO):
    """Lleva una imagen a 1920x462 (RGB) segun el modo de ajuste.

        ajustar    letterbox: se ve entera, con bandas negras donde sobra (por defecto)
        recortar   se llena el panel y se recorta lo que sobra, centrado
        estirar    se deforma para ocupar exactamente el panel
    """
    if ajuste not in AJUSTES:
        raise ErrorVideo(f"ajuste desconocido: {ajuste!r} (hay {', '.join(AJUSTES)})")
    if imagen is None:
        raise ErrorVideo("no hay imagen que ajustar")
    # `convert` ya devuelve una copia, asi que la fuente no se toca.
    copia = imagen if imagen.mode == "RGB" else imagen.convert("RGB")
    if copia.size == (ancho, alto):
        return copia.copy()
    if ajuste == "estirar":
        return copia.resize((ancho, alto), _REESCALADO)
    if ajuste == "recortar":
        return ImageOps.fit(copia, (ancho, alto), method=_REESCALADO, centering=(0.5, 0.5))
    # ajustar: se encaja entera y se centra sobre fondo negro (las bandas del letterbox)
    reducida = copia.copy()
    reducida.thumbnail((ancho, alto), _REESCALADO)
    fondo = Image.new("RGB", (ancho, alto), (0, 0, 0))
    fondo.paste(reducida, ((ancho - reducida.width) // 2, (alto - reducida.height) // 2))
    return fondo


# ------------------------------------------------------------------ fuentes
class Fuente:
    """Fuente de fotogramas: entrega imagenes RGB de 1920x462 y su duracion en ms.

    Las subclases implementan `_generador()` (fotogramas al tamano original) y, si hace
    falta, `_cerrar_fuente()`; el ajuste a 1920x462 lo hace esta clase, en un solo sitio.
    """

    tipo = "desconocido"

    def __init__(self, ajuste: str = AJUSTE_POR_DEFECTO):
        _exigir_pillow()
        if ajuste not in AJUSTES:
            raise ErrorVideo(f"ajuste desconocido: {ajuste!r} (hay {', '.join(AJUSTES)})")
        self.ajuste = ajuste
        self.fotogramas_leidos = 0
        self.tamano_original = None
        self.fotogramas_conocidos = None
        self._gen = None

    # ---------------------------------------------------------- API
    def siguiente(self):
        """(imagen RGB 1920x462, duracion_ms) del fotograma siguiente, o None al terminar."""
        if self._gen is None:
            self.reiniciar()                           # deja el generador listo
        try:
            crudo, duracion = next(self._gen)
        except StopIteration:
            return None
        imagen = ajustar_imagen(crudo, self.ajuste)
        if self.tamano_original is None:
            self.tamano_original = crudo.size
        self.fotogramas_leidos += 1
        return imagen, float(duracion or DURACION_POR_DEFECTO_MS)

    def reiniciar(self) -> None:
        """Vuelve al primer fotograma (cierra lo que hubiera abierto)."""
        self._cerrar_fuente()
        self._gen = self._generador()
        self.fotogramas_leidos = 0

    def cerrar(self) -> None:
        """Suelta el fichero, el pipeline... Todo lo que tenga abierto la fuente."""
        gen, self._gen = self._gen, None
        if gen is not None:
            try:
                gen.close()                            # dispara el `finally` del generador
            except Exception:                          # noqa: BLE001
                pass
        self._cerrar_fuente()

    def describe(self) -> str:
        """Frase corta para la interfaz y el CLI."""
        return f"{self.tipo} ({self.fotogramas_conocidos or '?'} fotogramas)"

    # ---------------------------------------------------------- a implementar
    def _generador(self):
        raise NotImplementedError

    def _cerrar_fuente(self) -> None:
        """Cierra recursos propios (ficheros, pipeline). Por defecto, nada."""

    # ---------------------------------------------------------- gestor de contexto
    def __enter__(self) -> "Fuente":
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    def __iter__(self):
        """Itera la fuente entera desde el principio (la reinicia)."""
        self.reiniciar()
        while True:
            cuadro = self.siguiente()
            if cuadro is None:
                return
            yield cuadro


class FuenteGIF(Fuente):
    """GIF animado con Pillow (`ImageSequence`), con la duracion de cada fotograma."""

    tipo = "gif"

    def __init__(self, ruta: str, ajuste: str = AJUSTE_POR_DEFECTO):
        super().__init__(ajuste)
        self.ruta = os.path.abspath(ruta)
        if not os.path.isfile(self.ruta):
            raise ErrorVideo(f"no existe el GIF: {ruta}")
        # Metadatos (baratos): tamano del primer fotograma y numero de fotogramas.
        try:
            with Image.open(self.ruta) as im:
                self.tamano_original = im.size
                self.fotogramas_conocidos = int(getattr(im, "n_frames", 1))
                self._duracion_media_ms = _duracion_media_gif(im)
        except Exception as exc:                       # noqa: BLE001
            raise ErrorVideo(f"no se pudo abrir el GIF {ruta}: {exc}") from exc

    def _generador(self):
        from PIL import ImageSequence
        if not os.path.isfile(self.ruta):
            raise ErrorVideo(f"el GIF ha desaparecido: {self.ruta}")
        # El generador abre y cierra el fichero el solo: si el reproductor se para a la
        # mitad, `cerrar()` cierra el generador y el `with` suelta el descriptor.
        try:
            with Image.open(self.ruta) as im:
                for cuadro in ImageSequence.Iterator(im):
                    duracion = cuadro.info.get("duration") or DURACION_POR_DEFECTO_MS
                    # Iterator reutiliza el mismo objeto `im` en cada vuelta: hay que copiar.
                    yield cuadro.copy(), float(duracion)
        except ErrorVideo:
            raise
        except Exception as exc:                       # noqa: BLE001
            raise ErrorVideo(f"error al leer el GIF {self.ruta}: {exc}") from exc

    def describe(self) -> str:
        return f"GIF {os.path.basename(self.ruta)} ({self.fotogramas_conocidos} fotogramas)"


class FuenteSecuencia(Fuente):
    """Secuencia de imagenes: una carpeta (orden natural) o una lista de rutas."""

    tipo = "secuencia"

    def __init__(self, rutas, ajuste: str = AJUSTE_POR_DEFECTO,
                 duracion_ms: float = DURACION_SECUENCIA_MS):
        super().__init__(ajuste)
        self.duracion_ms = float(duracion_ms)
        if isinstance(rutas, (str, os.PathLike)):
            rutas = [rutas] if os.path.isfile(os.fspath(rutas)) else \
                _rutas_de_carpeta(os.fspath(rutas))
        self.rutas = [os.path.abspath(os.fspath(r)) for r in rutas]
        if not self.rutas:
            raise ErrorVideo("la secuencia no tiene imagenes (¿carpeta vacia?)")
        faltan = [r for r in self.rutas if not os.path.isfile(r)]
        if faltan:
            raise ErrorVideo(f"no existe la imagen {faltan[0]}")
        self.fotogramas_conocidos = len(self.rutas)
        try:
            with Image.open(self.rutas[0]) as im:
                self.tamano_original = im.size
        except Exception as exc:                       # noqa: BLE001
            raise ErrorVideo(f"no se pudo leer {self.rutas[0]}: {exc}") from exc

    def _generador(self):
        for ruta in self.rutas:
            try:
                with Image.open(ruta) as im:
                    # convert("RGB") crea una imagen nueva: se puede cerrar el fichero.
                    yield im.convert("RGB"), self.duracion_ms
            except ErrorVideo:
                raise
            except Exception as exc:                   # noqa: BLE001
                raise ErrorVideo(f"no se pudo leer {ruta}: {exc}") from exc

    def describe(self) -> str:
        return (f"secuencia de {len(self.rutas)} imagenes "
                f"({os.path.basename(self.rutas[0])}...)")


class FuenteVideo(Fuente):
    """Video (mp4/mkv/webm/avi/mov) con GStreamer, sacando muestras de un `appsink`.

    El tubo es el pedido: `filesrc ! decodebin ! videoconvert ! videoscale ! appsink`, con
    el appsink en **modo pull** y con la cola **bloqueante** (`drop=false`): asi el
    decodificador solo avanza cuando el reproductor pide el fotograma siguiente. Medido con
    `drop=true` el tubo decodifica todo el video de golpe mientras se sube un fotograma y
    tira los demas; con `drop=false` no se pierde ninguno.
    """

    tipo = "video"

    def __init__(self, ruta: str, ajuste: str = AJUSTE_POR_DEFECTO, timeout_s: float = 5.0):
        super().__init__(ajuste)
        self.ruta = os.path.abspath(ruta)
        self.timeout_s = float(timeout_s)
        if not os.path.isfile(self.ruta):
            raise ErrorVideo(f"no existe el video: {ruta}")
        if os.path.getsize(self.ruta) == 0:
            raise ErrorVideo(f"el video esta vacio: {ruta}")
        # El tubo NO se abre aqui: eso pasa al pedir el primer fotograma, asi que construir la
        # fuente es barato. Solo se piden metadatos con GstPbutils, que es tolerante: si
        # GStreamer no esta (o el fichero no se deja reconocer), `_descubrir_video` devuelve
        # {} y el error claro aparece al leer el primer fotograma.
        datos = _descubrir_video(self.ruta)
        self.tamano_original = datos.get("tamano")
        self.fps_nativo = datos.get("fps")
        self.duracion_s = datos.get("duracion_s")
        self.avisos = list(datos.get("avisos", []))
        if self.duracion_s and self.fps_nativo:
            self.fotogramas_conocidos = max(1, int(round(self.duracion_s * self.fps_nativo)))
        self._tubo = None
        self._appsink = None
        self._Gst = None
        self._GstApp = None
        # True cuando el tubo ha llegado al final (EOS): el video se leyo entero.
        self.agotado = False

    # ---------------------------------------------------------- tubo de GStreamer
    def _abrir_tubo(self) -> None:
        Gst, GstApp, _ = _cargar_gstreamer()
        self._Gst, self._GstApp = Gst, GstApp
        if not os.path.isfile(self.ruta):
            raise ErrorVideo(f"el video ha desaparecido: {self.ruta}")
        # `max-buffers=2 drop=false sync=false`: cola corta y bloqueante, sin reloj. El ritmo
        # lo pone el reproductor (el panel sostiene ~3 fps), no el tubo.
        canal = (f"filesrc location={_citar_gst(self.ruta)} ! decodebin ! videoconvert ! "
                 f"videoscale ! appsink name=salida emit-signals=false sync=false "
                 f"max-buffers=2 drop=false caps=video/x-raw,format=RGB")
        try:
            tubo = Gst.parse_launch(canal)
        except Exception as exc:                       # noqa: BLE001
            raise ErrorVideo(f"no se pudo preparar el tubo de GStreamer para "
                             f"{os.path.basename(self.ruta)}: {exc}") from exc
        if tubo is None:
            raise ErrorVideo("GStreamer no pudo crear el tubo de lectura de video")
        self._tubo = tubo
        self._appsink = tubo.get_by_name("salida")
        if self._appsink is None:
            self._cerrar_tubo()
            raise ErrorVideo("el tubo de GStreamer no tiene appsink (¿falta "
                             "gstreamer1.0-plugins-base?)")
        try:
            tubo.set_state(Gst.State.PLAYING)
        except Exception as exc:                       # noqa: BLE001
            self._cerrar_tubo()
            raise ErrorVideo(f"no se pudo arrancar la lectura del video: {exc}") from exc

    def _cerrar_tubo(self) -> None:
        tubo, self._tubo = self._tubo, None
        self._appsink = None
        if tubo is not None:
            try:
                tubo.set_state(self._Gst.State.NULL)
            except Exception:                          # noqa: BLE001
                pass

    def _sacar_muestra(self, segundos: float):
        """Un `Gst.Sample` o None si no llego nada en `segundos`."""
        if self._appsink is None:
            return None
        margen = max(0, int(segundos * self._Gst.SECOND))
        if self._GstApp is not None:
            return self._appsink.try_pull_sample(margen)
        # Sin el typelib GstApp, la senal hace el mismo trabajo.
        return self._appsink.emit("try-pull-sample", margen)

    def _error_del_bus(self) -> str:
        """Primer error que haya publicado el tubo, ya en espanol."""
        if self._tubo is None:
            return ""
        Gst = self._Gst
        bus = self._tubo.get_bus()
        if bus is None:
            return ""
        try:
            mensaje = bus.pop_filtered(Gst.MessageType.ERROR)
        except Exception:                              # noqa: BLE001
            return ""
        if mensaje is None:
            return ""
        error, depuracion = mensaje.parse_error()
        detalle = str(error.message) if error is not None else "error desconocido"
        if depuracion:
            detalle += f" [{depuracion}]"
        return (f"GStreamer no pudo leer {os.path.basename(self.ruta)}: {detalle}. "
                f"¿esta el fichero completo? ¿hay decodificador para ese formato "
                f"(gstreamer1.0-libav / plugins-bad)?")

    def _imagen_de_muestra(self, muestra):
        """Convierte un `Gst.Sample` (video/x-raw RGB) en una imagen de Pillow."""
        Gst = self._Gst
        caps = muestra.get_caps()
        if caps is None or caps.get_size() == 0:
            raise ErrorVideo("el video no trae formato de imagen (¿solo audio?)")
        estructura = caps.get_structure(0)
        ancho = estructura.get_int("width")[1]
        alto = estructura.get_int("height")[1]
        if not ancho or not alto:
            raise ErrorVideo("el video no dice su tamano de fotograma")
        buffer = muestra.get_buffer()
        if buffer is None:
            raise ErrorVideo("muestra de video sin datos")
        crudo = buffer.extract_dup(0, buffer.get_size())
        if isinstance(crudo, tuple):                   # segun la version de PyGObject
            crudo = crudo[0]
        crudo = bytes(crudo)
        esperado = ancho * alto * 3
        if len(crudo) == esperado:
            return Image.frombytes("RGB", (ancho, alto), crudo)
        # Algunos decodificadores rellenan cada fila hasta un multiplo: se quita el relleno.
        if len(crudo) > esperado and len(crudo) % alto == 0:
            paso = len(crudo) // alto
            filas = [crudo[y * paso:y * paso + ancho * 3] for y in range(alto)]
            return Image.frombytes("RGB", (ancho, alto), b"".join(filas))
        raise ErrorVideo(f"fotograma de tamano inesperado: {len(crudo)} B para "
                         f"{ancho}x{alto} RGB ({esperado} B)")

    def _generador(self):
        self.agotado = False
        self._abrir_tubo()
        try:
            while True:
                muestra = self._sacar_muestra(self.timeout_s)
                if muestra is None:
                    error = self._error_del_bus()
                    if error:
                        raise ErrorVideo(error)
                    if self._appsink is not None and self._appsink.is_eos():
                        self.agotado = True
                        return
                    # Ni datos ni EOS ni error: el tubo no arranca (formato raro, fichero
                    # cortado a la mitad...). Mejor decirlo que quedarse colgado.
                    raise ErrorVideo(
                        f"el video {os.path.basename(self.ruta)} no entrega fotogramas "
                        f"(se espero {self.timeout_s:.0f} s). ¿esta completo? ¿hay "
                        f"decodificador para su formato?")
                # Duracion del fotograma: la de los datos manda; si no la trae, la del
                # framerate de las caps.
                yield self._imagen_de_muestra(muestra), self._duracion_de(muestra)
        finally:
            # El tubo se cierra al agotar el video, al fallar o al cerrar el generador desde
            # fuera (parada del reproductor). Para volver a empezar se monta otro: montarlo
            # cuesta ~20-50 ms, menos que el fotograma que se sube al panel.
            self._cerrar_tubo()

    def _duracion_de(self, muestra) -> float:
        buffer = muestra.get_buffer()
        Gst = self._Gst
        if buffer is not None and buffer.duration not in (0, Gst.CLOCK_TIME_NONE):
            return buffer.duration / Gst.MSECOND
        if self.fps_nativo:
            return 1000.0 / self.fps_nativo
        caps = muestra.get_caps().get_structure(0)
        if caps.has_field("framerate"):
            ok, num, den = caps.get_fraction("framerate")
            if ok and num and den:
                self.fps_nativo = num / float(den)
                return 1000.0 / self.fps_nativo
        return DURACION_POR_DEFECTO_MS

    # ---------------------------------------------------------- reinicio
    def _cerrar_fuente(self) -> None:
        self._cerrar_tubo()

    def describe(self) -> str:
        fps = f"{self.fps_nativo:.1f} fps" if self.fps_nativo else "fps ?"
        return f"video {os.path.basename(self.ruta)} ({fps})"


# ------------------------------------------------------------------ descubrir fuentes
def _rutas_de_carpeta(carpeta: str) -> list:
    """PNG/JPG de una carpeta, en orden natural (foto2 antes que foto10)."""
    if not os.path.isdir(carpeta):
        raise ErrorVideo(f"no existe la carpeta: {carpeta}")
    rutas = []
    for nombre in os.listdir(carpeta):
        if _extension(nombre) in EXTENSIONES_IMAGEN:
            ruta = os.path.join(carpeta, nombre)
            if os.path.isfile(ruta):
                rutas.append(ruta)
    rutas.sort(key=_clave_natural)
    return rutas


def _duracion_media_gif(im, maximo: int = 30) -> float:
    """Duracion media de los primeros fotogramas de un GIF ya abierto, en ms."""
    cuantos = min(int(getattr(im, "n_frames", 1)), maximo)
    total = 0.0
    for indice in range(cuantos):
        try:
            im.seek(indice)
        except Exception:                              # noqa: BLE001
            break
        total += float(im.info.get("duration") or DURACION_POR_DEFECTO_MS)
    return total / cuantos if cuantos else DURACION_POR_DEFECTO_MS


def _descubrir_video(ruta: str) -> dict:
    """Metadatos de un video con GstPbutils (sin decodificarlo). Tolerante: {} si no puede."""
    datos: dict = {"tamano": None, "fps": None, "duracion_s": None, "avisos": []}
    try:
        Gst, _, GstPbutils = _cargar_gstreamer()
    except ErrorVideo as exc:
        datos["avisos"].append(str(exc))
        return datos
    if GstPbutils is None:
        datos["avisos"].append("falta GstPbutils: no se pudo leer la duracion ni el tamano "
                               "sin decodificar el video")
        return datos
    try:
        descubridor = GstPbutils.Discoverer.new(5 * Gst.SECOND)
        uri = "file://" + ruta
        info = descubridor.discover_uri(uri)
    except Exception as exc:                           # noqa: BLE001
        datos["avisos"].append(f"no se pudo inspeccionar el video: {exc}")
        return datos
    if info is None:
        datos["avisos"].append("GStreamer no reconocio el fichero como video")
        return datos
    duracion = info.get_duration()
    if duracion not in (0, Gst.CLOCK_TIME_NONE):
        datos["duracion_s"] = duracion / Gst.SECOND
    videos = info.get_video_streams()
    if videos:
        primero = videos[0]
        ancho, alto = primero.get_width(), primero.get_height()
        if ancho and alto:
            datos["tamano"] = (ancho, alto)
        num, den = primero.get_framerate_num(), primero.get_framerate_denom()
        if num and den:
            datos["fps"] = num / float(den)
    else:
        datos["avisos"].append("el fichero no tiene pista de video")
    return datos


def abrir_fuente(fuente, ajuste: str = AJUSTE_POR_DEFECTO):
    """Abre una fuente a partir de una ruta, una carpeta, una lista de rutas o un objeto.

    Es el punto de entrada comodo: decide si es GIF, secuencia o video mirando la ruta, y
    devuelve la `Fuente` correspondiente ya ajustada al pedir el primer fotograma.
    """
    if isinstance(fuente, Fuente):
        return fuente
    if isinstance(fuente, (list, tuple)):
        return FuenteSecuencia(list(fuente), ajuste=ajuste)
    if isinstance(fuente, os.PathLike):
        fuente = os.fspath(fuente)
    if not isinstance(fuente, str):
        raise ErrorVideo(f"no se entiende la fuente: {fuente!r} (ruta, carpeta o lista)")

    ruta = os.path.abspath(os.path.expanduser(fuente))
    if os.path.isdir(ruta):
        return FuenteSecuencia(_rutas_de_carpeta(ruta), ajuste=ajuste)
    if not os.path.exists(ruta):
        raise ErrorVideo(f"no existe la fuente: {fuente}")
    if _es_video(ruta):
        return FuenteVideo(ruta, ajuste=ajuste)
    if _extension(ruta) == ".gif":
        return FuenteGIF(ruta, ajuste=ajuste)
    if _es_imagen(ruta):
        return FuenteSecuencia([ruta], ajuste=ajuste)
    # Extension desconocida: se prueba con Pillow (un GIF sin extension, por ejemplo).
    _exigir_pillow()
    try:
        with Image.open(ruta) as im:
            if getattr(im, "n_frames", 1) > 1 or (im.format or "").upper() == "GIF":
                return FuenteGIF(ruta, ajuste=ajuste)
            return FuenteSecuencia([ruta], ajuste=ajuste)
    except ErrorVideo:
        raise
    except Exception as exc:                           # noqa: BLE001
        raise ErrorVideo(f"no se sabe que hacer con {fuente!r}: no es una imagen ni un "
                         f"formato de video conocido ({exc})") from exc


def inspeccionar(ruta) -> dict:
    """Informe de una fuente, **sin abrir el panel** y sin decodificarla entera.

    Devuelve un diccionario con:

        tipo                "gif" | "secuencia" | "video" | "desconocido"
        fotogramas          numero de fotogramas, si se sabe (None si no)
        fotogramas_estimados True si `fotogramas` es una estimacion (video por duracion)
        fps                 fotogramas por segundo sugeridos (ya limitados a lo realista)
        tamano              (ancho, alto) original, si se sabe
        necesita_gstreamer  True si para leerla hace falta GStreamer
        duracion_s          duracion en segundos, si se sabe
        archivos            cuantas imagenes tiene una secuencia
        detalle             frase legible de una linea
        error               "" si todo bien; si no, el motivo

    Nunca lanza: un fichero ilegible sale con `tipo` "desconocido" y el `error` puesto.
    """
    informe = {"ruta": str(ruta), "tipo": "desconocido", "fotogramas": None,
               "fotogramas_estimados": False, "fps": FPS_POR_DEFECTO, "tamano": None,
               "necesita_gstreamer": False, "duracion_s": None, "archivos": None,
               "detalle": "", "error": ""}
    try:
        if isinstance(ruta, os.PathLike):
            ruta = os.fspath(ruta)
        if not isinstance(ruta, str):
            raise ErrorVideo(f"ruta no valida: {ruta!r}")
        camino = os.path.abspath(os.path.expanduser(ruta))
        informe["ruta"] = camino
        if not os.path.exists(camino):
            raise ErrorVideo(f"no existe: {ruta}")
        if os.path.isdir(camino):
            _informe_secuencia(informe, _rutas_de_carpeta(camino))
        elif _es_video(camino):
            _informe_video(informe, camino)
        elif _extension(camino) == ".gif":
            _informe_gif(informe, camino)
        elif _es_imagen(camino):
            _informe_secuencia(informe, [camino])
        else:
            _exigir_pillow()
            try:
                with Image.open(camino) as im:
                    if getattr(im, "n_frames", 1) > 1 or (im.format or "").upper() == "GIF":
                        _informe_gif(informe, camino)
                    else:
                        _informe_secuencia(informe, [camino])
            except ErrorVideo:
                raise
            except Exception as exc:                   # noqa: BLE001
                raise ErrorVideo(f"formato no reconocido ({exc})") from exc
    except ErrorVideo as exc:
        informe["error"] = str(exc)
    except Exception as exc:                           # noqa: BLE001
        informe["error"] = f"fallo al inspeccionar: {exc}"
    if not informe["detalle"]:
        informe["detalle"] = informe["error"] or "sin informacion"
    return informe


def _limitar_fps(fps) -> float:
    """Fps sugeridos dentro de lo que el panel aguanta (nunca mas de 5)."""
    try:
        valor = float(fps)
    except (TypeError, ValueError):
        return FPS_POR_DEFECTO
    if valor <= 0:
        return FPS_POR_DEFECTO
    return max(FPS_MINIMO, min(FPS_MAXIMO_REALISTA, valor))


def _informe_gif(informe: dict, ruta: str) -> None:
    _exigir_pillow()
    try:
        with Image.open(ruta) as im:
            informe["tipo"] = "gif"
            informe["fotogramas"] = int(getattr(im, "n_frames", 1))
            informe["tamano"] = im.size
            media = _duracion_media_gif(im)
    except Exception as exc:                           # noqa: BLE001
        raise ErrorVideo(f"no se pudo abrir el GIF: {exc}") from exc
    informe["fps"] = _limitar_fps(1000.0 / media if media else FPS_POR_DEFECTO)
    informe["duracion_s"] = informe["fotogramas"] * media / 1000.0
    informe["detalle"] = (f"GIF animado de {informe['fotogramas']} fotogramas "
                          f"{informe['tamano'][0]}x{informe['tamano'][1]}, "
                          f"{media:.0f} ms por fotograma "
                          f"(~{informe['duracion_s']:.1f} s por vuelta)")


def _informe_secuencia(informe: dict, rutas: list) -> None:
    if not rutas:
        raise ErrorVideo("no hay imagenes en la secuencia (carpeta vacia o sin PNG/JPG)")
    _exigir_pillow()
    try:
        with Image.open(rutas[0]) as im:
            tamano = im.size
    except Exception as exc:                           # noqa: BLE001
        raise ErrorVideo(f"no se pudo leer {rutas[0]}: {exc}") from exc
    informe["tipo"] = "secuencia"
    informe["fotogramas"] = len(rutas)
    informe["archivos"] = len(rutas)
    informe["tamano"] = tamano
    informe["fps"] = _limitar_fps(1000.0 / DURACION_SECUENCIA_MS)
    informe["duracion_s"] = len(rutas) * DURACION_SECUENCIA_MS / 1000.0
    informe["detalle"] = (f"secuencia de {len(rutas)} imagenes "
                          f"{tamano[0]}x{tamano[1]} (la primera: "
                          f"{os.path.basename(rutas[0])})")


def _informe_video(informe: dict, ruta: str) -> None:
    informe["tipo"] = "video"
    informe["necesita_gstreamer"] = True
    datos = _descubrir_video(ruta)
    informe["fps"] = _limitar_fps(datos.get("fps") or FPS_POR_DEFECTO)
    informe["tamano"] = datos.get("tamano")
    informe["duracion_s"] = datos.get("duracion_s")
    if datos.get("duracion_s") and datos.get("fps"):
        # GstPbutils no da el numero exacto de fotogramas de un mp4 sin decodificarlo.
        informe["fotogramas"] = max(1, int(round(datos["duracion_s"] * datos["fps"])))
        informe["fotogramas_estimados"] = True
    partes = [f"video {os.path.basename(ruta)}"]
    if informe["tamano"]:
        partes.append(f"{informe['tamano'][0]}x{informe['tamano'][1]}")
    if datos.get("fps"):
        partes.append(f"{datos['fps']:.2f} fps de origen")
    if informe["duracion_s"]:
        partes.append(f"{informe['duracion_s']:.2f} s")
    if informe["fotogramas"]:
        partes.append(f"~{informe['fotogramas']} fotogramas (estimado)")
    if not gstreamer_disponible():
        partes.append("FALTA GStreamer para leerlo")
        informe["error"] = motivo_sin_gstreamer()
    for aviso in datos.get("avisos", []):
        partes.append(f"aviso: {aviso}")
        if not informe["error"]:
            informe["error"] = aviso
    informe["detalle"] = ", ".join(partes)


def informe_legible(informe: dict) -> str:
    """El informe de `inspeccionar` en varias lineas de texto (para el CLI y los logs)."""
    lineas = [f"fuente        : {informe.get('ruta')}",
              f"tipo          : {informe.get('tipo')}"]
    if informe.get("fotogramas") is not None:
        estimado = " (estimado)" if informe.get("fotogramas_estimados") else ""
        lineas.append(f"fotogramas    : {informe['fotogramas']}{estimado}")
    if informe.get("tamano"):
        lineas.append(f"tamano        : {informe['tamano'][0]}x{informe['tamano'][1]}")
    lineas.append(f"fps sugeridos : {informe.get('fps'):.1f} "
                  f"(el panel sostiene ~3 fps)")
    if informe.get("duracion_s"):
        lineas.append(f"duracion      : {informe['duracion_s']:.2f} s")
    lineas.append(f"GStreamer     : "
                  f"{'SI hace falta' if informe.get('necesita_gstreamer') else 'no hace falta'}")
    lineas.append(f"detalle       : {informe.get('detalle')}")
    if informe.get("error"):
        lineas.append(f"ERROR         : {informe['error']}")
    return "\n".join(lineas)


# ------------------------------------------------------------------ el reproductor
class Reproductor:
    """Sube los fotogramas de una fuente al panel, uno detras de otro.

    El nombre de fichero en el panel es **fijo** (`NOMBRE`) y la capa es la OSD por
    defecto: asi el panel reescribe siempre el mismo hueco (~4 KB) y no acumula memoria,
    que es la diferencia entre un video de un minuto y un panel atascado.

    El PNG se compone en memoria (`io.BytesIO`): no se escribe nada en disco.
    """

    def __init__(self, panel, fuente, fps: float = FPS_POR_DEFECTO, bucle: bool = True,
                 capa: str = "osd", verboso: bool = False):
        self.panel = panel
        self.fuente = abrir_fuente(fuente)
        try:
            self.fps = max(FPS_MINIMO, float(fps))
        except (TypeError, ValueError) as exc:
            raise ErrorVideo(f"fps no valido: {fps!r}") from exc
        self.bucle = bool(bucle)
        self.capa = capa
        self.verboso = bool(verboso)
        # Contadores y estado (lo que se cuenta en el CLI y en los avisos).
        self.fotogramas = 0          # subidos con exito
        self.intentos = 0            # fotogramas que se intentaron subir
        self.fallos = 0              # subidas rechazadas o con excepcion
        self.saltados = 0            # fotogramas tirados para ponerse al dia
        self.ultimo_error = ""
        self.ultimo_png = b""
        self.ultimo_tamano = 0
        self.duracion_ms = 0.0       # duracion del ultimo fotograma de la fuente
        self.fin = False             # True: la fuente se acabo (o se rompio)
        self.fin_fuente = False      # True si el final fue normal (no un error)

    # ---------------------------------------------------------- un fotograma
    def fotograma(self) -> bool:
        """Prepara y sube UN fotograma. False si no se pudo (el motivo, en `ultimo_error`)."""
        self.fin = False
        self.fin_fuente = False
        try:
            cuadro = self._siguiente_cuadro()
        except ErrorVideo as exc:
            # La fuente se ha roto a mitad: se para el bucle, pero sin volcar la excepcion.
            self.ultimo_error = str(exc)
            self.fallos += 1
            self.fin = True
            return False
        if cuadro is None:
            # Fin normal del material: no es un fotograma fallido.
            self.fin = True
            self.fin_fuente = True
            if not self.fotogramas:
                self.ultimo_error = "la fuente no tiene fotogramas"
            return False
        imagen, duracion = cuadro
        self.duracion_ms = duracion
        self.intentos += 1
        datos = self._png_de(imagen)
        if datos is None:
            self.fallos += 1
            return False
        try:
            resultado = self.panel.subir_datos(datos, NOMBRE, capa=self.capa)
        except Exception as exc:                       # noqa: BLE001
            # Un fallo de canal no debe matar el bucle: se cuenta y se sigue probando.
            self.ultimo_error = f"al subir el fotograma {self.intentos}: {exc}"
            self.fallos += 1
            return False
        if not resultado.ok:
            self.ultimo_error = resultado.motivo or "el panel rechazo el fotograma"
            self.fallos += 1
            return False
        self.fotogramas += 1
        self.ultimo_png = datos
        self.ultimo_tamano = len(datos)
        self.ultimo_error = ""
        return True

    def _siguiente_cuadro(self):
        """Fotograma siguiente; al terminar, rebobina si el bucle esta activo."""
        cuadro = self.fuente.siguiente()
        if cuadro is not None:
            return cuadro
        if not self.bucle:
            return None
        self.fuente.reiniciar()
        return self.fuente.siguiente()

    def _png_de(self, imagen):
        """PNG en memoria. `compress_level=1`: comprimir mas no aporta y cuesta milisegundos
        que, con 160 ms de subida por fotograma, se notan."""
        memoria = io.BytesIO()
        try:
            imagen.save(memoria, format="PNG", compress_level=1, optimize=False)
        except Exception as exc:                       # noqa: BLE001
            self.ultimo_error = f"al codificar el PNG: {exc}"
            return None
        return memoria.getvalue()

    # ---------------------------------------------------------- el bucle
    def periodo(self, duracion_ms=None) -> float:
        """Segundos entre fotogramas: manda el mas lento entre `fps` y la fuente.

        `fps` es un **techo de velocidad** (el panel no da mas); si la fuente trae
        duraciones propias (un GIF de 1 s por fotograma), se respeta la mas lenta.
        """
        segundos = 1.0 / self.fps
        if duracion_ms:
            segundos = max(segundos, float(duracion_ms) / 1000.0)
        return segundos

    def reproducir(self, max_fotogramas: int = 0, parar=None, avisar=None) -> int:
        """Bucle de fotogramas hasta el fin de la fuente, `max_fotogramas` o `parar`.

            max_fotogramas  cuantos fotogramas se INTENTAN subir (0 = sin limite)
            parar           threading.Event para cortarlo desde otro hilo
            avisar          funcion(n, ok, error, ms) tras cada fotograma (el fin del
                            material no se avisa como fallo)

        Devuelve los fotogramas subidos con exito. Si la subida tarda mas que el periodo, se
        tiran fotogramas de la fuente (`saltados`) para no ir acumulando retraso: con ~160 ms
        por subida no se puede ir a mas de ~3 fps, y el video se ve a saltos.
        """
        intentos = 0
        while not (parar is not None and parar.is_set()):
            if max_fotogramas and intentos >= max_fotogramas:
                break
            intentos += 1
            inicio = time.time()
            ok = self.fotograma()
            ms = (time.time() - inicio) * 1000.0
            if self.fin and not ok:
                # Se acabo el material (o la fuente se rompio): no es un fotograma fallido,
                # asi que no se cuenta como tal. Solo se avisa si fue un error de verdad.
                if avisar and self.ultimo_error and not self.fin_fuente:
                    avisar(intentos, False, self.ultimo_error, ms)
                break
            if avisar:
                avisar(intentos, ok, self.ultimo_error, ms)
            periodo = self.periodo(self.duracion_ms)
            objetivo = inicio + periodo
            # Si la subida se ha pasado del presupuesto, se descartan fotogramas de la fuente
            # (uno por cada periodo de retraso) para no ir acumulando: nunca se sube "de
            # golpe", porque el panel no puede. Con `fps` alto esto es lo unico que se puede
            # hacer: el material se salta.
            retraso = time.time() - objetivo
            if retraso > 0:
                saltos = int(retraso // periodo)
                for _ in range(saltos):
                    if not self._saltar_uno():
                        break
                objetivo += saltos * periodo
            if self.fin:
                break
            # Espera hasta el proximo fotograma, mirando `parar` cada poco.
            while not (parar is not None and parar.is_set()):
                falta = objetivo - time.time()
                if falta <= 0:
                    break
                time.sleep(min(0.05, falta))
        return self.fotogramas

    def _saltar_uno(self) -> bool:
        """Descarta un fotograma de la fuente. False si ya no queda ninguno."""
        try:
            cuadro = self._siguiente_cuadro()
        except ErrorVideo as exc:
            self.ultimo_error = str(exc)
            self.fin = True
            return False
        if cuadro is None:
            self.fin = True
            self.fin_fuente = True
            return False
        self.saltados += 1
        return True

    def cerrar(self) -> None:
        self.fuente.cerrar()

    def __enter__(self) -> "Reproductor":
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    def resumen(self) -> str:
        partes = [f"{self.fotogramas} fotogramas subidos"]
        if self.fallos:
            partes.append(f"{self.fallos} fallos")
        if self.saltados:
            partes.append(f"{self.saltados} saltados")
        if self.ultimo_error:
            partes.append(f"ultimo aviso: {self.ultimo_error}")
        return ", ".join(partes)


__all__ = [
    "ANCHO", "ALTO", "NOMBRE", "AJUSTES", "AJUSTE_POR_DEFECTO", "FPS_POR_DEFECTO",
    "FPS_MAXIMO_REALISTA", "ErrorVideo", "Fuente", "FuenteGIF", "FuenteSecuencia",
    "FuenteVideo", "Reproductor", "abrir_fuente", "inspeccionar", "informe_legible",
    "ajustar_imagen", "gstreamer_disponible", "motivo_sin_gstreamer",
]
