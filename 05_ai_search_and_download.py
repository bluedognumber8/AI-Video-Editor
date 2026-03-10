import sqlite3
import datetime
import subprocess
import sys
import os
import chromadb
from sentence_transformers import SentenceTransformer

# --- НАСТРОЙКИ ---
SQLITE_DB = 'movies_master.sqlite'
CHROMA_DIR = './chroma_db'

print("⏳ Загрузка ИИ-модели для поиска (E5-Base)...")
# Модель загружается один раз при старте скрипта
model = SentenceTransformer('intfloat/multilingual-e5-base')

print("📁 Подключение к базам данных...")
chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
collection = chroma_client.get_collection(name="subtitles_semantic")

def srt_to_seconds(srt_time_str):
    srt_time_str = srt_time_str.replace('.', ',')
    time_part, ms_part = srt_time_str.split(',')
    h, m, s = map(int, time_part.split(':'))
    ms = int(ms_part)
    return h * 3600 + m * 60 + s + ms / 1000.0

def seconds_to_hms(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

def semantic_search(query_text, limit=5, min_rating=0.0, target_type='all', country_filter='all', genre_filter=None):
    """Ищет фразы по СМЫСЛУ через векторы ChromaDB, а затем достает таймкоды из SQLite"""
    
    # Модель E5 требует префикс "query: " для поисковых запросов
    e5_query = f"query: {query_text}"
    
    # 1. Превращаем запрос в вектор
    query_vector = model.encode([e5_query], normalize_embeddings=True).tolist()
    
    # 2. Формируем ИИ-фильтры (Where clause для ChromaDB)
    where_filters = {}
    and_conditions = []
    
    if min_rating > 0:
        and_conditions.append({"rating": {"$gte": min_rating}})
        
    if target_type in ['movie', 'tv']:
        and_conditions.append({"type": {"$eq": target_type}})
        
    if country_filter == 'ru':
        # ChromaDB оператор $in ищет в списке
        and_conditions.append({"countries": {"$in": ["RU", "SU", "SUHH"]}})
    elif country_filter == 'foreign':
        and_conditions.append({"countries": {"$nin": ["RU", "SU", "SUHH", "Unknown"]}})
        
    if genre_filter:
        and_conditions.append({"genres": {"$contains": genre_filter}})
        
    # Собираем фильтры воедино
    if len(and_conditions) == 1:
        where_filters = and_conditions[0]
    elif len(and_conditions) > 1:
        where_filters = {"$and": and_conditions}

    # 3. ИЩЕМ В CHROMADB
    search_args = {
        "query_embeddings": query_vector,
        "n_results": limit,
        "include": ["distances"] # Нам нужны только ID и дистанция (схожесть)
    }
    
    if where_filters:
        search_args["where"] = where_filters
        
    chroma_result = collection.query(**search_args)
    
    found_ids = chroma_result['ids'][0]
    distances = chroma_result['distances'][0] # Чем ближе к 0, тем точнее совпадение
    
    if not found_ids:
        return []

    # 4. ИДЕМ В SQLITE ЗА ТАЙМКОДАМИ И НАЗВАНИЯМИ
    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    
    placeholders = ','.join('?' for _ in found_ids)
    sql = f'''
        SELECT 
            m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
            s.start_time, s.end_time, s.text, m.imdb_id, m.countries, m.title_original, s.id
        FROM subtitles s
        JOIN movies m ON s.imdb_id = m.imdb_id
        WHERE s.id IN ({placeholders})
    '''
    cursor.execute(sql, found_ids)
    sqlite_rows = cursor.fetchall()
    conn.close()
    
    # Сортируем результаты из SQLite в том же порядке, в котором их выдала нейросеть (по смыслу)
    id_to_row = {str(row[13]): row for row in sqlite_rows} # row[13] это s.id
    
    final_results = []
    for f_id, dist in zip(found_ids, distances):
        if f_id in id_to_row:
            # Превращаем дистанцию (0.15) в процент сходства (85%)
            similarity = round((1.0 - dist) * 100, 1)
            final_results.append((id_to_row[f_id], similarity))
            
    return final_results

def download_clip(title, orig_title, year, imdb_id, start_srt, end_srt, m_type, season, episode, source_pref, pad_start, pad_end):
    start_sec = max(0, srt_to_seconds(start_srt) - pad_start)
    end_sec = srt_to_seconds(end_srt) + pad_end
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
    print(" 🧠 AI-РЕЖИССЕР МОНТАЖА (v3.0 Семантический Поиск) ")
    print("="*60)
    
    while True:
        # ТЕПЕРЬ ВЫ ВВОДИТЕ НЕ СЛОВА, А ОПИСАНИЯ СЦЕН!
        word = input("\n📝 Опишите смысл сцены или эмоцию (или 'q' для выхода):\n> ").strip()
        if word.lower() == 'q': break
        if not word: continue
            
        print("\n⚙️  НАСТРОЙКИ ПОИСКА (Нажмите Enter, чтобы пропустить):")
        rating_input = input("   ⭐️ Мин. рейтинг (например, 7.5): ").strip()
        min_r = float(rating_input) if rating_input.replace('.', '', 1).isdigit() else 0.0
        
        country_input = input("   🌍 Производство? (1 - Наше [RU/SU], 2 - Зарубежное, Enter - Везде): ").strip()
        c_filter = 'all'
        if country_input == '1': c_filter = 'ru'
        elif country_input == '2': c_filter = 'foreign'

        genre_input = input("   🎭 Жанр? (1-Комедия, 2-Драма, 3-Боевик, 4-Ужасы, Enter-Любой): ").strip()
        g_filter = None
        if genre_input == '1': g_filter = 'Comedy'
        elif genre_input == '2': g_filter = 'Drama'
        elif genre_input == '3': g_filter = 'Action'
        elif genre_input == '4': g_filter = 'Horror'
        elif genre_input: g_filter = genre_input
            
        print("\n🔍 Ищу по смыслу (Нейросеть думает...)...\n")
        results = semantic_search(word, limit=5, min_rating=min_r, target_type='movie', country_filter=c_filter, genre_filter=g_filter)
        
        if not results:
            print("❌ Нейросеть не нашла подходящих по смыслу сцен.")
            continue
            
        for i, (row, similarity) in enumerate(results, 1):
            title, year, genres, rating, m_type, season, ep, start, end, text, imdb_id, countries, orig_title, s_id = row
            c_disp = countries if countries else "Unknown"
            
            display_title = f"🎬 {title} ({year})"
            
            # Выводим процент совпадения смысла!
            print(f"[{i}] {display_title} | 🌍 {c_disp} | ★ {rating} | 🧠 Смысл: {similarity}%")
            print(f"    ⏱ {start} --> {end}")
            print(f"    💬 {text}\n")
            
        choice = input("Какой номер качаем? (Enter - пропустить поиск): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            selected_row = results[int(choice)-1][0]
            
            src_input = input("Где искать видео? (1 - Везде, 2 - ТОЛЬКО Торрент): ").strip()
            pref = "torrent" if src_input == "2" else "all"
            
            print("\n✂️  Настройка отрезка (по умолчанию берем 2 сек до фразы и 2 после):")
            pad_start_in = input("Секунд ДО фразы? (Enter = 2): ").strip()
            pad_end_in = input("Секунд ПОСЛЕ фразы? (Enter = 2): ").strip()
            
            p_start = float(pad_start_in) if pad_start_in.replace('.','',1).isdigit() else 2.0
            p_end = float(pad_end_in) if pad_end_in.replace('.','',1).isdigit() else 2.0
            
            download_clip(
                title=selected_row[0], 
                orig_title=selected_row[12], 
                year=selected_row[1], 
                imdb_id=selected_row[10], 
                start_srt=selected_row[7], 
                end_srt=selected_row[8],
                m_type=selected_row[4],
                season=selected_row[5],
                episode=selected_row[6],
                source_pref=pref,
                pad_start=p_start,
                pad_end=p_end
            )

if __name__ == '__main__':
    main()