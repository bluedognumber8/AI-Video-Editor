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
from dotenv import load_dotenv
load_dotenv()
import os

if "TORRSERVER_PATH" not in os.environ:
    os.environ["TORRSERVER_PATH"] = "torrserver"

import sqlite3
import pandas as pd
import re
import datetime
import subprocess
import sys
import time
import json
import logging
import html as html_module
from collections import namedtuple
import atexit

from ai_agent import generate_search_queries, rank_database_results

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(handler)

# Маппинг жанров: русское отображение → английское значение (как в БД)
GENRE_MAP = {
    "Любой": "Любой",
    "Боевик": "Action",
    "Комедия": "Comedy",
    "Драма": "Drama",
    "Фантастика": "Sci-Fi",
    "Ужасы": "Horror",
    "Романтика": "Romance",
    "Криминал": "Crime",
    "Анимация": "Animation",
    "Приключения": "Adventure",
    "Биография": "Biography",
    "Семейный": "Family",
    "Фэнтези": "Fantasy",
    "Нуар": "Film-Noir",
    "Исторический": "History",
    "Музыка": "Music",
    "Мюзикл": "Musical",
    "Мистика": "Mystery",
    "Спорт": "Sport",
    "Триллер": "Thriller",
    "Военный": "War",
    "Вестерн": "Western",
    "Неизвестно": "Unknown",
}

# Track Popen processes and file handles for cleanup on exit
_active_processes = []
_active_handles = []

def _cleanup_resources():
    for proc in _active_processes:
        if proc and proc.poll() is None:
            try: proc.terminate()
            except OSError: pass
    for h in _active_handles:
        if h and not h.closed:
            try: h.close()
            except OSError: pass

atexit.register(_cleanup_resources)

try:
    import nltk
    from nltk.stem.snowball import SnowballStemmer
    try: nltk.data.find('corpora/wordnet')
    except Exception: nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True)
    try: nltk.data.find('tokenizers/punkt')
    except Exception: nltk.download('punkt', quiet=True)
    ru_stemmer = SnowballStemmer("russian")
except Exception: ru_stemmer = None

try:
    import pymorphy3
    morph = pymorphy3.MorphAnalyzer()
except Exception: morph = None

DB_NAME = 'movies_master.sqlite'
CLIPS_DIR = 'clips'
LOGS_DIR = 'logs'
RESULTS_PER_PAGE = 10
MAX_ACTIVE_DOWNLOADS = 5
SETTINGS_FILE = 'ui_settings.json'

os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

def get_db():
    """Get SQLite connection with WAL mode and row factory."""
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

st.markdown("""
    <style>
        /* ── Sticky search bar ── */
        .main .block-container > div[data-testid="stVerticalBlock"] > div:first-child {
            position: sticky; top: 2.875rem; background-color: var(--primary-background-color);
            z-index: 999; padding-top: 10px; padding-bottom: 10px;
            border-bottom: 1px solid rgba(128,128,128,0.2); margin-bottom: 15px;
        }
        .stButton button { margin-top: 0px; }
        .clear-btn-col { padding-top: 28px; }

        /* ── Source meta box ── */
        .source-meta-box { background: rgba(128,128,128,0.1); padding: 10px; border-radius: 8px; margin-bottom: 10px; font-size: 13px; }

        /* ── Result cards ── */
        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 12px !important;
            transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
            border: 1px solid rgba(128,128,128,0.15) !important;
            margin-bottom: 12px;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.3);
            border-color: rgba(255,255,255,0.15) !important;
        }

        /* ── Tabs styling ── */
        div[data-testid="stTabs"] button {
            transition: all 0.15s ease;
            border-radius: 8px 8px 0 0 !important;
            font-weight: 500;
        }
        div[data-testid="stTabs"] button[aria-selected="true"] {
            border-bottom: 2px solid #ff4b4b !important;
        }

        /* ── Sidebar min-width ── */
        section[data-testid="stSidebar"] {
            min-width: 320px !important;
        }

        /* ── Download manager badges ── */
        .dl-badge {
            display: inline-block; padding: 2px 10px; border-radius: 12px;
            font-size: 12px; font-weight: 600; margin-left: 6px;
        }
        .dl-badge-running { background: rgba(255,193,7,0.2); color: #ffc107; }
        .dl-badge-success { background: rgba(40,167,69,0.2); color: #28a745; }
        .dl-badge-error   { background: rgba(220,53,69,0.2); color: #dc3545; }
        .dl-badge-stopped { background: rgba(108,117,125,0.2); color: #6c757d; }

        /* ── Animations ── */
        @keyframes cardFadeIn {
            from { opacity: 0; transform: translateY(12px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            animation: cardFadeIn 0.35s ease-out both;
        }
        div[data-testid="stVerticalBlockBorderWrapper"]:nth-child(1) { animation-delay: 0.00s; }
        div[data-testid="stVerticalBlockBorderWrapper"]:nth-child(2) { animation-delay: 0.04s; }
        div[data-testid="stVerticalBlockBorderWrapper"]:nth-child(3) { animation-delay: 0.08s; }
        div[data-testid="stVerticalBlockBorderWrapper"]:nth-child(4) { animation-delay: 0.12s; }
        div[data-testid="stVerticalBlockBorderWrapper"]:nth-child(5) { animation-delay: 0.16s; }

        /* ── Responsive ── */
        @media (max-width: 768px) {
            section[data-testid="stSidebar"] { min-width: 100% !important; }
            div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 8px !important; }
            .main .block-container > div[data-testid="stVerticalBlock"] > div:first-child {
                padding-top: 6px; padding-bottom: 6px;
            }
        }

        /* ── Reduced motion ── */
        @media (prefers-reduced-motion: reduce) {
            div[data-testid="stVerticalBlockBorderWrapper"],
            div[data-testid="stVerticalBlockBorderWrapper"]:hover {
                transition: none !important; animation: none !important;
                transform: none !important;
            }
        }

        /* ── Time input fields: compact layout ── */
        .time-input-row div[data-testid="stColumn"] {
            min-width: 0;
        }
        .time-input-row div[data-testid="stNumberInput"] {
            max-width: 110px;
        }
        .time-input-row div[data-testid="stNumberInput"] input {
            text-align: center;
            font-size: 14px;
        }
        .time-input-row label p {
            font-size: 13px !important;
        }
        .time-input-row .st-emotion-cache-1nq1r1b {
            gap: 8px;
        }
        /* ── Duration input ── */
        .time-input-row + div div[data-testid="stNumberInput"] {
            max-width: 200px;
        }
        /* ── Selectbox in quality ── */
        div[data-testid="stSelectbox"] label p {
            font-size: 14px !important;
        }
    </style>
""", unsafe_allow_html=True)

