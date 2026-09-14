#!/usr/bin/env python3
"""Captura la ventana de la app a un PNG, para poder revisar el diseno sin mirar la pantalla.

No necesita herramientas externas (en este equipo no hay grim, scrot ni ffmpeg): se pide a
GTK que dibuje la ventana en un `Gtk.Snapshot` y el resultado se guarda con GSK.

    python3 herramientas/capturar_ventana.py salida.png [pagina] [ancho] [alto]

`pagina` es el nombre de la pestana a capturar (estado, imagen, temas, dashboard, video,
diagnostico). Con `CFV235_PANEL` se puede apuntar a un PTY del simulador para que la captura
no dependa del panel real.

Es una herramienta de desarrollo: no forma parte de la app.
"""

import os
import sys
import threading

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402

from cfv235_gtk import ventana as modulo_ventana  # noqa: E402


def capturar(salida: str, pagina: str = "", ancho: int = 1180, alto: int = 760,
             espera_ms: int = 2500) -> int:
    os.environ.setdefault("CFV235_ID_APLICACION", "org.cfv235.Captura")
    aplicacion = Adw.Application(application_id=os.environ["CFV235_ID_APLICACION"])
    resultado = {"codigo": 1}

    def guardar(ventana):
        """Guarda la imagen que GTK ya tiene pintada de la ventana."""
        try:
            if pagina and hasattr(ventana, "pila"):
                ventana.pila.set_visible_child_name(pagina)
            paintable = Gtk.WidgetPaintable.new(ventana)
            imagen = paintable.get_current_image()
            if imagen is None or not hasattr(imagen, "save_to_png"):
                return False                          # aun no hay frame pintado
            imagen.save_to_png(salida)
            resultado["codigo"] = 0
            print(f"captura: {salida} ({imagen.get_width()}x{imagen.get_height()})")
        except Exception as exc:                      # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"!! no se pudo capturar: {exc}", file=sys.stderr)
            resultado["codigo"] = 1
        aplicacion.quit()
        return True

    def activar(app):
        v = modulo_ventana.construir_ventana(app)
        estado = {"tics": 0}

        def al_dibujar(widget, reloj, _datos):
            """Se llama en cada fotograma: en el primero ya hay contenido que capturar."""
            estado["tics"] += 1
            if not widget.get_mapped():
                return True
            if guardar(v):
                return False
            if estado["tics"] > 120:                  # ~2 s de fotogramas: rendirse
                print("!! GTK no llego a pintar la ventana", file=sys.stderr)
                aplicacion.quit()
                return False
            return True

        v.add_tick_callback(al_dibujar, None)
        v.present()

    aplicacion.connect("activate", activar)
    threading.Timer(max(20, espera_ms / 1000 + 15), lambda: GLib.idle_add(aplicacion.quit)).start()
    aplicacion.run([])
    return resultado["codigo"]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    salida = sys.argv[1]
    pagina = sys.argv[2] if len(sys.argv) > 2 else ""
    ancho = int(sys.argv[3]) if len(sys.argv) > 3 else 1180
    alto = int(sys.argv[4]) if len(sys.argv) > 4 else 760
    return capturar(salida, pagina, ancho, alto)


if __name__ == "__main__":
    sys.exit(main())
