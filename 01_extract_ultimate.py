import sqlite3
import zipfile
import io
import re
import pysrt
import os
import csv
import gzip
from tqdm import tqdm

# --- НАСТРОЙКИ ---
TXT_FILE = 'subtitles_all.txt'
SOURCE_DB = 'opensubs.db'
BASICS_FILE = 'title.basics.tsv.gz'
DEST_DB = 'movies_master.sqlite'

# НАСТРОЙКИ УМНОЙ НАРЕЗКИ (АЛГОРИТМ SLIDING WINDOW)
TARGET_DURATION_SEC = 15.0  # Идеальная длина куска для мема/контекста
MAX_SILENCE_SEC = 3.0       # Пауза, означающая смену сцены
OVERLAP_LINES = 2           # Сколько фраз берем в нахлест для следующего куска

# ЧЕРНЫЕ СПИСКИ (Фильтруем на входе!)
BAD_GENRES = {'Documentary', 'Short', 'Adult', 'Reality-TV', 'Game-Show', 'Talk-Show', 'News'}
BAD_TYPES = {'short', 'videoGame', 'tvShort', 'tvPilot', 'tvSpecial'}

def setup_db():
    if os.path.exists(DEST_DB): os.remove(DEST_DB)
    conn = sqlite3.connect(DEST_DB)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE subtitles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, imdb_id TEXT, start_time TEXT, end_time TEXT, text TEXT
    )''')
    cursor.execute('''CREATE TABLE movies (
        imdb_id TEXT PRIMARY KEY, tmdb_id INTEGER, type TEXT, title_ru TEXT, title_original TEXT,
        year INTEGER, genres TEXT, countries TEXT, rating REAL, season INTEGER, episode INTEGER, poster_url TEXT
    )''')
    cursor.execute("CREATE VIRTUAL TABLE subtitles_fts USING fts5(text, content='subtitles', content_rowid='id');")
    conn.commit()
    return conn

def get_banned_imdb_ids():
    print(f"🕵️‍♂️ ШАГ 1/3: Анализ IMDb ({BASICS_FILE}) и создание черного списка...")
    banned_ids = set()
    try:
        with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
            next(f)
            for line in tqdm(f, desc="Поиск мусора", total=10500000, unit=" строк"):
                parts = line.strip('\n').split('\t')
                if len(parts) < 9: continue
                
                tconst, t_type, genres = parts[0], parts[1], parts[8]
                
                if t_type in BAD_TYPES:
                    banned_ids.add(tconst)
                    continue
                    
                if set(genres.split(',')).intersection(BAD_GENRES):
                    banned_ids.add(tconst)
    except FileNotFoundError:
        print(f"🛑 ОШИБКА: {BASICS_FILE} не найден!")
        return None
        
    print(f"🚫 Заблокировано мусорных тайтлов: {len(banned_ids):,}")
    return banned_ids

def read_treasure_map(banned_ids):
    print(f"\n🗺 ШАГ 2/3: Чтение карты сокровищ ({TXT_FILE})...")
    rus_media = {}
    with open(TXT_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            if row.get('LanguageName') == 'Russian' and row.get('SubFormat') == 'srt':
                raw_imdb = row['ImdbID'].strip()
                if raw_imdb and raw_imdb != '0':
                    imdb_id = f"tt{raw_imdb.zfill(7)}"
                    
                    if imdb_id in banned_ids: continue # ПРОПУСКАЕМ МУСОР
                    
                    season = row.get('SeriesSeason')
                    episode = row.get('SeriesEpisode')
                    
                    rus_media[int(row['IDSubtitle'])] = {
                        'imdb_id': imdb_id,
                        'type': row.get('MovieKind', 'movie'),
                        'season': int(season) if season and season.isdigit() else 0,
                        'episode': int(episode) if episode and episode.isdigit() else 0
                    }
    print(f"✅ Отобрано чистых русскоязычных архивов: {len(rus_media):,}")
    return rus_media

def sub_to_sec(sub_time):
    """Конвертирует время pysrt в секунды (float)"""
    return sub_time.hours * 3600 + sub_time.minutes * 60 + sub_time.seconds + sub_time.milliseconds / 1000.0

def clean_text(text):
    """Очищает текст от тегов, но сохраняет смысловые пометки в скобках"""
    text = text.replace('\n', ' ').strip()
    text = re.sub(r'<[^>]+>', '', text) # HTML теги <i>, <b>
    text = re.sub(r'\{[^}]+\}', '', text) # ASS теги {\an8}
    text = re.sub(r'^-\s*|^--\s*', '', text) # Черточки диалогов
    # Мы НЕ удаляем [смеется], так как это важно для нейросети!
    return text

def process_srt_smart(srt_text, imdb_id, cursor):
    """УМНАЯ НАРЕЗКА (ВРЕМЯ + ТИШИНА + НАХЛЕСТ)"""
    try: subs = pysrt.from_string(srt_text)
    except: return False

    if not subs: return False

    current_chunk = []
    
    for i, sub in enumerate(subs):
        text = clean_text(sub.text)
        if not text or "www.OpenSubtitles.org" in text or "VIP" in text: 
            continue
            
        current_chunk.append(sub)
        
        # Если это не последняя фраза, проверяем условия отсечения
        if i < len(subs) - 1:
            next_sub = subs[i + 1]
            
            chunk_start_sec = sub_to_sec(current_chunk[0].start)
            current_end_sec = sub_to_sec(sub.end)
            next_start_sec = sub_to_sec(next_sub.start)
            
            duration = current_end_sec - chunk_start_sec
            silence_gap = next_start_sec - current_end_sec
            
            # УСЛОВИЕ РАЗРЕЗА: Набрали 15 сек ИЛИ смена сцены (тишина > 3 сек)
            if duration >= TARGET_DURATION_SEC or silence_gap >= MAX_SILENCE_SEC:
                # 1. Записываем текущий чанк
                chunk_text = ' '.join([clean_text(s.text) for s in current_chunk])
                cursor.execute('INSERT INTO subtitles (imdb_id, start_time, end_time, text) VALUES (?, ?, ?, ?)', 
                               (imdb_id, str(current_chunk[0].start), str(current_chunk[-1].end), chunk_text))
                cursor.execute('INSERT INTO subtitles_fts (rowid, text) VALUES (?, ?)', (cursor.lastrowid, chunk_text))
                
                # 2. Оставляем хвост (OVERLAP) для следующего чанка, чтобы не рвать контекст!
                if silence_gap < MAX_SILENCE_SEC:
                    current_chunk = current_chunk[-OVERLAP_LINES:]
                else:
                    # Если была смена сцены, нахлест не делаем (начинаем с чистого листа)
                    current_chunk = []
        else:
            # Последняя фраза в фильме
            chunk_text = ' '.join([clean_text(s.text) for s in current_chunk])
            cursor.execute('INSERT INTO subtitles (imdb_id, start_time, end_time, text) VALUES (?, ?, ?, ?)', 
                           (imdb_id, str(current_chunk[0].start), str(current_chunk[-1].end), chunk_text))
            cursor.execute('INSERT INTO subtitles_fts (rowid, text) VALUES (?, ?)', (cursor.lastrowid, chunk_text))

    return True

def main():
    print("🚀 СТАРТ: Ультимативный Конвейер...")
    banned_ids = get_banned_imdb_ids()
    if banned_ids is None: return
    rus_map = read_treasure_map(banned_ids)
    if not rus_map: return
        
    src_conn, dest_conn = sqlite3.connect(SOURCE_DB), setup_db()
    src_cursor, dest_cursor = src_conn.cursor(), dest_conn.cursor()
    
    sub_ids = list(rus_map.keys())
    processed = 0
    
    print("\n📥 ШАГ 3/3: Распаковка и УМНАЯ нарезка (по времени и сценам)...")
    
    with tqdm(total=len(sub_ids), desc="Извлечение") as pbar:
        for i in range(0, len(sub_ids), 900):
            batch = sub_ids[i:i+900]
            src_cursor.execute(f"SELECT num, file FROM subz WHERE num IN ({','.join('?'*len(batch))})", batch)
            
            for num, blob_data in src_cursor.fetchall():
                if not blob_data: 
                    pbar.update(1); continue
                
                info = rus_map[num]
                imdb_id = info['imdb_id']
                
                try:
                    with zipfile.ZipFile(io.BytesIO(blob_data)) as z:
                        srt_file = next((f for f in z.namelist() if f.endswith('.srt')), None)
                        if srt_file:
                            try: srt_text = z.read(srt_file).decode('utf-8')
                            except: srt_text = z.read(srt_file).decode('cp1251', errors='ignore')
                                
                            if process_srt_smart(srt_text, imdb_id, dest_cursor):
                                dest_cursor.execute('INSERT OR IGNORE INTO movies (imdb_id, type, season, episode) VALUES (?, ?, ?, ?)', 
                                                    (imdb_id, info['type'], info['season'], info['episode']))
                                processed += 1
                except: pass
                pbar.update(1)
            dest_conn.commit()

    dest_conn.close(); src_conn.close()
    print(f"\n🎉 БАЗА ИДЕАЛЬНО НАРЕЗАНА! Готовых релизов: {processed:,}")

if __name__ == '__main__':
    main()