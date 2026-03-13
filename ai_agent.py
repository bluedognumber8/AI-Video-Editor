# ai_agent.py
"""
========================================================================================
ТЗ И АРХИТЕКТУРА ИИ-ПАЙПЛАЙНА: "ДВУХЭТАПНЫЙ RAG (Retrieval-Augmented Generation)"
========================================================================================
Стратегия решения проблемы "Слепого ИИ", который не имеет прямого доступа к базе данных.

ШАГ 1: QUERY EXPANSION (Расширение запроса)
- Пользователь вводит контекст: "Человек радуется победе".
- ИИ (в роли переводчика) генерирует 5-10 коротких бытовых фраз или тегов, 
  которые реально могут встретиться в .srt файлах (например: "Ура!", "Мы сделали это", "Да!").

ШАГ 2: RETRIEVAL (Слепой поиск) - выполняется в app.py
- SQLite FTS (Full-Text Search) ищет эти фразы в базе за 10 мс.
- Получаем сырой список из 30-50 потенциальных совпадений (среди которых много мусора).

ШАГ 3: LLM AS A JUDGE (ИИ-Отборщик)
- Мы формируем компактный список найденных сцен (ID + Жанр + Текст) и отправляем обратно ИИ.
- ИИ "читает" субтитры, понимает контекст и выбирает ТОП-5 сцен, которые идеально
  соответствуют изначальному запросу пользователя.
- ИИ возвращает массив ID лучших сцен [12, 4, 1].

РЕЗУЛЬТАТ: Пользователь видит только самые точные и релевантные сцены.
========================================================================================
"""
# ai_agent.py
import os
import json
import re
import time
import requests
import argparse
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

LLM_MODELS_FALLBACK = [
    # Тир 1: Гениальные гиганты (Лучшее понимание контекста)
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    
    # Тир 2: Продвинутые Reasoning-модели (умеют "думать")
    "deepseek/deepseek-r1:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "google/gemini-2.5-pro-exp-03-25:free",
    "deepseek/deepseek-v3-base:free",
    "deepseek/deepseek-r1-zero:free",
    
    # Тир 3: Средний класс и быстрые надежные
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "stepfun/step-3.5-flash:free",
    "zhipu/glm-4.5-air:free",
    "meta-llama/llama-4-maverick:free",
    "meta-llama/llama-4-scout:free",
    "qwen/qwen3-coder:free",
    
    # Тир 4: Маленькие запасные модели
    "google/gemma-3-12b:free",
    "nvidia/llama-3.1-nemotron-nano-8b-v1:free",
    "nousresearch/deephermes-3-llama-3-8b-preview:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemma-3-4b:free",
    "qwen/qwen2.5-vl-3b-instruct:free",
    "moonshotai/kimi-vl-a3b-thinking:free",
    "nvidia/nemotron-nano-2-vl:free",
    "mistralai/devstral-small:free",
    
    # Тир 5: Роутеры (пусть OpenRouter сам решит)
    "openrouter/hunter-alpha",
    "openrouter/healer-alpha",
    "openrouter/optimus-alpha",
    "openrouter/quasar-alpha",
    "openrouter/free"
]

CACHE_FILE = "last_ai_model.json"

def get_best_model_order():
    models = LLM_MODELS_FALLBACK.copy()
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                last_model = json.load(f).get("last_working_model")
                if last_model in models:
                    models.remove(last_model)
                    models.insert(0, last_model)
        except: pass
    return models

def save_working_model(model_name):
    try:
        with open(CACHE_FILE, "w") as f: json.dump({"last_working_model": model_name}, f)
    except: pass

class DummyWidget:
    def info(self, msg): print(f"[INFO] {msg}")
    def success(self, msg): print(f"[SUCCESS] {msg}")
    def warning(self, msg): print(f"[WARNING] {msg}")
    def error(self, msg): print(f"[ERROR] {msg}")

