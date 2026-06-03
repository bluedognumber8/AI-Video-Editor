#!/bin/bash
# AI Video Editor — macOS double-click launcher
# Put this file anywhere, double-click in Finder to start

# Get the directory where this script lives (works from any location)
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "🎬 AI Video Editor"
echo "=================="
echo ""

# Auto-update: git pull if clean repo
if git -C "$DIR" rev-parse --git-dir &>/dev/null; then
    if git -C "$DIR" diff --quiet 2>/dev/null; then
        echo "📡 Проверка обновлений..."
        PULL_OUTPUT=$(git -C "$DIR" pull --ff-only 2>&1)
        if [ $? -eq 0 ] && [ "$PULL_OUTPUT" != "Already up to date." ]; then
            echo "   ✅ Обновлено: $(echo "$PULL_OUTPUT" | grep -c 'changed') файлов"
        else
            echo "   ✅ Актуальная версия"
        fi
        echo ""
    else
        echo "⚠️  Есть локальные изменения — git pull пропущен"
        echo ""
    fi
fi

# Check if venv exists, run installer if missing
if [ ! -f "venv/bin/activate" ]; then
    echo "📦 Первый запуск — устанавливаю зависимости..."
    echo ""
    python3 install.py
    echo ""
fi

# Activate venv and run Streamlit
source venv/bin/activate

echo "🚀 Запускаю сервер..."
echo "   Приложение откроется в браузере автоматически"
echo "   Чтобы остановить — нажми Ctrl+C в этом окне"
echo ""

python3 -m streamlit run app.py

echo ""
echo "❌ Сервер остановлен. Можете закрыть это окно."
echo ""
