import sqlite3
import os

# --- НАСТРОЙКИ ---
SRC_DB = 'movies_master copy.sqlite' # Откуда берем (ваша копия)
DEST_DB = 'movies_master.sqlite'     # Куда вставляем (рабочая база)

def main():
    print(f"🔄 Старт: Копируем страны из '{SRC_DB}' в '{DEST_DB}'...")
    
    if not os.path.exists(SRC_DB):
        print(f"🛑 ОШИБКА: Файл {SRC_DB} не найден!")
        return
    if not os.path.exists(DEST_DB):
        print(f"🛑 ОШИБКА: Файл {DEST_DB} не найден!")
        return

    # 1. ЧИТАЕМ ИЗ КОПИИ
    src_conn = sqlite3.connect(SRC_DB)
    src_cursor = src_conn.cursor()
    
    # Берем только те строки, где страна реально существует
    src_cursor.execute('''
        SELECT imdb_id, countries 
        FROM movies 
        WHERE countries IS NOT NULL 
          AND countries != 'Unknown' 
          AND countries != ''
    ''')
    
    saved_data = src_cursor.fetchall()
    src_conn.close()

    if not saved_data:
        print("❌ В копии базы не найдено ни одной заполненной страны.")
        return

    print(f"✅ Найдено {len(saved_data)} фильмов с сохраненными странами. Переношу...")

    # 2. ПЕРЕНОСИМ В РАБОЧУЮ БАЗУ
    dest_conn = sqlite3.connect(DEST_DB)
    dest_cursor = dest_conn.cursor()

    # Меняем местами для SQL-запроса UPDATE (SET countries = ? WHERE imdb_id = ?)
    update_data = [(country, imdb_id) for imdb_id, country in saved_data]

    dest_cursor.executemany('''
        UPDATE movies 
        SET countries = ? 
        WHERE imdb_id = ?
    ''', update_data)

    dest_conn.commit()
    dest_conn.close()
    
    print("\n🎉 ГОТОВО! Ваш прогресс успешно восстановлен.")
    print("👉 Теперь вы можете запустить 02c_fetch_countries_tmdb.py, и он скачает только недостающие!")

if __name__ == '__main__':
    main()