def call_openrouter(system_prompt, user_prompt, log_widget=None):
    widget = log_widget if log_widget else DummyWidget()
    if not OPENROUTER_API_KEY:
        widget.error("❌ OPENROUTER_API_KEY не задан.")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}", 
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ai-director",
        "X-Title": "AI Director"
    }
    
    combined_prompt = f"ИНСТРУКЦИЯ:\n{system_prompt}\n\nЗАДАЧА:\n{user_prompt}"
    models_to_try = get_best_model_order()

    for model in models_to_try:
        widget.info(f"⏳ Стучимся в модель: `{model}`...")
        payload = {"model": model, "messages": [{"role": "user", "content": combined_prompt}]}
        
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, data=json.dumps(payload), timeout=25)
            if resp.status_code == 200:
                widget.success(f"✅ Модель `{model}` ответила!")
                save_working_model(model)
                return resp.json()['choices'][0]['message']['content'].strip()
            elif resp.status_code == 429: 
                widget.warning(f"⚠️ Очередь переполнена (429), пробуем следующую...")
                time.sleep(1)
            else:
                try: err = resp.json().get("error", {}).get("message", resp.text)
                except: err = resp.text
                widget.warning(f"⚠️ Отказ {resp.status_code}: {err}")
        except Exception as e:
            widget.warning(f"⚠️ Ошибка сети: {str(e)}")
            continue
            
    widget.error("❌ Все модели недоступны.")
    return None

def generate_search_queries(query_text, log_widget=None):
    widget = log_widget if log_widget else DummyWidget()
    system_p = """Ты эксперт по поиску в базе кино-субтитров.
Пользователь описывает сцену. Твоя задача сгенерировать 6-8 КОРОТКИХ фраз (1-3 слова), которые актеры РЕАЛЬНО произносят в такой ситуации.
Правила:
1. Используй короткие бытовые фразы ("что за", "вот блин", "ха ха", "поехали").
2. Используй теги субтитров в скобках, если это действие ("[смеется]", "[чихает]", "[плачет]").
3. Никаких длинных описаний!
ОТВЕТЬ СТРОГО В ФОРМАТЕ JSON (Массив строк):
["фраза 1", "фраза 2", "фраза 3"]"""
    
    response = call_openrouter(system_p, f"Опиши сцену: {query_text}", log_widget)
    if response:
        try:
            clean = re.sub(r'```json\n?|```\n?', '', response).strip()
            queries = json.loads(clean)
            if isinstance(queries, list): return queries
        except: 
            widget.error("❌ ИИ сломал формат JSON.")
    return []

def rank_database_results(user_query, fts_results, log_widget=None):
    if not fts_results: return []
    db_context = "Кандидаты из базы:\n"
    for item in fts_results:
        db_context += f"ID {item['id']}: [{item['genre']}] - {item['text']}\n"

    system_p = """Ты режиссер монтажа. Выбери из списка сцен те, которые ИДЕАЛЬНО подходят под запрос пользователя.
Отсеивай случайные совпадения слов (например, если искали "упал в лужу", а герой говорит "лужа крови" - отбрось это).
ОТВЕТЬ СТРОГО В ФОРМАТЕ JSON (Массив ID):
[0, 4, 7]"""
    
    response = call_openrouter(system_p, f"Запрос: '{user_query}'\n\n{db_context}", log_widget)
    if response:
        try:
            clean = re.sub(r'```json\n?|```\n?', '', response).strip()
            best_ids = json.loads(clean)
            if isinstance(best_ids, list): return [int(x) for x in best_ids]
        except: pass
    return [item['id'] for item in fts_results[:5]]

# =====================================================================
# ТЕСТОВЫЙ БЛОК ДЛЯ ЗАПУСКА В ТЕРМИНАЛЕ
# =====================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", type=str, help="Текст для поиска (например: 'кто-то чихает')")
    args = parser.parse_args()
    
    print(f"\n🎬 ТЕСТ ИИ-АГЕНТА: '{args.query}'")
    print("="*50)
    
    queries = generate_search_queries(args.query)
    
    print("\n✅ ИТОГОВЫЙ РЕЗУЛЬТАТ (Сгенерированные фразы):")
    if queries:
        for q in queries:
            print(f"  - {q}")
    else:
        print("❌ Не удалось сгенерировать запросы.")
    print("="*50)