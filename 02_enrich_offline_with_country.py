import sqlite3
import gzip
import os

DB_NAME = 'movies_master.sqlite'
BASICS_FILE = 'title.basics.tsv.gz'
RATINGS_FILE = 'title.ratings.tsv.gz'
AKAS_FILE = 'title.akas.tsv.gz' # <--- НОВЫЙ ФАЙЛ СО СТРАНАМИ

def main():
    print("🚀 ЭТАП 2: Оффлайн-обогащение ФИЛЬМОВ (с добавлением Стран)...")
    
    if not all(os.path.exists(f) for f in [BASICS_FILE, RATINGS_FILE, AKAS_FILE]):
        print(f"🛑 ОШИБКА: Убедитесь, что у вас лежат 3 файла: basics, ratings и akas (.tsv.gz)!")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT imdb_id FROM movies")
    our_ids = {row[0] for row in cursor.fetchall()}
    
    if not our_ids:
        print("База пуста! Сначала запустите 01_extract_smart.py")
        return
        
    print(f"🔍 Нам нужно найти метаданные для {len(our_ids)} фильмов.")
    
    movies_data = {}
    
    # 1. Читаем названия, года и жанры
    print("📖 1/3 Читаем базовые данные...")
    with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
        next(f) 
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 9: continue
            
            tconst, title, year_str, genres = parts[0], parts[2], parts[5], parts[8]
            
            if tconst in our_ids:
                movies_data[tconst] = {
                    'title': title,
                    'year': int(year_str) if year_str.isdigit() else 0,
                    'genres': genres if genres != '\\N' else 'Unknown',
                    'rating': 0.0,
                    'country': 'Unknown' # По умолчанию страна неизвестна
                }

    # 2. Читаем рейтинги
    print("⭐️ 2/3 Прикрепляем рейтинги...")
    with gzip.open(RATINGS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 3: continue
            
            tconst, rating_str = parts[0], parts[1]
            if tconst in movies_data:
                movies_data[tconst]['rating'] = float(rating_str) if rating_str else 0.0

    # 3. Читаем СТРАНЫ из файла AKAS
    print("🌍 3/3 Вычисляем страны происхождения (это займет около 20-30 секунд)...")
    with gzip.open(AKAS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 8: continue
            
            tconst = parts[0]
            region = parts[3]
            is_original = parts[7] # Флаг: 1 если это оригинальное название (и страна)
            
            if tconst in movies_data:
                # Если это оригинальное название фильма, то его регион = страна производства!
                if is_original == '1' and region != '\\N':
                    movies_data[tconst]['country'] = region

    # 4. Сохраняем в БД
    print("💾 Записываем всё в базу...")
    update_data = []
    
    for imdb_id, data in movies_data.items():
        update_data.append((
            data['title'],      
            data['title'],      
            data['year'], 
            data['genres'], 
            data['rating'],
            data['country'],    # ЗАПИСЫВАЕМ СТРАНУ (Например: RU, SU, US, FR)
            imdb_id
        ))
        
    cursor.executemany('''
        UPDATE movies 
        SET title_ru=?, title_original=?, year=?, genres=?, rating=?, countries=?
        WHERE imdb_id=?
    ''', update_data)
    
    conn.commit()
    conn.close()
    print("\n🎉 ГОТОВО! Метаданные (включая СТРАНЫ) мгновенно добавлены в мастер-базу!")

if __name__ == '__main__':
    main()