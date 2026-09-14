"""Paginas EXTRA de la aplicacion GTK4 + libadwaita del panel COUGAR CFV235.

Este fichero es NUEVO y se puede anadir a `ventana.py` sin tocar nada mas. Integra las
tres paginas con:

    from . import paginas_extra
    ...
    self._anadir_pagina(paginas_extra.pagina_patrones(self), "patrones",
                        "Patrones", "view-grid-symbolic")
    self._anadir_pagina(paginas_extra.pagina_temas(self), "editor",
                        "Editor de temas", "applications-graphics-symbolic")
    self._anadir_pagina(paginas_extra.pagina_paletas(self), "paletas",
                        "Paletas", "color-select-symbolic")

Las tres funciones devuelven el widget raiz (un `Gtk.ScrolledWindow` ya centrado por
`pagina_desplazable`) y se pueden llamar sin panel: construir no habla con el panel, y
cualquier subida avisa con un toast en vez de lanzar.

Reglas de la casa (las mismas de `ventana.py`)
----------------------------------------------
* Todo lo que dibuja o habla con el panel va en `en_hilo` y el resultado se aplica con
  `GLib.idle_add`: la interfaz nunca se queda colgada.
* El panel admite UNA sesion: se usa siempre `with ventana.panel.sesion() as panel:` y
  `escritura_fiable()` (el simulador es un PTY y sin eso la escritura puede dar EAGAIN).
* Nada de PNG temporales que queden por ahi: las previsualizaciones se hacen con
  `temas.renderizar_datos` / `textura_desde_bytes` y, cuando hay que pasar por
  `patrones.generar` (que solo escribe a fichero), se usa un `tempfile.TemporaryDirectory`
  que se borra solo al salir del `with`.
* Si un JSON esta mal se avisa y NO se toca el panel.
* Los ayudantes de `ventana.py` (`pagina_desplazable`, `estado_vacio`, `fila_accion`,
  `en_hilo`, `poner_imagen`, `escritura_fiable`...) se toman por un acceso perezoso
  (`_ayuda`), no con `from .ventana import ...` al cargar el modulo: asi `ventana.py` puede
  hacer `from . import paginas_extra` arriba del todo sin chocar con un import circular.
"""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from cfv235 import dashboard, patrones, sensores, temas  # noqa: E402


# --------------------------------------------------------------- ayudantes de ventana.py


class _Ayudas:
    """Acceso perezoso a los ayudantes de `ventana.py`.

    No se importan arriba a proposito: si `ventana.py` hace `from . import paginas_extra`
    en su cabecera, un `from .ventana import pagina_desplazable` fallaria porque
    `ventana.py` todavia no ha definido sus funciones. Resolviendo en la primera llamada
    (mucho despues de que la ventana este montada) funciona en los dos ordenes.
    """

    def __getattr__(self, nombre):
        from . import ventana as modulo
        return getattr(modulo, nombre)


_ayuda = _Ayudas()


def _config_de(ventana, clave, defecto=None):
    """Lee una preferencia sin exigir que la ventana traiga `_config`."""
    config = getattr(ventana, "_config", None)
    if not isinstance(config, dict):
        return defecto
    return config.get(clave, defecto)


def _guardar_preferencia(ventana, **cambios):
    """Apunta preferencias con `ventana._guardar()`. Nunca lanza."""
    guardar = getattr(ventana, "_guardar", None)
    if not callable(guardar):
        return
    try:
        guardar(**cambios)
    except Exception:                             # noqa: BLE001  (preferencias no criticas)
        pass


def _avisar(ventana, texto):
    avisar = getattr(ventana, "avisar", None)
    if callable(avisar):
        avisar(texto)


def _avisar_error(ventana, texto):
    avisar = getattr(ventana, "avisar_error", None)
    if callable(avisar):
        avisar(texto)


def _avisar_largo(ventana, texto):
    avisar = getattr(ventana, "avisar_largo", None)
    if callable(avisar):
        avisar(texto)


def _mensaje(exc, corto=False):
    """Mensaje legible para una excepcion; usa el traductor de `ventana.py` si esta."""
    try:
        return _ayuda._texto_error(exc, corto=corto)
    except Exception:                             # noqa: BLE001
        return str(exc) or type(exc).__name__


def _panel_compartido(ventana):
    """El `PanelCompartido` de la ventana, o None si esta no lo trae."""
    return getattr(ventana, "panel", None)


def _subir_bytes(ventana, datos, nombre, capa="osd"):
    """Sube unos bytes PNG al panel DENTRO de una sesion. Se llama desde un hilo.

    `panel.subir_datos` nunca lanza: todo lo que puede salir mal vuelve en el
    `ResultadoSubida` (`.ok`, `.motivo`, acuses...). Lo que si puede lanzar es abrir la
    sesion (`SinPanel`, `PanelOcupado`), y de eso se encarga `al_fallar` de `en_hilo`.
    """
    compartido = _panel_compartido(ventana)
    if compartido is None:
        raise RuntimeError("La ventana no tiene panel compartido (no se puede subir).")
    fiable = getattr(_ayuda, "escritura_fiable", None)
    with compartido.sesion() as panel:
        if fiable is None:
            return panel.subir_datos(datos, nombre, capa=capa)
        with fiable(panel):
            return panel.subir_datos(datos, nombre, capa=capa)


def _nombre_seguro(texto, prefijo="cfv235"):
    """Nombre de medio valido para el panel a partir de un texto cualquiera."""
    limpio = "".join(caracter if (caracter.isalnum() or caracter in "-_") else "_"
                     for caracter in str(texto or ""))
    limpio = limpio.strip("_")[:50] or "extra"
    return "%s_%s.png" % (prefijo, limpio)


def _boton(etiqueta, al_pulsar, principal=False, icono=None):
    """Boton con la jerarquia de la casa (suggested-action para el principal)."""
    boton = Gtk.Button(label=etiqueta)
    if icono:
        boton.set_icon_name(icono)
    boton.set_valign(Gtk.Align.CENTER)
    if principal:
        boton.add_css_class("suggested-action")
    boton.connect("clicked", al_pulsar)
    return boton


def _marco_imagen(picture):
    """Mete un `Gtk.Picture` en el marco con clase `view` que usa la ventana."""
    marco = Gtk.Frame()
    marco.add_css_class("view")
    marco.set_child(picture)
    return marco


def _problemas_como_texto(problemas):
    """Lista de problemas de `temas.validar` en texto para el TextView."""
    if not problemas:
        return ("temas.validar() no encontro problemas: el tema se puede dibujar y subir "
                "tal cual.")
    return ("temas.validar() devolvio %d problema(s):\n\n%s"
            % (len(problemas), "\n".join("- %s" % p for p in problemas)))


# ------------------------------------------------------------------ color (utilidades)


