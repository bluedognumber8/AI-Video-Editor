import os
import requests
from magnet_get import TorrServerEngine

# 1. Скачиваем 100% рабочий торрент (Sintel, ~129 MB, огромное количество сидов)
TORRENT_URL = "https://webtorrent.io/torrents/sintel.torrent"
TEST_TORRENT_FILE = "sintel_test.torrent"
OUTPUT_CLIP = "sintel_test_clip.mp4"

print("1️⃣ Скачивание тестового торрент-файла (Sintel)...")
r = requests.get(TORRENT_URL)
with open(TEST_TORRENT_FILE, "wb") as f:
    f.write(r.content)

# 2. Инициализируем наш движок (используя системный бинарник из AUR)
print("2️⃣ Инициализация TorrServerEngine...")
engine = TorrServerEngine(binary_path="torrserver")

try:
    # Запускаем локальный сервер с собственной временной БД
    engine.start()

    # 3. Добавляем торрент в сервер
    print("3️⃣ Загрузка торрента в сервер...")
    t_hash = engine.add_torrent(TEST_TORRENT_FILE)
    
    # 4. Получаем индекс видеофайла (target_episode=0, значит берем самый большой файл - фильм)
    print("4️⃣ Поиск пиров и метаданных...")
    file_idx = engine.find_file_index(t_hash, target_season=0, target_episode=0)
    
    # 5. Вырезаем крутой момент: с 2-й минуты 15 секунд, длительность 10 секунд
    print("5️⃣ Запуск FFmpeg (отправка HTTP Range Requests к TorrServer)...")
    success = engine.download_clip(
        torrent_hash=t_hash, 
        file_index=file_idx, 
        start_time="00:02:15", 
        duration_secs=10, 
        output_path=OUTPUT_CLIP
    )
    
    if success and os.path.exists(OUTPUT_CLIP):
        size_mb = os.path.getsize(OUTPUT_CLIP) / (1024 * 1024)
        print(f"\n🎉 ИДЕАЛЬНО! Клип успешно вырезан и сохранен: {OUTPUT_CLIP} ({size_mb:.2f} MB)")
        print("▶️ Откройте этот файл в плеере, чтобы проверить результат.")
    else:
        print("\n❌ ОШИБКА: FFmpeg не смог создать файл.")

finally:
    # 6. Убираем за собой мусор
    print("\n🧹 Остановка сервера и удаление временных файлов...")
    engine.stop()
    if os.path.exists(TEST_TORRENT_FILE):
        os.remove(TEST_TORRENT_FILE)