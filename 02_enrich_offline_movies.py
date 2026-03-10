import sqlite3
import gzip
import os

DB_NAME = 'movies_master.sqlite'
BASICS_FILE = 'title.basics.tsv.gz'
RATINGS_FILE = 'title.ratings.tsv.gz'

def main():
    print("🚀 ЭТАП 2: Оффлайн-обогащение ФИЛЬМОВ из дампов IMDb...")
    
    if not os.path.exists(BASICS_FILE) or not os.path.exists(RATINGS_FILE):
        print(f"🛑 ОШИБКА: Скачайте и положите в папку {BASICS_FILE} и {RATINGS_FILE}!")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Получаем ID фильмов
    cursor.execute("SELECT imdb_id FROM movies")
    our_ids = {row[0] for row in cursor.fetchall()}
    
    if not our_ids:
        print("База пуста! Сначала запустите 01_extract_smart.py")
        return
        
    print(f"🔍 Нам нужно найти метаданные для {len(our_ids)} фильмов.")
    
    # Словарь для хранения данных: {'tt0109942': {'title': '...', 'year': 1994, ...}}
    movies_data = {}
    
    # 1. Читаем названия, года и жанры
    print("📖 Читаем названия и жанры (это займет секунд 15)...")
    with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
        next(f) # пропуск заголовка
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 9: continue
            
            tconst, title, year_str, genres = parts[0], parts[2], parts[5], parts[8]
            
            if tconst in our_ids:
                movies_data[tconst] = {
                    'title': title,
                    'year': int(year_str) if year_str.isdigit() else 0,
                    'genres': genres if genres != '\\N' else 'Unknown',
                    'rating': 0.0
                }

    # 2. Читаем рейтинги
    print("⭐️ Прикрепляем рейтинги...")
    with gzip.open(RATINGS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 3: continue
            
            tconst, rating_str = parts[0], parts[1]
            if tconst in movies_data:
                movies_data[tconst]['rating'] = float(rating_str) if rating_str else 0.0

    # 3. Сохраняем в БД
    print("💾 Записываем всё в базу...")
    update_data = []
    
    for imdb_id, data in movies_data.items():
        update_data.append((
            data['title'],      # title_ru (Английское название из дампа)
            data['title'],      # title_original
            data['year'], 
            data['genres'], 
            data['rating'], 
            imdb_id
        ))
        
    cursor.executemany('''
        UPDATE movies 
        SET title_ru=?, title_original=?, year=?, genres=?, rating=?
        WHERE imdb_id=?
    ''', update_data)
    
    conn.commit()
    conn.close()
    print("\n🎉 ГОТОВО! Метаданные мгновенно добавлены в мастер-базу!")

if __name__ == '__main__':
    main()