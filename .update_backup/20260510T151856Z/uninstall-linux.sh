#!/usr/bin/env bash
# uninstall-linux.sh — Remove atalhos do Notion → Anki no Linux

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Notion → Anki — Desinstalador ==="

# ── Atalho do Desktop ─────────────────────────────────────────────────────────
DESKTOP_DIR=""
if command -v xdg-user-dir &>/dev/null; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null)"
fi
if [ -z "$DESKTOP_DIR" ] || [ ! -d "$DESKTOP_DIR" ]; then
    for candidate in "$HOME/Desktop" "$HOME/Área de Trabalho" "$HOME/Escritório"; do
        [ -d "$candidate" ] && DESKTOP_DIR="$candidate" && break
    done
fi

if [ -f "$DESKTOP_DIR/anke-notion.desktop" ]; then
    rm -f "$DESKTOP_DIR/anke-notion.desktop"
    echo "✓ Atalho do Desktop removido"
else
    echo "- Atalho do Desktop não encontrado"
fi

# ── Menu de aplicativos ───────────────────────────────────────────────────────
APP_FILE="$HOME/.local/share/applications/anke-notion.desktop"
if [ -f "$APP_FILE" ]; then
    rm -f "$APP_FILE"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo "✓ Removido do menu de aplicativos"
else
    echo "- Entrada no menu não encontrada"
fi

# ── Arquivo .desktop local do projeto ────────────────────────────────────────
if [ -f "$DIR/anke-notion.desktop" ]; then
    rm -f "$DIR/anke-notion.desktop"
    echo "✓ anke-notion.desktop do projeto removido"
fi

# ── Log de inicialização ──────────────────────────────────────────────────────
if [ -f "$DIR/start.log" ]; then
    rm -f "$DIR/start.log"
    echo "✓ start.log removido"
fi

echo ""
echo "=== Pronto! Atalhos removidos. ==="
echo "A pasta do projeto permanece intacta em: $DIR"