SearchRow = namedtuple('SearchRow', ['title_ru', 'year', 'genres', 'rating', 'type', 'season', 'episode', 'start_time', 'end_time', 'text', 'imdb_id', 'countries', 'title_original', 'sub_id'])

def init_db():
    with get_db() as conn: 
        conn.execute("CREATE TABLE IF NOT EXISTS favorites (imdb_id TEXT, sub_id INTEGER, added_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(imdb_id, sub_id))")
        conn.execute("CREATE TABLE IF NOT EXISTS search_history (id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, mode TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        
        # --- NEW DB SCHEMA FOR SOURCES & OFFSETS ---
        conn.execute("CREATE TABLE IF NOT EXISTS movie_bindings (imdb_id TEXT PRIMARY KEY, source_id TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS source_offsets (source_id TEXT PRIMARY KEY, offset_sec REAL)")
        
        # Auto-migrate old data if exists
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='movie_sources'")
        if cur.fetchone():
            try:
                cur.execute("SELECT imdb_id, source_id, offset_sec FROM movie_sources")
                for r_imdb, r_src, r_off in cur.fetchall():
                    eff_src = r_src if r_src else f"auto_{r_imdb}"
                    conn.execute("INSERT OR IGNORE INTO movie_bindings VALUES (?, ?)", (r_imdb, r_src))
                    conn.execute("INSERT OR IGNORE INTO source_offsets VALUES (?, ?)", (eff_src, r_off))
                conn.execute("DROP TABLE movie_sources")
            except Exception: pass
init_db()

def toggle_favorite(imdb_id, sub_id):
    with get_db() as conn:
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
        with get_db() as conn:
            sql = "SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode, s.start_time, s.end_time, s.text, m.imdb_id, m.countries, m.title_original, s.id FROM favorites f JOIN subtitles s ON f.sub_id = s.id JOIN movies m ON f.imdb_id = m.imdb_id ORDER BY f.added_at DESC"
            cur = conn.cursor(); cur.execute(sql)
            return [(SearchRow(*row), 100.0) for row in cur.fetchall()]
    except Exception: return []

def is_favorite(imdb_id, sub_id):
    try:
        with get_db() as conn:
            cur = conn.cursor(); cur.execute("SELECT 1 FROM favorites WHERE imdb_id=? AND sub_id=?", (imdb_id, sub_id))
            return bool(cur.fetchone())
    except Exception: return False

def add_to_history(query, mode):
    if not query or not query.strip(): return
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT query, mode FROM search_history ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            if row and row[0] == query and row[1] == mode:
                return
            conn.execute("INSERT INTO search_history (query, mode) VALUES (?, ?)", (query, mode))
    except Exception: pass

def get_search_history(limit=50):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, query, mode, datetime(created_at, 'localtime') FROM search_history ORDER BY id DESC LIMIT ?", (limit,))
            return cur.fetchall()
    except Exception: return []

def clear_search_history():
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM search_history")
    except Exception: pass

# --- SESSION BASED SETTINGS ---
DEFAULT_SETTINGS = {
    "search_mode": "По словам (Быстро ⚡️)", "specific_movie": "Все фильмы",
    "t_type": "Все", "c_filter": "Все", "genre_include": [], "genre_exclude": [],
    "min_rating": 0.0, "pad_start": 30.0, "pad_end": 30.0, "source_pref": "all"
}

def save_settings():
    """Save current filter settings to JSON file."""
    settings = {
        "search_mode": st.session_state.get("search_mode", DEFAULT_SETTINGS["search_mode"]),
        "specific_movie": st.session_state.get("specific_movie", DEFAULT_SETTINGS["specific_movie"]),
        "t_type": st.session_state.get("t_type", DEFAULT_SETTINGS["t_type"]),
        "c_filter": st.session_state.get("c_filter", DEFAULT_SETTINGS["c_filter"]),
        "genre_include": st.session_state.get("genre_include", DEFAULT_SETTINGS["genre_include"]),
        "genre_exclude": st.session_state.get("genre_exclude", DEFAULT_SETTINGS["genre_exclude"]),
        "min_rating": st.session_state.get("min_rating", DEFAULT_SETTINGS["min_rating"]),
        "pad_start": st.session_state.get("pad_start", DEFAULT_SETTINGS["pad_start"]),
        "pad_end": st.session_state.get("pad_end", DEFAULT_SETTINGS["pad_end"]),
        "source_pref": st.session_state.get("source_pref", DEFAULT_SETTINGS["source_pref"]),
        "exact_match_checkbox": st.session_state.get("exact_match_checkbox", False),
    }
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save settings: {e}")

def load_settings():
    """Load saved settings from JSON file into session_state before init."""
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        for key, val in saved.items():
            if key in DEFAULT_SETTINGS:
                st.session_state[key] = val
    except Exception as e:
        logger.warning(f"Failed to load settings: {e}")

load_settings()  # Restore saved filters first

if "settings_initialized" not in st.session_state:
    st.session_state.settings_initialized = True
    for key, val in DEFAULT_SETTINGS.items(): 
        if key not in st.session_state:
            st.session_state[key] = val

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
    st.session_state.search_offset = 0
    save_settings()
    if st.session_state.get("search_query_input", "").strip(): trigger_new_search()
    else: st.session_state.search_results = []

def on_download_settings_change():
    save_settings()

def sanitize_filename(name: str, max_length: int = 50) -> str:
    safe = re.sub(r'[^\w\s\-]', '', name, flags=re.UNICODE).strip().replace(" ", "_")
    return safe[:max_length] if safe else "unnamed"

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
    except (ValueError, IndexError, TypeError): return 0.0

def seconds_to_hms(seconds): return str(datetime.timedelta(seconds=max(0, int(seconds))))
def safe_int(val, default=0): 
    try: return int(val) if val is not None else default
    except (ValueError, TypeError): return default

@st.cache_data(ttl=3600)
def get_movie_titles():
    try:
        with get_db() as conn: return pd.read_sql_query("SELECT DISTINCT title_ru FROM movies ORDER BY title_ru ASC", conn)['title_ru'].tolist()
    except Exception: return []

def get_sync_subtitles(imdb_id, anchor_sub_id, phrase=""):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if phrase:
                cur.execute("SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? AND text LIKE ? ORDER BY ABS(id - ?) ASC LIMIT 50", (imdb_id, f"%{phrase}%", anchor_sub_id))
            else:
                cur.execute("SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? AND id BETWEEN ? AND ? ORDER BY start_time", (imdb_id, anchor_sub_id - 20, anchor_sub_id + 20))
            return cur.fetchall()
    except Exception: return []

def get_surrounding_context(imdb_id, target_start_sec, target_sub_id, window_sec=90):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT start_time, text FROM subtitles WHERE imdb_id = ? AND id BETWEEN ? AND ? ORDER BY start_time ASC", (imdb_id, target_sub_id - 100, target_sub_id + 100))
            rows = cur.fetchall()
    except Exception: return []
    items, seen = [], set()
    for r_st, r_tx in rows:
        r_sec = srt_to_seconds(r_st)
        if abs(r_sec - target_start_sec) <= window_sec and r_tx not in seen:
            seen.add(r_tx)
            items.append({"sec": r_sec, "label": f"[{str(r_st)[:8]}] {str(r_tx)[:70]}", "text": r_tx, "time_str": str(r_st)[:8], "is_target": abs(r_sec - target_start_sec) < 5})
    return items

def get_wide_context(imdb_id, target_start_sec, target_sub_id=None, window_sec=900):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if target_sub_id is not None:
                cur.execute("SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? AND id BETWEEN ? AND ? ORDER BY start_time ASC", (imdb_id, target_sub_id - 800, target_sub_id + 800))
            else:
                cur.execute("SELECT id, start_time, text FROM subtitles WHERE imdb_id = ? ORDER BY start_time ASC", (imdb_id,))
            rows = cur.fetchall()
    except Exception: return []
    
    items = []
    for s_id, r_st, r_tx in rows:
        r_sec = srt_to_seconds(r_st)
        if abs(r_sec - target_start_sec) <= window_sec:
            if items and any(x['text'] == r_tx and abs(x['sec'] - r_sec) < 5.0 for x in items[-5:]):
                continue
            items.append({"id": s_id, "sec": r_sec, "time_str": str(r_st)[:8], "text": r_tx})
    return items

# --- SOURCE & OFFSET LOGIC REWRITE ---
def get_saved_source_info(imdb_id):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT source_id FROM movie_bindings WHERE imdb_id = ?", (imdb_id,))
            row = cur.fetchone()
            source_id = row[0] if row else None
            
            # If no manual source is bound, use an 'auto' profile for this movie's offset
            eff_src = source_id if source_id else f"auto_{imdb_id}"
            cur.execute("SELECT offset_sec FROM source_offsets WHERE source_id = ?", (eff_src,))
            row_off = cur.fetchone()
            offset_sec = row_off[0] if row_off else 0.0
            
            return source_id, offset_sec
    except Exception: return None, 0.0

def set_movie_source(imdb_id, source_id):
    try:
        with get_db() as conn:
            if source_id:
                conn.execute("INSERT OR REPLACE INTO movie_bindings VALUES (?, ?)", (imdb_id, source_id))
            else:
                conn.execute("DELETE FROM movie_bindings WHERE imdb_id = ?", (imdb_id,))
    except Exception: pass

def set_source_offset(imdb_id, source_id, offset_sec):
    try:
        with get_db() as conn:
            eff_src = source_id if source_id else f"auto_{imdb_id}"
            conn.execute("INSERT OR REPLACE INTO source_offsets VALUES (?, ?)", (eff_src, offset_sec))
    except Exception: pass
# ------------------------------------

def manual_video_search(query, min_duration):
    res = []
    try:
        sub = subprocess.run(["yt-dlp", f"ytsearch10:{query}", "--dump-json", "--no-warnings"], capture_output=True, text=True, timeout=15)
        for l in sub.stdout.strip().split("\n"):
            if l:
                try:
                    v = json.loads(l)
                    if v.get("duration", 0) >= min_duration: res.append({"label": f"🔴 [YT] {v.get('title')} ({seconds_to_hms(v.get('duration'))})", "id": f"youtube:{v.get('id')}"})
                except (json.JSONDecodeError, KeyError): pass
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError): pass
    return res

