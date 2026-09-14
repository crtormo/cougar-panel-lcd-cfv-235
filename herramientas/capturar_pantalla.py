#!/usr/bin/env python3
"""Captura la pantalla usando el portal XDG de escritorio.

GTK4 no permite sacar una imagen de una ventana desde PyGObject: `Gsk.Renderer.render_texture`
no acepta un `GskRenderNode` (no es un GObject) y `serialize`/`draw` provocan segfaults — esta
documentado en el issue 439 de pygobject. En Wayland tampoco hay API para leer el framebuffer.

Lo que si existe es el **portal** `org.freedesktop.portal.Screenshot`, que es la via que usa
cualquier aplicacion en Wayland. La primera vez GNOME pide permiso al usuario (y lo recuerda).

    python3 herramientas/capturar_pantalla.py /tmp/pantalla.png [--interactivo]

Con `--interactivo` GNOME deja elegir la ventana o el area; sin el, captura todo el escritorio.
"""

import sys
import urllib.parse

RAIZ = None
import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

PORTAL = "org.freedesktop.portal.Desktop"
RUTA_PORTAL = "/org/freedesktop/portal/desktop"
IFACE_SCREENSHOT = "org.freedesktop.portal.Screenshot"
IFACE_REQUEST = "org.freedesktop.portal.Request"


def capturar(destino: str, interactivo: bool = False, timeout: float = 60.0) -> int:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if bus is None:
        print("!! no hay bus de sesion", file=sys.stderr)
        return 1

    resultado = {"codigo": 1, "uri": None}
    bucle = GLib.MainLoop()

    def al_responder(_bus, _remitente, _ruta, _iface, _senal, parametros, _datos):
        codigo, opciones = parametros.unpack()
        resultado["codigo"] = int(codigo)
        resultado["uri"] = opciones.get("uri")
        bucle.quit()

    try:
        respuesta = bus.call_sync(
            PORTAL, RUTA_PORTAL, IFACE_SCREENSHOT, "Screenshot",
            GLib.Variant("(sa{sv})", ("", {"interactive": GLib.Variant("b", interactivo)})),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as exc:
        print(f"!! el portal no responde: {exc.message}", file=sys.stderr)
        print("   (¿esta xdg-desktop-portal-gnome instalado y en marcha?)", file=sys.stderr)
        return 1
    handle = respuesta.unpack()[0]
    bus.signal_subscribe(PORTAL, IFACE_REQUEST, "Response", handle, None,
                         Gio.DBusSignalFlags.NONE, al_responder, None)

    # el usuario tiene que autorizar la captura la primera vez
    GLib.timeout_add(int(timeout * 1000), bucle.quit)
    bucle.run()

    if resultado["codigo"] != 0 or not resultado["uri"]:
        print(f"!! captura rechazada o cancelada (codigo {resultado['codigo']})", file=sys.stderr)
        return 1
    # el portal devuelve un URI con la ruta escapada (%C3%A1 = "a" con tilde)
    origen = urllib.parse.unquote(resultado["uri"].replace("file://", ""))
    try:
        datos = open(origen, "rb").read()
        with open(destino, "wb") as fh:
            fh.write(datos)
        print(f"captura: {destino} ({len(datos)} B) desde {origen}")
    except OSError as exc:
        print(f"!! no pude copiar la captura: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    interactivo = "--interactivo" in sys.argv
    return capturar(sys.argv[1], interactivo)


if __name__ == "__main__":
    sys.exit(main())
