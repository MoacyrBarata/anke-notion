#!/usr/bin/env bash
# install-linux.sh — Cria atalho de duplo clique no Desktop do Linux
# Execute uma única vez: bash install-linux.sh
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Notion → Anki — Instalador de Atalho Linux ==="

# ── 1. Tornar start.sh executável ────────────────────────────────────────────
chmod +x "$DIR/start.sh"
echo "✓ start.sh marcado como executável"

# ── 2. Gerar arquivo .desktop com caminhos absolutos ─────────────────────────
DESKTOP_FILE="$DIR/anke-notion.desktop"

ICON_PATH="$DIR/icon.png"
[ ! -f "$ICON_PATH" ] && ICON_PATH="$DIR/icon.svg"
[ ! -f "$ICON_PATH" ] && ICON_PATH="utilities"   # fallback ícone do sistema

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Notion → Anki
GenericName=Sincronizador Notion-Anki
Comment=Sincroniza flashcards do Notion para o Anki com IA
Exec=bash "$DIR/start.sh"
Icon=$ICON_PATH
Terminal=false
StartupNotify=true
StartupWMClass=anke-notion
Categories=Utility;Education;
Keywords=notion;anki;flashcard;sync;
EOF

chmod +x "$DESKTOP_FILE"
echo "✓ Arquivo .desktop gerado"

# ── 3. Copiar para o Desktop ──────────────────────────────────────────────────
DESKTOP_DIR=""

# Tentar xdg-user-dir primeiro (mais confiável)
if command -v xdg-user-dir &>/dev/null; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null)"
fi

# Fallback para caminhos comuns
if [ -z "$DESKTOP_DIR" ] || [ ! -d "$DESKTOP_DIR" ]; then
    for candidate in "$HOME/Desktop" "$HOME/Área de Trabalho" "$HOME/Escritório"; do
        if [ -d "$candidate" ]; then
            DESKTOP_DIR="$candidate"
            break
        fi
    done
fi

if [ -n "$DESKTOP_DIR" ] && [ -d "$DESKTOP_DIR" ]; then
    SHORTCUT="$DESKTOP_DIR/anke-notion.desktop"
    cp "$DESKTOP_FILE" "$SHORTCUT"
    chmod +x "$SHORTCUT"

    # Marcar como confiável no GNOME (evita aviso "arquivo de texto não confiável")
    if command -v gio &>/dev/null; then
        gio set "$SHORTCUT" metadata::trusted true 2>/dev/null || true
    fi

    echo "✓ Atalho criado: $SHORTCUT"
else
    echo "⚠  Desktop não encontrado. Copie manualmente:"
    echo "   cp '$DESKTOP_FILE' ~/Desktop/"
    echo "   chmod +x ~/Desktop/anke-notion.desktop"
fi

# ── 4. Instalar no menu de aplicativos ───────────────────────────────────────
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"
cp "$DESKTOP_FILE" "$APP_DIR/anke-notion.desktop"

# Atualizar cache do menu (silencioso)
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

echo "✓ Adicionado ao menu de aplicativos"

echo ""
echo "=== Pronto! ==="
echo "Dê duplo clique no ícone do Desktop para iniciar."
echo "Ou busque 'Notion Anki' no menu de aplicativos."
