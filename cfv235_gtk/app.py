"""Punto de entrada de la aplicacion de escritorio del panel COUGAR CFV235.

    python3 -m cfv235_gtk
    bin/cfv235-gtk

Es un `Adw.Application` normal: la ventana se construye en `activate` y el panel se
cierra en `shutdown`, de modo que no queda ningun hidraw abierto al salir.

Con `CFV235_ID_APLICACION` se puede arrancar una segunda instancia con otro identificador
de D-Bus: lo usan las pruebas y sirve para mirar dos cosas a la vez sin que la segunda
invoque a la primera (GApplication es de instancia unica por identificador).
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------------------
# Dialogo de ficheros: el portal del escritorio solo funciona si la app se lanzo DESDE el
# escritorio.
#
# `Gtk.FileDialog` no dibuja el dialogo por si mismo: se lo pide al portal del escritorio
# (`org.freedesktop.portal.FileChooser`). Para colocarlo como hijo de la ventana, el portal
# necesita el *token* de activacion que el escritorio le pasa a la app al arrancarla
# (`XDG_ACTIVATION_TOKEN`). Si la app se lanza desde una terminal, un servicio de systemd o
# `systemd-run`, ese token no existe, el portal falla con:
#
#     xdg-desktop-portal-gnome: Failed to associate portal window with parent window ''
#
# y el dialogo NO APARECE: el boton parece no hacer nada, sin ningun error. Con
# `GTK_USE_PORTAL=0` GTK usa su propio dialogo, que funciona siempre.
#
# Asi que: si hay token (lanzada desde el escritorio) se usa el portal, que es el dialogo
# nativo e integrado; si no lo hay, el de GTK. Esto tiene que decidirse ANTES de que GTK se
# inicialice, o sea, antes de `run()`.
# ---------------------------------------------------------------------------------------
# Se usa el dialogo **propio de GTK** por defecto. Es la opcion que funciona siempre: el
# portal necesita el token de activacion, y sin el falla en silencio (el boton parece no hacer
# nada, sin ningun error). Con `CFV235_DIALOGO_PORTAL=1` se usa el portal nativo de GNOME, que
# es mas integrado (recientes, carpetas de la nube...) pero solo es fiable si la app se lanzo
# desde el escritorio.
if os.environ.get("CFV235_DIALOGO_PORTAL", "").strip().lower() not in (
        "1", "si", "sí", "true", "yes", "on"):
    os.environ["GTK_USE_PORTAL"] = "0"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

from .ventana import construir_ventana  # noqa: E402


def id_de_aplicacion():
    """Identificador de D-Bus de la app (se puede cambiar con `CFV235_ID_APLICACION`)."""
    return os.environ.get("CFV235_ID_APLICACION") or "org.cfv235.Panel"


class Aplicacion(Adw.Application):
    """Aplicacion GTK4 + libadwaita del panel CFV235."""

    def __init__(self, informe=None, sin_panel=None):
        identificador = id_de_aplicacion()
        if informe is not None and not os.environ.get("CFV235_ID_APLICACION"):
            # En modo informe NO se puede usar el identificador normal: si la app ya esta
            # abierta, GApplication delegaria en ella y esta instancia saldria sin volcar
            # nada. Con uno propio, el informe siempre se ejecuta.
            identificador = "org.cfv235.Informe"
        super().__init__(application_id=identificador,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.ventana = None
        # Modo informe: en vez de quedarse abierta, vuelca la interfaz en texto y sale.
        # Es para revisar la app sin mirarla (ni capturas ni ojos).
        self.informe = informe

    def do_activate(self):
        if self.ventana is None:
            self.ventana = construir_ventana(self)
        if self.informe is not None:
            self.ventana.present()
            # se espera un momento a que se lea el panel y se pinten los controles
            GLib.timeout_add(2000, self._volcar_y_salir)
            return
        self.ventana.present()

    def _volcar_y_salir(self):
        """Escribe el volcado de la interfaz y cierra. Devuelve False (un solo disparo)."""
        try:
            texto = self.ventana.volcado_ui()
            if self.informe in ("-", ""):
                print(texto)
            else:
                with open(self.informe, "w", encoding="utf-8") as fh:
                    fh.write(texto + "\n")
                print(f"informe de la interfaz: {self.informe} "
                      f"({texto.count(chr(10)) + 1} lineas)")
        except Exception as exc:                      # noqa: BLE001
            print(f"!! no se pudo volcar la interfaz: {exc}", file=sys.stderr)
        finally:
            self.quit()
        return False

    def do_shutdown(self):
        # Cierra el Panel (el hidraw) antes de que muera el proceso.
        if self.ventana is not None:
            self.ventana.cerrar_panel()
        Adw.Application.do_shutdown(self)


def main(argv=None):
    """Arranca la aplicacion y devuelve el codigo de salida.

    Con `--informe [RUTA]` (o `-` para la salida estandar) no se queda abierta: escribe lo
    que ensena la interfaz y termina. Sirve para revisar la app por texto.
    """
    argumentos = list(argv if argv is not None else sys.argv)
    informe = None
    if "--informe" in argumentos:
        posicion = argumentos.index("--informe")
        informe = "-"
        if posicion + 1 < len(argumentos) and not argumentos[posicion + 1].startswith("-"):
            informe = argumentos[posicion + 1]
            del argumentos[posicion + 1]
        del argumentos[posicion]
    aplicacion = Aplicacion(informe=informe)
    return aplicacion.run(argumentos)


if __name__ == "__main__":
    sys.exit(main())
