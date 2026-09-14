#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# probar.sh - comprueba el entorno y el panel, de menos a mas, sin cambiar nada
#             en la pantalla (salvo que le pidas escribir).
#
#   ./herramientas/probar.sh              solo lectura
#   ./herramientas/probar.sh --escribir   ademas sube un patron de prueba (visible)
#   ./herramientas/probar.sh --osd        el patron va a la capa OSD
#
# NO se ha podido ejecutar en Linux todavia (se escribio en un PC con Windows): si algo
# falla, el propio guion dice donde mirar. Los modulos Python si estan probados.
# ---------------------------------------------------------------------------
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(dirname "$AQUI")"
PY="${PYTHON:-python3}"
ESCRIBIR=0
CAPA_ARG=""
FALLOS=0

for arg in "$@"; do
    case "$arg" in
        --escribir) ESCRIBIR=1 ;;
        --osd) CAPA_ARG="--osd" ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "opcion desconocida: $arg" >&2; exit 2 ;;
    esac
done

titulo() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
bien()   { printf '   \033[32mOK\033[0m   %s\n' "$*"; }
mal()    { printf '   \033[31mFALLA\033[0m %s\n' "$*"; FALLOS=$((FALLOS + 1)); }
aviso()  { printf '   \033[33m!\033[0m   %s\n' "$*"; }

cd "$RAIZ"

titulo "1) Python y Pillow"
if command -v "$PY" >/dev/null 2>&1; then
    bien "$("$PY" -c 'import sys; print(sys.executable, sys.version.split()[0])')"
else
    mal "no encuentro python3"
    echo "      Debian/Ubuntu:  sudo apt install python3" >&2
    exit 1
fi
if "$PY" -c 'import PIL' 2>/dev/null; then
    bien "Pillow (dibujo de temas) presente"
else
    aviso "falta Pillow: sudo apt install python3-pil  (solo hace falta para dibujar)"
fi

titulo "2) El paquete cougar se importa y esta coherente"
if "$PY" -c 'import cougar; print("   version", cougar.__version__)' 2>&1; then
    bien "cougar importa"
else
    mal "cougar no importa"
fi
if "$PY" pruebas/test_protocolo.py > /tmp/cfv235-protocolo.log 2>&1; then
    bien "pruebas/test_protocolo.py: $(tail -n 1 /tmp/cfv235-protocolo.log)"
else
    mal "pruebas/test_protocolo.py falla (mira /tmp/cfv235-protocolo.log)"
fi
if "$PY" pruebas/test_temas.py > /tmp/cfv235-temas.log 2>&1; then
    bien "pruebas/test_temas.py: $(tail -n 1 /tmp/cfv235-temas.log)"
else
    aviso "pruebas/test_temas.py: $(tail -n 1 /tmp/cfv235-temas.log)"
fi
if "$PY" pruebas/test_simulador.py > /tmp/cfv235-simulador.log 2>&1; then
    bien "pruebas/test_simulador.py: $(tail -n 1 /tmp/cfv235-simulador.log)"
else
    mal "pruebas/test_simulador.py falla (mira /tmp/cfv235-simulador.log)"
fi

titulo "3) Dispositivos HID y panel"
SALIDA_LISTA="$("$PY" -m cougar.cli listar 2>&1)"
echo "$SALIDA_LISTA" | sed 's/^/   /'
if echo "$SALIDA_LISTA" | grep -q 'PANEL COUGAR'; then
    bien "el panel (1d6b:0126) esta presente"
else
    mal "no aparece el panel 1d6b:0126"
    cat <<'FIN'
      Comprueba:
        - el cable USB de datos (no solo alimentacion)
        - lsusb | grep -i 1d6b
        - ls -l /dev/hidraw*
        - permisos: sudo, o la regla udev (sudo ./herramientas/instalar.sh --sin-servicio)
FIN
fi

titulo "4) Saludo y telemetria"
if SALUDO="$("$PY" -m cougar.cli conn 2>&1)"; then
    echo "$SALUDO" | grep -E 'bootFinish|space|osdState|AVISO' | sed 's/^/   /'
    bien "el panel responde (POST conn)"
    if echo "$SALUDO" | grep -q 'osdState=1'; then
        aviso "hay capa OSD activa: usa 'cougar recovery' antes de subir imagenes"
    fi
else
    echo "$SALUDO" | sed 's/^/   /'
    mal "sin respuesta al saludo (permisos, o el panel ocupado por otro programa)"
fi
if "$PY" -m cougar.cli estado >/dev/null 2>&1; then
    bien "acepta telemetria (STATE all)"
else
    mal "no acepta telemetria"
fi

titulo "5) Sensores de este PC"
"$PY" - <<'FIN'
import sys
sys.path.insert(0, ".")
from cougar import temas
datos = temas.fuentes_disponibles()
if not datos["valores"]:
    print("   !    no se ha podido leer ningun valor")
else:
    faltan = [k for k, v in datos["valores"].items() if v is None]
    print(f"   OK   {len(datos['valores'])} valores leidos, {len(faltan)} sin dato")
    if faltan:
        print("        sin dato: " + ", ".join(sorted(faltan)[:12]))
FIN

titulo "6) Dibujo y subida"
"$PY" - <<'FIN'
import os, sys
sys.path.insert(0, ".")
from cougar import temas
salida = "/tmp/cfv235-prueba.png"
temas.renderizar(temas.tema_por_defecto(), salida)
print(f"   OK   tema de ejemplo dibujado: {salida} ({os.path.getsize(salida)} B)")
FIN
if [ "$ESCRIBIR" -eq 1 ]; then
    aviso "vas a subir un patron al panel: se vera en la pantalla"
    if "$PY" -m cougar.cli patron esquinas $CAPA_ARG; then
        bien "patron subido"
    else
        mal "no se pudo subir el patron"
    fi
else
    echo "   (para subir un patron de prueba: ./herramientas/probar.sh --escribir)"
fi

titulo "Resumen"
if [ "$FALLOS" -eq 0 ]; then
    cat <<'FIN'
   Todo bien. Para desarrollar tu editor:

     python3 -m cougar.editor --abrir        editor visual en el navegador
     python3 -m cougar.simulador --traza     panel falso, sin hardware
     python3 -m cougar.patrones --todos /tmp/patrones
     ./herramientas/cougar --help            todas las ordenes

   Y lee DESARROLLO.md antes de tocar nada: ahi estan las trampas del panel
   (la sesion de subida que caduca, las dos capas, el apagado por espera).
FIN
    exit 0
fi
echo "   ${FALLOS} paso(s) con problemas. Mira DESARROLLO.md, seccion 'si algo no funciona'."
exit 1
