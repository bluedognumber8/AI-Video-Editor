import sqlite3
import pandas as pd

# Подключаемся к вашей базе данных
db_path = 'rus.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("🔍 Изучаем структуру rus.db...\n")

# 1. Получаем список всех таблиц в базе
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()

if not tables:
    print("База пуста или имеет другой формат.")
else:
    for table_name in tables:
        t_name = table_name[0]
        print(f"📁 Таблица: '{t_name}'")
        
        # 2. Получаем структуру колонок для каждой таблицы
        cursor.execute(f"PRAGMA table_info({t_name});")
        columns = cursor.fetchall()
        print("   Колонки:")
        for col in columns:
            print(f"   - {col[1]} (Тип: {col[2]})")
            
        # 3. Выводим первые 3 строки из таблицы, чтобы посмотреть на живые данные
        print("\n   👀 Примеры данных (первые 3 строки):")
        try:
            df = pd.read_sql_query(f"SELECT * FROM {t_name} LIMIT 3", conn)
            print(df.to_string(index=False))
        except Exception as e:
            print(f"   [Ошибка чтения данных]: {e}")
        print("-" * 50)

conn.close()