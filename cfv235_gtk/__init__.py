"""Aplicacion de escritorio (GTK4 + libadwaita) para el panel LCD COUGAR CFV235.

    python3 -m cfv235_gtk
    bin/cfv235-gtk

La aplicacion solo envuelve el nucleo que ya existe en el paquete `cfv235`: no habla
con el hidraw ni dibuja temas por su cuenta, todo eso lo hacen `cfv235.panel`,
`cfv235.canal`, `cfv235.temas` y `cfv235.dashboard`.

Los modulos `cfv235.temas` y `cfv235.widgets` (el motor de dibujo) se importan de forma
perezosa desde las funciones `cargar_*` de este modulo: si todavia no estan en el
arbol, la ventana arranca igual y avisa en la interfaz en vez de morir con ImportError.
"""

__version__ = "1.0.0"

import os

# Raiz del proyecto (la carpeta que contiene `cfv235/`, `cfv235_gtk/`, `bin/`...).
RAIZ_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Carpeta de temas de ejemplo del repositorio hermano, si existe.
CARPETA_EJEMPLOS_EXTERNA = "/home/maximo/cfv235/cfv-235-linux/ejemplos"


def carpetas_de_ejemplos():
    """Carpetas donde buscar temas JSON de ejemplo, sin repetir y existentes."""
    candidatas = [
        os.path.join(RAIZ_APP, "ejemplos"),
        os.path.join(os.getcwd(), "ejemplos"),
        CARPETA_EJEMPLOS_EXTERNA,
    ]
    vistas = []
    for carpeta in candidatas:
        ruta = os.path.abspath(carpeta)
        if ruta not in vistas and os.path.isdir(ruta):
            vistas.append(ruta)
    return vistas


def _importar_perezoso(nombre):
    """(modulo, error). `modulo` es None si el modulo no esta disponible todavia."""
    try:
        modulo = __import__(nombre, fromlist=["*"])
    except ImportError as exc:
        return None, str(exc)
    except Exception as exc:                      # noqa: BLE001  (modulo roto a medias)
        return None, "%s: %s" % (type(exc).__name__, exc)
    return modulo, ""


def cargar_temas():
    """El modulo `cfv235.temas` (motor de dibujo) o (None, motivo)."""
    return _importar_perezoso("cfv235.temas")


def cargar_widgets():
    """El modulo `cfv235.widgets` o (None, motivo)."""
    return _importar_perezoso("cfv235.widgets")


def cargar_dashboard():
    """El modulo `cfv235.dashboard` o (None, motivo)."""
    return _importar_perezoso("cfv235.dashboard")


def cargar_sensores():
    """La clase `cfv235.sensores.Sensores` o (None, motivo)."""
    try:
        from cfv235.sensores import Sensores
    except ImportError as exc:
        return None, str(exc)
    except Exception as exc:                      # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, exc)
    return Sensores, ""
