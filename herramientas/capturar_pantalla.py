#!/usr/bin/env python3
"""Captura la pantalla usando el portal XDG de escritorio.

GTK4 no permite sacar una imagen de una ventana desde PyGObject: `Gsk.Renderer.render_texture`
no acepta un `GskRenderNode` (no es un GObject) y `serialize`/`draw` provocan segfaults — esta
documentado en el issue 439 de pygobject. En Wayland tampoco hay API para leer el framebuffer.

Lo que si existe es el **portal** `org.freedesktop.portal.Screenshot`, que es la via que usa
cualquier aplicacion en Wayland. La primera vez GNOME pide permiso al usuario (y lo recuerda).

    python3 herramientas/capturar_pantalla.py /tmp/pantalla.png [--interactivo]

Con `--interactivo` GNOME deja elegir la ventana o el area; sin el, captura todo el escritorio.

`capturar_bytes()` es la variante pensada para el stream: devuelve los bytes de la captura sin
escribir en disco y sin imprimir nada por cada fotograma.
"""

import sys
import urllib.parse

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

PORTAL = "org.freedesktop.portal.Desktop"
RUTA_PORTAL = "/org/freedesktop/portal/desktop"
IFACE_SCREENSHOT = "org.freedesktop.portal.Screenshot"
IFACE_REQUEST = "org.freedesktop.portal.Request"


def capturar_bytes(interactivo: bool = False, timeout: float = 5.0) -> bytes:
    """Captura el escritorio y devuelve los bytes (PNG). No imprime nada.

    Lanza `RuntimeError` si no hay bus de sesion, el portal no responde o la captura se
    rechaza/cancela. `timeout` corto para que un stream no se quede colgado si el usuario no
    autoriza a tiempo: se reintenta en el siguiente fotograma.
    """
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if bus is None:
        raise RuntimeError("no hay bus de sesion (¿hay un escritorio activo?)")

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
        raise RuntimeError(f"el portal no responde: {exc.message}") from exc
    handle = respuesta.unpack()[0]
    bus.signal_subscribe(PORTAL, IFACE_REQUEST, "Response", handle, None,
                         Gio.DBusSignalFlags.NONE, al_responder, None)

    # el usuario tiene que autorizar la captura la primera vez
    GLib.timeout_add(int(timeout * 1000), bucle.quit)
    bucle.run()

    if resultado["codigo"] != 0 or not resultado["uri"]:
        raise RuntimeError(f"captura rechazada o cancelada (codigo {resultado['codigo']})")
    # el portal devuelve un URI con la ruta escapada (%C3%A1 = "a" con tilde)
    origen = urllib.parse.unquote(resultado["uri"].replace("file://", ""))
    try:
        with open(origen, "rb") as fh:
            return fh.read()
    except OSError as exc:
        raise RuntimeError(f"no pude leer la captura: {exc}") from exc


def capturar(destino: str, interactivo: bool = False, timeout: float = 60.0) -> int:
    """Captura y escribe el PNG en `destino`. Devuelve 0 si fue bien, 1 si no."""
    try:
        datos = capturar_bytes(interactivo, timeout)
    except RuntimeError as exc:
        print(f"!! {exc}", file=sys.stderr)
        mensaje = str(exc).lower()
        if "portal" in mensaje or "bus de sesion" in mensaje:
            print("   (¿esta xdg-desktop-portal-gnome instalado y en marcha?)", file=sys.stderr)
        return 1
    try:
        with open(destino, "wb") as fh:
            fh.write(datos)
    except OSError as exc:
        print(f"!! no pude escribir la captura: {exc}", file=sys.stderr)
        return 1
    print(f"captura: {destino} ({len(datos)} B)")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    interactivo = "--interactivo" in sys.argv
    return capturar(sys.argv[1], interactivo)


if __name__ == "__main__":
    sys.exit(main())
