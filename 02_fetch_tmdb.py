import sqlite3
import requests
import time

# Ваш API Ключ
API_KEY = "TMDB_API_KEY_PLACEHOLDER"
DB_NAME = 'movies_master.sqlite'
BASE_URL = "https://api.themoviedb.org/3"

def update_movie_in_db(cursor, data):
    cursor.execute('''
        UPDATE movies 
        SET tmdb_id=?, type=?, title_ru=?, title_original=?, 
            year=?, genres=?, countries=?, rating=?, 
            season=?, episode=?, poster_url=?
        WHERE imdb_id=?
    ''', (
        data['tmdb_id'], data['type'], data['title_ru'], data['title_original'],
        data['year'], data['genres'], data['countries'], data['rating'],
        data['season'], data['episode'], data['poster_url'],
        data['imdb_id']
    ))

def fetch_tmdb_data(imdb_id):
    find_url = f"{BASE_URL}/find/{imdb_id}?api_key={API_KEY}&external_source=imdb_id&language=ru-RU"
    try:
        resp = requests.get(find_url, timeout=5)
        
        # Если сервер вернул ошибку (например, 401 Неверный ключ), скрипт выбросит исключение
        resp.raise_for_status() 
        
        resp_json = resp.json()
    except Exception as e:
        # ТЕПЕРЬ МЫ УВИДИМ ОШИБКУ!
        print(f"❌ Сбой при запросе к TMDB для {imdb_id}: {e}")
        return None

    # Подготавливаем пустой словарь
    result = {
        'imdb_id': imdb_id, 'tmdb_id': 0, 'type': 'unknown',
        'title_ru': 'Not Found', 'title_original': 'Not Found',
        'year': 0, 'genres': '', 'countries': '', 'rating': 0.0,
        'season': 0, 'episode': 0, 'poster_url': ''
    }

    # СЦЕНАРИЙ А: Это ФИЛЬМ
    if resp_json.get('movie_results'):
        item = resp_json['movie_results'][0]
        tmdb_id = item['id']
        result['type'] = 'movie'
        result['tmdb_id'] = tmdb_id
        
        try:
            det_url = f"{BASE_URL}/movie/{tmdb_id}?api_key={API_KEY}&language=ru-RU"
            details = requests.get(det_url, timeout=5).json()
            
            result['title_ru'] = details.get('title', '')
            result['title_original'] = details.get('original_title', '')
            result['year'] = int(details.get('release_date', '0')[:4]) if details.get('release_date') else 0
            result['genres'] = ', '.join([g['name'] for g in details.get('genres', [])])
            result['countries'] = ', '.join([c['iso_3166_1'] for c in details.get('production_countries', [])])
            result['rating'] = details.get('vote_average', 0.0)
            if details.get('poster_path'):
                result['poster_url'] = f"https://image.tmdb.org/t/p/w500{details['poster_path']}"
        except: pass

    # СЦЕНАРИЙ Б: Это ЭПИЗОД СЕРИАЛА
    elif resp_json.get('tv_episode_results'):
        item = resp_json['tv_episode_results'][0]
        show_id = item['show_id']
        
        result['type'] = 'tv'
        result['tmdb_id'] = item['id']
        result['season'] = item.get('season_number', 0)
        result['episode'] = item.get('episode_number', 0)
        
        try:
            show_url = f"{BASE_URL}/tv/{show_id}?api_key={API_KEY}&language=ru-RU"
            show_details = requests.get(show_url, timeout=5).json()
            
            result['title_ru'] = show_details.get('name', '')
            result['title_original'] = show_details.get('original_name', '')
            result['year'] = int(show_details.get('first_air_date', '0')[:4]) if show_details.get('first_air_date') else 0
            result['genres'] = ', '.join([g['name'] for g in show_details.get('genres', [])])
            result['countries'] = ', '.join([c['iso_3166_1'] for c in show_details.get('production_countries', [])])
            result['rating'] = item.get('vote_average', 0.0)
            if item.get('still_path'):
                result['poster_url'] = f"https://image.tmdb.org/t/p/w500{item['still_path']}"
            elif show_details.get('poster_path'):
                result['poster_url'] = f"https://image.tmdb.org/t/p/w500{show_details['poster_path']}"
        except: pass

    # СЦЕНАРИЙ В: Это СЕРИАЛ ЦЕЛИКОМ
    elif resp_json.get('tv_results'):
        item = resp_json['tv_results'][0]
        tmdb_id = item['id']
        result['type'] = 'tv'
        result['tmdb_id'] = tmdb_id
        
        try:
            det_url = f"{BASE_URL}/tv/{tmdb_id}?api_key={API_KEY}&language=ru-RU"
            details = requests.get(det_url, timeout=5).json()
            
            result['title_ru'] = details.get('name', '')
            result['title_original'] = details.get('original_name', '')
            result['year'] = int(details.get('first_air_date', '0')[:4]) if details.get('first_air_date') else 0
            result['genres'] = ', '.join([g['name'] for g in details.get('genres', [])])
            result['countries'] = ', '.join([c['iso_3166_1'] for c in details.get('production_countries', [])])
            result['rating'] = details.get('vote_average', 0.0)
            if details.get('poster_path'):
                result['poster_url'] = f"https://image.tmdb.org/t/p/w500{details['poster_path']}"
        except: pass

    return result
def main():
    print("🎬 ЭТАП 2: Связь с TMDB и обогащение базы...")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Берем только те ID, у которых еще нет русского названия (можно прерывать скрипт!)
    cursor.execute("SELECT imdb_id FROM movies WHERE title_ru IS NULL")
    movies_to_fetch = [row[0] for row in cursor.fetchall()]
    
    total = len(movies_to_fetch)
    if total == 0:
        print("✅ Все фильмы уже имеют идеальные метаданные!")
        return
        
    print(f"🔍 Найдено {total} фильмов для загрузки. Начинаем (это займет пару минут)...\n")

    processed = 0
    for imdb_id in movies_to_fetch:
        data = fetch_tmdb_data(imdb_id)
        
        if data:
            update_movie_in_db(cursor, data)
            processed += 1
            
            # Красивый вывод в консоль
            if data['type'] == 'tv' and data['season'] > 0:
                print(f"[{processed}/{total}] 📺 {data['title_ru']} (S{data['season']}E{data['episode']}) | 🌍 {data['countries']}")
            elif data['title_ru'] != 'Not Found':
                print(f"[{processed}/{total}] 🎬 {data['title_ru']} ({data['year']}) | 🌍 {data['countries']}")
            else:
                print(f"[{processed}/{total}] ❌ Не найдено в TMDB: {imdb_id}")
                
            # Сохраняем каждые 10 запросов
            if processed % 10 == 0:
                conn.commit()
                
            # Легкая пауза (TMDB разрешает до 40 запросов в секунду, мы делаем с запасом)
            time.sleep(0.05)

    conn.commit()
    conn.close()
    
    print("\n" + "="*50)
    print("🎉 ГОТОВО! Ваша мастер-база полностью сформирована и обогащена!")
    print("="*50)

if __name__ == '__main__':
    main()