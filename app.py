from dotenv import load_dotenv
load_dotenv()  # WHY: загружает переменные из .env в os.environ
import os

# --- Указываем путь к бинарнику из AUR ---
if "TORRSERVER_PATH" not in os.environ:
    os.environ["TORRSERVER_PATH"] = "torrserver"

import streamlit as st
import sqlite3
import pandas as pd
import re
import datetime
import subprocess
import sys
import time
import json
import requests
import urllib.parse
import logging
import html as html_module
from collections import namedtuple
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# --- ИНИЦИАЛИЗАЦИЯ МОРФОЛОГИИ ---
try:
    import nltk
    from nltk.stem.snowball import SnowballStemmer
    try: nltk.data.find('corpora/wordnet')
    except LookupError: nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True)
    try: nltk.data.find('tokenizers/punkt')
    except LookupError: nltk.download('punkt', quiet=True)
    ru_stemmer = SnowballStemmer("russian")
except Exception as e:
    ru_stemmer = None

try:
    import pymorphy3
    morph = pymorphy3.MorphAnalyzer()
except ImportError:
    morph = None

# --- НАСТРОЙКИ ---
DB_NAME = 'movies_master.sqlite'
CLIPS_DIR = 'clips'
SETTINGS_FILE = "user_settings.json"
RESULTS_PER_PAGE = 10
MAX_ACTIVE_DOWNLOADS = 5

os.makedirs(CLIPS_DIR, exist_ok=True)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# Обновленный список бесплатных моделей по вашему списку (от лучших к запасным)
LLM_MODELS_FALLBACK = [
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "google/gemma-3-12b:free",
    "google/gemma-3-4b:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemini-2.5-flash:free",
    "venice/venice-uncensored:free"
]

st.set_page_config(page_title="AI-Режиссер Монтажа", page_icon="🎬", layout="wide")

# =====================================================================
# 🎨 CSS ХАКИ ДЛЯ STICKY ПОИСКА И КРАСОТЫ
# Вставляем стили скрыто, чтобы они не ломали порядок блоков
# =====================================================================
st.markdown("""
    <style>
        /* Делаем ПЕРВЫЙ блок в главном контейнере прилипающим (это будет наша панель поиска) */
        .main .block-container > div[data-testid="stVerticalBlock"] > div:first-child {
            position: sticky;
            top: 2.875rem; /* Отступ под системную шапку Streamlit */
            background-color: var(--primary-background-color);
            z-index: 999;
            padding-top: 10px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(128,128,128, 0.2);
            margin-bottom: 15px;
        }
        /* Убираем лишние отступы у кнопок в поиске */
        .stButton button { margin-top: 0px; }
    </style>
""", unsafe_allow_html=True)

SearchRow = namedtuple('SearchRow', ['title_ru', 'year', 'genres', 'rating', 'type', 'season', 'episode', 'start_time', 'end_time', 'text', 'imdb_id', 'countries', 'title_original', 'sub_id'])

# =====================================================================
# 💾 БАЗА ДАННЫХ ИЗБРАННОГО
# =====================================================================
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS favorites (imdb_id TEXT, sub_id INTEGER, added_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(imdb_id, sub_id))")

init_db()

def toggle_favorite(imdb_id, sub_id):
    with sqlite3.connect(DB_NAME) as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM favorites WHERE imdb_id=? AND sub_id=?", (imdb_id, sub_id))
        if cur.fetchone():
            cur.execute("DELETE FROM favorites WHERE imdb_id=? AND sub_id=?", (imdb_id, sub_id))
            return False
        else:
            cur.execute("INSERT INTO favorites (imdb_id, sub_id) VALUES (?, ?)", (imdb_id, sub_id))
            return True

def get_all_favorites():
    try:
        with sqlite3.connect(DB_NAME) as conn:
            sql = """
                SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
                       s.start_time, s.end_time, s.text, m.imdb_id, m.countries, m.title_original, s.id
                FROM favorites f
                JOIN subtitles s ON f.sub_id = s.id
                JOIN movies m ON f.imdb_id = m.imdb_id
                ORDER BY f.added_at DESC
            """
            cur = conn.cursor()
            cur.execute(sql)
            return [(SearchRow(*row), 100.0) for row in cur.fetchall()]
    except: return []

def is_favorite(imdb_id, sub_id):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM favorites WHERE imdb_id=? AND sub_id=?", (imdb_id, sub_id))
            return bool(cur.fetchone())
    except: return False

