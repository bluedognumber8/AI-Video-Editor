import sqlite3
import pandas as pd
import os

DB_NAME = 'movies_master.sqlite'
EPISODES_FILE = 'title.episode.tsv.gz'
BASICS_FILE = 'title.basics.tsv.gz'

def update_tv_shows():
    if not os.path.exists(EPISODES_FILE) or not os.path.exists(BASICS_FILE):
        print("❌ Скачайте title.episode.tsv.gz и title.basics.tsv.gz с IMDb!")
        return

    print("🔌 Чтение базы данных SQLite...")
    conn = sqlite3.connect(DB_NAME)
    # Берем только ID эпизодов сериалов
    db_episodes = pd.read_sql_query("SELECT imdb_id FROM movies WHERE type = 'tv'", conn)
    episode_ids = set(db_episodes['imdb_id'].tolist())
    
    if not episode_ids:
        print("В базе нет сериалов.")
        return

    print("📖 Поиск родительских сериалов в title.episode.tsv.gz...")
    ep_to_parent = {}
    chunksize = 10 ** 6
    for chunk in pd.read_csv(EPISODES_FILE, sep='\t', usecols=['tconst', 'parentTconst'], chunksize=chunksize, dtype=str):
        # Оставляем только те эпизоды, которые есть в нашей БД
        filtered = chunk[chunk['tconst'].isin(episode_ids)]
        ep_to_parent.update(dict(zip(filtered['tconst'], filtered['parentTconst'])))

    parent_ids = set(ep_to_parent.values())
    print(f"   Найдено {len(parent_ids)} уникальных сериалов-родителей.")

    print("📖 Поиск названий сериалов в title.basics.tsv.gz...")
    parent_names = {}
    for chunk in pd.read_csv(BASICS_FILE, sep='\t', usecols=['tconst', 'primaryTitle'], chunksize=chunksize, dtype=str):
        filtered = chunk[chunk['tconst'].isin(parent_ids)]
        parent_names.update(dict(zip(filtered['tconst'], filtered['primaryTitle'])))

    print("💾 Обновление базы данных...")
    cursor = conn.cursor()
    updates_count = 0
    
    for ep_id, parent_id in ep_to_parent.items():
        show_name = parent_names.get(parent_id)
        if show_name:
            cursor.execute("""
                UPDATE movies 
                SET title_ru = ?, title_original = ?
                WHERE imdb_id = ?
            """, (show_name, show_name, ep_id))
            updates_count += 1

    conn.commit()
    conn.close()
    print(f"✅ Успех! Обновлено {updates_count} эпизодов.")

if __name__ == "__main__":
    update_tv_shows()