#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ejecutar.sh - todas las pruebas del proyecto (no hace falta panel ni Linux de escritorio).
#
#   ./tests/ejecutar.sh            todo
#   ./tests/ejecutar.sh protocolo  solo un conjunto
# ---------------------------------------------------------------------------
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(dirname "$AQUI")"
PY="${PYTHON:-python3}"
cd "$RAIZ"

case "${1:-}" in
    protocolo) conjunto="tests/test_protocolo.py" ;;
    simulador) conjunto="tests/test_simulador.py" ;;
    temas)     conjunto="tests/test_temas.py" ;;
    video)     conjunto="tests/test_video.py" ;;
    regresion) conjunto="tests/test_regresiones.py" ;;
    rendimiento) conjunto="tests/test_rendimiento.py" ;;
    ejemplos)  conjunto="tests/test_ejemplos.py" ;;
    fuentes)   conjunto="tests/test_fuentes_ext.py tests/test_dashboard_clima.py" ;;
    "")        conjunto="tests/test_protocolo.py tests/test_simulador.py tests/test_temas.py tests/test_video.py tests/test_regresiones.py tests/test_rendimiento.py tests/test_ejemplos.py tests/test_fuentes_ext.py tests/test_dashboard_clima.py" ;;
    *) echo "no conozco '$1' (protocolo | simulador | temas | video | regresion | rendimiento | ejemplos | fuentes)" >&2; exit 2 ;;
esac

FALLOS=0
for prueba in $conjunto; do
    [ -f "$prueba" ] || continue
    printf '\n\033[1m### %s\033[0m\n' "$prueba"
    if "$PY" -B "$prueba"; then :; else FALLOS=$((FALLOS + 1)); fi
done

printf '\n'
if [ "$FALLOS" -eq 0 ]; then
    echo "todo correcto"
else
    echo "$FALLOS conjunto(s) con fallos"
fi
exit "$FALLOS"
