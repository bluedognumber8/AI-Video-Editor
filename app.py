# app.py
"""
AI Video Editor with Simple Authentication
"""

# Must be first Streamlit command
import streamlit as st
st.set_page_config(
    page_title="AI Video Editor",
    page_icon="🎬",
    layout="wide"
)

# Simple authentication
from auth import check_password

# Check password - if False, stops here and shows login
if not check_password():
    st.stop()

from dotenv import load_dotenv
load_dotenv()
import os

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
import urllib.parse
import logging
import html as html_module
from collections import namedtuple

from ai_agent import generate_search_queries, rank_database_results

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

try:
    import nltk
    from nltk.stem.snowball import SnowballStemmer
    try: nltk.data.find('corpora/wordnet')
    except: nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True)
    try: nltk.data.find('tokenizers/punkt')
    except: nltk.download('punkt', quiet=True)
    ru_stemmer = SnowballStemmer("russian")
except: ru_stemmer = None

try:
    import pymorphy3
    morph = pymorphy3.MorphAnalyzer()
except: morph = None

DB_NAME = 'movies_master.sqlite'
CLIPS_DIR = 'clips'
SETTINGS_FILE = "user_settings.json"
RESULTS_PER_PAGE = 10
MAX_ACTIVE_DOWNLOADS = 5

os.makedirs(CLIPS_DIR, exist_ok=True)
st.set_page_config(page_title="AI-Режиссер Монтажа", page_icon="🎬", layout="wide")

st.markdown("""
    <style>
        .main .block-container > div[data-testid="stVerticalBlock"] > div:first-child {
            position: sticky; top: 2.875rem; background-color: var(--primary-background-color);
            z-index: 999; padding-top: 10px; padding-bottom: 10px;
            border-bottom: 1px solid rgba(128,128,128, 0.2); margin-bottom: 15px;
        }
        .stButton button { margin-top: 0px; }
    </style>
""", unsafe_allow_html=True)

SearchRow = namedtuple('SearchRow', ['title_ru', 'year', 'genres', 'rating', 'type', 'season', 'episode', 'start_time', 'end_time', 'text', 'imdb_id', 'countries', 'title_original', 'sub_id'])

