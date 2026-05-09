#!/usr/bin/env bash
# start.sh — Linux / macOS launcher
# Duplo clique no Desktop (após rodar install-linux.sh) ou: bash start.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

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

if [ -z "$PY" ]; then
    # Tentar exibir mensagem gráfica se não há terminal
    MSG="Python 3 não encontrado.\nInstale em: https://python.org"
    if command -v zenity &>/dev/null; then
        zenity --error --text="$MSG" --title="Notion → Anki" 2>/dev/null
    elif command -v notify-send &>/dev/null; then
        notify-send "Notion → Anki" "Python 3 não encontrado. Instale em python.org"
    else
        echo -e "$MSG" >&2
    fi
    exit 1
fi

# ── Executar launcher ─────────────────────────────────────────────────────────
"$PY" "$DIR/launcher.py"
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    MSG="Erro ao iniciar Notion → Anki (código $EXIT_CODE).\nVeja o log em: $DIR/sync.log"
    if command -v zenity &>/dev/null; then
        zenity --error --text="$MSG" --title="Notion → Anki" 2>/dev/null
    fi
fi

exit $EXIT_CODE
