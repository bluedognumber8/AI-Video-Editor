import sqlite3

DB_NAME = 'movies_brain.sqlite'

def search_joke(keyword, min_rating=0.0, genre=None, limit=10):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    search_query = f"{keyword}*"

    # Обратите внимание на GROUP BY m.imdb_id — это убьет дубликаты переводов!
    sql = '''
        SELECT 
            m.title, m.year, m.genres, m.rating, 
            MIN(s.start_time), s.text
        FROM subtitles s
        JOIN movies m ON s.imdb_id = m.imdb_id
        WHERE s.id IN (
            SELECT rowid FROM subtitles_fts WHERE text MATCH ?
        )
    '''
    
    params = [search_query]

    if min_rating > 0:
        sql += ' AND m.rating >= ?'
        params.append(min_rating)
        
    if genre:
        sql += ' AND m.genres LIKE ?'
        params.append(f'%{genre}%')

    # Группируем по ID фильма, чтобы исключить разные версии субтитров одного и того же момента
    sql += ' GROUP BY m.imdb_id, SUBSTR(s.start_time, 1, 5)' 
    
    sql += ' ORDER BY m.rating DESC LIMIT ?'
    params.append(limit)

    cursor.execute(sql, params)
    results = cursor.fetchall()
    conn.close()
    
    return results

def main():
    while True:
        word = input("\nСлово для поиска (или 'q'): ").strip()
        if word.lower() == 'q': break
        if not word: continue
            
        results = search_joke(word, limit=5)
        
        if not results:
            print("❌ Ничего не найдено.")
            continue
            
        for i, row in enumerate(results, 1):
            title, year, genres, rating, start, text = row
            
            # Подсветка слова (грубая, без учета регистра)
            import re
            text_highlighted = re.sub(f'(?i)({word}[а-яА-Яa-zA-Z]*)', r'【\1】', text)
            
            print(f"[{i}] {title} ({year}) | ★ {rating} | {genres}")
            print(f"    ⏱ Таймкод: {start}")
            print(f"    💬 Текст: {text_highlighted}\n")

if __name__ == '__main__':
    main()