def init_db():
    with sqlite3.connect(DB_NAME) as conn: 
        conn.execute("CREATE TABLE IF NOT EXISTS favorites (imdb_id TEXT, sub_id INTEGER, added_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(imdb_id, sub_id))")
        # Создаем таблицу для истории поиска
        conn.execute("CREATE TABLE IF NOT EXISTS search_history (id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, mode TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
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
            sql = "SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode, s.start_time, s.end_time, s.text, m.imdb_id, m.countries, m.title_original, s.id FROM favorites f JOIN subtitles s ON f.sub_id = s.id JOIN movies m ON f.imdb_id = m.imdb_id ORDER BY f.added_at DESC"
            cur = conn.cursor(); cur.execute(sql)
            return [(SearchRow(*row), 100.0) for row in cur.fetchall()]
    except: return []

def is_favorite(imdb_id, sub_id):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor(); cur.execute("SELECT 1 FROM favorites WHERE imdb_id=? AND sub_id=?", (imdb_id, sub_id))
            return bool(cur.fetchone())
    except: return False

def add_to_history(query, mode):
    if not query or not query.strip(): return
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            # Проверяем последний запрос, чтобы не спамить дубликатами подряд
            cur.execute("SELECT query, mode FROM search_history ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row and row[0] == query and row[1] == mode:
                return
            conn.execute("INSERT INTO search_history (query, mode) VALUES (?, ?)", (query, mode))
    except: pass

def get_search_history(limit=50):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, query, mode, datetime(created_at, 'localtime') FROM search_history ORDER BY id DESC LIMIT ?", (limit,))
            return cur.fetchall()
    except: return []

def clear_search_history():
    try:
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("DELETE FROM search_history")
    except: pass

DEFAULT_SETTINGS = {
    "search_mode": "По словам (Быстро ⚡️)", "specific_movie": "Все фильмы",
    "t_type": "Все", "c_filter": "Все", "genre_filter": "Любой",
    "min_rating": 0.0, "pad_start": 30.0, "pad_end": 30.0, "source_pref": "all"
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
                cur.execute("SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? AND text LIKE ? ORDER BY ABS(id - ?) ASC LIMIT 50", (imdb_id, f"%{phrase}%", anchor_sub_id))
            else:
                cur.execute("SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? AND id BETWEEN ? AND ? ORDER BY start_time", (imdb_id, anchor_sub_id - 20, anchor_sub_id + 20))
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

def get_wide_context(imdb_id, target_start_sec, target_sub_id=None, window_sec=900):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cur = conn.cursor()
            if target_sub_id is not None:
                # Ограничиваем окно по ID субтитра (± 800 фраз), чтобы не захватывать другие версии субтитров этого же фильма
                cur.execute("SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? AND id BETWEEN ? AND ? ORDER BY start_time ASC", (imdb_id, target_sub_id - 800, target_sub_id + 800))
            else:
                cur.execute("SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? ORDER BY start_time ASC", (imdb_id,))
            rows = cur.fetchall()
    except: return []
    
    items = []
    for s_id, r_st, r_tx in rows:
        r_sec = srt_to_seconds(r_st)
        if abs(r_sec - target_start_sec) <= window_sec:
            # Умная локальная дедупликация: не добавлять фразу, если точно такая же прозвучала меньше 5 секунд назад
            if items and any(x['text'] == r_tx and abs(x['sec'] - r_sec) < 5.0 for x in items[-5:]):
                continue
            items.append({"id": s_id, "sec": r_sec, "time_str": str(r_st)[:8], "text": r_tx})
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
    return res

def build_sql_filters(min_rating, t_type, country_filter, genre_filter, specific_movie, params):
    sql = ""
    if min_rating > 0:
        sql += " AND m.rating >= ?"; params.append(min_rating)
    if t_type != "Все":
        sql += " AND m.type = ?"; params.append("movie" if t_type == "Фильмы" else "tv")
    if country_filter == "Наше (RU/SU)":
        sql += " AND (m.countries LIKE '%RU%' OR m.countries LIKE '%SU%')"
    elif country_filter == "Зарубежное":
        sql += " AND (m.countries NOT LIKE '%RU%' AND m.countries NOT LIKE '%SU%' OR m.countries IS NULL)"
    if genre_filter != "Любой":
        sql += " AND m.genres LIKE ?"; params.append(f"%{genre_filter}%")
    if specific_movie != "Все фильмы":
        sql += " AND m.title_ru = ?"; params.append(specific_movie)
    return sql

def _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match):
    if not os.path.exists(DB_NAME): return []
    try: conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
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

class StreamlitAIWidget:
    def __init__(self, status_container):
        self.status = status_container
    def info(self, msg): 
        self.status.write(f"💭 {msg}")
    def success(self, msg): 
        self.status.write(f"✅ **{msg}**")
    def warning(self, msg): 
        self.status.write(f"⚠️ *{msg}*")
    def error(self, msg): 
        self.status.error(msg)

def _search_ai_pipeline(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, st_status_box):
    ai_logger = StreamlitAIWidget(st_status_box)
    
    st_status_box.update(label="🧠 ИИ придумывает поисковые теги...", state="running")
    ai_logger.info("Шаг 1: Трансляция смысла в слова базы данных...")
    
    queries = generate_search_queries(query_text, log_widget=ai_logger)
    
    if not queries:
        ai_logger.warning("ИИ не смог придумать фразы. Запускаем слепой текстовый поиск...")
        st_status_box.update(label="⚠️ ИИ не справился. Обычный поиск.", state="error")
        return _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, False)

    ai_logger.success(f"ИИ предлагает искать по фразам: {', '.join(queries)}")

    st_status_box.update(label=f"🔍 Сканируем базу данных по {len(queries)} фразам...", state="running")
    ai_logger.info("Шаг 2: Извлекаем сырые данные из SQLite...")
    
    raw_results = []
    seen_ids = set()
    for q in queries:
        res = _search_fts(q, limit=10, offset=0, min_rating=min_rating, t_type=t_type, country_filter=country_filter, genre_filter=genre_filter, specific_movie=specific_movie, exact_match=True)
        for r in res:
            uid = f"{r[0].imdb_id}_{r[0].sub_id}"
            if uid not in seen_ids:
                seen_ids.add(uid)
                raw_results.append(r)
                
    if not raw_results: 
        ai_logger.error("По этим фразам в базе ничего не найдено.")
        st_status_box.update(label="❌ Ничего не найдено", state="error")
        return []

    ai_logger.success(f"Найдено {len(raw_results)} потенциальных совпадений (кандидатов).")

    st_status_box.update(label=f"⚖️ ИИ отсматривает {len(raw_results[:30])} сцен...", state="running")
    ai_logger.info("Шаг 3: Отправляем кандидатов обратно в ИИ для оценки контекста...")
    
    candidates_for_ai = [{"id": idx, "genre": r[0].genres, "text": r[0].text} for idx, r in enumerate(raw_results[:30])]
    best_indices = rank_database_results(query_text, candidates_for_ai, ai_logger)
    
    final_results = []
    if best_indices:
        ai_logger.success(f"ИИ выбрал лучшие сцены: ID {best_indices}")
        for idx in best_indices:
            if 0 <= idx < len(raw_results): 
                final_results.append(raw_results[idx])
    
    for r in raw_results:
        if r not in final_results: 
            final_results.append(r)
    
    if final_results:
        st_status_box.update(label=f"🎯 Найдено {len(final_results[:limit])} лучших сцен", state="complete", expanded=False)
    else:
        st_status_box.update(label="❌ Результаты не прошли проверку", state="error", expanded=False)
            
    return final_results[:limit]

def perform_search(query_text, search_mode, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match, log_widget=None):
    if not query_text or not query_text.strip(): return []
    if search_mode == "По словам (Быстро ⚡️)":
        if log_widget: log_widget.info("⚡ Выполняем точный поиск по базе...")
        return _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, exact_match)
    else:
        return []

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

