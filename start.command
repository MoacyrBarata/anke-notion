#!/usr/bin/env bash
# start.command — macOS Finder-clickable launcher
# Double-click no Finder para abrir o app no Terminal

cd "$(dirname "$0")"

if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null && python --version 2>&1 | grep -q "3\."; then
    PY=python
else
    osascript -e 'display dialog "Python 3 não encontrado.\n\nInstale em: https://python.org" buttons {"OK"} default button "OK" with icon stop with title "Notion → Anki"'
    exit 1
fi

$PY launcher.py
