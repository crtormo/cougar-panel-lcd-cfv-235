"""Ventana principal de la aplicacion cfv235-gtk y sus seis paginas.

Las paginas son: Estado, Imagen, Temas, Dashboard, Video y Diagnostico.

Reglas de la casa:

* Ninguna operacion con el panel se hace en el hilo de la interfaz: todo va en un
  `threading.Thread` y el resultado se aplica con `GLib.idle_add`.
* El panel es un HID: dos peticiones a la vez se pisarian. Por eso hay un unico
  `PanelCompartido` y todo acceso pasa por su cerrojo (`sesion()`).
* El panel se abre bajo demanda. Si no hay panel, la ventana arranca igual y lo cuenta
  en la pagina Estado, sin traceback.
* `temas`, `widgets`, `dashboard` y `video` se importan de forma perezosa (dentro de las
  funciones que los usan) para que la aplicacion arranque aunque falte algun modulo.
* Las confirmaciones y los errores salen como `Adw.Toast` (`avisar()` / `avisar_error()`),
  ademas del texto de estado de cada fila.
* Las acciones que no se pueden usar ahora mismo se desactivan Y dicen el motivo en su
  subtitulo, no solo se apagan.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from cfv235 import canal, config  # noqa: E402
from cfv235 import protocolo as protocolo  # noqa: E402
from cfv235.canal import ErrorCanal, PanelOcupado, SinPanel, SinPermisos  # noqa: E402
from cfv235.panel import Panel  # noqa: E402

# El dashboard tambien puede correr como servicio de systemd **de usuario**, y el panel
# admite una sola sesion: sin esta capa habria que ir a la terminal a pararlo cada vez que
# se quiere usar la ventana. Si el nucleo todavia no trae `cfv235.servicio`, la aplicacion
# arranca igual y las paginas lo cuentan en vez de morir con ImportError.
try:
    from cfv235 import servicio  # noqa: E402
except ImportError:                               # pragma: no cover
    servicio = None

from . import RAIZ_APP, cargar_dashboard, cargar_sensores, cargar_temas, carpetas_de_ejemplos
from . import estilo
from . import paginas_extra

# --------------------------------------------------------------------------- constantes

RUTA_SONDEAR = os.path.join(RAIZ_APP, "herramientas", "sondear_canal.py")

# Variables de entorno utiles para pruebas y soporte.
VAR_DISPOSITIVO = "CFV235_PANEL"
VAR_DISPOSITIVO_FALSO = "CFV235_SIMULADOR"

# Con CFV235_SIN_BLOQUEO=1 la app no toma el bloqueo exclusivo del panel: sirve para
# mirarlo (pagina Estado / Diagnostico) mientras el dashboard lo esta usando. Escribir
# sin el bloqueo puede cruzar respuestas, asi que solo es para diagnostico.
VAR_SIN_BLOQUEO = "CFV235_SIN_BLOQUEO"

# Orden preferido de las propiedades de `conn` en la tabla de Estado.
ORDEN_PROPIEDADES = [
    "bootFinish", "space", "brightness", "degree", "osdState", "background",
    "displayInSleep", "version", "sn", "OS", "mode", "logo", "timeout",
    "presetThemeId", "sleepClockId",
]

ETIQUETAS_PROPIEDADES = {
    "bootFinish": "bootFinish (panel arrancado)",
    "space": "space (espacio libre)",
    "brightness": "brightness (brillo)",
    "degree": "degree (rotacion)",
    "osdState": "osdState (capa OSD encima)",
    "background": "background (ultimo medio adoptado)",
    "displayInSleep": "displayInSleep (mostrar en reposo)",
    "version": "version (app / firmware / sdk / hardware)",
    "sn": "sn (numero de serie)",
    "OS": "OS (sistema que reporta el panel)",
    "mode": "mode (0-3, efecto sin determinar)",
    "logo": "logo (pantalla de arranque)",
    "timeout": "timeout (apagado por inactividad)",
    "presetThemeId": "presetThemeId (tema predefinido, no se controla por protocolo)",
    "sleepClockId": "sleepClockId (reloj de reposo, no se controla por protocolo)",
}

UNIDADES_PROPIEDADES = {
    "space": " KB",
    "brightness": " %",
    "degree": " grados",
    "timeout": " s",
}

# Sensores que se ensenan en la pagina Dashboard: (clave, etiqueta, unidad).
SENSORES_VISIBLES = [
    ("cpu_uso", "Uso de CPU", " %"),
    ("cpu_temp", "Temperatura de CPU", " C"),
    ("cpu_mhz", "Frecuencia de CPU", " MHz"),
    ("ram_uso", "Uso de RAM", " %"),
    ("ram_usado_gb", "RAM usada", " GB"),
    ("ram_total_gb", "RAM total", " GB"),
    ("gpu_uso", "Uso de GPU", " %"),
    ("gpu_temp", "Temperatura de GPU", " C"),
    ("disco_uso", "Uso de disco", " %"),
    ("red_bajada_mb", "Red (bajada)", " MB/s"),
    ("red_subida_mb", "Red (subida)", " MB/s"),
    ("uptime_h", "Encendido", " h"),
]

PERIODOS_DASHBOARD = [1, 2, 5]

# Perfiles del dashboard, en el orden en que se ensenan: (clave, etiqueta, descripcion).
# Las claves son las de `dashboard.PERFILES`; si el nucleo trae otras, el combo se
# completa con las que falten y se ignora una clave que ya no exista.
PERFILES_DASHBOARD = [
    ("completo", "Completo", "Todos los bloques del dashboard."),
    ("esencial", "Esencial", "Sin grafica, red, sistema, ventiladores ni temperaturas."),
    ("graficas", "Graficas", "Grafica de CPU y red; sin disco ni sensores opcionales."),
    ("minimo", "Minimo", "Solo el titulo, el reloj y la tarjeta de CPU."),
    ("presentacion", "Presentacion", "Reloj y titulo grandes, sin metricas."),
]

# Modos de ajuste de la imagen al panel: (clave, etiqueta) en el orden del ComboRow.
AJUSTES_VIDEO = [
    ("ajustar", "Ajustar (letterbox)"),
    ("recortar", "Recortar (centrado)"),
    ("estirar", "Estirar"),
]

# Limites del control de fps de la pagina Video.
FPS_MINIMO_VIDEO = 1
FPS_MAXIMO_VIDEO = 5
FPS_INICIAL_VIDEO = 4

# Aviso del ritmo real: cada subida de un PNG de 1920x462 tarda unos 160 ms.
AVISO_FPS_VIDEO = ("El panel refresca a 60 Hz, pero el envio por USB sostiene ~3 fps: cada "
                   "fotograma tarda entre 100 y 440 ms en subirse. Con mas fps solo se salta "
                   "fotogramas de la fuente. La reproduccion a 60 Hz solo la daria un modo "
                   "interno del panel (ver docs/PENDIENTE_WINDOWS.md).")

# Previsualizaciones: ANTES se escribian PNG con nombre fijo en /tmp (y dos renders
# concurrentes se pisaban el fichero). Ahora viajan como bytes en memoria
# (`temas.renderizar_datos` + Gdk.Texture), asi que no hay ningun temporal que compartir.

# Nombre de la unidad, para poder ensenarlo aunque falte `cfv235.servicio`.
NOMBRE_SERVICIO = getattr(servicio, "NOMBRE", "cfv235-dashboard.service")

# Lineas de journal que se leen al abrir "Ver registro" y al pintar la pagina Diagnostico.
LINEAS_REGISTRO_ESTADO = 20
LINEAS_REGISTRO_DIALOGO = 30

# Grados que admite el combo de rotacion de la pagina Estado, en su orden.
GRADOS_ROTACION = [0, 90, 180, 270]

# Paginas en el orden de Ctrl+1..Ctrl+6 (el mismo en el que se anaden a la pila).
PAGINAS_ATAJOS = ["estado", "imagen", "temas", "patrones", "editor", "paletas",
                  "dashboard", "video", "diagnostico"]

# Imagen elegida: validacion previa para no leer (ni subir) algo desproporcionado.
#
# OJO: el limite del PROTOCOLO son 62,5 MB, pero el PANEL no aguanta eso ni de lejos. Medido
# en el panel real (docs/RENDIMIENTO.md): con un fichero de 10 MB el panel se ATASCA
# (`bootFinish=0`, 12 s) y con 5 MB va sobrado (2,8 s); con 1 MB, 1,2 s. Si se le desborda la
# memoria se queda inservible, asi que el limite de la app se pone muy por debajo de lo que
# admite el protocolo: 8 MB como maximo y aviso a partir de 4 MB.
TAMANO_MAXIMO_IMAGEN = 8 * 1024 * 1024
AVISO_IMAGEN_GRANDE = 4 * 1024 * 1024
# Por encima de estos megapixeles no se genera miniatura: decodificar 6000x6000 (36 MP)
# costo +106 MB de RSS por una vista previa de 960 px que no hace falta para subir.
MEGAPIXELES_PREVIA = 12_000_000
LADO_MINIATURA = 960

# El bucle del dashboard de la aplicacion mira el espacio libre cada 10 fotogramas y se para
# por debajo de 20 MB: un panel sin memoria se queda con bootFinish=0.
FOTOGRAMAS_POR_COMPROBACION_ESPACIO = 10
ESPACIO_MINIMO_DASHBOARD_KB = 20 * 1024

# Cuantos `conn` seguidos con 0 propiedades hacen falta para dar el panel por perdido: uno
# solo puede ser una respuesta a medias, dos ya son una desconexion.
CONN_VACIOS_PARA_RECONECTAR = 2

# Limites de la ventana recordada en las preferencias.
ANCHO_MINIMO_VENTANA = 480
ALTO_MINIMO_VENTANA = 380

# Etiqueta del boton del aviso de la pagina Estado.
ETIQUETA_PARAR_SERVICIO = "Parar el servicio y usar el panel"


# --------------------------------------------------------------------------- utilidades


def en_hilo(tarea, al_terminar=None, al_fallar=None, nombre="cfv235"):
    """Ejecuta `tarea()` en un hilo y entrega el resultado en el hilo de la interfaz.

    `al_terminar(resultado)` o `al_fallar(excepcion)` se llaman con `GLib.idle_add`,
    asi que pueden tocar widgets sin miedo.
    """

    def cuerpo():
        try:
            resultado = tarea()
        except BaseException as exc:              # noqa: BLE001  (nada escapa al hilo)
            if al_fallar is not None:
                GLib.idle_add(al_fallar, exc)
            return
        if al_terminar is not None:
            GLib.idle_add(al_terminar, resultado)

    hilo = threading.Thread(target=cuerpo, name=nombre, daemon=True)
    hilo.start()
    return hilo


def formatear_valor(valor):
    """Texto legible para un valor de `conn` (que trae dicts, listas y numeros)."""
    if valor is None:
        return "-"
    if isinstance(valor, bool):
        return "si" if valor else "no"
    if isinstance(valor, dict):
        return "  ".join("%s=%s" % (k, formatear_valor(v)) for k, v in valor.items())
    if isinstance(valor, (list, tuple)):
        return ", ".join(formatear_valor(v) for v in valor) or "(vacio)"
    if isinstance(valor, float):
        texto = "%.2f" % valor
        return texto.rstrip("0").rstrip(".")
    return str(valor)


def formatear_propiedad(clave, valor):
    """Valor de una propiedad con su unidad, si la tiene."""
    if valor is None:
        return "-"
    texto = formatear_valor(valor)
    if texto == "-":
        return texto
    return texto + UNIDADES_PROPIEDADES.get(clave, "")


def titulo_propiedad(clave):
    """Nombre legible de una propiedad.

    `ETIQUETAS_PROPIEDADES` guarda "clave (explicacion)"; para el titulo interesa solo la
    explicacion ("Panel arrancado"), porque la clave va debajo en monoespaciado.
    """
    etiqueta = ETIQUETAS_PROPIEDADES.get(clave, clave)
    if etiqueta.endswith(")") and "(" in etiqueta:
        dentro = etiqueta[etiqueta.rindex("(") + 1:-1].strip()
        if dentro:
            return dentro[0].upper() + dentro[1:]
    return clave


def clase_pildora(clave, valor):
    """Clase CSS de la pildora de estado de una propiedad de un solo digito."""
    if clave == "bootFinish":
        return "cfv-pildora-ok" if valor == 1 else "cfv-pildora-error"
    if clave == "osdState":
        # osdState=1 significa que hay una capa OSD dibujandose ENCIMA: aviso, no error.
        return "cfv-pildora-aviso" if valor == 1 else "cfv-pildora-ok"
    return "cfv-pildora-neutra"


def datos_del_dispositivo(ruta):
    """Ficha de `canal.listar()` del dispositivo, o {} si no aparece."""
    if not ruta:
        return {}
    try:
        for ficha in canal.listar():
            if ficha.get("ruta") == ruta:
                return ficha
    except Exception:                             # noqa: BLE001
        return {}
    return {}


def identificar(ruta):
    """'COUGAR Inc. COUGAR USB Device  1d6b:0126' a partir de la ruta hidraw."""
    ficha = datos_del_dispositivo(ruta)
    if not ficha:
        return ruta or "(sin panel)"
    vid, pid = ficha.get("vid"), ficha.get("pid")
    ids = "%04x:%04x" % (vid, pid) if isinstance(vid, int) and isinstance(pid, int) else "?"
    return "%s  %s" % (ficha.get("nombre") or "(sin nombre)", ids)


def pagina_desplazable(contenido, ancho_maximo=900):
    """Contenido centrado y desplazable, al estilo de las preferencias de GNOME."""
    contenido.set_margin_top(18)
    contenido.set_margin_bottom(18)
    contenido.set_margin_start(18)
    contenido.set_margin_end(18)

    limite = Adw.Clamp()
    limite.set_maximum_size(ancho_maximo)
    limite.set_tightening_threshold(600)
    limite.set_child(contenido)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_child(limite)
    return scroll


def vista_de_texto(alto=140, editable=False):
    """TextView monoespaciado dentro de un marco con scroll."""
    vista = Gtk.TextView()
    vista.set_editable(editable)
    vista.set_monospace(True)
    vista.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    vista.set_top_margin(8)
    vista.set_bottom_margin(8)
    vista.set_left_margin(8)
    vista.set_right_margin(8)
    if not editable:
        vista.set_cursor_visible(False)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.set_min_content_height(alto)
    scroll.set_child(vista)

    marco = Gtk.Frame()
    marco.set_child(scroll)
    return marco, vista


def poner_texto(vista, texto):
    """Reemplaza el contenido de un TextView."""
    vista.get_buffer().set_text(texto if texto is not None else "")


def leer_texto(vista):
    """Contenido completo de un TextView."""
    almacen = vista.get_buffer()
    return almacen.get_text(almacen.get_start_iter(), almacen.get_end_iter(), False)


def textura_desde_bytes(datos):
    """`Gdk.Texture` a partir de un PNG en memoria, o None si no se puede.

    Se usa para las previsualizaciones: asi no hace falta escribir un PNG temporal (que
    ademas se pisaba cuando dos renders coincidian) ni volver a leerlo del disco.
    """
    if not datos:
        return None
    try:
        from gi.repository import Gdk
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(bytes(datos)))
    except Exception:                             # noqa: BLE001  (textura no disponible)
        return None


def poner_imagen(widget, datos):
    """Pinta unos bytes PNG en un `Gtk.Picture`. Devuelve si lo consiguio."""
    textura = textura_desde_bytes(datos)
    if textura is None:
        return False
    widget.set_paintable(textura)
    return True


def etiqueta_accesible(widget, texto):
    """Nombre accesible para un widget de solo icono.

    GTK4 no tiene `set_accessible_label` (era de GTK3): la etiqueta se pone con
    `update_property`, que es la API de `Gtk.Accessible`. Sin esto, un boton de solo icono
    se anuncia como "boton" y nada mas.
    """
    with contextlib.suppress(Exception):
        widget.update_property([Gtk.AccessibleProperty.LABEL], [texto])


def dimensiones_imagen(ruta):
    """(ancho, alto) leyendo SOLO la cabecera del fichero; (None, None) si no se sabe.

    Hace falta antes de decidir si merece la pena decodificar la imagen: con las
    dimensiones no se toca la memoria, y una foto de 6000x6000 (36 MP) no debe decodificarse
    entera solo para ensenar una miniatura de 960 px.
    """
    try:
        with open(ruta, "rb") as fh:
            cabecera = fh.read(32)
            if cabecera.startswith(b"\x89PNG\r\n\x1a\n") and len(cabecera) >= 24:
                ancho, alto = struct.unpack(">II", cabecera[16:24])
                return int(ancho), int(alto)
            if cabecera[:6] in (b"GIF87a", b"GIF89a") and len(cabecera) >= 10:
                ancho, alto = struct.unpack("<HH", cabecera[6:10])
                return int(ancho), int(alto)
            # JPEG: hay que recorrer los marcadores hasta el SOF, que trae las medidas.
            fh.seek(2)
            while True:
                byte = fh.read(1)
                if not byte:
                    return None, None
                if byte != b"\xff":
                    continue
                marcador = fh.read(1)
                while marcador == b"\xff":
                    marcador = fh.read(1)
                if not marcador:
                    return None, None
                if 0xC0 <= marcador[0] <= 0xCF and marcador[0] not in (0xC4, 0xC8, 0xCC):
                    fh.read(3)
                    alto, ancho = struct.unpack(">HH", fh.read(4))
                    return int(ancho), int(alto)
                largo = fh.read(2)
                if len(largo) < 2:
                    return None, None
                salto = int.from_bytes(largo, "big")
                if salto < 2:
                    return None, None
                fh.seek(salto - 2, os.SEEK_CUR)
    except (OSError, struct.error, ValueError):
        return None, None


def describir_imagen(ancho, alto, tamano):
    """Texto corto con las medidas y el peso de una imagen, para los avisos."""
    partes = []
    if ancho and alto:
        partes.append("%dx%d" % (ancho, alto))
    partes.append("%.1f MB" % (tamano / 1048576.0))
    return ", ".join(partes)


def tipo_de_fichero(ruta):
    """'png' | 'jpeg' | 'gif' | None leyendo solo los primeros bytes del fichero."""
    try:
        with open(ruta, "rb") as fh:
            cabecera = fh.read(16)
    except OSError:
        return None
    return protocolo.tipo_de_imagen(cabecera)


def nombre_para_el_panel(ruta, prefijo="tema"):
    """Nombre de medio seguro para el panel a partir de la ruta de un fichero local."""
    base = os.path.splitext(os.path.basename(ruta or ""))[0] or prefijo
    limpio = "".join(caracter if caracter.isalnum() or caracter in "-_" else "_"
                     for caracter in base)
    return "%s_%s.png" % (prefijo, limpio[:60])


@contextlib.contextmanager
def escritura_fiable(panel):
    """Evita el EAGAIN al escribir en un descriptor no bloqueante.

    `Canal.abrir()` abre el dispositivo con O_NONBLOCK y `Canal._escribir()` no reintenta
    EAGAIN: solo mira EINVAL y ENODEV/EIO/ESHUTDOWN, y cualquier otro error lo vuelve a
    lanzar. En el panel real no se nota (los informes son atomicos y el buffer del kernel
    es holgado), pero el simulador es un PTY con un buffer de unos pocos KB: al subir un
    PNG de mas de ~4 KB, las escrituras pueden adelantarse al lector y el write devuelve
    EAGAIN, con lo que la subida se pierde con un BlockingIOError.

    Mientras dura la subida se pone el descriptor en modo bloqueante y se restaura
    despues. Las lecturas no se ven afectadas: `Canal._leer()` usa select(), que se
    comporta igual sobre un descriptor bloqueante.
    """
    canal = getattr(panel, "canal", None)
    fd = getattr(canal, "fd", None)
    if fd is None:
        yield
        return
    try:
        if os.get_blocking(fd):
            yield
            return
    except OSError:
        yield
        return

    os.set_blocking(fd, True)
    try:
        yield
    finally:
        try:
            os.set_blocking(fd, False)
        except OSError:
            pass


class PanelPrestado:
    """Panel que toma el cerrojo de `PanelCompartido` en CADA escritura.

    El reproductor de video y el bucle del dashboard suben fotogramas sin parar; si se les
    pasara el panel con el cerrojo tomado durante todo el rato, las otras paginas se
    quedarian esperando. Este envoltorio abre y cierra la sesion fotograma a fotograma,
    igual que hace el bucle del dashboard, y usa `escritura_fiable` para no perder el
    fotograma con EAGAIN en los descriptores no bloqueantes.

    NO guarda ninguna referencia al `Panel`: lo pide al cerrojo en cada uso. Antes si la
    guardaba, y cuando el panel se desconectaba (o se reconectaba en otro `/dev/hidrawN`)
    el envoltorio seguia hablandole al objeto viejo, que reabria el hidraw por detras del
    dueño compartido. Ahora, si el panel cambio, el envoltorio usa el nuevo.
    """

    def __init__(self, compartido):
        self._compartido = compartido

    def subir_datos(self, datos, nombre, capa="osd"):
        with self._compartido.sesion() as panel:
            with escritura_fiable(panel):
                return panel.subir_datos(datos, nombre, capa=capa)

    def subir_archivo(self, ruta, capa="osd", nombre=None):
        with self._compartido.sesion() as panel:
            with escritura_fiable(panel):
                return panel.subir_archivo(ruta, capa=capa, nombre=nombre)

    def espacio_libre_kb(self):
        with self._compartido.sesion() as panel:
            return panel.espacio_libre_kb()

    def __getattr__(self, nombre):
        # Todo lo demas (propiedades, telemetria, lecturas) va al panel de verdad: solo las
        # escrituras necesitan el cuidado del cerrojo y del EAGAIN.
        return getattr(self._compartido.abrir(), nombre)


def cargar_video():
    """El modulo `cfv235.video` o (None, motivo). Import perezoso y tolerante.

    La pagina Video se construye igual aunque el modulo no exista: en ese caso ensena un
    aviso en vez de romper la ventana.
    """
    try:
        from cfv235 import video
    except ImportError as exc:
        return None, str(exc)
    except Exception as exc:                      # noqa: BLE001  (modulo roto a medias)
        return None, "%s: %s" % (type(exc).__name__, exc)
    return video, ""


def estado_vacio(titulo, descripcion, icono, hijo=None):
    """`Adw.StatusPage` para el estado de espera o vacio de una pagina.

    `hijo` es el widget que va debajo del texto: sirve para poner ahi mismo el boton que
    saca al usuario del estado vacio.
    """
    pagina = Adw.StatusPage()
    pagina.set_title(titulo)
    pagina.set_description(descripcion)
    pagina.set_icon_name(icono)
    pagina.set_vexpand(True)
    pagina.set_hexpand(True)
    if hijo is not None:
        pagina.set_child(hijo)
    return pagina


def apilar(*paginas):
    """`Gtk.Stack` con las paginas dadas como pares (nombre, widget)."""
    pila = Gtk.Stack()
    pila.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
    pila.set_transition_duration(150)
    for nombre, pagina in paginas:
        pila.add_named(pagina, nombre)
    return pila


def fila_accion(titulo, subtitulo="", icono=None):
    """`Adw.ActionRow` con icono, para agrupar botones con jerarquia."""
    fila = Adw.ActionRow(title=titulo)
    if subtitulo:
        fila.set_subtitle(subtitulo)
    if icono:
        fila.set_icon_name(icono)
    return fila


def detalle_subida(resultado):
    """Texto con transport / acuse / transported de un `ResultadoSubida`."""
    lineas = [resultado.resumen()]
    lineas.append("")
    lineas.append("bytes: %d   bloques: %d   tiempo: %.0f ms"
                  % (resultado.bytes, resultado.bloques, resultado.ms_total))
    if resultado.espacio_antes_kb is not None:
        lineas.append("espacio antes: %s KB" % resultado.espacio_antes_kb)

    lineas.append("")
    if resultado.transport is not None:
        lineas.append("transport : code=%s  %s"
                      % (resultado.transport.code,
                         (resultado.transport.cuerpo or "").strip()[:300] or "(sin cuerpo)"))
    else:
        lineas.append("transport : (no se llego a enviar)")
    lineas.append("acuse     : code=%s  llego=%s"
                  % (resultado.acuse_code, "si" if resultado.acuse_llego else "no"))
    if resultado.transported is not None:
        lineas.append("transported: code=%s  %s"
                      % (resultado.transported.code,
                         (resultado.transported.cuerpo or "").strip()[:300] or "(sin cuerpo)"))
    else:
        lineas.append("transported: (no se llego a enviar)")

    if resultado.avisos:
        lineas.append("")
        for aviso in resultado.avisos:
            lineas.append("- aviso: %s" % aviso)
    return "\n".join(lineas)


def _si_no(valor):
    return "si" if valor else "no"


def resumen_servicio(datos):
    """Linea corta con el estado del servicio, para las filas de la interfaz.

    `datos` es lo que devuelve `_leer_servicio()` (siempre trae las mismas claves, aunque
    `systemctl` no exista).
    """
    if not datos:
        return "Leyendo el estado del servicio..."
    if not datos.get("modulo", True):
        return "El nucleo no trae cfv235.servicio: no se puede gestionar desde aqui."
    if not datos.get("disponible"):
        return "systemd de usuario no disponible en este sistema."
    estado = datos.get("estado") or {}
    partes = [estado.get("descripcion") or estado.get("estado") or "desconocido"]
    if estado.get("subestado"):
        partes.append(estado["subestado"])
    partes.append("arranca al iniciar sesion" if estado.get("habilitado")
                  else "no arranca al iniciar sesion")
    partes.append("%d reinicios" % (estado.get("reinicios") or 0))
    if datos.get("ocupado"):
        partes.append("tiene tomado el panel")
    if not datos.get("instalada"):
        partes.append("unidad no instalada")
    return "  -  ".join(partes)


def detalle_servicio(datos):
    """Volcado completo del servicio y su registro (pagina Diagnostico)."""
    if not datos:
        return "Leyendo el estado del servicio..."
    if not datos.get("modulo", True):
        return ("El nucleo no trae el modulo `cfv235.servicio`: la aplicacion no puede "
                "gestionar el servicio del dashboard desde aqui.")

    lineas = ["SERVICIO SYSTEMD DE USUARIO"]
    lineas.append("  unidad            : %s" % (datos.get("nombre") or "(desconocida)"))
    lineas.append("  instalada         : %s" % _si_no(datos.get("instalada")))
    lineas.append("  disponible        : %s" % _si_no(datos.get("disponible")))
    if datos.get("error"):
        lineas.append("  error             : %s" % datos["error"])

    estado = datos.get("estado") or {}
    if estado:
        lineas.append("  estado            : %s / %s"
                      % (estado.get("estado") or "-", estado.get("subestado") or "-"))
        lineas.append("  descripcion       : %s" % (estado.get("descripcion") or "-"))
        lineas.append("  pid               : %s" % (estado.get("pid") or "-"))
        lineas.append("  reinicios         : %s" % (estado.get("reinicios") or 0))
        lineas.append("  al iniciar sesion : %s" % _si_no(estado.get("habilitado")))
    else:
        lineas.append("  estado            : (sin datos de systemd)")

    lineas.append("  pid del bloqueo del panel : %s  (el ultimo que lo tomo; el fichero "
                  "no se borra al soltarlo)" % (datos.get("pid_bloqueo") or "-"))
    lineas.append("  el servicio tiene el panel: %s" % _si_no(datos.get("ocupado")))
    lineas.append("")
    lineas.append("REGISTRO (journalctl --user -u %s)" % (datos.get("nombre") or "-"))
    if not (datos.get("registro") or "").strip():
        # El journal NO se lee en cada refresco (era un proceso mas por vuelta): se lee al
        # pintar la pagina Diagnostico y al abrir "Ver registro".
        lineas.append("  (no leido: pulsa Actualizar en Diagnostico o Ver registro)")
        return "\n".join(lineas)
    for linea in (datos.get("registro") or "").strip().splitlines():
        lineas.append("  %s" % linea)
    return "\n".join(lineas)


# --------------------------------------------------------------------------- el panel


class PanelCompartido:
    """Un unico `Panel` abierto bajo demanda y serializado entre hilos.

    Se abre la primera vez que alguien lo pide (no al arrancar la ventana), de modo que
    la aplicacion funciona igual sin panel: la primera operacion falla con `SinPanel` y
    la pagina Estado lo cuenta.
    """

    def __init__(self):
        self._cerrojo = threading.RLock()
        self._panel = None
        self._invalidado = False
        self.variante = None
        self.error_negociacion = ""
        self._conn_vacios = 0
        self.sin_bloqueo = os.environ.get(VAR_SIN_BLOQUEO, "").lower() in (
            "1", "si", "sí", "true", "yes", "on")

    def _crear_panel(self, ruta):
        """Crea el Panel, respetando el bloqueo exclusivo del dispositivo.

        El panel solo admite una sesion: si el dashboard (o el editor de COUGAR) lo esta
        usando, `cfv235.canal` lanza `PanelOcupado` y aqui se cuenta tal cual en la
        pagina Estado en vez de romper. Con CFV235_SIN_BLOQUEO=1 se salta el bloqueo,
        que es lo que hace falta para mirar el panel mientras otro proceso lo usa.
        """
        if not self.sin_bloqueo:
            return Panel(dispositivo=ruta, timeout=3.0)
        try:
            from cfv235.canal import Canal
            return Panel(canal=Canal(ruta, timeout=3.0, sin_bloqueo=True))
        except TypeError:
            # El nucleo todavia no conoce `sin_bloqueo`: se usa el camino normal.
            self.sin_bloqueo = False
            return Panel(dispositivo=ruta, timeout=3.0)

    @staticmethod
    def ruta_pedida():
        """La ruta que pide el entorno AHORA (puede haber cambiado desde el ultimo uso)."""
        return (os.environ.get(VAR_DISPOSITIVO)
                or os.environ.get(VAR_DISPOSITIVO_FALSO)
                or "")

    @property
    def ruta(self):
        panel = self._panel
        return panel.dispositivo if panel is not None else None

    @property
    def abierto(self):
        return self._panel is not None

    def abrir(self):
        """Devuelve el panel abierto, abriendolo la primera vez. Lanza ErrorCanal.

        Antes, si ya habia un `Panel` se devolvia tal cual **para siempre**: cuando el panel
        se desconectaba (o volvia con otro `/dev/hidrawN`) la aplicacion seguia hablandole al
        objeto viejo y acababa diciendo "el panel esta abierto pero no contesta". Ahora el
        panel cacheado se tira en cuanto hay un fallo (`invalidar()`), al reconectar y
        cuando el entorno pasa a pedir otra ruta.
        """
        with self._cerrojo:
            if self._invalidado:
                # Alguien dio el panel por perdido: se tira y se resuelve de nuevo.
                self._invalidado = False
                self._cerrar_panel(self._olvidar())
            elif self._panel is not None:
                pedida = self.ruta_pedida()
                if pedida and self._panel.ruta_pedida and pedida != self._panel.ruta_pedida:
                    # El entorno apunta a otro dispositivo: el panel cacheado ya no vale.
                    self._cerrar_panel(self._olvidar())
                else:
                    return self._panel
            return self._abrir_de_cero()

    def _abrir_de_cero(self):
        """Resuelve la ruta, crea y abre un `Panel` nuevo. Se llama con el cerrojo tomado."""
        ruta = self.ruta_pedida() or canal.buscar()
        if not ruta:
            raise SinPanel(
                "no se encontro el panel COUGAR CFV235. Comprueba que esta conectado "
                "con el cable de datos y que existe /dev/hidraw*."
            )

        panel = self._crear_panel(ruta)
        try:
            panel.abrir()
        except BaseException:
            panel.cerrar()
            raise

        # La negociacion no es imprescindible para abrir: si falla se deja el panel
        # abierto y se avisa, porque muchas veces el panel responde un poco despues.
        self.error_negociacion = ""
        self.variante = None
        try:
            self.variante = panel.canal.autonegociar(timeout=2.5)
        except ErrorCanal as exc:
            self.error_negociacion = str(exc)

        self._panel = panel
        self._conn_vacios = 0
        return panel

    def _olvidar(self):
        """Suelta el panel cacheado SIN cerrarlo. Se llama con el cerrojo tomado."""
        panel, self._panel = self._panel, None
        self.variante = None
        self.error_negociacion = ""
        self._conn_vacios = 0
        return panel

    @staticmethod
    def _cerrar_panel(panel):
        """Cierra un `Panel` sin que un fallo de cierre salga de aqui."""
        if panel is None:
            return
        try:
            panel.cerrar()
        except Exception:                         # noqa: BLE001
            pass

    @contextlib.contextmanager
    def sesion(self):
        """El panel, con el cerrojo tomado, mientras dure el `with`."""
        with self._cerrojo:
            yield self.abrir()

    def usar(self, tarea):
        """Ejecuta `tarea(panel)` en exclusion mutua con el resto de la aplicacion."""
        with self.sesion() as panel:
            return tarea(panel)

    def invalidar(self):
        """Marca el panel cacheado como no fiable tras un fallo.

        Solo levanta una bandera: el descriptor lo cierra el siguiente que use el panel (o
        `cerrar()`). Asi se puede llamar desde el hilo de la interfaz sin arriesgarse a
        esperar a una subida en marcha.
        """
        self._invalidado = True

    def anotar_conn_vacio(self):
        """Cuenta un `conn` contestado con 0 propiedades. True si toca reconectar."""
        with self._cerrojo:
            self._conn_vacios += 1
            return self._conn_vacios >= CONN_VACIOS_PARA_RECONECTAR

    def anotar_conn_ok(self):
        """Un `conn` con propiedades: el panel esta vivo."""
        with self._cerrojo:
            self._conn_vacios = 0

    def reconectar(self):
        """Cierra lo que hubiera y vuelve a resolver el panel respetando la ruta pedida.

        Primero se intenta `Panel.reabrir()` (que respeta la ruta que pidio el usuario y,
        si era un `/dev/hidraw*`, busca el panel por VID/PID porque puede haber cambiado de
        numero). Si eso falla, se resuelve de cero. Lanza `ErrorCanal` si no hay panel.
        """
        with self._cerrojo:
            self._invalidado = False
            panel = self._panel
            pedida = self.ruta_pedida()
            if panel is not None and (not pedida or pedida == panel.ruta_pedida):
                try:
                    if panel.reabrir():
                        self.error_negociacion = ""
                        self.variante = panel.canal.variante
                        self._conn_vacios = 0
                        return panel
                except Exception:                 # noqa: BLE001  (se resuelve de cero)
                    pass
            self._olvidar()
            try:
                return self._abrir_de_cero()
            finally:
                self._cerrar_panel(panel)

    def cerrar(self):
        """Cierra el hidraw y olvida el panel (idempotente).

        El cierre se hace **dentro** del cerrojo: antes se sacaba el panel y se cerraba
        fuera, asi que una operacion en marcha podia seguir usando (y reabriendo por detras)
        un descriptor que ya se habia dado por cerrado.
        """
        with self._cerrojo:
            self._invalidado = False
            self._cerrar_panel(self._olvidar())


# --------------------------------------------------------------------------- la ventana



# ------------------------------------------------------------------ volcado de la interfaz
def describir_ui(widget, nivel=0):
    """Lineas de texto que describen un widget y sus hijos.

    Es la forma de revisar la app **sin verla**: en vez de capturas de pantalla, la interfaz
    se cuenta a si misma (paginas, grupos, filas, textos y estados). Lo usan las pruebas y el
    modo `--informe`.
    """
    sangria = "  " * nivel
    lineas = []
    try:
        clase = type(widget).__name__
        texto = None

        if isinstance(widget, Adw.PreferencesGroup):
            texto = "[grupo] %s" % (widget.get_title() or "")
            if widget.get_description():
                texto += "  --  %s" % widget.get_description()
        elif isinstance(widget, Adw.Banner):
            texto = "[aviso] %s   revelado=%s" % (widget.get_title() or "",
                                                  widget.get_revealed())
        elif isinstance(widget, Adw.StatusPage):
            texto = "[estado vacio] %s -- %s" % (widget.get_title() or "",
                                                 widget.get_description() or "")
            if not widget.get_visible():
                texto += "   (oculto)"
        elif isinstance(widget, Adw.ActionRow):
            texto = "- %s" % (widget.get_title() or "")
            if widget.get_subtitle():
                texto += "   [%s]" % widget.get_subtitle()
            estado = []
            if not widget.get_sensitive():
                estado.append("deshabilitada")
            if isinstance(widget, Adw.SwitchRow):
                estado.append("activo" if widget.get_active() else "apagado")
            elif isinstance(widget, Adw.ComboRow):
                modelo = widget.get_model()
                elegido = ""
                if modelo is not None and widget.get_selected() < modelo.get_n_items():
                    item = modelo.get_item(widget.get_selected())
                    if item is not None:
                        elegido = item.get_string() or ""
                estado.append("seleccion=%s" % (elegido or widget.get_selected()))
            elif isinstance(widget, Adw.SpinRow):
                estado.append("valor=%s" % widget.get_value())
            elif isinstance(widget, Adw.ExpanderRow):
                estado.append("desplegada" if widget.get_expanded() else "plegada")
            if hasattr(widget, "get_active") and isinstance(widget, Gtk.CheckButton):
                estado.append("marcada" if widget.get_active() else "sin marcar")
            if estado:
                texto += "   (%s)" % ", ".join(estado)
        elif isinstance(widget, Gtk.Label):
            contenido = (widget.get_text() or "").strip()
            if contenido:
                texto = '"%s"' % contenido.replace("\n", " / ")
        elif isinstance(widget, Gtk.Picture):
            texto = "[imagen]%s" % ("" if widget.get_visible() else "   (oculta)")
        elif isinstance(widget, Gtk.TextView):
            texto = "[texto: %d caracteres]" % len(leer_texto(widget))
        elif isinstance(widget, Gtk.Button):
            etiqueta = widget.get_label()
            if etiqueta:
                texto = "(%s)" % etiqueta
            elif widget.get_icon_name():
                texto = "(%s)" % widget.get_icon_name()
            if not widget.get_sensitive():
                texto = (texto or "(boton)") + "   deshabilitado"
        elif isinstance(widget, Gtk.Scale):
            texto = "[deslizador: %s]" % widget.get_value()
        elif isinstance(widget, Gtk.Spinner):
            if widget.get_visible():
                texto = "[girando]"

        if texto:
            lineas.append(sangria + texto)
            # Estas clases ya se cuentan enteras: sus hijos son las etiquetas internas que
            # GTK crea para el titulo y el subtitulo, y repetirlas llenaba el informe.
            if isinstance(widget, (Adw.ActionRow, Adw.Banner, Adw.StatusPage, Gtk.Label,
                                   Gtk.Button, Gtk.Picture, Gtk.TextView)):
                return lineas
            sangria = "  " * (nivel + 1)

        hijo = widget.get_first_child()
        while hijo is not None:
            lineas.extend(describir_ui(hijo, nivel + 1 if not texto else nivel + 1))
            hijo = hijo.get_next_sibling()
    except Exception as exc:                          # noqa: BLE001
        lineas.append("%s!! no se pudo describir (%s): %s" % (sangria, clase, exc))
    return lineas



# ------------------------------------------------------------------ dialogos de ficheros
def filtro_imagenes():
    """Filtro de imagenes: por MIME, por extension, por sufijo y por formatos de GdkPixbuf.

    Con solo los MIME types el dialogo escondia ficheros validos (un .jpg normal no aparecia),
    porque el tipo se adivina por el contenido y no siempre acierta.
    """
    filtro = Gtk.FileFilter()
    filtro.set_name("Imagenes (PNG, JPEG, GIF)")
    for tipo in ("image/png", "image/jpeg", "image/gif"):
        filtro.add_mime_type(tipo)
    for patron in ("*.png", "*.jpg", "*.jpeg", "*.jpe", "*.jfif", "*.gif"):
        filtro.add_pattern(patron)
    with contextlib.suppress(Exception):
        filtro.add_pixbuf_formats()
    for sufijo in ("png", "jpg", "jpeg", "jpe", "jfif", "gif"):
        with contextlib.suppress(Exception):
            filtro.add_suffix(sufijo)
    return filtro


def filtro_todos():
    """Filtro sin restricciones: se deja por defecto para que NUNCA haya un fichero oculto."""
    filtro = Gtk.FileFilter()
    filtro.set_name("Todos los ficheros")
    return filtro


def abrir_dialogo_fichero(ventana, titulo, al_elegir, carpeta="", filtros=(),
                          accion=None, etiqueta_aceptar="Abrir"):
    """Abre el selector de ficheros y llama a `al_elegir(ruta)` (None si se cancela).

    Se usa **`Gtk.FileChooserDialog`**, que GTK dibuja en el propio proceso. Ni
    `Gtk.FileDialog` ni `Gtk.FileChooserNative` sirven aqui:

      * `Gtk.FileDialog` (la API nueva) manda SIEMPRE la peticion al portal del escritorio. Ese
        portal necesita el token de activacion que solo existe si la app la lanzo el escritorio;
        sin el falla con "Failed to associate portal window with parent window ''" y el dialogo
        **no se ve**: el boton parece no hacer nada, sin ningun error.
      * `Gtk.FileChooserNative` **tampoco** vale: en GTK 4.22 usa el portal de todas formas, y
        `GTK_USE_PORTAL=0` ya no lo evita (probado: sigue apareciendo la peticion al portal y el
        dialogo no se dibuja).
      * `Gtk.FileChooserDialog` **no pasa por ningun portal**: es un dialogo de GTK de toda la
        vida. Esta marcado como obsoleto desde 4.10, pero es el unico que garantiza que el
        usuario vea algo, que es lo que importa.

    La referencia se guarda en `ventana._dialogo_abierto` mientras esta abierto, para que
    Python no lo recoja a mitad.
    """
    if accion is None:
        accion = Gtk.FileChooserAction.OPEN
    dialogo = Gtk.FileChooserDialog(title=titulo, transient_for=ventana, action=accion,
                                    modal=True)
    dialogo.add_button("Cancelar", Gtk.ResponseType.CANCEL)
    dialogo.add_button(etiqueta_aceptar, Gtk.ResponseType.ACCEPT)
    if carpeta and os.path.isdir(carpeta):
        with contextlib.suppress(Exception):
            dialogo.set_current_folder(Gio.File.new_for_path(carpeta))
    for filtro in filtros:
        with contextlib.suppress(Exception):
            dialogo.add_filter(filtro)
    if filtros:
        with contextlib.suppress(Exception):
            dialogo.set_filter(filtros[0])

    def responder(dlg, respuesta):
        ventana._dialogo_abierto = None
        ruta = None
        if respuesta == Gtk.ResponseType.ACCEPT:
            with contextlib.suppress(Exception):
                archivo = dlg.get_file()
                if archivo is not None:
                    ruta = archivo.get_path()
        with contextlib.suppress(Exception):
            dlg.destroy()
        with contextlib.suppress(Exception):
            al_elegir(ruta)

    dialogo.connect("response", responder)
    ventana._dialogo_abierto = dialogo         # la referencia que lo mantiene vivo
    dialogo.present()
    return dialogo



class VentanaPrincipal(Adw.ApplicationWindow):
    """Ventana con las seis paginas: Estado, Imagen, Temas, Dashboard, Video y Diagnostico."""

    def __init__(self, aplicacion):
        super().__init__(application=aplicacion)
        self.set_title("Panel COUGAR CFV235")

        # Preferencias persistentes (JSON en ~/.config/cfv235/config.json). Se leen antes de
        # montar nada para que la ventana, la pagina y los controles arranquen como se
        # dejaron. `config.leer()` nunca lanza: si el fichero esta corrupto, defectos.
        self._config = config.leer()
        self._persistir_activo = False      # no se guarda nada mientras se monta la ventana
        self.set_default_size(self._ancho_guardado(), self._alto_guardado())

        self.panel = PanelCompartido()

        # Imagen
        self._imagen = None
        self._tamano_imagen = 0
        self._miniatura = None              # ultimo PNG de miniatura en memoria

        # Vista previa de lo que muestra el panel: el ultimo contenido que hemos enviado.
        self._ultimo_enviado = None
        self._ultimo_enviado_nombre = ""

        # Gtk.FileDialog es asincrono: si no se guarda una referencia, Python lo destruye al
        # salir de la funcion y el dialogo NO LLEGA A VERSE. Esta es la referencia que lo
        # mantiene vivo mientras esta abierto (se suelta en el callback).
        self._dialogo_abierto = None

        # Temas
        self._temas = []            # [{"ruta":..., "nombre":...}]
        self._tema_actual = None
        self._tema_renderizando = False

        # Dashboard
        self._parar_dashboard = None
        self._hilo_dashboard = None
        self._dashboard_activo = False
        self._fotogramas = 0

        # Dashboard: perfil y secciones elegidos. Se aplican al momento (el bucle relee el
        # tema cuando `_dashboard_version` cambia), sin reiniciar la aplicacion.
        self._perfil_dashboard = self._perfil_guardado()
        self._ajustes_secciones = self._secciones_guardadas()
        self._dashboard_version = 0
        self._silenciar_secciones = False

        # Video
        self._video_ruta = None
        self._video_informe = None
        self._video_reproductor = None
        self._parar_video = None
        self._hilo_video = None
        self._video_activo = False
        self._video_fotogramas = 0

        # Keepalive: trafico periodico (telemetria) para que el panel no se apague por
        # espera cuando solo hay una foto puesta, sin dashboard ni video en marcha.
        # El hilo lo arranca el interruptor de la pagina Estado (y solo hablo con el
        # panel si el dashboard/video no estan corriendo: ellos ya generan trafico).
        self._parar_keepalive = None
        self._hilo_keepalive = None
        self._keepalive_activo = False
        self._keepalive_tramas = 0

        # Evita que rellenar los controles dispare las ordenes al panel.
        self._silenciar = False

        # Servicio de usuario del dashboard (todo lo que habla con systemctl va en hilo).
        self._servicio = {}                 # lo ultimo que devolvio _leer_servicio()
        self._leyendo_servicio = False      # hay una lectura en marcha
        self._servicio_pendiente = False    # pidieron otro refresco mientras se leia
        self._con_registro_pendiente = False  # ese refresco pendiente pedia el journal
        self._accion_servicio_en_curso = False
        self._silenciar_servicio = False    # evita que pintar el interruptor lo dispare
        self._panel_ocupado = False         # el ultimo fallo al abrir fue PanelOcupado
        self._dialogo_servicio = None       # dialogo "Ver registro" abierto
        self._disponible_cache = None       # (valor, momento) de servicio.disponible()
        self._servicio_estuvo_activo = False

        # Panel: recuperacion ante desconexiones (BUG-1 y BUG-2).
        self._reconectando = False
        self._esperando_arranque = False
        self._recovery_en_curso = False
        self._reconexion_pendiente = False  # pidieron reconectar mientras se intentaba
        self._panel_estuvo_ok = False       # para no avisar de una desconexion que no hubo
        self._avisado_desconexion = False
        self._props_estado = {}             # ultimas propiedades leidas (para "Acerca de")

        self._construir()
        self.connect("close-request", self._al_cerrar)

    # --- preferencias: lectura de los valores guardados

    def _ancho_guardado(self):
        return max(ANCHO_MINIMO_VENTANA, self._entero_guardado("ancho", 1060))

    def _alto_guardado(self):
        return max(ALTO_MINIMO_VENTANA, self._entero_guardado("alto", 800))

    def _entero_guardado(self, clave, defecto):
        valor = self._config.get(clave, defecto)
        return valor if isinstance(valor, int) and not isinstance(valor, bool) else defecto

    def _perfil_guardado(self):
        perfil = self._config.get("perfil")
        claves = [clave for clave, _etiqueta, _desc in PERFILES_DASHBOARD]
        return perfil if perfil in claves else PERFILES_DASHBOARD[0][0]

    def _secciones_guardadas(self):
        guardadas = self._config.get("secciones")
        if not isinstance(guardadas, dict):
            return {}
        return {str(clave): bool(valor) for clave, valor in guardadas.items()}

    def _guardar(self, **cambios):
        """Apunta preferencias en memoria y en disco. Nunca lanza ni interrumpe nada."""
        if not self._persistir_activo:
            return
        self._config.update(cambios)
        config.escribir(**cambios)

    # ------------------------------------------------------------------ montaje

    def _construir(self):
        # La hoja de estilo con la identidad de la app (acento, tarjetas, pilidoras).
        estilo.aplicar()
        # El tema que se dejo elegido (claro/oscuro/sistema).
        with contextlib.suppress(Exception):
            estilo.poner_esquema(self._config.get("tema") or "sistema")

        self.pila = Adw.ViewStack()
        self.conmutador_titulo = Adw.ViewSwitcherTitle(stack=self.pila)
        self.conmutador_barra = Adw.ViewSwitcherBar(stack=self.pila)

        cabecera = Adw.HeaderBar()
        # El conmutador ensena el titulo de la pagina visible en la barra superior.
        cabecera.set_title_widget(self.conmutador_titulo)

        # Refrescar global: mira la pagina visible y la vuelve a leer (Ctrl+R o F5).
        boton_refrescar = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        boton_refrescar.set_tooltip_text("Refrescar la pagina actual (Ctrl+R)")
        # Es un boton de solo icono: sin etiqueta accesible, un lector de pantalla no tiene
        # nada que anunciar.
        etiqueta_accesible(boton_refrescar, "Refrescar la pagina actual")
        boton_refrescar.connect("clicked", self._refrescar_todo)
        cabecera.pack_end(boton_refrescar)
        self.boton_refrescar_global = boton_refrescar

        # Menu de la aplicacion (Atajos, Acerca de y Salir).
        self.boton_menu = Gtk.MenuButton()
        self.boton_menu.set_icon_name("open-menu-symbolic")
        self.boton_menu.set_tooltip_text("Menu de la aplicacion")
        etiqueta_accesible(self.boton_menu, "Menu de la aplicacion")
        self.boton_menu.set_menu_model(self._modelo_de_menu())
        cabecera.pack_end(self.boton_menu)

        vista = Adw.ToolbarView()
        vista.add_top_bar(cabecera)
        vista.set_content(self.pila)
        vista.add_bottom_bar(self.conmutador_barra)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(vista)
        self.set_content(self.toasts)

        self._acciones_y_atajos()

        # Patron de GNOME: la barra solo aparece cuando el titulo no cabe arriba.
        self.conmutador_titulo.bind_property("title-visible", self.conmutador_barra,
                                             "reveal", GObject.BindingFlags.SYNC_CREATE)

        self._pagina_estado()
        self._pagina_imagen()
        self._pagina_temas()
        # Las tres paginas de paginas_extra: galeria de patrones, editor de temas y paletas.
        # Van aqui, entre Temas y Dashboard, para que el orden de las pestanas sea el del
        # trabajo: mirar, crear y aplicar.
        self._anadir_pagina(paginas_extra.pagina_patrones(self), "patrones", "Patrones",
                            "view-grid-symbolic")
        self._anadir_pagina(paginas_extra.pagina_fondos(self), "fondos", "Fondos",
                            "wallpaper-symbolic")
        self._anadir_pagina(paginas_extra.pagina_temas(self), "editor", "Editor",
                            "document-edit-symbolic")
        self._anadir_pagina(paginas_extra.pagina_paletas(self), "paletas", "Paletas",
                            "preferences-color-symbolic")
        self._pagina_dashboard()
        self._pagina_video()
        self._pagina_diagnostico()

        # El estado del servicio se relee al entrar en las paginas que lo ensenan, y una
        # vez al arrancar (con `idle_add`, para que ya existan todos los widgets).
        self.pila.connect("notify::visible-child", self._al_cambiar_pagina)
        GLib.idle_add(self._refrescar_servicio)

        # La pagina que se estaba viendo al cerrar (si sigue existiendo).
        self._restaurar_pagina()

        # A partir de aqui los cambios de controles ya se apuntan en las preferencias.
        self._persistir_activo = True

        # Keepalive: si quedo activado de la sesion anterior, arranca al montar.
        if bool(self._config.get("keepalive", False)):
            self._arrancar_keepalive()

    # --- menu y atajos

    def _modelo_de_menu(self):
        """Menu de la cabecera: tema, Atajos, Acerca de y Salir."""
        menu = Gio.Menu()
        menu.append_submenu("Tema de la app", estilo.menu_de_esquema())
        menu.append("Atajos de teclado", "win.atajos")
        menu.append("Acerca de", "win.acerca")
        seccion_salir = Gio.Menu()
        seccion_salir.append("Salir", "win.salir")
        menu.append_section(None, seccion_salir)
        return menu

    def _acciones_y_atajos(self):
        """Registra las acciones de la ventana y sus atajos de teclado."""
        for nombre, funcion in (("refrescar", self._refrescar_todo),
                                ("salir", self._salir),
                                ("cerrar", self._cerrar_ventana),
                                ("atajos", self._mostrar_atajos),
                                ("acerca", self._mostrar_acerca),
                                ("parar", self._parar_todo),
                                ("reconectar", self._reconectar_accion)):
            accion = Gio.SimpleAction.new(nombre, None)
            accion.connect("activate", funcion)
            self.add_action(accion)

        # Tema de la app (claro / oscuro / segun el sistema): accion con parametro de texto.
        accion_tema = Gio.SimpleAction.new("tema", GLib.VariantType.new("s"))
        accion_tema.connect("activate", self._cambiar_tema)
        self.add_action(accion_tema)

        # Ctrl+1..Ctrl+6: una unica accion con parametro entero.
        accion_pagina = Gio.SimpleAction.new("pagina", GLib.VariantType.new("i"))
        accion_pagina.connect("activate", self._ir_a_pagina)
        self.add_action(accion_pagina)

        aplicacion = self.get_application()
        if aplicacion is None:
            return
        # Las notificaciones de escritorio solo pueden apuntar a acciones `app.` (GLib lo
        # avisa y no las ejecutaria): se registra `app.reconectar` como puente.
        if aplicacion.lookup_action("reconectar") is None:
            with contextlib.suppress(Exception):
                accion_app = Gio.SimpleAction.new("reconectar", None)
                accion_app.connect("activate", self._reconectar_accion)
                aplicacion.add_action(accion_app)
        atajos = {
            "win.refrescar": ["<Control>r", "F5"],
            "win.salir": ["<Control>q"],
            "win.cerrar": ["<Control>w"],
            "win.parar": ["Escape"],
        }
        for indice in range(len(PAGINAS_ATAJOS)):
            atajos["win.pagina(%d)" % indice] = ["<Control>%d" % (indice + 1)]
        for nombre, teclas in atajos.items():
            with contextlib.suppress(Exception):
                aplicacion.set_accels_for_action(nombre, teclas)

    def _salir(self, *_):
        """Ctrl+Q / menu Salir."""
        aplicacion = self.get_application()
        if aplicacion is not None:
            aplicacion.quit()

    def _cerrar_ventana(self, *_):
        """Ctrl+W: cierra esta ventana (la aplicacion termina con ella)."""
        self.close()

    def _parar_todo(self, *_):
        """Escape: corta el dashboard y el video si estan en marcha."""
        if self._dashboard_activo:
            self._parar_dashboard_y_esperar()
        if self._video_activo:
            self._parar_reproduccion_video()

    def _ir_a_pagina(self, _accion, parametro):
        """Ctrl+1..Ctrl+9: cambia de pagina."""
        indice = parametro.get_int32() if parametro is not None else -1
        if 0 <= indice < len(PAGINAS_ATAJOS):
            self.pila.set_visible_child_name(PAGINAS_ATAJOS[indice])

    def _restaurar_pagina(self):
        """Deja visible la pagina que estaba abierta al cerrar la ultima vez."""
        pagina = self._config.get("pagina")
        if pagina in PAGINAS_ATAJOS:
            self.pila.set_visible_child_name(pagina)

    def _cambiar_tema(self, _accion, parametro):
        """Cambia el tema de la app: claro, oscuro o el que use el sistema."""
        nombre = parametro.get_string() if parametro is not None else "sistema"
        if not estilo.poner_esquema(nombre):
            return
        self._guardar(tema=nombre)
        etiquetas = {"claro": "claro", "oscuro": "oscuro", "sistema": "segun el sistema"}
        self.avisar(f"Tema de la app: {etiquetas.get(nombre, nombre)}")

    def _mostrar_atajos(self, *_):
        """Dialogo con la lista de atajos de teclado."""
        dialogo = Adw.AlertDialog(
            heading="Atajos de teclado",
            body=("Ctrl+Q   Salir de la aplicacion\n"
                  "Ctrl+W   Cerrar la ventana\n"
                  "Ctrl+R   Refrescar la pagina (tambien F5)\n"
                  "Escape   Parar el dashboard o el video\n"
                  "Ctrl+1   Estado\n"
                  "Ctrl+2   Imagen\n"
                  "Ctrl+3   Temas\n"
                  "Ctrl+4   Dashboard\n"
                  "Ctrl+5   Video\n"
                  "Ctrl+6   Diagnostico\n"
                  "\nLas paginas Patrones, Fondos, Editor y Paletas se eligen en la barra "
                  "lateral."))
        dialogo.add_response("cerrar", "Cerrar")
        dialogo.set_close_response("cerrar")
        dialogo.present(self)

    def _mostrar_acerca(self, *_):
        """Dialogo Acerca de: nombre, version y lo que se sabe del panel."""
        import cfv235
        cuerpo = ["Aplicacion de escritorio del panel LCD COUGAR CFV235 (1920x462, USB HID)."]
        ruta = self.panel.ruta
        if ruta:
            cuerpo.append("Panel: %s" % ruta)
            props = self._props_estado or {}
            version = props.get("version")
            if isinstance(version, dict):
                firmware = version.get("firmware")
                if firmware:
                    cuerpo.append("Firmware: %s" % firmware)
        else:
            cuerpo.append("Panel: no conectado.")
        dialogo = Adw.AboutDialog(
            application_name="Panel COUGAR CFV235",
            application_icon="computer-symbolic",
            version=getattr(cfv235, "__version__", "?"),
            comments="\n".join(cuerpo))
        dialogo.present(self)

    def _reconectar_accion(self, *_):
        """Accion `win.reconectar`: la usan el boton, el atajo y la notificacion."""
        return self._reconectar()

    def _anadir_pagina(self, contenido, nombre, titulo, icono):
        pagina = self.pila.add_titled(contenido, nombre, titulo)
        pagina.set_icon_name(icono)
        return pagina

    def avisar(self, texto):
        """Toast breve de confirmacion en la ventana."""
        self.toasts.add_toast(Adw.Toast.new(texto))

    def avisar_largo(self, texto):
        """Toast para mensajes que hay que poder leer con calma."""
        toast = Adw.Toast.new(texto)
        toast.set_timeout(6)
        self.toasts.add_toast(toast)

    def avisar_error(self, texto):
        """Toast de error: dura mas y no lo tapa un aviso corto que llegue despues."""
        toast = Adw.Toast.new(texto)
        toast.set_timeout(8)
        toast.set_priority(Adw.ToastPriority.HIGH)
        self.toasts.add_toast(toast)

    def _notificar(self, clave, titulo, cuerpo="", accion="", etiqueta_accion=""):
        """Notificacion de escritorio (`Gio.Notification`) ademas del toast.

        Nunca lanza: en un sistema sin `org.freedesktop.Notifications` (o sin sesion de
        escritorio) el envio simplemente no hace nada y la aplicacion sigue igual.
        """
        aplicacion = self.get_application()
        if aplicacion is None:
            return
        if not aplicacion.get_application_id():
            return                                # sin id de aplicacion no hay notificacion
        try:
            notificacion = Gio.Notification.new(titulo)
            if cuerpo:
                notificacion.set_body(cuerpo)
            if accion:
                if etiqueta_accion:
                    notificacion.add_button(etiqueta_accion, accion)
                notificacion.set_default_action(accion)
            aplicacion.send_notification(clave or None, notificacion)
        except Exception:                         # noqa: BLE001  (sin demonio de avisos)
            pass

    def _refrescar_todo(self, *_):
        """Boton Refrescar de la barra superior: relee lo que ensena la pagina visible."""
        pagina = self._pagina_visible()
        if pagina == "estado":
            self._refrescar_estado()
        elif pagina == "imagen":
            if self._imagen:
                self._consultar_espacio()
            else:
                self.avisar("Elige antes una imagen para consultar el espacio libre.")
        elif pagina == "temas":
            self._cargar_lista_temas()
        elif pagina == "dashboard":
            self._muestrear_sensores()
        elif pagina == "video":
            if self._video_ruta:
                self._inspeccionar_video()
            else:
                self.avisar("Elige antes un video, un GIF o una carpeta de imagenes.")
        elif pagina == "diagnostico":
            self._actualizar_diagnostico()
        self._refrescar_servicio()
        return False

    # ------------------------------------------------------------------ pagina 1: Estado

    def _pagina_estado(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        # --- estado de espera: se ve mientras se lee el panel por primera vez
        self.espera_estado = estado_vacio(
            "Leyendo el panel",
            "Se consulta POST conn en un hilo; los controles aparecen en cuanto llegue la "
            "respuesta. La ventana funciona igual aunque no haya ningun panel conectado.",
            "emblem-synchronizing-symbolic")
        self.espera_estado.set_vexpand(False)
        caja.append(self.espera_estado)

        # --- avisos destacados
        self.aviso_panel = Adw.Banner(
            title="Sin panel: no se encontro ningun COUGAR CFV235 conectado.")
        self.aviso_panel.set_revealed(False)

        # El panel lo tiene el servicio del dashboard: se ofrece pararlo sin salir de la
        # aplicacion. Solo aparece si hay unidad instalada (lo decide _pintar_servicio).
        self.aviso_servicio = Adw.Banner(
            title="El panel lo tiene el servicio del dashboard y el panel solo admite "
                  "una sesion.")
        self.aviso_servicio.set_button_label(ETIQUETA_PARAR_SERVICIO)
        self.aviso_servicio.set_revealed(False)
        self.aviso_servicio.connect("button-clicked", self._parar_servicio_para_usar)

        self.aviso_brillo = Adw.Banner(
            title="El brillo esta a 0: la pantalla se ve negra; pulsa Despertar.")
        self.aviso_brillo.set_button_label("Despertar")
        self.aviso_brillo.set_revealed(False)
        self.aviso_brillo.connect("button-clicked", self._despertar)

        self.aviso_arranque = Adw.Banner(
            title="El panel no ha arrancado (bootFinish=0): puede tardar 60-80 s.")
        self.aviso_arranque.set_button_label("Esperar a que arranque")
        self.aviso_arranque.set_revealed(False)
        # Solo sale con bootFinish=0, asi que el boton espera justo lo que hace falta.
        self.aviso_arranque.connect("button-clicked", self._esperar_arranque)

        self.aviso_osd = Adw.Banner(
            title="Hay una capa OSD encima del fondo (osdState=1).")
        self.aviso_osd.set_revealed(False)

        caja_avisos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for aviso in (self.aviso_panel, self.aviso_servicio, self.aviso_brillo,
                      self.aviso_arranque, self.aviso_osd):
            caja_avisos.append(aviso)
        caja.append(caja_avisos)

        # --- lo que esta mostrando el panel
        # Es lo primero que se quiere saber al abrir la app, y el panel no tiene forma de
        # devolver su pantalla: se ensena el ultimo contenido que hemos enviado nosotros (o,
        # si aun no se ha enviado nada, como quedaria el dashboard ahora mismo).
        caja_vista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        caja_vista.add_css_class("cfv-tarjeta")

        cabecera_vista = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        titulo_vista = Gtk.Label(label="Lo que muestra el panel", xalign=0.0, hexpand=True)
        titulo_vista.add_css_class("cfv-titulo-seccion")
        boton_vista = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        boton_vista.set_tooltip_text("Actualizar la vista previa")
        boton_vista.add_css_class("flat")
        etiqueta_accesible(boton_vista, "Actualizar la vista previa del panel")
        boton_vista.connect("clicked", lambda *_: self._pintar_vista_previa())
        cabecera_vista.append(titulo_vista)
        cabecera_vista.append(boton_vista)
        caja_vista.append(cabecera_vista)

        marco_vista = Gtk.Box()
        marco_vista.add_css_class("cfv-marco-panel")
        self.vista_previa_panel = Gtk.Picture()
        self.vista_previa_panel.set_can_shrink(True)
        self.vista_previa_panel.set_size_request(-1, 148)
        marco_vista.append(self.vista_previa_panel)
        caja_vista.append(marco_vista)

        self.pie_vista = Gtk.Label(xalign=0.0)
        self.pie_vista.add_css_class("cfv-descripcion-seccion")
        caja_vista.append(self.pie_vista)
        caja.append(caja_vista)

        # --- ficha del dispositivo
        grupo_dispositivo = Adw.PreferencesGroup(
            title="Dispositivo",
            description="Lo que el sistema sabe del COUGAR CFV235 conectado.")
        self.fila_nombre = fila_accion("Nombre", icono="computer-symbolic")
        self.fila_serie = fila_accion("Numero de serie", icono="dialog-password-symbolic")
        self.fila_ids = fila_accion("VID:PID", icono="network-wired-symbolic")
        self.fila_ruta = fila_accion("Ruta hidraw", icono="drive-harddisk-symbolic")
        self.fila_variante = fila_accion("Variante de escritura negociada",
                                         icono="emblem-synchronizing-symbolic")
        for fila in (self.fila_nombre, self.fila_serie, self.fila_ids, self.fila_ruta,
                     self.fila_variante):
            grupo_dispositivo.add(fila)
        caja.append(grupo_dispositivo)

        # --- tabla de propiedades (se rellena en cada refresco)
        self.grupo_propiedades = Adw.PreferencesGroup(
            title="Propiedades del panel",
            description="Respuesta del panel a la peticion `POST conn`.")
        caja.append(self.grupo_propiedades)
        self._filas_propiedades = {}
        self._valores_propiedades = {}

        # --- controles
        grupo_control = Adw.PreferencesGroup(
            title="Control",
            description="Ordenes que se envian al panel; se ejecutan en un hilo.")

        ajuste = Gtk.Adjustment(value=100, lower=0, upper=100,
                                step_increment=1, page_increment=10)
        self.escala_brillo = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                       adjustment=ajuste)
        self.escala_brillo.set_size_request(240, -1)
        self.escala_brillo.set_draw_value(True)
        self.escala_brillo.set_tooltip_text("Brillo del panel (0-100)")
        boton_brillo = Gtk.Button(label="Aplicar")
        boton_brillo.set_valign(Gtk.Align.CENTER)
        boton_brillo.connect("clicked", self._aplicar_brillo)

        fila_brillo = fila_accion(
            "Brillo", "0 lo deja negro; usa Despertar para recuperarlo.",
            icono="display-brightness-symbolic")
        fila_brillo.add_suffix(self.escala_brillo)
        fila_brillo.add_suffix(boton_brillo)
        grupo_control.add(fila_brillo)
        self.fila_brillo = fila_brillo

        self.combo_rotacion = Adw.ComboRow(
            title="Rotacion",
            subtitle="Grados que gira la imagen en el panel.",
            model=Gtk.StringList.new(["0", "90", "180", "270"]))
        self.combo_rotacion.set_icon_name("object-rotate-right-symbolic")
        self.combo_rotacion.connect("notify::selected", self._cambiar_rotacion)
        grupo_control.add(self.combo_rotacion)

        self.interruptor_no_dormir = Adw.SwitchRow(
            title="No dormir",
            subtitle="Conmuta displayInSleep (mostrar en reposo); su efecto sobre el "
                     "apagado no esta claro. Lo que de verdad lo evita es trafico periodico.")
        self.interruptor_no_dormir.set_icon_name("weather-clear-night-symbolic")
        self.interruptor_no_dormir.connect("notify::active", self._cambiar_no_dormir)
        grupo_control.add(self.interruptor_no_dormir)

        self.interruptor_keepalive = Adw.SwitchRow(
            title="Mantener el panel despierto",
            subtitle="Trafico periodico para que la imagen no se apague a ~1 minuto. "
                     "Solo actua sin dashboard ni video: ellos ya generan trafico.")
        self.interruptor_keepalive.set_icon_name("coffee-symbolic")
        self.interruptor_keepalive.set_active(bool(self._config.get("keepalive", False)))
        self.interruptor_keepalive.connect("notify::active", self._cambiar_keepalive)
        grupo_control.add(self.interruptor_keepalive)

        self.fila_acciones = fila_accion("Acciones",
                                         "Refresca el estado o despierta el panel.",
                                         icono="system-run-symbolic")
        boton_refrescar = Gtk.Button(label="Refrescar")
        boton_refrescar.set_valign(Gtk.Align.CENTER)
        boton_refrescar.set_tooltip_text("Vuelve a leer el estado del panel.")
        boton_refrescar.connect("clicked", self._refrescar_estado)
        # Reconectar: cierra el hidraw y vuelve a resolver el panel. Es lo que hace falta
        # cuando el panel se ha desconectado o ha vuelto con otro /dev/hidrawN.
        self.boton_reconectar = Gtk.Button(label="Reconectar")
        self.boton_reconectar.set_valign(Gtk.Align.CENTER)
        self.boton_reconectar.set_tooltip_text(
            "Cierra el panel y vuelve a buscarlo (util si se ha desconectado o ha cambiado "
            "de /dev/hidrawN).")
        self.boton_reconectar.connect("clicked", self._reconectar_accion)
        boton_despertar = Gtk.Button(label="Despertar")
        boton_despertar.set_valign(Gtk.Align.CENTER)
        boton_despertar.add_css_class("suggested-action")
        boton_despertar.connect("clicked", self._despertar)
        self.fila_acciones.add_suffix(boton_refrescar)
        self.fila_acciones.add_suffix(self.boton_reconectar)
        self.fila_acciones.add_suffix(boton_despertar)
        grupo_control.add(self.fila_acciones)
        caja.append(grupo_control)

        # --- mantenimiento: recovery (reinicia el panel y apaga la OSD; NO borra medios)
        grupo_mantenimiento = Adw.PreferencesGroup(
            title="Mantenimiento del panel",
            description="Reinicio del panel: apaga la capa OSD y restaura el fondo; no "
                        "borra medios.")
        self.fila_recovery = fila_accion(
            "Recovery",
            "Reinicia el panel y apaga la OSD; no borra medios. Tarda 2-8 min.",
            icono="edit-clear-all-symbolic")
        self.boton_recovery = Gtk.Button(label="Recovery...")
        self.boton_recovery.add_css_class("destructive-action")
        self.boton_recovery.set_valign(Gtk.Align.CENTER)
        self.boton_recovery.set_tooltip_text(
            "Reinicia el panel y apaga la capa OSD. Pide confirmacion antes.")
        self.boton_recovery.connect("clicked", self._confirmar_recovery)
        self.fila_recovery.add_suffix(self.boton_recovery)
        grupo_mantenimiento.add(self.fila_recovery)
        caja.append(grupo_mantenimiento)

        self._anadir_pagina(pagina_desplazable(caja), "estado", "Estado",
                            "dialog-information-symbolic")

        # Primer refresco en cuanto la ventana este en pantalla.
        GLib.idle_add(self._refrescar_estado)

    # --- lectura del estado

    def _leer_estado(self):
        """Se ejecuta en un hilo: recoge todo lo de la pagina Estado.

        Se usa SIEMPRE el panel que devuelve el cerrojo (`with ... as panel`), no una
        variable capturada antes: si el panel se ha reconectado entre medias, el objeto
        viejo ya no vale (y usarlo reabriria el hidraw por detras del dueño compartido).
        """
        with self.panel.sesion() as panel:
            props = panel.propiedades_seguras()
            negociacion = [dict(x) for x in panel.canal.negociacion]
            variante = panel.canal.variante
            return {
                "ruta": panel.dispositivo,
                "props": props,
                "negociacion": negociacion,
                "variante": variante.describe() if variante is not None else "",
                "error_negociacion": self.panel.error_negociacion,
            }

    def _refrescar_estado(self, *_):
        self.fila_acciones.set_subtitle("Consultando el panel...")
        en_hilo(self._leer_estado, self._estado_recibido, self._estado_fallido, "estado")
        return False

    def _estado_fallido(self, exc, reintentar=True):
        self.espera_estado.set_visible(False)
        self.fila_acciones.set_subtitle("Sin conexion con el panel.")
        self.aviso_panel.set_title(_texto_error(exc))
        self.aviso_panel.set_revealed(True)
        for fila in (self.fila_nombre, self.fila_serie, self.fila_ids, self.fila_ruta,
                     self.fila_variante):
            fila.set_subtitle("-")
        self._pintar_propiedades({})
        self.aviso_brillo.set_revealed(False)
        self.aviso_arranque.set_revealed(False)
        self.aviso_osd.set_revealed(False)

        # Si el panel esta tomado por otro proceso, puede ser el servicio del dashboard:
        # se relee su estado (en hilo) y `_pintar_servicio` decide si sale el aviso.
        self._anotar_fallo_panel(exc)
        if not self._panel_ocupado:
            self.aviso_servicio.set_revealed(False)

        if self._panel_estuvo_ok:
            self.avisar_error(_texto_error(exc, corto=True))
        self._recuperar_panel(exc, reintentar)

    def _recuperar_panel(self, exc, reintentar=True):
        """Da el panel por perdido y, si procede, reintenta resolverlo.

        Un fallo hablando con el panel casi siempre significa que se ha desconectado, que ha
        vuelto con otro `/dev/hidrawN` o que lo tiene otro proceso. El `Panel` cacheado se
        invalida aqui mismo (sin bloquear: `invalidar()` solo levanta una bandera) y el
        reintento va en un hilo, que es quien vuelve a llamar a `Panel.reabrir()`.
        """
        self.panel.invalidar()
        self._anotar_fallo_panel(exc)
        if isinstance(exc, PanelOcupado):
            # Lo tiene el servicio: reconectar no arregla nada, manda el aviso del servicio.
            self._refrescar_servicio()
            return
        if self._panel_estuvo_ok and not self._avisado_desconexion:
            self._avisado_desconexion = True
            self._notificar("panel-desconectado", "Panel COUGAR CFV235 desconectado",
                            "La aplicacion ha perdido el panel: %s"
                            % _texto_error(exc, corto=True),
                            accion="app.reconectar", etiqueta_accion="Reconectar")
        if reintentar:
            self._reconectar()

    def _reconectar(self):
        """Cierra lo que hubiera y vuelve a resolver el panel (en un hilo).

        Si ya hay un reintento en marcha no se lanza otro a la vez: se apunta para repetirlo
        al terminar. Asi pulsar Reconectar mientras la aplicacion se esta recuperando sola
        no se pierde.
        """
        if self._reconectando:
            self._reconexion_pendiente = True
            return False
        self._reconectando = True
        self._reconexion_pendiente = False
        self.boton_reconectar.set_sensitive(False)
        self.fila_acciones.set_subtitle("Reconectando con el panel...")

        def tarea():
            panel = self.panel.reconectar()
            return {"ruta": panel.dispositivo, "props": panel.propiedades_seguras()}

        en_hilo(tarea, self._reconexion_hecha, self._reconexion_fallida, "reconectar")
        return False

    def _repetir_reconexion(self):
        """Si pidieron reconectar mientras se intentaba, se intenta otra vez."""
        if not self._reconexion_pendiente:
            return False
        self._reconexion_pendiente = False
        self._reconectar()
        return True

    def _reconexion_hecha(self, datos):
        self._reconectando = False
        self.boton_reconectar.set_sensitive(True)
        ruta = datos.get("ruta") or "?"
        if datos.get("props"):
            self.avisar("Panel reconectado en %s" % ruta)
        else:
            self.avisar_error("El panel (%s) sigue sin contestar a conn." % ruta)
        self._refrescar_estado()
        self._repetir_reconexion()
        return False

    def _reconexion_fallida(self, exc):
        self._reconectando = False
        self.boton_reconectar.set_sensitive(True)
        if self._repetir_reconexion():
            return False
        # Sin reintento automatico: si acaba de fallar, no se entra en un bucle.
        self._estado_fallido(exc, reintentar=False)
        return False

    def _esperar_arranque(self, *_):
        """Espera (en un hilo) a que el panel termine de arrancar: bootFinish=1.

        El panel tarda 60-80 s en arrancar tras un corte de alimentacion o un `recovery`, y
        mientras tanto contesta con bootFinish=0. `Panel.esperar_arranque` sondea cada 5 s.
        """
        if self._esperando_arranque:
            self.avisar("Ya se esta esperando a que arranque el panel.")
            return False
        self._esperando_arranque = True
        self.boton_reconectar.set_sensitive(False)
        self.fila_acciones.set_subtitle("Esperando a que arranque el panel...")

        def aviso(estado):
            GLib.idle_add(self.fila_acciones.set_subtitle,
                          "Esperando a que arranque el panel (bootFinish=%s)..." % (estado,))

        def tarea():
            # El cerrojo se toma solo para conseguir el panel; la espera (hasta 90 s) va
            # fuera, para que las otras paginas no se queden bloqueadas.
            panel = self.panel.abrir()
            return panel.esperar_arranque(max_segundos=90, cada=5, avisar=aviso)

        en_hilo(tarea, self._arranque_esperado, self._arranque_esperado_fallido,
                "esperar-arranque")
        return False

    def _arranque_esperado(self, arrancado):
        self._esperando_arranque = False
        self.boton_reconectar.set_sensitive(True)
        if arrancado:
            self.avisar("El panel ha arrancado (bootFinish=1)")
        else:
            self.avisar_error("El panel no ha arrancado en 90 s: pulsa Reconectar y "
                              "comprueba la alimentacion.")
        self._refrescar_estado()
        return False

    def _arranque_esperado_fallido(self, exc):
        self._esperando_arranque = False
        self.boton_reconectar.set_sensitive(True)
        self.fila_acciones.set_subtitle("No se pudo esperar al arranque.")
        self.avisar_error(_texto_error(exc, corto=True))

    # --- mantenimiento: recovery

    def _texto_recovery(self):
        """Aviso del recovery, en un solo sitio (lo ensena el dialogo y lo prueba el test)."""
        return ("Esto REINICIA el panel y apaga la capa OSD (osdState pasa a 0), pero "
                "NO borra los medios: medido, el espacio no se libera y el fondo vuelve "
                "al que tenia configurado antes.\n\n"
                "El panel desaparecera de la pantalla y del sistema entre 2 y 8 minutos. "
                "No lo desconectes ni le cortes la corriente mientras tanto.\n\n"
                "En este firmware no hay forma conocida de borrar medios.")

    def _confirmar_recovery(self, *_):
        """Recovery pide confirmacion: reinicia el panel y apaga la capa OSD."""
        dialogo = Adw.AlertDialog(
            heading="Recovery del panel",
            body=self._texto_recovery())
        dialogo.add_response("cancelar", "Cancelar")
        dialogo.add_response("recovery", "Reiniciar")
        dialogo.set_response_appearance("recovery", Adw.ResponseAppearance.DESTRUCTIVE)
        dialogo.set_close_response("cancelar")
        dialogo.choose(self, None, self._recovery_confirmado)

    def _recovery_confirmado(self, dialogo, resultado):
        try:
            respuesta = dialogo.choose_finish(resultado)
        except GLib.Error:
            return                                    # se cerro sin elegir
        except Exception:                             # noqa: BLE001
            return
        if respuesta == "recovery":
            self._hacer_recovery()

    def _hacer_recovery(self):
        """Manda `recovery` en un hilo y espera a que el panel vuelva solo."""
        if self._recovery_en_curso:
            self.avisar("Ya hay un recovery en marcha.")
            return False
        self._recovery_en_curso = True
        self.boton_recovery.set_sensitive(False)
        self.fila_recovery.set_subtitle("Enviando recovery: el panel se reiniciara...")

        def aviso(estado):
            GLib.idle_add(self.fila_recovery.set_subtitle,
                          "Esperando a que el panel vuelva (bootFinish=%s)..." % (estado,))

        def tarea():
            try:
                with self.panel.sesion() as panel:
                    respuesta = panel.recovery()
                    code = getattr(respuesta, "code", None)
            except ErrorCanal as exc:
                return {"code": None, "arrancado": False, "ruta": "",
                        "error": _texto_error(exc, corto=True)}
            # El panel se ha ido: se resuelve otra vez (Panel.reabrir respeta la ruta) y se
            # espera a que termine de arrancar.
            try:
                panel = self.panel.reconectar()
            except ErrorCanal as exc:
                return {"code": code, "arrancado": False, "ruta": "",
                        "error": _texto_error(exc, corto=True)}
            arrancado = panel.esperar_arranque(max_segundos=90, cada=5, avisar=aviso)
            return {"code": code, "arrancado": arrancado, "ruta": panel.dispositivo,
                    "error": ""}

        en_hilo(tarea, self._recovery_hecho, self._recovery_fallido, "recovery")
        return False

    def _recovery_hecho(self, datos):
        self._recovery_en_curso = False
        self.boton_recovery.set_sensitive(True)
        if datos.get("error"):
            self.fila_recovery.set_subtitle("Recovery enviado, pero el panel no ha vuelto: %s"
                                            % datos["error"])
            self.avisar_error("Recovery enviado, pero el panel no ha vuelto: %s"
                              % datos["error"])
        elif datos.get("arrancado"):
            self.fila_recovery.set_subtitle(
                "Recovery hecho: el panel ha vuelto en %s y esta arrancado."
                % (datos.get("ruta") or "?"))
            self.avisar("Recovery hecho: el panel ha vuelto (todos los medios borrados)")
        else:
            self.fila_recovery.set_subtitle(
                "Recovery hecho, pero el panel (%s) no ha terminado de arrancar."
                % (datos.get("ruta") or "?"))
            self.avisar_error("Recovery hecho, pero el panel no ha terminado de arrancar: "
                              "usa Esperar a que arranque.")
        self._refrescar_estado()
        self._refrescar_servicio()
        return False

    def _recovery_fallido(self, exc):
        self._recovery_en_curso = False
        self.boton_recovery.set_sensitive(True)
        self.fila_recovery.set_subtitle("El recovery fallo: %s" % _texto_error(exc, corto=True))
        self._recuperar_panel(exc, reintentar=False)
        self.avisar_error("El recovery fallo: %s" % _texto_error(exc, corto=True))

    def _anotar_fallo_panel(self, exc):
        """Recuerda si el ultimo fallo fue por tener el panel otro proceso.

        Lo usa el aviso de la pagina Estado: si el sospechoso es el servicio, alli sale el
        boton para pararlo aunque el fallo haya venido de otra pagina (Imagen, Temas...).
        """
        self._panel_ocupado = isinstance(exc, PanelOcupado)
        if self._panel_ocupado:
            self._refrescar_servicio()

    def _estado_recibido(self, datos):
        self.espera_estado.set_visible(False)
        ruta = datos.get("ruta")
        props = datos.get("props") or {}
        ficha = datos_del_dispositivo(ruta)

        # Si el panel se ha podido abrir, ya no lo tiene el servicio: el aviso sobra.
        self._panel_ocupado = False
        if self.aviso_servicio.get_revealed():
            self.aviso_servicio.set_revealed(False)

        self.fila_nombre.set_subtitle(ficha.get("nombre") or "(sin nombre)")
        self.fila_serie.set_subtitle(ficha.get("serie") or "(sin serie)")
        vid, pid = ficha.get("vid"), ficha.get("pid")
        self.fila_ids.set_subtitle(
            "%04x:%04x" % (vid, pid) if isinstance(vid, int) and isinstance(pid, int) else "-")
        self.fila_ruta.set_subtitle(ruta or "-")

        variante = datos.get("variante") or "(sin negociar)"
        if datos.get("error_negociacion"):
            variante = "%s  [%s]" % (variante, datos["error_negociacion"])
        self.fila_variante.set_subtitle(variante)

        if props:
            # El panel contesta: se olvida cualquier desconexion anterior.
            self.panel.anotar_conn_ok()
            self._panel_estuvo_ok = True
            self._avisado_desconexion = False
            self._props_estado = props
            self.aviso_panel.set_revealed(False)
            self.fila_acciones.set_subtitle(
                "Ultima consulta correcta. %d propiedades." % len(props))
        else:
            self.aviso_panel.set_title(
                "El panel esta abierto pero no contesta a `conn` (0 propiedades).")
            self.aviso_panel.set_revealed(True)
            self.fila_acciones.set_subtitle("El panel no contesta.")
            # Varios `conn` seguidos con 0 propiedades = el panel se ha desconectado (o ha
            # vuelto con otro /dev/hidrawN): se invalida y se resuelve de nuevo.
            if self.panel.anotar_conn_vacio():
                self.aviso_panel.set_title(
                    "El panel esta abierto pero no contesta a `conn` (0 propiedades). "
                    "Se reintenta la conexion.")
                self._recuperar_panel(
                    ErrorCanal("el panel no contesta a conn (0 propiedades)"))

        self._pintar_propiedades(props)
        self._pintar_vista_previa()

        # Avisos destacados.
        self.aviso_brillo.set_revealed(props.get("brightness") == 0)
        self.aviso_arranque.set_revealed(props.get("bootFinish") == 0)
        self.aviso_osd.set_revealed(props.get("osdState") == 1)

        # Controles, sin disparar ordenes.
        self._silenciar = True
        try:
            brillo = props.get("brightness")
            if isinstance(brillo, (int, float)):
                self.escala_brillo.set_value(float(brillo))
            grado = props.get("degree")
            if isinstance(grado, int):
                if grado in GRADOS_ROTACION:
                    self.combo_rotacion.set_selected(GRADOS_ROTACION.index(grado))
            # El panel manda `displayInSleep` como ENTERO (0/1), no como booleano: exigir
            # `isinstance(dormir, bool)` hacia que el interruptor no reflejara nunca el
            # estado real. Se aceptan numeros, igual que con brightness o degree.
            # `displayInSleep=1` significa "sigue mostrando en reposo", o sea NO dormir: el
            # interruptor se enciende con 1 (antes se ponia al reves y parecia que "no dormir"
            # estaba activado justo cuando el panel se apagaba).
            dormir = props.get("displayInSleep")
            if isinstance(dormir, (bool, int, float)):
                self.interruptor_no_dormir.set_active(bool(dormir))
        finally:
            self._silenciar = False

    def _pintar_propiedades(self, props):
        """Rellena la tabla de propiedades: nombre legible, clave tecnica y valor a la derecha.

        Antes cada fila ponia la clave como prefijo, la etiqueta "clave (descripcion)" como
        titulo y el VALOR escondido en el subtitulo, asi que se leia "bootFinish | bootFinish
        (panel arrancado) | 1" y el dato costaba encontrarlo. Ahora:

            Panel arrancado                    <- titulo, en lenguaje natural
            bootFinish                         <- subtitulo, la clave tecnica en monoespaciado
                                       1       <- sufijo, el valor destacado
        """
        for fila in self._filas_propiedades.values():
            self.grupo_propiedades.remove(fila)
        self._filas_propiedades = {}
        self._valores_propiedades = {}

        claves = [c for c in ORDEN_PROPIEDADES if c in props]
        claves += sorted(c for c in props if c not in ORDEN_PROPIEDADES)

        if not claves:
            fila = Adw.ActionRow(title="Sin datos",
                                 subtitle="No se pudo leer ninguna propiedad del panel.")
            self.grupo_propiedades.add(fila)
            self._filas_propiedades["(sin datos)"] = fila
            return

        for clave in claves:
            fila = Adw.ActionRow(title=titulo_propiedad(clave))
            fila.set_subtitle(clave)
            fila.add_css_class("cfv-propiedad")
            valor = Gtk.Label(label=formatear_propiedad(clave, props.get(clave)),
                              css_classes=["cfv-valor-propiedad"],
                              valign=Gtk.Align.CENTER, xalign=1.0)
            fila.add_suffix(valor)
            if clave in ("bootFinish", "osdState", "logo", "mode"):
                # Son estados de un solo digito: mejor una pildora de color que un numero.
                valor.add_css_class("cfv-pildora")
                valor.add_css_class(clase_pildora(clave, props.get(clave)))
            self.grupo_propiedades.add(fila)
            self._filas_propiedades[clave] = fila
            self._valores_propiedades[clave] = valor

    def _recordar_enviado(self, datos, nombre):
        """Guarda el ultimo contenido enviado al panel, para la vista previa."""
        if not datos:
            return
        self._ultimo_enviado = datos
        self._ultimo_enviado_nombre = nombre or ""
        with contextlib.suppress(Exception):
            self._pintar_vista_previa()

    def _pintar_vista_previa(self):
        """Ensena en la tarjeta el ultimo contenido enviado (o el dashboard previsto)."""
        if not hasattr(self, "vista_previa_panel"):
            return
        datos = getattr(self, "_ultimo_enviado", None)
        if datos:
            poner_imagen(self.vista_previa_panel, datos)
            kb = max(1, len(datos) // 1024)
            self.pie_vista.set_text(
                f"Ultimo envio: {self._ultimo_enviado_nombre or '(sin nombre)'}  -  {kb} KB")
            return
        # Nada enviado todavia: se dibuja el dashboard con los sensores de ahora mismo, que
        # es lo mas parecido a "lo que se veria" y no depende de que el panel conteste.
        try:
            # OJO: los cargadores devuelven (modulo, error). Antes se usaba la tupla como si
            # fuera el modulo, asi que esto fallaba siempre y la vista previa se quedaba en el
            # mensaje de reserva sin ensenar nunca el dashboard.
            modulo, _error = cargar_dashboard()
            clase_sensores, _error2 = cargar_sensores()
            modulo_temas, _error3 = cargar_temas()
            if modulo is None or clase_sensores is None or modulo_temas is None:
                raise RuntimeError("faltan modulos para dibujar el dashboard")
            sensores = clase_sensores()
            sensores.muestra()
            time.sleep(0.2)
            valores = sensores.muestra()
            tema = modulo.tema_dashboard(valores=valores)
            datos = modulo_temas.renderizar_datos(tema, valores)
            poner_imagen(self.vista_previa_panel, datos)
            self.pie_vista.set_text(
                "Aun no se ha enviado nada al panel: asi quedaria el dashboard ahora mismo.")
        except Exception:                             # noqa: BLE001
            self.vista_previa_panel.set_paintable(None)
            self.pie_vista.set_text(
                "Aun no se ha enviado nada al panel. Sube una imagen, aplica un tema o "
                "arranca el dashboard en vivo.")

    def volcado_ui(self):
        """Describe en texto TODA la interfaz: paginas, grupos, filas, textos y estados.

        Es la forma de revisar la app sin mirarla: en vez de capturas de pantalla, el
        programa cuenta lo que ensena. Lo usan las pruebas y el modo `--informe`:

            python3 -m cfv235_gtk --informe /tmp/ui.txt
        """
        lineas = ["VENTANA: %s   %dx%d   tema=%s" % (self.get_title(), self.get_width(),
                                                     self.get_height(),
                                                     estilo.esquema_actual())]
        # `Adw.ViewStack` no es un Gtk.Notebook: las paginas se piden a su lista.
        paginas = self.pila.get_pages()
        visible = self.pila.get_visible_child_name()
        for indice in range(paginas.get_n_items()):
            pagina = paginas.get_item(indice)
            nombre = pagina.get_name()
            titulo = pagina.get_title() or nombre
            hijo = pagina.get_child()
            marca = "   <-- visible" if nombre == visible else ""
            lineas.append("")
            lineas.append("== PAGINA %s: %s%s" % (nombre, titulo, marca))
            if hijo is not None:
                lineas.extend(describir_ui(hijo, 1))
        return "\n".join(lineas)

    def volcado_estado(self):
        """Texto con lo que la pagina Estado esta mostrando (para pruebas y soporte)."""
        lineas = []
        for titulo, fila in (("nombre", self.fila_nombre), ("serie", self.fila_serie),
                             ("vid:pid", self.fila_ids), ("ruta", self.fila_ruta),
                             ("variante", self.fila_variante),
                             ("acciones", self.fila_acciones)):
            lineas.append("%-10s %s" % (titulo + ":", fila.get_subtitle() or "-"))
        lineas.append("propiedades:")
        for clave in self._filas_propiedades:
            etiqueta = self._valores_propiedades.get(clave)
            texto = etiqueta.get_label() if etiqueta is not None else "-"
            lineas.append("    %-18s %s" % (clave, texto))
        for nombre, aviso in (("panel", self.aviso_panel),
                              ("servicio", self.aviso_servicio),
                              ("brillo", self.aviso_brillo),
                              ("arranque", self.aviso_arranque), ("osd", self.aviso_osd)):
            if aviso.get_revealed():
                lineas.append("AVISO %-9s %s" % (nombre + ":", aviso.get_title()))
        return "\n".join(lineas)

    # --- ordenes del panel

    def _aplicar_brillo(self, *_):
        valor = int(self.escala_brillo.get_value())
        self._orden("brillo a %d" % valor, lambda panel: panel.brillo(valor),
                    exito="Brillo aplicado: %d %%" % valor, fila=self.fila_brillo)
        return False

    def _despertar(self, *_):
        """power resume + brillo: recupera un panel que se ve negro."""
        valor = int(self.escala_brillo.get_value()) or 100
        if self.escala_brillo.get_value() == 0:
            self.escala_brillo.set_value(100)
            valor = 100

        def tarea(panel):
            respuesta_power = panel.power("resume")
            respuesta_brillo = panel.brillo(valor)
            return (respuesta_power, respuesta_brillo, valor)

        def hecho(datos):
            respuesta_power, respuesta_brillo, brillo = datos
            # El panel contesta 400 cuando no acepta la orden: antes se decia "despertado"
            # pasara lo que pasara. Se mira el code de las DOS respuestas.
            if getattr(respuesta_power, "ok", False) and getattr(respuesta_brillo, "ok", False):
                texto = ("Panel despertado (power code=%s, brillo %d code=%s)"
                         % (respuesta_power.code, brillo, respuesta_brillo.code))
                self.avisar(texto)
                self.fila_acciones.set_subtitle(texto)
            else:
                texto = ("El panel no acepto despertar: power code=%s, brillo code=%s"
                         % (respuesta_power.code, respuesta_brillo.code))
                self.avisar_error(texto)
                self.fila_acciones.set_subtitle(texto)
            self._refrescar_estado()

        self.fila_acciones.set_subtitle("Despertando el panel...")
        en_hilo(lambda: self.panel.usar(tarea), hecho, self._orden_fallida, "despertar")
        return False

    def _cambiar_rotacion(self, fila, _parametro=None):
        if self._silenciar:
            return
        # El combo puede llegar sin seleccion (-1) al rellenarlo: indexar sin comprobar
        # lanzaba IndexError y la orden se quedaba a medias.
        indice = fila.get_selected()
        if not 0 <= indice < len(GRADOS_ROTACION):
            return
        grados = GRADOS_ROTACION[indice]
        self._orden("rotacion a %d" % grados, lambda panel: panel.girar(grados),
                    exito="Rotacion aplicada: %d grados" % grados, fila=fila)

    # ------------------------------------------------------------------ keepalive

    INTERVALO_KEEPALIVE = 25.0   # medido: el panel se apaga ~1 min sin trafico

    def _cambiar_keepalive(self, fila, _parametro=None):
        activo = fila.get_active()
        self._guardar(keepalive=bool(activo))
        if activo:
            self._arrancar_keepalive()
            self.avisar("Keepalive activado: el panel se mantiene despierto.")
        else:
            self._parar_hilo_keepalive()
            self.avisar("Keepalive desactivado.")

    def _arrancar_keepalive(self):
        """Arranca el hilo del keepalive (si no esta ya)."""
        if self._keepalive_activo:
            return
        self._keepalive_activo = True
        self._parar_keepalive = threading.Event()
        self._hilo_keepalive = threading.Thread(
            target=self._bucle_keepalive, name="keepalive", daemon=True)
        self._hilo_keepalive.start()

    def _parar_hilo_keepalive(self):
        self._keepalive_activo = False
        if self._parar_keepalive is not None:
            self._parar_keepalive.set()
        # NO se hace join() aqui: si alguien llama desde el propio hilo se colgaria.

    def _bucle_keepalive(self):
        """Telemetria cada INTERVALO_KEEPALIVE sin dashboard ni video en marcha.

        Usa `compartido.sesion()` en cada trama (nunca un Panel capturado): si el panel
        se reconecta en otro /dev/hidrawN, la trama siguiente va al objeto nuevo. Los
        errores se ignoran — el keepalive es oportunista, el estado lo pinta la pagina
        Estado. Si el dashboard o el video se ponen en marcha, el hilo se queda dormido
        esperando a que terminen (ellos ya generan trafico).
        """
        parar = self._parar_keepalive
        while parar is not None and not parar.wait(self.INTERVALO_KEEPALIVE):
            if self._dashboard_activo or self._video_activo:
                continue
            try:
                from cfv235.panel import telemetria_desde_sensores
                Sensores, _error = cargar_sensores()
                datos = None
                if Sensores is not None:
                    try:
                        datos = telemetria_desde_sensores(Sensores(intervalo=1.0).muestra())
                    except Exception:             # noqa: BLE001
                        datos = None
                with self.panel.sesion() as panel:
                    with escritura_fiable(panel):
                        panel.telemetria(datos)
                self._keepalive_tramas += 1
            except Exception:                     # noqa: BLE001
                # Sin panel u ocupado: se reintenta en el proximo ciclo.
                continue

    def _cambiar_no_dormir(self, fila, _parametro=None):
        if self._silenciar:
            return
        activo = fila.get_active()
        self._orden("no dormir %s" % ("activado" if activo else "desactivado"),
                    lambda panel: panel.no_dormir(activo),
                    exito="No dormir %s" % ("activado" if activo else "desactivado"),
                    fila=fila)

    def _orden(self, descripcion, tarea, exito=None, fila=None):
        """Lanza una orden corta al panel y refresca el estado al terminar.

        `exito` es el texto del toast de confirmacion; si no se da, se ensena el code. La
        respuesta se mira con `.ok`: el panel contesta **400** cuando rechaza una orden, y
        antes se anunciaba "Brillo aplicado: 100 %" aunque hubiera dicho que no.
        """
        self.fila_acciones.set_subtitle("Enviando %s..." % descripcion)

        def hecho(respuesta):
            destino = fila if fila is not None else self.fila_acciones
            if getattr(respuesta, "ok", False):
                texto = exito or ("%s: code=%s" % (descripcion,
                                                   getattr(respuesta, "code", "?")))
                self.avisar(texto)
            else:
                texto = ("El panel rechazo %s (code=%s)"
                         % (descripcion, getattr(respuesta, "code", "?")))
                self.avisar_error(texto)
            # El resultado queda tambien en el subtitulo de la fila (accesibilidad: el toast
            # desaparece y el lector de pantalla no lo lee).
            with contextlib.suppress(Exception):
                destino.set_subtitle(texto)
            self._refrescar_estado()

        en_hilo(lambda: self.panel.usar(tarea), hecho, self._orden_fallida, "orden")

    def _orden_fallida(self, exc):
        self.fila_acciones.set_subtitle("La orden fallo.")
        self.aviso_panel.set_title(_texto_error(exc))
        self.aviso_panel.set_revealed(True)
        self.avisar_error(_texto_error(exc, corto=True))
        self._recuperar_panel(exc)

    # ------------------------------------------------------------------ pagina 2: Imagen

    def _pagina_imagen(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        grupo = Adw.PreferencesGroup(
            title="Subir una imagen al panel",
            description="El panel es 1920x462. Acepta PNG, JPEG y GIF (el GIF se ve "
                        "en blanco: mejor PNG).")

        self.fila_fichero = fila_accion("Fichero", "(ninguno)",
                                        icono="image-x-generic-symbolic")
        boton_elegir = Gtk.Button(label="Elegir imagen...")
        boton_elegir.set_valign(Gtk.Align.CENTER)
        boton_elegir.set_tooltip_text("Busca un PNG, JPEG o GIF en el equipo.")
        boton_elegir.connect("clicked", self._elegir_imagen)
        self.fila_fichero.add_suffix(boton_elegir)
        grupo.add(self.fila_fichero)

        # El panel NO escala lo que le mandas: dibuja la imagen a su tamano y REPITE lo que
        # falta (mosaico). Una foto de 1024x240 en una pantalla de 1920x462 sale dos veces y
        # con la segunda cortada, que es justo lo que le paso a la primera prueba.
        self.interruptor_ajustar = Adw.SwitchRow(
            title="Ajustar al tamano del panel",
            subtitle="El panel no escala: si la imagen no es 1920x462, la repite.")
        self.interruptor_ajustar.set_icon_name("transform-scale-symbolic")
        self.interruptor_ajustar.set_active(bool(self._config.get("ajustar_imagen", True)))
        self.interruptor_ajustar.connect("notify::active", self._cambiar_ajustar_imagen)
        grupo.add(self.interruptor_ajustar)

        self.combo_ajuste_imagen = Adw.ComboRow(
            title="Como ajustarla",
            subtitle="ajustar: entera con bandas - recortar: llena y recorta - estirar: deforma",
            model=Gtk.StringList.new(["ajustar", "recortar", "estirar"]))
        self.combo_ajuste_imagen.set_icon_name("object-flip-horizontal-symbolic")
        ajuste_guardado = self._config.get("ajuste_imagen") or "ajustar"
        indices = {"ajustar": 0, "recortar": 1, "estirar": 2}
        self.combo_ajuste_imagen.set_selected(indices.get(ajuste_guardado, 0))
        self.combo_ajuste_imagen.connect("notify::selected", self._cambiar_ajuste_imagen)
        grupo.add(self.combo_ajuste_imagen)

        self.combo_ajuste_imagen.set_visible(self.interruptor_ajustar.get_active())
        self.combo_capa = Adw.ComboRow(
            title="Capa de destino",
            subtitle="El fondo acumula ficheros y gasta espacio; la capa OSD reutiliza "
                     "su hueco y NO acumula.",
            model=Gtk.StringList.new(["fondo", "OSD"]))
        self.combo_capa.set_icon_name("view-grid-symbolic")
        # La capa elegida se recuerda entre arranques.
        self.combo_capa.set_selected(0 if self._config.get("capa") != "osd" else 1)
        self.combo_capa.connect("notify::selected", self._cambiar_capa)
        grupo.add(self.combo_capa)

        self.fila_espacio = fila_accion("Espacio libre en el panel", "-",
                                        icono="drive-harddisk-symbolic")
        grupo.add(self.fila_espacio)

        self.fila_subir = fila_accion("Subir", "Envia el fichero elegido al panel.",
                                      icono="document-save-symbolic")
        self.spinner_subida = Gtk.Spinner()
        self.spinner_subida.set_valign(Gtk.Align.CENTER)
        self.spinner_subida.set_visible(False)
        self.boton_subir = Gtk.Button(label="Subir")
        self.boton_subir.add_css_class("suggested-action")
        self.boton_subir.set_valign(Gtk.Align.CENTER)
        self.boton_subir.set_tooltip_text("Elige antes una imagen.")
        self.boton_subir.connect("clicked", self._subir_imagen)
        self.fila_subir.add_suffix(self.spinner_subida)
        self.fila_subir.add_suffix(self.boton_subir)
        grupo.add(self.fila_subir)

        caja.append(grupo)

        # Previsualizacion
        grupo_previa = Adw.PreferencesGroup(title="Previsualizacion",
                                            description="Miniatura del fichero elegido "
                                                        "(no se decodifica la imagen entera).")
        self.imagen_previa = Gtk.Picture()
        self.imagen_previa.set_can_shrink(True)
        self.imagen_previa.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.imagen_previa.set_size_request(-1, 220)
        marco_previa = Gtk.Frame()
        marco_previa.add_css_class("view")
        marco_previa.set_child(self.imagen_previa)
        # Explica que se esta ensenando (miniatura) o por que no hay previsualizacion.
        self.etiqueta_previa_imagen = Gtk.Label(xalign=0, wrap=True)
        self.etiqueta_previa_imagen.set_max_width_chars(90)
        self.etiqueta_previa_imagen.add_css_class("dim-label")
        self.etiqueta_previa_imagen.set_text("Elige una imagen para ver la miniatura.")
        etiqueta_accesible(self.etiqueta_previa_imagen, "Estado de la previsualizacion")
        caja.append(grupo_previa)
        caja.append(self.etiqueta_previa_imagen)
        caja.append(marco_previa)

        # Resultado
        grupo_resultado = Adw.PreferencesGroup(
            title="Resultado de la subida",
            description="transport, acuse de bloques y transported.")
        marco, vista = vista_de_texto(160)
        self.texto_subida = vista
        poner_texto(vista, "Todavia no se ha subido nada.")
        caja.append(grupo_resultado)
        caja.append(marco)

        # Antes de elegir fichero la pagina ensena un estado vacio con el boton a mano.
        boton_vacio = Gtk.Button(label="Elegir imagen...")
        boton_vacio.add_css_class("pill")
        boton_vacio.add_css_class("suggested-action")
        boton_vacio.connect("clicked", self._elegir_imagen)
        self.pagina_imagen = apilar(
            ("vacio", estado_vacio(
                "Sin imagen elegida",
                "Elige un PNG de 1920x462 y subelo a la capa OSD. La previsualizacion y el "
                "detalle de la subida aparecen aqui mismo.",
                "image-x-generic-symbolic", boton_vacio)),
            ("contenido", pagina_desplazable(caja)))
        self.pagina_imagen.set_visible_child_name("vacio")

        self._anadir_pagina(self.pagina_imagen, "imagen", "Imagen",
                            "image-x-generic-symbolic")

        # El boton Subir no tiene sentido sin fichero: apagado y con el motivo a la vista.
        self._actualizar_acciones_imagen()

    def _actualizar_acciones_imagen(self):
        """Habilita Subir solo cuando hay fichero, y explica el motivo si no lo hay."""
        hay = bool(self._imagen)
        self.boton_subir.set_sensitive(hay)
        if hay:
            nombre = os.path.basename(self._imagen)
            self.fila_subir.set_subtitle("Envia %s al panel." % nombre)
            self.boton_subir.set_tooltip_text("Envia %s al panel." % nombre)
        else:
            self.fila_subir.set_subtitle("Elige antes una imagen: sin fichero no hay nada "
                                         "que subir.")
            self.boton_subir.set_tooltip_text("Elige antes una imagen.")

    def _elegir_imagen(self, *_):
        abrir_dialogo_fichero(self, "Elegir imagen para el panel", self._imagen_elegida,
                              carpeta=self._config.get("carpeta_imagen") or "",
                              filtros=(filtro_imagenes(), filtro_todos()))
        return False

    def _imagen_elegida(self, ruta):
        """Recibe la ruta elegida (o None si se cerro el dialogo)."""
        if not ruta:
            return
        if not os.path.exists(ruta):
            self.avisar("Solo se pueden subir ficheros locales.")
            return
        self._aplicar_imagen(ruta)

    def _imagen_ajustada(self, ruta, modo):
        """PNG de la imagen escalado a 1920x462, o None si no se puede (se sube el original).

        Las imagenes enormes no se tocan: decodificarlas para escalarlas costaria cientos de
        MB, y para eso ya esta el aviso de "imagen muy grande".
        """
        tamano = self._tamano_imagen or 0
        if tamano > AVISO_IMAGEN_GRANDE:
            return None
        video, error = cargar_video()
        if video is None:
            return None
        try:
            with Image.open(ruta) as original:
                ajustada = video.ajustar_imagen(original, modo)
            buffer = io.BytesIO()
            ajustada.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
        except Exception:                             # noqa: BLE001
            return None

    def _cambiar_ajustar_imagen(self, fila, _parametro=None):
        """Guarda si hay que escalar antes de subir y refresca los avisos."""
        if getattr(self, "_silenciar", False):
            return
        self._guardar(ajustar_imagen=bool(fila.get_active()))
        self._actualizar_aviso_ajuste()
        self.combo_ajuste_imagen.set_visible(bool(fila.get_active()))

    def _cambiar_ajuste_imagen(self, fila, _parametro=None):
        if getattr(self, "_silenciar", False):
            return
        nombres = ("ajustar", "recortar", "estirar")
        indice = fila.get_selected()
        if 0 <= indice < len(nombres):
            self._guardar(ajuste_imagen=nombres[indice])
            self._actualizar_aviso_ajuste()

    def _ajuste_imagen_elegido(self):
        """(ajustar, modo) segun los controles de la pagina Imagen."""
        nombres = ("ajustar", "recortar", "estirar")
        indice = self.combo_ajuste_imagen.get_selected()
        modo = nombres[indice] if 0 <= indice < len(nombres) else "ajustar"
        return bool(self.interruptor_ajustar.get_active()), modo

    def _actualizar_aviso_ajuste(self):
        """Explica en la propia fila que le pasara a ESTA imagen."""
        if not self._imagen:
            self.interruptor_ajustar.set_subtitle(
                "El panel no escala: si la imagen no es 1920x462, la repite.")
            return
        ancho, alto = dimensiones_imagen(self._imagen)
        if not ancho or not alto:
            return
        modulo_temas, _error = cargar_temas()
        if modulo_temas is None:
            return
        ancho_panel, alto_panel = modulo_temas.ANCHO, modulo_temas.ALTO
        activo, modo = self._ajuste_imagen_elegido()
        if (ancho, alto) == (ancho_panel, alto_panel):
            self.interruptor_ajustar.set_subtitle(
                "La imagen ya es 1920x462: no hace falta tocar nada.")
            return
        veces_x = -(-ancho_panel // ancho)          # division hacia arriba
        veces_y = -(-alto_panel // alto)
        repeticiones = veces_x * veces_y
        if repeticiones <= 1:
            cuanto = "cabe entera"
        elif repeticiones == 2:
            cuanto = "saldria repetida 2 veces"
        else:
            cuanto = "saldria repetida %d veces (%d x %d)" % (repeticiones, veces_x, veces_y)
        if activo:
            self.interruptor_ajustar.set_subtitle(
                "Tu imagen es %dx%d: %s. Se ajustara (%s) a %dx%d antes de subirla."
                % (ancho, alto, cuanto, modo, ancho_panel, alto_panel))
        else:
            self.interruptor_ajustar.set_subtitle(
                "Tu imagen es %dx%d y el panel la dibuja a su tamano: %s, cortando la ultima. "
                "Activa esto para ajustarla." % (ancho, alto, cuanto))

    def _aplicar_imagen(self, ruta):
        """Valida la imagen elegida y prepara la miniatura (sin abrir el dialogo).

        Se prueba por separado del `Gtk.FileDialog`; es donde estaban los dos problemas:
        no se validaba NADA antes de leer y la previsualizacion decodificaba la imagen
        entera (una de 6000x6000 subio el RSS mas de 100 MB).
        """
        self._guardar(carpeta_imagen=os.path.dirname(ruta) or "")
        try:
            tamano = os.path.getsize(ruta)
        except OSError as exc:
            self._descartar_imagen("No se pudo leer el fichero: %s"
                                   % _texto_error(exc, corto=True))
            return
        if tamano > TAMANO_MAXIMO_IMAGEN:
            self._descartar_imagen(
                "La imagen ocupa %.1f MB y el maximo que admite el panel es %.0f MB."
                % (tamano / 1048576.0, TAMANO_MAXIMO_IMAGEN / 1048576.0))
            return
        tipo = tipo_de_fichero(ruta)                  # lee solo los primeros bytes
        if tipo is None:
            self._descartar_imagen("El fichero no es PNG, JPEG ni GIF.")
            return

        self._imagen = ruta
        self._tamano_imagen = tamano
        ancho, alto = dimensiones_imagen(ruta)        # solo la cabecera

        megapixeles = (ancho or 0) * (alto or 0)
        if tamano > AVISO_IMAGEN_GRANDE or megapixeles > MEGAPIXELES_PREVIA:
            # Ni se decodifica: la miniatura de 960 px no merece 100 MB de RAM, y la
            # imagen se sube tal cual (el panel la recibe byte a byte).
            motivo = ("Imagen muy grande (%s): se sube tal cual, sin miniatura, para no "
                      "gastar memoria." % describir_imagen(ancho, alto, tamano))
            self._sin_miniatura(motivo)
            self.avisar_largo("Imagen muy grande (%s). %s"
                              % (describir_imagen(ancho, alto, tamano),
                                 "Se subira al panel tal cual; la previsualizacion se omite "
                                 "para no gastar memoria."))
        else:
            self._miniatura = None
            self.etiqueta_previa_imagen.set_text("Generando la miniatura...")
            en_hilo(lambda: self._miniatura_png(ruta), self._miniatura_hecha,
                    self._miniatura_fallida, "miniatura")
            self.avisar("Imagen elegida: %s" % os.path.basename(ruta))

        self.fila_fichero.set_subtitle(
            "%s  (%s, %s)" % (os.path.basename(ruta), tipo,
                              describir_imagen(ancho, alto, tamano)))
        self.pagina_imagen.set_visible_child_name("contenido")
        # El aviso dice si ESTA imagen se va a repetir y cuanto, no una frase generica.
        self._actualizar_aviso_ajuste()
        self._actualizar_acciones_imagen()
        self._consultar_espacio()

    def _descartar_imagen(self, motivo):
        """Una imagen que no sirve: se olvida la anterior y se dice por que."""
        self._imagen = None
        self._tamano_imagen = 0
        self._miniatura = None
        self.imagen_previa.set_visible(False)
        self.etiqueta_previa_imagen.set_text(motivo)
        self.fila_fichero.set_subtitle(motivo)
        self.avisar_error(motivo)
        self._actualizar_acciones_imagen()

    def _sin_miniatura(self, motivo):
        """Sin previsualizacion, pero explicando el motivo (y limpiando la anterior)."""
        self._miniatura = None
        self.imagen_previa.set_visible(False)
        self.imagen_previa.set_paintable(None)
        self.etiqueta_previa_imagen.set_text(motivo)

    def _miniatura_png(self, ruta, lado=LADO_MINIATURA):
        """PNG reducido EN MEMORIA con Pillow. Corre en un hilo: decodificar tarda.

        `draft()` deja que el decodificador de JPEG trabaje ya a la escala pedida (para una
        foto de 12 MP la diferencia de memoria es grande); en PNG no hace nada y se reduce
        despues con `thumbnail`.
        """
        from PIL import Image
        with Image.open(ruta) as imagen:
            with contextlib.suppress(Exception):
                imagen.draft(None, (lado, lado))
            imagen.thumbnail((lado, lado))
            if imagen.mode not in ("RGB", "RGBA"):
                imagen = imagen.convert("RGBA")
            almacen = io.BytesIO()
            imagen.save(almacen, format="PNG")
        return almacen.getvalue()

    def _miniatura_hecha(self, datos):
        self._miniatura = datos
        if not datos or not poner_imagen(self.imagen_previa, datos):
            self._sin_miniatura("No se pudo dibujar la miniatura de esta imagen.")
            return False
        self.imagen_previa.set_visible(True)
        self.etiqueta_previa_imagen.set_text(
            "Miniatura de %d px (el fichero original no se decodifica entero)."
            % LADO_MINIATURA)
        return False

    def _miniatura_fallida(self, exc):
        # Sin Pillow (o con un fichero que no se puede decodificar) se sigue pudiendo subir:
        # solo se queda sin previsualizacion.
        self._sin_miniatura("Sin miniatura: %s. La imagen se puede subir igual."
                            % _texto_error(exc, corto=True))

    def _cambiar_capa(self, fila, _parametro=None):
        """La capa elegida (fondo u OSD) se recuerda entre arranques."""
        indice = fila.get_selected()
        if 0 <= indice <= 1:
            self._guardar(capa="osd" if indice == 1 else "fondo")

    def _consultar_espacio(self):
        def tarea(panel):
            return panel.espacio_libre_kb()

        def hecho(libre):
            if libre is None:
                self.fila_espacio.set_subtitle("el panel no informa del espacio libre")
                return
            tamano_kb = self._tamano_imagen / 1024.0
            aviso = ""
            if self._tamano_imagen and tamano_kb > libre:
                aviso = "  OJO: el fichero no cabe en la capa de fondo"
            self.fila_espacio.set_subtitle("%s KB libres (el fichero ocupa %.0f KB)%s"
                                           % (libre, tamano_kb, aviso))

        en_hilo(lambda: self.panel.usar(tarea), hecho, self._espacio_fallido, "espacio")

    def _espacio_fallido(self, exc):
        self.fila_espacio.set_subtitle("no se pudo consultar: %s" % _texto_error(exc, corto=True))
        self._recuperar_panel(exc)

    def _subir_imagen(self, *_):
        if not self._imagen:
            self.avisar("Elige antes una imagen.")
            return False
        ruta = self._imagen
        capa = "fondo" if self.combo_capa.get_selected() == 0 else "osd"

        self.boton_subir.set_sensitive(False)
        self.spinner_subida.set_visible(True)
        self.spinner_subida.start()
        self.fila_subir.set_subtitle("Subiendo %s a la capa %s..." % (os.path.basename(ruta), capa))
        poner_texto(self.texto_subida, "Subiendo...")

        def tarea(panel):
            # Si la imagen no es del tamano del panel, el panel la REPITE en mosaico (y corta
            # la ultima). Con el ajuste activo se escala aqui, antes de mandarla.
            ajustar, modo = self._ajuste_imagen_elegido()
            if ajustar:
                datos = self._imagen_ajustada(ruta, modo)
                if datos:
                    nombre = os.path.splitext(os.path.basename(ruta))[0] + ".png"
                    return panel.subir_datos(datos, nombre, capa=capa)
            with escritura_fiable(panel):
                return panel.subir_archivo(ruta, capa=capa)

        en_hilo(lambda: self.panel.usar(tarea),
                self._subida_hecha, self._subida_fallida, "subir")
        return False

    def _parar_spinner(self):
        self.spinner_subida.stop()
        self.spinner_subida.set_visible(False)
        self._actualizar_acciones_imagen()

    def _subida_hecha(self, resultado):
        self._parar_spinner()
        self.fila_subir.set_subtitle(resultado.resumen())
        poner_texto(self.texto_subida, detalle_subida(resultado))
        if resultado.ok:
            capa = "fondo" if self.combo_capa.get_selected() == 0 else "OSD"
            self.avisar("Imagen subida al panel (capa %s)" % capa)
            # se recuerda lo enviado para la vista previa (las imagenes enormes no se
            # vuelven a leer: ya se subieron tal cual y no compensa tenerlas en memoria)
            if self._imagen and (self._tamano_imagen or 0) <= AVISO_IMAGEN_GRANDE:
                try:
                    with open(self._imagen, "rb") as fh:
                        self._recordar_enviado(fh.read(), os.path.basename(self._imagen))
                except OSError:
                    pass
            self._consultar_espacio()
            self._refrescar_estado()
        else:
            self.avisar_error("El panel rechazo la imagen: %s" % resultado.resumen())

    def _subida_fallida(self, exc):
        self._parar_spinner()
        self.fila_subir.set_subtitle("La subida fallo.")
        poner_texto(self.texto_subida, _texto_error(exc))
        self._recuperar_panel(exc)
        self.avisar_error(_texto_error(exc, corto=True))

    # ------------------------------------------------------------------ pagina 3: Temas

    def _pagina_temas(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        grupo = Adw.PreferencesGroup(
            title="Temas de ejemplo",
            description="Se buscan los .json de %s y de ./ejemplos."
                        % " y ".join(carpetas_de_ejemplos() or ["(ninguna carpeta)"]))

        self.fila_temas_recargar = fila_accion(
            "Buscar temas", "Pulsa para volver a leer las carpetas.",
            icono="folder-open-symbolic")
        boton_recargar = Gtk.Button(label="Recargar lista")
        boton_recargar.set_valign(Gtk.Align.CENTER)
        boton_recargar.set_tooltip_text("Vuelve a leer las carpetas de ejemplos.")
        boton_recargar.connect("clicked", lambda *_: self._cargar_lista_temas())
        self.fila_temas_recargar.add_suffix(boton_recargar)
        grupo.add(self.fila_temas_recargar)
        caja.append(grupo)

        self.lista_temas = Gtk.ListBox()
        self.lista_temas.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.lista_temas.add_css_class("boxed-list")
        self.lista_temas.connect("row-selected", self._tema_elegido)

        marco_lista = Gtk.Frame()
        marco_lista.set_child(self.lista_temas)
        caja.append(marco_lista)

        grupo_acciones = Adw.PreferencesGroup(
            title="Acciones",
            description="Previsualizar no toca el panel; Aplicar sube el tema a la capa OSD.")
        self.fila_tema = fila_accion("Tema elegido", "(ninguno)",
                                     icono="applications-graphics-symbolic")
        self.boton_tema_previa = Gtk.Button(label="Previsualizar")
        self.boton_tema_previa.set_valign(Gtk.Align.CENTER)
        self.boton_tema_previa.connect("clicked", self._previsualizar_tema)
        self.boton_tema_aplicar = Gtk.Button(label="Aplicar al panel")
        self.boton_tema_aplicar.add_css_class("suggested-action")
        self.boton_tema_aplicar.set_valign(Gtk.Align.CENTER)
        self.boton_tema_aplicar.connect("clicked", self._aplicar_tema)
        self.fila_tema.add_suffix(self.boton_tema_previa)
        self.fila_tema.add_suffix(self.boton_tema_aplicar)
        grupo_acciones.add(self.fila_tema)
        caja.append(grupo_acciones)

        grupo_previa = Adw.PreferencesGroup(title="Previsualizacion",
                                            description="El PNG se dibuja en memoria y se "
                                                        "ensena aqui; no se escribe en /tmp.")
        self.picture_tema = Gtk.Picture()
        self.picture_tema.set_can_shrink(True)
        self.picture_tema.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture_tema.set_size_request(-1, 220)
        marco_picture = Gtk.Frame()
        marco_picture.add_css_class("view")
        marco_picture.set_child(self.picture_tema)
        caja.append(grupo_previa)
        caja.append(marco_picture)

        grupo_problemas = Adw.PreferencesGroup(
            title="Problemas del tema",
            description="Lo que devuelve temas.validar(): vacio = se puede subir tal cual.")
        marco_problemas, self.texto_problemas = vista_de_texto(110)
        poner_texto(self.texto_problemas, "Elige un tema.")
        caja.append(grupo_problemas)
        caja.append(marco_problemas)

        # Sin ningun .json no tiene sentido ensenar los controles: estado vacio con el
        # boton de recargar a mano.
        boton_vacio = Gtk.Button(label="Buscar temas")
        boton_vacio.add_css_class("pill")
        boton_vacio.add_css_class("suggested-action")
        boton_vacio.connect("clicked", lambda *_: self._cargar_lista_temas())
        self.pagina_temas = apilar(
            ("vacio", estado_vacio(
                "No hay temas de ejemplo",
                "Se buscan ficheros .json en las carpetas del proyecto. Anade uno y pulsa "
                "Buscar temas: no hay que reiniciar la aplicacion.",
                "applications-graphics-symbolic", boton_vacio)),
            ("contenido", pagina_desplazable(caja)))
        self.pagina_temas.set_visible_child_name("vacio")

        self._anadir_pagina(self.pagina_temas, "temas", "Temas",
                            "applications-graphics-symbolic")

        GLib.idle_add(self._cargar_lista_temas)

    def _actualizar_acciones_tema(self):
        """Previsualizar y Aplicar solo con tema elegido, y con el motivo a la vista.

        Mientras hay un render en marcha los dos botones se apagan: antes se podia lanzar
        otro y los dos escribian el mismo PNG temporal a la vez.
        """
        temas, _error = cargar_temas()
        motor = temas is not None
        hay = bool(self._tema_actual) and motor and not self._tema_renderizando
        for boton in (self.boton_tema_previa, self.boton_tema_aplicar):
            boton.set_sensitive(hay)
        if self._tema_renderizando:
            motivo = "Hay un render en marcha."
            self.boton_tema_previa.set_tooltip_text(motivo)
            self.boton_tema_aplicar.set_tooltip_text(motivo)
        elif hay:
            self.boton_tema_previa.set_tooltip_text("Dibuja el tema en memoria y lo ensena.")
            self.boton_tema_aplicar.set_tooltip_text("Sube el tema al panel (capa OSD).")
        elif not motor:
            motivo = "Hace falta el motor de dibujo cfv235.temas."
            self.boton_tema_previa.set_tooltip_text(motivo)
            self.boton_tema_aplicar.set_tooltip_text(motivo)
        else:
            motivo = "Elige antes un tema de la lista."
            self.boton_tema_previa.set_tooltip_text(motivo)
            self.boton_tema_aplicar.set_tooltip_text(motivo)

    def _cargar_lista_temas(self):
        while True:
            hijo = self.lista_temas.get_first_child()
            if hijo is None:
                break
            self.lista_temas.remove(hijo)

        self._temas = []
        self._tema_actual = None
        for carpeta in carpetas_de_ejemplos():
            try:
                nombres = sorted(os.listdir(carpeta))
            except OSError:
                continue
            for nombre in nombres:
                if nombre.lower().endswith(".json"):
                    self._temas.append({"ruta": os.path.join(carpeta, nombre),
                                        "nombre": nombre})

        for datos in self._temas:
            fila = Gtk.ListBoxRow()
            caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            caja.set_margin_top(8)
            caja.set_margin_bottom(8)
            caja.set_margin_start(10)
            caja.set_margin_end(10)
            titulo = Gtk.Label(label=datos["nombre"], xalign=0)
            titulo.add_css_class("heading")
            ruta = Gtk.Label(label=os.path.dirname(datos["ruta"]), xalign=0)
            ruta.add_css_class("dim-label")
            ruta.add_css_class("caption")
            caja.append(titulo)
            caja.append(ruta)
            fila.set_child(caja)
            self.lista_temas.append(fila)

        if not self._temas:
            self.fila_temas_recargar.set_subtitle(
                "No se encontro ningun .json en: %s"
                % ", ".join(carpetas_de_ejemplos() or ["(ninguna carpeta)"]))
            poner_texto(self.texto_problemas,
                        "No hay temas de ejemplo. Comprueba que existe la carpeta "
                        "ejemplos/ con ficheros .json.")
            self.pagina_temas.set_visible_child_name("vacio")
        else:
            self.fila_temas_recargar.set_subtitle("%d temas encontrados." % len(self._temas))
            self.lista_temas.select_row(self.lista_temas.get_row_at_index(0))
            self.pagina_temas.set_visible_child_name("contenido")

        # Si el motor de dibujo todavia no esta, el aviso manda sobre lo anterior.
        self._comprobar_temas()
        self._actualizar_acciones_tema()
        return False

    def _tema_elegido(self, _lista, fila):
        if fila is None:
            return
        indice = fila.get_index()
        if 0 <= indice < len(self._temas):
            self._tema_actual = self._temas[indice]
            self.fila_tema.set_subtitle(self._tema_actual["ruta"])
            self._actualizar_acciones_tema()

    def _comprobar_temas(self):
        """(modulo temas, motivo del fallo). Avisa en la interfaz si no esta."""
        temas, error = cargar_temas()
        if temas is None:
            mensaje = ("El motor de dibujo `cfv235.temas` todavia no esta disponible:\n"
                       "%s\n\nLas paginas Temas y Dashboard no pueden funcionar sin el."
                       % error)
            poner_texto(self.texto_problemas, mensaje)
            self.fila_tema.set_subtitle("Sin motor de dibujo (cfv235.temas).")
        return temas, error

    def _renderizar_tema(self, ruta):
        """En un hilo: carga, valida y dibuja el tema **en memoria** (bytes PNG).

        `temas.renderizar_datos` evita el PNG temporal con nombre fijo que se usaba antes
        (y que dos renders concurrentes se pisaban): los bytes van directos al panel o a la
        previsualizacion.
        """
        temas, error = cargar_temas()
        if temas is None:
            raise RuntimeError("falta el modulo cfv235.temas: %s" % error)
        tema = temas.cargar(ruta)
        problemas = list(temas.validar(tema) or [])
        datos = temas.renderizar_datos(tema)
        return {"ruta": ruta, "problemas": problemas, "datos": datos,
                "nombre": tema.get("nombre") if isinstance(tema, dict) else None}

    def _previsualizar_tema(self, *_):
        if not self._tema_actual:
            self.avisar("Elige antes un tema de la lista.")
            return False
        if self._tema_renderizando:
            self.avisar("Ya hay un render en marcha.")
            return False
        self._tema_renderizando = True
        self._actualizar_acciones_tema()
        self.fila_tema.set_subtitle("Dibujando %s..." % self._tema_actual["nombre"])
        en_hilo(lambda: self._renderizar_tema(self._tema_actual["ruta"]),
                self._previa_hecha, self._tema_fallido, "tema-previa")
        return False

    def _previa_hecha(self, datos):
        self._tema_renderizando = False
        self._actualizar_acciones_tema()
        if not poner_imagen(self.picture_tema, datos.get("datos")):
            self.fila_tema.set_subtitle("No se pudo dibujar la previsualizacion del tema.")
        else:
            self.fila_tema.set_subtitle(
                "%s  ->  PNG en memoria (%.0f KB)"
                % (datos["ruta"], len(datos.get("datos") or b"") / 1024.0))
        problemas = datos["problemas"]
        if problemas:
            poner_texto(self.texto_problemas,
                        "temas.validar() devolvio %d problema(s):\n\n%s"
                        % (len(problemas), "\n".join("- %s" % p for p in problemas)))
        else:
            poner_texto(self.texto_problemas,
                        "temas.validar() no encontro problemas: el tema se puede subir tal cual.")
        self.avisar("Previsualizacion lista")

    def _aplicar_tema(self, *_):
        if not self._tema_actual:
            self.avisar("Elige antes un tema de la lista.")
            return False
        if self._tema_renderizando:
            self.avisar("Ya hay un render en marcha.")
            return False
        ruta = self._tema_actual["ruta"]
        nombre = nombre_para_el_panel(ruta)
        self._tema_renderizando = True
        self._actualizar_acciones_tema()
        self.fila_tema.set_subtitle("Aplicando %s al panel (capa OSD)..." % ruta)

        def tarea():
            datos = self._renderizar_tema(ruta)

            def subir(panel):
                with escritura_fiable(panel):
                    # Los bytes van directos al panel: sin fichero temporal de por medio.
                    return panel.subir_datos(datos["datos"], nombre, capa="osd")

            datos["resultado"] = self.panel.usar(subir)
            return datos

        en_hilo(tarea, self._tema_aplicado, self._tema_fallido, "tema-aplicar")
        return False

    def _tema_aplicado(self, datos):
        self._previa_hecha(datos)
        resultado = datos["resultado"]
        problemas = datos["problemas"]
        texto = detalle_subida(resultado)
        if problemas:
            texto += "\n\nproblemas del tema:\n" + "\n".join("- %s" % p for p in problemas)
        poner_texto(self.texto_problemas, texto)
        self.fila_tema.set_subtitle(resultado.resumen())
        if resultado.ok:
            self.avisar("Tema aplicado al panel (capa OSD)")
            # los bytes del tema ya estan en memoria: se recuerdan para la vista previa
            if datos.get("datos"):
                self._recordar_enviado(datos["datos"],
                                       os.path.basename(self._tema_actual["ruta"]))
            self._refrescar_estado()
        else:
            self.avisar_error("El panel rechazo el tema: %s" % resultado.resumen())

    def _tema_fallido(self, exc):
        self._tema_renderizando = False
        self._actualizar_acciones_tema()
        self.fila_tema.set_subtitle("Fallo al procesar el tema.")
        poner_texto(self.texto_problemas, _texto_error(exc))
        self.avisar_error(_texto_error(exc, corto=True))
        self._recuperar_panel(exc)

    # ------------------------------------------------------------------ pagina 4: Dashboard

    def _franja_servicio(self):
        """Franja de control del servicio de usuario del dashboard (systemd).

        Todo lo que pulsa aqui llama a `systemctl --user` en un hilo; mientras tanto los
        controles se desactivan y el resultado se aplica con `GLib.idle_add`.
        """
        grupo = Adw.PreferencesGroup(
            title="Servicio del dashboard (systemd de usuario)",
            description="El dashboard tambien puede correr como servicio de usuario "
                        "(%s). El panel admite una sola sesion: mientras el servicio "
                        "esta en marcha, esta aplicacion no puede usar el panel."
                        % NOMBRE_SERVICIO)

        self.fila_servicio_estado = fila_accion("Estado del servicio", "Leyendo...",
                                                icono="power-profile-performance-symbolic")
        self.fila_servicio_estado.add_prefix(
            Gtk.Label(label=NOMBRE_SERVICIO, css_classes=["dim-label"]))
        grupo.add(self.fila_servicio_estado)

        self.fila_servicio_botones = fila_accion(
            "Control", "Arrancar, parar o reiniciar el servicio.",
            icono="preferences-system-symbolic")
        self.boton_servicio_arrancar = Gtk.Button(label="Arrancar")
        self.boton_servicio_arrancar.set_valign(Gtk.Align.CENTER)
        self.boton_servicio_arrancar.connect("clicked", self._arrancar_servicio)
        self.boton_servicio_parar = Gtk.Button(label="Parar")
        self.boton_servicio_parar.set_valign(Gtk.Align.CENTER)
        self.boton_servicio_parar.connect("clicked", self._parar_servicio)
        self.boton_servicio_reiniciar = Gtk.Button(label="Reiniciar")
        self.boton_servicio_reiniciar.set_valign(Gtk.Align.CENTER)
        self.boton_servicio_reiniciar.connect("clicked", self._reiniciar_servicio)
        self.boton_servicio_registro = Gtk.Button(label="Ver registro")
        self.boton_servicio_registro.set_valign(Gtk.Align.CENTER)
        self.boton_servicio_registro.connect("clicked", self._ver_registro_servicio)
        for boton in (self.boton_servicio_arrancar, self.boton_servicio_parar,
                      self.boton_servicio_reiniciar, self.boton_servicio_registro):
            self.fila_servicio_botones.add_suffix(boton)
        grupo.add(self.fila_servicio_botones)

        self.interruptor_servicio_auto = Adw.SwitchRow(
            title="Arrancar al iniciar sesion",
            subtitle="Equivale a `systemctl --user enable %s`: el dashboard se pone en "
                     "marcha solo al entrar en la sesion." % NOMBRE_SERVICIO)
        self.interruptor_servicio_auto.set_icon_name("system-reboot-symbolic")
        self.interruptor_servicio_auto.connect("notify::active",
                                               self._cambiar_arranque_automatico)
        grupo.add(self.interruptor_servicio_auto)

        # Mientras no se sepa el estado, los controles no se tocan.
        self._bloquear_controles_servicio(True)
        return grupo

    def _pagina_dashboard(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        # Perfiles y secciones del nucleo (se leen una vez al montar la pagina).
        self._perfiles = self._perfiles_disponibles()
        claves = [clave for clave, _etiqueta, _desc in self._perfiles]
        if self._perfil_dashboard not in claves:
            self._perfil_dashboard = claves[0]

        # --- aviso: el panel solo admite una sesion; si el servicio esta en marcha, la
        # aplicacion no puede dibujar su propio dashboard en vivo.
        self.aviso_servicio_panel = Adw.Banner(
            title="El servicio del dashboard esta en marcha y tiene tomado el panel: "
                  "paralo para usar el dashboard en vivo de la aplicacion.")
        self.aviso_servicio_panel.set_button_label("Parar el servicio")
        self.aviso_servicio_panel.set_revealed(False)
        self.aviso_servicio_panel.connect("button-clicked", self._parar_servicio)
        caja.append(self.aviso_servicio_panel)

        grupo = Adw.PreferencesGroup(
            title="Dashboard en vivo",
            description="Dibuja las metricas del PC como tema y lo sube a la capa OSD "
                        "cada pocos segundos.")

        self.fila_dashboard = fila_accion("Bucle del dashboard", "Parado.",
                                          icono="media-playback-start-symbolic")
        self.boton_dashboard = Gtk.Button(label="Activar")
        self.boton_dashboard.add_css_class("suggested-action")
        self.boton_dashboard.set_valign(Gtk.Align.CENTER)
        self.boton_dashboard.connect("clicked", self._alternar_dashboard)
        self.spinner_dashboard = Gtk.Spinner()
        self.spinner_dashboard.set_valign(Gtk.Align.CENTER)
        self.spinner_dashboard.set_visible(False)
        self.fila_dashboard.add_suffix(self.spinner_dashboard)
        self.fila_dashboard.add_suffix(self.boton_dashboard)
        grupo.add(self.fila_dashboard)

        self.combo_periodo = Adw.ComboRow(
            title="Periodo",
            subtitle="Tiempo entre fotogramas.",
            model=Gtk.StringList.new(["%d s" % p for p in PERIODOS_DASHBOARD]))
        self.combo_periodo.set_icon_name("preferences-system-time-symbolic")
        # El periodo elegido se recuerda entre arranques.
        self.combo_periodo.set_selected(self._indice_periodo())
        self.combo_periodo.connect("notify::selected", self._cambiar_periodo_dashboard)
        grupo.add(self.combo_periodo)
        caja.append(grupo)

        # Perfil y secciones: se aplican al momento, sin reiniciar la aplicacion.
        caja.append(self._grupo_perfil_dashboard())
        caja.append(self._grupo_secciones_dashboard())

        # Previsualizacion: dibuja a PNG y NO toca el panel.
        caja.append(self._bloque_previa_dashboard())

        caja.append(self._franja_servicio())

        grupo_sensores = Adw.PreferencesGroup(
            title="Sensores del PC",
            description="Valores de cfv235.sensores.Sensores.")
        self._filas_sensores = {}
        for clave, etiqueta, _unidad in SENSORES_VISIBLES:
            fila = Adw.ActionRow(title=etiqueta, subtitle="-")
            fila.add_prefix(Gtk.Label(label=clave, css_classes=["dim-label"]))
            grupo_sensores.add(fila)
            self._filas_sensores[clave] = fila
        caja.append(grupo_sensores)

        grupo_estado = Adw.PreferencesGroup(
            title="Estado del bucle",
            description="Lo que lleva subido el dashboard en vivo desde que se activo.")
        self.fila_fotogramas = fila_accion("Fotogramas subidos", "0",
                                           icono="emblem-ok-symbolic")
        self.fila_ultimo_error = fila_accion("Ultimo error", "(ninguno)",
                                             icono="dialog-warning-symbolic")
        grupo_estado.add(self.fila_fotogramas)
        grupo_estado.add(self.fila_ultimo_error)
        caja.append(grupo_estado)

        self._anadir_pagina(pagina_desplazable(caja), "dashboard", "Dashboard",
                            "utilities-system-monitor-symbolic")

        self._pintar_secciones_dashboard()
        GLib.idle_add(self._muestrear_sensores)

    # --- perfil y secciones del dashboard

    def _perfiles_disponibles(self):
        """[(clave, etiqueta, descripcion)] de los perfiles que existen de verdad."""
        dashboard, _error = cargar_dashboard()
        claves = set(getattr(dashboard, "PERFILES", {}) or {}) if dashboard is not None else set()
        lista = [(clave, etiqueta, descripcion)
                 for clave, etiqueta, descripcion in PERFILES_DASHBOARD
                 if not claves or clave in claves]
        conocidas = {clave for clave, _etiqueta, _desc in PERFILES_DASHBOARD}
        for clave in sorted(claves - conocidas):
            lista.append((clave, str(clave).capitalize(), "Perfil de cfv235.dashboard."))
        return lista or list(PERFILES_DASHBOARD)

    def _indice_perfil(self, perfil):
        """Posicion de un perfil en el combo (0 si ya no existe)."""
        for indice, (clave, _etiqueta, _desc) in enumerate(
                getattr(self, "_perfiles", PERFILES_DASHBOARD)):
            if clave == perfil:
                return indice
        return 0

    def _indice_periodo(self):
        """Posicion del periodo guardado en el combo (por defecto, el segundo)."""
        guardado = self._config.get("periodo")
        if guardado in PERIODOS_DASHBOARD:
            return PERIODOS_DASHBOARD.index(guardado)
        return 1 if len(PERIODOS_DASHBOARD) > 1 else 0

    def _cambiar_periodo_dashboard(self, fila, _parametro=None):
        """El periodo elegido se recuerda; el bucle en marcha lo toma al reiniciarse."""
        indice = fila.get_selected()
        if not 0 <= indice < len(PERIODOS_DASHBOARD):
            return
        self._guardar(periodo=PERIODOS_DASHBOARD[indice])

    def _grupo_perfil_dashboard(self):
        """ComboRow para elegir el perfil del dashboard (los cinco)."""
        dashboard, error = cargar_dashboard()
        grupo = Adw.PreferencesGroup(
            title="Perfil del dashboard",
            description="El perfil decide que bloques se dibujan. Las secciones de abajo "
                        "se pueden cambiar una a una sobre el perfil elegido.")

        self.combo_perfil = Adw.ComboRow(
            title="Perfil",
            subtitle="Completo, Esencial, Graficas, Minimo o Presentacion.",
            model=Gtk.StringList.new([etiqueta for _c, etiqueta, _d in self._perfiles]))
        self.combo_perfil.set_icon_name("view-list-symbolic")
        self.combo_perfil.set_selected(self._indice_perfil(self._perfil_dashboard))
        self.combo_perfil.connect("notify::selected", self._cambiar_perfil_dashboard)
        grupo.add(self.combo_perfil)

        self.fila_resumen_dashboard = fila_accion("Seleccion actual", "-",
                                                  icono="emblem-ok-symbolic")
        grupo.add(self.fila_resumen_dashboard)

        if dashboard is None:
            # Sin el modulo no hay perfiles: se apaga y se dice por que.
            motivo = "Falta el modulo cfv235.dashboard: %s" % error
            self.combo_perfil.set_sensitive(False)
            self.combo_perfil.set_tooltip_text(motivo)
            self.fila_resumen_dashboard.set_subtitle(motivo)
            self.fila_resumen_dashboard.set_tooltip_text(motivo)
        return grupo

    def _grupo_secciones_dashboard(self):
        """Casillas (Adw.SwitchRow) con el catalogo de secciones del dashboard."""
        dashboard, error = cargar_dashboard()
        grupo = Adw.PreferencesGroup(
            title="Secciones",
            description="Activa o desactiva cada bloque. El cambio se aplica al momento al "
                        "bucle en vivo y a la previsualizacion, sin reiniciar la aplicacion.")

        self._filas_secciones = {}
        self._clave_de_fila = {}
        if dashboard is None:
            grupo.add(fila_accion("Sin catalogo de secciones",
                                  "Falta el modulo cfv235.dashboard: %s" % error,
                                  icono="dialog-warning-symbolic"))
            return grupo

        try:
            catalogo = list(dashboard.catalogo_secciones() or [])
        except Exception as exc:                  # noqa: BLE001
            catalogo = []
            grupo.add(fila_accion("Sin catalogo de secciones", str(exc),
                                  icono="dialog-warning-symbolic"))

        for seccion in catalogo:
            clave = seccion.get("clave")
            fila = Adw.SwitchRow(title=seccion.get("etiqueta") or clave,
                                 subtitle=seccion.get("descripcion") or "")
            fila.connect("notify::active", self._cambiar_seccion_dashboard)
            grupo.add(fila)
            self._filas_secciones[clave] = fila
            self._clave_de_fila[fila] = clave
        return grupo

    def _cambiar_perfil_dashboard(self, fila, _parametro=None):
        """Cambio de perfil: se olvidan los ajustes sueltos y mandan los del perfil."""
        if self._silenciar_secciones:
            return
        indice = fila.get_selected()
        if not 0 <= indice < len(self._perfiles):
            return
        self._perfil_dashboard = self._perfiles[indice][0]
        self._ajustes_secciones = {}
        self._pintar_secciones_dashboard()
        self._dashboard_version += 1
        # El perfil se recuerda entre arranques (con sus ajustes vacios).
        self._guardar(perfil=self._perfil_dashboard, secciones={})
        self.avisar("Perfil del dashboard: %s" % self._perfiles[indice][1])

    def _cambiar_seccion_dashboard(self, fila, _parametro=None):
        """Casilla cambiada: se apunta como ajuste sobre el perfil elegido."""
        if self._silenciar_secciones:
            return
        clave = self._clave_de_fila.get(fila)
        if not clave:
            return
        self._ajustes_secciones[clave] = bool(fila.get_active())
        self._dashboard_version += 1
        self._resumen_dashboard()
        # Los ajustes sueltos tambien se recuerdan.
        self._guardar(secciones=dict(self._ajustes_secciones))
        self.avisar("Seccion %s: %s"
                    % (fila.get_title(), "activada" if fila.get_active() else "desactivada"))

    def _pintar_secciones_dashboard(self):
        """Pone cada casilla en el estado que sale del perfil mas los ajustes."""
        dashboard, _error = cargar_dashboard()
        activas = {}
        if dashboard is not None:
            try:
                activas = dashboard.secciones_activas(self._perfil_dashboard,
                                                      dict(self._ajustes_secciones))
            except Exception:                     # noqa: BLE001
                activas = {}
        self._silenciar_secciones = True
        try:
            for clave, fila in getattr(self, "_filas_secciones", {}).items():
                if clave in activas:
                    fila.set_active(bool(activas[clave]))
        finally:
            self._silenciar_secciones = False
        self._resumen_dashboard()

    def _resumen_dashboard(self):
        """Texto con el perfil elegido y cuantas secciones quedan activas."""
        perfiles = getattr(self, "_perfiles", PERFILES_DASHBOARD)
        indice = self._indice_perfil(self._perfil_dashboard)
        etiqueta = perfiles[indice][1]
        filas = getattr(self, "_filas_secciones", {})
        activas = sum(1 for fila in filas.values() if fila.get_active())
        extra = ""
        if self._ajustes_secciones:
            extra = "  (%d cambio(s) sobre el perfil)" % len(self._ajustes_secciones)
        self.fila_resumen_dashboard.set_subtitle(
            "Perfil %s: %d de %d secciones activas.%s" % (etiqueta, activas, len(filas), extra))

    def _ajustes_dashboard(self):
        """(perfil, ajustes) elegidos, ya comprobados contra el modulo del nucleo."""
        dashboard, _error = cargar_dashboard()
        perfil = self._perfil_dashboard
        ajustes = dict(self._ajustes_secciones)
        if dashboard is None:
            return perfil, ajustes
        try:
            dashboard.secciones_activas(perfil, ajustes)
        except Exception:                         # noqa: BLE001
            # Un perfil o una seccion que el nucleo ya no conoce: se cae a lo minimo.
            perfiles = getattr(dashboard, "PERFILES", {}) or {}
            disponible = "completo" if "completo" in perfiles else next(iter(perfiles), perfil)
            return disponible, {}
        return perfil, ajustes

    # --- previsualizacion del dashboard (no toca el panel)

    def _bloque_previa_dashboard(self):
        """Grupo + marco con el PNG del dashboard. Dibujar aqui NO usa el panel."""
        dashboard, error = cargar_dashboard()
        grupo = Adw.PreferencesGroup(
            title="Previsualizacion",
            description="Dibuja el dashboard EN MEMORIA y lo ensena aqui. No toca el panel, "
                        "asi que funciona aunque el servicio lo tenga ocupado.")

        self.fila_previa_dashboard = fila_accion(
            "Previsualizar", "Con el perfil y las secciones elegidos.",
            icono="image-x-generic-symbolic")
        self.spinner_previa_dashboard = Gtk.Spinner()
        self.spinner_previa_dashboard.set_valign(Gtk.Align.CENTER)
        self.spinner_previa_dashboard.set_visible(False)
        self.boton_previsualizar_dashboard = Gtk.Button(label="Previsualizar")
        self.boton_previsualizar_dashboard.add_css_class("suggested-action")
        self.boton_previsualizar_dashboard.set_valign(Gtk.Align.CENTER)
        self.boton_previsualizar_dashboard.connect("clicked", self._previsualizar_dashboard)
        self.fila_previa_dashboard.add_suffix(self.spinner_previa_dashboard)
        self.fila_previa_dashboard.add_suffix(self.boton_previsualizar_dashboard)
        grupo.add(self.fila_previa_dashboard)

        self.picture_dashboard = Gtk.Picture()
        self.picture_dashboard.set_can_shrink(True)
        self.picture_dashboard.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture_dashboard.set_size_request(-1, 220)
        marco = Gtk.Frame()
        marco.add_css_class("view")
        marco.set_child(self.picture_dashboard)

        self.pagina_previa_dashboard = apilar(
            ("vacio", estado_vacio(
                "Sin previsualizacion",
                "Pulsa Previsualizar para dibujar el dashboard con el perfil y las "
                "secciones elegidos. El PNG se guarda en el temporal, no en el panel.",
                "image-x-generic-symbolic")),
            ("contenido", marco))
        self.pagina_previa_dashboard.set_visible_child_name("vacio")
        self.pagina_previa_dashboard.set_size_request(-1, 240)

        if dashboard is None:
            motivo = "Hace falta el modulo cfv235.dashboard: %s" % error
            self.boton_previsualizar_dashboard.set_sensitive(False)
            self.boton_previsualizar_dashboard.set_tooltip_text(motivo)
            self.fila_previa_dashboard.set_subtitle(motivo)

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caja.append(grupo)
        caja.append(self.pagina_previa_dashboard)
        return caja

    def _muestra_sensores(self):
        """Muestra tolerante de sensores (para dibujar): {} si no se puede leer."""
        try:
            return self._leer_sensores()
        except Exception:                         # noqa: BLE001
            return {}

    def _leer_sensores(self):
        """Dos muestras separadas: los deltas (CPU, red, disco) necesitan la primera
        como referencia, y con una sola vuelta la tabla se quedaria en '-'.

        Se ejecuta en un hilo. Lanza si falta el modulo o si la lectura falla.
        """
        Sensores, error = cargar_sensores()
        if Sensores is None:
            raise RuntimeError("falta cfv235.sensores: %s" % error)
        sensores = Sensores()
        sensores.muestra()                        # primera vuelta: fija la referencia
        time.sleep(0.2)
        return sensores.muestra() or {}

    def _sensores_fallidos(self, exc):
        self.fila_ultimo_error.set_subtitle("no se pudieron leer los sensores: %s"
                                            % _texto_error(exc, corto=True))

    def _previsualizar_dashboard(self, *_):
        """Dibuja el dashboard EN MEMORIA y lo ensena. No toca el panel."""
        dashboard, error_dashboard = cargar_dashboard()
        temas, error_temas = cargar_temas()
        if dashboard is None or temas is None:
            motivo = ("Hace falta el modulo cfv235.dashboard: %s" % error_dashboard
                      if dashboard is None else
                      "Hace falta el motor de dibujo cfv235.temas: %s" % error_temas)
            self.fila_previa_dashboard.set_subtitle(motivo)
            self.boton_previsualizar_dashboard.set_sensitive(False)
            self.boton_previsualizar_dashboard.set_tooltip_text(motivo)
            self.avisar_error(motivo)
            return False

        perfil, ajustes = self._ajustes_dashboard()
        self.spinner_previa_dashboard.set_visible(True)
        self.spinner_previa_dashboard.start()
        self.boton_previsualizar_dashboard.set_sensitive(False)
        self.fila_previa_dashboard.set_subtitle("Dibujando con el perfil %s..." % perfil)

        def tarea():
            valores = self._muestra_sensores()
            tema = dashboard.tema_dashboard(titulo="CFV 235", perfil=perfil,
                                            ajustes=ajustes, valores=valores)
            datos = temas.renderizar_datos(tema, valores)
            return {"datos": datos, "perfil": perfil, "tema": tema, "ajustes": ajustes}

        en_hilo(tarea, self._previa_dashboard_hecha, self._previa_dashboard_fallida,
                "dashboard-previa")
        return False

    def _previa_dashboard_hecha(self, datos):
        self.spinner_previa_dashboard.stop()
        self.spinner_previa_dashboard.set_visible(False)
        self.boton_previsualizar_dashboard.set_sensitive(True)
        self.pagina_previa_dashboard.set_visible_child_name("contenido")
        widgets = len((datos.get("tema") or {}).get("widgets") or [])
        if poner_imagen(self.picture_dashboard, datos.get("datos")):
            self.fila_previa_dashboard.set_subtitle(
                "Perfil %s, %d widgets  ->  PNG en memoria (%.0f KB)"
                % (datos["perfil"], widgets, len(datos.get("datos") or b"") / 1024.0))
        else:
            self.fila_previa_dashboard.set_subtitle("No se pudo dibujar la previsualizacion.")
        self.avisar("Previsualizacion del dashboard lista")

    def _previa_dashboard_fallida(self, exc):
        self.spinner_previa_dashboard.stop()
        self.spinner_previa_dashboard.set_visible(False)
        self.boton_previsualizar_dashboard.set_sensitive(True)
        self.fila_previa_dashboard.set_subtitle("No se pudo dibujar: %s"
                                                % _texto_error(exc, corto=True))
        self.avisar_error("No se pudo dibujar el dashboard: %s"
                          % _texto_error(exc, corto=True))

    def _muestrear_sensores(self, *_):
        """Pide una muestra de sensores (sin panel) para llenar la tabla.

        Va en un hilo: `Sensores.muestra()` lee /proc y los deltas necesitan dos lecturas
        separadas un momento. Asi la interfaz no se queda esperando.
        """
        en_hilo(self._leer_sensores, self._pintar_sensores, self._sensores_fallidos,
                "sensores")
        return False

    def _pintar_sensores(self, valores):
        for clave, _etiqueta, unidad in SENSORES_VISIBLES:
            fila = self._filas_sensores.get(clave)
            if fila is None:
                continue
            valor = (valores or {}).get(clave)
            if valor is None:
                fila.set_subtitle("-")
            elif isinstance(valor, (int, float)):
                fila.set_subtitle("%.1f%s" % (valor, unidad))
            else:
                fila.set_subtitle(str(valor))

    def _alternar_dashboard(self, *_):
        if self._dashboard_activo:
            self._parar_dashboard_y_esperar()
        else:
            # El servicio y este bucle no pueden tener el panel a la vez.
            if self._ocupado_por_el_servicio():
                self.avisar("El servicio del dashboard tiene el panel: paralelo antes "
                            "de activar el dashboard en vivo.")
                return False
            self._arrancar_dashboard()
        return False

    def _arrancar_dashboard(self):
        dashboard, error = cargar_dashboard()
        if dashboard is None:
            self.avisar_error("Falta el modulo cfv235.dashboard")
            self.fila_dashboard.set_subtitle("No se puede arrancar: %s" % error)
            return

        self._parar_dashboard = threading.Event()
        parar = self._parar_dashboard
        self._dashboard_activo = True
        self._fotogramas = 0
        self.boton_dashboard.set_label("Desactivar")
        self.boton_dashboard.remove_css_class("suggested-action")
        self.boton_dashboard.add_css_class("destructive-action")
        self.spinner_dashboard.set_visible(True)
        self.spinner_dashboard.start()
        self.fila_dashboard.set_subtitle("Arrancando...")
        self.fila_ultimo_error.set_subtitle("(ninguno)")

        # El combo puede llegar sin seleccion (-1): indexar sin comprobar lanzaba IndexError.
        indice_periodo = self.combo_periodo.get_selected()
        if not 0 <= indice_periodo < len(PERIODOS_DASHBOARD):
            indice_periodo = 1 if len(PERIODOS_DASHBOARD) > 1 else 0
        periodo = PERIODOS_DASHBOARD[indice_periodo]
        self._guardar(periodo=periodo)
        perfil, ajustes = self._ajustes_dashboard()
        self._hilo_dashboard = en_hilo(
            lambda: self._bucle_dashboard(dashboard, periodo, parar, perfil, ajustes),
            self._dashboard_terminado, self._dashboard_fallido, "dashboard")

    def _crear_tablero(self, modulo_dashboard, panel, perfil, ajustes):
        """El `Dashboard` con el perfil y las secciones elegidos.

        Si el nucleo todavia no acepta `perfil`/`ajustes`, se arranca con lo de siempre en
        vez de romper: la ventana sigue funcionando con un nucleo mas antiguo.
        """
        try:
            return modulo_dashboard.Dashboard(panel, capa="osd", perfil=perfil,
                                              ajustes=ajustes)
        except TypeError:
            return modulo_dashboard.Dashboard(panel, capa="osd")

    def _bucle_dashboard(self, modulo_dashboard, periodo, parar, perfil, ajustes):
        """Bucle del dashboard. Corre en un hilo; `parar` es un threading.Event.

        El tablero recibe un `PanelPrestado`, que toma el cerrojo del panel fotograma a
        fotograma (no durante todo el bucle) y siempre pide el panel ACTUAL: si el panel se
        reconecta a mitad del bucle, el siguiente fotograma ya usa el nuevo.

        Si el usuario cambia el perfil o las secciones, `_dashboard_version` sube y aqui se
        vuelve a componer el tema: el cambio se aplica sin reiniciar la aplicacion.

        Cada 10 fotogramas se mira el espacio libre del panel (igual que hace el bucle del
        nucleo en `cfv235.dashboard`): un panel sin memoria se queda atascado con
        bootFinish=0 y solo se recupera con `recovery`.
        """
        tablero = self._crear_tablero(modulo_dashboard, PanelPrestado(self.panel),
                                      perfil, ajustes)
        version = self._dashboard_version
        fotogramas_bucle = 0
        while not parar.is_set():
            if self._dashboard_version != version:
                version = self._dashboard_version
                nuevo_perfil, nuevos_ajustes = self._ajustes_dashboard()
                try:
                    tablero.tema = modulo_dashboard.tema_dashboard(
                        perfil=nuevo_perfil, ajustes=nuevos_ajustes,
                        valores=tablero.sensores.muestra())
                    GLib.idle_add(self._dashboard_reconfigurado, nuevo_perfil)
                except Exception as exc:          # noqa: BLE001
                    GLib.idle_add(self.fila_ultimo_error.set_subtitle,
                                  "no se pudo cambiar el perfil: %s" % exc)

            error = ""
            try:
                # El envoltorio toma el cerrojo y aplica `escritura_fiable` por fotograma.
                ok = tablero.fotograma()
                error = tablero.ultimo_error
            except (SinPanel, SinPermisos, ErrorCanal, OSError) as exc:
                ok = False
                error = str(exc)

            fotogramas_bucle += 1
            if ok:
                self._fotogramas += 1

            valores = {}
            try:
                crudo = tablero.sensores.muestra()
                valores = {clave: crudo.get(clave) for clave, _, _ in SENSORES_VISIBLES}
            except Exception:                     # noqa: BLE001
                pass

            GLib.idle_add(self._fotograma_hecho, self._fotogramas, error, valores)

            if fotogramas_bucle % FOTOGRAMAS_POR_COMPROBACION_ESPACIO == 0:
                libre = None
                try:
                    libre = tablero.panel.espacio_libre_kb()
                except (SinPanel, SinPermisos, ErrorCanal, OSError):
                    libre = None
                if libre is not None and libre < ESPACIO_MINIMO_DASHBOARD_KB:
                    GLib.idle_add(self._dashboard_sin_espacio, libre)
                    break

            # Event.wait() permite cortar el bucle al instante al desactivar.
            if parar.wait(periodo):
                break

    def _dashboard_sin_espacio(self, libre):
        """El panel se esta quedando sin memoria: se corta el bucle y se avisa."""
        self._dashboard_activo = False
        self.spinner_dashboard.stop()
        self.spinner_dashboard.set_visible(False)
        self.boton_dashboard.set_label("Activar")
        self.boton_dashboard.remove_css_class("destructive-action")
        self.boton_dashboard.add_css_class("suggested-action")
        mensaje = ("Queda muy poco espacio en el panel (%s KB): bucle detenido para no "
                   "dejarlo atascado. Libera espacio con Recovery y vuelve a subir el tema."
                   % libre)
        self.fila_dashboard.set_subtitle("Parado: queda poco espacio en el panel.")
        self.fila_ultimo_error.set_subtitle(mensaje)
        self.avisar_error(mensaje)
        self._notificar("dashboard-sin-espacio", "El dashboard se ha detenido",
                        mensaje, accion="app.reconectar", etiqueta_accion="Reconectar")
        return False

    def _dashboard_reconfigurado(self, perfil):
        """Aviso de que el bucle en vivo ha tomado el nuevo perfil (hilo de la interfaz)."""
        perfiles = getattr(self, "_perfiles", PERFILES_DASHBOARD)
        etiqueta = perfiles[self._indice_perfil(perfil)][1]
        self.avisar("Dashboard en vivo: perfil %s" % etiqueta)
        return False

    def _fotograma_hecho(self, fotogramas, error, valores):
        if not self._dashboard_activo:
            return False
        self.fila_fotogramas.set_subtitle(str(fotogramas))
        self.fila_ultimo_error.set_subtitle(error or "(ninguno)")
        self.fila_dashboard.set_subtitle("Activo: %d fotogramas subidos%s"
                                         % (fotogramas, "" if not error else " (con errores)"))
        self._pintar_sensores(valores)
        return False

    def _dashboard_fallido(self, exc):
        self._dashboard_activo = False
        self.spinner_dashboard.stop()
        self.spinner_dashboard.set_visible(False)
        self.boton_dashboard.set_label("Activar")
        self.boton_dashboard.remove_css_class("destructive-action")
        self.boton_dashboard.add_css_class("suggested-action")
        self.fila_dashboard.set_subtitle("No arranco: %s" % _texto_error(exc, corto=True))
        self.fila_ultimo_error.set_subtitle(_texto_error(exc))
        self.avisar_error(_texto_error(exc, corto=True))
        # Error del bucle: se avisa tambien en el escritorio y se intenta recuperar el panel.
        self._notificar("dashboard-error", "El dashboard en vivo ha fallado",
                        _texto_error(exc, corto=True),
                        accion="app.reconectar", etiqueta_accion="Reconectar")
        if isinstance(exc, (ErrorCanal, OSError)):
            self._recuperar_panel(exc)

    def _dashboard_terminado(self, _resultado=None):
        return False

    def _parar_dashboard_y_esperar(self):
        """Levanta el Event del bucle y deja la interfaz en estado parado."""
        if self._parar_dashboard is not None:
            self._parar_dashboard.set()
        self._dashboard_activo = False
        self.spinner_dashboard.stop()
        self.spinner_dashboard.set_visible(False)
        self.boton_dashboard.set_label("Activar")
        self.boton_dashboard.remove_css_class("destructive-action")
        self.boton_dashboard.add_css_class("suggested-action")
        self.fila_dashboard.set_subtitle("Parado despues de %d fotogramas." % self._fotogramas)
        self.avisar("Dashboard en vivo parado")
        # El servicio puede haber cambiado mientras el bucle corria.
        self._refrescar_servicio()

    # ------------------------------------------------------------------ pagina 5: Video

    def _pagina_video(self):
        self._video_ruta = None
        self._video_informe = None
        self._video_reproductor = None
        self._parar_video = None
        self._hilo_video = None
        self._video_activo = False
        self._video_fotogramas = 0

        # --- aviso de GStreamer: solo hace falta para mp4/mkv/webm/avi/mov
        self.aviso_gstreamer = Adw.Banner(
            title="GStreamer no esta disponible: los GIF y las carpetas de imagenes "
                  "funcionan igual, pero los ficheros de video no se podran leer.")
        self.aviso_gstreamer.set_revealed(False)

        self.pila_video = apilar(
            ("vacio", self._video_sin_fuente()),
            ("contenido", pagina_desplazable(self._contenido_video())))
        self.pila_video.set_visible_child_name("vacio")

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caja.append(self.aviso_gstreamer)
        caja.append(self.pila_video)

        self._anadir_pagina(caja, "video", "Video", "video-x-generic-symbolic")

        # GStreamer se comprueba en un hilo: cargar PyGObject/Gst tarda y no debe bloquear
        # la interfaz. La pagina ya esta montada, asi que el aviso solo la actualiza.
        en_hilo(self._comprobar_gstreamer, self._gstreamer_comprobado,
                lambda _exc: None, "video-gst")

    def _comprobar_gstreamer(self):
        """(disponible, motivo) en un hilo, sin tocar el panel."""
        video, error = cargar_video()
        if video is None:
            return False, "no se pudo importar cfv235.video: %s" % error
        try:
            if video.gstreamer_disponible():
                return True, ""
            return False, video.motivo_sin_gstreamer()
        except Exception as exc:                  # noqa: BLE001
            return False, "%s: %s" % (type(exc).__name__, exc)

    def _gstreamer_comprobado(self, datos):
        """Aplica el resultado de mirar GStreamer (hilo de la interfaz)."""
        disponible, motivo = datos
        video, error = cargar_video()
        if video is None:
            self.aviso_gstreamer.set_title("Falta el modulo cfv235.video: %s" % error)
            self.aviso_gstreamer.set_revealed(True)
            return False
        if not disponible:
            self.aviso_gstreamer.set_title(
                "GStreamer no esta disponible (%s). Los GIF, los PNG y las carpetas de "
                "imagenes funcionan igual; los ficheros de video no." % motivo)
            self.aviso_gstreamer.set_revealed(True)
        return False

    def _botones_fuente_video(self):
        """Caja con los dos botones de eleccion de fuente (fichero o carpeta)."""
        caja = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        caja.set_halign(Gtk.Align.CENTER)
        boton_fichero = Gtk.Button(label="Elegir fichero...")
        boton_fichero.add_css_class("pill")
        boton_fichero.add_css_class("suggested-action")
        boton_fichero.set_tooltip_text("GIF animado o video (mp4, mkv, webm, avi, mov).")
        boton_fichero.connect("clicked", self._elegir_video_fichero)
        boton_carpeta = Gtk.Button(label="Elegir carpeta...")
        boton_carpeta.add_css_class("pill")
        boton_carpeta.set_tooltip_text("Carpeta con imagenes PNG/JPEG en orden natural.")
        boton_carpeta.connect("clicked", self._elegir_video_carpeta)
        caja.append(boton_fichero)
        caja.append(boton_carpeta)
        return caja

    def _video_sin_fuente(self):
        """Estado vacio de la pagina Video (con los botones a mano)."""
        video, error = cargar_video()
        if video is None:
            return estado_vacio(
                "Falta el modulo de video",
                "No se pudo importar cfv235.video (%s). La reproduccion no estara "
                "disponible, pero el resto de la aplicacion funciona igual." % error,
                "dialog-warning-symbolic")
        return estado_vacio(
            "Sin fuente de video",
            "Elige un GIF animado, un fichero de video (mp4, mkv, webm, avi, mov) o una "
            "carpeta con imagenes. El panel muestra los fotogramas como PNG, uno detras "
            "de otro, y sin sonido.",
            "video-x-generic-symbolic", self._botones_fuente_video())

    def _contenido_video(self):
        """Controles de la pagina Video: fuente, ajustes, reproduccion, estado, informe."""
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        # --- fuente
        grupo_fuente = Adw.PreferencesGroup(
            title="Fuente",
            description="Un fichero (GIF animado o video) o una carpeta con imagenes "
                        "PNG/JPEG.")
        self.fila_video_fuente = fila_accion("Sin elegir", "Ninguna fuente todavia.",
                                             icono="video-x-generic-symbolic")
        boton_fichero = Gtk.Button(label="Fichero...")
        boton_fichero.set_valign(Gtk.Align.CENTER)
        boton_fichero.set_tooltip_text("GIF animado o video (mp4, mkv, webm, avi, mov).")
        boton_fichero.connect("clicked", self._elegir_video_fichero)
        boton_carpeta = Gtk.Button(label="Carpeta...")
        boton_carpeta.set_valign(Gtk.Align.CENTER)
        boton_carpeta.set_tooltip_text("Carpeta con imagenes PNG/JPEG en orden natural.")
        boton_carpeta.connect("clicked", self._elegir_video_carpeta)
        self.fila_video_fuente.add_suffix(boton_fichero)
        self.fila_video_fuente.add_suffix(boton_carpeta)
        grupo_fuente.add(self.fila_video_fuente)

        self.fila_video_inspeccionar = fila_accion(
            "Inspeccionar", "Lee el tipo, los fotogramas y el tamano sin tocar el panel.",
            icono="dialog-information-symbolic")
        self.spinner_inspeccion = Gtk.Spinner()
        self.spinner_inspeccion.set_valign(Gtk.Align.CENTER)
        self.spinner_inspeccion.set_visible(False)
        self.boton_inspeccionar = Gtk.Button(label="Inspeccionar")
        self.boton_inspeccionar.set_valign(Gtk.Align.CENTER)
        self.boton_inspeccionar.connect("clicked", self._inspeccionar_video)
        self.fila_video_inspeccionar.add_suffix(self.spinner_inspeccion)
        self.fila_video_inspeccionar.add_suffix(self.boton_inspeccionar)
        grupo_fuente.add(self.fila_video_inspeccionar)
        caja.append(grupo_fuente)

        # --- ajustes de reproduccion
        grupo_ajustes = Adw.PreferencesGroup(
            title="Ajustes de reproduccion",
            description=AVISO_FPS_VIDEO)
        # Los fps, el bucle y el ajuste se recuerdan entre arranques.
        fps_guardados = self._entero_guardado("fps", FPS_INICIAL_VIDEO)
        fps_guardados = max(FPS_MINIMO_VIDEO, min(FPS_MAXIMO_VIDEO, fps_guardados))
        ajuste_fps = Gtk.Adjustment(value=fps_guardados, lower=FPS_MINIMO_VIDEO,
                                    upper=FPS_MAXIMO_VIDEO, step_increment=1,
                                    page_increment=1)
        self.escala_fps = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                    adjustment=ajuste_fps)
        self.escala_fps.set_digits(0)
        self.escala_fps.set_draw_value(True)
        self.escala_fps.set_size_request(220, -1)
        self.escala_fps.set_tooltip_text(AVISO_FPS_VIDEO)
        self.escala_fps.connect("value-changed", self._cambiar_fps)
        self.fila_video_fps = fila_accion("Fotogramas por segundo", AVISO_FPS_VIDEO,
                                          icono="speedometer-symbolic")
        self.fila_video_fps.add_suffix(self.escala_fps)
        grupo_ajustes.add(self.fila_video_fps)

        self.interruptor_bucle = Adw.SwitchRow(
            title="Repetir en bucle",
            subtitle="Al acabar la fuente, vuelve a empezar por el primer fotograma.")
        self.interruptor_bucle.set_icon_name("media-playlist-repeat-symbolic")
        self.interruptor_bucle.set_active(bool(self._config.get("bucle", True)))
        self.interruptor_bucle.connect("notify::active", self._cambiar_bucle_video)
        grupo_ajustes.add(self.interruptor_bucle)

        self.combo_ajuste_video = Adw.ComboRow(
            title="Ajuste a 1920x462",
            subtitle="Como se encaja la imagen original en la pantalla del panel.",
            model=Gtk.StringList.new([etiqueta for _clave, etiqueta in AJUSTES_VIDEO]))
        self.combo_ajuste_video.set_icon_name("zoom-fit-best-symbolic")
        self.combo_ajuste_video.set_selected(self._indice_ajuste_video())
        self.combo_ajuste_video.connect("notify::selected", self._cambiar_ajuste_video)
        grupo_ajustes.add(self.combo_ajuste_video)
        caja.append(grupo_ajustes)

        # --- reproduccion
        grupo_repro = Adw.PreferencesGroup(
            title="Reproduccion",
            description="Cada fotograma se sube a la capa OSD con un nombre fijo: el panel "
                        "reutiliza su hueco y no acumula memoria.")
        self.fila_video_reproducir = fila_accion("Reproducir", "Elige antes una fuente.",
                                                 icono="media-playback-start-symbolic")
        self.spinner_video = Gtk.Spinner()
        self.spinner_video.set_valign(Gtk.Align.CENTER)
        self.spinner_video.set_visible(False)
        self.boton_video_reproducir = Gtk.Button(label="Reproducir")
        self.boton_video_reproducir.add_css_class("suggested-action")
        self.boton_video_reproducir.set_valign(Gtk.Align.CENTER)
        self.boton_video_reproducir.connect("clicked", self._reproducir_video)
        self.boton_video_parar = Gtk.Button(label="Parar")
        self.boton_video_parar.set_valign(Gtk.Align.CENTER)
        self.boton_video_parar.set_sensitive(False)
        self.boton_video_parar.set_tooltip_text("No hay ninguna reproduccion en marcha.")
        self.boton_video_parar.connect("clicked", self._parar_reproduccion_video)
        self.fila_video_reproducir.add_suffix(self.spinner_video)
        self.fila_video_reproducir.add_suffix(self.boton_video_reproducir)
        self.fila_video_reproducir.add_suffix(self.boton_video_parar)
        grupo_repro.add(self.fila_video_reproducir)
        caja.append(grupo_repro)

        # --- estado
        grupo_estado = Adw.PreferencesGroup(
            title="Estado",
            description="Contadores de la ultima reproduccion.")
        self.fila_video_fotogramas = fila_accion("Fotogramas subidos", "0",
                                                 icono="emblem-ok-symbolic")
        self.fila_video_resumen = fila_accion("Resumen", "Todavia no se ha reproducido nada.",
                                              icono="emblem-synchronizing-symbolic")
        self.fila_video_ultimo_error = fila_accion("Ultimo error", "(ninguno)",
                                                   icono="dialog-warning-symbolic")
        for fila in (self.fila_video_fotogramas, self.fila_video_resumen,
                     self.fila_video_ultimo_error):
            grupo_estado.add(fila)
        caja.append(grupo_estado)

        # --- informe y primer fotograma
        grupo_informe = Adw.PreferencesGroup(
            title="Informe de la fuente",
            description="Lo que devuelve video.informe_legible() al inspeccionar, con el "
                        "primer fotograma ya ajustado a 1920x462.")
        caja.append(grupo_informe)
        self.picture_video = Gtk.Picture()
        self.picture_video.set_can_shrink(True)
        self.picture_video.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture_video.set_size_request(-1, 220)
        marco_picture = Gtk.Frame()
        marco_picture.add_css_class("view")
        marco_picture.set_child(self.picture_video)
        caja.append(marco_picture)
        marco_texto, self.texto_video = vista_de_texto(180)
        poner_texto(self.texto_video, "Elige una fuente y pulsa Inspeccionar.")
        caja.append(marco_texto)

        # Sin fuente, Inspeccionar y Reproducir quedan apagados y dicen por que.
        self._actualizar_acciones_video()
        return caja

    # --- eleccion de fuente

    def _elegir_video_fichero(self, *_):
        video, error = cargar_video()
        if video is None:
            self.avisar_error("Falta el modulo cfv235.video: %s" % error)
            return False
        filtro = Gtk.FileFilter()
        filtro.set_name("Video y animaciones (GIF, MP4, MKV, WEBM, AVI, MOV)")
        for tipo in ("video/mp4", "video/x-matroska", "video/webm", "video/quicktime",
                     "video/x-msvideo", "image/gif"):
            filtro.add_mime_type(tipo)
        for patron in ("*.gif", "*.mp4", "*.mkv", "*.webm", "*.avi", "*.mov"):
            filtro.add_pattern(patron)
        abrir_dialogo_fichero(self, "Elegir video o animacion para el panel",
                              self._video_fichero_elegido,
                              carpeta=self._config.get("carpeta_video") or "",
                              filtros=(filtro, filtro_todos()))
        return False

    def _video_fichero_elegido(self, ruta):
        if not ruta:
            return
        self._fijar_fuente_video(ruta)

    def _elegir_video_carpeta(self, *_):
        video, error = cargar_video()
        if video is None:
            self.avisar_error("Falta el modulo cfv235.video: %s" % error)
            return False
        abrir_dialogo_fichero(self, "Elegir una carpeta con imagenes para el panel",
                              self._video_carpeta_elegida,
                              carpeta=self._config.get("carpeta_video") or "",
                              accion=Gtk.FileChooserAction.SELECT_FOLDER,
                              etiqueta_aceptar="Elegir carpeta")
        return False

    def _video_carpeta_elegida(self, ruta):
        if not ruta:
            return
        self._fijar_fuente_video(ruta)

    def _fijar_fuente_video(self, ruta):
        """Apunta la fuente elegida, pasa al contenido e inspecciona en un hilo."""
        self._video_ruta = ruta
        # Se recuerda la carpeta para la proxima vez (la del fichero o la elegida).
        self._guardar(carpeta_video=os.path.dirname(ruta) if not os.path.isdir(ruta) else ruta)
        try:
            if os.path.isdir(ruta):
                cuantos = len(os.listdir(ruta))
                self.fila_video_fuente.set_title(os.path.basename(ruta.rstrip("/")) or ruta)
                self.fila_video_fuente.set_subtitle("Carpeta con %d entradas." % cuantos)
            else:
                kb = os.path.getsize(ruta) / 1024.0
                self.fila_video_fuente.set_title(os.path.basename(ruta))
                self.fila_video_fuente.set_subtitle("Fichero de %.0f KB." % kb)
        except OSError as exc:
            self.fila_video_fuente.set_title(os.path.basename(ruta))
            self.fila_video_fuente.set_subtitle("No se pudo leer: %s" % exc)

        self.pila_video.set_visible_child_name("contenido")
        self._actualizar_acciones_video()
        self.avisar("Fuente elegida: %s" % os.path.basename(ruta))
        self._inspeccionar_video()

    # --- inspeccion (no toca el panel)

    def _inspeccionar_video(self, *_):
        video, error = cargar_video()
        if video is None:
            self.avisar_error("Falta el modulo cfv235.video: %s" % error)
            self._actualizar_acciones_video()
            return False
        ruta = self._video_ruta
        if not ruta:
            self.avisar("Elige antes un fichero o una carpeta.")
            return False

        ajuste = self._ajuste_video()
        self.spinner_inspeccion.set_visible(True)
        self.spinner_inspeccion.start()
        self.boton_inspeccionar.set_sensitive(False)
        self.fila_video_inspeccionar.set_subtitle("Mirando %s..." % os.path.basename(ruta))

        def tarea():
            informe = video.inspeccionar(ruta)
            texto = video.informe_legible(informe)
            png = None
            if not informe.get("error"):
                png = self._primer_fotograma_video(video, ruta, ajuste)
            return {"informe": informe, "texto": texto, "png": png}

        en_hilo(tarea, self._video_inspeccionado, self._video_inspeccion_fallida,
                "video-informe")
        return False

    def _primer_fotograma_video(self, modulo, ruta, ajuste):
        """PNG del primer fotograma ya ajustado al panel, EN MEMORIA. Corre en un hilo.

        Devuelve None si no se puede sacar: el informe ya cuenta el motivo, asi que la
        pagina se queda sin imagen y sigue funcionando. Antes se guardaba en un fichero
        temporal de nombre fijo (y dos inspecciones a la vez se pisaban).
        """
        fuente = None
        try:
            fuente = modulo.abrir_fuente(ruta, ajuste=ajuste)
            cuadro = fuente.siguiente()
            if cuadro is None:
                return None
            almacen = io.BytesIO()
            cuadro[0].save(almacen, format="PNG")
            return almacen.getvalue()
        except Exception:                         # noqa: BLE001
            return None
        finally:
            if fuente is not None:
                try:
                    fuente.cerrar()
                except Exception:                 # noqa: BLE001
                    pass

    def _video_inspeccionado(self, datos):
        self.spinner_inspeccion.stop()
        self.spinner_inspeccion.set_visible(False)
        informe = datos.get("informe") or {}
        self._video_informe = informe
        poner_texto(self.texto_video, datos.get("texto") or "(sin informe)")
        if datos.get("png"):
            poner_imagen(self.picture_video, datos["png"])
        if informe.get("error"):
            self.fila_video_inspeccionar.set_subtitle("No se pudo leer: %s"
                                                      % informe["error"])
            self.avisar_error("No se pudo inspeccionar: %s" % informe["error"])
        else:
            self.fila_video_inspeccionar.set_subtitle(informe.get("detalle") or "Leido.")
            self.avisar("Informe listo")
        self._actualizar_acciones_video()

    def _video_inspeccion_fallida(self, exc):
        self.spinner_inspeccion.stop()
        self.spinner_inspeccion.set_visible(False)
        poner_texto(self.texto_video, _texto_error(exc))
        self.fila_video_inspeccionar.set_subtitle("Fallo al inspeccionar la fuente.")
        self.avisar_error(_texto_error(exc, corto=True))
        self._actualizar_acciones_video()

    # --- reproduccion (hilo + threading.Event, como el bucle del dashboard)

    def _indice_ajuste_video(self):
        """Posicion del ajuste guardado en el combo (0 si ya no existe)."""
        guardado = self._config.get("ajuste")
        for indice, (clave, _etiqueta) in enumerate(AJUSTES_VIDEO):
            if clave == guardado:
                return indice
        return 0

    def _ajuste_video(self):
        indice = self.combo_ajuste_video.get_selected()
        if 0 <= indice < len(AJUSTES_VIDEO):
            return AJUSTES_VIDEO[indice][0]
        return AJUSTES_VIDEO[0][0]

    def _cambiar_ajuste_video(self, fila, _parametro=None):
        """El ajuste elegido (ajustar/recortar/estirar) se recuerda."""
        indice = fila.get_selected()
        if 0 <= indice < len(AJUSTES_VIDEO):
            self._guardar(ajuste=AJUSTES_VIDEO[indice][0])

    def _cambiar_bucle_video(self, fila, _parametro=None):
        """El interruptor de bucle se recuerda."""
        self._guardar(bucle=bool(fila.get_active()))

    def _cambiar_fps(self, escala):
        """Aviso de que por encima de ~3 fps el panel salta fotogramas."""
        fps = int(round(escala.get_value()))
        self._guardar(fps=fps)
        if fps > 4:
            self.fila_video_fps.set_subtitle(
                "A %d fps el panel no da mas de si: cada subida tarda unos 160 ms y se "
                "saltaran fotogramas de la fuente." % fps)
        else:
            self.fila_video_fps.set_subtitle(AVISO_FPS_VIDEO)

    def _actualizar_acciones_video(self):
        """Habilita Inspeccionar/Reproducir segun haya fuente, y dice el motivo si no."""
        video, error = cargar_video()

        hay = bool(self._video_ruta) and video is not None and not self._video_activo
        for boton in (self.boton_inspeccionar, self.boton_video_reproducir):
            boton.set_sensitive(hay)

        if video is None:
            motivo = "Falta el modulo cfv235.video: %s" % error
            self.boton_inspeccionar.set_tooltip_text(motivo)
            self.boton_video_reproducir.set_tooltip_text(motivo)
            self.fila_video_reproducir.set_subtitle(motivo)
        elif not self._video_ruta:
            motivo = "Elige antes un fichero o una carpeta."
            self.boton_inspeccionar.set_tooltip_text(motivo)
            self.boton_video_reproducir.set_tooltip_text(motivo)
            self.fila_video_reproducir.set_subtitle(motivo)
        elif self._video_activo:
            self.boton_inspeccionar.set_tooltip_text("Hay una reproduccion en marcha.")
            self.boton_video_reproducir.set_tooltip_text("Hay una reproduccion en marcha.")
            self.fila_video_reproducir.set_subtitle(
                "Reproduciendo: %d fotogramas subidos." % self._video_fotogramas)
        else:
            self.boton_inspeccionar.set_tooltip_text(
                "Lee el tipo, los fotogramas y el tamano de la fuente.")
            self.boton_video_reproducir.set_tooltip_text("Sube los fotogramas al panel.")
            self.fila_video_reproducir.set_subtitle(
                "Listo para reproducir %s." % os.path.basename(self._video_ruta))

        self.boton_video_parar.set_sensitive(self._video_activo)
        self.boton_video_parar.set_tooltip_text(
            "Corta la reproduccion en marcha." if self._video_activo
            else "No hay ninguna reproduccion en marcha.")

    def _reproducir_video(self, *_):
        video, error = cargar_video()
        if video is None:
            self.avisar_error("Falta el modulo cfv235.video: %s" % error)
            return False
        if self._video_activo:
            self.avisar("Ya hay una reproduccion en marcha.")
            return False
        ruta = self._video_ruta
        if not ruta:
            self.avisar("Elige antes un fichero o una carpeta.")
            return False

        fps = max(FPS_MINIMO_VIDEO,
                  min(FPS_MAXIMO_VIDEO, int(round(self.escala_fps.get_value()))))
        bucle = self.interruptor_bucle.get_active()
        ajuste = self._ajuste_video()

        self._parar_video = threading.Event()
        parar = self._parar_video
        self._video_activo = True
        self._video_fotogramas = 0
        self.boton_video_reproducir.set_label("Reproduciendo")
        self.boton_video_reproducir.remove_css_class("suggested-action")
        self.spinner_video.set_visible(True)
        self.spinner_video.start()
        self.fila_video_ultimo_error.set_subtitle("(ninguno)")
        self.fila_video_resumen.set_subtitle("Arrancando...")
        self._actualizar_acciones_video()

        self._hilo_video = en_hilo(
            lambda: self._bucle_video(video, ruta, fps, bucle, ajuste, parar),
            self._video_terminado, self._video_fallido, "video")
        return False

    def _bucle_video(self, modulo, ruta, fps, bucle, ajuste, parar):
        """Hilo de reproduccion: abre la fuente, sube fotogramas y los cuenta.

        El panel se toma y se suelta en cada fotograma (`PanelPrestado`), de modo que las
        otras paginas siguen pudiendo usarlo entre fotograma y fotograma.
        """
        fuente = modulo.abrir_fuente(ruta, ajuste=ajuste)
        reproductor = None
        try:
            reproductor = modulo.Reproductor(
                PanelPrestado(self.panel), fuente,
                fps=fps, bucle=bucle, capa="osd")
            self._video_reproductor = reproductor

            def avisar(_n, ok, error, ms):
                GLib.idle_add(self._video_fotograma, reproductor, ok, error, ms)

            reproductor.reproducir(parar=parar, avisar=avisar)
            return {"subidos": reproductor.fotogramas, "resumen": reproductor.resumen()}
        finally:
            if reproductor is not None:
                try:
                    reproductor.cerrar()
                except Exception:                 # noqa: BLE001
                    pass
            else:
                try:
                    fuente.cerrar()
                except Exception:                 # noqa: BLE001
                    pass
            self._video_reproductor = None

    def _video_fotograma(self, reproductor, ok, error, ms):
        """Contadores del fotograma recien subido (hilo de la interfaz)."""
        if not self._video_activo:
            return False
        if ok:
            self._video_fotogramas += 1
        self.fila_video_fotogramas.set_subtitle(str(self._video_fotogramas))
        self.fila_video_resumen.set_subtitle(
            "%d intentos, %d saltados, %.0f ms el ultimo"
            % (reproductor.intentos, reproductor.saltados, ms))
        self.fila_video_ultimo_error.set_subtitle(error or "(ninguno)")
        self.fila_video_reproducir.set_subtitle(
            "Reproduciendo: %d fotogramas subidos%s"
            % (self._video_fotogramas, "" if not error else " (con errores)"))
        return False

    def _parar_reproduccion_video(self, *_):
        """Boton Parar: levanta el Event; el hilo corta al momento."""
        if not self._video_activo:
            self.avisar("No hay ninguna reproduccion en marcha.")
            return False
        if self._parar_video is not None:
            self._parar_video.set()
        self.boton_video_parar.set_sensitive(False)
        self.fila_video_reproducir.set_subtitle("Parando la reproduccion...")
        return False

    def _reposar_video(self):
        """Deja los controles en estado parado (idempotente)."""
        self._video_activo = False
        self.spinner_video.stop()
        self.spinner_video.set_visible(False)
        self.boton_video_reproducir.set_label("Reproducir")
        self.boton_video_reproducir.add_css_class("suggested-action")
        self._actualizar_acciones_video()

    def _video_terminado(self, datos):
        """El hilo de reproduccion ha acabado (fin de la fuente o Parar)."""
        parada = self._parar_video is not None and self._parar_video.is_set()
        resumen = (datos or {}).get("resumen") or ""
        self._reposar_video()
        self.fila_video_resumen.set_subtitle(resumen or "Sin datos.")
        self.avisar("Reproduccion %s: %d fotogramas subidos"
                    % ("parada" if parada else "terminada", self._video_fotogramas))
        return False

    def _video_fallido(self, exc):
        self._reposar_video()
        self.fila_video_reproducir.set_subtitle("No se pudo reproducir la fuente.")
        self.fila_video_ultimo_error.set_subtitle(_texto_error(exc))
        self.avisar_error(_texto_error(exc, corto=True))

    # ------------------------------------------------------------------ pagina 6: Diagnostico

    def _pagina_diagnostico(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        grupo = Adw.PreferencesGroup(
            title="Diagnostico del canal",
            description="Descriptor HID y resultado de la negociacion de escritura.")
        self.fila_diagnostico = fila_accion("Estado", "Sin leer todavia.",
                                            icono="utilities-terminal-symbolic")
        boton_diag = Gtk.Button(label="Actualizar")
        boton_diag.set_valign(Gtk.Align.CENTER)
        boton_diag.set_tooltip_text("Vuelve a leer el descriptor y la negociacion.")
        boton_diag.connect("clicked", self._actualizar_diagnostico)
        boton_sondear = Gtk.Button(label="Sondear canal")
        boton_sondear.set_valign(Gtk.Align.CENTER)
        boton_sondear.set_tooltip_text("Lanza herramientas/sondear_canal.py (tarda).")
        boton_sondear.connect("clicked", self._sondear_canal)
        self.fila_diagnostico.add_suffix(boton_diag)
        self.fila_diagnostico.add_suffix(boton_sondear)
        grupo.add(self.fila_diagnostico)
        caja.append(grupo)

        _marco_diag, self.texto_diagnostico = vista_de_texto(240)
        poner_texto(self.texto_diagnostico, "Pulsa Actualizar para leer el canal.")

        # Estado de espera del volcado: se cambia por el texto en cuanto llega la lectura.
        boton_vacio = Gtk.Button(label="Leer el canal")
        boton_vacio.add_css_class("pill")
        boton_vacio.add_css_class("suggested-action")
        boton_vacio.connect("clicked", self._actualizar_diagnostico)
        self.pagina_diagnostico = apilar(
            ("espera", estado_vacio(
                "Sin leer todavia",
                "Pulsa Leer el canal para ver el descriptor HID, la negociacion y las "
                "propiedades del panel. Todo se lee en un hilo.",
                "utilities-terminal-symbolic", boton_vacio)),
            ("contenido", _marco_diag))
        self.pagina_diagnostico.set_visible_child_name("espera")
        caja.append(self.pagina_diagnostico)

        # --- comandos raw
        grupo_raw = Adw.PreferencesGroup(
            title="Comando raw",
            description="Se envia tal cual por el canal: metodo, cmd y cuerpo JSON opcional.")

        self.entrada_metodo = Gtk.Entry()
        self.entrada_metodo.set_text("POST")
        self.entrada_metodo.set_width_chars(8)
        self.entrada_metodo.set_valign(Gtk.Align.CENTER)
        fila_metodo = fila_accion("Metodo", icono="insert-text-symbolic")
        fila_metodo.add_suffix(self.entrada_metodo)
        grupo_raw.add(fila_metodo)

        self.entrada_cmd = Gtk.Entry()
        self.entrada_cmd.set_text("conn")
        self.entrada_cmd.set_valign(Gtk.Align.CENTER)
        self.entrada_cmd.set_hexpand(True)
        self.entrada_cmd.connect("activate", self._enviar_raw)
        fila_cmd = fila_accion("cmd", icono="insert-text-symbolic")
        fila_cmd.add_suffix(self.entrada_cmd)
        grupo_raw.add(fila_cmd)
        caja.append(grupo_raw)

        etiqueta_cuerpo = Gtk.Label(label="Cuerpo JSON (vacio = sin cuerpo)", xalign=0)
        etiqueta_cuerpo.add_css_class("dim-label")
        caja.append(etiqueta_cuerpo)
        _marco_cuerpo, self.texto_cuerpo = vista_de_texto(90, editable=True)
        poner_texto(self.texto_cuerpo, "")
        caja.append(_marco_cuerpo)

        fila_enviar = fila_accion("Enviar comando", "La respuesta aparece abajo.",
                                  icono="mail-send-symbolic")
        self.casilla_cuerpo = Gtk.CheckButton(
            label="Esperar cuerpo (ignorar los 200 sin cuerpo)")
        self.casilla_cuerpo.set_active(True)
        self.casilla_cuerpo.set_valign(Gtk.Align.CENTER)
        self.casilla_cuerpo.set_tooltip_text(
            "Este firmware contesta 200 sin cuerpo a peticiones que todavia no ha "
            "atendido; con esto se sigue esperando hasta que llega la respuesta buena.")
        self.boton_raw = Gtk.Button(label="Enviar")
        self.boton_raw.add_css_class("suggested-action")
        self.boton_raw.set_valign(Gtk.Align.CENTER)
        self.boton_raw.connect("clicked", self._enviar_raw)
        fila_enviar.add_suffix(self.casilla_cuerpo)
        fila_enviar.add_suffix(self.boton_raw)
        caja.append(fila_enviar)

        grupo_respuesta = Adw.PreferencesGroup(
            title="Respuesta",
            description="Lo que conteste el panel a la ultima peticion.")
        _marco_resp, self.texto_respuesta = vista_de_texto(140)
        poner_texto(self.texto_respuesta, "(sin respuesta)")
        caja.append(grupo_respuesta)
        caja.append(_marco_resp)

        # --- servicio del dashboard: estado y registro, para depurar sin terminal
        grupo_servicio = Adw.PreferencesGroup(
            title="Servicio del dashboard",
            description="Estado de systemd de usuario y ultimas lineas del journal del "
                        "servicio (%s). Solo lectura." % NOMBRE_SERVICIO)
        self.fila_servicio_diag = fila_accion("Estado", "Sin leer todavia.",
                                              icono="system-run-symbolic")
        boton_diag_servicio = Gtk.Button(label="Actualizar")
        boton_diag_servicio.set_valign(Gtk.Align.CENTER)
        boton_diag_servicio.connect("clicked", self._refrescar_servicio)
        boton_registro_diag = Gtk.Button(label="Ver registro")
        boton_registro_diag.set_valign(Gtk.Align.CENTER)
        boton_registro_diag.connect("clicked", self._ver_registro_servicio)
        self.fila_servicio_diag.add_suffix(boton_diag_servicio)
        self.fila_servicio_diag.add_suffix(boton_registro_diag)
        grupo_servicio.add(self.fila_servicio_diag)
        caja.append(grupo_servicio)
        _marco_servicio, self.texto_servicio_diag = vista_de_texto(180)
        poner_texto(self.texto_servicio_diag,
                    "El estado del servicio y su registro aparecen aqui.")
        caja.append(_marco_servicio)

        self._anadir_pagina(pagina_desplazable(caja), "diagnostico", "Diagnostico",
                            "utilities-terminal-symbolic")

        GLib.idle_add(self._actualizar_diagnostico)

    def _leer_diagnostico(self):
        """En un hilo: todo lo que se puede saber del panel sin cambiar nada.

        Como en `_leer_estado`, se usa el panel que devuelve el cerrojo: una referencia
        capturada antes seguiria apuntando al panel viejo tras una reconexion.
        """
        with self.panel.sesion() as panel:
            info = {"dispositivo": panel.dispositivo}
            if panel.dispositivo:
                try:
                    info["hardware"] = canal.info_dispositivo(panel.dispositivo)
                except Exception as exc:          # noqa: BLE001
                    info["error_hardware"] = str(exc)
            variante = panel.canal.variante
            info["variante"] = variante.describe() if variante is not None else "(sin negociar)"
            info["negociacion"] = [dict(x) for x in panel.canal.negociacion]
            info["propiedades"] = panel.propiedades_seguras()
        return info

    def _actualizar_diagnostico(self, *_):
        self.fila_diagnostico.set_subtitle("Leyendo el canal...")
        en_hilo(self._leer_diagnostico, self._diagnostico_hecho,
                self._diagnostico_fallido, "diagnostico")
        # El mismo boton refresca tambien el estado del servicio, CON su registro: aqui si
        # interesa leer el journal (es la pagina de diagnostico).
        self._refrescar_servicio(con_registro=True)
        return False

    def _diagnostico_hecho(self, info):
        lineas = []
        lineas.append("DISPOSITIVO")
        lineas.append("  ruta          : %s" % (info.get("dispositivo") or "-"))
        lineas.append("  ficha         : %s" % identificar(info.get("dispositivo")))
        lineas.append("")

        hardware = info.get("hardware") or {}
        if info.get("error_hardware"):
            lineas.append("No se pudo leer el descriptor: %s" % info["error_hardware"])
        lineas.append("DESCRIPTOR HID")
        if hardware:
            for clave in ("vid", "pid", "nombre", "serie", "fisico", "driver",
                          "usb_manufacturer", "usb_product", "usb_serial", "usb_version",
                          "usb_bcddevice", "usb_maxpower", "usb_puertos"):
                if hardware.get(clave) not in (None, ""):
                    lineas.append("  %-14s: %s" % (clave, hardware[clave]))
            descriptor = hardware.get("descriptor")
            if descriptor:
                lineas.append("  descriptor    : %d bytes" % (len(descriptor) // 2))
                lineas.append("    %s" % descriptor[:160])
                if len(descriptor) > 160:
                    lineas.append("    ... (%d bytes mas)" % ((len(descriptor) - 160) // 2))
            analizado = hardware.get("descriptor_analizado") or {}
            lineas.append("")
            lineas.append("DESCRIPTOR ANALIZADO")
            for clave, valor in analizado.items():
                lineas.append("  %-14s: %s" % (clave, formatear_valor(valor)))
        else:
            lineas.append("  (no disponible)")

        lineas.append("")
        lineas.append("NEGOCIACION DEL CANAL")
        lineas.append("  elegida       : %s" % info.get("variante"))
        for intento in info.get("negociacion") or []:
            lineas.append("  - %-34s ok=%-5s code=%-5s %s"
                          % (intento.get("descripcion") or intento.get("variante"),
                             intento.get("ok"), intento.get("code"),
                             intento.get("error") or ""))

        lineas.append("")
        lineas.append("PROPIEDADES (POST conn)")
        props = info.get("propiedades") or {}
        if props:
            for clave in [c for c in ORDEN_PROPIEDADES if c in props]:
                lineas.append("  %-18s: %s" % (clave, formatear_propiedad(clave, props[clave])))
            for clave in sorted(c for c in props if c not in ORDEN_PROPIEDADES):
                lineas.append("  %-18s: %s" % (clave, formatear_propiedad(clave, props[clave])))
        else:
            lineas.append("  (el panel no contesto)")

        poner_texto(self.texto_diagnostico, "\n".join(lineas))
        self.pagina_diagnostico.set_visible_child_name("contenido")
        self.fila_diagnostico.set_subtitle(
            "Leido: %s" % (info.get("dispositivo") or "sin dispositivo"))

    def _diagnostico_fallido(self, exc):
        poner_texto(self.texto_diagnostico, _texto_error(exc))
        self.pagina_diagnostico.set_visible_child_name("contenido")
        self.fila_diagnostico.set_subtitle("Fallo al leer el canal.")
        self.avisar_error(_texto_error(exc, corto=True))
        self._recuperar_panel(exc)

    def _enviar_raw(self, *_):
        metodo = (self.entrada_metodo.get_text() or "POST").strip().upper()
        cmd = (self.entrada_cmd.get_text() or "").strip()
        if not cmd:
            self.avisar("Hace falta un `cmd`.")
            return False

        bruto = leer_texto(self.texto_cuerpo).strip()
        cuerpo = None
        if bruto:
            try:
                cuerpo = json.loads(bruto)
            except ValueError as exc:
                poner_texto(self.texto_respuesta,
                            "El cuerpo no es JSON valido:\n%s" % exc)
                self.avisar_error("JSON invalido")
                return False

        self.boton_raw.set_sensitive(False)
        poner_texto(self.texto_respuesta, "Enviando %s %s..." % (metodo, cmd))
        esperar_cuerpo = self.casilla_cuerpo.get_active()

        def tarea(panel):
            # Se tira lo que haya pendiente para no ensenar la respuesta de una
            # peticion anterior.
            panel.canal.drenar(0.05)
            return panel.canal.peticion(cmd, cuerpo, metodo=metodo, timeout=5.0,
                                        aceptar_vacio=not esperar_cuerpo)

        def hecho(respuesta):
            self.boton_raw.set_sensitive(True)
            poner_texto(self.texto_respuesta, _detalle_respuesta(respuesta))
            self.avisar("%s %s -> code=%s" % (metodo, cmd, respuesta.code))

        def fallo(exc):
            self.boton_raw.set_sensitive(True)
            poner_texto(self.texto_respuesta, _texto_error(exc))
            self.avisar_error(_texto_error(exc, corto=True))

        en_hilo(lambda: self.panel.usar(tarea), hecho, fallo, "raw")
        return False

    def _sondear_canal(self, *_):
        if not os.path.exists(RUTA_SONDEAR):
            poner_texto(self.texto_diagnostico,
                        "No existe el script de sondeo:\n%s" % RUTA_SONDEAR)
            self.avisar("Falta herramientas/sondear_canal.py")
            return False

        self.fila_diagnostico.set_subtitle("Sondeando el canal (tarda unos segundos)...")

        def tarea():
            ruta = self.panel.ruta or canal.buscar()
            orden = [sys.executable, RUTA_SONDEAR]
            if ruta:
                orden += ["--device", ruta]
            proceso = subprocess.run(orden, capture_output=True, text=True, timeout=300,
                                     cwd=RAIZ_APP)
            salida = (proceso.stdout or "") + (proceso.stderr or "")
            return {"ruta": ruta, "codigo": proceso.returncode, "salida": salida}

        en_hilo(tarea, self._sondeo_hecho, self._sondeo_fallido, "sondeo")
        return False

    def _sondeo_hecho(self, datos):
        cabecera = ("$ python3 herramientas/sondear_canal.py --device %s\n"
                    "(codigo de salida: %s)\n\n" % (datos["ruta"], datos["codigo"]))
        poner_texto(self.texto_diagnostico, cabecera + datos["salida"])
        self.pagina_diagnostico.set_visible_child_name("contenido")
        self.fila_diagnostico.set_subtitle("Sondeo terminado: code=%s" % datos["codigo"])
        self.avisar("Sondeo terminado")

    def _sondeo_fallido(self, exc):
        poner_texto(self.texto_diagnostico, _texto_error(exc))
        self.pagina_diagnostico.set_visible_child_name("contenido")
        self.fila_diagnostico.set_subtitle("El sondeo fallo.")
        self.avisar_error(_texto_error(exc, corto=True))

    # ---------------------------------------------------- el servicio del dashboard

    def _pagina_visible(self):
        """Nombre de la pagina que se esta viendo ("estado", "dashboard"...).

        OJO: `widget.get_name()` devuelve el nombre CSS del widget (salia "GtkStack" o
        "GtkScrolledWindow"), no el nombre con el que se registro la pagina. El correcto es
        `get_visible_child_name()`.
        """
        return self.pila.get_visible_child_name() or ""

    def _al_cambiar_pagina(self, pila, _parametro=None):
        """Relee el servicio al entrar en sus paginas y recuerda la pagina visible."""
        pagina = self._pagina_visible()
        if pagina in ("estado", "dashboard", "diagnostico"):
            # El journal solo se lee en Diagnostico (y al abrir "Ver registro"): leerlo en
            # cada refresco eran 3-4 procesos de systemctl/journalctl por vuelta.
            self._refrescar_servicio(con_registro=(pagina == "diagnostico"))
        self._guardar(pagina=pagina)
        return False

    # --- lectura (siempre en un hilo: systemctl puede tardar)

    def _servicio_disponible(self):
        """`servicio.disponible()` con cache: no hace falta preguntar a systemd cada vez.

        Cada llamada son uno o dos procesos `systemctl`; el estado no cambia de un refresco
        al siguiente (y menos al entrar y salir de una pagina). Se vacia la cache al hacer
        cualquier accion sobre el servicio.
        """
        ahora = time.time()
        cache = self._disponible_cache
        if cache is not None and ahora - cache[1] < 30.0:
            return cache[0]
        valor = bool(servicio.disponible())
        self._disponible_cache = (valor, ahora)
        return valor

    def _leer_servicio(self, con_registro=False):
        """Se ejecuta en un hilo: estado del servicio y quien tiene el panel.

        Es solo lectura (`show`, el fichero de bloqueo y, si se pide, el journal) y nunca
        lanza: si `systemctl` no existe, el nucleo no trae `cfv235.servicio` o el journal no
        responde, devuelve un diccionario con la misma forma que lo cuenta.
        """
        datos = {"modulo": servicio is not None, "disponible": False, "instalada": False,
                 "estado": {}, "ocupado": False, "pid_bloqueo": None,
                 "nombre": NOMBRE_SERVICIO, "registro": "", "error": ""}
        if servicio is None:
            return datos
        try:
            datos["nombre"] = servicio.NOMBRE
            datos["instalada"] = servicio.es_unidad_instalada()
            if not self._servicio_disponible():
                datos["registro"] = "systemd de usuario no disponible."
                return datos
            datos["disponible"] = True
            datos["estado"] = servicio.estado() or {}
            # El panel lo puede tener el servicio, el editor de COUGAR o un dashboard
            # lanzado a mano: `ocupado` solo es True cuando el pid del bloqueo es el del
            # servicio. Se calcula aqui con el estado que ya se ha leido, en vez de llamar
            # a `servicio.ocupado_por_el_servicio()` (que repetiria el `systemctl show`).
            datos["pid_bloqueo"] = servicio.pid_del_bloqueo(self.panel.ruta)
            pid_servicio = (datos["estado"] or {}).get("pid")
            datos["ocupado"] = (pid_servicio is not None
                                and datos["pid_bloqueo"] == pid_servicio)
            if con_registro:
                datos["registro"] = servicio.registro(LINEAS_REGISTRO_DIALOGO)
        except Exception as exc:                  # noqa: BLE001  (nada escapa al hilo)
            datos["error"] = "%s: %s" % (type(exc).__name__, exc)
        return datos

    def _refrescar_servicio(self, *_, con_registro=None):
        """Pide el estado del servicio en un hilo; se puede llamar desde cualquier sitio.

        Si ya hay una lectura en marcha no se lanza otra: se apunta que hace falta otra
        vuelta y se repite al terminar (asi ninguna peticion se pierde).

        `con_registro` decide si ademas se lee el journal: por defecto solo en la pagina
        Diagnostico, porque `journalctl` es un proceso mas en cada refresco.
        """
        if con_registro is None:
            con_registro = self._pagina_visible() == "diagnostico"
        if self._leyendo_servicio:
            self._servicio_pendiente = True
            self._con_registro_pendiente = self._con_registro_pendiente or con_registro
            return False
        self._leyendo_servicio = True
        self._con_registro_pendiente = False
        en_hilo(lambda: self._leer_servicio(con_registro), self._pintar_servicio,
                self._servicio_fallido, "servicio")
        return False

    def _repetir_lectura_servicio(self):
        """Si alguien pidio un refresco mientras se leia, se lanza ahora."""
        if self._servicio_pendiente:
            self._servicio_pendiente = False
            con_registro = self._con_registro_pendiente
            self._con_registro_pendiente = False
            self._refrescar_servicio(con_registro=con_registro)

    def _servicio_fallido(self, exc):
        """Fallo inesperado al leer el servicio: se cuenta sin romper nada."""
        self._leyendo_servicio = False
        self._servicio = {"modulo": servicio is not None,
                          "disponible": False, "instalada": False, "estado": {},
                          "ocupado": False, "pid_bloqueo": None,
                          "nombre": NOMBRE_SERVICIO,
                          "registro": _texto_error(exc)}
        self.fila_servicio_estado.set_subtitle("No se pudo leer el servicio: %s"
                                               % _texto_error(exc, corto=True))
        self.fila_servicio_diag.set_subtitle("No se pudo leer el servicio.")
        poner_texto(self.texto_servicio_diag, detalle_servicio(self._servicio))
        self.aviso_servicio_panel.set_revealed(False)
        # Sin datos no se puede afirmar que lo tenga el servicio: mejor no ofrecer nada.
        self.aviso_servicio.set_revealed(False)
        self._repetir_lectura_servicio()

    # --- pintado (hilo de la interfaz)

    def _bloquear_controles_servicio(self, bloqueado):
        """Desactiva los controles del servicio mientras hay una orden en marcha."""
        for control in (self.boton_servicio_arrancar, self.boton_servicio_parar,
                        self.boton_servicio_reiniciar, self.interruptor_servicio_auto):
            control.set_sensitive(not bloqueado)
        self.boton_servicio_registro.set_sensitive(True)

    def _pintar_servicio(self, datos):
        """Aplica el estado leido a las tres paginas (se llama con GLib.idle_add)."""
        self._leyendo_servicio = False
        self._servicio = datos
        disponible = bool(datos.get("disponible"))
        estado = datos.get("estado") or {}
        activo = bool(estado.get("activo"))

        # --- franja del Dashboard
        self.fila_servicio_estado.set_subtitle(resumen_servicio(datos))
        if self._accion_servicio_en_curso:
            self._bloquear_controles_servicio(True)
        else:
            self.boton_servicio_arrancar.set_sensitive(disponible and not activo)
            self.boton_servicio_parar.set_sensitive(disponible and activo)
            self.boton_servicio_reiniciar.set_sensitive(disponible and activo)
            self.boton_servicio_registro.set_sensitive(True)
            self._silenciar_servicio = True
            try:
                self.interruptor_servicio_auto.set_sensitive(disponible)
                self.interruptor_servicio_auto.set_active(bool(estado.get("habilitado")))
            finally:
                self._silenciar_servicio = False

        # --- aviso: con el servicio en marcha el panel esta tomado
        self.aviso_servicio_panel.set_revealed(disponible and activo)
        if disponible and activo and not self._dashboard_activo:
            self.boton_dashboard.set_sensitive(False)
            self.fila_dashboard.set_subtitle(
                "El servicio del dashboard tiene el panel: paralelo para usar este bucle.")
        else:
            self.boton_dashboard.set_sensitive(True)

        # --- aviso de la pagina Estado (solo con la unidad instalada)
        self._aviso_servicio_ocupado(datos)

        # Si el servicio se ha quedado con el panel y la aplicacion no lo tiene, la fila de
        # acciones no puede seguir diciendo "ultima consulta correcta".
        if (disponible and bool(datos.get("ocupado")) and activo
                and not self.panel.abierto):
            self.fila_acciones.set_subtitle("El panel lo tiene el servicio del dashboard.")

        # --- pagina Diagnostico
        self.fila_servicio_diag.set_subtitle(resumen_servicio(datos))
        poner_texto(self.texto_servicio_diag, detalle_servicio(datos))

        # --- el servicio se ha caido (estaba en marcha y ya no)
        if disponible:
            if self._servicio_estuvo_activo and not activo:
                self.avisar_error("El servicio del dashboard se ha parado.")
                self._notificar("servicio-caido", "Servicio del dashboard parado",
                                "El servicio %s ya no esta en marcha."
                                % (datos.get("nombre") or NOMBRE_SERVICIO),
                                accion="app.reconectar", etiqueta_accion="Reconectar")
            self._servicio_estuvo_activo = activo

        self._repetir_lectura_servicio()

    def _aviso_servicio_ocupado(self, datos):
        """Ensena (o esconde) el aviso de la pagina Estado.

        Solo sale si la unidad esta instalada y el servicio es quien tiene el panel: se
        confirma con `ocupado_por_el_servicio()`, y si el bloqueo no lo delata pero el
        servicio esta en marcha mientras la aplicacion no puede abrir el panel, se ofrece
        pararlo igualmente (es el sospechoso habitual). Si la aplicacion tiene el panel
        abierto, el aviso sobra.
        """
        instalada = bool(datos.get("instalada"))
        activo = bool((datos.get("estado") or {}).get("activo"))
        revelar = (instalada and not self.panel.abierto
                   and (bool(datos.get("ocupado")) or (activo and self._panel_ocupado)))
        if revelar:
            estado = datos.get("estado") or {}
            self.aviso_servicio.set_title(
                "El panel lo tiene el servicio del dashboard (%s, %s) y el panel solo "
                "admite una sesion."
                % (datos.get("nombre") or NOMBRE_SERVICIO,
                   estado.get("descripcion") or "en marcha"))
        self.aviso_servicio.set_revealed(revelar)

    # --- acciones (systemctl, siempre en un hilo)

    def _ocupado_por_el_servicio(self):
        """True si lo ultimo que se leyo dice que el servicio tiene el panel."""
        return bool(self._servicio.get("disponible")
                    and (self._servicio.get("estado") or {}).get("activo"))

    def _accion_servicio(self, nombre, descripcion, tarea):
        """Lanza `tarea()` (systemctl) en un hilo y refresca el estado al terminar."""
        if servicio is None:
            self.avisar("El nucleo no trae cfv235.servicio")
            return False
        if self._accion_servicio_en_curso:
            self.avisar("Hay otra orden del servicio en marcha.")
            return False
        self._accion_servicio_en_curso = True
        self._bloquear_controles_servicio(True)
        # El servicio va a cambiar: la cache de `disponible()` deja de valer.
        self._disponible_cache = None
        self.fila_servicio_estado.set_subtitle("%s..." % descripcion)
        en_hilo(tarea,
                lambda resultado: self._accion_servicio_hecha(nombre, descripcion, resultado),
                self._accion_servicio_fallida, "servicio-%s" % nombre)
        return False

    def _accion_servicio_hecha(self, nombre, descripcion, resultado):
        """Aplica el (ok, mensaje) de systemctl. Corre en el hilo de la interfaz."""
        self._accion_servicio_en_curso = False
        if isinstance(resultado, tuple) and len(resultado) == 2:
            ok, mensaje = resultado
        else:
            ok, mensaje = bool(resultado), ""
        if ok:
            # Confirmacion clara de lo que se acaba de hacer con el servicio.
            hecho = {"arrancar": "Servicio del dashboard arrancado",
                     "parar": "Servicio del dashboard parado",
                     "reiniciar": "Servicio del dashboard reiniciado",
                     "habilitar": "Arranque al iniciar sesion activado",
                     "deshabilitar": "Arranque al iniciar sesion desactivado"}.get(nombre)
            self.avisar(hecho or "%s: %s" % (descripcion, mensaje or "hecho"))
        else:
            self.avisar_error("%s: %s" % (descripcion, mensaje or "fallo"))

        # Cambia el dueno del panel, pero SOLO cuando la accion lo justifica y ha salido
        # bien: arrancar o reiniciar el servicio le traspasan el panel (y la unidad tiene
        # Restart=always, asi que hay que soltarlo antes de que arranque). `enable`,
        # `disable` y `parar` no tocan el panel, y una accion fallida tampoco: cerrarlo
        # entonces dejaba a la aplicacion sin panel sin ninguna razon.
        if ok and nombre in ("arrancar", "reiniciar"):
            self.panel.cerrar()
        self._refrescar_servicio()
        if nombre in ("arrancar", "reiniciar") and ok:
            # El panel pasa a ser del servicio: NO se intenta abrir aqui. Si se abriera, la
            # aplicacion le quitaria el bloqueo en la ventana de arranque del servicio y la
            # unidad (Restart=always) entraria en un bucle de reinicios. Se le da un respiro
            # y se vuelve a mirar quien lo tiene, que es lo que enciende el aviso.
            GLib.timeout_add(3000, self._refrescar_servicio)
        elif nombre == "parar" and ok:
            # Con el servicio parado el panel queda libre: se reintenta leerlo para que la
            # aplicacion se recupere sola y el aviso desaparezca.
            self._refrescar_estado()
        return False

    def _accion_servicio_fallida(self, exc):
        self._accion_servicio_en_curso = False
        self.avisar_error("La orden del servicio fallo: %s" % _texto_error(exc, corto=True))
        self._refrescar_servicio()

    def _arrancar_servicio(self, *_):
        if self._dashboard_activo:
            self.avisar("Para antes el dashboard en vivo de la aplicacion: el panel "
                        "solo admite una sesion.")
            return False
        # El servicio necesita el panel libre: si lo tiene esta aplicacion, se suelta antes
        # de arrancarlo (se vuelve a abrir solo cuando haga falta). Si no, `systemctl
        # start` fallaria porque el bloqueo del panel seguiria siendo nuestro.
        self.panel.cerrar()
        return self._accion_servicio("arrancar", "Arrancando el servicio",
                                     lambda: servicio.arrancar())

    def _parar_servicio(self, *_):
        return self._accion_servicio("parar", "Parando el servicio",
                                     lambda: servicio.parar())

    def _reiniciar_servicio(self, *_):
        if self._dashboard_activo:
            self.avisar("Para antes el dashboard en vivo de la aplicacion: el panel "
                        "solo admite una sesion.")
            return False
        # Mismo traspaso que al arrancar: el servicio reiniciado tiene que poder tomar el
        # panel (la unidad lleva Restart=always: sin el bloqueo entraria en bucle).
        self.panel.cerrar()
        return self._accion_servicio("reiniciar", "Reiniciando el servicio",
                                     lambda: servicio.reiniciar())

    def _parar_servicio_para_usar(self, *_):
        """Boton del aviso de la pagina Estado: para el servicio y reintenta el panel."""
        if servicio is None:
            self.avisar("El nucleo no trae cfv235.servicio")
            return False
        if self._accion_servicio_en_curso:
            self.avisar("Hay otra orden del servicio en marcha.")
            return False

        # `servicio.parar()` no vuelve hasta que systemd da la unidad por parada (y con
        # ella muere el proceso que tenia el bloqueo del panel), asi que al terminar el
        # reintento de `_refrescar_estado()` ya encuentra el panel libre.
        self.aviso_servicio.set_button_label("Parando el servicio...")

        def hecho(resultado):
            ok, mensaje = resultado if isinstance(resultado, tuple) else (bool(resultado), "")
            self._accion_servicio_en_curso = False
            self.aviso_servicio.set_button_label(ETIQUETA_PARAR_SERVICIO)
            if ok:
                self.avisar("Servicio del dashboard parado: el panel queda libre")
            else:
                self.avisar_error("No se pudo parar el servicio: %s"
                                  % (mensaje or "sin detalle"))
            self._refrescar_servicio()
            self._refrescar_estado()
            return False

        def fallo(exc):
            self._accion_servicio_en_curso = False
            self.aviso_servicio.set_button_label(ETIQUETA_PARAR_SERVICIO)
            self.avisar_error("No se pudo parar el servicio: %s"
                              % _texto_error(exc, corto=True))
            self._refrescar_servicio()

        self._accion_servicio_en_curso = True
        en_hilo(servicio.parar, hecho, fallo, "servicio-parar-panel")
        return False

    def _cambiar_arranque_automatico(self, fila, _parametro=None):
        """Interruptor "arrancar al iniciar sesion" -> `systemctl --user enable/disable`."""
        if self._silenciar_servicio:
            return
        if servicio is None or not self._servicio.get("disponible"):
            return
        activar = fila.get_active()
        self._accion_servicio(
            "habilitar" if activar else "deshabilitar",
            "Habilitando el arranque al iniciar sesion" if activar
            else "Deshabilitando el arranque al iniciar sesion",
            lambda: servicio.habilitar(activar))

    # --- registro (journalctl)

    def _leer_registro_servicio(self, lineas):
        """Se ejecuta en un hilo: ultimas lineas del journal del servicio."""
        if servicio is None:
            return "El nucleo no trae el modulo cfv235.servicio."
        try:
            return servicio.registro(lineas) or "(el registro esta vacio)"
        except Exception as exc:                  # noqa: BLE001
            return "No se pudo leer el registro: %s: %s" % (type(exc).__name__, exc)

    def _ver_registro_servicio(self, *_):
        """Ventana de solo lectura con las ultimas lineas del journal del servicio."""
        dialogo = Adw.Dialog()
        dialogo.set_title("Registro de %s" % NOMBRE_SERVICIO)
        dialogo.set_content_width(820)
        dialogo.set_content_height(480)

        cabecera = Adw.HeaderBar()
        vista = Adw.ToolbarView()
        vista.add_top_bar(cabecera)
        _marco, texto = vista_de_texto(360)
        poner_texto(texto, "Leyendo el registro...")

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caja.set_margin_top(12)
        caja.set_margin_bottom(12)
        caja.set_margin_start(12)
        caja.set_margin_end(12)
        caja.append(_marco)
        vista.set_content(caja)
        dialogo.set_child(vista)

        self._dialogo_servicio = dialogo

        def hecho(contenido):
            if self._dialogo_servicio is not dialogo:
                return False
            poner_texto(texto, contenido)
            return False

        def fallo(exc):
            if self._dialogo_servicio is not dialogo:
                return False
            poner_texto(texto, _texto_error(exc))
            return False

        def cerrado(*_):
            if self._dialogo_servicio is dialogo:
                self._dialogo_servicio = None

        dialogo.connect("closed", cerrado)
        en_hilo(lambda: self._leer_registro_servicio(LINEAS_REGISTRO_DIALOGO),
                hecho, fallo, "servicio-registro")
        dialogo.present(self)
        return False

    # ------------------------------------------------------------------ cierre

    def cerrar_panel(self):
        """Para el dashboard y el video, guarda la ventana y cierra el hidraw. Idempotente.

        Lo llaman tanto el cierre de la ventana como `do_shutdown` de la aplicacion, asi que
        las preferencias de ventana se guardan aqui y no solo en `close-request` (con Ctrl+Q
        la ventana puede destruirse sin pasar por el).
        """
        self._guardar_tamano()
        if self._parar_dashboard is not None:
            self._parar_dashboard.set()
        self._dashboard_activo = False
        # El video se corta pidiendoselo al hilo: su `finally` cierra la fuente y el
        # reproductor (cerrarlos desde aqui chocaria con el hilo que los esta usando).
        if self._parar_video is not None:
            self._parar_video.set()
        self._video_activo = False
        self._parar_hilo_keepalive()
        self.panel.cerrar()

    def _al_cerrar(self, *_):
        self.cerrar_panel()
        return False

    def _guardar_tamano(self):
        """Apunta el tamano actual de la ventana y la pagina visible."""
        ancho, alto = self.get_default_size()
        if self.get_width() > 0:
            ancho = self.get_width()
        if self.get_height() > 0:
            alto = self.get_height()
        with contextlib.suppress(Exception):
            self._guardar(ancho=int(ancho), alto=int(alto),
                          pagina=self._pagina_visible() or "estado")


# --------------------------------------------------------------------------- auxiliares


def _texto_error(exc, corto=False):
    """Mensaje legible para lo que puede fallar hablando con el panel.

    `corto=True` devuelve **una sola linea** (para toasts y subtitulos) y traduce los casos
    tipicos, en vez de ensenar la traza cruda en ingles (`FileNotFoundError: [Errno 2]...`),
    que es lo que salia antes porque el parametro no se miraba.
    """
    if isinstance(exc, FileNotFoundError):
        mensaje = ("El fichero ya no existe (se ha movido o se ha borrado): %s"
                   % (getattr(exc, "filename", None) or exc))
    elif isinstance(exc, PermissionError):
        mensaje = ("Permiso denegado: %s. Comprueba los permisos del fichero o del "
                   "dispositivo." % (getattr(exc, "filename", None) or exc))
    elif isinstance(exc, IsADirectoryError):
        mensaje = "La ruta es una carpeta, no un fichero: %s" % (getattr(exc, "filename", None) or exc)
    elif isinstance(exc, SinPanel):
        mensaje = str(exc)
    elif isinstance(exc, SinPermisos):
        mensaje = str(exc)
    elif isinstance(exc, PanelOcupado):
        # El caso mas comun: lo tiene el servicio del dashboard (o el editor de COUGAR).
        mensaje = ("%s\n\nSi lo tiene el servicio del dashboard, se puede parar con el boton "
                   "`%s` de esta pagina." % (exc, ETIQUETA_PARAR_SERVICIO))
    elif isinstance(exc, ErrorCanal):
        mensaje = str(exc)
    elif isinstance(exc, ImportError):
        mensaje = "Falta un modulo del nucleo: %s" % exc
    elif isinstance(exc, OSError) and getattr(exc, "strerror", None):
        mensaje = "%s: %s" % (getattr(exc, "filename", None) or type(exc).__name__,
                              exc.strerror)
    else:
        mensaje = "%s: %s" % (type(exc).__name__, exc)

    if corto:
        lineas = [linea.strip() for linea in str(mensaje).splitlines() if linea.strip()]
        return lineas[0] if lineas else type(exc).__name__
    return mensaje


def _detalle_respuesta(respuesta):
    """Texto con todo lo que trae una `Respuesta` del canal."""
    lineas = ["code  : %s" % respuesta.code,
              "ack   : %s" % respuesta.ack,
              "checksum ok: %s" % respuesta.checksum_ok,
              ""]
    if respuesta.cabecera:
        lineas.append("cabecera:")
        lineas.append(respuesta.cabecera)
        lineas.append("")
    lineas.append("cuerpo:")
    lineas.append(respuesta.cuerpo or "(vacio)")
    datos = respuesta.json()
    if datos is not None:
        lineas.append("")
        lineas.append("json:")
        try:
            lineas.append(json.dumps(datos, indent=2, ensure_ascii=False))
        except (TypeError, ValueError):
            lineas.append(formatear_valor(datos))
    return "\n".join(lineas)


def construir_ventana(aplicacion):
    """Crea la ventana principal. La usan `app.py` y las pruebas."""
    return VentanaPrincipal(aplicacion)
