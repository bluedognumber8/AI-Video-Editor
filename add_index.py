import sqlite3

DB_NAME = 'movies_master.sqlite'

def main():
    print("⚙️ Создаю индекс для колонки imdb_id (Ускоряет UI в 100 раз)...")
    print("⏳ Пожалуйста, подождите. Для базы в 24 млн строк это может занять 1-3 минуты.")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Создаем индекс. Если он уже есть, база просто проигнорирует команду.
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subtitles_imdb ON subtitles(imdb_id);")
    
    conn.commit()
    conn.close()
    
    print("✅ ГОТОВО! Теперь интерфейс будет летать.")

if __name__ == '__main__':
    main()