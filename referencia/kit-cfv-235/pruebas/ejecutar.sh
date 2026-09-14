#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ejecutar.sh - todas las pruebas del kit (no hace falta panel ni Linux).
#
#   ./pruebas/ejecutar.sh            todo
#   ./pruebas/ejecutar.sh protocolo  solo una parte
# ---------------------------------------------------------------------------
set -uo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(dirname "$AQUI")"
PY="${PYTHON:-python3}"
cd "$RAIZ"

if [ $# -gt 0 ]; then
    case "$1" in
        protocolo) conjunto="pruebas/test_protocolo.py" ;;
        temas)     conjunto="pruebas/test_temas.py" ;;
        simulador) conjunto="pruebas/test_simulador.py" ;;
        tramas)    conjunto="herramientas/probar_tramas.py" ;;
        *) echo "no conozco '$1' (protocolo | temas | simulador | tramas)" >&2; exit 2 ;;
    esac
else
    conjunto="herramientas/probar_tramas.py pruebas/test_protocolo.py pruebas/test_temas.py pruebas/test_simulador.py"
fi

FALLOS=0
for prueba in $conjunto; do
    printf '\n\033[1m### %s\033[0m\n' "$prueba"
    if "$PY" "$prueba"; then
        :
    else
        FALLOS=$((FALLOS + 1))
    fi
done

printf '\n'
if [ "$FALLOS" -eq 0 ]; then
    echo "todo correcto"
else
    echo "$FALLOS conjunto(s) de pruebas con fallos"
fi
exit "$FALLOS"
