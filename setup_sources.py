import sqlite3
DB_NAME = 'movies_master.sqlite'

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()
# Удаляем старую глупую таблицу
cursor.execute("DROP TABLE IF EXISTS offsets")
# Создаем новую Умную таблицу
cursor.execute('''
CREATE TABLE IF NOT EXISTS movie_sources (
    imdb_id TEXT PRIMARY KEY,
    source_id TEXT,
    offset_sec REAL
)
''')
conn.commit()
conn.close()
print("✅ Новая таблица источников создана!")