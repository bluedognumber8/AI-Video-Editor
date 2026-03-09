import sqlite3
import time
import sys
import importlib.util
import pkgutil

# --- ПАТЧ ДЛЯ PYTHON 3.12 - 3.14 ---
# Искусственно возвращаем удаленную функцию, чтобы cinemagoer не ломался
if not hasattr(pkgutil, 'find_loader'):
    pkgutil.find_loader = lambda name: importlib.util.find_spec(name)
# -----------------------------------

from imdb import Cinemagoer  # Теперь импорт пройдет успешно!

DB_NAME = 'movies_brain.sqlite'

def add_columns_if_not_exist(cursor):
    cursor.execute("PRAGMA table_info(movies)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'country' not in columns:
        cursor.execute("ALTER TABLE movies ADD COLUMN country TEXT")
    if 'kind' not in columns:
        cursor.execute("ALTER TABLE movies ADD COLUMN kind TEXT")

def main():
    print("🌍 Начинаем скачивание стран и типов (Movie/TV) с IMDb...")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    add_columns_if_not_exist(cursor)
    conn.commit()
    
    cursor.execute("SELECT imdb_id, title FROM movies WHERE country IS NULL")
    movies_to_update = cursor.fetchall()
    
    total = len(movies_to_update)
    if total == 0:
        print("✅ У всех фильмов уже проставлены страны!")
        return
        
    print(f"🔍 Осталось обработать: {total} фильмов.\n")
    
    ia = Cinemagoer()
    processed = 0
    errors = 0

    for full_id, title in movies_to_update:
        clean_id = full_id.replace('tt', '')
        
        try:
            movie = ia.get_movie(clean_id, info=['main'])
            
            countries = movie.get('countries', [])
            country_str = ', '.join(countries) if countries else 'Unknown'
            kind = movie.get('kind', 'unknown')
            
            cursor.execute('''
                UPDATE movies 
                SET country = ?, kind = ? 
                WHERE imdb_id = ?
            ''', (country_str, kind, full_id))
            
            processed += 1
            print(f"[{processed}/{total}] {title} ➡️ 🌍 {country_str} | 🎬 {kind}")
            
            if processed % 10 == 0:
                conn.commit()
                
            time.sleep(0.3)
            
        except Exception as e:
            errors += 1
            print(f"❌ Ошибка для {title} ({full_id}): {e}")
            cursor.execute("UPDATE movies SET country = 'Error', kind = 'Error' WHERE imdb_id = ?", (full_id,))
            conn.commit()

    conn.commit()
    conn.close()
    
    print("\n🎉 ГОТОВО! Страны успешно добавлены в базу.")

if __name__ == '__main__':
    main()