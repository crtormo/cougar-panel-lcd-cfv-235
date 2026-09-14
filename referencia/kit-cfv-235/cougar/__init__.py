"""cfv-235 — kit de desarrollo para el panel COUGAR CFV235 (9.16", 1920x462).

Todo lo que hay aqui salio del cliente que se verifico contra el panel de verdad
(Windows hizo de banco de pruebas) y de la ingenieria inversa del editor oficial.
La implementacion original, sin tocar, esta en `referencia/cougar_panel.py`: sirve
para comprobar que este paquete sigue produciendo exactamente las mismas tramas
(lo hace `pruebas/test_coherencia.py`).

    from cougar import Panel, temas, widgets
    from cougar.fuentes import Fuentes

    panel = Panel()                       # busca /dev/hidraw con 1d6b:0126
    with panel:
        print(panel.propiedades())        # JSON de `POST conn`
        panel.subir("fondo.png")          # imagen -> capa de fondo

Para desarrollar el editor sin la pantalla delante: `cougar.simulador` levanta un
panel falso sobre un PTY y el cliente se conecta con `--device /dev/pts/N`.
"""

__version__ = "1.0.0"

# `widgets.py` y `fuentes.py` vienen del proyecto original, donde se importaban entre si
# como modulos sueltos (`import sensores`, `from fuentes import Fuentes`). Para no tocar
# codigo ya verificado, basta con que la carpeta del paquete este en sys.path.
import os as _os
import sys as _sys

_AQUI = _os.path.dirname(_os.path.abspath(__file__))
if _AQUI not in _sys.path:
    _sys.path.insert(0, _AQUI)

# OJO: `simulador` NO se importa aqui a proposito. Usa el modulo `pty`, que solo existe
# en Linux, y el resto del paquete tiene que poder importarse en cualquier sistema para
# analizar tramas o dibujar temas. Se importa a mano:  from cougar import simulador
from . import fuentes, patrones, protocolo, sensores, temas, widgets  # noqa: F401
from .panel import Panel, Respuesta, buscar_panel, listar_dispositivos  # noqa: F401

__all__ = [
    "Panel", "Respuesta", "buscar_panel", "listar_dispositivos",
    "protocolo", "temas", "widgets", "fuentes", "sensores", "patrones",
]
