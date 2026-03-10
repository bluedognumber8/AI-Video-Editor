import sqlite3
import gzip
import os
import re
from tqdm import tqdm

DB_NAME = 'movies_master.sqlite'
BASICS_FILE = 'title.basics.tsv.gz'
RATINGS_FILE = 'title.ratings.tsv.gz'
AKAS_FILE = 'title.akas.tsv.gz'

def has_cyrillic(text):
    return bool(re.search(r'[А-Яа-яЁё]', text))

def main():
    print("🎬 СТАРТ: Оффлайн-обогащение (Названия, Года, Рейтинги)...")
    if not all(os.path.exists(f) for f in [BASICS_FILE, RATINGS_FILE, AKAS_FILE]):
        print("🛑 ОШИБКА: Нужны 3 файла: basics, ratings, akas (.tsv.gz)!")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT imdb_id FROM movies")
    our_ids = {row[0] for row in cursor.fetchall()}
    if not our_ids: return print("База пуста!")

    # Хранилище: imdb_id -> {данные}
    movies = {i: {'ru': '', 'orig': '', 'score': 0, 'year': 0, 'genres': '', 'rating': 0.0} for i in our_ids}

    # 1. BASICS (Год, Жанры, Базовое английское название)
    print("📖 1/3 Читаем title.basics...")
    with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 9: continue
            tconst = parts[0]
            if tconst in movies:
                movies[tconst]['orig'] = parts[3] # originalTitle
                movies[tconst]['year'] = int(parts[5]) if parts[5].isdigit() else 0
                movies[tconst]['genres'] = parts[8] if parts[8] != '\\N' else 'Unknown'

    # 2. RATINGS (Оценки)
    print("⭐️ 2/3 Прикрепляем рейтинги...")
    with gzip.open(RATINGS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 3: continue
            if parts[0] in movies:
                movies[parts[0]]['rating'] = float(parts[1]) if parts[1] else 0.0

    # 3. AKAS (Охота за кириллицей!)
    print("🇷🇺 3/3 Охота за локализованными названиями...")
    with gzip.open(AKAS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 8: continue
            
            tconst, title, region, lang = parts[0], parts[2], parts[3], parts[4]
            if tconst not in movies: continue

            is_cyr = has_cyrillic(title)
            
            if lang == 'ru' and is_cyr:
                if movies[tconst]['score'] < 3:
                    movies[tconst]['ru'] = title
                    movies[tconst]['score'] = 3
            elif region in ('RU', 'SU', 'SUHH') and is_cyr:
                if movies[tconst]['score'] < 2:
                    movies[tconst]['ru'] = title
                    movies[tconst]['score'] = 2
            elif is_cyr:
                if movies[tconst]['score'] < 1:
                    movies[tconst]['ru'] = title
                    movies[tconst]['score'] = 1

    # Если кириллического названия так и не нашли, ставим оригинальное
    for i, data in movies.items():
        if data['score'] == 0:
            data['ru'] = data['orig']

    print("💾 Сохраняем в базу...")
    update_data = [(d['ru'], d['orig'], d['year'], d['genres'], d['rating'], i) for i, d in movies.items()]
    
    cursor.executemany('''
        UPDATE movies 
        SET title_ru=?, title_original=?, year=?, genres=?, rating=? 
        WHERE imdb_id=?
    ''', update_data)
    
    conn.commit(); conn.close()
    print("✅ ГОТОВО! Ваша база полностью готова к работе и векторному поиску!")

if __name__ == '__main__':
    main()