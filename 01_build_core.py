import os
import re
import sqlite3
import pysrt

DUMP_FOLDER = './rus_subs'
DB_NAME = 'movies_master.sqlite'
CHUNK_SIZE = 3

def setup_database():
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME) # Начинаем с чистого листа
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # ТАБЛИЦА 1: Субтитры
    cursor.execute('''
    CREATE TABLE subtitles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        imdb_id TEXT,
        start_time TEXT,
        end_time TEXT,
        text TEXT
    )
    ''')
    
    # ТАБЛИЦА 2: Метаданные (Подготовлено для TMDB)
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
    )
    ''')
    
    # ТАБЛИЦА 3: Умный текстовый поиск
    cursor.execute('''
    CREATE VIRTUAL TABLE subtitles_fts 
    USING fts5(text, content='subtitles', content_rowid='id');
    ''')
    
    conn.commit()
    return conn

def parse_nfo_strict(nfo_path):
    """
    Строгий парсер NFO. 
    Возвращает imdb_id ТОЛЬКО если в файле есть 'Language : Russian'.
    """
    try:
        with open(nfo_path, 'r', encoding='cp437', errors='ignore') as f:
            content = f.read()
            
            # 1. Жесткая проверка на русский язык (учитываем любое количество пробелов)
            if not re.search(r'Language\s*:\s*Russian', content, re.IGNORECASE):
                return None
                
            # 2. Ищем IMDb ID
            match = re.search(r'(tt\d+)', content)
            return match.group(1) if match else None
    except:
        return None

def process_srt(srt_path, imdb_id, cursor):
    """Читает, чистит и нарезает субтитры"""
    try: 
        subs = pysrt.open(srt_path, encoding='utf-8')
    except:
        try: 
            subs = pysrt.open(srt_path, encoding='cp1251')
        except: 
            return False

    chunk_text = []
    chunk_start = None
    
    for i, sub in enumerate(subs):
        text = sub.text.replace('\n', ' ').strip()
        
        # Фильтр мусора
        if "www.OpenSubtitles.org" in text or "VIP" in text or "реклам" in text: 
            continue
            
        text = re.sub(r'<[^>]+>', '', text)
        if not text: 
            continue
            
        if not chunk_text: 
            chunk_start = str(sub.start)
            
        chunk_text.append(text)
        chunk_end = str(sub.end)
        
        # Если собрали чанк нужного размера или это последняя строчка
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

def main():
    print("🚀 ЭТАП 1: Сборка ядра базы данных (Строгий RU-режим)...")
    
    if not os.path.exists(DUMP_FOLDER):
        print(f"🛑 ОШИБКА: Папка {DUMP_FOLDER} не найдена!")
        return

    conn = setup_database()
    cursor = conn.cursor()
    
    processed_count = 0
    skipped_lang_count = 0
    
    for root, _, files in os.walk(DUMP_FOLDER):
        nfo_file = next((f for f in files if f.endswith('.nfo')), None)
        srt_file = next((f for f in files if f.endswith('.srt')), None)
        
        if nfo_file and srt_file:
            nfo_path = os.path.join(root, nfo_file)
            srt_path = os.path.join(root, srt_file)
            
            # Ищем ID только если язык 100% русский
            imdb_id = parse_nfo_strict(nfo_path)
            
            if imdb_id:
                success = process_srt(srt_path, imdb_id, cursor)
                if success:
                    # Резервируем место в таблице movies
                    cursor.execute('INSERT OR IGNORE INTO movies (imdb_id) VALUES (?)', (imdb_id,))
                    processed_count += 1
                    
                    if processed_count % 500 == 0:
                        print(f"⏳ Обраработано фильмов (RU): {processed_count}...")
                        conn.commit()
            else:
                skipped_lang_count += 1

    conn.commit()
    conn.close()
    
    print("\n" + "="*50)
    print(f"✅ База {DB_NAME} ИДЕАЛЬНО собрана!")
    print(f"🎬 Добавлено RU-фильмов: {processed_count}")
    print(f"🗑 Отбраковано (не русский язык или нет ID): {skipped_lang_count}")
    print("="*50)

if __name__ == '__main__':
    main()