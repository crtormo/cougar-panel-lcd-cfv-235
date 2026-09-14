#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# vista_rapida.sh - reinicia la app en una pagina y guarda una captura de la ventana.
#
#   ./herramientas/vista_rapida.sh estado /tmp/vista.png
#
# Sirve para revisar el diseno: deja la app en la pagina pedida (se guarda en las
# preferencias) y captura la pantalla con el portal XDG. El recorte de la ventana se hace
# con la caja de VENTANA, que se ajusta a mano si cambia el tamano.
#
# Es una herramienta de desarrollo, no forma parte de la app.
# ---------------------------------------------------------------------------
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(dirname "$AQUI")"
PAGINA="${1:-estado}"
SALIDA="${2:-/tmp/vista_$PAGINA.png}"
CFG="$HOME/.config/cfv235/config.json"
PY="${PYTHON:-python3}"

# deja la pagina pedida en las preferencias (la app la restaura al arrancar)
python3 - "$CFG" "$PAGINA" <<'FIN'
import json, os, sys
ruta, pagina = sys.argv[1], sys.argv[2]
datos = {}
if os.path.exists(ruta):
    try:
        with open(ruta, encoding="utf-8") as fh:
            datos = json.load(fh)
    except Exception:
        datos = {}
datos["pagina"] = pagina
os.makedirs(os.path.dirname(ruta), exist_ok=True)
with open(ruta, "w", encoding="utf-8") as fh:
    json.dump(datos, fh, indent=1)
FIN

systemctl --user stop cfv235-gtk 2>/dev/null
sleep 2
systemctl --user reset-failed cfv235-gtk 2>/dev/null
systemd-run --user --unit=cfv235-gtk --description="App CFV235" \
    "$HOME/.local/bin/cfv235-gtk" >/dev/null 2>&1
# Se espera a que la app este EN EL BUS (senal de que ya arranco), en vez de un sleep fijo:
# asi la captura sale ~1 s despues de que exista la ventana y no hay que adivinar el tiempo.
for _intento in $(seq 1 60); do
    if gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
        --method org.freedesktop.DBus.NameHasOwner org.cfv235.Panel 2>/dev/null | grep -q true; then
        break
    fi
    sleep 0.5
done

# Se trae la app al frente y se captura 1 s despues. Si el usuario esta usando otra ventana,
# la captura sale del navegador: se reintenta, comprobando antes que lo capturado PARECE la
# app (fondo oscuro y uniforme en el centro, no una pagina web).
capturar_ventana() {
    gdbus call --session --dest org.cfv235.Panel --object-path /org/cfv235/Panel \
        --method org.gtk.Application.Activate '{}' >/dev/null 2>&1
    sleep 1                       # 1 s: lo justo para que se pinte, sin dar tiempo a perder el foco
    "$PY" -B "$AQUI/capturar_pantalla.py" "$SALIDA" >/dev/null 2>&1 || return 1
    "$PY" - "$SALIDA" <<'FIN'
import sys
from PIL import Image, ImageStat
im = Image.open(sys.argv[1]).convert("RGB")
w, h = im.size
# el centro de la ventana de la app es fondo oscuro y con poco detalle
centro = im.crop((int(w * 0.35), int(h * 0.25), int(w * 0.65), int(h * 0.55)))
stat = ImageStat.Stat(centro)
brillo = sum(stat.mean) / 3
varianza = sum(stat.stddev) / 3
sys.exit(0 if brillo < 90 and varianza < 45 else 1)
FIN
}

for intento in 1 2 3 4; do
    if capturar_ventana; then
        echo "captura de '$PAGINA': $SALIDA (intento $intento)"
        break
    fi
    echo "  .. la ventana no estaba al frente, reintento ($intento)" >&2
    sleep 1
    if [ "$intento" = 4 ]; then
        echo "!! no consegui capturar la ventana (¿esta tapada?)" >&2
        exit 1
    fi
done
# recorta SOLO la ventana de la app (GNOME la centra; el sobrante es la decoracion)
python3 - "$SALIDA" "$CFG" <<'FIN'
import json, os, sys
from PIL import Image
captura, cfg = sys.argv[1], sys.argv[2]
ancho, alto = 1060, 800
try:
    with open(cfg, encoding="utf-8") as fh:
        datos = json.load(fh)
    ancho = int(datos.get("ancho") or ancho)
    alto = int(datos.get("alto") or alto)
except Exception:
    pass
im = Image.open(captura)
pantalla_w, pantalla_h = im.size
# margenes medidos de la decoracion de GNOME (barra de titulo + sombras)
x = max(0, (pantalla_w - ancho) // 2 - 18)
y = max(0, (pantalla_h - alto) // 2 - 48)
caja = (x, y, min(pantalla_w, x + ancho + 36), min(pantalla_h, y + alto + 96))
im.crop(caja).save(captura)
print(f"recortada a la ventana: {caja[2]-caja[0]}x{caja[3]-caja[1]}")
FIN
