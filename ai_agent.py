#ai_agent.py
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
CUSTOM_API_BASE_URL = os.environ.get("CUSTOM_API_BASE_URL", "")
CUSTOM_MODEL_NAME = os.environ.get("CUSTOM_MODEL_NAME", "")
CUSTOM_API_KEY = os.environ.get("CUSTOM_API_KEY", "")

LLM_MODELS_FALLBACK = [
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "deepseek/deepseek-r1:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "google/gemini-2.5-pro-exp-03-25:free",
    "deepseek/deepseek-v3-base:free",
    "deepseek/deepseek-r1-zero:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "stepfun/step-3.5-flash:free",
    "zhipu/glm-4.5-air:free",
    "meta-llama/llama-4-maverick:free",
    "meta-llama/llama-4-scout:free",
    "qwen/qwen3-coder:free",
    "openrouter/auto"
]

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_ai_model.json")
PROMPTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts_history.json")

# ==========================================
# СИСТЕМА УПРАВЛЕНИЯ ПРОМПТАМИ
# ==========================================

DEFAULT_PROMPT = """Ты эксперт по поиску в базе кино-субтитров.
Пользователь описывает сцену. Твоя задача сгенерировать 100 КОРОТКИХ ФРАЗ (1-6 слов), которые актеры РЕАЛЬНО произносят в такой ситуации.
Правила:
1. Используй короткие бытовые фразы ("что за", "вот блин", "ха ха", "поехали").
2. Используй теги субтитров в скобках, если это действие ("[смеется]", "[чихает]", "[плачет]").
3. Никаких длинных описаний!"""

JSON_INSTRUCTION_SUFFIX = """

ОТВЕТЬ СТРОГО В ФОРМАТЕ JSON (Массив строк). БЕЗ МАРКДАУНА, БЕЗ ПОЯСНЕНИЙ:
["фраза 1", "фраза 2", "фраза 3", "фраза 4", "фраза 5"]"""

def load_prompts():
    """Загружает историю промптов или создает дефолтную"""
    if os.path.exists(PROMPTS_FILE):
        try:
            with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError): pass
    return {"current": "Базовый (По умолчанию)", "history": {"Базовый (По умолчанию)": DEFAULT_PROMPT}}

def save_prompts(data):
    """Сохраняет историю промптов"""
    try:
        with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (OSError, IOError): pass

def get_active_prompt():
    """Возвращает текущий активный промпт без JSON суффикса"""
    data = load_prompts()
    curr = data.get("current", "Базовый (По умолчанию)")
    return data.get("history", {}).get(curr, DEFAULT_PROMPT)

# ==========================================
# ОСНОВНОЙ КОД API
# ==========================================

def get_best_model_order():
    models = LLM_MODELS_FALLBACK.copy()
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                last_model = json.load(f).get("last_working_model")
                if last_model in models:
                    models.remove(last_model)
                    models.insert(0, last_model)
        except (json.JSONDecodeError, KeyError, FileNotFoundError): pass
    return models

def save_working_model(model_name):
    try:
        with open(CACHE_FILE, "w") as f: json.dump({"last_working_model": model_name}, f)
    except OSError: pass

class DummyWidget:
    def info(self, msg): print(f"[INFO] {msg}")
    def success(self, msg): print(f"[SUCCESS] {msg}")
    def warning(self, msg): print(f"[WARNING] {msg}")
    def error(self, msg): print(f"[ERROR] {msg}")

def _extract_content(response_json):
    """Safely extract content from OpenRouter-compatible response JSON."""
    try:
        choices = response_json.get('choices', [])
        if choices:
            return choices[0].get('message', {}).get('content', '').strip()
    except (KeyError, IndexError, AttributeError, TypeError):
        pass
    return None

