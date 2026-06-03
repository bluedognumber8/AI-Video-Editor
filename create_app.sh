#!/bin/bash
# 🎬 AI Video Editor — macOS .app Builder (Terminal delegate)
# Run this ON your Mac to create a double-clickable .app
#
# Unlike make_mac_app.sh, this .app delegates to AI-Video-Editor.command
# via Terminal.app, ensuring a full login shell environment so numpy
# and other C extensions can import correctly.
#
# Usage:  cd /path/to/AI-Video-Editor && bash create_app.sh
#         Creates "AI Video Editor.app" right in the project folder

APP_NAME="AI Video Editor"
APP_PATH="${PWD}/${APP_NAME}.app"
ICON_PNG="/tmp/aive_icon.png"
ICON_ICNS="/tmp/aive_icon.icns"

# ANSI colors
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

echo "🎬 AI Video Editor — macOS .app Builder (Terminal delegate)"
echo "============================================================"
echo ""

# ── Check we are in the project folder ──
if [ ! -f "app.py" ] || [ ! -f "AI-Video-Editor.command" ]; then
    echo -e "${RED}❌ Ошибка: скрипт нужно запускать из папки проекта (где лежат app.py и AI-Video-Editor.command)${NC}"
    echo "   Сделайте: cd /путь/к/AI-Video-Editor && bash create_app.sh"
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

# ── Step 3: Build .app (manual bundle) ──
echo ""
echo "🏗  Собираю ${APP_NAME}.app..."

mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

# Copy icon if we have one
if [ "$HAS_ICON" -eq 0 ] && [ -f "$ICON_ICNS" ]; then
    cp "$ICON_ICNS" "$APP_PATH/Contents/Resources/app.icns"
fi

# Create launcher script — delegates to .command via Terminal
# This ensures a full login shell environment, fixing numpy import errors
cat > "$APP_PATH/Contents/MacOS/AppLauncher" <<LAUNCHER_EOF
#!/bin/bash
# AI Video Editor — App Bundle Launcher (Terminal delegate)
# Opens AI-Video-Editor.command in Terminal.app for full login shell env

DIR="${PWD}"
COMMAND="\${DIR}/AI-Video-Editor.command"

if [ ! -f "\$COMMAND" ]; then
    # Fallback: try to find project by traversing up from .app location
    DIR="\$(cd "\$(dirname "\$0")/../../.." && pwd 2>/dev/null)"
    COMMAND="\${DIR}/AI-Video-Editor.command"
fi

if [ ! -f "\$COMMAND" ]; then
    osascript -e 'display dialog "Не могу найти AI-Video-Editor.command рядом с .app bundle" buttons {"OK"} default button 1 with icon stop'
    exit 1
fi

open -a Terminal "\$COMMAND"
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

# ── Step 5: Done ──
echo ""
echo "   ✨ Двойной клик на '${APP_NAME}.app' в Finder —"
echo "      откроет Terminal с AI-Video-Editor.command"
echo ""
echo "   Если переместите .app в другое место — он всё равно найдёт"
echo "   .command файл (вшит абсолютный путь: ${PWD})"
echo ""
