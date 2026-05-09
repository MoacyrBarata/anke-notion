#!/usr/bin/env bash
# start.sh — Linux / Mac launcher
# Usage: bash start.sh   OR   ./start.sh (after chmod +x start.sh)

set -e
cd "$(dirname "$0")"

# Check Python 3
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null && python --version 2>&1 | grep -q "3\."; then
    PY=python
else
    echo ""
    echo " Python 3 não encontrado."
    echo " Instale em: https://python.org"
    echo ""
    exit 1
fi

echo " Python encontrado: $($PY --version)"
$PY launcher.py
