import sqlite3
import pandas as pd

DB_NAME = 'movies_brain.sqlite'

def main():
    conn = sqlite3.connect(DB_NAME)
    
    print("📊 АНАЛИЗ БАЗЫ ДАННЫХ 📊\n")
    
    # 1. Сколько всего уникальных произведений?
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT imdb_id) FROM movies")
    print(f"🎬 Всего уникальных фильмов/серий: {cursor.fetchone()[0]}")
    
    # 2. Сколько всего строк субтитров?
    cursor.execute("SELECT COUNT(*) FROM subtitles")
    print(f"💬 Всего склеенных фраз (чанков): {cursor.fetchone()[0]}")
    
    # 3. Топ-10 самых частых жанров
    print("\n🎭 Популярные жанры в вашей базе:")
    df_genres = pd.read_sql_query('''
        SELECT genres, COUNT(*) as count 
        FROM movies 
        GROUP BY genres 
        ORDER BY count DESC 
        LIMIT 10
    ''', conn)
    print(df_genres.to_string(index=False))
    
    # 4. Пример случайных фильмов из базы
    print("\n🎲 Случайные 5 произведений из базы:")
    df_random = pd.read_sql_query('''
        SELECT title, year, genres, rating 
        FROM movies 
        ORDER BY RANDOM() 
        LIMIT 5
    ''', conn)
    print(df_random.to_string(index=False))

    conn.close()

if __name__ == '__main__':
    main()