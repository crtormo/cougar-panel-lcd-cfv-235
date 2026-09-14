"""Permite `python3 -m cfv235 <comando>`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
