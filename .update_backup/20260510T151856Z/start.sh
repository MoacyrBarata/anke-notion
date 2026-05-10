#!/usr/bin/env bash
# start.sh — Linux / macOS launcher

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Redirecionar saída para log (terminal oculto — sem spam visual)
LOG="$DIR/start.log"
exec > "$LOG" 2>&1
echo "=== $(date) ==="
echo "DIR: $DIR"

# ── Localizar Python 3 ────────────────────────────────────────────────────────
PY=""
for candidate in python3 python3.12 python3.11 python3.10 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" --version 2>&1)
        if echo "$version" | grep -q "Python 3\."; then
            PY="$candidate"
            break
        fi
    fi
done

echo "Python: ${PY:-NENHUM}"

if [ -z "$PY" ]; then
    if command -v zenity &>/dev/null; then
        zenity --error \
            --text="Python 3 não encontrado.\nInstale em: https://python.org" \
            --title="Notion → Anki" 2>/dev/null &
    elif command -v notify-send &>/dev/null; then
        notify-send "Notion → Anki" "Python 3 não encontrado. Instale em python.org"
    fi
    exit 1
fi

# ── Executar launcher (sem terminal visível) ──────────────────────────────────
echo "Iniciando launcher.py..."
"$PY" "$DIR/launcher.py"
EXIT_CODE=$?
echo "Encerrado com código: $EXIT_CODE"

if [ $EXIT_CODE -ne 0 ] && command -v zenity &>/dev/null; then
    zenity --error \
        --text="Erro ao iniciar (código $EXIT_CODE).\nLog: $LOG" \
        --title="Notion → Anki" 2>/dev/null &
fi

exit $EXIT_CODE
