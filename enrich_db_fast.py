import sqlite3
import gzip
import os

DB_NAME = 'movies_brain.sqlite'
BASICS_FILE = 'title.basics.tsv.gz'
RATINGS_FILE = 'title.ratings.tsv.gz'

def init_movies_table(cursor):
    """Создаем таблицу для хранения данных о фильмах"""
    cursor.execute('DROP TABLE IF EXISTS movies') # Очищаем, если были старые тесты
    cursor.execute('''
    CREATE TABLE movies (
        imdb_id TEXT PRIMARY KEY,
        title TEXT,
        year INTEGER,
        genres TEXT,
        rating REAL
    )
    ''')

def main():
    if not os.path.exists(BASICS_FILE) or not os.path.exists(RATINGS_FILE):
        print("🛑 ОШИБКА: Скачайте title.basics.tsv.gz и title.ratings.tsv.gz!")
        return

    print("🚀 Подключаемся к базе...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    init_movies_table(cursor)
    
    # 1. Получаем список нужных нам ID из базы субтитров
    print("🔍 Собираем уникальные IMDb ID из базы субтитров...")
    cursor.execute('SELECT DISTINCT imdb_id FROM subtitles WHERE imdb_id IS NOT NULL')
    needed_ids = {row[0] for row in cursor.fetchall()}
    
    if not needed_ids:
        print("В базе субтитров нет IMDb ID! Сначала запустите build_database.py")
        return
        
    print(f"✅ Нам нужно найти информацию для {len(needed_ids)} фильмов.")
    
    # Словарь для хранения найденных данных в памяти перед записью
    # Формат: {'tt0109942': {'title': 'Dead Tired', 'year': 1994, 'genres': 'Comedy', 'rating': 0.0}}
    movies_data = {imdb_id: {'title': '', 'year': 0, 'genres': '', 'rating': 0.0} for imdb_id in needed_ids}

    # 2. Читаем title.basics.tsv.gz (Названия, Год, Жанры)
    print(f"📖 Читаем {BASICS_FILE} (это займет около 10-20 секунд)...")
    with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
        next(f) # Пропускаем заголовок
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 9:
                continue
                
            tconst = parts[0] # tt0109942
            
            if tconst in needed_ids:
                title = parts[2] # primaryTitle
                year_str = parts[5] # startYear
                genres = parts[8] # genres
                
                movies_data[tconst]['title'] = title
                movies_data[tconst]['genres'] = genres if genres != '\\N' else 'Unknown'
                movies_data[tconst]['year'] = int(year_str) if year_str.isdigit() else 0

    # 3. Читаем title.ratings.tsv.gz (Рейтинги)
    print(f"⭐️ Читаем {RATINGS_FILE}...")
    with gzip.open(RATINGS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 3:
                continue
                
            tconst = parts[0]
            
            if tconst in needed_ids:
                rating_str = parts[1]
                movies_data[tconst]['rating'] = float(rating_str) if rating_str else 0.0

    # 4. Сохраняем всё в нашу базу SQLite
    print("💾 Сохраняем данные в базу...")
    insert_data = []
    for imdb_id, data in movies_data.items():
        insert_data.append((
            imdb_id, 
            data['title'], 
            data['year'], 
            data['genres'], 
            data['rating']
        ))
        
    cursor.executemany('''
        INSERT INTO movies (imdb_id, title, year, genres, rating)
        VALUES (?, ?, ?, ?, ?)
    ''', insert_data)
    
    conn.commit()
    conn.close()
    
    print("🎉 ГОТОВО! Метаданные мгновенно добавлены в вашу базу.")

if __name__ == '__main__':
    main()