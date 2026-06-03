#!/bin/bash
# AI Video Editor — macOS double-click launcher
# Put this file anywhere, double-click in Finder to start

# Get the directory where this script lives (works from any location)
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "🎬 AI Video Editor"
echo "=================="
echo ""

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