# =====================================================================
# 💾 ПЕРСИСТЕНТНЫЕ НАСТРОЙКИ
# =====================================================================
DEFAULT_SETTINGS = {
    "search_mode": "По словам (Быстро ⚡️)", "specific_movie": "Все фильмы",
    "t_type": "Все", "c_filter": "Все", "genre_filter": "Любой",
    "min_rating": 0.0, "pad_start": 30.0, "pad_end": 30.0,
    "source_pref": "all", "ai_scenario": "🎬 Стандартный (поиск по описанию)"
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f: return {**DEFAULT_SETTINGS, **json.load(f)}
        except: pass
    return dict(DEFAULT_SETTINGS)

def save_settings(settings_dict):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f: json.dump(settings_dict, f, ensure_ascii=False, indent=2)
    except: pass

if "settings_loaded" not in st.session_state:
    st.session_state.settings_loaded = True
    for key, val in load_settings().items(): st.session_state[key] = val

if "search_results" not in st.session_state: st.session_state.search_results = []
if "search_offset" not in st.session_state: st.session_state.search_offset = 0
if "last_query" not in st.session_state: st.session_state.last_query = ""
if "search_query_input" not in st.session_state: st.session_state.search_query_input = ""
if "exact_match_checkbox" not in st.session_state: st.session_state.exact_match_checkbox = False
if "trigger_search" not in st.session_state: st.session_state.trigger_search = False
if "llm_ideas" not in st.session_state: st.session_state.llm_ideas = []
if "active_downloads" not in st.session_state: st.session_state.active_downloads = {}

def trigger_new_search():
    if st.session_state.search_query_input.strip():
        st.session_state.last_query = st.session_state.search_query_input
        st.session_state.search_offset = 0
        st.session_state.trigger_search = True

def on_settings_change():
    save_settings({k: st.session_state[k] for k in DEFAULT_SETTINGS if k in st.session_state})
    st.session_state.search_offset = 0
    if st.session_state.get("search_query_input", "").strip(): trigger_new_search()
    else: st.session_state.search_results = []

def on_download_settings_change():
    save_settings({k: st.session_state[k] for k in DEFAULT_SETTINGS if k in st.session_state})

# =====================================================================
# 🔧 УТИЛИТЫ БЕЗОПАСНОСТИ И ТАЙМИНГОВ
# =====================================================================
def sanitize_filename(name: str, max_length: int = 50) -> str:
    safe = re.sub(r'[^\w\s\-]', '', name, flags=re.UNICODE).strip().replace(" ", "_")
    return safe[:max_length] if safe else "unnamed"

def validate_path_within_dir(file_path: str, allowed_dir: str) -> str:
    abs_allowed, abs_path = os.path.abspath(allowed_dir), os.path.abspath(file_path)
    if not abs_path.startswith(abs_allowed + os.sep) and abs_path != abs_allowed: raise ValueError("Path traversal!")
    return abs_path

def sanitize_html_text(text: str) -> str: return html_module.escape(str(text))

def srt_to_seconds(srt_time_str):
    try:
        srt_time_str = str(srt_time_str).strip().replace('.', ',')
        time_part, ms_part = srt_time_str.split(',', 1) if ',' in srt_time_str else (srt_time_str, '0')
        parts = list(map(int, time_part.split(':')))
        if len(parts) == 3: h, m, s = parts
        elif len(parts) == 2: h, m, s = 0, parts[0], parts[1]
        else: h, m, s = 0, 0, parts[0]
        return h * 3600 + m * 60 + s + int(ms_part) / 1000.0
    except: return 0.0

def seconds_to_hms(seconds): return str(datetime.timedelta(seconds=max(0, int(seconds))))
def safe_int(val, default=0): 
    try: return int(val) if val is not None else default
    except: return default

# =====================================================================
# 🧠 ИИ И ПОИСКОВЫЕ ФУНКЦИИ
# =====================================================================
@st.cache_data
def get_movie_titles():
    try:
        with sqlite3.connect(DB_NAME) as conn: return pd.read_sql_query("SELECT DISTINCT title_ru FROM movies ORDER BY title_ru ASC", conn)['title_ru'].tolist()
    except: return []

def get_sync_subtitles(imdb_id, anchor_sub_id, phrase=""):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            if phrase:
                cur.execute(
                    "SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? AND text LIKE ? ORDER BY ABS(id - ?) ASC LIMIT 50", 
                    (imdb_id, f"%{phrase}%", anchor_sub_id)
                )
            else:
                cur.execute(
                    "SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? AND id BETWEEN ? AND ? ORDER BY start_time", 
                    (imdb_id, anchor_sub_id - 20, anchor_sub_id + 20)
                )
            return cur.fetchall()
    except: return []

def get_surrounding_context(imdb_id, target_start_sec, target_sub_id, window_sec=90):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            cur.execute("SELECT start_time, text FROM subtitles WHERE imdb_id = ? AND id BETWEEN ? AND ? ORDER BY start_time ASC", (imdb_id, target_sub_id - 100, target_sub_id + 100))
            rows = cur.fetchall()
    except: return []
    items, seen = [], set()
    for r_st, r_tx in rows:
        r_sec = srt_to_seconds(r_st)
        if abs(r_sec - target_start_sec) <= window_sec and r_tx not in seen:
            seen.add(r_tx)
            items.append({"sec": r_sec, "label": f"[{str(r_st)[:8]}] {str(r_tx)[:70]}", "text": r_tx, "time_str": str(r_st)[:8], "is_target": abs(r_sec - target_start_sec) < 5})
    return items

def get_saved_source_info(imdb_id):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='movie_sources'")
            if not cur.fetchone(): return None, 0.0
            cur.execute("SELECT source_id, offset_sec FROM movie_sources WHERE imdb_id = ?", (imdb_id,))
            row = cur.fetchone()
            return (row[0], row[1]) if row else (None, 0.0)
    except: return None, 0.0

def save_source_info(imdb_id, source_id, offset_sec):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS movie_sources (imdb_id TEXT PRIMARY KEY, source_id TEXT, offset_sec REAL)")
            conn.execute("INSERT OR REPLACE INTO movie_sources VALUES (?, ?, ?)", (imdb_id, source_id, offset_sec))
    except: pass

def delete_source_info(imdb_id):
    try:
        with sqlite3.connect(DB_NAME) as conn: conn.execute("DELETE FROM movie_sources WHERE imdb_id = ?", (imdb_id,))
    except: pass

def manual_video_search(query, min_duration):
    res = []
    try:
        sub = subprocess.run(["yt-dlp", f"ytsearch10:{query}", "--dump-json", "--no-warnings"], capture_output=True, text=True, timeout=15)
        for l in sub.stdout.strip().split("\n"):
            if l:
                try:
                    v = json.loads(l)
                    if v.get("duration", 0) >= min_duration: res.append({"label": f"🔴 [YT] {v.get('title')} ({seconds_to_hms(v.get('duration'))})", "id": f"youtube:{v.get('id')}"})
                except: pass
    except: pass
    try:
        url = f"https://rutube.ru/api/search/video/?query={urllib.parse.quote(query)}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        for v in resp.json().get("results", []):
            dur_str = v.get("duration", "0")
            dur_sec = srt_to_seconds(dur_str) if ":" in str(dur_str) else int(dur_str)
            if dur_sec == 0 or dur_sec >= min_duration:
                vid_url = v.get("video_url")
                if vid_url:
                    if not vid_url.startswith("http"): vid_url = "https://rutube.ru" + vid_url
                    res.append({"label": f"🟢 [RuTube] {v.get('title')} ({dur_str})", "id": f"rutube:{vid_url}"})
    except: pass
    return res

def build_sql_filters(min_rating, t_type, country_filter, genre_filter, specific_movie, params):
    sql = ""
    if min_rating > 0:
        sql += " AND m.rating >= ?"
        params.append(min_rating)
    if t_type != "Все":
        sql += " AND m.type = ?"
        params.append("movie" if t_type == "Фильмы" else "tv")
    if country_filter == "Наше (RU/SU)":
        sql += " AND (m.countries LIKE '%RU%' OR m.countries LIKE '%SU%')"
    elif country_filter == "Зарубежное":
        sql += " AND (m.countries NOT LIKE '%RU%' AND m.countries NOT LIKE '%SU%' OR m.countries IS NULL)"
    if genre_filter != "Любой":
        sql += " AND m.genres LIKE ?"
        params.append(f"%{genre_filter}%")
    if specific_movie != "Все фильмы":
        sql += " AND m.title_ru = ?"
        params.append(specific_movie)
    return sql

def perform_search(query_text, search_mode, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match, log_widget=None):
    if not query_text or not query_text.strip(): return []
    if search_mode == "По словам (Быстро ⚡️)":
        if log_widget: log_widget.write("⚡ Выполняем поиск по базе субтитров...")
        return _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match)
    else:
        return _search_ai_agent(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match, log_widget)

