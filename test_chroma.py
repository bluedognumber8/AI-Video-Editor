import os
import time
import chromadb
from sentence_transformers import SentenceTransformer
import torch

CHROMA_DIR = './chroma_db'
COLLECTION_NAME = 'subtitles_semantic'

print("=== ДИАГНОСТИКА CHROMADB ===")

# 1. Проверка пути
if not os.path.exists(CHROMA_DIR):
    print(f"❌ ПАПКА НЕ НАЙДЕНА: {CHROMA_DIR}")
    exit()
else:
    print(f"✅ Папка базы найдена. Размер: {sum(f.stat().st_size for f in os.scandir(CHROMA_DIR) if f.is_file()) / (1024*1024):.2f} MB")

# 2. Подключение к базе
print("\n⏳ Подключение к ChromaDB...")
try:
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    print("✅ Успешно подключено.")
except Exception as e:
    print(f"❌ ОШИБКА ПОДКЛЮЧЕНИЯ: {e}")
    exit()

# 3. Проверка коллекций
collections = client.list_collections()
col_names = [c.name for c in collections]
print(f"📂 Найдены коллекции: {col_names}")

if COLLECTION_NAME not in col_names:
    print(f"❌ КОЛЛЕКЦИЯ '{COLLECTION_NAME}' НЕ НАЙДЕНА!")
    exit()

collection = client.get_collection(name=COLLECTION_NAME)
count = collection.count()
print(f"📊 Количество субтитров (векторов) в базе: {count}")

if count == 0:
    print("❌ БАЗА ПУСТАЯ! Векторов нет. Поиск работать не будет.")
    exit()

# 4. Проверка структуры данных
print("\n🔍 Проверка структуры первого элемента...")
peek = collection.peek(1)
print(f"ID: {peek['ids'][0] if peek['ids'] else 'Нет'}")
print(f"Метаданные: {peek['metadatas'][0] if peek['metadatas'] else 'Нет'}")

# 5. Тестовый поиск
print("\n🧠 Загрузка нейросети для тестового запроса...")
device = 'cuda' if torch.cuda.is_available() else 'cpu'
model = SentenceTransformer('intfloat/multilingual-e5-base', device=device)

print("⏳ Векторизация запроса 'тест'...")
with torch.no_grad():
    vector = model.encode(["Тестовый запрос поиска"], normalize_embeddings=True).tolist()
    vector_dim = len(vector[0])
    print(f"✅ Вектор создан. Размерность: {vector_dim}")

print("🚀 Запуск поиска по базе...")
try:
    start = time.time()
    results = collection.query(
        query_embeddings=vector,
        n_results=3,
        include=["distances"]
    )
    print(f"✅ ПОИСК УСПЕШЕН! Заняло: {time.time() - start:.3f} сек.")
    print(f"Найденные ID: {results['ids'][0]}")
    print("=== БАЗА ПОЛНОСТЬЮ ИСПРАВНА ===")
except Exception as e:
    print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА ПОИСКА: {e}")
    print("Возможные причины: несовпадение размерности векторов (например, базу создавали другой моделью) или повреждение HNSW индекса.")