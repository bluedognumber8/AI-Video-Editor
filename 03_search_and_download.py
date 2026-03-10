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

# ДОБАВЛЕН genre_filter
def search_jokes(keyword, limit=7, min_rating=0.0, target_type='all', country_filter='all', genre_filter=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    search_query = f"{keyword}*"
    params = [search_query]
    
    sql = '''
        SELECT 
            m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
            MIN(s.start_time), MAX(s.end_time), s.text, m.imdb_id, m.countries, m.title_original
        FROM subtitles s
        JOIN movies m ON s.imdb_id = m.imdb_id
        WHERE s.id IN (
            SELECT rowid FROM subtitles_fts WHERE text MATCH ?
        )
    '''
    
    # Фильтр по рейтингу
    if min_rating > 0:
        sql += ' AND m.rating >= ?'
        params.append(min_rating)
        
    # Фильтр по типу (фильм/сериал)
    if target_type in ['movie', 'tv']:
        sql += ' AND m.type = ?'
        params.append(target_type)
        
    # Фильтр по стране
    if country_filter == 'ru':
        sql += " AND (m.countries LIKE '%RU%' OR m.countries LIKE '%SU%')"
    elif country_filter == 'foreign':
        sql += " AND (m.countries NOT LIKE '%RU%' AND m.countries NOT LIKE '%SU%' OR m.countries IS NULL)"

    # ФИЛЬТР ПО ЖАНРУ
    if genre_filter:
        sql += " AND m.genres LIKE ?"
        params.append(f"%{genre_filter}%")

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

def download_clip(title, orig_title, year, imdb_id, start_srt, end_srt, m_type, season, episode, source_pref):
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
        "--orig_title", orig_title,
        "--year", str(year),
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
    print(" 🤖 AI-РЕЖИССЕР МОНТАЖА (v2.6 Ультимативные фильтры) ")
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
        
        country_input = input("   🌍 Производство? (1 - Наше [RU/SU], 2 - Зарубежное, Enter - Везде): ").strip()
        c_filter = 'all'
        if country_input == '1': c_filter = 'ru'
        elif country_input == '2': c_filter = 'foreign'

        # НОВОЕ МЕНЮ ЖАНРОВ
        genre_input = input("   🎭 Жанр? (1 - Комедия, 2 - Драма, 3 - Боевик, 4 - Фантастика, 5 - Ужасы, Enter - Любой): ").strip()
        g_filter = None
        if genre_input == '1': g_filter = 'Comedy'
        elif genre_input == '2': g_filter = 'Drama'
        elif genre_input == '3': g_filter = 'Action'
        elif genre_input == '4': g_filter = 'Sci-Fi'
        elif genre_input == '5': g_filter = 'Horror'
        elif genre_input: g_filter = genre_input # Если ввели текстом (например "Romance")
            
        print("\n🔍 Ищу в локальной базе...\n")
        results = search_jokes(word, limit=7, min_rating=min_r, target_type=t_type, country_filter=c_filter, genre_filter=g_filter)
        
        if not results:
            print("❌ По вашим фильтрам ничего не найдено. Попробуйте смягчить условия.")
            continue
            
        for i, row in enumerate(results, 1):
            title, year, genres, rating, m_type, season, ep, start, end, text, imdb_id, countries, orig_title = row
            
            c_disp = countries if countries else "Unknown"
            
            if m_type == 'tv' and season > 0:
                display_title = f"📺 {title} (S{season:02d}E{ep:02d})"
            else:
                display_title = f"🎬 {title} ({year})"
                
            text_hl = re.sub(f'(?i)({word}[а-яА-Яa-zA-Z]*)', r'【\1】', text)
            
            print(f"[{i}] {display_title} | 🌍 {c_disp} | ★ {rating} | {genres}")
            print(f"    ⏱ {start} --> {end}")
            print(f"    💬 {text_hl}\n")
            
        choice = input("Какой номер качаем? (Enter - пропустить поиск): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            selected = results[int(choice)-1]
            
            src_input = input("Где искать видео? (1 - Везде (сначала YouTube), 2 - ТОЛЬКО Торрент): ").strip()
            pref = "torrent" if src_input == "2" else "all"
            
            download_clip(
                title=selected[0], 
                orig_title=selected[12], 
                year=selected[1], 
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