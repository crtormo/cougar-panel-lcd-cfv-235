"""Permite `python3 -m cfv235_gtk`.

`python -m paquete` ejecuta `paquete/__main__.py`, asi que este fichero es lo que hace
falta para que el punto de entrada documentado en `app.py` funcione.
"""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
