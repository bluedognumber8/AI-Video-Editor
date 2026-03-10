import sqlite3
import zipfile
import io
import re
import pysrt
import os
import glob
import gzip
from tqdm import tqdm

# --- НАСТРОЙКИ ---
DEST_DB = 'movies_master.sqlite'
BASICS_FILE = 'title.basics.tsv.gz'
IGNORE_DBS = ['opensubs.db', 'movies_master.sqlite'] # Игнорируем гиганта и саму себя

# НАСТРОЙКИ УМНОЙ НАРЕЗКИ (Точно как в основном скрипте!)
TARGET_DURATION_SEC = 15.0  
MAX_SILENCE_SEC = 3.0       
OVERLAP_LINES = 2           

BAD_GENRES = {'Documentary', 'Short', 'Adult', 'Reality-TV', 'Game-Show', 'Talk-Show', 'News'}
BAD_TYPES = {'short', 'videoGame', 'tvShort', 'tvPilot', 'tvSpecial'}

def get_banned_imdb_ids():
    print(f"🕵️‍♂️ Анализ IMDb ({BASICS_FILE}) и создание черного списка...")
    banned_ids = set()
    try:
        with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
            next(f)
            for line in tqdm(f, desc="Сбор мусора", total=10500000, unit=" строк"):
                parts = line.strip('\n').split('\t')
                if len(parts) < 9: continue
                tconst, t_type, genres = parts[0], parts[1], parts[8]
                if t_type in BAD_TYPES or set(genres.split(',')).intersection(BAD_GENRES):
                    banned_ids.add(tconst)
    except Exception:
        print("⚠️ Не удалось загрузить черный список. Качаем всё подряд.")
        return set()
    return banned_ids

def extract_imdb_id(nfo_text):
    if not re.search(r'Language\s*:\s*Russian', nfo_text, re.IGNORECASE): return None
    match = re.search(r'(tt\d+)', nfo_text)
    return match.group(1) if match else None

def sub_to_sec(sub_time):
    return sub_time.hours * 3600 + sub_time.minutes * 60 + sub_time.seconds + sub_time.milliseconds / 1000.0

def clean_text(text):
    text = text.replace('\n', ' ').strip()
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\{[^}]+\}', '', text)
    text = re.sub(r'^-\s*|^--\s*', '', text)
    return text

def process_srt_smart(srt_text, imdb_id, cursor):
    try: subs = pysrt.from_string(srt_text)
    except: return False
    if not subs: return False

    current_chunk = []
    for i, sub in enumerate(subs):
        text = clean_text(sub.text)
        if not text or "www.OpenSubtitles.org" in text or "VIP" in text: continue
            
        current_chunk.append(sub)
        
        if i < len(subs) - 1:
            next_sub = subs[i + 1]
            duration = sub_to_sec(sub.end) - sub_to_sec(current_chunk[0].start)
            silence_gap = sub_to_sec(next_sub.start) - sub_to_sec(sub.end)
            
            if duration >= TARGET_DURATION_SEC or silence_gap >= MAX_SILENCE_SEC:
                chunk_text = ' '.join([clean_text(s.text) for s in current_chunk])
                cursor.execute('INSERT INTO subtitles (imdb_id, start_time, end_time, text) VALUES (?, ?, ?, ?)', 
                               (imdb_id, str(current_chunk[0].start), str(current_chunk[-1].end), chunk_text))
                cursor.execute('INSERT INTO subtitles_fts (rowid, text) VALUES (?, ?)', (cursor.lastrowid, chunk_text))
                
                if silence_gap < MAX_SILENCE_SEC: current_chunk = current_chunk[-OVERLAP_LINES:]
                else: current_chunk = []
        else:
            chunk_text = ' '.join([clean_text(s.text) for s in current_chunk])
            cursor.execute('INSERT INTO subtitles (imdb_id, start_time, end_time, text) VALUES (?, ?, ?, ?)', 
                           (imdb_id, str(current_chunk[0].start), str(current_chunk[-1].end), chunk_text))
            cursor.execute('INSERT INTO subtitles_fts (rowid, text) VALUES (?, ?)', (cursor.lastrowid, chunk_text))
    return True

def process_single_db(db_path, dest_conn, dest_cursor, banned_ids):
    try:
        src_conn = sqlite3.connect(db_path)
        src_cursor = src_conn.cursor()
        
        src_cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in src_cursor.fetchall()]
        table_name = 'zipfiles' if 'zipfiles' in tables else 'subz' if 'subz' in tables else None
        blob_col = 'content' if table_name == 'zipfiles' else 'file'
            
        if not table_name: return 0
            
        src_cursor.execute(f"SELECT name, {blob_col} FROM {table_name} WHERE name LIKE '%rus%'")
        rows = src_cursor.fetchall()
        if not rows: return 0

        processed = 0
        with tqdm(total=len(rows), desc=f"📂 {db_path[:15]}", leave=False) as pbar:
            for name, blob_data in rows:
                if not blob_data: 
                    pbar.update(1); continue
                    
                try:
                    with zipfile.ZipFile(io.BytesIO(blob_data)) as z:
                        files = z.namelist()
                        nfo_file = next((f for f in files if f.endswith('.nfo')), None)
                        srt_file = next((f for f in files if f.endswith('.srt')), None)
                        
                        if nfo_file and srt_file:
                            nfo_text = z.read(nfo_file).decode('cp437', errors='ignore')
                            imdb_id = extract_imdb_id(nfo_text)
                            
                            # 🛑 МАГИЯ: ОТСЕКАЕМ МУСОРНЫЕ ФИЛЬМЫ ПО ЧЕРНОМУ СПИСКУ!
                            if imdb_id and imdb_id not in banned_ids:
                                srt_bytes = z.read(srt_file)
                                try: srt_text = srt_bytes.decode('utf-8')
                                except: srt_text = srt_bytes.decode('cp1251', errors='ignore')
                                    
                                if process_srt_smart(srt_text, imdb_id, dest_cursor):
                                    dest_cursor.execute('INSERT OR IGNORE INTO movies (imdb_id, type) VALUES (?, ?)', (imdb_id, 'movie'))
                                    processed += 1
                except: pass
                pbar.update(1)
                
        dest_conn.commit()
        src_conn.close()
        return processed
    except Exception: return 0

def main():
    print("🚀 СТАРТ: Умная подгрузка новых баз (2023-2026)...")
    banned_ids = get_banned_imdb_ids()
    
    dest_conn = sqlite3.connect(DEST_DB)
    dest_cursor = dest_conn.cursor()
    
    all_dbs = glob.glob("*.db")
    total_added = 0
    
    print("\n📥 Начинаю пакетную выкачку с умной нарезкой...")
    for db_path in sorted(all_dbs):
        if db_path in IGNORE_DBS: continue
        added = process_single_db(db_path, dest_conn, dest_cursor, banned_ids)
        total_added += added

    dest_conn.close()
    print("\n" + "="*50)
    print(f"🎉 ВСЕ НОВЫЕ БАЗЫ ОБРАБОТАНЫ! Добавлено чистых файлов: {total_added}")
    print("👉 ТЕПЕРЬ ЗАПУСКАЙТЕ: 02_enrich_metadata.py")

if __name__ == '__main__':
    main()