def call_openrouter(system_prompt, user_prompt, log_widget=None):
    widget = log_widget if log_widget else DummyWidget()
    if not OPENROUTER_API_KEY and not (CUSTOM_API_BASE_URL and CUSTOM_MODEL_NAME):
        widget.error("❌ OPENROUTER_API_KEY не задан и кастомный эндпоинт не настроен.")
        return None

    combined_prompt = f"ИНСТРУКЦИЯ:\n{system_prompt}\n\nЗАДАЧА:\n{user_prompt}"

    if CUSTOM_API_BASE_URL and CUSTOM_MODEL_NAME:
        widget.info(f"⏳ Стучимся в кастомный эндпоинт: `{CUSTOM_MODEL_NAME}`...")
        custom_headers = {"Content-Type": "application/json"}
        if CUSTOM_API_KEY:
            custom_headers["Authorization"] = f"Bearer {CUSTOM_API_KEY}"
        payload = {"model": CUSTOM_MODEL_NAME, "messages": [{"role": "user", "content": combined_prompt}]}
        try:
            endpoint_url = f"{CUSTOM_API_BASE_URL.rstrip('/')}/chat/completions"
            resp = requests.post(endpoint_url, headers=custom_headers, data=json.dumps(payload), timeout=25)
            if resp.status_code == 200:
                widget.success(f"✅ Модель `{CUSTOM_MODEL_NAME}` (кастомный эндпоинт) ответила!")
                save_working_model(CUSTOM_MODEL_NAME)
                content = _extract_content(resp.json())
                if content:
                    return content
                widget.warning(f"⚠️ Кастомный эндпоинт вернул неожиданный формат: {resp.text[:200]}")
            else:
                try: err = resp.json().get("error", {}).get("message", resp.text)
                except json.JSONDecodeError: err = resp.text
                widget.warning(f"⚠️ Кастомный эндпоинт отказал {resp.status_code}: {err}")
        except Exception as e:
            widget.warning(f"⚠️ Кастомный эндпоинт недоступен: {str(e)}")

    if not OPENROUTER_API_KEY:
        widget.error("❌ OPENROUTER_API_KEY не задан.")
        return None

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}", 
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ai-director",
        "X-Title": "AI Director"
    }
    
    models_to_try = get_best_model_order()
    start_time = time.time()
    MAX_TOTAL_WAIT = 120  # seconds total budget for all model attempts

    for model in models_to_try:
        if time.time() - start_time > MAX_TOTAL_WAIT:
            widget.warning(f"⏰ Исчерпан общий бюджет времени ({MAX_TOTAL_WAIT}с). Прерываем перебор.")
            break
            
        widget.info(f"⏳ Стучимся в модель: `{model}`...")
        payload = {"model": model, "messages": [{"role": "user", "content": combined_prompt}]}
        
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, data=json.dumps(payload), timeout=25)
            if resp.status_code == 200:
                widget.success(f"✅ Модель `{model}` ответила!")
                save_working_model(model)
                content = _extract_content(resp.json())
                if content:
                    return content
                widget.warning(f"⚠️ Модель `{model}` вернула неожиданный формат")
            elif resp.status_code == 429: 
                widget.warning(f"⚠️ Очередь переполнена (429), пробуем следующую...")
                time.sleep(1)
            else:
                try: err = resp.json().get("error", {}).get("message", resp.text)
                except json.JSONDecodeError: err = resp.text
                widget.warning(f"⚠️ Отказ {resp.status_code}: {err}")
        except requests.Timeout:
            widget.warning(f"⚠️ Таймаут модели {model} (25с)")
            continue
        except requests.ConnectionError as e:
            widget.warning(f"⚠️ Ошибка соединения: {str(e)}")
            continue
        except Exception as e:
            widget.warning(f"⚠️ Ошибка сети: {str(e)}")
            continue
            
    widget.error("❌ Все модели недоступны.")
    return None

def generate_search_queries(query_text, log_widget=None):
    widget = log_widget if log_widget else DummyWidget()
    
    # 1. Берем пользовательскую стратегию
    base_strategy = get_active_prompt()
    # 2. Жестко приклеиваем форматирование (скрыто от юзера)
    system_p = base_strategy + JSON_INSTRUCTION_SUFFIX
    
    response = call_openrouter(system_p, f"Опиши сцену: {query_text}", log_widget)
    if response:
        try:
            clean = re.sub(r'```json\n?|```\n?', '', response).strip()
            queries = json.loads(clean)
            if isinstance(queries, list): return queries
        except (json.JSONDecodeError, ValueError):
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
        except (json.JSONDecodeError, ValueError, TypeError): pass
    return [item['id'] for item in fts_results[:5]]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", type=str, help="Текст для поиска (например: 'кто-то чихает')")
    args = parser.parse_args()
    
    print(f"\n🎬 ТЕСТ ИИ-АГЕНТА: '{args.query}'")
    queries = generate_search_queries(args.query)
    print("\n✅ ИТОГОВЫЙ РЕЗУЛЬТАТ (Сгенерированные фразы):")
    if queries:
        for q in queries: print(f"  - {q}")
    else:
        print("❌ Не удалось сгенерировать запросы.")