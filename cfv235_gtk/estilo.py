"""Identidad visual de la app: hoja de estilo y preferencia de tema claro/oscuro.

La app se apoya en libadwaita, asi que aqui solo se anade lo que da caracter propio:

  * un **color de acento** (el cian del panel) para titulos, valores y acentos;
  * **tarjetas** con borde y esquinas redondeadas, en vez de listas grises planas;
  * **pilidoras de estado** (en marcha / parado / aviso) con color de fondo suave;
  * un estilo para los **valores** de las propiedades (mas grandes, cifras tabulares);
  * el marco de las **vistas previas**.

El CSS usa los colores del tema de libadwaita (`@card_bg_color`, `@borders`, `@accent_color`...)
para que funcione igual en claro y en oscuro: no se fijan colores de fondo a mano salvo el
acento de identidad, que si tiene que ser siempre el mismo cian.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

# El cian del panel: es el color con el que se dibuja el dashboard y da identidad a la app.
ACENTO = "#00b4ff"

CSS = """
/* --- color de identidad ------------------------------------------------- */
@define-color cfv_acento #00b4ff;
@define-color cfv_acento_suave alpha(#00b4ff, 0.16);

/* El titulo de la app, en la barra superior, lleva el acento. */
.cfv-marca {
    font-weight: 800;
    color: @cfv_acento;
}

/* --- tarjetas ----------------------------------------------------------- */
/* En vez de listas grises planas: cada bloque es una tarjeta con borde suave. */
.cfv-tarjeta {
    background-color: @card_bg_color;
    border: 1px solid @borders;
    border-radius: 14px;
    padding: 4px;
}

/* Una tarjeta de dato grande (el valor que se senala). */
.cfv-dato {
    background-color: @card_bg_color;
    border: 1px solid @borders;
    border-radius: 14px;
    padding: 14px 18px;
}

.cfv-dato .cfv-valor {
    font-size: 1.9em;
    font-weight: 800;
    color: @cfv_acento;
    /* cifras de ancho fijo: al cambiar el numero no baila el texto */
    font-feature-settings: "tnum";
}

.cfv-dato .cfv-etiqueta {
    font-size: 0.8em;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: @dim_label_color;
}

.cfv-dato .cfv-pie {
    font-size: 0.85em;
    color: @dim_label_color;
}

/* --- valores de las propiedades ---------------------------------------- */
.cfv-valor-propiedad {
    font-weight: 700;
    font-feature-settings: "tnum";
}

.cfv-clave-propiedad {
    font-family: monospace;
    font-size: 0.92em;
    color: @dim_label_color;
}

/* --- pilidoras de estado ------------------------------------------------ */
.cfv-pildora {
    border-radius: 999px;
    padding: 2px 12px;
    font-size: 0.82em;
    font-weight: 700;
}

.cfv-pildora-ok {
    background-color: alpha(@success_color, 0.18);
    color: @success_color;
}

.cfv-pildora-aviso {
    background-color: alpha(@warning_color, 0.20);
    color: @warning_color;
}

.cfv-pildora-error {
    background-color: alpha(@error_color, 0.18);
    color: @error_color;
}

.cfv-pildora-neutra {
    background-color: alpha(@dim_label_color, 0.15);
    color: @dim_label_color;
}

/* --- vistas previas ----------------------------------------------------- */
/* El marco del panel: un borde como el del aparato, para que se vea que es una pantalla. */
.cfv-marco-panel {
    background-color: #05070b;
    border: 2px solid @borders;
    border-radius: 12px;
    padding: 6px;
}

/* --- secciones ---------------------------------------------------------- */
.cfv-titulo-seccion {
    font-weight: 700;
    font-size: 1.05em;
}

.cfv-descripcion-seccion {
    color: @dim_label_color;
    font-size: 0.92em;
}

/* Los bloques de datos tecnicos (diagnostico) en monoespaciado y algo mas juntos. */
.cfv-tecnico {
    font-family: monospace;
    font-size: 0.92em;
}
"""


def aplicar() -> None:
    """Registra la hoja de estilo de la app. Se puede llamar mas de una vez."""
    pantalla = Gdk.Display.get_default()
    if pantalla is None:
        return
    proveedor = Gtk.CssProvider()
    proveedor.load_from_data(CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(
        pantalla, proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


# ------------------------------------------------------------------ tema de la app
ESQUEMAS = (
    ("sistema", "Segun el sistema", Adw.ColorScheme.DEFAULT),
    ("claro", "Claro", Adw.ColorScheme.FORCE_LIGHT),
    ("oscuro", "Oscuro", Adw.ColorScheme.FORCE_DARK),
)


def esquema_actual() -> str:
    """Nombre del esquema que esta usando la app ("sistema", "claro" u "oscuro")."""
    gestor = Adw.StyleManager.get_default()
    esquema = gestor.get_color_scheme()
    for nombre, _etiqueta, valor in ESQUEMAS:
        if valor == esquema:
            return nombre
    return "sistema"


def poner_esquema(nombre: str) -> bool:
    """Cambia el esquema de color de la app. Devuelve False si el nombre no existe."""
    for clave, _etiqueta, valor in ESQUEMAS:
        if clave == nombre:
            Adw.StyleManager.get_default().set_color_scheme(valor)
            return True
    return False


def menu_de_esquema() -> "Gio.Menu":
    """Submenu con las tres opciones de tema, marcando la activa."""
    from gi.repository import Gio
    menu = Gio.Menu()
    actual = esquema_actual()
    for clave, etiqueta, _valor in ESQUEMAS:
        item = Gio.MenuItem.new(etiqueta, f"win.tema::{clave}")
        if clave == actual:
            item.set_attribute_value("icon", None) if False else None
        menu.append_item(item)
    return menu
