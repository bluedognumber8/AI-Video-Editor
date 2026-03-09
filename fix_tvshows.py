import sqlite3
import gzip
import os

DB_NAME = 'movies_brain.sqlite'
EPISODE_FILE = 'title.episode.tsv.gz'
BASICS_FILE = 'title.basics.tsv.gz'

def add_tv_columns(cursor):
    """Добавляем колонки для сериалов, если их нет"""
    cursor.execute("PRAGMA table_info(movies)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'show_title' not in columns:
        cursor.execute("ALTER TABLE movies ADD COLUMN show_title TEXT")
    if 'season' not in columns:
        cursor.execute("ALTER TABLE movies ADD COLUMN season INTEGER")
    if 'episode' not in columns:
        cursor.execute("ALTER TABLE movies ADD COLUMN episode INTEGER")

def main():
    if not os.path.exists(EPISODE_FILE) or not os.path.exists(BASICS_FILE):
        print(f"🛑 Скачайте {EPISODE_FILE} и {BASICS_FILE}!")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    add_tv_columns(cursor)
    
    # 1. Получаем все ID из нашей базы
    cursor.execute("SELECT imdb_id FROM movies")
    our_ids = {row[0] for row in cursor.fetchall()}
    
    print("🔍 Ищем связи эпизодов с сериалами...")
    
    # Словарь: id_эпизода -> (id_сериала, сезон, серия)
    episode_links = {}
    # Множество всех ID сериалов, названия которых нам нужно будет узнать
    parent_shows_ids = set()
    
    with gzip.open(EPISODE_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 4: continue
            
            ep_id = parts[0]
            parent_id = parts[1]
            season = parts[2]
            episode = parts[3]
            
            if ep_id in our_ids:
                episode_links[ep_id] = {
                    'parent_id': parent_id,
                    'season': int(season) if season.isdigit() else 0,
                    'episode': int(episode) if episode.isdigit() else 0
                }
                parent_shows_ids.add(parent_id)

    print(f"📺 Найдено эпизодов: {len(episode_links)}. Ищем названия их сериалов...")

    # 2. Ищем названия самих сериалов в basics
    parent_titles = {}
    with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
        next(f)
        for line in f:
            parts = line.strip('\n').split('\t')
            if len(parts) < 3: continue
            
            tconst = parts[0]
            if tconst in parent_shows_ids:
                parent_titles[tconst] = parts[2] # primaryTitle

    # 3. Обновляем нашу базу данных
    print("💾 Сохраняем данные в базу...")
    update_data = []
    for ep_id, link in episode_links.items():
        parent_id = link['parent_id']
        show_name = parent_titles.get(parent_id, "Unknown Show")
        
        update_data.append((
            show_name,
            link['season'],
            link['episode'],
            'tv-episode',
            ep_id
        ))
        
    cursor.executemany('''
        UPDATE movies 
        SET show_title = ?, season = ?, episode = ?, kind = ?
        WHERE imdb_id = ?
    ''', update_data)
    
    # У всех остальных ставим kind = 'movie', чтобы было чисто
    cursor.execute("UPDATE movies SET kind = 'movie' WHERE kind IS NULL OR kind = 'unknown'")
    
    conn.commit()
    conn.close()
    print("🎉 ГОТОВО! Сериалы аккуратно разложены по полочкам.")

if __name__ == '__main__':
    main()