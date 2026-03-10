import sqlite3
import os
import chromadb
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

# --- НАСТРОЙКИ ---
SQLITE_DB = 'movies_master.sqlite'
CHROMA_DIR = './chroma_db'
BATCH_SIZE = 1000 # По сколько фраз скармливать нейросети за один раз

def main():
    print("🧠 СТАРТ: Превращение текста в смысловые векторы (ИИ-индексация)...")
    
    if not os.path.exists(SQLITE_DB):
        print(f"🛑 ОШИБКА: База {SQLITE_DB} не найдена!")
        return

    # 1. Загружаем нейросеть (при первом запуске она скачается из интернета ~400мб)
    print("⏳ Загрузка языковой модели (paraphrase-multilingual)...")
    # Эта модель идеальна для поиска по смыслу на русском языке
    model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    
    # 2. Подключаемся к ChromaDB
    print("📁 Подключение к векторной базе ChromaDB...")
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    
    # Создаем или получаем "коллекцию" (аналог таблицы)
    # Используем косинусное сходство (cosine) - стандарт для текстового поиска
    collection = chroma_client.get_or_create_collection(
        name="subtitles_semantic",
        metadata={"hnsw:space": "cosine"} 
    )
    
    # 3. Подключаемся к SQLite
    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    
    # Считаем, сколько всего строк в базе
    cursor.execute("SELECT COUNT(*) FROM subtitles")
    total_rows = cursor.fetchone()[0]
    
    # Считаем, сколько мы УЖЕ векторизовали (для возобновления работы)
    current_count = collection.count()
    
    print("="*50)
    print(f"📊 Всего фраз в базе: {total_rows:,}")
    print(f"✅ Уже векторизовано: {current_count:,}")
    print("="*50)
    
    if current_count >= total_rows:
        print("🎉 Вся база уже векторизована! Можно переходить к поиску.")
        return

    # 4. Начинаем выборку тех строк, которых еще нет в Chroma
    # Берем строки, где ID больше, чем количество уже обработанных
    cursor.execute("SELECT id, text, imdb_id FROM subtitles WHERE id > ? ORDER BY id ASC", (current_count,))
    
    batch_ids = []
    batch_texts = []
    batch_metadatas = []
    
    # Прогресс-бар
    with tqdm(total=(total_rows - current_count), desc="Векторизация", unit="фраз") as pbar:
        for row in cursor:
            row_id, text, imdb_id = row
            
            # ChromaDB требует, чтобы ID были строками
            batch_ids.append(str(row_id))
            batch_texts.append(text)
            batch_metadatas.append({"imdb_id": imdb_id}) # Полезно для будущих фильтров
            
            # Как только накопили батч (1000 шт) - отправляем в нейросеть
            if len(batch_texts) >= BATCH_SIZE:
                # Магия ИИ: текст превращается в числа
                embeddings = model.encode(batch_texts, show_progress_bar=False).tolist()
                
                # Сохраняем в ChromaDB
                collection.add(
                    ids=batch_ids,
                    embeddings=embeddings,
                    documents=batch_texts,
                    metadatas=batch_metadatas
                )
                
                pbar.update(len(batch_texts))
                
                # Очищаем батчи для следующего круга
                batch_ids, batch_texts, batch_metadatas = [], [], []
                
        # 5. Сохраняем "хвост" (если осталось меньше 1000 строк)
        if batch_texts:
            embeddings = model.encode(batch_texts, show_progress_bar=False).tolist()
            collection.add(
                ids=batch_ids,
                embeddings=embeddings,
                documents=batch_texts,
                metadatas=batch_metadatas
            )
            pbar.update(len(batch_texts))

    conn.close()
    print("\n🎉 ГОТОВО! Ваш ИИ-Мозг полностью сформирован.")

if __name__ == '__main__':
    main()