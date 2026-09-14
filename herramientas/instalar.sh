#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# instalar.sh - deja la app cfv235 lista para usar.
#
#   ./herramientas/instalar.sh                 regla udev + ordenes + servicio
#   ./herramientas/instalar.sh --sin-servicio  sin el dashboard automatico
#   ./herramientas/instalar.sh --sin-udev      si ya tienes permisos en el panel
#
# La regla udev necesita sudo; el resto NO (el dashboard corre como tu usuario, no como
# root: con la regla udev no hace falta ningun privilegio).
# ---------------------------------------------------------------------------
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(dirname "$AQUI")"
PY="${PYTHON:-python3}"
SERVICIO=1
UDEV=1

for arg in "$@"; do
    case "$arg" in
        --sin-servicio) SERVICIO=0 ;;
        --sin-udev) UDEV=0 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "opcion desconocida: $arg" >&2; exit 2 ;;
    esac
done

titulo() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
bien()   { printf '   \033[32mOK\033[0m   %s\n' "$*"; }
aviso()  { printf '   \033[33m!\033[0m   %s\n' "$*"; }
mal()    { printf '   \033[31mFALLA\033[0m %s\n' "$*"; }

titulo "1) Comprobaciones"
if command -v "$PY" >/dev/null 2>&1; then
    bien "$("$PY" -c 'import sys; print(sys.executable, sys.version.split()[0])')"
else
    mal "no encuentro python3"; exit 1
fi
if "$PY" -c 'import PIL' 2>/dev/null; then
    bien "Pillow presente (dibujo de temas y dashboard)"
else
    aviso "falta Pillow: sudo apt install python3-pil  (sin el no se puede dibujar)"
fi
if "$PY" -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' 2>/dev/null; then
    bien "GTK4 + libadwaita (app de escritorio)"
else
    aviso "falta GTK4/libadwaita: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
fi

titulo "2) Pruebas (sin panel)"
if "$PY" -B "$RAIZ/tests/test_protocolo.py" >/tmp/cfv235-test-protocolo.log 2>&1; then
    bien "protocolo: $(tail -n 1 /tmp/cfv235-test-protocolo.log)"
else
    mal "protocolo falla (mira /tmp/cfv235-test-protocolo.log)"
fi
if "$PY" -B "$RAIZ/tests/test_simulador.py" >/tmp/cfv235-test-simulador.log 2>&1; then
    bien "canal y subida (simulador): $(tail -n 1 /tmp/cfv235-test-simulador.log)"
else
    mal "simulador falla (mira /tmp/cfv235-test-simulador.log)"
fi

if [ "$UDEV" -eq 1 ]; then
    titulo "3) Permisos del panel (regla udev, necesita sudo)"
    if [ -e /etc/udev/rules.d/90-cougar-lcd.rules ]; then
        bien "la regla ya esta instalada"
    else
        echo "   hace falta sudo una sola vez:"
        sudo "$AQUI/instalar_udev.sh" || aviso "no se pudo instalar la regla udev"
    fi
fi

titulo "4) Ordenes en ~/.local/bin"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/cfv235" <<LANZADOR
#!/bin/sh
PYTHONPATH="$RAIZ" exec "$PY" -m cfv235 "\$@"
LANZADOR
chmod 755 "$HOME/.local/bin/cfv235"
bien "$HOME/.local/bin/cfv235"
cat > "$HOME/.local/bin/cfv235-gtk" <<LANZADOR
#!/bin/sh
PYTHONPATH="$RAIZ" exec "$PY" -m cfv235_gtk "\$@"
LANZADOR
chmod 755 "$HOME/.local/bin/cfv235-gtk"
bien "$HOME/.local/bin/cfv235-gtk"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) aviso "añade ~/.local/bin al PATH:  echo 'export PATH=\$HOME/.local/bin:\$PATH' >> ~/.bashrc" ;;
esac

titulo "4.bis Entrada en el menu de aplicaciones"
if [ -f "$RAIZ/desktop/cfv235.desktop" ] && [ -f "$RAIZ/desktop/cfv235.png" ]; then
    mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
    # OJO: el Exec se escribe con RUTA ABSOLUTA. El PATH de la sesion grafica (GNOME) no
    # incluye ~/.local/bin, asi que un `Exec=cfv235-gtk` a secas hace que el menu ignore la
    # entrada (es el fallo que tenia la primera version de esta entrada).
    sed -e "s|^Exec=cfv235-gtk|Exec=$HOME/.local/bin/cfv235-gtk|" \
        -e "s|^Exec=cfv235$|Exec=$HOME/.local/bin/cfv235|" \
        "$RAIZ/desktop/cfv235.desktop" > "$HOME/.local/share/applications/cfv235.desktop"
    if ! grep -q "^Path=" "$HOME/.local/share/applications/cfv235.desktop"; then
        sed -i "s|^Exec=.*|&\nPath=$RAIZ|" "$HOME/.local/share/applications/cfv235.desktop"
    fi
    cp "$RAIZ/desktop/cfv235.png" "$HOME/.local/share/icons/hicolor/256x256/apps/cfv235.png"
    command -v desktop-file-validate >/dev/null 2>&1 \
        && desktop-file-validate "$HOME/.local/share/applications/cfv235.desktop" \
        && bien "entrada valida (Exec con ruta absoluta)"
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
    command -v gtk-update-icon-cache >/dev/null 2>&1 \
        && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null
    bien "aparece en el menu como «Panel COUGAR CFV235»"
    # lanzador en el escritorio (Ubuntu lo muestra con la extension de iconos, que viene activa)
    if [ -d "$HOME/Escritorio" ]; then
        cp "$HOME/.local/share/applications/cfv235.desktop" "$HOME/Escritorio/"
        chmod +x "$HOME/Escritorio/cfv235.desktop"
        gio set "$HOME/Escritorio/cfv235.desktop" metadata::trusted true 2>/dev/null || true
        bien "lanzador en el escritorio"
    fi
else
    aviso "no encuentro desktop/cfv235.desktop (se omite la entrada de menu)"
fi

if [ "$SERVICIO" -eq 1 ]; then
    titulo "5) Dashboard automatico (servicio de usuario)"
    mkdir -p "$HOME/.config/systemd/user"
    cp "$RAIZ/systemd/cfv235-dashboard.service" "$HOME/.config/systemd/user/"
    systemctl --user daemon-reload 2>/dev/null && bien "unidad instalada"
    if systemctl --user enable --now cfv235-dashboard.service 2>/dev/null; then
        bien "servicio activo: systemctl --user status cfv235-dashboard"
        bien "registro:         journalctl --user -u cfv235-dashboard -f"
    else
        aviso "no se pudo arrancar ahora; pruebalo con: systemctl --user enable --now cfv235-dashboard"
    fi
    aviso "para que siga con la sesion cerrada:  sudo loginctl enable-linger $USER"
else
    titulo "5) Servicio"
    echo "   omitido (--sin-servicio)"
fi

cat <<FIN

Listo. Empieza por:

    cfv235 doctor                 estado del canal, del panel y de los sensores
    cfv235 dashboard --una-vez    manda un fotograma del dashboard al panel
    cfv235 subir imagen.png --osd sube tu propia imagen (la capa OSD no acumula espacio)
    cfv235-gtk                    aplicacion de escritorio

Documentacion: $RAIZ/README.md y $RAIZ/docs/CANAL.md
FIN
