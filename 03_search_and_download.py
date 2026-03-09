import sqlite3
import re
import datetime
import subprocess
import sys

DB_NAME = 'movies_master.sqlite'

def srt_to_seconds(srt_time_str):
    """Превращает таймкод субтитров 00:45:10,000 в секунды"""
    # Если вдруг таймкод пришел в формате с точкой вместо запятой
    srt_time_str = srt_time_str.replace('.', ',')
    time_part, ms_part = srt_time_str.split(',')
    h, m, s = map(int, time_part.split(':'))
    ms = int(ms_part)
    return h * 3600 + m * 60 + s + ms / 1000.0

def seconds_to_hms(seconds):
    """Превращает секунды обратно в 00:45:10"""
    return str(datetime.timedelta(seconds=int(seconds)))

def search_jokes(keyword, limit=10):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Добавляем * для поиска по началу слова/окончаниям
    search_query = f"{keyword}*"
    
    sql = '''
        SELECT 
            m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
            MIN(s.start_time), MAX(s.end_time), s.text, m.imdb_id
        FROM subtitles s
        JOIN movies m ON s.imdb_id = m.imdb_id
        WHERE s.id IN (
            SELECT rowid FROM subtitles_fts WHERE text MATCH ?
        )
        GROUP BY m.imdb_id, SUBSTR(s.start_time, 1, 5)
        ORDER BY m.rating DESC
        LIMIT ?
    '''
    cursor.execute(sql, (search_query, limit))
    results = cursor.fetchall()
    conn.close()
    return results

def download_clip(title, imdb_id, start_srt, end_srt, m_type, season, episode):
    """Считает таймкоды и вызывает скрипт скачивания"""
    # Добавляем запас времени (3 сек до и 3 сек после)
    start_sec = max(0, srt_to_seconds(start_srt) - 3)
    end_sec = srt_to_seconds(end_srt) + 3
    duration = end_sec - start_sec
    start_hms = seconds_to_hms(start_sec)
    
    print("\n" + "="*60)
    print("🎬 ПЕРЕДАЮ ЗАДАЧУ ДВИЖКУ СКАЧИВАНИЯ 🎬")
    print("="*60)
    
    # Используем sys.executable, чтобы всегда вызывать правильную версию Python (ту же, где запущен скрипт)
    command = [
        sys.executable, "magnet_get.py",
        "--title", title,
        "--type", str(m_type),
        "--season", str(season),
        "--episode", str(episode),
        "--start", start_hms,
        "--duration", str(int(duration))
    ]
    
    # Запускаем magnet_get.py
    subprocess.run(command)

def main():
    print("="*60)
    print(" 🤖 AI-РЕЖИССЕР МОНТАЖА (Мастер-Скрипт v2.0) ")
    print("="*60)
    
    while True:
        word = input("\nВведите фразу/слово для поиска мема (или 'q' для выхода): ").strip()
        if word.lower() == 'q': break
        if not word: continue
            
        print("🔍 Ищу в локальной базе...\n")
        results = search_jokes(word, limit=7)
        
        if not results:
            print("❌ Ничего не найдено.")
            continue
            
        for i, row in enumerate(results, 1):
            title, year, genres, rating, m_type, season, ep, start, end, text, imdb_id = row
            
            if m_type == 'tv' and season > 0:
                display_title = f"📺 {title} (S{season:02d}E{ep:02d})"
            else:
                display_title = f"🎬 {title} ({year})"
                
            text_hl = re.sub(f'(?i)({word}[а-яА-Яa-zA-Z]*)', r'【\1】', text)
            
            print(f"[{i}] {display_title} | ★ {rating} | {genres}")
            print(f"    ⏱ {start} --> {end}")
            print(f"    💬 {text_hl}\n")
            
        choice = input("Какой номер качаем? (Enter - пропустить поиск): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            selected = results[int(choice)-1]
            
            download_clip(
                title=selected[0], 
                imdb_id=selected[10], 
                start_srt=selected[7], 
                end_srt=selected[8],
                m_type=selected[4],
                season=selected[5],
                episode=selected[6]
            )

if __name__ == '__main__':
    main()