import sqlite3
import zipfile
import io

DB_PATH = 'opensubs.db'

def check_nfo():
    print(f"🔍 Достаем 3 случайных .nfo файла из базы...\n")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Берем 3 архива с русскими субтитрами
    cursor.execute("SELECT name, file FROM subz WHERE name LIKE '%rus%' LIMIT 3;")
    rows = cursor.fetchall()

    for name, blob_data in rows:
        print("="*80)
        print(f"📁 АРХИВ: {name}")
        print("="*80)
        
        if blob_data:
            try:
                with zipfile.ZipFile(io.BytesIO(blob_data)) as z:
                    # Ищем файл .nfo внутри ZIP
                    nfo_file = next((f for f in z.namelist() if f.endswith('.nfo')), None)
                    
                    if nfo_file:
                        nfo_bytes = z.read(nfo_file)
                        # Читаем в кодировке cp437 (стандарт для ASCII-арта в NFO)
                        nfo_text = nfo_bytes.decode('cp437', errors='ignore')
                        print(nfo_text)
                    else:
                        print("❌ В этом архиве нет .nfo файла.")
            except Exception as e:
                print(f"❌ Ошибка чтения архива: {e}")
        print("\n")

    conn.close()

if __name__ == '__main__':
    check_nfo()