import sqlite3
import re
import datetime
import subprocess
import sys

DB_NAME = 'movies_master.sqlite'

def srt_to_seconds(srt_time_str):
    srt_time_str = srt_time_str.replace('.', ',')
    time_part, ms_part = srt_time_str.split(',')
    h, m, s = map(int, time_part.split(':'))
    ms = int(ms_part)
    return h * 3600 + m * 60 + s + ms / 1000.0

def seconds_to_hms(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

def search_jokes(keyword, limit=7, min_rating=0.0, target_type='all'):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    search_query = f"{keyword}*"
    params = [search_query]
    
    sql = '''
        SELECT 
            m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
            MIN(s.start_time), MAX(s.end_time), s.text, m.imdb_id
        FROM subtitles s
        JOIN movies m ON s.imdb_id = m.imdb_id
        WHERE s.id IN (
            SELECT rowid FROM subtitles_fts WHERE text MATCH ?
        )
    '''
    
    if min_rating > 0:
        sql += ' AND m.rating >= ?'
        params.append(min_rating)
        
    if target_type in ['movie', 'tv']:
        sql += ' AND m.type = ?'
        params.append(target_type)
        
    sql += '''
        GROUP BY m.imdb_id, SUBSTR(s.start_time, 1, 5)
        ORDER BY m.rating DESC
        LIMIT ?
    '''
    params.append(limit)
    
    cursor.execute(sql, params)
    results = cursor.fetchall()
    conn.close()
    return results

# ДОБАВЛЕН АРГУМЕНТ year
def download_clip(title, year, imdb_id, start_srt, end_srt, m_type, season, episode, source_pref):
    start_sec = max(0, srt_to_seconds(start_srt) - 3)
    end_sec = srt_to_seconds(end_srt) + 3
    duration = end_sec - start_sec
    start_hms = seconds_to_hms(start_sec)
    
    print("\n" + "="*60)
    print("🎬 ПЕРЕДАЮ ЗАДАЧУ ДВИЖКУ СКАЧИВАНИЯ 🎬")
    print("="*60)
    
    command = [
        sys.executable, "magnet_get.py",
        "--title", title,
        "--year", str(year), # ПЕРЕДАЕМ ГОД В КАЧАЛКУ
        "--type", str(m_type),
        "--season", str(season),
        "--episode", str(episode),
        "--start", start_hms,
        "--duration", str(int(duration)),
        "--source", source_pref
    ]
    
    subprocess.run(command)

def main():
    print("="*60)
    print(" 🤖 AI-РЕЖИССЕР МОНТАЖА (v2.3 Точный поиск) ")
    print("="*60)
    
    while True:
        word = input("\n📝 Введите слово/фразу (или 'q' для выхода): ").strip()
        if word.lower() == 'q': break
        if not word: continue
            
        print("\n⚙️  НАСТРОЙКИ ПОИСКА (Нажмите Enter, чтобы пропустить):")
        rating_input = input("   ⭐️ Мин. рейтинг (например, 7.5): ").strip()
        min_r = float(rating_input) if rating_input.replace('.', '', 1).isdigit() else 0.0
        
        type_input = input("   🎞  Где ищем? (1 - Фильмы, 2 - Сериалы, Enter - Везде): ").strip()
        t_type = 'all'
        if type_input == '1': t_type = 'movie'
        elif type_input == '2': t_type = 'tv'
            
        print("\n🔍 Ищу в локальной базе...\n")
        results = search_jokes(word, limit=7, min_rating=min_r, target_type=t_type)
        
        if not results:
            print("❌ По вашим фильтрам ничего не найдено. Попробуйте смягчить условия.")
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
            
            src_input = input("Где искать видео? (1 - Везде (сначала YouTube), 2 - ТОЛЬКО Торрент): ").strip()
            pref = "torrent" if src_input == "2" else "all"
            
            download_clip(
                title=selected[0], 
                year=selected[1], # БЕРЕМ ГОД ИЗ ВЫБРАННОГО РЕЗУЛЬТАТА
                imdb_id=selected[10], 
                start_srt=selected[7], 
                end_srt=selected[8],
                m_type=selected[4],
                season=selected[5],
                episode=selected[6],
                source_pref=pref
            )

if __name__ == '__main__':
    main()