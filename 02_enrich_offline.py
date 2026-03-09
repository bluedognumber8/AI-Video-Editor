import sqlite3
import gzip
import os

DB_NAME = 'movies_master.sqlite'
BASICS_FILE = 'title.basics.tsv.gz'
RATINGS_FILE = 'title.ratings.tsv.gz'
EPISODE_FILE = 'title.episode.tsv.gz'

def main():
    print("🚀 ЭТАП 2: Оффлайн-обогащение базы из дампов IMDb...")
    
    if not all(os.path.exists(f) for f in [BASICS_FILE, RATINGS_FILE, EPISODE_FILE]):
        print(f"🛑 ОШИБКА: Скачайте и положите в папку 3 файла .tsv.gz!")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Получаем ID, которые нам нужны
    cursor.execute("SELECT imdb_id FROM movies")
    our_ids = {row[0] for row in cursor.fetchall()}
    
    if not our_ids:
        print("База пуста! Сначала запустите 01_build_core.py")
        return
        
    print(f"🔍 Нам нужно найти метаданные для {len(our_ids)} фильмов/серий.")
    
    # Словари для хранения данных в памяти
    movies_data = {}
    parent_shows_ids = set() # Сюда соберем ID сериалов
    
    # 2. Ищем сезоны и серии в title.episode.tsv.gz
    print("📺 Обрабатываем эпизоды сериалов...")
    with gzip.open(EPISODE_FILE, 'rt', encoding='utf-8') as f:
        next(f) # пропуск заголовка
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 4: continue
            
            ep_id, parent_id, season, episode = parts[0], parts[1], parts[2], parts[3]
            
            if ep_id in our_ids:
                movies_data[ep_id] = {
                    'type': 'tv',
                    'parent_id': parent_id,
                    'season': int(season) if season.isdigit() else 0,
                    'episode': int(episode) if episode.isdigit() else 0,
                    'title': '', 'year': 0, 'genres': '', 'rating': 0.0
                }
                parent_shows_ids.add(parent_id)

    # Добавляем в общий список поиска ID родительских сериалов
    all_needed_ids = our_ids.union(parent_shows_ids)
    
    # 3. Читаем названия, года и жанры
    print("📖 Читаем названия и жанры (это займет секунд 15)...")
    parent_titles = {} # Сохраняем названия сериалов отдельно
    
    with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 9: continue
            
            tconst, title, year_str, genres = parts[0], parts[2], parts[5], parts[8]
            
            if tconst in all_needed_ids:
                if tconst in parent_shows_ids:
                    parent_titles[tconst] = title
                
                if tconst in our_ids:
                    if tconst not in movies_data:
                        movies_data[tconst] = {
                            'type': 'movie', 'season': 0, 'episode': 0,
                            'title': title, 'rating': 0.0
                        }
                    else:
                        # Если это эпизод, пока запишем его собственное название (потом заменим на сериал)
                        movies_data[tconst]['title'] = title
                        
                    movies_data[tconst]['year'] = int(year_str) if year_str.isdigit() else 0
                    movies_data[tconst]['genres'] = genres if genres != '\\N' else 'Unknown'

    # 4. Читаем рейтинги
    print("⭐️ Прикрепляем рейтинги...")
    with gzip.open(RATINGS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 3: continue
            
            tconst, rating_str = parts[0], parts[1]
            if tconst in movies_data:
                movies_data[tconst]['rating'] = float(rating_str) if rating_str else 0.0

    # 5. Сохраняем в БД
    print("💾 Записываем всё в базу...")
    update_data = []
    
    for imdb_id, data in movies_data.items():
        # Если это сериал, заменяем название эпизода на название сериала
        if data['type'] == 'tv' and 'parent_id' in data:
            final_title = parent_titles.get(data['parent_id'], data['title'])
        else:
            final_title = data['title']
            
        update_data.append((
            data['type'], 
            final_title,      # Записываем в title_ru (чтобы поиск не ломался)
            final_title,      # Записываем в title_original
            data['year'], 
            data['genres'], 
            data['rating'], 
            data['season'], 
            data['episode'],
            imdb_id
        ))
        
    cursor.executemany('''
        UPDATE movies 
        SET type=?, title_ru=?, title_original=?, year=?, 
            genres=?, rating=?, season=?, episode=?
        WHERE imdb_id=?
    ''', update_data)
    
    conn.commit()
    conn.close()
    print("\n🎉 ГОТОВО! Метаданные мгновенно добавлены в мастер-базу!")

if __name__ == '__main__':
    main()