with st.sidebar:
    st.header("⚙️ Режим Поиска")
    search_mode = st.radio("Как ищем?", ["По словам (Быстро ⚡️)", "ИИ-Агент (RAG Пайплайн 🤖)"], key="search_mode", on_change=on_settings_change)

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
    pad_start = st.number_input("Секунд ДО фразы:", min_value=0.0, step=5.0, value=float(st.session_state.get("pad_start", 30.0)), key="pad_start", on_change=on_download_settings_change)
    pad_end = st.number_input("Секунд ПОСЛЕ фразы:", min_value=0.0, step=5.0, value=float(st.session_state.get("pad_end", 30.0)), key="pad_end", on_change=on_download_settings_change)
    source_pref = st.radio("🌐 Источник:", ["all", "youtube", "rutube", "torrent"], format_func=lambda x: {"all": "Везде (YT -> Tor)", "youtube": "Только YT", "rutube": "Только RuTube", "torrent": "Только Torrent (TorrServer ⚡️)"}[x], key="source_pref", on_change=on_download_settings_change)

search_container = st.container()
with search_container:
    c_search, c_opt, c_btn = st.columns([5, 2, 1])
    with c_search:
        st.text_input("🔍 Поиск фрагмента:", placeholder="Введите цитату (для точной фразы используйте кавычки \"\")", key="search_query_input", on_change=trigger_new_search, label_visibility="collapsed")
    with c_opt:
        st.checkbox("🎯 Точная фраза", key="exact_match_checkbox", on_change=trigger_new_search)
    with c_btn:
        if st.button("Найти 🚀", use_container_width=True, type="primary"): trigger_new_search()


