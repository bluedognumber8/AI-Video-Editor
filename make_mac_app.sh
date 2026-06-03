#!/bin/bash
# 🎬 AI Video Editor — macOS .app Builder
# Run this ON your Mac to create a polished double-clickable .app
#
# Usage:  bash make_mac_app.sh
#         Creates "AI Video Editor.app" in the current directory

set -e

APP_NAME="AI Video Editor"
APP_PATH="./${APP_NAME}.app"
ICON_URL="https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f3ac.png"
ICON_PNG="/tmp/aive_icon.png"
ICON_ICNS="/tmp/aive_icon.icns"

echo "🎬 AI Video Editor — macOS .app Builder"
echo "========================================"
echo ""

# ── Step 1: Create icon ──
echo "📸 Создаю иконку..."
if [ ! -f "$ICON_ICNS" ]; then
    curl -sL "$ICON_URL" -o "$ICON_PNG"
    echo "   Скачал иконку 🎬"

    # Create iconset directory (macOS standard format)
    ICONSET="/tmp/aive_icon.iconset"
    mkdir -p "$ICONSET"

    # Generate all required sizes using sips (built-in macOS)
    for size in 16 32 64 128 256 512; do
        sips -z $size $size "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}.png" &>/dev/null
        sips -z $((size*2)) $((size*2)) "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}@2x.png" &>/dev/null
    done

    # Convert iconset to .icns using iconutil (built-in macOS)
    iconutil -c icns "$ICONSET" -o "$ICON_ICNS"
    rm -rf "$ICONSET"
    echo "   Иконка готова ✓"
else
    echo "   Иконка уже есть, пропускаю"
fi

# ── Step 2: Check for Platypus ──
HAS_PLATYPUS=false
if command -v platypus &>/dev/null; then
    HAS_PLATYPUS=true
    echo "✅ Platypus найден"
else
    echo ""
    echo "⚠️  Platypus не найден."
    echo "   Platypus создаёт красивые .app с собственным окном терминала."
    echo ""
    read -p "   Установить Platypus через Homebrew? (Y/n): " yn
    yn=${yn:-Y}
    if [[ "$yn" =~ ^[YyДд] ]]; then
        echo "   Устанавливаю Platypus..."
        brew install platypus
        HAS_PLATYPUS=true
        echo "   Platypus установлен ✓"
    else
        echo "   Пропускаю Platypus, создаю простой .app"
    fi
fi

# ── Step 3: Build .app ──
echo ""
echo "🏗  Собираю ${APP_NAME}.app..."

# Remove old app if exists
rm -rf "$APP_PATH"

if [ "$HAS_PLATYPUS" = true ]; then
    # ── Platypus method (polished) ──
    # Create a temporary launcher that cd's to the project dir
    LAUNCHER="/tmp/aive_launcher.sh"
    cat > "$LAUNCHER" <<'LAUNCHER_EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$DIR" || cd ~

if [ ! -f "venv/bin/activate" ]; then
    python3 install.py
fi

source venv/bin/activate
python3 -m streamlit run app.py --server.headless=true
LAUNCHER_EOF
    chmod +x "$LAUNCHER"

    platypus \
        --name "$APP_NAME" \
        --app-icon "$ICON_ICNS" \
        --interface-type "None" \
        --interpreter "/bin/bash" \
        --script-args "" \
        --droppable=false \
        --overwrite \
        "$LAUNCHER" \
        "$APP_PATH" 2>/dev/null

    echo "   Готово через Platypus ✓"
else
    # ── Manual .app bundle ──
    mkdir -p "$APP_PATH/Contents/MacOS"
    mkdir -p "$APP_PATH/Contents/Resources"

    # Copy icon
    cp "$ICON_ICNS" "$APP_PATH/Contents/Resources/app.icns"

    # Create launcher script inside the .app bundle
    cat > "$APP_PATH/Contents/MacOS/AppLauncher" <<'LAUNCHER_EOF'
#!/bin/bash
# AI Video Editor — App Bundle Launcher
DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$DIR"

if [ ! -f "venv/bin/activate" ]; then
    echo "📦 Первый запуск — устанавливаю зависимости..."
    python3 install.py
fi

source venv/bin/activate
echo "🚀 AI Video Editor запускается..."
echo "   Чтобы остановить — нажми Ctrl+C"
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
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
</dict>
</plist>
PLIST_EOF

    echo "   Готово (ручная сборка) ✓"
fi

# ── Step 4: Done ──
echo ""
echo "✅ ${APP_NAME}.app создан!"
echo ""
echo "   Просто перетащите его в папку Applications:"
echo "     mv \"${APP_PATH}\" /Applications/"
echo ""
echo "   Или запустите двойным кликом прямо здесь."
echo ""
echo "   Чтобы изменить иконку:"
echo "     1. Нажмите Cmd+I на .app в Finder"
echo "     2. Перетащите /tmp/aive_icon.png на иконку в окне Info"
echo ""