def build_sql_filters(min_rating, t_type, country_filter, genre_include, genre_exclude, specific_movie, params):
    sql = ""
    if specific_movie != "Все фильмы":
        sql += " AND m.title_ru = ?"
        params.append(specific_movie)
        return sql

    if min_rating > 0:
        sql += " AND m.rating >= ?"; params.append(min_rating)
    if t_type != "Все":
        sql += " AND m.type = ?"; params.append("movie" if t_type == "Фильмы" else "tv")
    if country_filter == "Наше (RU/SU)":
        sql += " AND (m.countries LIKE '%RU%' OR m.countries LIKE '%SU%')"
    elif country_filter == "Зарубежное":
        sql += " AND (m.countries NOT LIKE '%RU%' AND m.countries NOT LIKE '%SU%' OR m.countries IS NULL)"

    if genre_include:
        clauses = []
        for g in genre_include:
            genre_en = GENRE_MAP.get(g, g)
            clauses.append("m.genres LIKE ?")
            params.append(f"%{genre_en}%")
        sql += " AND (" + " OR ".join(clauses) + ")"

    if genre_exclude:
        for g in genre_exclude:
            genre_en = GENRE_MAP.get(g, g)
            sql += " AND m.genres NOT LIKE ?"
            params.append(f"%{genre_en}%")

    return sql

def _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_include, genre_exclude, specific_movie, exact_match):
    if not os.path.exists(DB_NAME): return []
    try:
        conn = get_db()
        cursor = conn.cursor()
    except sqlite3.Error as e:
        logger.error(f"DB connection error in _search_fts: {e}")
        return []

    if query_text.startswith('"') and query_text.endswith('"'):
        exact_match = True
        query_text = query_text.strip('"')

    if exact_match:
        words = re.findall(r'\w+', query_text)
        if not words: return []
        search_query = " AND ".join([f'"{w}"' for w in words]) 
    else:
        search_terms = []
        for word in re.findall(r'\w+', query_text):
            word = word.lower()
            
            if len(word) <= 3:
                search_terms.append(f"{word}")
                continue
                
            if re.search(r'[а-яё]', word):
                variants = {word}
                if morph:
                    parsed = morph.parse(word)
                    lemma = parsed[0].normal_form if parsed else word
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
    sql_filters = build_sql_filters(min_rating, t_type, country_filter, genre_include, genre_exclude, specific_movie, params)

    sql = f"""
        WITH ranked AS (
            SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
                   s.start_time, s.end_time, s.text, m.imdb_id, m.countries, 
                   m.title_original, s.id, tm.rank AS fts_rank,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.imdb_id, 
                       SUBSTR(s.start_time, 1, 4), 
                       SUBSTR(LTRIM(LOWER(s.text), ' .-,:;!?…"'''), 1, 20) 
                       ORDER BY s.start_time ASC
                   ) AS rn
            FROM subtitles_fts tm 
            JOIN subtitles s ON s.id = tm.rowid 
            JOIN movies m ON s.imdb_id = m.imdb_id 
            WHERE tm.text MATCH ? {sql_filters}
        ) 
        SELECT title_ru, year, genres, rating, type, season, episode,
               start_time, end_time, text, imdb_id, countries, title_original, id
        FROM ranked WHERE rn = 1 
    """
    
    if specific_movie != "Все фильмы":
        sql += " ORDER BY fts_rank ASC, start_time ASC LIMIT ? OFFSET ?"
    else:
        sql += " ORDER BY rating DESC, fts_rank ASC LIMIT ? OFFSET ?"
        
    params.extend([limit, offset])

    try:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return [(SearchRow(*row[:14]), 100.0) for row in rows]
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []
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

