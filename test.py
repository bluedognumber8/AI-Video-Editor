import torch
from sentence_transformers import SentenceTransformer
import time

print("Версия PyTorch:", torch.__version__)
print("Видит ли CUDA:", torch.cuda.is_available())

if torch.cuda.is_available():
    print("Название карты:", torch.cuda.get_device_name(0))

print("Загрузка модели...")
model = SentenceTransformer('intfloat/multilingual-e5-base', device='cuda')

print("Пробный запуск...")
start = time.time()
vec = model.encode(["Тестовая фраза для разогрева GPU"])
print(f"Успешно! Заняло: {time.time() - start:.3f} сек.")