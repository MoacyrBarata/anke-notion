#!/usr/bin/env bash
# start.sh — Linux / macOS launcher

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Log tudo para arquivo + terminal
LOG="$DIR/start.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== $(date) ==="
echo "DIR: $DIR"
echo "PATH: $PATH"

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

echo "Python encontrado: ${PY:-NENHUM}"

if [ -z "$PY" ]; then
    echo "ERRO: Python 3 não encontrado."
    echo "Instale em: https://python.org"
    read -rp "Pressione Enter para fechar..."
    exit 1
fi

echo "Iniciando launcher.py..."
"$PY" "$DIR/launcher.py"
EXIT_CODE=$?

echo "launcher.py encerrou com código: $EXIT_CODE"

if [ $EXIT_CODE -ne 0 ]; then
    echo "ERRO — veja detalhes acima."
    read -rp "Pressione Enter para fechar..."
fi

exit $EXIT_CODE
