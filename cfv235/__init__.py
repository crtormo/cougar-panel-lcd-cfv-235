"""cfv235 - aplicacion para el panel COUGAR CFV235 (9,16", 1920x462, USB HID 1d6b:0126).

    python3 -m cfv235 doctor          estado del canal y del panel
    python3 -m cfv235 estado          propiedades del panel (JSON)
    python3 -m cfv235 subir fondo.png sube una imagen
    cfv235-gtk                        aplicacion de escritorio

El paquete se organiza asi:

    protocolo.py   tramas, escape, checksum, informes de medios (sin E/S)
    canal.py       /dev/hidraw: como escribirle al panel, con autonegociacion
    panel.py       el panel como objeto: estado, ordenes y subida de imagenes
    sensores.py    metricas del PC por /proc y /sys
    temas.py       temas JSON -> PNG (motor de dibujo)
    dashboard.py   el dashboard que se sube al panel
    simulador.py   panel falso por PTY, para trabajar sin hardware
"""

__version__ = "1.0.0"

from . import canal, panel, protocolo  # noqa: F401
from .canal import (Canal, ErrorCanal, PanelOcupado, SinPanel, SinPermisos,  # noqa: F401
                    Variante, buscar, info_dispositivo, listar)
from .panel import Panel, ResultadoSubida, telemetria_demo  # noqa: F401

__all__ = [
    "Panel", "ResultadoSubida", "Canal", "Variante", "ErrorCanal", "SinPanel",
    "SinPermisos", "PanelOcupado", "buscar", "listar", "info_dispositivo",
    "telemetria_demo", "protocolo", "canal", "panel", "__version__",
]
