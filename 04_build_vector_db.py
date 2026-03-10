import sqlite3
import os
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from tqdm import tqdm
import torch

# --- НАСТРОЙКИ ---
SQLITE_DB = 'movies_master.sqlite'
CHROMA_DIR = './chroma_db'
BATCH_SIZE = 5000  # Идеальный баланс скорости и лимитов БД

def get_device():
    if torch.cuda.is_available():
        return 'cuda' 
    elif torch.backends.mps.is_available():
        return 'mps'  
    return 'cpu'      

def main():
    print("="*60)
    print(" 🧠 ИИ-ИНДЕКСАЦИЯ БАЗЫ (Турбо-Векторизация E5-Base) ")
    print("="*60)
    
    if not os.path.exists(SQLITE_DB):
        print(f"🛑 ОШИБКА: База {SQLITE_DB} не найдена!")
        return

    device = get_device()
    print(f"🚀 Оборудование для ИИ: [{device.upper()}]")

    print("⏳ Загрузка нейросети (intfloat/multilingual-e5-base)...")
    model_kwargs = {"torch_dtype": torch.float16} if device == 'cuda' else {}
    
    model = SentenceTransformer(
        'intfloat/multilingual-e5-base', 
        device=device,
        model_kwargs=model_kwargs
    )
    
    print("📁 Подключение к векторной базе ChromaDB...")
    chroma_client = chromadb.PersistentClient(
        path=CHROMA_DIR,
        settings=Settings(anonymized_telemetry=False) # Отключаем мусорные логи телеметрии
    )
    
    collection = chroma_client.get_or_create_collection(
        name="subtitles_semantic",
        metadata={"hnsw:space": "cosine"} 
    )
    
    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM subtitles")
    total_rows = cursor.fetchone()[0]
    
    current_count = collection.count()
    last_processed_id = 0
    
    if current_count > 0:
        print(f"🔄 Обнаружена существующая база. Уже векторизовано: {current_count:,}")
        cursor.execute("SELECT id FROM subtitles ORDER BY id LIMIT 1 OFFSET ?", (current_count - 1,))
        row = cursor.fetchone()
        if row:
            last_processed_id = row[0]
            
    print(f"📊 Всего фраз для обработки: {total_rows - current_count:,}")
    if current_count >= total_rows:
        print("🎉 Вся база уже векторизована!")
        return

    sql = '''
        SELECT s.id, s.text, m.imdb_id, m.genres, m.countries, m.rating, m.type
        FROM subtitles s
        JOIN movies m ON s.imdb_id = m.imdb_id
        WHERE s.id > ?
        ORDER BY s.id ASC
    '''
    cursor.execute(sql, (last_processed_id,))
    
    batch_ids = []
    batch_texts = []
    batch_metadatas = []
    
    with tqdm(total=(total_rows - current_count), desc="Векторизация", unit="фраз") as pbar:
        for row in cursor:
            s_id, text, imdb_id, genres, countries, rating, m_type = row
            
            e5_text = f"passage: {text}"
            
            batch_ids.append(str(s_id))
            batch_texts.append(e5_text)
            
            batch_metadatas.append({
                "imdb_id": imdb_id if imdb_id else "unknown",
                "genres": genres if genres else "unknown",
                "countries": countries if countries else "unknown",
                "rating": float(rating) if rating else 0.0,
                "type": m_type if m_type else "movie",
                "raw_text": text 
            })
            
            if len(batch_texts) >= BATCH_SIZE:
                embeddings = model.encode(batch_texts, batch_size=256, show_progress_bar=False, normalize_embeddings=True).tolist()
                
                collection.add(
                    ids=batch_ids,
                    embeddings=embeddings,
                    documents=batch_texts,
                    metadatas=batch_metadatas
                )
                
                pbar.update(len(batch_texts))
                batch_ids, batch_texts, batch_metadatas = [], [], []
                
        if batch_texts:
            embeddings = model.encode(batch_texts, batch_size=256, show_progress_bar=False, normalize_embeddings=True).tolist()
            collection.add(
                ids=batch_ids,
                embeddings=embeddings,
                documents=batch_texts,
                metadatas=batch_metadatas
            )
            pbar.update(len(batch_texts))

    conn.close()
    print("\n🎉 ГОТОВО! Ваша база превратилась в смысловой ИИ-движок.")

if __name__ == '__main__':
    main()