def _rgb(valor, defecto=(0.5, 0.5, 0.5)):
    """(r, g, b) en 0..1 a partir de '#rrggbb'. Nunca lanza."""
    if not isinstance(valor, str):
        return defecto
    texto = valor.strip().lstrip("#")
    if len(texto) == 3:
        texto = "".join(caracter * 2 for caracter in texto)
    if len(texto) != 6:
        return defecto
    try:
        return tuple(int(texto[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return defecto


def _hexa(rgb):
    """'#rrggbb' a partir de (r, g, b) en 0..1 (recortando a 0..1)."""
    canales = [max(0.0, min(1.0, valor)) for valor in rgb]
    return "#%02x%02x%02x" % tuple(int(round(canal * 255)) for canal in canales)


def _luminancia(rgb):
    """Luminancia relativa sencilla (0 = negro, 1 = blanco)."""
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste(fondo):
    """Color de texto que se lee sobre `fondo` (oscuro o claro) sin que nadie lo diga."""
    return "#10131a" if _luminancia(_rgb(fondo, (0, 0, 0))) > 0.55 else "#f2f5fa"


def _matizar(valor, cantidad):
    """Aclara (cantidad > 0) u oscurece (cantidad < 0) un color. Sirve para derivar
    tarjeta/borde/tenue cuando una paleta no los trae: asi anadir una paleta nueva es
    solo dar el fondo y un par de colores."""
    r, g, b = _rgb(valor)
    if cantidad >= 0:
        r, g, b = (r + (1.0 - r) * cantidad,
                   g + (1.0 - g) * cantidad,
                   b + (1.0 - b) * cantidad)
    else:
        factor = 1.0 + cantidad
        r, g, b = r * factor, g * factor, b * factor
    return _hexa((r, g, b))


_PROVEEDORES_DE_COLOR = {}


def _clase_de_color(valor):
    """Clase CSS para un color literal, registrada una sola vez por proceso.

    Las muestras de las paletas se pintan con cajas y CSS (no con `Gtk.DrawingArea` y
    `set_draw_func`): dibujar a mano necesita el convertidor de `cairo.Context` de
    `python3-gi-cairo`, que no esta en requirements.txt. Asi no hace falta ninguna
    dependencia nueva.
    """
    normal = (valor or "").strip()
    if not normal.startswith("#"):
        normal = "#808080"
    clase = "cfv-muestra-" + normal.lstrip("#").lower()
    if clase in _PROVEEDORES_DE_COLOR:
        return clase
    proveedor = Gtk.CssProvider()
    proveedor.load_from_string(
        ".%s { background-color: %s; border: 1px solid rgba(128, 128, 128, 0.35);"
        " border-radius: 3px; }" % (clase, normal))
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _PROVEEDORES_DE_COLOR[clase] = proveedor
    return clase


def _muestra_color(valor, alto=22, ancho=None, etiqueta=""):
    """Caja de color para la muestra de una paleta (sin CSS externo ni ficheros).

    Con `ancho=None` la caja se estira a lo ancho (para la banda del fondo); con un ancho
    fijo vale para la fila de muestras pequenas.
    """
    caja = Gtk.Box()
    caja.add_css_class(_clase_de_color(valor))
    caja.set_size_request(-1 if ancho is None else int(ancho), int(alto))
    caja.set_valign(Gtk.Align.CENTER)
    if ancho is None:
        caja.set_hexpand(True)
    if etiqueta:
        caja.set_tooltip_text(etiqueta)
        _ayuda.etiqueta_accesible(caja, etiqueta)
    return caja


# =============================================================== 1. galeria de patrones


def _patron_bytes(nombre, lado=8, etiquetas=False):
    """PNG de un patron EN MEMORIA.

    `patrones.generar` solo escribe a fichero, asi que se usa una carpeta temporal que
    `tempfile.TemporaryDirectory` borra sola al salir del `with`: no queda nada en /tmp.
    """
    with tempfile.TemporaryDirectory(prefix="cfv235-patron-") as carpeta:
        ruta = os.path.join(carpeta, "patron-%s.png" % nombre)
        patrones.generar(nombre, ruta, lado=lado, etiquetas=etiquetas)
        with open(ruta, "rb") as fichero:
            return fichero.read()


def _reducir_png(datos, ancho_max=560):
    """Miniatura en memoria (Pillow). Si algo falla se devuelve el PNG original."""
    try:
        from PIL import Image
        reduccion = getattr(Image, "Resampling", Image).LANCZOS
        with Image.open(io.BytesIO(datos)) as imagen:
            imagen.thumbnail((ancho_max, 10000), reduccion)
            salida = io.BytesIO()
            imagen.save(salida, format="PNG")
            return salida.getvalue()
    except Exception:                             # noqa: BLE001  (sin Pillow o PNG raro)
        return datos


def _patron_miniatura(nombre, lado=8, etiquetas=False):
    """Miniatura del patron: se genera a tamano completo y se reduce en memoria."""
    return _reducir_png(_patron_bytes(nombre, lado, etiquetas))


class _GaleriaPatrones:
    """Galeria de los seis patrones de `patrones.CATALOGO`, con miniatura y subida."""

    def __init__(self, ventana):
        self.ventana = ventana
        self._generacion = 0
        self._ocupado = False
        self._pictures = {}
        self._botones_subir = {}

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        grupo_info = Adw.PreferencesGroup(
            title="Patrones de calibracion",
            description="Cada patron se dibuja a %dx%d (el tamano exacto del panel). La "
                        "miniatura y la vista ampliada se dibujan en memoria; al panel se "
                        "sube el PNG a la capa OSD, que es la que se ve encima del fondo."
                        % (patrones.ANCHO, patrones.ALTO))
        caja.append(grupo_info)

        # --- opciones comunes de dibujo
        grupo_opciones = Adw.PreferencesGroup(
            title="Opciones de dibujo",
            description="Al cambiarlas se vuelven a dibujar las miniaturas, sin tocar el "
                        "panel.")

        self.fila_lado = Adw.SpinRow.new_with_range(1, 64, 1)
        self.fila_lado.set_title("Tamano del cuadro (lado)")
        self.fila_lado.set_subtitle("Lado, en pixeles, de cada cuadro del damero y del paso "
                                    "de la rejilla.")
        self.fila_lado.set_icon_name("view-grid-symbolic")
        lado = _config_de(ventana, "patron_lado", 8)
        self.fila_lado.set_value(lado if isinstance(lado, int) and lado >= 1 else 8)
        self.fila_lado.connect("notify::value", self._cambio_lado)
        grupo_opciones.add(self.fila_lado)

        self.fila_etiquetas = Adw.SwitchRow(
            title="Numerar los cruces",
            subtitle="En la rejilla, escribe las coordenadas de cada cruce. Ayuda a "
                     "localizar recortes y desplazamientos.")
        self.fila_etiquetas.set_icon_name("font-x-generic-symbolic")
        self.fila_etiquetas.set_active(bool(_config_de(ventana, "patron_etiquetas", False)))
        self.fila_etiquetas.connect("notify::active", self._cambio_etiquetas)
        grupo_opciones.add(self.fila_etiquetas)
        caja.append(grupo_opciones)

        # --- lista con las seis fichas
        grupo_lista = Adw.PreferencesGroup(
            title="Catalogo",
            description="Ver abre el patron en grande (sin tocar el panel). Subir al panel "
                        "lo manda a la capa OSD.")

        self.lista = Gtk.ListBox()
        self.lista.set_selection_mode(Gtk.SelectionMode.NONE)
        self.lista.add_css_class("boxed-list")

        for nombre, descripcion in patrones.CATALOGO.items():
            fila = Adw.ActionRow(title=nombre, subtitle=descripcion)
            fila.set_icon_name("image-x-generic-symbolic")

            picture = Gtk.Picture()
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_can_shrink(True)
            picture.set_size_request(220, 56)
            _ayuda.etiqueta_accesible(picture, "Miniatura del patron %s" % nombre)
            marco = Gtk.Frame()
            marco.add_css_class("view")
            marco.set_child(picture)
            fila.add_prefix(marco)
            self._pictures[nombre] = picture

            boton_ver = _boton("Ver", lambda *_a, n=nombre: self._ver(n))
            boton_subir = _boton("Subir al panel", lambda *_a, n=nombre: self._subir(n),
                                 principal=True)
            self._botones_subir[nombre] = boton_subir
            fila.add_suffix(boton_ver)
            fila.add_suffix(boton_subir)
            self.lista.append(fila)

        grupo_lista.add(self.lista)
        caja.append(grupo_lista)

        # --- estado y accion masiva
        grupo_acciones = Adw.PreferencesGroup(
            title="Estado",
            description="Subir todos recorre el catalogo en orden, reutilizando UNA sola "
                        "sesion del panel.")

        self.fila_estado = _ayuda.fila_accion("Miniaturas", "Generando...",
                                              icono="view-refresh-symbolic")
        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_visible(False)
        self.fila_estado.add_suffix(self.spinner)
        grupo_acciones.add(self.fila_estado)

        self.fila_todos = _ayuda.fila_accion(
            "Subir todos", "Manda los seis patrones a la capa OSD, uno detras de otro.",
            icono="document-send-symbolic")
        self.boton_todos = _boton("Subir todos", self._subir_todos, principal=True)
        self.fila_todos.add_suffix(self.boton_todos)
        grupo_acciones.add(self.fila_todos)
        caja.append(grupo_acciones)

        self.raiz = _ayuda.pagina_desplazable(caja, ancho_maximo=1040)

        # Las miniaturas se generan cuando la pagina ya esta montada.
        GLib.idle_add(self._refrescar_miniaturas)

    # --- opciones

    def _opciones(self):
        """(lado, etiquetas) tal como estan ahora los controles."""
        lado = int(self.fila_lado.get_value() or 8)
        return max(1, lado), bool(self.fila_etiquetas.get_active())

    def _cambio_lado(self, fila, _parametro=None):
        _guardar_preferencia(self.ventana, patron_lado=int(fila.get_value() or 8))
        self._refrescar_miniaturas()
        return False

    def _cambio_etiquetas(self, fila, _parametro=None):
        _guardar_preferencia(self.ventana, patron_etiquetas=bool(fila.get_active()))
        self._refrescar_miniaturas()
        return False

    # --- miniaturas

    def _refrescar_miniaturas(self, *_):
        """Genera las seis miniaturas en un hilo (una generacion cada vez)."""
        self._generacion += 1
        generacion = self._generacion
        lado, etiquetas = self._opciones()
        self._ocupado_miniaturas(True)
        self.fila_estado.set_subtitle("Dibujando %d miniaturas (lado=%d)..."
                                      % (len(patrones.CATALOGO), lado))

        def tarea():
            return {nombre: _patron_miniatura(nombre, lado, etiquetas)
                    for nombre in patrones.CATALOGO}

        def hecho(miniaturas):
            if generacion != self._generacion:
                return False                    # llego tarde: hay otra generacion en curso
            for nombre, picture in self._pictures.items():
                datos = miniaturas.get(nombre)
                if not _ayuda.poner_imagen(picture, datos):
                    picture.set_tooltip_text("No se pudo dibujar la miniatura de %s." % nombre)
            self._ocupado_miniaturas(False)
            self.fila_estado.set_subtitle(
                "%d miniaturas listas (lado=%d, etiquetas=%s)."
                % (len(miniaturas), lado, "si" if etiquetas else "no"))
            return False

        def fallo(exc):
            if generacion != self._generacion:
                return False
            self._ocupado_miniaturas(False)
            self.fila_estado.set_subtitle("No se pudieron dibujar las miniaturas.")
            _avisar_error(self.ventana, "Patrones: %s" % _mensaje(exc, corto=True))
            return False

        _ayuda.en_hilo(tarea, hecho, fallo, nombre="patrones-miniaturas")
        return False

    def _ocupado_miniaturas(self, ocupado):
        self.spinner.set_visible(bool(ocupado))
        if ocupado:
            self.spinner.start()
        else:
            self.spinner.stop()

    # --- ver en grande

    def _ver(self, nombre):
        """Amplia la miniatura en un `Adw.Dialog`. No toca el panel."""
        lado, etiquetas = self._opciones()

        dialogo = Adw.Dialog()
        dialogo.set_title("Patron %s" % nombre)
        dialogo.set_content_width(1020)
        dialogo.set_content_height(420)

        vista = Adw.ToolbarView()
        cabecera = Adw.HeaderBar()
        vista.add_top_bar(cabecera)

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        caja.set_margin_top(12)
        caja.set_margin_bottom(12)
        caja.set_margin_start(12)
        caja.set_margin_end(12)

        picture = Gtk.Picture()
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_can_shrink(True)
        picture.set_size_request(-1, 320)
        _ayuda.etiqueta_accesible(picture, "Patron %s a tamano completo" % nombre)
        caja.append(_marco_imagen(picture))

        # La etiqueta es LOCAL: si se guardara en `self`, dos dialogos abiertos a la vez
        # compartirian el mismo widget y el segundo callback escribiria en el primero.
        etiqueta = Gtk.Label(xalign=0, label=patrones.CATALOGO.get(nombre, ""))
        etiqueta.add_css_class("dim-label")
        etiqueta.set_wrap(True)
        caja.append(etiqueta)

        vista.set_content(caja)
        dialogo.set_child(vista)

        def tarea():
            return _patron_bytes(nombre, lado, etiquetas)

        def hecho(datos):
            if not _ayuda.poner_imagen(picture, datos):
                etiqueta.set_text("No se pudo dibujar el patron.")
            else:
                etiqueta.set_text(
                    "%s   %dx%d   %.0f KB (dibujado en memoria, sin ficheros temporales)"
                    % (patrones.CATALOGO.get(nombre, ""), patrones.ANCHO, patrones.ALTO,
                       len(datos) / 1024.0))
            return False

        def fallo(exc):
            etiqueta.set_text("Fallo al dibujar: %s" % _mensaje(exc, corto=True))
            return False

        _ayuda.en_hilo(tarea, hecho, fallo, nombre="patron-ver")
        dialogo.present(self.ventana)
        return False

    # --- subir

    def _subir(self, nombre):
        """Sube UN patron a la capa OSD, sin bloquear la interfaz."""
        lado, etiquetas = self._opciones()
        boton = self._botones_subir.get(nombre)
        if boton is not None:
            boton.set_sensitive(False)
        self.fila_estado.set_subtitle("Subiendo el patron %s al panel..." % nombre)

        def tarea():
            datos = _patron_bytes(nombre, lado, etiquetas)
            return _subir_bytes(self.ventana, datos, _nombre_seguro(nombre, "patron"),
                                capa="osd")

        def hecho(resultado):
            if boton is not None:
                boton.set_sensitive(True)
            self.fila_estado.set_subtitle(resultado.resumen())
            if resultado.ok:
                _avisar(self.ventana, "Patron %s subido al panel (capa OSD)" % nombre)
            else:
                _avisar_error(self.ventana, "El panel rechazo el patron %s: %s"
                              % (nombre, resultado.motivo or resultado.resumen()))
            return False

        def fallo(exc):
            if boton is not None:
                boton.set_sensitive(True)
            self.fila_estado.set_subtitle("No se pudo subir el patron %s." % nombre)
            _avisar_error(self.ventana, "Patron %s: %s" % (nombre, _mensaje(exc, corto=True)))
            return False

        _ayuda.en_hilo(tarea, hecho, fallo, nombre="patron-subir")
        return False

    def _subir_todos(self, *_):
        """Sube los seis patrones en secuencia, dentro de UNA sola sesion del panel."""
        if self._ocupado:
            _avisar(self.ventana, "Ya hay una subida en marcha.")
            return False
        self._ocupado = True
        lado, etiquetas = self._opciones()
        nombres = list(patrones.CATALOGO)
        self.boton_todos.set_sensitive(False)
        self.fila_estado.set_subtitle("Preparando los %d patrones..." % len(nombres))

        def progreso(texto):
            self.fila_estado.set_subtitle(texto)
            return False

        def tarea():
            # Primero se dibuja todo (no se tiene el panel mientras se dibuja)...
            pares = [(nombre, _patron_bytes(nombre, lado, etiquetas)) for nombre in nombres]
            compartido = _panel_compartido(self.ventana)
            if compartido is None:
                raise RuntimeError("La ventana no tiene panel compartido (no se puede subir).")
            fiable = getattr(_ayuda, "escritura_fiable", None)
            # ...y luego se abre UNA sesion para las seis subidas, en orden.
            resultados = []
            with compartido.sesion() as panel:
                for indice, (nombre, datos) in enumerate(pares, 1):
                    GLib.idle_add(progreso, "Subiendo %d/%d: %s..."
                                  % (indice, len(pares), nombre))
                    if fiable is None:
                        resultado = panel.subir_datos(
                            datos, _nombre_seguro(nombre, "patron"), capa="osd")
                    else:
                        with fiable(panel):
                            resultado = panel.subir_datos(
                                datos, _nombre_seguro(nombre, "patron"), capa="osd")
                    resultados.append((nombre, resultado))
            return resultados

        def hecho(resultados):
            self._ocupado = False
            self.boton_todos.set_sensitive(True)
            correctas = [n for n, r in resultados if r.ok]
            fallidas = [(n, r) for n, r in resultados if not r.ok]
            self.fila_estado.set_subtitle(
                "%d de %d patrones subidos a la capa OSD."
                % (len(correctas), len(resultados)))
            if not fallidas:
                _avisar(self.ventana, "Los %d patrones estan en el panel (capa OSD)"
                        % len(correctas))
            elif correctas:
                _avisar_largo(self.ventana,
                              "Subidos %d de %d. Fallo en: %s"
                              % (len(correctas), len(resultados),
                                 ", ".join(n for n, _r in fallidas)))
            else:
                _avisar_error(self.ventana,
                              "El panel rechazo los patrones: %s"
                              % (fallidas[0][1].motivo or fallidas[0][1].resumen()))
            return False

        def fallo(exc):
            self._ocupado = False
            self.boton_todos.set_sensitive(True)
            self.fila_estado.set_subtitle("No se pudieron subir los patrones.")
            _avisar_error(self.ventana, "Patrones: %s" % _mensaje(exc, corto=True))
            return False

        _ayuda.en_hilo(tarea, hecho, fallo, nombre="patrones-subir-todos")
        return False


def pagina_patrones(ventana):
    """Pagina 1: galeria de los seis patrones de calibracion.

    Devuelve el widget raiz. Se puede llamar sin panel: solo las acciones de subida
    hablan con el, y avisan con un toast si no esta.
    """
    return _GaleriaPatrones(ventana).raiz


# ================================================================ 2. editor de temas


class _EditorTemas:
    """Editor del JSON del tema, con validacion, previa en memoria y subida al panel."""

    def __init__(self, ventana):
        self.ventana = ventana
        self._ocupado = False

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        grupo_info = Adw.PreferencesGroup(
            title="Editor de temas",
            description="Escribe el tema como JSON y comprueba como queda antes de subirlo. "
                        "El dibujo se hace EN MEMORIA (temas.renderizar_datos), asi que la "
                        "previsualizacion no escribe ningun fichero temporal ni toca el "
                        "panel.")

        self.fila_estado = _ayuda.fila_accion("Estado", "Listo.", icono="document-edit-symbolic")
        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_visible(False)
        self.fila_estado.add_suffix(self.spinner)
        grupo_info.add(self.fila_estado)
        caja.append(grupo_info)

        # --- el JSON
        grupo_json = Adw.PreferencesGroup(
            title="JSON del tema",
            description="Un tema es un objeto con 'fondo' y una lista 'widgets'. Cargar "
                        "ejemplo pone el dashboard completo para partir de algo que ya "
                        "funciona.")
        self.boton_ejemplo = _boton("Cargar ejemplo (dashboard)", self._cargar_ejemplo)
        self.boton_defecto = _boton("Tema por defecto", self._cargar_por_defecto)
        self.boton_fichero = _boton("Cargar de fichero...", self._elegir_fichero,
                                    icono="document-open-symbolic")
        fila_carga = _ayuda.fila_accion(
            "Cargar", "Un .json del disco o un ejemplo del nucleo.",
            icono="document-open-symbolic")
        for boton in (self.boton_fichero, self.boton_ejemplo, self.boton_defecto):
            fila_carga.add_suffix(boton)
        grupo_json.add(fila_carga)

        marco_json, self.vista = _ayuda.vista_de_texto(340, editable=True)
        _ayuda.poner_texto(self.vista, self._texto_inicial())
        grupo_json.add(marco_json)
        caja.append(grupo_json)

        # --- acciones
        grupo_acciones = Adw.PreferencesGroup(
            title="Acciones",
            description="Validar y Previsualizar NO tocan el panel. Aplicar renderiza el "
                        "JSON actual y lo sube a la capa OSD.")
        fila_acciones = _ayuda.fila_accion(
            "Tema actual", "Valida, previsualiza o sube lo que hay en el editor.",
            icono="applications-graphics-symbolic")
        self.boton_validar = _boton("Validar", self._validar)
        self.boton_previa = _boton("Previsualizar", self._previsualizar, principal=True)
        self.boton_aplicar = _boton("Aplicar al panel", self._aplicar, principal=True)
        for boton in (self.boton_validar, self.boton_previa, self.boton_aplicar):
            fila_acciones.add_suffix(boton)
        grupo_acciones.add(fila_acciones)
        caja.append(grupo_acciones)

        # --- previsualizacion
        grupo_previa = Adw.PreferencesGroup(
            title="Previsualizacion",
            description="El PNG se dibuja en memoria y se ensena aqui; no se escribe en "
                        "/tmp.")
        self.picture = Gtk.Picture()
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_can_shrink(True)
        self.picture.set_size_request(-1, 240)
        self.pagina_previa = _ayuda.apilar(
            ("vacio", _ayuda.estado_vacio(
                "Sin previsualizacion",
                "Pulsa Previsualizar para dibujar el JSON actual. No se toca el panel.",
                "image-x-generic-symbolic")),
            ("contenido", _marco_imagen(self.picture)))
        self.pagina_previa.set_visible_child_name("vacio")
        self.pagina_previa.set_size_request(-1, 260)
        caja.append(grupo_previa)
        caja.append(self.pagina_previa)

        # --- problemas
        grupo_problemas = Adw.PreferencesGroup(
            title="Problemas del tema",
            description="Lo que devuelve temas.validar(): vacio = se puede subir tal cual.")
        marco_problemas, self.texto_problemas = _ayuda.vista_de_texto(120)
        _ayuda.poner_texto(self.texto_problemas,
                           "Pulsa Validar para comprobar el JSON del editor.")
        grupo_problemas.add(marco_problemas)
        caja.append(grupo_problemas)

        self.raiz = _ayuda.pagina_desplazable(caja, ancho_maximo=1040)

    # --- contenido

    @staticmethod
    def _texto_inicial():
        try:
            return json.dumps(dashboard.tema_dashboard(), indent=2, ensure_ascii=False)
        except Exception:                         # noqa: BLE001
            return json.dumps(temas.tema_por_defecto(), indent=2, ensure_ascii=False)

    def _tema_del_editor(self):
        """(tema, problema). `problema` es '' cuando el JSON esta bien.

        Se comprueba ANTES de dibujar o subir: si el JSON esta mal se avisa y el panel no
        se toca.
        """
        texto = _ayuda.leer_texto(self.vista).strip()
        if not texto:
            return None, "El editor esta vacio: pega un tema JSON o pulsa Cargar ejemplo."
        try:
            tema = json.loads(texto)
        except (ValueError, TypeError) as exc:
            return None, "El JSON no se puede leer: %s" % exc
        if not isinstance(tema, dict):
            return None, "Un tema tiene que ser un objeto JSON (empieza por '{')."
        return tema, ""

    def _avisar_json_malo(self, problema):
        _ayuda.poner_texto(self.texto_problemas, problema)
        self.fila_estado.set_subtitle("El JSON del editor no es valido.")
        _avisar_error(self.ventana, problema)

    def _ocupar(self, ocupado, texto=""):
        self._ocupado = bool(ocupado)
        for boton in (self.boton_validar, self.boton_previa, self.boton_aplicar):
            boton.set_sensitive(not self._ocupado)
        self.spinner.set_visible(self._ocupado)
        if self._ocupado:
            self.spinner.start()
        else:
            self.spinner.stop()
        if texto:
            self.fila_estado.set_subtitle(texto)

    # --- cargar

    def _cargar_ejemplo(self, *_):
        try:
            tema = dashboard.tema_dashboard()
        except Exception as exc:                  # noqa: BLE001  (nucleo a medias)
            _avisar_error(self.ventana, "No se pudo construir el ejemplo: %s" % exc)
            return False
        _ayuda.poner_texto(self.vista, json.dumps(tema, indent=2, ensure_ascii=False))
        self.fila_estado.set_subtitle("Cargado el tema del dashboard (perfil completo).")
        _avisar(self.ventana, "Ejemplo del dashboard cargado en el editor")
        return False

    def _cargar_por_defecto(self, *_):
        try:
            tema = temas.tema_por_defecto()
        except Exception as exc:                  # noqa: BLE001
            _avisar_error(self.ventana, "No se pudo leer el tema por defecto: %s" % exc)
            return False
        _ayuda.poner_texto(self.vista, json.dumps(tema, indent=2, ensure_ascii=False))
        self.fila_estado.set_subtitle("Cargado el tema por defecto del nucleo.")
        return False

    def _elegir_fichero(self, *_):
        dialogo = Gtk.FileDialog()
        dialogo.set_title("Abrir un tema JSON")

        filtro = Gtk.FileFilter()
        filtro.set_name("Temas JSON (*.json)")
        filtro.add_pattern("*.json")
        filtro.add_mime_type("application/json")

        todos = Gtk.FileFilter()
        todos.set_name("Todos los ficheros")

        almacen = Gio.ListStore.new(Gtk.FileFilter)
        almacen.append(filtro)
        almacen.append(todos)
        dialogo.set_filters(almacen)
        dialogo.set_default_filter(filtro)

        # `Gtk.FileDialog` es asincrono: sin una referencia viva, Python lo destruye al
        # salir de aqui y el dialogo no llega a verse nunca.
        self._dialogo = dialogo
        dialogo.open(self.ventana, None, self._fichero_elegido)
        return False

    def _fichero_elegido(self, dialogo, resultado):
        self._dialogo = None
        try:
            archivo = dialogo.open_finish(resultado)
        except GLib.Error:
            return                                # el usuario cancelo
        except Exception:                         # noqa: BLE001
            return
        if archivo is None:
            return
        ruta = archivo.get_path()
        if not ruta:
            _avisar_error(self.ventana, "Solo se pueden abrir ficheros locales.")
            return
        try:
            with open(ruta, encoding="utf-8") as fichero:
                tema = json.load(fichero)
        except (OSError, ValueError) as exc:
            problema = "No se pudo leer %s: %s" % (os.path.basename(ruta), exc)
            self._avisar_json_malo(problema)
            return
        if not isinstance(tema, dict):
            self._avisar_json_malo("%s no contiene un objeto JSON de tema."
                                   % os.path.basename(ruta))
            return
        _ayuda.poner_texto(self.vista, json.dumps(tema, indent=2, ensure_ascii=False))
        self.fila_estado.set_subtitle("Cargado %s." % os.path.basename(ruta))
        _avisar(self.ventana, "Tema cargado: %s" % os.path.basename(ruta))

    # --- validar / previsualizar / aplicar

    def _validar(self, *_):
        tema, problema = self._tema_del_editor()
        if problema:
            self._avisar_json_malo(problema)
            return False
        self._ocupar(True, "Validando el tema...")

        def tarea():
            return list(temas.validar(tema) or [])

        def hecho(problemas):
            self._ocupar(False)
            _ayuda.poner_texto(self.texto_problemas, _problemas_como_texto(problemas))
            if problemas:
                self.fila_estado.set_subtitle(
                    "temas.validar() encontro %d problema(s)." % len(problemas))
                _avisar_largo(self.ventana, "El tema tiene %d problema(s): mira la lista."
                              % len(problemas))
            else:
                self.fila_estado.set_subtitle("El tema es valido: se puede subir tal cual.")
                _avisar(self.ventana, "El tema es valido")
            return False

        def fallo(exc):
            self._ocupar(False, "No se pudo validar el tema.")
            _ayuda.poner_texto(self.texto_problemas, _mensaje(exc))
            _avisar_error(self.ventana, "Validar: %s" % _mensaje(exc, corto=True))
            return False

        _ayuda.en_hilo(tarea, hecho, fallo, nombre="tema-editor-validar")
        return False

    def _previsualizar(self, *_):
        tema, problema = self._tema_del_editor()
        if problema:
            self._avisar_json_malo(problema)
            return False
        if self._ocupado:
            _avisar(self.ventana, "Ya hay una operacion en marcha: espera a que termine.")
            return False
        self._ocupar(True, "Dibujando el tema en memoria...")

        def tarea():
            problemas = list(temas.validar(tema) or [])
            datos = temas.renderizar_datos(tema)
            return {"datos": datos, "problemas": problemas}

        def hecho(resultado):
            self._ocupar(False)
            self._mostrar_previa(resultado)
            return False

        def fallo(exc):
            self._ocupar(False, "No se pudo dibujar el tema.")
            _ayuda.poner_texto(self.texto_problemas, _mensaje(exc))
            _avisar_error(self.ventana, "Previsualizar: %s" % _mensaje(exc, corto=True))
            return False

        _ayuda.en_hilo(tarea, hecho, fallo, nombre="tema-editor-previa")
        return False

    def _aplicar(self, *_):
        tema, problema = self._tema_del_editor()
        if problema:
            self._avisar_json_malo(problema)
            return False
        if self._ocupado:
            _avisar(self.ventana, "Ya hay una operacion en marcha: espera a que termine.")
            return False
        self._ocupar(True, "Dibujando y subiendo el tema al panel (capa OSD)...")

        def tarea():
            problemas = list(temas.validar(tema) or [])
            datos = temas.renderizar_datos(tema)
            resultado = _subir_bytes(self.ventana, datos, "tema_editor.png", capa="osd")
            return {"datos": datos, "problemas": problemas, "resultado": resultado}

        def hecho(resultado):
            self._ocupar(False)
            self._mostrar_previa(resultado)
            subida = resultado["resultado"]
            if subida.ok:
                self.fila_estado.set_subtitle(subida.resumen())
                _avisar(self.ventana, "Tema aplicado al panel (capa OSD)")
            else:
                self.fila_estado.set_subtitle(subida.resumen())
                _avisar_error(self.ventana, "El panel rechazo el tema: %s"
                              % (subida.motivo or subida.resumen()))
            return False

        def fallo(exc):
            self._ocupar(False, "No se pudo aplicar el tema.")
            _ayuda.poner_texto(self.texto_problemas, _mensaje(exc))
            _avisar_error(self.ventana, "Aplicar: %s" % _mensaje(exc, corto=True))
            return False

        _ayuda.en_hilo(tarea, hecho, fallo, nombre="tema-editor-aplicar")
        return False

    def _mostrar_previa(self, resultado):
        """Ensenza el PNG en memoria y cuenta los problemas (comun a previa y aplicar)."""
        datos = resultado.get("datos") or b""
        if _ayuda.poner_imagen(self.picture, datos):
            self.pagina_previa.set_visible_child_name("contenido")
            self.fila_estado.set_subtitle("PNG en memoria: %.0f KB." % (len(datos) / 1024.0))
        problemas = resultado.get("problemas") or []
        _ayuda.poner_texto(self.texto_problemas, _problemas_como_texto(problemas))


def pagina_temas(ventana):
    """Pagina 2: editor del JSON del tema con previa en memoria y subida a la capa OSD.

    Devuelve el widget raiz. Sin panel se puede editar, validar y previsualizar igual;
    Aplicar avisa con un toast.
    """
    return _EditorTemas(ventana).raiz


# ================================================================= 3. paletas de color

# Paletas predefinidas. Cada una es DATOS: {"nombre", "fondo", "colores": [...]} -- anadir
# una nueva es copiar un bloque y cambiar los colores. Los papeles que falten se derivan
# solos (contraste, tarjeta, borde, tenue...) para que una paleta minima sea solo el fondo
# y un par de colores.
PALETAS = [
    {
        "nombre": "Panel oscuro",
        "descripcion": "Los colores que trae el dashboard de fabrica.",
        "fondo": "#0b0e14",
        "colores": [
            {"papel": "tarjeta", "color": "#141922"},
            {"papel": "borde", "color": "#1f2836"},
            {"papel": "titulo", "color": "#00b4ff"},
            {"papel": "texto", "color": "#e8edf5"},
            {"papel": "tenue", "color": "#6b7484"},
            {"papel": "etiqueta", "color": "#8a94a6"},
            {"papel": "acento", "color": "#00b4ff"},
            {"papel": "acento2", "color": "#00d2d3"},
            {"papel": "cpu", "color": "#ff9f43"},
            {"papel": "gpu", "color": "#4cd137"},
            {"papel": "ram", "color": "#00b4ff"},
            {"papel": "disco", "color": "#e1b12c"},
            {"papel": "red", "color": "#00d2d3"},
        ],
    },
    {
        "nombre": "Contraste alto",
        "descripcion": "Negro puro y texto casi blanco: se lee desde lejos.",
        "fondo": "#000000",
        "colores": [
            {"papel": "tarjeta", "color": "#0a0a0a"},
            {"papel": "borde", "color": "#3a3a3a"},
            {"papel": "titulo", "color": "#ffffff"},
            {"papel": "texto", "color": "#f5f7fa"},
            {"papel": "tenue", "color": "#b9c0cc"},
            {"papel": "etiqueta", "color": "#cfd6e0"},
            {"papel": "acento", "color": "#ffe14d"},
            {"papel": "acento2", "color": "#7dfcff"},
            {"papel": "cpu", "color": "#ff8a3d"},
            {"papel": "gpu", "color": "#7dff7a"},
            {"papel": "ram", "color": "#7dfcff"},
            {"papel": "disco", "color": "#ffe14d"},
            {"papel": "red", "color": "#7dfcff"},
        ],
    },
    {
        "nombre": "Azul COUGAR",
        "descripcion": "Azul profundo con el naranja de la marca COUGAR.",
        "fondo": "#0a1526",
        "colores": [
            {"papel": "tarjeta", "color": "#102138"},
            {"papel": "borde", "color": "#1d3a5c"},
            {"papel": "titulo", "color": "#4da3ff"},
            {"papel": "texto", "color": "#eaf2ff"},
            {"papel": "tenue", "color": "#7d93b3"},
            {"papel": "etiqueta", "color": "#9db4d4"},
            {"papel": "acento", "color": "#ff7a18"},
            {"papel": "acento2", "color": "#4da3ff"},
            {"papel": "cpu", "color": "#ff7a18"},
            {"papel": "gpu", "color": "#4da3ff"},
            {"papel": "ram", "color": "#3ddc97"},
            {"papel": "disco", "color": "#ffd166"},
            {"papel": "red", "color": "#4dd0e1"},
        ],
    },
    {
        "nombre": "Ambar",
        "descripcion": "Fosforo ambar clasico, con todo en la misma gama.",
        "fondo": "#1a1206",
        "colores": [
            {"papel": "tarjeta", "color": "#241a09"},
            {"papel": "borde", "color": "#4a3512"},
            {"papel": "titulo", "color": "#ffb000"},
            {"papel": "texto", "color": "#ffe0a3"},
            {"papel": "tenue", "color": "#b3852f"},
            {"papel": "etiqueta", "color": "#d9a441"},
            {"papel": "acento", "color": "#ffb000"},
            {"papel": "acento2", "color": "#ff7b00"},
            {"papel": "cpu", "color": "#ffb000"},
            {"papel": "gpu", "color": "#ffd166"},
            {"papel": "ram", "color": "#ff9d2e"},
            {"papel": "disco", "color": "#ffc857"},
            {"papel": "red", "color": "#ffd166"},
        ],
    },
    {
        "nombre": "Verde terminal",
        "descripcion": "Verde fosforo sobre negro, como una consola antigua.",
        "fondo": "#04120a",
        "colores": [
            {"papel": "tarjeta", "color": "#081c10"},
            {"papel": "borde", "color": "#123a22"},
            {"papel": "titulo", "color": "#39ff88"},
            {"papel": "texto", "color": "#c8ffdc"},
            {"papel": "tenue", "color": "#5aa878"},
            {"papel": "etiqueta", "color": "#78c896"},
            {"papel": "acento", "color": "#39ff88"},
            {"papel": "acento2", "color": "#9dff5a"},
            {"papel": "cpu", "color": "#39ff88"},
            {"papel": "gpu", "color": "#9dff5a"},
            {"papel": "ram", "color": "#2ee6a8"},
            {"papel": "disco", "color": "#c8ff5a"},
            {"papel": "red", "color": "#5affd0"},
        ],
    },
    {
        "nombre": "Clara",
        "descripcion": "Fondo claro para paneles con mucha luz ambiente.",
        "fondo": "#f2f4f8",
        "colores": [
            {"papel": "tarjeta", "color": "#ffffff"},
            {"papel": "borde", "color": "#c9d2e0"},
            {"papel": "titulo", "color": "#0b4f9c"},
            {"papel": "texto", "color": "#10131a"},
            {"papel": "tenue", "color": "#5a6472"},
            {"papel": "etiqueta", "color": "#3c4552"},
            {"papel": "acento", "color": "#0b5fbf"},
            {"papel": "acento2", "color": "#c2410c"},
            {"papel": "cpu", "color": "#c2410c"},
            {"papel": "gpu", "color": "#15803d"},
            {"papel": "ram", "color": "#0b5fbf"},
            {"papel": "disco", "color": "#a16207"},
            {"papel": "red", "color": "#0e7490"},
        ],
    },
]

# Papel de cada dato del dashboard: la clave de `fuente` empieza por el nombre del bloque.
_FUENTES = (
    ("cpu", "cpu"), ("gpu", "gpu"), ("ram", "ram"), ("disco", "disco"),
    ("red", "red"), ("net", "red"),
)


def _papel_de_fuente(fuente):
    """'cpu'/'gpu'/'ram'/'disco'/'red' a partir de la fuente de datos de un widget."""
    if not isinstance(fuente, str):
        return None
    clave = fuente.strip().lower()
    for prefijo, papel in _FUENTES:
        if clave.startswith(prefijo):
            return papel
    return None


def _papel_por_valor(valor):
    """Papel del campo generico 'color' segun el color que traia el dashboard.

    Asi se acierta con el titulo, el texto y lo tenue sin mirar coordenadas ni tipos.
    """
    if not isinstance(valor, str):
        return None
    normal = valor.strip().lower()
    for candidato, papel in ((dashboard.COLOR_TITULO, "titulo"),
                             (dashboard.COLOR_TEXTO, "texto"),
                             (dashboard.COLOR_GRIS, "tenue"),
                             (dashboard.COLOR_TENUE, "tenue")):
        if normal == str(candidato).strip().lower():
            return papel
    return None


def _roles_de(paleta):
    """Papeles -> color de una paleta, completando los que falten.

    Los que no vengan se derivan del fondo (contraste, tarjeta, borde, tenue) o del primer
    color de la lista: una paleta nueva puede ser solo {"nombre", "fondo", "colores":[...]}.
    """
    roles = {"fondo": paleta.get("fondo") or "#0b0e14"}
    for entrada in paleta.get("colores") or []:
        if isinstance(entrada, dict) and entrada.get("papel") and entrada.get("color"):
            roles[str(entrada["papel"])] = entrada["color"]

    color_fondo = roles["fondo"]
    oscuro = _luminancia(_rgb(color_fondo, (0, 0, 0))) < 0.55

    texto = roles.get("texto") or _contraste(color_fondo)
    roles["texto"] = texto
    roles["tenue"] = roles.get("tenue") or _matizar(texto, -0.35 if oscuro else 0.35)
    roles["etiqueta"] = roles.get("etiqueta") or roles["tenue"]
    primero = next((e["color"] for e in (paleta.get("colores") or [])
                    if isinstance(e, dict) and e.get("color")), texto)
    roles["acento"] = roles.get("acento") or roles.get("titulo") or primero
    roles["titulo"] = roles.get("titulo") or roles["acento"]
    roles["acento2"] = roles.get("acento2") or roles["acento"]
    roles["tarjeta"] = roles.get("tarjeta") or _matizar(color_fondo, 0.06 if oscuro else -0.04)
    roles["borde"] = roles.get("borde") or _matizar(color_fondo, 0.14 if oscuro else -0.12)
    for papel in ("cpu", "gpu", "ram", "disco", "red"):
        roles[papel] = roles.get(papel) or roles["acento"]
    return roles


def _color_de(roles, papel, respaldo="acento"):
    return roles.get(papel) or roles.get(respaldo) or roles.get("texto")


def aplicar_paleta(tema, paleta):
    """Copia de `tema` con los colores del dashboard sustituidos por los de la paleta.

    Se sustituye por el NOMBRE del campo (`fondo`, `borde`, `color_etiqueta`,
    `color_relleno`) y, en el campo generico `color`, por el papel que tenia el color
    original (titulo, texto, tenue) o por el bloque de datos (`fuente`: cpu, gpu, ram...).
    Los colores que no se reconocen se dejan como estaban, para no estropear un tema
    propio. `tema` no se modifica: se trabaja sobre una copia.
    """
    copia = copy.deepcopy(tema)
    if not isinstance(copia, dict):
        return tema
    roles = _roles_de(paleta)
    copia["fondo"] = roles["fondo"]

    for widget in copia.get("widgets") or []:
        if not isinstance(widget, dict):
            continue
        tipo = str(widget.get("tipo") or "").strip().lower()

        if "fondo" in widget:
            # En la barra de progreso el 'fondo' es la pista, no la tarjeta.
            widget["fondo"] = _color_de(roles, "borde" if tipo == "barra" else "tarjeta",
                                        respaldo="fondo")
        if "borde" in widget:
            widget["borde"] = _color_de(roles, "borde", respaldo="fondo")
        if "color_etiqueta" in widget:
            widget["color_etiqueta"] = _color_de(roles, "etiqueta", respaldo="tenue")
        if "color_relleno" in widget:
            widget["color_relleno"] = _color_de(roles, _papel_de_fuente(widget.get("fuente")))
        if "color" in widget:
            papel = "acento2" if tipo == "grafica" else (
                _papel_de_fuente(widget.get("fuente")) or _papel_por_valor(widget.get("color")))
            if papel:
                widget["color"] = _color_de(roles, papel)
    return copia


class _Paletas:
    """Rejilla de paletas; Aplicar sustituye los colores del dashboard y lo sube."""

    def __init__(self, ventana):
        self.ventana = ventana
        self._ocupado = False
        self._botones = []

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        grupo_info = Adw.PreferencesGroup(
            title="Paletas de color",
            description="Cada paleta se aplica AL DASHBOARD: se construye el tema del "
                        "dashboard (dashboard.tema_dashboard), se le cambian los colores por "
                        "los de la paleta y el PNG se sube al panel en la capa OSD. No es un "
                        "tema de fondo ni toca los ficheros de temas: es el dashboard con "
                        "otros colores.")

        self.fila_estado = _ayuda.fila_accion("Estado", "Listo.",
                                              icono="color-select-symbolic")
        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_visible(False)
        self.fila_estado.add_suffix(self.spinner)
        grupo_info.add(self.fila_estado)

        self.fila_aviso = _ayuda.fila_accion(
            "Que se sube", "El dashboard completo con los colores de la paleta, a la capa "
                           "OSD.", icono="dialog-information-symbolic")
        grupo_info.add(self.fila_aviso)
        caja.append(grupo_info)

        # --- rejilla de tarjetas
        grupo_rejilla = Adw.PreferencesGroup(
            title="Paletas predefinidas",
            description="Pulsa Aplicar para mandar el dashboard con esos colores. Las "
                        "muestras son solo la vista previa de la paleta, no tocan el panel.")
        self.rejilla = Gtk.FlowBox()
        self.rejilla.set_selection_mode(Gtk.SelectionMode.NONE)
        self.rejilla.set_homogeneous(True)
        self.rejilla.set_max_children_per_line(2)
        self.rejilla.set_min_children_per_line(1)
        self.rejilla.set_column_spacing(12)
        self.rejilla.set_row_spacing(12)
        self.rejilla.set_hexpand(True)
        for paleta in PALETAS:
            self.rejilla.append(self._tarjeta(paleta))
        grupo_rejilla.add(self.rejilla)
        caja.append(grupo_rejilla)

        # --- ultima paleta aplicada
        grupo_previa = Adw.PreferencesGroup(
            title="Ultima paleta aplicada",
            description="El PNG que se subio, tal cual se dibujo en memoria.")
        self.picture = Gtk.Picture()
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_can_shrink(True)
        self.picture.set_size_request(-1, 220)
        self.pagina_previa = _ayuda.apilar(
            ("vacio", _ayuda.estado_vacio(
                "Sin aplicar todavia",
                "Elige una paleta y pulsa Aplicar: aqui se ensena el dashboard que se "
                "subio al panel.", "color-select-symbolic")),
            ("contenido", _marco_imagen(self.picture)))
        self.pagina_previa.set_visible_child_name("vacio")
        self.pagina_previa.set_size_request(-1, 240)
        caja.append(grupo_previa)
        caja.append(self.pagina_previa)

        self.raiz = _ayuda.pagina_desplazable(caja, ancho_maximo=1040)

    # --- tarjetas

    def _tarjeta(self, paleta):
        """Tarjeta con la muestra de colores y el boton Aplicar."""
        tarjeta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tarjeta.add_css_class("card")
        tarjeta.set_margin_top(10)
        tarjeta.set_margin_bottom(10)
        tarjeta.set_margin_start(10)
        tarjeta.set_margin_end(10)
        tarjeta.set_hexpand(True)
        tarjeta.set_valign(Gtk.Align.START)

        titulo = Gtk.Label(label=paleta.get("nombre") or "(sin nombre)", xalign=0)
        titulo.add_css_class("heading")
        tarjeta.append(titulo)

        if paleta.get("descripcion"):
            descripcion = Gtk.Label(label=paleta["descripcion"], xalign=0)
            descripcion.add_css_class("dim-label")
            descripcion.add_css_class("caption")
            descripcion.set_wrap(True)
            tarjeta.append(descripcion)

        # Banda del fondo, que es lo que mas se nota en el panel.
        fondo = _muestra_color(paleta.get("fondo") or "#000000", alto=44,
                               etiqueta="Fondo %s" % (paleta.get("fondo") or "?"))
        tarjeta.append(fondo)

        # Fila de muestras pequenas (los colores de la paleta).
        fila = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for entrada in paleta.get("colores") or []:
            if not isinstance(entrada, dict) or not entrada.get("color"):
                continue
            muestra = _muestra_color(
                entrada["color"], alto=24, ancho=24,
                etiqueta="%s: %s" % (entrada.get("papel", "color"), entrada["color"]))
            fila.append(muestra)
        tarjeta.append(fila)

        boton = _boton("Aplicar al dashboard",
                       lambda *_a, p=paleta: self._aplicar(p), principal=True)
        self._botones.append(boton)
        tarjeta.append(boton)
        return tarjeta

    # --- aplicar

    def _ocupar(self, ocupado, texto=""):
        self._ocupado = bool(ocupado)
        for boton in self._botones:
            boton.set_sensitive(not self._ocupado)
        self.spinner.set_visible(self._ocupado)
        if self._ocupado:
            self.spinner.start()
        else:
            self.spinner.stop()
        if texto:
            self.fila_estado.set_subtitle(texto)

    def _aplicar(self, paleta):
        """Construye el dashboard con los colores de la paleta y lo sube a la capa OSD."""
        if self._ocupado:
            _avisar(self.ventana, "Ya hay una paleta aplicandose.")
            return False
        nombre = paleta.get("nombre") or "paleta"
        self._ocupar(True, "Preparando la paleta %s..." % nombre)

        def tarea():
            # Una muestra de sensores para no pintar bloques que este equipo no tiene.
            valores = None
            try:
                valores = sensores.Sensores(intervalo=1.0).muestra()
            except Exception:                     # noqa: BLE001  (sin sensores: se pinta todo)
                valores = None
            tema = dashboard.tema_dashboard(titulo="CFV 235", perfil="completo",
                                            valores=valores)
            tema = aplicar_paleta(tema, paleta)
            datos = temas.renderizar_datos(tema)
            resultado = _subir_bytes(self.ventana, datos,
                                     _nombre_seguro(nombre, "paleta"), capa="osd")
            return {"datos": datos, "resultado": resultado, "tema": tema,
                    "widgets": len(tema.get("widgets") or [])}

        def hecho(datos):
            self._ocupar(False)
            if _ayuda.poner_imagen(self.picture, datos["datos"]):
                self.pagina_previa.set_visible_child_name("contenido")
            subida = datos["resultado"]
            if subida.ok:
                self.fila_estado.set_subtitle(
                    "%s aplicada al dashboard (%d widgets). %s"
                    % (nombre, datos["widgets"], subida.resumen()))
                _avisar(self.ventana, "Paleta %s aplicada al dashboard" % nombre)
            else:
                self.fila_estado.set_subtitle(
                    "%s: el panel rechazo la subida (%s)."
                    % (nombre, subida.motivo or subida.resumen()))
                _avisar_error(self.ventana, "El panel rechazo la paleta %s: %s"
                              % (nombre, subida.motivo or subida.resumen()))
            return False

        def fallo(exc):
            self._ocupar(False, "No se pudo aplicar la paleta %s." % nombre)
            _avisar_error(self.ventana, "Paleta %s: %s" % (nombre, _mensaje(exc, corto=True)))
            return False

        _ayuda.en_hilo(tarea, hecho, fallo, nombre="paleta-aplicar")
        return False


def pagina_paletas(ventana):
    """Pagina 3: paletas de color que se aplican AL DASHBOARD (capa OSD).

    Devuelve el widget raiz. Las paletas estan en `PALETAS` (datos) y la sustitucion de
    colores es `aplicar_paleta(tema, paleta)`, por si se quiere reutilizar desde fuera.
    """
    return _Paletas(ventana).raiz
