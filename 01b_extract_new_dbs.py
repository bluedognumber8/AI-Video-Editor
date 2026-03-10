import sqlite3
import zipfile
import io
import re
import pysrt
import os
import glob

# --- НАСТРОЙКИ ---
DEST_DB = 'movies_master.sqlite'
CHUNK_SIZE = 3
# Базы, которые нужно проигнорировать
IGNORE_DBS = ['opensubs.db', 'movies_brain.sqlite', 'rus_old.db'] # Названия ваших старых/основных баз

def extract_imdb_id(nfo_text):
    """Ищет ttXXXXXXX в тексте NFO"""
    # ЖЕСТКАЯ ПРОВЕРКА: Берем только если указан русский язык (защита от мусора)
    if not re.search(r'Language\s*:\s*Russian', nfo_text, re.IGNORECASE):
        return None
    match = re.search(r'(tt\d+)', nfo_text)
    return match.group(1) if match else None

def process_srt_in_memory(srt_text, imdb_id, cursor):
    """Нарезает субтитры и кладет в базу"""
    try:
        subs = pysrt.from_string(srt_text)
    except Exception:
        return False

    chunk_text = []
    chunk_start = None
    
    for i, sub in enumerate(subs):
        text = sub.text.replace('\n', ' ').strip()
        if "www.OpenSubtitles.org" in text or "VIP" in text or "реклам" in text: 
            continue
            
        text = re.sub(r'<[^>]+>', '', text)
        if not text: 
            continue
            
        if not chunk_text: 
            chunk_start = str(sub.start)
            
        chunk_text.append(text)
        chunk_end = str(sub.end)
        
        if len(chunk_text) >= CHUNK_SIZE or i == len(subs) - 1:
            full_text = ' '.join(chunk_text)
            cursor.execute('''
                INSERT INTO subtitles (imdb_id, start_time, end_time, text) 
                VALUES (?, ?, ?, ?)
            ''', (imdb_id, chunk_start, chunk_end, full_text))
            
            cursor.execute('''
                INSERT INTO subtitles_fts (rowid, text) 
                VALUES (?, ?)
            ''', (cursor.lastrowid, full_text))
            
            chunk_text = []
    return True

def process_single_db(db_path, dest_conn, dest_cursor):
    print(f"\n📂 Открываю базу: {db_path}...")
    try:
        src_conn = sqlite3.connect(db_path)
        src_cursor = src_conn.cursor()
        
        # 1. ПРОВЕРЯЕМ, КАК НАЗЫВАЕТСЯ ТАБЛИЦА (Умный поиск)
        src_cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in src_cursor.fetchall()]
        
        table_name = None
        blob_col = None
        
        if 'zipfiles' in tables:
            table_name = 'zipfiles'
            blob_col = 'content'
        elif 'subz' in tables:
            table_name = 'subz'
            blob_col = 'file'
            
        if not table_name:
            print(f"   ⚠️ Пропуск: нет подходящей таблицы с архивами.")
            src_conn.close()
            return 0
            
        # 2. ИЩЕМ ТОЛЬКО РУССКИЕ АРХИВЫ
        # Используем динамические имена таблиц и колонок
        query = f"SELECT name, {blob_col} FROM {table_name} WHERE name LIKE '%rus%'"
        src_cursor.execute(query)
        
        processed = 0
        for name, blob_data in src_cursor:
            if not blob_data: continue
                
            try:
                with zipfile.ZipFile(io.BytesIO(blob_data)) as z:
                    file_names = z.namelist()
                    nfo_file = next((f for f in file_names if f.endswith('.nfo')), None)
                    srt_file = next((f for f in file_names if f.endswith('.srt')), None)
                    
                    if nfo_file and srt_file:
                        nfo_text = z.read(nfo_file).decode('cp437', errors='ignore')
                        imdb_id = extract_imdb_id(nfo_text)
                        
                        if imdb_id:
                            srt_bytes = z.read(srt_file)
                            try: srt_text = srt_bytes.decode('utf-8')
                            except: srt_text = srt_bytes.decode('cp1251', errors='ignore')
                                
                            if process_srt_in_memory(srt_text, imdb_id, dest_cursor):
                                # Резервируем место в таблице movies
                                dest_cursor.execute('INSERT OR IGNORE INTO movies (imdb_id, type) VALUES (?, ?)', (imdb_id, 'movie'))
                                processed += 1
                                
                                if processed % 100 == 0:
                                    print(f"   ⏳ Добавлено новых сабов: {processed}...")
                                    dest_conn.commit()
            except zipfile.BadZipFile:
                pass # Игнорируем битые архивы
            except Exception:
                pass
                
        dest_conn.commit()
        src_conn.close()
        print(f"   ✅ Завершено! Добавлено: {processed} субтитров из {db_path}")
        return processed
        
    except Exception as e:
        print(f"   ❌ Ошибка при чтении {db_path}: {e}")
        return 0

def main():
    print("🚀 СТАРТ: Пакетная загрузка субтитров (Универсальный режим)...")
    
    # Подключаемся к мастер-базе
    dest_conn = sqlite3.connect(DEST_DB)
    dest_cursor = dest_conn.cursor()
    
    # Ищем все .db файлы в текущей папке
    all_dbs = glob.glob("*.db")
    
    total_added = 0
    for db_path in sorted(all_dbs):
        if db_path in IGNORE_DBS:
            continue
        
        added = process_single_db(db_path, dest_conn, dest_cursor)
        total_added += added

    dest_conn.close()
    
    print("\n" + "="*50)
    print(f"🎉 ВСЕ БАЗЫ ОБРАБОТАНЫ! Всего новых субтитров добавлено: {total_added}")
    print("="*50)
    print("👉 Не забудьте запустить скрипты обогащения метаданных для новых ID!")

if __name__ == '__main__':
    main()