def _search_ai_pipeline(query_text, limit, offset, min_rating, t_type, country_filter, genre_include, genre_exclude, specific_movie, st_status_box):
    ai_logger = StreamlitAIWidget(st_status_box)
    
    st_status_box.update(label="🧠 ИИ придумывает поисковые теги...", state="running")
    ai_logger.info("Шаг 1: Трансляция смысла в слова базы данных...")
    
    queries = generate_search_queries(query_text, log_widget=ai_logger)
    
    if not queries:
        ai_logger.warning("ИИ не смог придумать фразы. Запускаем слепой текстовый поиск...")
        st_status_box.update(label="⚠️ ИИ не справился. Обычный поиск.", state="error")
        return _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_include, genre_exclude, specific_movie, False)

    ai_logger.success(f"ИИ предлагает искать по фразам: {', '.join(queries)}")

    st_status_box.update(label=f"🔍 Сканируем базу данных по {len(queries)} фразам...", state="running")
    ai_logger.info("Шаг 2: Извлекаем сырые данные из SQLite...")
    
    raw_results = []
    seen_ids = set()
    for q in queries:
        res = _search_fts(q, limit=15, offset=0, min_rating=min_rating, t_type=t_type, country_filter=country_filter, genre_include=genre_include, genre_exclude=genre_exclude, specific_movie=specific_movie, exact_match=True)
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

    pool_size = 200
    st_status_box.update(label=f"⚖️ ИИ отсматривает {len(raw_results[:pool_size])} сцен...", state="running")
    ai_logger.info(f"Шаг 3: Отправляем {len(raw_results[:pool_size])} кандидатов обратно в ИИ для оценки контекста...")
    
    candidates_for_ai = [{"id": idx, "genre": r[0].genres, "text": r[0].text} for idx, r in enumerate(raw_results[:pool_size])]
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

def extract_download_metadata(log_lines):
    info = {"source_title": None, "exact_file": None}
    for line in log_lines:
        if "СКАЧИВАНИЕ РАЗДАЧИ:" in line:
            info["source_title"] = "🔵 [Торрент] " + line.split("СКАЧИВАНИЕ РАЗДАЧИ:")[1].strip()
        elif "ВЫБРАНО ВИДЕО:" in line:
            info["source_title"] = "🔴 [Stream] " + line.split("ВЫБРАНО ВИДЕО:")[1].strip()
        elif "Выбран файл:" in line:
            info["exact_file"] = line.split("Выбран файл:")[1].split("(ID")[0].strip()
        elif "Взят файл" in line:
            try:
                info["exact_file"] = line.split("Взят файл")[1].split(":")[1].split("(ID")[0].strip()
            except Exception: pass
    return info

if hasattr(st, "fragment"):
    auto_updating_fragment = st.fragment(run_every=2)
else:
    def auto_updating_fragment(func): return func

with st.sidebar:
    # ── Search mode (always visible) ──
    st.markdown("### ⚡ Режим Поиска")
    st.radio("Как ищем?", ["По словам (Быстро ⚡️)", "ИИ-Агент (RAG Пайплайн 🤖)"], key="search_mode", on_change=on_settings_change)

    # ── Filters collapsible ──
    with st.expander("🎛️ Фильтры", expanded=True):
        all_movies = ["Все фильмы"] + get_movie_titles()
        current_movie_idx = all_movies.index(st.session_state.get("specific_movie", "Все фильмы")) if st.session_state.get("specific_movie") in all_movies else 0

        def clear_movie_action():
            st.session_state.specific_movie = "Все фильмы"
            on_settings_change()

        c_mov1, c_mov2 = st.columns([5, 1])
        with c_mov1:
            st.selectbox("📌 В кино:", all_movies, index=current_movie_idx, key="specific_movie", on_change=on_settings_change)
        with c_mov2:
            st.markdown("<div class='clear-btn-col'></div>", unsafe_allow_html=True)
            if st.session_state.get("specific_movie", "Все фильмы") != "Все фильмы":
                st.button("❌", key="clear_movie", help="Сбросить выбранный фильм", on_click=clear_movie_action)

        filters_disabled = st.session_state.get("specific_movie", "Все фильмы") != "Все фильмы"
        if filters_disabled:
            st.caption("*(Глобальные фильтры отключены, так как выбран конкретный фильм)*")

        st.radio("🎞 Тип медиа:", ["Все", "Фильмы", "Сериалы"], horizontal=True, key="t_type", on_change=on_settings_change, disabled=filters_disabled)
        st.radio("🌍 Страна:", ["Все", "Наше (RU/SU)", "Зарубежное"], key="c_filter", on_change=on_settings_change, disabled=filters_disabled)
        genre_options = [k for k in GENRE_MAP if k != "Любой"]
        st.multiselect("🎯 Искать в жанрах", options=genre_options, key="genre_include", on_change=on_settings_change, disabled=filters_disabled, placeholder="Все жанры")
        st.multiselect("🚫 Исключить жанры", options=genre_options, key="genre_exclude", on_change=on_settings_change, disabled=filters_disabled, placeholder="Не исключать")
        st.slider("⭐️ Мин. рейтинг IMDb:", 0.0, 10.0, step=0.1, key="min_rating", on_change=on_settings_change, disabled=filters_disabled)

    # ── Timing & source collapsible ──
    with st.expander("✂️ Хронометраж и источник", expanded=True):
        pad_start = st.number_input("⏪ Секунд ДО фразы:", min_value=0.0, step=5.0, value=float(st.session_state.get("pad_start", 30.0)), key="pad_start", on_change=on_download_settings_change)
        pad_end = st.number_input("⏩ Секунд ПОСЛЕ фразы:", min_value=0.0, step=5.0, value=float(st.session_state.get("pad_end", 30.0)), key="pad_end", on_change=on_download_settings_change)
        source_pref = st.radio("🌐 Источник:", ["all", "youtube", "rutube", "torrent"], format_func=lambda x: {"all": "Везде (YT → Tor)", "youtube": "📺 Только YT", "rutube": "📺 Только RuTube", "torrent": "⚡ Только Torrent (TorrServer)"}[x], key="source_pref", on_change=on_download_settings_change)

