import sqlite3
import gzip
import os

DB_NAME = 'movies_master.sqlite'
BASICS_FILE = 'title.basics.tsv.gz'

def main():
    print("🧹 Запускаем тотальную зачистку базы от Сериалов и короткометражек...")
    
    if not os.path.exists(BASICS_FILE):
        print(f"🛑 ОШИБКА: Файл {BASICS_FILE} не найден!")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Берем все ID, которые сейчас есть в базе
    cursor.execute("SELECT imdb_id FROM movies")
    our_ids = {row[0] for row in cursor.fetchall()}
    
    if not our_ids:
        print("База пуста!")
        return

    ids_to_delete = []

    print("📖 Сканируем официальные типы IMDb (это займет 10-15 секунд)...")
    
    with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
        next(f) # Пропуск заголовка
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 2: continue
            
            tconst = parts[0]
            title_type = parts[1] # movie, tvEpisode, short, videoGame...
            
            if tconst in our_ids:
                # Оставляем ТОЛЬКО полнометражные фильмы и телефильмы
                if title_type not in ('movie', 'tvMovie'):
                    ids_to_delete.append((tconst,))

    if not ids_to_delete:
        print("✅ В базе нет сериалов. Всё чисто!")
        return

    print(f"🗑 Найдено не-фильмов (сериалы, игры, шортсы): {len(ids_to_delete)}. Удаляю...")

    # 1. Удаляем из таблицы фильмов
    cursor.executemany("DELETE FROM movies WHERE imdb_id = ?", ids_to_delete)
    
    # 2. Удаляем миллионы строк из таблицы субтитров
    cursor.executemany("DELETE FROM subtitles WHERE imdb_id = ?", ids_to_delete)
    
    print("⚙️ Перестраиваю поисковый индекс (FTS5), чтобы стереть фразы из памяти (может занять минуту)...")
    # Магическая команда SQLite, которая вычищает удаленный текст из умного поиска
    cursor.execute("INSERT INTO subtitles_fts(subtitles_fts) VALUES('rebuild');")
    
    # Опционально: сжимаем базу (освобождаем место на диске после удаления)
    print("🗜 Сжимаю базу данных для экономии места...")
    cursor.execute("VACUUM;")

    conn.commit()
    conn.close()
    
    print("\n🎉 ГОТОВО! Сериалы полностью уничтожены. В базе остались ТОЛЬКО фильмы.")

if __name__ == '__main__':
    main()