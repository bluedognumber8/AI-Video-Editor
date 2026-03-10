import sqlite3
import requests
import time
from tqdm import tqdm

DB_NAME = 'movies_master.sqlite'
API_KEY = "TMDB_API_KEY_PLACEHOLDER"
BASE_URL = "https://api.themoviedb.org/3"

def fetch_country_from_tmdb(imdb_id):
    find_url = f"{BASE_URL}/find/{imdb_id}?api_key={API_KEY}&external_source=imdb_id"
    
    try:
        resp = requests.get(find_url, timeout=10)
        resp.raise_for_status() # Бросит исключение, если сервер ответит 401 или 404
        data = resp.json()
        
        if data.get('movie_results'):
            tmdb_id = data['movie_results'][0]['id']
            det_url = f"{BASE_URL}/movie/{tmdb_id}?api_key={API_KEY}"
            det_resp = requests.get(det_url, timeout=10).json()
            countries = det_resp.get('production_countries', [])
            if countries:
                return ', '.join([c['iso_3166_1'] for c in countries]), None

        elif data.get('tv_results'):
            tmdb_id = data['tv_results'][0]['id']
            det_url = f"{BASE_URL}/tv/{tmdb_id}?api_key={API_KEY}"
            det_resp = requests.get(det_url, timeout=10).json()
            countries = det_resp.get('production_countries', [])
            if countries: return ', '.join([c['iso_3166_1'] for c in countries]), None
            origin = det_resp.get('origin_country', [])
            if origin: return ', '.join(origin), None

        elif data.get('tv_episode_results'):
            show_id = data['tv_episode_results'][0]['show_id']
            det_url = f"{BASE_URL}/tv/{show_id}?api_key={API_KEY}"
            det_resp = requests.get(det_url, timeout=10).json()
            countries = det_resp.get('production_countries', [])
            if countries: return ', '.join([c['iso_3166_1'] for c in countries]), None
            origin = det_resp.get('origin_country', [])
            if origin: return ', '.join(origin), None

    except Exception as e:
        # ВОЗВРАЩАЕМ ТЕКСТ ОШИБКИ ДЛЯ ДИАГНОСТИКИ
        return None, str(e)
        
    return 'Unknown', None

def main():
    print("="*50)
    print(" 🌍 ЗАГРУЗКА СТРАН ИЗ TMDB (УМНЫЙ РЕЖИМ С ПРЕДОХРАНИТЕЛЕМ) ")
    print("="*50)
    
    print("Что будем обновлять?")
    print("1 - ТОЛЬКО Фильмы (Быстро)")
    print("2 - ТОЛЬКО Сериалы")
    print("3 - ВСЁ ВМЕСТЕ")
    choice = input("Ваш выбор (по умолчанию 1): ").strip()
    
    type_filter = "AND type = 'movie'"
    if choice == '2': type_filter = "AND type != 'movie'"
    elif choice == '3': type_filter = ""
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    query = f'''
        SELECT imdb_id 
        FROM movies 
        WHERE (countries IS NULL 
           OR TRIM(countries) = '' 
           OR LOWER(countries) LIKE '%unknown%' 
           OR LOWER(countries) LIKE '%foreign%')
          {type_filter}
    '''
    
    cursor.execute(query)
    movies = cursor.fetchall()
    
    if not movies:
        print("✅ В выбранной категории все страны уже проставлены! База идеальна.")
        return

    print(f"\n🔍 Найдено медиафайлов для обновления: {len(movies)} шт.")
    
    update_data = []
    errors_total = 0
    consecutive_errors = 0 # СЧЕТЧИК ПОДРЯД ИДУЩИХ ОШИБОК

    with tqdm(total=len(movies), desc="Загрузка стран", unit="шт") as pbar:
        for row in movies:
            imdb_id = row[0]
            country, error_msg = fetch_country_from_tmdb(imdb_id)
            
            if country:
                update_data.append((country, imdb_id))
                consecutive_errors = 0 # Сбрасываем счетчик при успехе
            else:
                errors_total += 1
                consecutive_errors += 1
                
                # Если это первая ошибка, выведем её в консоль
                if consecutive_errors == 1 and error_msg:
                    tqdm.write(f"\n⚠️ Ошибка сети: {error_msg}")
                
                # СТОП-КРАН: Если 10 ошибок подряд - вырубаем скрипт!
                if consecutive_errors >= 10:
                    tqdm.write("\n🛑 СТОП-КРАН: 10 ошибок подряд. Интернет не работает или API заблокирован!")
                    tqdm.write("Скрипт остановлен, чтобы не тратить ваше время. Включите VPN и попробуйте снова.")
                    break
                
            # СОХРАНЯЕМ КАЖДЫЕ 50 ШТУК
            if len(update_data) >= 50:
                cursor.executemany("UPDATE movies SET countries = ? WHERE imdb_id = ?", update_data)
                conn.commit()
                update_data = [] 
                
            pbar.update(1)
            time.sleep(0.05)

    # Сохраняем остатки
    if update_data:
        cursor.executemany("UPDATE movies SET countries = ? WHERE imdb_id = ?", update_data)
        conn.commit()

    conn.close()
    
    print("\n" + "="*50)
    print("✅ Работа скрипта завершена.")
    if errors_total > 0:
        print(f"⚠️ Всего ошибок: {errors_total}")
    print("="*50)

if __name__ == '__main__':
    main()