def _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match):
    if not os.path.exists(DB_NAME): return []
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
    except: return []

    if query_text.startswith('"') and query_text.endswith('"'):
        exact_match = True
        query_text = query_text.strip('"')

    if exact_match:
        clean_q = re.sub(r'[^\w\s]', '', query_text).strip()
        if not clean_q: return []
        search_query = f'"{clean_q}"'
    else:
        search_terms = []
        for word in re.findall(r'\w+', query_text):
            word = word.lower()
            if re.search(r'[а-яё]', word):
                variants = {word}
                if morph:
                    lemma = morph.parse(word)[0].normal_form
                    variants.add(lemma)
                if ru_stemmer:
                    variants.add(ru_stemmer.stem(word))
                    if morph: variants.add(ru_stemmer.stem(lemma))
                if len(word) >= 7: variants.add(word[:5])
                elif len(word) >= 5: variants.add(word[:4])
                group = []
                for v in variants:
                    group.append(f"{v}*")
                    if 'е' in v: group.append(f"{v.replace('е', 'ё')}*")
                    if 'ё' in v: group.append(f"{v.replace('ё', 'е')}*")
                search_terms.append(f"({' OR '.join(set(group))})")
            else:
                search_terms.append(f"{word}*")

        if not search_terms: return []
        search_query = " AND ".join(search_terms)
        
    params = [search_query]

    sql = f"""
        WITH top_matches AS (
            SELECT rowid FROM subtitles_fts WHERE text MATCH ? ORDER BY rank LIMIT 2000
        ),
        ranked AS (
            SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
                   s.start_time, s.end_time, s.text, m.imdb_id, m.countries, 
                   m.title_original, s.id,
                   ROW_NUMBER() OVER (PARTITION BY m.imdb_id, SUBSTR(LTRIM(LOWER(s.text), ' .-,:;!?…"'''), 1, 20) ORDER BY s.start_time ASC) AS rn
            FROM top_matches tm 
            JOIN subtitles s ON s.id = tm.rowid 
            JOIN movies m ON s.imdb_id = m.imdb_id 
            WHERE 1=1
    """
    
    sql += build_sql_filters(min_rating, t_type, country_filter, genre_filter, specific_movie, params)
    sql += f") SELECT * FROM ranked WHERE rn = 1 ORDER BY rating DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [(SearchRow(*row[:14]), 100.0) for row in rows]
    except: return []
    finally: conn.close()

