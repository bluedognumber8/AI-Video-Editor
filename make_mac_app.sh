#!/bin/bash
# 🎬 AI Video Editor — macOS .app Builder
# Run this ON your Mac to create a polished double-clickable .app
#
# Usage:  cd /path/to/AI-Video-Editor && bash make_mac_app.sh
#         Creates "AI Video Editor.app" right in the project folder

APP_NAME="AI Video Editor"
APP_PATH="${PWD}/${APP_NAME}.app"
ICON_PNG="/tmp/aive_icon.png"
ICON_ICNS="/tmp/aive_icon.icns"

# ANSI colors
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

echo "🎬 AI Video Editor — macOS .app Builder"
echo "========================================"
echo ""

# ── Check we are in the project folder ──
if [ ! -f "app.py" ] || [ ! -f "install.py" ]; then
    echo -e "${RED}❌ Ошибка: скрипт нужно запускать из папки проекта (где лежат app.py и install.py)${NC}"
    echo "   Сделайте: cd /путь/к/AI-Video-Editor && bash make_mac_app.sh"
    exit 1
fi
echo -e "${GREEN}✅ Папка проекта: ${PWD}${NC}"

# ── Step 1: Create icon ──
create_icon() {
    echo ""
    echo "📸 Создаю иконку..."
    if [ -f "$ICON_ICNS" ]; then
        echo "   Иконка уже есть в /tmp, использую её"
        return 0
    fi

    # Download 🎬 emoji PNG from Twitter twemoji (MIT license)
    if ! curl -sL --max-time 10 \
        "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f3ac.png" \
        -o "$ICON_PNG"; then
        echo -e "   ${YELLOW}⚠️  Не удалось скачать иконку (нет интернета?) — .app будет без иконки${NC}"
        return 1
    fi
    echo "   Скачал 🎬"

    # Create iconset
    ICONSET="/tmp/aive_icon.iconset"
    mkdir -p "$ICONSET" 2>/dev/null

    # Generate sizes with sips (built-in macOS)
    for size in 16 32 128 256 512; do
        sips -z $size $size "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}.png" &>/dev/null
        sips -z $((size*2)) $((size*2)) "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}@2x.png" &>/dev/null
    done
    # Also generate 64x64 (for good measure)
    sips -z 64 64 "$ICON_PNG" --out "$ICONSET/icon_64x64.png" &>/dev/null

    # Convert to .icns (built-in macOS)
    if ! iconutil -c icns "$ICONSET" -o "$ICON_ICNS" 2>/dev/null; then
        echo -e "   ${YELLOW}⚠️  Не удалось создать .icns — .app будет без иконки${NC}"
        rm -rf "$ICONSET"
        return 1
    fi
    rm -rf "$ICONSET"
    echo -e "   ${GREEN}✓ Иконка готова${NC}"
    return 0
}

create_icon
HAS_ICON=$?

# ── Step 2: Remove old .app ──
if [ -d "$APP_PATH" ]; then
    echo ""
    echo "🗑  Удаляю старый ${APP_NAME}.app..."
    rm -rf "$APP_PATH"
fi

# ── Step 3: Build .app (manual bundle — always works, no deps) ──
echo ""
echo "🏗  Собираю ${APP_NAME}.app..."

# Create bundle structure
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

# Copy icon if we have one
if [ "$HAS_ICON" -eq 0 ] && [ -f "$ICON_ICNS" ]; then
    cp "$ICON_ICNS" "$APP_PATH/Contents/Resources/app.icns"
fi

# Create launcher script — uses ABSOLUTE path to project
# This way the .app works even if moved to /Applications/
cat > "$APP_PATH/Contents/MacOS/AppLauncher" <<LAUNCHER_EOF
#!/bin/bash
# AI Video Editor — App Bundle Launcher
# Auto-detects project by searching parent dirs

# First try: same directory as the .app bundle
DIR="\$(cd "\$(dirname "\$0")/../../.." && pwd 2>/dev/null)"

# If project files aren't there, try hardcoded path from build time
if [ ! -f "\$DIR/app.py" ]; then
    DIR="${PWD}"
fi

# Safety fallback — ask user
if [ ! -f "\$DIR/app.py" ]; then
    echo "❌ Не могу найти папку проекта с app.py"
    echo "   Ожидаемая папка: \$DIR"
    echo ""
    read -p "   Укажите полный путь к папке проекта: " DIR
fi

cd "\$DIR"

# Run installer if venv missing
if [ ! -f "venv/bin/activate" ]; then
    echo "📦 Первый запуск — устанавливаю зависимости..."
    python3 install.py
    echo ""
fi

source venv/bin/activate
echo "🚀 AI Video Editor запускается..."
echo "   Папка: \$DIR"
echo "   Чтобы остановить — нажми Ctrl+C в этом окне"
echo ""
python3 -m streamlit run app.py
echo ""
echo "❌ Сервер остановлен."
read -p "Нажми Enter чтобы закрыть окно..."
LAUNCHER_EOF
chmod +x "$APP_PATH/Contents/MacOS/AppLauncher"

# Create Info.plist
cat > "$APP_PATH/Contents/Info.plist" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>AppLauncher</string>
    <key>CFBundleIdentifier</key>
    <string>com.aivideoeditor.app</string>
    <key>CFBundleName</key>
    <string>AI Video Editor</string>
    <key>CFBundleDisplayName</key>
    <string>AI Video Editor</string>
    <key>CFBundleIconFile</key>
    <string>app</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST_EOF

# ── Step 4: Verify ──
if [ -d "$APP_PATH" ] && [ -f "$APP_PATH/Contents/MacOS/AppLauncher" ]; then
    echo -e "${GREEN}✅ ${APP_NAME}.app создан!${NC}"
else
    echo -e "${RED}❌ Ошибка: .app не создался${NC}"
    exit 1
fi

# ── Step 5: Suggest moving to Applications ──
echo ""
echo "   ✨ Теперь можно:"
echo "     1. Перетащить '${APP_NAME}.app' в /Applications/:"
echo "        mv \"${APP_PATH}\" /Applications/"
echo ""
echo "     2. Или запустить двойным кликом прямо здесь (Finder → Open)"
echo ""
echo "   После первого запуска .app сам найдёт папку проекта."
echo "   Если переместите .app в другое место — он всё равно найдёт проект"
echo "   (вшит абсолютный путь: ${PWD})"
echo ""
echo "   Чтобы изменить иконку вручную:"
echo "     Cmd+I на .app → перетащить /tmp/aive_icon.png на иконку"
echo ""
