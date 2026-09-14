#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# instalar_udev.sh - deja el panel COUGAR CFV235 accesible sin sudo.
#
#   sudo ./herramientas/instalar_udev.sh
#
# Instala la regla udev, la recarga y comprueba que el panel queda accesible para el
# usuario que lanzo sudo (no root). No toca nada mas del sistema.
# ---------------------------------------------------------------------------
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(dirname "$AQUI")"
REGLA_ORIGEN="$RAIZ/udev/90-cougar-lcd.rules"
REGLA_DESTINO=/etc/udev/rules.d/90-cougar-lcd.rules

if [ "$(id -u)" -ne 0 ]; then
    echo "Hace falta root:  sudo $0" >&2
    exit 1
fi

if [ ! -f "$REGLA_ORIGEN" ]; then
    echo "!! no encuentro $REGLA_ORIGEN" >&2
    exit 1
fi

USUARIO="${SUDO_USER:-$USER}"

echo "== 1/3  Regla udev =="
install -Dm644 "$REGLA_ORIGEN" "$REGLA_DESTINO" && echo "   instalada en $REGLA_DESTINO"

echo "== 2/3  Recargar y aplicar =="
udevadm control --reload-rules && echo "   reglas recargadas"
udevadm trigger --subsystem-match=hidraw && echo "   evento disparado a los hidraw"
# udev a veces tarda un instante en aplicar los permisos
sleep 1

echo "== 3/3  Comprobacion =="
encontrado=0
for h in /dev/hidraw*; do
    [ -e "$h" ] || continue
    uevent="/sys/class/hidraw/$(basename "$h")/device/uevent"
    if grep -q 'HID_ID=0003:00001D6B:00000126' "$uevent" 2>/dev/null; then
        encontrado=1
        printf '   %s -> %s\n' "$h" "$(stat -c '%A %U:%G' "$h")"
        if sudo -u "$USUARIO" test -r "$h" && sudo -u "$USUARIO" test -w "$h"; then
            echo "   OK: $USUARIO puede leer y escribir en $h"
        else
            echo "   AVISO: $USUARIO todavia no puede abrir $h"
            echo "          - comprueba que $USUARIO esta en el grupo 'users': id -nG $USUARIO"
            echo "          - para concederselo:  sudo usermod -aG users $USUARIO   (y reinicia sesion)"
            echo "          - o desconecta y vuelve a conectar el USB del panel"
        fi
    fi
done

if [ "$encontrado" -eq 0 ]; then
    echo "   no aparece ningun hidraw con 1d6b:0126 (¿panel desconectado?)"
    echo "   la regla queda instalada: se aplicara solo cuando conectes el panel"
fi

echo
echo "Listo. Ahora, sin sudo:"
echo "    python3 -m cfv235 doctor     # estado del panel y del canal"