@auto_updating_fragment
def render_download_manager():
    cleanup_finished_downloads()
    running_count = count_running_downloads()

    if not st.session_state.active_downloads:
        return

    st.markdown(f"### 📥 Менеджер загрузок (Активных: {running_count})")
    
    c_btn1, c_btn2, c_btn3 = st.columns([2, 1, 1])
    with c_btn1:
        if running_count > 0:
            st.info("🔄 Автообновление активно (идет скачивание...)")
        else:
            st.success("✅ Все задачи завершены")
    with c_btn2:
        if st.button("📂 Открыть папку (clips)", use_container_width=True, type="secondary"):
            abs_path = os.path.abspath(CLIPS_DIR)
            if sys.platform == "win32":
                os.startfile(abs_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", abs_path])
            else:
                subprocess.Popen(["xdg-open", abs_path])
    with c_btn3:
        if st.button("🗑 Очистить завершенные", use_container_width=True):
            to_delete = [k for k, v in st.session_state.active_downloads.items() if v['status'] != 'running']
            for k in to_delete:
                st.session_state.active_downloads.pop(k, None)
            st.rerun()

    st.markdown("---")

    for idx, (task_id, task) in enumerate(list(st.session_state.active_downloads.items())):
        if task['status'] == 'running':
            proc = task.get('process')
            if proc and proc.poll() is None:
                status_icon = "⏳"
                try:
                    with open(task['log_file'], 'r', encoding='utf-8') as f:
                        short_status = get_clean_status_from_log(f.readlines()[-15:])
                except:
                    short_status = "В процессе..."
            else:
                if os.path.exists(task['file_path']) and os.path.getsize(task['file_path']) > 1024:
                    task['status'] = 'success'
                    status_icon = "✅"
                    short_status = "Готово!"
                else:
                    task['status'] = 'error'
                    status_icon = "❌"
                    short_status = "Ошибка"
        elif task['status'] == 'success':
            status_icon = "✅"
            short_status = "Сохранено"
        elif task['status'] == 'stopped':
            status_icon = "⏹️"
            short_status = "Остановлено"
        else:
            status_icon = "❌"
            short_status = "Ошибка"

        quote_preview = f" — «{sanitize_html_text(task.get('quote', '')[:30])}...»" if task.get('quote') else ""
        accordion_label = f"{status_icon} {task['title']}{quote_preview} | {short_status}"

        with st.expander(accordion_label, expanded=(task['status'] == 'running')):
            if task['status'] == 'running':
                proc = task.get('process')
                if proc and proc.poll() is None:
                    try:
                        with open(task['log_file'], 'r', encoding='utf-8') as f:
                            st.info(get_clean_status_from_log(f.readlines()[-15:]))
                    except:
                        st.info("⏳ Ожидание логов...")
                    if st.button("Остановить ❌", key=f"stop_{task_id}", use_container_width=True):
                        proc.terminate()
                        task['status'] = 'stopped'
                        st.rerun()
                else:
                    if os.path.exists(task['file_path']) and os.path.getsize(task['file_path']) > 1024:
                        task['status'] = 'success'
                    else:
                        task['status'] = 'error'
                    st.rerun()

            elif task['status'] == 'success':
                st.success("✅ Сохранено!")
                try:
                    st.video(task['file_path'])
                    c_dl, c_rm = st.columns(2)
                    with c_dl:
                        with open(task['file_path'], "rb") as file:
                            st.download_button(
                                "💾 Скачать MP4", data=file,
                                file_name=os.path.basename(task['file_path']),
                                mime="video/mp4", key=f"dl_{task_id}",
                                use_container_width=True
                            )
                    with c_rm:
                        if st.button("Убрать из списка ✖", key=f"clr_{task_id}", use_container_width=True):
                            st.session_state.active_downloads.pop(task_id, None)
                            st.rerun()
                except:
                    st.error("Файл не найден")

            elif task['status'] == 'stopped':
                st.warning("⏹️ Загрузка остановлена пользователем.")
                if st.button("Убрать ✖", key=f"clr_{task_id}", use_container_width=True):
                    st.session_state.active_downloads.pop(task_id, None)
                    st.rerun()

            else:  
                st.error("❌ Ошибка загрузки")
                if st.button("Убрать ✖", key=f"clr_{task_id}", use_container_width=True):
                    st.session_state.active_downloads.pop(task_id, None)
                    st.rerun()

            with st.expander("📜 Транскрипция (±15 мин) и Корректировка Рассинхрона"):
                q_filter = st.text_input(
                    "🔍 Локальный поиск (Ctrl+F):", key=f"dl_search_{task_id}",
                    placeholder="Начните вводить текст, чтобы отфильтровать список..."
                )
                subs = get_wide_context(task['imdb_id'], task['orig_start_sec'], task.get('sub_id'), window_sec=900)

                if q_filter:
                    subs = [s for s in subs if q_filter.lower() in s['text'].lower()]

                with st.container(height=250):
                    for s in subs:
                        # Если известен точный sub_id - используем его для идеального совпадения, иначе примерное по секундам
                        is_target = (s['id'] == task.get('sub_id')) if task.get('sub_id') else (abs(s['sec'] - task['orig_start_sec']) < 2)
                        prefix = "🎯 " if is_target else "⏱ "
                        
                        if st.button(
                            f"{prefix}[{s['time_str']}] {s['text']}",
                            key=f"fix_dl_{task_id}_{s['id']}",
                            use_container_width=True,
                            type="primary" if is_target else "tertiary" # Красный / акцентный фон для нашей искомой фразы
                        ):
                            new_offset = task['orig_start_sec'] - s['sec']
                            save_source_info(task['imdb_id'], task.get('saved_source') or "", new_offset)

                            if task['status'] == 'running' and task.get('process') and task['process'].poll() is None:
                                task['process'].terminate()

                            SAFETY_BUFFER = 10.0
                            start_sec = max(0, task['orig_start_sec'] - task['pad_start'] - SAFETY_BUFFER + new_offset)
                            duration = max(1, (task['orig_end_sec'] + task['pad_end'] + new_offset) - start_sec)

                            cmd = [
                                sys.executable, "-u", "magnet_get.py",
                                "--title", str(task['title_raw']),
                                "--orig_title", str(task.get('orig_title') or ""),
                                "--year", str(safe_int(task['year'])),
                                "--type", str(task['m_type']),
                                "--season", str(safe_int(task['season'])),
                                "--episode", str(safe_int(task['ep'])),
                                "--start", seconds_to_hms(start_sec),
                                "--duration", str(int(duration)),
                                "--source", task['source_pref'],
                                "--output", task['file_path']
                            ]
                            if task.get('saved_source'):
                                cmd.extend(["--force_source", task['saved_source']])

                            log_handle = open(task['log_file'], "w", encoding="utf-8")
                            new_proc = subprocess.Popen(
                                cmd, stdout=log_handle, stderr=subprocess.STDOUT,
                                text=True, env=os.environ.copy()
                            )

                            task['process'] = new_proc
                            task['_log_handle'] = log_handle
                            task['status'] = 'running'
                            st.success(f"✅ Смещение обновлено ({new_offset:.1f} сек). Запущен рестарт!")
                            time.sleep(1)
                            st.rerun()

render_download_manager()

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
        
        state_key_offset = f"offset_{list_type}_{uid}"
        if state_key_offset not in st.session_state:
            st.session_state[state_key_offset] = float(saved_offset)

        def update_db_offset():
            save_source_info(imdb_id, saved_source or "", st.session_state[state_key_offset])

        with c_body:
            st.markdown(f"<div style='font-size: 18px; border-left: 4px solid #ff4b4b; padding-left: 15px; margin: 10px 0;'><i>«{text_html}»</i></div>", unsafe_allow_html=True)
            st.markdown(f"<div style='color: #888; font-size: 14px;'>⏳ Таймкод: <b>{str(start_srt)[:8]}</b></div>", unsafe_allow_html=True)

            with st.expander("💬 Показать полный диалог"):
                context_items = get_surrounding_context(imdb_id, srt_to_seconds(start_srt), s_id, window_sec=45)
                if context_items:
                    for item in context_items:
                        if item["is_target"]: st.markdown(f"**[{item['time_str']}] {item['text']}** ⬅️")
                        else: st.caption(f"[{item['time_str']}] {item['text']}")

            if saved_source:
                st.info(f"📌 Привязан источник: {sanitize_html_text(str(saved_source)[:20])}...")
                if st.button("🗑 Отвязать", key=f"reset_{list_type}_{uid}"): delete_source_info(imdb_id); st.rerun()

        with c_tools:
            with st.expander("🛠 Рассинхрон? (Умная подгонка)"):
                st.markdown("<span style='font-size:13px'>Услышали другую фразу в скачанном видео? Введите её здесь:</span>", unsafe_allow_html=True)
                heard_phrase = st.text_input("🔍 Поиск по фильму:", placeholder="Введите слово...", key=f"live_sync_{list_type}_{uid}")
                
                target_sec = srt_to_seconds(start_srt)
                subs_chunk = get_sync_subtitles(imdb_id, s_id, heard_phrase)
                
                if subs_chunk:
                    with st.container(height=200, border=True):
                        for sub_id_db, sub_time, sub_text in subs_chunk:
                            prefix = "🎯 " if sub_id_db == s_id else "⏱ "
                            if st.button(f"{prefix}[{str(sub_time)[:8]}] {sub_text}", key=f"sync_btn_{list_type}_{uid}_{sub_id_db}", use_container_width=True, type="tertiary"):
                                st.session_state[state_key_offset] = target_sec - srt_to_seconds(sub_time)
                                update_db_offset()
                                st.rerun()
                
                st.number_input("Текущий сдвиг (+/- сек):", min_value=-3600.0, max_value=3600.0, step=0.5, key=state_key_offset, on_change=update_db_offset)

            with st.expander("🔎 Качается мусор? (Сменить раздачу)"):
                search_q = f"{title} {year} полный фильм" if m_type != "tv" else f"{title} S{safe_int(season):02d}E{safe_int(ep):02d}"
                if st.button("Найти ролики в интернете", key=f"find_src_{list_type}_{uid}"):
                    with st.spinner("Ищем видео..."):
                        st.session_state[f"sources_{list_type}_{uid}"] = manual_video_search(search_q, srt_to_seconds(start_srt) + 30)

                saved_sources = st.session_state.get(f"sources_{list_type}_{uid}", [])
                if saved_sources:
                    source_options = {s["label"]: s["id"] for s in saved_sources}
                    
                    def on_source_select():
                        sel = st.session_state[f"sel_src_{list_type}_{uid}"]
                        save_source_info(imdb_id, source_options[sel], st.session_state[state_key_offset])
                        st.toast("✅ Источник закреплен!")
                        
                    st.selectbox("Выберите видео:", list(source_options.keys()), key=f"sel_src_{list_type}_{uid}", on_change=on_source_select)

            final_offset = st.session_state[state_key_offset]
            start_sec = max(0, srt_to_seconds(start_srt) - pad_start + final_offset)
            duration = max(1, (srt_to_seconds(end_srt) + pad_end + final_offset) - start_sec)

            if st.button("⬇️ СКАЧАТЬ КЛИП", key=f"dl_{list_type}_{uid}", use_container_width=True, type="primary"):
                if count_running_downloads() >= MAX_ACTIVE_DOWNLOADS: st.error(f"❌ Максимум {MAX_ACTIVE_DOWNLOADS} загрузок.")
                else:
                    task_id = f"task_{imdb_id}_{int(time.time())}"
                    expected_file = os.path.join(CLIPS_DIR, f"{sanitize_filename(f'{title}_{year}')}__{sanitize_filename(text_escaped, 25)}_{task_id[-6:]}.mp4")

                    cmd = [sys.executable, "-u", "magnet_get.py", "--title", str(title), "--orig_title", str(orig_title or ""), "--year", str(safe_int(year)), "--type", str(m_type), "--season", str(safe_int(season)), "--episode", str(safe_int(ep)), "--start", seconds_to_hms(start_sec), "--duration", str(int(duration)), "--source", source_pref, "--output", expected_file]
                    if saved_source: cmd.extend(["--force_source", saved_source])

                    log_file_path = os.path.join(CLIPS_DIR, f"{task_id}_log.txt")
                    log_file_handle = open(log_file_path, "w", encoding="utf-8")
                    process = subprocess.Popen(cmd, stdout=log_file_handle, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())

                    st.session_state.active_downloads[task_id] = {
                        "title": f"{display_title} [{str(start_srt)[:8]}]", 
                        "quote": str(text)[:60], 
                        "process": process, 
                        "file_path": expected_file, 
                        "log_file": log_file_path, 
                        "_log_handle": log_file_handle, 
                        "status": "running",
                        "imdb_id": imdb_id,
                        "sub_id": s_id,  # Убедились что передаем точный ID субтитра в менеджер загрузок
                        "orig_start_sec": srt_to_seconds(start_srt),
                        "orig_end_sec": srt_to_seconds(end_srt),
                        "pad_start": pad_start,
                        "pad_end": pad_end,
                        "title_raw": title,
                        "orig_title": orig_title,
                        "year": year,
                        "m_type": m_type,
                        "season": season,
                        "ep": ep,
                        "source_pref": source_pref,
                        "saved_source": saved_source
                    }
                    st.toast("📥 Загрузка начата! Смотрите в верхнюю панель.")

tab_search, tab_favs, tab_history, tab_ai = st.tabs(["🔍 Результаты поиска", "⭐ Моё Избранное", "🕰 История поиска", "🧠 Лаборатория Промптов"])

with tab_search:
    if st.session_state.trigger_search:
        st.session_state.trigger_search = False
        
        # Добавляем в историю перед самим поиском
        add_to_history(st.session_state.last_query, st.session_state.search_mode)
        
        if st.session_state.search_mode == "ИИ-Агент (RAG Пайплайн 🤖)":
            with st.status("🚀 Запуск ИИ-агента...", expanded=True) as status_box:
                st.session_state.search_results = _search_ai_pipeline(
                    st.session_state.last_query, 
                    RESULTS_PER_PAGE, 0, st.session_state.min_rating, st.session_state.t_type, 
                    st.session_state.c_filter, st.session_state.genre_filter, st.session_state.specific_movie, status_box
                )
        else:
            with st.spinner("⚡ Выполняем точный поиск по базе..."):
                st.session_state.search_results = _search_fts(
                    st.session_state.last_query, 
                    RESULTS_PER_PAGE, 0, st.session_state.min_rating, st.session_state.t_type,
                    st.session_state.c_filter, st.session_state.genre_filter, st.session_state.specific_movie, st.session_state.exact_match_checkbox
                )
        
        if not st.session_state.search_results:
            st.error("❌ Ничего не найдено")

    if st.session_state.search_results:
        st.success(f"Показана страница {st.session_state.search_offset // RESULTS_PER_PAGE + 1}")
        for i, (row, sim) in enumerate(st.session_state.search_results):
            render_result_card(row, f"{row.imdb_id}_{row.sub_id}", list_type="search")

        st.markdown("---")
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            if st.button("🔄 Показать еще 10 результатов...", use_container_width=True):
                st.session_state.search_offset += RESULTS_PER_PAGE
                
                if st.session_state.search_mode == "ИИ-Агент (RAG Пайплайн 🤖)":
                    with st.status("📎 ИИ ищет дополнительные сцены...", expanded=False) as status_box:
                        more_results = _search_ai_pipeline(
                            st.session_state.last_query, RESULTS_PER_PAGE, st.session_state.search_offset,
                            st.session_state.min_rating, st.session_state.t_type, st.session_state.c_filter, 
                            st.session_state.genre_filter, st.session_state.specific_movie, status_box
                        )
                else:
                    with st.spinner("📎 Подгружаем..."):
                        more_results = _search_fts(
                            st.session_state.last_query, RESULTS_PER_PAGE, st.session_state.search_offset,
                            st.session_state.min_rating, st.session_state.t_type, st.session_state.c_filter, 
                            st.session_state.genre_filter, st.session_state.specific_movie, st.session_state.exact_match_checkbox
                        )
                
                if more_results:
                    st.session_state.search_results.extend(more_results)
                    st.rerun()
                else: 
                    st.info("Больше результатов нет.")

with tab_favs:
    st.markdown("### 🌟 Сохраненные моменты")
    fav_results = get_all_favorites()
    if fav_results:
        for i, (row, sim) in enumerate(fav_results):
            render_result_card(row, f"{row.imdb_id}_{row.sub_id}", list_type="fav")
    else:
        st.info("Вы пока ничего не добавили в избранное. Нажмите 🤍 на любой карточке в поиске!")

with tab_history:
    st.markdown("### 🕰 История поиска")
    c1, c2 = st.columns([4, 1])
    with c2:
        if st.button("🗑 Очистить историю", use_container_width=True):
            clear_search_history()
            st.rerun()

    history_records = get_search_history(30)
    if history_records:
        for hid, q, mode, dt in history_records:
            with st.container(border=True):
                hc1, hc2, hc3 = st.columns([4, 2, 2])
                with hc1:
                    st.markdown(f"**{sanitize_html_text(q)}**")
                with hc2:
                    st.caption(f"{mode}")
                    st.caption(f"🗓 {dt}")
                with hc3:
                    if st.button("Повторить 🔍", key=f"hist_{hid}", use_container_width=True):
                        st.session_state.search_query_input = q
                        st.session_state.last_query = q
                        st.session_state.search_mode = mode
                        st.session_state.trigger_search = True
                        st.toast("🚀 Поиск запущен! Перейдите во вкладку 'Результаты поиска'.")
                        st.rerun()
    else:
        st.info("История поиска пока пуста.")


with tab_ai:
    st.markdown("### 🧠 Настройка стратегии поиска (Промпты)")
    st.info("Экспериментируйте с тем, как ИИ 'переводит' ваши смыслы в поисковые теги. Инструкции по генерации JSON подставляются скриптом **автоматически** (не пишите их здесь).")
    
    import ai_agent
    
    prompt_data = ai_agent.load_prompts()
    hist_dict = prompt_data.get("history", {})
    curr_name = prompt_data.get("current", "Базовый (По умолчанию)")
    
    c_sel, c_del = st.columns([4, 1])
    with c_sel:
        # Если вдруг ключ удалился из истории
        if curr_name not in hist_dict:
            curr_name = list(hist_dict.keys())[0] if hist_dict else "Базовый (По умолчанию)"
            
        selected_prompt_name = st.selectbox(
            "📂 Загрузить из истории:", 
            list(hist_dict.keys()), 
            index=list(hist_dict.keys()).index(curr_name) if curr_name in hist_dict else 0
        )
        
    with c_del:
        st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
        if st.button("🗑 Удалить", use_container_width=True):
            if selected_prompt_name != "Базовый (По умолчанию)":
                del hist_dict[selected_prompt_name]
                prompt_data["current"] = "Базовый (По умолчанию)"
                ai_agent.save_prompts(prompt_data)
                st.rerun()
            else:
                st.error("Базовый промпт удалить нельзя!")
                
    # Текстовое поле с редактируемым промптом
    edit_text = st.text_area(
        "📝 Логика генерации (ваша стратегия):", 
        value=hist_dict.get(selected_prompt_name, ""), 
        height=300
    )
    
    st.caption("✨ *Скрытая часть (добавится автоматически): 'ОТВЕТЬ СТРОГО В ФОРМАТЕ JSON (Массив строк)...'*")
    
    c_save1, c_save2 = st.columns([3, 1])
    with c_save1:
        new_prompt_name = st.text_input("Название (сохранить как):", value=selected_prompt_name)
    with c_save2:
        st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
        if st.button("💾 Сохранить и Применить", type="primary", use_container_width=True):
            if not new_prompt_name.strip():
                st.error("Укажите название!")
            else:
                hist_dict[new_prompt_name] = edit_text
                prompt_data["current"] = new_prompt_name
                prompt_data["history"] = hist_dict
                ai_agent.save_prompts(prompt_data)
                st.toast(f"✅ Промпт '{new_prompt_name}' сохранен и активирован!")
                st.rerun()


if count_running_downloads() > 0:
    time.sleep(1.5)
    st.rerun()