import sqlite3
import zipfile
import io
import re
import pysrt
import os
import csv

# --- НАСТРОЙКИ ---
TXT_FILE = 'subtitles_all.txt'
SOURCE_DB = 'opensubs.db'
DEST_DB = 'new_media_master.sqlite' # Changed name slightly to reflect it's not just movies
CHUNK_SIZE = 3

def setup_destination_db():
    if os.path.exists(DEST_DB):
        os.remove(DEST_DB)
        
    conn = sqlite3.connect(DEST_DB)
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE subtitles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        imdb_id TEXT,
        start_time TEXT,
        end_time TEXT,
        text TEXT
    )''')
    
    cursor.execute('''
    CREATE TABLE movies (
        imdb_id TEXT PRIMARY KEY,
        tmdb_id INTEGER,
        type TEXT,
        title_ru TEXT,
        title_original TEXT,
        year INTEGER,
        genres TEXT,
        countries TEXT,
        rating REAL,
        season INTEGER,
        episode INTEGER,
        poster_url TEXT
    )''')
    
    cursor.execute('''
    CREATE VIRTUAL TABLE subtitles_fts 
    USING fts5(text, content='subtitles', content_rowid='id');
    ''')
    
    conn.commit()
    return conn

def read_treasure_map():
    """Читает subtitles_all.txt и находит ID ВСЕХ типов видео на РУССКОМ языке"""
    print(f"🗺 Читаю карту сокровищ ({TXT_FILE}). Фильтруем ВСЕ русские субтитры...")
    rus_media = {}
    
    with open(TXT_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            # 1. Проверяем язык и формат
            is_russian = row.get('LanguageName') == 'Russian'
            is_srt = row.get('SubFormat') == 'srt'
            
            # УБРАН ЖЕСТКИЙ ФИЛЬТР ПО ФИЛЬМАМ. Теперь берем всё.
            
            if is_russian and is_srt:
                sub_id = int(row['IDSubtitle'])
                
                # Формируем правильный IMDb ID
                raw_imdb = row['ImdbID'].strip()
                if raw_imdb and raw_imdb != '0':
                    imdb_id = f"tt{raw_imdb.zfill(7)}"
                    
                    # Получаем тип (movie, episode, и т.д.), сезон и эпизод
                    media_type = row.get('MovieKind', 'unknown')
                    
                    # Безопасно парсим сезон и эпизод (если они есть)
                    season = row.get('SeriesSeason')
                    season = int(season) if season and season.isdigit() else None
                    
                    episode = row.get('SeriesEpisode')
                    episode = int(episode) if episode and episode.isdigit() else None
                    
                    # Теперь сохраняем не просто ID, а словарь с метаданными
                    rus_media[sub_id] = {
                        'imdb_id': imdb_id,
                        'type': media_type,
                        'season': season,
                        'episode': episode
                    }
                    
    print(f"✅ Найдено субтитров (фильмы/сериалы/т.д.): {len(rus_media)} штук!")
    return rus_media

def process_srt_in_memory(srt_text, imdb_id, cursor):
    try:
        subs = pysrt.from_string(srt_text)
    except:
        return False

    chunk_text = []
    chunk_start = None
    
    for i, sub in enumerate(subs):
        text = sub.text.replace('\n', ' ').strip()
        if "www.OpenSubtitles.org" in text or "VIP" in text or "реклам" in text: 
            continue
        text = re.sub(r'<[^>]+>', '', text)
        if not text: continue
            
        if not chunk_text: chunk_start = str(sub.start)
        chunk_text.append(text)
        chunk_end = str(sub.end)
        
        if len(chunk_text) >= CHUNK_SIZE or i == len(subs) - 1:
            full_text = ' '.join(chunk_text)
            cursor.execute('INSERT INTO subtitles (imdb_id, start_time, end_time, text) VALUES (?, ?, ?, ?)', 
                           (imdb_id, chunk_start, chunk_end, full_text))
            cursor.execute('INSERT INTO subtitles_fts (rowid, text) VALUES (?, ?)', 
                           (cursor.lastrowid, full_text))
            chunk_text = []
    return True

def main():
    print("🚀 СТАРТ: Умная выкачка по карте сокровищ...")
    
    if not os.path.exists(SOURCE_DB) or not os.path.exists(TXT_FILE):
        print("🛑 ОШИБКА: Нет opensubs.db или subtitles_all.txt!")
        return

    # 1. Получаем точные ID и метаданные
    rus_map = read_treasure_map()
    
    if not rus_map:
        print("❌ Не найдено ни одного медиафайла по заданным фильтрам.")
        return
        
    src_conn = sqlite3.connect(SOURCE_DB)
    src_cursor = src_conn.cursor()
    dest_conn = setup_destination_db()
    dest_cursor = dest_conn.cursor()
    
    processed = 0
    errors = 0
    
    print("📥 Начинаю распаковку и нарезку...")
    
    sub_ids = list(rus_map.keys())
    chunk_size = 900
    
    for i in range(0, len(sub_ids), chunk_size):
        batch_ids = sub_ids[i:i+chunk_size]
        placeholders = ','.join('?' for _ in batch_ids)
        
        src_cursor.execute(f"SELECT num, file FROM subz WHERE num IN ({placeholders})", batch_ids)
        
        for row in src_cursor.fetchall():
            num, blob_data = row
            
            # Достаем все данные об этом файле из нашего словаря
            media_info = rus_map[num]
            imdb_id = media_info['imdb_id']
            m_type = media_info['type']
            m_season = media_info['season']
            m_episode = media_info['episode']
            
            if not blob_data: continue
                
            try:
                with zipfile.ZipFile(io.BytesIO(blob_data)) as z:
                    srt_file = next((f for f in z.namelist() if f.endswith('.srt')), None)
                    if srt_file:
                        srt_bytes = z.read(srt_file)
                        try: srt_text = srt_bytes.decode('utf-8')
                        except: srt_text = srt_bytes.decode('cp1251', errors='ignore')
                            
                        if process_srt_in_memory(srt_text, imdb_id, dest_cursor):
                            # Пишем реальный type, season и episode в таблицу
                            dest_cursor.execute('''
                                INSERT OR IGNORE INTO movies (imdb_id, type, season, episode) 
                                VALUES (?, ?, ?, ?)
                            ''', (imdb_id, m_type, m_season, m_episode))
                            processed += 1
            except:
                errors += 1
                
        dest_conn.commit()
        print(f"⏳ Обработано: {processed} / {len(sub_ids)}...")

    dest_conn.close()
    src_conn.close()
    
    print("\n" + "="*50)
    print(f"✅ МАГИЯ СВЕРШИЛАСЬ! База {DEST_DB} собрана!")
    print(f"🎬 Успешно добавлено файлов: {processed}")
    print(f"🗑 Ошибок архивов: {errors}")
    print("="*50)

if __name__ == '__main__':
    main()