import sqlite3
import os

DB_PATH = 'opensubs.db'

def check_structure():
    if not os.path.exists(DB_PATH):
        print(f"❌ ОШИБКА: Файл {DB_PATH} не найден!")
        return

    print(f"🔍 Исследуем огромную базу: {DB_PATH} ({os.path.getsize(DB_PATH) / (1024**3):.2f} GB)\n")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()

    for table in tables:
        t_name = table[0]
        print("="*60)
        print(f"📁 ТАБЛИЦА: {t_name}")
        print("="*60)
        
        cursor.execute(f"PRAGMA table_info({t_name});")
        columns = cursor.fetchall()
        col_names = [c[1] for c in columns]
        
        print("Колонки:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
            
        print("\nПример данных (1 строка):")
        try:
            cursor.execute(f"SELECT * FROM {t_name} LIMIT 1;")
            row = cursor.fetchone()
            if row:
                for col_name, value in zip(col_names, row):
                    # Если это бинарный файл (ZIP-архив), не печатаем его, пишем только размер!
                    if isinstance(value, bytes):
                        print(f"  > {col_name}: <BLOB файл, размер: {len(value)} байт>")
                    else:
                        print(f"  > {col_name}: {str(value)[:100]}") # Печатаем максимум 100 символов
        except Exception as e:
            print(f"  [Ошибка]: {e}")
            
        print("\n")

    conn.close()

if __name__ == '__main__':
    check_structure()