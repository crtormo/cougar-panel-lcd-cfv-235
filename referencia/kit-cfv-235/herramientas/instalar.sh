#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# instalar.sh - deja el kit utilizable en todo el sistema (opcional: sin esto,
#               todo funciona ejecutandolo desde la carpeta).
#
#   sudo ./herramientas/instalar.sh                  instala todo
#   sudo ./herramientas/instalar.sh --sin-servicio   sin el dashboard automatico
#   sudo ./herramientas/instalar.sh --servicio       ademas arranca el dashboard ya
#
# Instala:
#   /usr/local/lib/cfv-235/cougar/            el paquete (biblioteca + editor)
#   /usr/local/bin/cougar                     la orden de linea de comandos
#   /etc/udev/rules.d/90-cougar-lcd.rules     acceso al panel sin sudo
#   /etc/systemd/system/cougar-dashboard.service   (opcional)
#   /etc/default/cougar-dashboard             tema y periodo del dashboard
#
# NO probado en Linux todavia (se escribio en un PC con Windows).
# ---------------------------------------------------------------------------
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(dirname "$AQUI")"
DESTINO=/usr/local/lib/cfv-235
SERVICIO=0
PERIODO=2
TEMA="$DESTINO/ejemplos/tema_dashboard.json"

while [ $# -gt 0 ]; do
    case "$1" in
        --servicio) SERVICIO=1; shift ;;
        --sin-servicio) SERVICIO=0; shift ;;
        --periodo) PERIODO="$2"; shift 2 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "opcion desconocida: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Hace falta root:  sudo ./herramientas/instalar.sh" >&2
    exit 1
fi

echo "== 1/5  Comprobaciones =="
python3 -c 'import sys; print("   python", sys.version.split()[0])'
if python3 -c 'import PIL' 2>/dev/null; then
    echo "   Pillow presente"
else
    echo "   AVISO: falta Pillow (sudo apt install python3-pil): sin el no se puede"
    echo "          dibujar temas; el cliente y las subidas funcionan igual."
fi

echo "== 2/5  Paquete en $DESTINO =="
install -d -m 0755 "$DESTINO"
rm -rf "$DESTINO/cougar"
cp -r "$RAIZ/cougar" "$DESTINO/cougar"
install -d -m 0755 "$DESTINO/ejemplos" "$DESTINO/pruebas"
cp "$RAIZ"/ejemplos/*.json "$DESTINO/ejemplos/" 2>/dev/null || true
cp "$RAIZ"/pruebas/*.py "$DESTINO/pruebas/" 2>/dev/null || true
cp "$RAIZ"/docs/*.md "$DESTINO/" 2>/dev/null || true
install -m 0644 "$RAIZ/LEEME.md" "$RAIZ/DESARROLLO.md" "$RAIZ/ESQUEMA_TEMA.md" "$DESTINO/" 2>/dev/null || true
find "$DESTINO" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "== 3/5  Orden 'cougar' =="
cat > /usr/local/bin/cougar <<LANZADOR
#!/bin/sh
exec python3 -c 'import sys; sys.path.insert(0, "$DESTINO"); from cougar.cli import main; sys.exit(main())' "\$@"
LANZADOR
chmod 0755 /usr/local/bin/cougar
echo "   /usr/local/bin/cougar"

echo "== 4/5  Regla udev =="
install -Dm644 "$RAIZ/herramientas/90-cougar-lcd.rules" /etc/udev/rules.d/90-cougar-lcd.rules
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true
USUARIO="${SUDO_USER:-$USER}"
if ! id -nG "$USUARIO" 2>/dev/null | tr ' ' '\n' | grep -qx plugdev; then
    echo "   AVISO: $USUARIO no esta en el grupo plugdev:"
    echo "          sudo usermod -aG plugdev $USUARIO    (y volver a entrar en la sesion)"
fi

echo "== 5/5  Dashboard automatico =="
if [ "$SERVICIO" -eq 1 ]; then
    cat > /etc/default/cougar-dashboard <<ENTORNO
# Tema que dibuja el dashboard y cada cuanto lo refresca.
# Edita tu propio tema:  cp $DESTINO/ejemplos/tema_dashboard.json /etc/cougar-tema.json
COUGAR_TEMA=$TEMA
COUGAR_PERIODO=$PERIODO
COUGAR_PNG=/tmp/cougar-dashboard.png
ENTORNO
    install -Dm644 "$RAIZ/herramientas/cougar-dashboard.service" \
        /etc/systemd/system/cougar-dashboard.service
    systemctl daemon-reload
    systemctl enable --now cougar-dashboard.service
    echo "   servicio activo:  systemctl status cougar-dashboard"
    echo "   registro:         journalctl -u cougar-dashboard -f"
else
    echo "   omitido (usa --servicio para dejarlo arrancando solo)"
fi

cat <<FIN

Instalado. Empieza por:

    cougar listar                    ver el panel
    cougar conn                      versiones, espacio, brillo, capas
    cougar patron esquinas --osd     probar la pantalla con un patron
    cougar tema /etc/cougar-tema.json --subir
    python3 -m cougar.editor --abrir              editor visual
    python3 $DESTINO/cougar/simulador.py --traza  panel falso, sin hardware

Documentacion: $DESTINO/DESARROLLO.md y $DESTINO/ESQUEMA_TEMA.md
FIN