def extract_json_from_llm(text):
    text = re.sub(r'```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'```\n?', '', text)
    try:
        match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
        if match: return json.loads(match.group(0))
    except: pass
    return []

def call_openrouter(system_prompt, user_prompt, log_widget=None):
    if not OPENROUTER_API_KEY:
        if log_widget: log_widget.error("❌ OPENROUTER_API_KEY не задан.")
        return None

    # Обязательные заголовки для бесплатных моделей
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}", 
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ai-director",
        "X-Title": "AI Director"
    }
    
    combined_prompt = f"ИНСТРУКЦИЯ:\n{system_prompt}\n\nЗАДАЧА:\n{user_prompt}"

    for model in LLM_MODELS_FALLBACK:
        if log_widget: log_widget.write(f"⏳ Пробуем ИИ: `{model}`...")
        try:
            payload = json.dumps({"model": model, "messages": [{"role": "user", "content": combined_prompt}], "temperature": 0.5})
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, data=payload, timeout=20)
            if resp.status_code == 200:
                if log_widget: log_widget.write(f"✅ Успешно ({model})!")
                return resp.json()['choices'][0]['message']['content'].strip()
            elif resp.status_code == 429: 
                if log_widget: log_widget.write("⚠️ Лимит запросов (429), ждем 2 сек...")
                time.sleep(2)
            else:
                if log_widget: log_widget.write(f"⚠️ Ошибка сервера: {resp.status_code}")
        except Exception as e:
            if log_widget: log_widget.write(f"⚠️ Сетевая ошибка: {str(e)}")
            continue
            
    if log_widget: log_widget.error("❌ Все ИИ модели недоступны.")
    return None

def _search_ai_agent(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match, log_widget=None):
    if offset == 0:
        scenario = st.session_state.get("ai_scenario", "🎬 Стандартный")
        if "Свадебный" in scenario:
            system_p = "Ты гениальный комедийный режиссер видеомонтажа. Гость на свадьбе говорит ОДНО слово (пожелание). Придумай 3 смешные, абсурдные или легендарные цитаты из известного кино (СССР/Россия), которые станут идеальной реакцией на это слово.\nОТВЕТЬ СТРОГО В ФОРМАТЕ JSON (массив из 3 объектов, без форматирования кода):\n[{\"quote\": \"цитата\", \"movie\": \"фильм\", \"reasoning\": \"задумка\"}]"
        else:
            system_p = "Ты режиссер. Пользователь описывает сцену. Придумай 3 короткие фразы.\nОТВЕТЬ СТРОГО В ФОРМАТЕ JSON (массив из 3 объектов, без форматирования кода):\n[{\"quote\": \"цитата\", \"movie\": \"жанр\", \"reasoning\": \"почему\"}]"
        
        llm_response = call_openrouter(system_p, f"Запрос: {query_text}", log_widget)
        if llm_response:
            st.session_state.llm_ideas = extract_json_from_llm(llm_response)
            # ВАЖНО: Если ИИ выдал текст, но не JSON - показываем этот текст юзеру!
            if not st.session_state.llm_ideas and log_widget:
                log_widget.error(f"❌ ИИ ответил текстом, а не JSON:\n\n{llm_response}")
        else:
            st.session_state.llm_ideas = []

    results = []
    if getattr(st.session_state, 'llm_ideas', []):
        if log_widget: log_widget.write("⚡ **Шаг 2:** Ищем сгенерированные фразы в базе...")
        for idea in st.session_state.llm_ideas:
            if idea.get('quote'):
                results.extend(_search_fts(idea['quote'], limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match))
    else:
        if log_widget: log_widget.write("⚠️ ИИ не сработал. Переходим к прямому поиску...")
        results = _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match)

    seen_phrases, final_res = set(), []
    for row_tuple in sorted(results, key=lambda x: x[1], reverse=True):
        row = row_tuple[0]
        clean_text = re.sub(r'[^\w\s]', '', str(row.text).lower()).strip()[:20]
        unique_key = (row.imdb_id, clean_text)
        if unique_key not in seen_phrases:
            seen_phrases.add(unique_key)
            final_res.append(row_tuple)

    return final_res[:limit]

# =====================================================================
# 📥 МЕНЕДЖЕР ЗАГРУЗОК — ЛОГИКА
# =====================================================================
def cleanup_finished_downloads():
    for task_id, task in list(st.session_state.active_downloads.items()):
        proc = task.get('process')
        if proc and proc.poll() is not None:
            log_handle = task.get('_log_handle')
            if log_handle and not log_handle.closed:
                try: log_handle.close()
                except OSError: pass
            task['_log_handle'] = None

def count_running_downloads():
    return len([t for t in st.session_state.active_downloads.values() if t['status'] == 'running'])

def get_clean_status_from_log(log_lines):
    if not log_lines: return "Инициализация..."
    for line in reversed(log_lines):
        line = line.strip()
        if not line: continue
        if "time=" in line:
            m = re.search(r'time=(\d{2}:\d{2}:\d{2})', line)
            if m: return f"🎥 Кодирование: {m.group(1)}"
        elif "Пиров:" in line or "Статус:" in line: return "📡 " + line.split("]")[-1].strip()
        elif "Выбран файл" in line: return "📁 Анализ торрента..."
    return "⏳ В процессе..."

if hasattr(st, "fragment"):
    auto_updating_fragment = st.fragment(run_every=2)
else:
    def auto_updating_fragment(func): return func

# =====================================================================
# 🎨 САЙДБАР
# =====================================================================
with st.sidebar:
    st.header("⚙️ Режим Поиска")
    search_mode = st.radio("Как ищем?", ["По словам (Быстро ⚡️)", "ИИ-Агент (OpenRouter 🤖)"], key="search_mode", on_change=on_settings_change)

    if search_mode == "ИИ-Агент (OpenRouter 🤖)":
        st.selectbox("🎭 Режиссерский сценарий:", ["🎬 Стандартный (поиск по описанию)", "💍 Свадебный (юмор и реакция на 1 слово)"], key="ai_scenario", on_change=on_settings_change)

    st.markdown("---")
    st.header("🎛 Фильтры")
    all_movies = ["Все фильмы"] + get_movie_titles()
    current_movie_idx = all_movies.index(st.session_state["specific_movie"]) if st.session_state.get("specific_movie") in all_movies else 0

    st.selectbox("📌 В кино:", all_movies, index=current_movie_idx, key="specific_movie", on_change=on_settings_change)
    st.radio("🎞 Тип медиа:", ["Все", "Фильмы", "Сериалы"], horizontal=True, key="t_type", on_change=on_settings_change)
    st.radio("🌍 Страна:", ["Все", "Наше (RU/SU)", "Зарубежное"], key="c_filter", on_change=on_settings_change)
    st.selectbox("🎭 Жанр:", ["Любой", "Comedy", "Drama", "Action", "Sci-Fi", "Horror", "Romance", "Crime"], key="genre_filter", on_change=on_settings_change)
    st.slider("⭐️ Мин. рейтинг IMDb:", 0.0, 10.0, step=0.1, key="min_rating", on_change=on_settings_change)

    st.markdown("---")
    st.header("✂️ Хронометраж")
    pad_start = st.number_input("Секунд ДО фразы:", 0.0, 120.0, step=5.0, key="pad_start", on_change=on_download_settings_change)
    pad_end = st.number_input("Секунд ПОСЛЕ фразы:", 0.0, 120.0, step=5.0, key="pad_end", on_change=on_download_settings_change)
    source_pref = st.radio("🌐 Источник:", ["all", "youtube", "rutube", "torrent"], format_func=lambda x: {"all": "Везде (YT -> Tor)", "youtube": "Только YT", "rutube": "Только RuTube", "torrent": "Только Torrent (TorrServer ⚡️)"}[x], key="source_pref", on_change=on_download_settings_change)

# =====================================================================
# ГЛАВНЫЙ ЭКРАН - 1. СТРОКА ПОИСКА (БЛОК 1)
# =====================================================================
# Этот блок визуально первый в main container, к нему применятся стили sticky top
search_container = st.container()
with search_container:
    c_search, c_opt, c_btn = st.columns([5, 2, 1])
    with c_search:
        st.text_input("🔍 Поиск фрагмента:", placeholder="Введите цитату (для точной фразы используйте кавычки \"\")", key="search_query_input", on_change=trigger_new_search, label_visibility="collapsed")
    with c_opt:
        st.checkbox("🎯 Точная фраза", key="exact_match_checkbox", on_change=trigger_new_search)
    with c_btn:
        if st.button("Найти 🚀", use_container_width=True, type="primary"): trigger_new_search()

# =====================================================================
# 📥 РЕНДЕР МЕНЕДЖЕРА ЗАГРУЗОК (БЛОК 2 - ЖИВОЙ ФРАГМЕНТ)
# =====================================================================
@auto_updating_fragment
def render_download_manager():
    cleanup_finished_downloads()
    running_count = count_running_downloads()

    if not st.session_state.active_downloads:
        return

    with st.expander(f"📥 Менеджер загрузок (Активных: {running_count})", expanded=True):
        c_btn1, c_btn2, c_btn3 = st.columns([2, 1, 1])
        with c_btn1:
            if running_count > 0: st.info("🔄 Автообновление активно (идет скачивание...)")
            else: st.success("✅ Все задачи завершены")
        with c_btn2:
            if st.button("📂 Открыть папку (clips)", use_container_width=True, type="secondary"):
                abs_path = os.path.abspath(CLIPS_DIR)
                if sys.platform == "win32": os.startfile(abs_path)
                elif sys.platform == "darwin": subprocess.Popen(["open", abs_path])
                else: subprocess.Popen(["xdg-open", abs_path])
        with c_btn3:
            if st.button("🗑 Очистить завершенные", use_container_width=True):
                # БЕЗОПАСНОЕ УДАЛЕНИЕ
                to_delete = [k for k, v in st.session_state.active_downloads.items() if v['status'] != 'running']
                for k in to_delete: st.session_state.active_downloads.pop(k, None)
                st.rerun()

        st.markdown("---")
        cols = st.columns(3)
        for idx, (task_id, task) in enumerate(list(st.session_state.active_downloads.items())):
            col = cols[idx % 3]
            with col:
                with st.container(border=True):
                    st.markdown(f"**{task['title']}**")
                    if 'quote' in task: st.caption(f"🗣️ *«{sanitize_html_text(task['quote'][:40])}...»*")

                    if task['status'] == 'running':
                        proc = task.get('process')
                        if proc and proc.poll() is None:
                            try:
                                with open(task['log_file'], 'r', encoding='utf-8') as f:
                                    clean_status = get_clean_status_from_log(f.readlines()[-15:])
                                    st.info(clean_status)
                            except: st.info("⏳ Ожидание логов...")
                            if st.button("Остановить ❌", key=f"stop_{task_id}", use_container_width=True):
                                proc.terminate()
                                task['status'] = 'stopped'
                                st.rerun()
                        else:
                            if os.path.exists(task['file_path']) and os.path.getsize(task['file_path']) > 1024: task['status'] = 'success'
                            else: task['status'] = 'error'
                            st.rerun()
                    elif task['status'] == 'success':
                        st.success("✅ Сохранено!")
                        try:
                            st.video(task['file_path'])
                            c_dl, c_del = st.columns(2)
                            with c_dl:
                                with open(task['file_path'], "rb") as file:
                                    st.download_button("💾", data=file, file_name=os.path.basename(task['file_path']), mime="video/mp4", key=f"dl_{task_id}", use_container_width=True)
                            with c_del:
                                if st.button("Убрать ✖", key=f"clr_{task_id}", use_container_width=True):
                                    st.session_state.active_downloads.pop(task_id, None)
                                    st.rerun()
                        except: st.error("Файл не найден")
                    else:
                        st.error("❌ Ошибка")
                        if st.button("Убрать ✖", key=f"clr_{task_id}", use_container_width=True):
                            st.session_state.active_downloads.pop(task_id, None)
                            st.rerun()

render_download_manager()


# =====================================================================
# РЕНДЕР КАРТОЧКИ ФИЛЬМА (БЛОК 3)
# =====================================================================
def render_result_card(row, uid, list_type="search"):
    title, year, genres, rating, m_type, season, ep, start_srt, end_srt, text, imdb_id, countries, orig_title, s_id = row
    
    imdb_link = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else "#"
    display_title = f"📺 **{title}** (S{safe_int(season):02d}E{safe_int(ep):02d})" if m_type == "tv" else f"🎬 **{title}** ({safe_int(year)})"

    text_escaped = sanitize_html_text(str(text))
    text_html = text_escaped
    
    q = st.session_state.last_query
    if q:
        is_exact = st.session_state.get("exact_match_checkbox", False) or (q.startswith('"') and q.endswith('"'))
        if is_exact:
            clean_q = re.escape(re.sub(r'[^\w\s]', '', q).strip())
            try: text_html = re.sub(f"(?i)({clean_q})", r'<mark style="background-color: #ffd700; color: #000; padding: 0 4px; border-radius: 4px;"><b>\g<0></b></mark>', text_html)
            except: pass
        else:
            for w in re.findall(r'\w+', q):
                try: text_html = re.sub(f"(?i)({re.escape(w)})[а-яА-Яa-zA-Z]*", r'<mark style="background-color: #ffd700; color: #000; padding: 0 2px; border-radius: 3px;"><b>\g<0></b></mark>', text_html)
                except: pass

    with st.container(border=True):
        c_head1, c_head2 = st.columns([5, 1])
        with c_head1:
            st.markdown(f"### {display_title} &nbsp;&nbsp; <a href='{imdb_link}' target='_blank' style='font-size:14px; text-decoration:none;'>🔗 IMDb</a>", unsafe_allow_html=True)
            st.caption(f"⭐ {rating if rating else '?'} | 🎭 {genres} | 🌍 {countries}")
        with c_head2:
            is_fav = is_favorite(imdb_id, s_id)
            fav_icon = "❤️ В избранном" if is_fav else "🤍 Сохранить"
            if st.button(fav_icon, key=f"fav_{list_type}_{uid}", use_container_width=True):
                toggle_favorite(imdb_id, s_id)
                st.rerun()

        c_body, c_tools = st.columns([3, 2])
        saved_source, saved_offset = get_saved_source_info(imdb_id)
        auto_offset = 0.0

        with c_body:
            st.markdown(f"<div style='font-size: 18px; border-left: 4px solid #ff4b4b; padding-left: 15px; margin: 10px 0;'><i>«{text_html}»</i></div>", unsafe_allow_html=True)
            st.markdown(f"<div style='color: #888; font-size: 14px;'>⏳ Таймкод: <b>{str(start_srt)[:8]}</b></div>", unsafe_allow_html=True)

            with st.expander("💬 Показать полный диалог"):
                context_items = get_surrounding_context(imdb_id, srt_to_seconds(start_srt), s_id, window_sec=45)
                if context_items:
                    for item in context_items:
                        if item["is_target"]: st.markdown(f"**[{item['time_str']}] {item['text']}** ⬅️")
                        else: st.caption(f"[{item['time_str']}] {item['text']}")
                else: st.info("Контекст не найден")

            if saved_source:
                st.info(f"📌 Привязан источник: {sanitize_html_text(str(saved_source)[:20])}...")
                if st.button("🗑 Отвязать", key=f"reset_{list_type}_{uid}"):
                    delete_source_info(imdb_id); st.rerun()

        with c_tools:
            with st.expander("🛠 Рассинхрон? (Умная подгонка)"):
                st.markdown("<span style='font-size:13px'>Услышали другую фразу в скачанном видео? Введите её здесь и кликните по результату:</span>", unsafe_allow_html=True)
                heard_phrase = st.text_input("🔍 Поиск по фильму:", placeholder="Введите слово...", key=f"live_sync_{list_type}_{uid}")
                
                target_sec = srt_to_seconds(start_srt)
                subs_chunk = get_sync_subtitles(imdb_id, s_id, heard_phrase)
                
                if subs_chunk:
                    with st.container(height=250, border=True):
                        for sub_id_db, sub_time, sub_text in subs_chunk:
                            is_target = (sub_id_db == s_id)
                            prefix = "🎯 " if is_target else "⏱ "
                            if st.button(f"{prefix}[{str(sub_time)[:8]}] {sub_text}", key=f"sync_btn_{list_type}_{uid}_{sub_id_db}", use_container_width=True, type="tertiary"):
                                new_sec = srt_to_seconds(sub_time)
                                st.session_state[f"auto_off_{list_type}_{uid}"] = target_sec - new_sec
                else:
                    st.warning("Субтитры не найдены.")
                
                auto_offset = st.session_state.get(f"auto_off_{list_type}_{uid}", 0.0)
                if auto_offset != 0:
                    st.success(f"🤖 Авто-сдвиг: **{'+' if auto_offset > 0 else ''}{auto_offset:.1f} сек.**")

                manual_offset = st.number_input("Ручная корректировка (+/- сек):", min_value=-600.0, max_value=600.0, value=float(saved_offset), step=0.5, key=f"man_{list_type}_{uid}")

            with st.expander("🔎 Качается мусор? (Сменить раздачу)"):
                search_q = f"{title} {year} полный фильм" if m_type != "tv" else f"{title} S{safe_int(season):02d}E{safe_int(ep):02d}"
                if st.button("Найти ролики в интернете", key=f"find_src_{list_type}_{uid}"):
                    with st.spinner("Ищем видео..."):
                        st.session_state[f"sources_{list_type}_{uid}"] = manual_video_search(search_q, srt_to_seconds(start_srt) + 30)

                saved_sources = st.session_state.get(f"sources_{list_type}_{uid}", [])
                if saved_sources:
                    source_options = {s["label"]: s["id"] for s in saved_sources}
                    selected_label = st.selectbox("Выберите видео:", list(source_options.keys()), key=f"sel_src_{list_type}_{uid}")
                    if st.button("💾 Закрепить источник", key=f"fix_src_{list_type}_{uid}"):
                        save_source_info(imdb_id, source_options[selected_label], saved_offset)
                        st.success("Закреплено!")
                        st.rerun()

            final_offset = auto_offset + manual_offset
            start_sec = max(0, srt_to_seconds(start_srt) - pad_start + final_offset)
            end_sec = srt_to_seconds(end_srt) + pad_end + final_offset
            duration = max(1, end_sec - start_sec)

            if st.button("⬇️ СКАЧАТЬ КЛИП", key=f"dl_{list_type}_{uid}", use_container_width=True, type="primary"):
                if count_running_downloads() >= MAX_ACTIVE_DOWNLOADS:
                    st.error(f"❌ Дождитесь завершения других загрузок (макс {MAX_ACTIVE_DOWNLOADS}).")
                else:
                    if final_offset != saved_offset: save_source_info(imdb_id, saved_source if saved_source else "", final_offset)
                    task_id = f"task_{imdb_id}_{int(time.time())}"
                    
                    safe_quote = sanitize_filename(text_escaped, max_length=25)
                    safe_name = sanitize_filename(f"{title}_{year}")
                    expected_file = os.path.join(CLIPS_DIR, f"{safe_name}__{safe_quote}_{task_id[-6:]}.mp4")

                    cmd = [
                        sys.executable, "-u", "magnet_get.py",
                        "--title", str(title), "--orig_title", str(orig_title or ""),
                        "--year", str(safe_int(year)), "--type", str(m_type),
                        "--season", str(safe_int(season)), "--episode", str(safe_int(ep)),
                        "--start", seconds_to_hms(start_sec), "--duration", str(int(duration)),
                        "--source", source_pref, "--output", expected_file
                    ]
                    if saved_source: cmd.extend(["--force_source", saved_source])

                    log_file_path = os.path.join(CLIPS_DIR, f"{task_id}_log.txt")
                    log_file_handle = open(log_file_path, "w", encoding="utf-8")
                    
                    env = os.environ.copy()
                    process = subprocess.Popen(cmd, stdout=log_file_handle, stderr=subprocess.STDOUT, text=True, env=env)

                    st.session_state.active_downloads[task_id] = {
                        "title": f"{display_title} [{str(start_srt)[:8]}]",
                        "quote": str(text)[:60], "process": process, "file_path": expected_file,
                        "log_file": log_file_path, "_log_handle": log_file_handle, "status": "running"
                    }
                    st.toast("📥 Загрузка начата! Смотрите в верхнюю панель.")


# =====================================================================
# ВЫВОД РЕЗУЛЬТАТОВ (БЛОК 4)
# =====================================================================
tab_search, tab_favs = st.tabs(["🔍 Результаты поиска", "⭐ Моё Избранное"])

with tab_search:
    if st.session_state.trigger_search:
        st.session_state.trigger_search = False
        with st.status("🔍 Запуск поиска...", expanded=True) as status_box:
            st.session_state.search_results = perform_search(
                st.session_state.last_query, st.session_state.search_mode,
                RESULTS_PER_PAGE, 0, st.session_state.min_rating, st.session_state.t_type,
                st.session_state.c_filter, st.session_state.genre_filter, st.session_state.specific_movie,
                st.session_state.exact_match_checkbox, log_widget=status_box
            )
            if st.session_state.search_results: status_box.update(label="✅ Сцены найдены.", state="complete", expanded=False)
            else: status_box.update(label="❌ Ничего не найдено", state="error", expanded=False)

    if (search_mode == "ИИ-Агент (OpenRouter 🤖)" and getattr(st.session_state, 'llm_ideas', [])):
        st.markdown("### 🧠 Режиссерские задумки ИИ:")
        df_ideas = pd.DataFrame(st.session_state.llm_ideas)
        if not df_ideas.empty:
            df_ideas.columns = ["Фраза для поиска", "Ожидаемый фильм/Жанр", "Почему это круто (Задумка)"]
            st.table(df_ideas)
        st.markdown("---")

    if st.session_state.search_results:
        st.success(f"Показана страница {st.session_state.search_offset // RESULTS_PER_PAGE + 1}")
        for i, (row, sim) in enumerate(st.session_state.search_results):
            uid = f"{row.imdb_id}_{row.sub_id}"
            render_result_card(row, uid, list_type="search")

        st.markdown("---")
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            if st.button("🔄 Показать еще 10 результатов...", use_container_width=True):
                st.session_state.search_offset += RESULTS_PER_PAGE
                with st.status("📎 Подгружаем...", expanded=True) as status:
                    more_results = perform_search(
                        st.session_state.last_query, st.session_state.search_mode,
                        RESULTS_PER_PAGE, st.session_state.search_offset,
                        st.session_state.min_rating, st.session_state.t_type,
                        st.session_state.c_filter, st.session_state.genre_filter,
                        st.session_state.specific_movie, st.session_state.exact_match_checkbox, log_widget=status
                    )
                    if more_results:
                        st.session_state.search_results.extend(more_results)
                        st.rerun()
                    else: st.info("Больше результатов нет.")

with tab_favs:
    st.markdown("### 🌟 Сохраненные моменты")
    fav_results = get_all_favorites()
    if fav_results:
        for i, (row, sim) in enumerate(fav_results):
            uid = f"{row.imdb_id}_{row.sub_id}"
            render_result_card(row, uid, list_type="fav")
    else:
        st.info("Вы пока ничего не добавили в избранное. Нажмите 🤍 на любой карточке в поиске!")

# =====================================================================
# 🔄 ФОНОВОЕ АВТООБНОВЛЕНИЕ ДЛЯ МЕНЕДЖЕРА ЗАГРУЗОК
# =====================================================================
if count_running_downloads() > 0:
    time.sleep(1.5)
    st.rerun()