search_container = st.container()
with search_container:
    c_search, c_opt, c_btn = st.columns([5, 1, 1])
    with c_search:
        st.text_input(
            "🔍 Поиск фрагмента:",
            placeholder='Введите слово или фразу из фильма/сериала… (для точного поиска возьмите фразу в кавычки "вот так")',
            key="search_query_input",
            on_change=trigger_new_search,
            label_visibility="collapsed",
        )
    with c_opt:
        st.checkbox(
            "🎯 Точная фраза",
            key="exact_match_checkbox",
            on_change=trigger_new_search,
            help="Искать ТОЛЬКО введённые слова целиком, без словоформ, окончаний и склонений. Полезно для редких имён и терминов.",
        )
    with c_btn:
        if st.button("🚀 Найти", use_container_width=True, type="primary"):
            trigger_new_search()

def _status_badge(status):
    """Return HTML for a colored status badge."""
    colors = {
        "running": ("running", "#ffc107"),
        "success": ("success", "#28a745"),
        "error": ("error", "#dc3545"),
        "stopped": ("stopped", "#6c757d"),
    }
    cls, color = colors.get(status, ("error", "#dc3545"))
    return f'<span class="dl-badge dl-badge-{cls}" style="background:{color}22; color:{color}">{status}</span>'

@auto_updating_fragment
def render_download_manager():
    cleanup_finished_downloads()
    running_count = count_running_downloads()

    if not st.session_state.active_downloads:
        return

    st.markdown(f"### 📥 Менеджер загрузок  <span style='font-size:14px; font-weight:400; color:#888;'>Активных: {running_count}</span>", unsafe_allow_html=True)
    
    c_btn1, c_btn2, c_btn3 = st.columns([2, 1, 1])
    with c_btn1:
        if running_count > 0:
            bar = st.progress(0, text="🔄 Идёт скачивание...")
            bar.progress(100, text="🔄 Идёт скачивание...")
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
        if st.button("🗑️ Очистить завершённые", use_container_width=True, type="primary"):
            to_delete = [k for k, v in st.session_state.active_downloads.items() if v['status'] != 'running']
            for k in to_delete:
                st.session_state.active_downloads.pop(k, None)
            st.rerun()

    st.markdown("---")

    for idx, (task_id, task) in enumerate(list(st.session_state.active_downloads.items())):
        if task['status'] == 'running':
            proc = task.get('process')
            if proc and proc.poll() is None:
                try:
                    with open(task['log_file'], 'r', encoding='utf-8') as f:
                        get_clean_status_from_log(f.readlines()[-15:])
                except OSError:
                    pass
            else:
                if os.path.exists(task['file_path']) and os.path.getsize(task['file_path']) > 1024:
                    task['status'] = 'success'
                else:
                    task['status'] = 'error'

        status_emoji = {"running": "⏳", "success": "✅", "error": "❌", "stopped": "⏹️"}.get(task['status'], "❌")
        quote_preview = f" — «{sanitize_html_text(task.get('quote', '')[:30])}...»" if task.get('quote') else ""
        accordion_label = f"{status_emoji} {task['title']}{quote_preview}"

        with st.expander(accordion_label, expanded=(task['status'] == 'running')):
            st.markdown(_status_badge(task['status']), unsafe_allow_html=True)
            
            if os.path.exists(task['log_file']):
                try:
                    with open(task['log_file'], 'r', encoding='utf-8') as f:
                        all_lines = f.readlines()
                        meta = extract_download_metadata(all_lines)
                        if meta["source_title"] or meta["exact_file"]:
                            st.markdown("<div class='source-meta-box'>", unsafe_allow_html=True)
                            if meta["source_title"]:
                                st.markdown(f"**Источник:** `{meta['source_title']}`")
                            if meta["exact_file"]:
                                st.markdown(f"**Файл внутри:** `{meta['exact_file']}`")
                            st.markdown("</div>", unsafe_allow_html=True)
                except (OSError, IOError): pass

            if task['status'] == 'running':
                proc = task.get('process')
                if proc and proc.poll() is None:
                    try:
                        with open(task['log_file'], 'r', encoding='utf-8') as f:
                            st.info(get_clean_status_from_log(f.readlines()[-15:]))
                    except OSError:
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
                except (OSError, FileNotFoundError, RuntimeError):
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

            if task.get('imdb_id') and task.get('orig_start_sec') is not None:
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
                            is_target = (s['id'] == task.get('sub_id')) if task.get('sub_id') else (abs(s['sec'] - task['orig_start_sec']) < 2)
                            prefix = "🎯 " if is_target else "⏱ "
                            
                            if st.button(
                                f"{prefix}[{s['time_str']}] {s['text']}",
                                key=f"fix_dl_{task_id}_{s['id']}",
                                use_container_width=True,
                                type="primary" if is_target else "tertiary" 
                            ):
                                new_offset = task['orig_start_sec'] - s['sec']
                                set_source_offset(task['imdb_id'], task.get('saved_source'), new_offset)
                                
                                for k in list(st.session_state.keys()):
                                    if k.startswith(f"offset_search_{task['imdb_id']}_{task['sub_id']}") or k.startswith(f"offset_fav_{task['imdb_id']}_{task['sub_id']}"):
                                        st.session_state[k] = float(new_offset)

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
                                _active_handles.append(log_handle)
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
            for w in clean_q.split():
                try: text_html = re.sub(f"(?i)({re.escape(w)})", r'<mark style="background-color: #ffd700; color: #000; padding: 0 4px; border-radius: 4px;"><b>\g<0></b></mark>', text_html)
                except re.error: pass
        else:
            for w in re.findall(r'\w+', q):
                if len(w) > 3:
                    try: text_html = re.sub(f"(?i)({re.escape(w)})[а-яА-Яa-zA-Z]*", r'<mark style="background-color: #ffd700; color: #000; padding: 0 2px; border-radius: 3px;"><b>\g<0></b></mark>', text_html)
                    except re.error: pass
                else:
                    try: text_html = re.sub(f"(?i)\\b({re.escape(w)})\\b", r'<mark style="background-color: #ffd700; color: #000; padding: 0 2px; border-radius: 3px;"><b>\g<0></b></mark>', text_html)
                    except re.error: pass

    with st.container(border=True):
        c_head1, c_head2 = st.columns([5, 1])
        with c_head1:
            st.markdown(f"### {display_title} &nbsp;&nbsp; <a href='{imdb_link}' target='_blank' style='font-size:13px; text-decoration:none; opacity:0.7;'>🔗 IMDb</a>", unsafe_allow_html=True)
            st.caption(f"⭐ {rating if rating else '?'} &nbsp;·&nbsp; 🎭 {genres} &nbsp;·&nbsp; 🌍 {countries}")
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
            set_source_offset(imdb_id, saved_source, st.session_state[state_key_offset])

        with c_body:
            st.markdown(
                f"<div style='font-size: 19px; border-left: 4px solid #ff4b4b; padding: 8px 0 8px 18px; "
                f"margin: 12px 0; line-height: 1.5; color: #e0e0e0;'>"
                f"<i>«{text_html}»</i></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='color: #999; font-size: 13px; display: flex; gap: 16px; margin-bottom: 8px;'>"
                f"<span>⏳ <b>{str(start_srt)[:8]}</b></span>"
                f"<span>📏 {seconds_to_hms(srt_to_seconds(end_srt) - srt_to_seconds(start_srt))}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

            with st.expander("💬 Показать полный диалог"):
                context_items = get_surrounding_context(imdb_id, srt_to_seconds(start_srt), s_id, window_sec=45)
                if context_items:
                    for item in context_items:
                        if item["is_target"]: st.markdown(f"**[{item['time_str']}] {item['text']}** ⬅️")
                        else: st.caption(f"[{item['time_str']}] {item['text']}")

            if saved_source:
                st.info(f"📌 Привязан источник: {sanitize_html_text(str(saved_source)[:20])}...")
                if st.button("🗑 Отвязать", key=f"reset_{list_type}_{uid}"): 
                    set_movie_source(imdb_id, None)
                    _, new_off = get_saved_source_info(imdb_id)
                    st.session_state[state_key_offset] = float(new_off)
                    st.rerun()

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
                        new_src = source_options[sel]
                        set_movie_source(imdb_id, new_src)
                        _, new_off = get_saved_source_info(imdb_id)
                        st.session_state[state_key_offset] = float(new_off)
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
                    log_file_path = os.path.join(LOGS_DIR, f"{task_id}_log.txt")

                    cmd = [sys.executable, "-u", "magnet_get.py", "--title", str(title), "--orig_title", str(orig_title or ""), "--year", str(safe_int(year)), "--type", str(m_type), "--season", str(safe_int(season)), "--episode", str(safe_int(ep)), "--start", seconds_to_hms(start_sec), "--duration", str(int(duration)), "--source", source_pref, "--output", expected_file]
                    if saved_source: cmd.extend(["--force_source", saved_source])

                    log_file_handle = open(log_file_path, "w", encoding="utf-8")
                    _active_handles.append(log_file_handle)
                    process = subprocess.Popen(cmd, stdout=log_file_handle, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
                    _active_processes.append(process)

                    st.session_state.active_downloads[task_id] = {
                        "title": f"{display_title} [{str(start_srt)[:8]}]", 
                        "quote": str(text)[:60], 
                        "process": process, 
                        "file_path": expected_file, 
                        "log_file": log_file_path, 
                        "_log_handle": log_file_handle, 
                        "status": "running",
                        "imdb_id": imdb_id,
                        "sub_id": s_id, 
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

tab_search, tab_favs, tab_history, tab_ai, tab_url_dl, tab_title_dl = st.tabs(["🔍 Результаты поиска", "⭐ Моё Избранное", "🕰 История поиска", "🧠 Лаборатория Промптов", "📥 Скачать по ссылке", "🔎 Скачать по названию"])

with tab_search:
    if st.session_state.trigger_search:
        st.session_state.trigger_search = False
        
        add_to_history(st.session_state.last_query, st.session_state.search_mode)
        
        if st.session_state.search_mode == "ИИ-Агент (RAG Пайплайн 🤖)":
            with st.status("🚀 Запуск ИИ-агента...", expanded=True) as status_box:
                st.session_state.search_results = _search_ai_pipeline(
                    st.session_state.last_query, 
                    RESULTS_PER_PAGE, 0, st.session_state.min_rating, st.session_state.t_type, 
                    st.session_state.c_filter, st.session_state.genre_include, st.session_state.genre_exclude, st.session_state.specific_movie, status_box
                )
        else:
            with st.spinner("⚡ Выполняем точный поиск по базе..."):
                st.session_state.search_results = _search_fts(
                    st.session_state.last_query, 
                    RESULTS_PER_PAGE, 0, st.session_state.min_rating, st.session_state.t_type,
                    st.session_state.c_filter, st.session_state.genre_include, st.session_state.genre_exclude, st.session_state.specific_movie, st.session_state.exact_match_checkbox
                )
        
        if not st.session_state.search_results:
            st.warning("😕 Ничего не найдено. Попробуйте: изменить запрос, снять фильтры, переключить режим на «ИИ-Агент» или убрать галочку «Точная фраза».")

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
                            st.session_state.genre_include, st.session_state.genre_exclude, st.session_state.specific_movie, status_box
                        )
                else:
                    with st.spinner("📎 Подгружаем..."):
                        more_results = _search_fts(
                            st.session_state.last_query, RESULTS_PER_PAGE, st.session_state.search_offset,
                            st.session_state.min_rating, st.session_state.t_type, st.session_state.c_filter, 
                            st.session_state.genre_include, st.session_state.genre_exclude, st.session_state.specific_movie, st.session_state.exact_match_checkbox
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


# ── Вкладка: Скачать по ссылке ──
with tab_url_dl:
    st.markdown("### 📥 Скачать фрагмент видео по ссылке")
    st.caption("Вставьте ссылку на видео с YouTube, RuTube или VK Video. Скрипт скачает только нужный отрезок.")

    url = st.text_input(
        "🔗 Ссылка на видео",
        placeholder="https://www.youtube.com/watch?v=... , https://rutube.ru/video/... , https://vk.com/video...",
        key="url_dl_input"
    )

    # Авто-определение платформы
    platform_icons = {"youtube": "▶️ YouTube", "rutube": "📺 RuTube", "vkvideo": "💬 VK Video", "url": "🌐 Другой"}
    detected_platform = "url"
    if url:
        m = re.search(r'(youtube\.com|youtu\.be)', url, re.IGNORECASE)
        if m: detected_platform = "youtube"
        m = re.search(r'rutube\.ru', url, re.IGNORECASE)
        if m: detected_platform = "rutube"
        m = re.search(r'vk\.com/video|vkvideo\.ru', url, re.IGNORECASE)
        if m: detected_platform = "vkvideo"

    if url:
        st.info(f"🎯 Определён источник: {platform_icons.get(detected_platform, '🌐 Другой')}")

    quality = st.selectbox(
        "🎬 Качество видео",
        options=["best", "2160p", "1080p", "720p", "480p", "360p"],
        index=0,
        key="url_dl_quality",
        help="best — максимальное доступное (может быть 4K/8K)"
    )

    def sec_to_hms(s):
        s = int(s)
        return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

    with st.expander("⚙️ Дополнительно"):
        url_full = st.checkbox("📦 Полное видео (без обрезки)", value=False, key="url_dl_full",
                               help="Скачать видео целиком без вырезания фрагмента.")
        if not url_full:
            url_pad_before = st.number_input(
                "Захватить раньше начала (сек)", min_value=0, max_value=300,
                value=2, step=1, key="url_pad",
                help="Добавит N секунд до указанного времени — пространство для монтажных склеек"
            )
        else:
            url_pad_before = 0

    if not url_full:
        st.markdown("**⏱ Параметры обрезки**")
        st.caption("Укажите начало и длительность фрагмента.")

        c_h, c_m, c_s = st.columns(3)
        with c_h:
            start_h = st.number_input("Часы", min_value=0, max_value=23, value=0, key="url_start_h")
        with c_m:
            start_m = st.number_input("Минуты", min_value=0, max_value=59, value=1, key="url_start_m")
        with c_s:
            start_s = st.number_input("Секунды", min_value=0, max_value=59, value=0, key="url_start_s")

        dur_sec = st.number_input("Длительность (сек)", min_value=1, max_value=86400, value=60, step=5, key="url_dur")

        start_sec_raw = start_h*3600 + start_m*60 + start_s
        actual_start = max(0, start_sec_raw - url_pad_before)
        actual_duration = dur_sec + (start_sec_raw - actual_start)
        st.info(f"📐 Будет скачан отрезок: **{sec_to_hms(actual_start)}** → "
                f"**{sec_to_hms(actual_start + actual_duration)}** "
                f"(+{url_pad_before} сек запаса слева)")
    else:
        start_h = 0; start_m = 0; start_s = 0
        dur_sec = 0
        start_sec_raw = 0
        actual_start = 0
        actual_duration = 0

    all_valid = bool(url and url.strip()) and (url_full or dur_sec > 0)
    btn_label = "⬇️ Скачать видео" if url_full else "⬇️ Скачать отрезок"

    if st.button(btn_label, type="primary", use_container_width=True, key="url_dl_btn",
                 disabled=not all_valid):
        if count_running_downloads() >= MAX_ACTIVE_DOWNLOADS:
            st.error(f"❌ Максимум {MAX_ACTIVE_DOWNLOADS} загрузок.")
        else:
            task_id = f"urldl_{int(time.time())}"
            sanitized_url = re.sub(r'[^a-zA-Z0-9]', '_', url.strip())[:30]
            expected_file = os.path.join(CLIPS_DIR, f"url_{sanitized_url}_{task_id[-6:]}.mp4")
            log_file_path = os.path.join(LOGS_DIR, f"{task_id}_log.txt")

            cmd = [
                sys.executable, "-u", "magnet_get.py",
                "--url", url.strip(),
                "--format", quality,
                "--output", expected_file
            ]
            if url_full:
                cmd.append("--full")
            else:
                cmd.extend(["--start", sec_to_hms(actual_start),
                            "--duration", str(actual_duration)])

            log_file_handle = open(log_file_path, "w", encoding="utf-8")
            _active_handles.append(log_file_handle)
            process = subprocess.Popen(cmd, stdout=log_file_handle, stderr=subprocess.STDOUT,
                                       text=True, env=os.environ.copy())
            _active_processes.append(process)

            clip_label = "Полное видео" if url_full else f"{sec_to_hms(actual_start)} — {sec_to_hms(actual_start + actual_duration)}"
            st.session_state.active_downloads[task_id] = {
                "title": f"URL: {url.strip()[:50]}",
                "quote": clip_label,
                "process": process,
                "file_path": expected_file,
                "log_file": log_file_path,
                "_log_handle": log_file_handle,
                "status": "running",
            }
            st.toast("📥 Загрузка по ссылке начата! Смотрите в верхнюю панель.")

    # Кнопка открыть папку
    if st.button("📂 Открыть папку со всеми клипами", use_container_width=False, key="url_open_folder"):
        abs_path = os.path.abspath(CLIPS_DIR)
        if sys.platform == "win32":
            os.startfile(abs_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", abs_path])
        else:
            subprocess.Popen(["xdg-open", abs_path])

    st.markdown("---")
    # Показываем превью скачанных файлов по ссылкам
    url_dl_files = sorted(
        [f for f in os.listdir(CLIPS_DIR) if f.startswith("url_") and f.endswith(".mp4")],
        key=lambda f: os.path.getmtime(os.path.join(CLIPS_DIR, f)), reverse=True
    )[:5]
    if url_dl_files:
        st.markdown("---")
        st.markdown("**📂 Последние скачанные по ссылке:**")
        for f in url_dl_files:
            fpath = os.path.join(CLIPS_DIR, f)
            size_kb = os.path.getsize(fpath) / 1024
            st.markdown(f"- `{f}` ({size_kb:.0f} KB)")


# ── Вкладка: Скачать по названию ──
with tab_title_dl:
    def sec_to_hms(s):
        s = int(s)
        return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

    st.markdown("### 🔎 Скачать по названию")
    st.caption("Найти и скачать видео по названию фильма/сериала с RuTube или через торренты (RuTracker).")

    title_query = st.text_input(
        "Название фильма или сериала",
        placeholder="Интерстеллар, Matrix, 1+1...",
        key="title_dl_query"
    )

    title_source = st.radio(
        "Источник",
        options=["rutube", "torrent"],
        format_func=lambda x: {"rutube": "📺 RuTube", "torrent": "⚡ Торренты (RuTracker)"}.get(x, x),
        horizontal=True,
        key="title_dl_source"
    )

    title_quality = st.selectbox(
        "🎬 Качество видео",
        options=["best", "2160p", "1080p", "720p", "480p", "360p"],
        index=0,
        key="title_dl_quality",
        help="best — максимальное доступное"
    )

    with st.expander("⚙️ Дополнительно", expanded=False):
        title_full = st.checkbox("📦 Полное видео (без обрезки)", value=False, key="title_dl_full",
                                 help="Скачать целиком. Отметьте, чтобы вырезать фрагмент (по умолчанию выключено).")
        title_pick = st.checkbox("🔎 Выбрать видео из результатов поиска", value=False, key="title_dl_pick",
                                 help="Показать список найденных видео и выбрать нужное перед скачиванием.")
        if not title_full:
            title_pad = st.number_input(
                "Захватить раньше начала (сек)", min_value=0, max_value=300,
                value=2, step=1, key="title_pad",
                help="Добавит N секунд до указанного времени — пространство для монтажных склеек"
            )
        else:
            title_pad = 0

    if not title_full:
        st.markdown("**⏱ Параметры обрезки**")
        st.caption("Укажите начало и длительность фрагмента.")

        c_h, c_m, c_s = st.columns(3)
        with c_h:
            title_start_h = st.number_input("Часы", min_value=0, max_value=23, value=0, key="title_start_h")
        with c_m:
            title_start_m = st.number_input("Минуты", min_value=0, max_value=59, value=1, key="title_start_m")
        with c_s:
            title_start_s = st.number_input("Секунды", min_value=0, max_value=59, value=0, key="title_start_s")

        title_dur_sec = st.number_input("Длительность (сек)", min_value=1, max_value=86400, value=60, step=5, key="title_dur")

        raw_start = title_start_h*3600 + title_start_m*60 + title_start_s
        actual_start = max(0, raw_start - title_pad)
        actual_dur = title_dur_sec + (raw_start - actual_start)
        st.info(f"📐 Будет скачан отрезок: **{sec_to_hms(actual_start)}** → "
                f"**{sec_to_hms(actual_start + actual_dur)}** "
                f"(+{title_pad} сек запаса слева)")
    else:
        title_start_h = 0; title_start_m = 0; title_start_s = 0
        title_dur_sec = 0
        raw_start = 0
        actual_start = 0
        actual_dur = 0

    # ── Режим выбора из результатов ──
    if title_pick and title_query and title_query.strip():
        if st.button("🔍 Искать результаты", use_container_width=True, key="title_search_btn"):
            with st.status("🔎 Поиск видео...", expanded=True) as search_status:
                try:
                    cmd = [
                        sys.executable, "-u", "magnet_get.py",
                        "--title", title_query.strip(),
                        "--source", title_source,
                        "--show-results"
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    results = json.loads(res.stdout) if res.stdout.strip() else []
                except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
                    results = []
                    st.error(f"Ошибка поиска: {e}")

            if results:
                search_status.update(label=f"🔎 Найдено {len(results)} видео", state="complete", expanded=False)
                st.session_state["title_search_results"] = results
            else:
                search_status.update(label="❌ Ничего не найдено", state="error", expanded=True)
                st.session_state["title_search_results"] = []

        # Показываем результаты, если есть
        saved_results = st.session_state.get("title_search_results", [])
        if saved_results:
            st.markdown("**Результаты поиска:**")
            options = []
            for i, r in enumerate(saved_results):
                src_icon = {"youtube": "▶️", "rutube": "📺", "torrent": "⚡"}.get(r.get("source", ""), "🔗")
                dur_str = f", {r['duration']} сек" if r.get("duration") else ""
                seeds_str = f", 👤 {r['seeds']} сидов" if r.get("seeds") is not None else ""
                size_str = f", 💾 {r['size_gb']} GB" if r.get("size_gb") else ""
                label = f"{src_icon} [{r.get('source', '?')}] {r.get('title', '?')}{dur_str}{seeds_str}{size_str}"
                options.append(label)

            selected_idx = st.selectbox("Выберите видео для скачивания", options=options, key="title_pick_select")

            if st.button("⬇️ Скачать выбранное", type="primary", use_container_width=True, key="title_pick_dl_btn"):
                sel = saved_results[options.index(selected_idx)]
                force_source = f"{sel['source']}:{sel['id']}"

                if count_running_downloads() >= MAX_ACTIVE_DOWNLOADS:
                    st.error(f"❌ Максимум {MAX_ACTIVE_DOWNLOADS} загрузок.")
                else:
                    task_id = f"titledl_{int(time.time())}"
                    sanitized_name = re.sub(r'[^a-zA-Z0-9а-яА-Я]', '_', title_query.strip())[:40]
                    expected_file = os.path.join(CLIPS_DIR, f"title_{sanitized_name}_{task_id[-6:]}.mp4")
                    log_file_path = os.path.join(LOGS_DIR, f"{task_id}_log.txt")

                    cmd = [
                        sys.executable, "-u", "magnet_get.py",
                        "--title", title_query.strip(),
                        "--source", title_source,
                        "--format", title_quality,
                        "--force-source", force_source,
                        "--output", expected_file
                    ]
                    if title_full:
                        cmd.append("--full")
                    else:
                        cmd.extend(["--start", sec_to_hms(actual_start),
                                    "--duration", str(actual_dur)])

                    log_file_handle = open(log_file_path, "w", encoding="utf-8")
                    _active_handles.append(log_file_handle)
                    process = subprocess.Popen(cmd, stdout=log_file_handle, stderr=subprocess.STDOUT,
                                               text=True, env=os.environ.copy())
                    _active_processes.append(process)

                    st.session_state.active_downloads[task_id] = {
                        "title": f"🔎 {title_query.strip()[:50]}",
                        "quote": sel['title'][:60],
                        "process": process,
                        "file_path": expected_file,
                        "log_file": log_file_path,
                        "_log_handle": log_file_handle,
                        "status": "running",
                    }
                    st.toast("📥 Загрузка начата! Смотрите в верхнюю панель.")

    # ── Обычный режим (без выбора) ──
    elif not title_pick:
        # Кнопка открыть папку
        if st.button("📂 Открыть папку со всеми клипами", use_container_width=False, key="title_open_folder"):
            abs_path = os.path.abspath(CLIPS_DIR)
            if sys.platform == "win32":
                os.startfile(abs_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", abs_path])
            else:
                subprocess.Popen(["xdg-open", abs_path])

        st.markdown("---")

        can_download = bool(title_query and title_query.strip())
        if st.button("🔍 Найти и скачать", type="primary", use_container_width=True, key="title_dl_btn",
                     disabled=not can_download):
            if count_running_downloads() >= MAX_ACTIVE_DOWNLOADS:
                st.error(f"❌ Максимум {MAX_ACTIVE_DOWNLOADS} загрузок.")
            else:
                task_id = f"titledl_{int(time.time())}"
                sanitized_name = re.sub(r'[^a-zA-Z0-9а-яА-Я]', '_', title_query.strip())[:40]
                expected_file = os.path.join(CLIPS_DIR, f"title_{sanitized_name}_{task_id[-6:]}.mp4")
                log_file_path = os.path.join(LOGS_DIR, f"{task_id}_log.txt")

                cmd = [
                    sys.executable, "-u", "magnet_get.py",
                    "--title", title_query.strip(),
                    "--source", title_source,
                    "--format", title_quality,
                    "--output", expected_file
                ]
                if title_full:
                    cmd.append("--full")
                else:
                    cmd.extend(["--start", sec_to_hms(actual_start),
                                "--duration", str(actual_dur)])

                log_file_handle = open(log_file_path, "w", encoding="utf-8")
                _active_handles.append(log_file_handle)
                process = subprocess.Popen(cmd, stdout=log_file_handle, stderr=subprocess.STDOUT,
                                           text=True, env=os.environ.copy())
                _active_processes.append(process)

                st.session_state.active_downloads[task_id] = {
                    "title": f"🔎 {title_query.strip()[:50]}",
                    "quote": f"Источник: {title_source}",
                    "process": process,
                    "file_path": expected_file,
                    "log_file": log_file_path,
                    "_log_handle": log_file_handle,
                    "status": "running",
                }
                st.toast("📥 Поиск и загрузка начаты! Смотрите в верхнюю панель.")


# Download status updates handled by auto_updating_fragment (run_every=2s)
# No script-level rerun needed — avoids infinite loop
pass