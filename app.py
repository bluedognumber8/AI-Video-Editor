import streamlit as st
import sqlite3
import pandas as pd
import re
import datetime
import subprocess
import sys
import os
import time
import json
import chromadb
from sentence_transformers import SentenceTransformer
import torch

# --- НАСТРОЙКИ ---
DB_NAME = 'movies_master.sqlite'
CHROMA_DIR = './chroma_db'
CLIPS_DIR = 'clips'
DOWNLOAD_TIMEOUT = 300
SETTINGS_FILE = "user_settings.json"
SUB_ID_RANGE = 5000
RESULTS_PER_PAGE = 10 # Количество результатов на одну подгрузку

st.set_page_config(page_title="AI-Режиссер Монтажа", page_icon="🎬", layout="wide")

# =====================================================================
# 💾 ПЕРСИСТЕНТНЫЕ НАСТРОЙКИ И СОСТОЯНИЕ
# =====================================================================
DEFAULT_SETTINGS = {
    "search_mode": "По словам (Быстро ⚡️)",
    "specific_movie": "Все фильмы",
    "t_type": "Все",
    "c_filter": "Все",
    "genre_filter": "Любой",
    "min_rating": 0.0,
    "pad_start": 30.0,
    "pad_end": 30.0,
    "source_pref": "all",
}

def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            return {**DEFAULT_SETTINGS, **saved}
    except Exception:
        pass
    return dict(DEFAULT_SETTINGS)

def save_settings(settings_dict):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings_dict, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

if "settings_loaded" not in st.session_state:
    st.session_state.settings_loaded = True
    for key, val in load_settings().items():
        st.session_state[key] = val

# Инициализация стейта для пагинации
if "search_results" not in st.session_state:
    st.session_state.search_results = []
if "search_offset" not in st.session_state:
    st.session_state.search_offset = 0
if "last_query" not in st.session_state:
    st.session_state.last_query = ""

def on_settings_change():
    save_settings({k: st.session_state[k] for k in DEFAULT_SETTINGS if k in st.session_state})
    # При смене фильтров сбрасываем результаты
    st.session_state.search_results = []
    st.session_state.search_offset = 0

# =====================================================================
# 🧠 ИИ И БД
# =====================================================================
@st.cache_resource(show_spinner="Загрузка нейросети...")
def load_ai_model():
    device = 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    return SentenceTransformer('intfloat/multilingual-e5-base', device=device)

@st.cache_resource(show_spinner=False)
def load_chroma_client():
    if not os.path.exists(CHROMA_DIR): return None
    return chromadb.PersistentClient(path=CHROMA_DIR)

ai_model = load_ai_model()
chroma_client = load_chroma_client()
chroma_collection = chroma_client.get_collection(name="subtitles_semantic") if chroma_client else None

# =====================================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================
def srt_to_seconds(srt_time_str):
    try:
        srt_time_str = str(srt_time_str).strip().replace('.', ',')
        time_part, ms_part = srt_time_str.split(',', 1) if ',' in srt_time_str else (srt_time_str, '0')
        parts = map(int, time_part.split(':'))
        parts = list(parts)
        if len(parts) == 3: h, m, s = parts
        elif len(parts) == 2: h, m, s = 0, parts[0], parts[1]
        else: h, m, s = 0, 0, parts[0]
        return h * 3600 + m * 60 + s + int(ms_part) / 1000.0
    except: return 0.0

def seconds_to_hms(seconds):
    return str(datetime.timedelta(seconds=int(seconds))) if seconds >= 0 else "0:00:00"

def safe_int(val, default=0):
    try: return int(val) if val is not None else default
    except: return default

@st.cache_data
def get_movie_titles():
    if not os.path.exists(DB_NAME): return []
    try:
        with sqlite3.connect(DB_NAME) as conn:
            return pd.read_sql_query("SELECT DISTINCT title_ru FROM movies ORDER BY title_ru ASC", conn)['title_ru'].tolist()
    except: return []

# Функции работы с БД...
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
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("DELETE FROM movie_sources WHERE imdb_id = ?", (imdb_id,))
    except: pass

# =====================================================================
# 🔍 ПОИСК (С ДЕДУПЛИКАЦИЕЙ И ПАГИНАЦИЕЙ)
# =====================================================================
def perform_search(query_text, search_mode, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie):
    if search_mode == "По словам (Быстро ⚡️)":
        return _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie)
    else:
        return _search_semantic(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie)

def _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
    except: return []

    search_query = f"{query_text}*"
    params = [search_query]

    # ДЕДУПЛИКАЦИЯ: Группируем результаты по imdb_id и 30-секундным интервалам (чтобы отсеять дубли из разных сабов)
    sql = """
        WITH top_matches AS (
            SELECT rowid FROM subtitles_fts WHERE text MATCH ? ORDER BY rank LIMIT 2000
        ),
        ranked AS (
            SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
                   s.start_time, s.end_time, s.text, m.imdb_id, m.countries, m.title_original, s.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.imdb_id, 
                       CAST(SUBSTR(s.start_time, 1, 2) AS INTEGER)*3600 + CAST(SUBSTR(s.start_time, 4, 2) AS INTEGER)*60 + CAST(SUBSTR(s.start_time, 7, 2) AS INTEGER) / 30
                       ORDER BY s.start_time ASC
                   ) AS rn
            FROM top_matches tm
            JOIN subtitles s ON s.id = tm.rowid
            JOIN movies m ON s.imdb_id = m.imdb_id
            WHERE 1=1
    """

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

    sql += f"""
        )
        SELECT * FROM ranked WHERE rn = 1
        ORDER BY rating DESC LIMIT {limit} OFFSET {offset}
    """
    
    try:
        cursor.execute(sql, params)
        results = cursor.fetchall()
        return [(row, 100.0) for row in results]
    except Exception as e:
        return []
    finally:
        conn.close()

def _search_semantic(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie):
    if not chroma_collection: return []
    query_vector = ai_model.encode([f"query: {query_text}"], normalize_embeddings=True).tolist()

    and_conditions = []
    if min_rating > 0: and_conditions.append({"rating": {"$gte": min_rating}})
    if t_type != "Все": and_conditions.append({"type": {"$eq": "movie" if t_type == "Фильмы" else "tv"}})
    if country_filter == "Наше (RU/SU)": and_conditions.append({"countries": {"$in": ["RU", "SU", "SUHH"]}})
    elif country_filter == "Зарубежное": and_conditions.append({"countries": {"$nin": ["RU", "SU", "SUHH", "Unknown"]}})
    if genre_filter != "Любой": and_conditions.append({"genres": {"$contains": genre_filter}})

    where_filters = None
    if len(and_conditions) == 1: where_filters = and_conditions[0]
    elif len(and_conditions) > 1: where_filters = {"$and": and_conditions}

    # Для дедупликации просим у Chroma больше результатов
    fetch_limit = (offset + limit) * 4 

    try:
        chroma_result = chroma_collection.query(
            query_embeddings=query_vector, n_results=fetch_limit, include=["distances"], where=where_filters
        )
    except: return []

    found_ids = list(chroma_result["ids"][0])
    if not found_ids: return []

    try:
        conn = sqlite3.connect(DB_NAME)
        placeholders = ",".join("?" for _ in found_ids)
        sql = f"""
            SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
                   s.start_time, s.end_time, s.text, m.imdb_id, m.countries, m.title_original, s.id
            FROM subtitles s JOIN movies m ON s.imdb_id = m.imdb_id
            WHERE s.id IN ({placeholders})
        """
        params = list(found_ids)
        if specific_movie != "Все фильмы":
            sql += " AND m.title_ru = ?"; params.append(specific_movie)
        
        cursor = conn.cursor()
        cursor.execute(sql, params)
        sqlite_rows = cursor.fetchall()
        conn.close()
    except: return []

    id_to_row = {str(row[13]): row for row in sqlite_rows}
    
    # ДЕДУПЛИКАЦИЯ на уровне Python
    unique_results = []
    seen_chunks = set()
    
    for f_id, dist in zip(chroma_result["ids"][0], chroma_result["distances"][0]):
        if f_id in id_to_row:
            row = id_to_row[f_id]
            imdb_id = row[10]
            sec = srt_to_seconds(row[7])
            chunk = int(sec / 30) # 30 секундный чанк
            unique_key = (imdb_id, chunk)
            
            if unique_key not in seen_chunks:
                seen_chunks.add(unique_key)
                unique_results.append((row, round((1.0 - dist) * 100, 1)))

    # Возвращаем только нужный "кусок" страницы (пагинация)
    return unique_results[offset : offset + limit]


# =====================================================================
# 🎨 ИНТЕРФЕЙС
# =====================================================================
st.title("🎬 AI-Режиссер Монтажа (Студия 5.0)")

with st.sidebar:
    st.header("⚙️ Режим Поиска")
    search_mode = st.radio("Как ищем?", ["По словам (Быстро ⚡️)", "По смыслу (Нейросеть 🧠)"], key="search_mode", on_change=on_settings_change)

    st.markdown("---")
    st.header("🎛 Фильтры")
    all_movies = ["Все фильмы"] + get_movie_titles()
    specific_movie = st.selectbox("📌 В конкретном кино:", all_movies, key="specific_movie", on_change=on_settings_change)
    t_type = st.radio("🎞 Тип медиа:", ["Все", "Фильмы", "Сериалы"], horizontal=True, key="t_type", on_change=on_settings_change)
    c_filter = st.radio("🌍 Страна:", ["Все", "Наше (RU/SU)", "Зарубежное"], key="c_filter", on_change=on_settings_change)
    genre_filter = st.selectbox("🎭 Жанр:", ["Любой", "Comedy", "Drama", "Action", "Sci-Fi", "Horror", "Romance", "Crime"], key="genre_filter", on_change=on_settings_change)
    min_rating = st.slider("⭐️ Мин. рейтинг IMDb:", 0.0, 10.0, step=0.1, key="min_rating", on_change=on_settings_change)

    st.markdown("---")
    st.header("✂️ Хронометраж")
    pad_start = st.number_input("Секунд ДО фразы:", 0.0, 120.0, step=5.0, key="pad_start", on_change=on_settings_change)
    pad_end = st.number_input("Секунд ПОСЛЕ фразы:", 0.0, 120.0, step=5.0, key="pad_end", on_change=on_settings_change)
    source_pref = st.radio("🌐 Источник:", ["all", "torrent", "youtube"], format_func=lambda x: {"all": "Везде", "torrent": "Только Torrent", "youtube": "Только YouTube"}[x], key="source_pref", on_change=on_settings_change)


# ГЛАВНЫЙ ЭКРАН ПОИСКА
query = st.text_input("🔍 Поиск фрагмента:", placeholder="Введите точную цитату или опишите сцену...", value=st.session_state.last_query)

col1, col2 = st.columns([1, 5])
with col1:
    if st.button("Найти 🚀", use_container_width=True, type="primary"):
        if query:
            st.session_state.last_query = query
            st.session_state.search_offset = 0
            with st.spinner("Ищем лучшие кадры..."):
                st.session_state.search_results = perform_search(
                    query, st.session_state.search_mode, RESULTS_PER_PAGE, 0,
                    st.session_state.min_rating, st.session_state.t_type, st.session_state.c_filter,
                    st.session_state.genre_filter, st.session_state.specific_movie
                )

# ОТРИСОВКА РЕЗУЛЬТАТОВ
if st.session_state.search_results:
    st.success(f"Найдено уникальных сцен: {len(st.session_state.search_results)} (показана страница {st.session_state.search_offset//RESULTS_PER_PAGE + 1})")

    for i, (row, similarity) in enumerate(st.session_state.search_results):
        (title, year, genres, rating, m_type, season, ep, start_srt, end_srt, text, imdb_id, countries, orig_title, s_id) = row[:14]
        
        display_title = f"📺 {title} (S{safe_int(season):02d}E{safe_int(ep):02d})" if m_type == "tv" else f"🎬 {title} ({safe_int(year)})"
        text_html = re.sub(f"(?i)({re.escape(query)}[а-яА-Яa-zA-Z]*)", r'<mark style="background-color: #ffd700; color: #000; border-radius: 3px; padding: 0 2px;"><b>\1</b></mark>', text) if search_mode == "По словам (Быстро ⚡️)" else text
        sim_badge = f" | 🧠 Смысл: {similarity}%" if search_mode == "По смыслу (Нейросеть 🧠)" else ""

        # Улучшенная карточка результата
        with st.container(border=True):
            st.subheader(f"{display_title} | ★ {rating if rating else 0.0} {sim_badge}")
            
            c1, c2 = st.columns([3, 2])
            saved_source, saved_offset = get_saved_source_info(imdb_id)
            
            with c1:
                st.markdown(f"**Таймкод:** `{start_srt}` ➡️ `{end_srt}`")
                st.markdown(f"**Цитата:** 🗣️ _{text_html}_", unsafe_allow_html=True)
                
                if saved_source:
                    st.info(f"📌 **Источник закреплен:** {str(saved_source)[:20]}... | Сдвиг: {saved_offset} сек.")
                    if st.button("🗑 Отвязать торрент", key=f"reset_{imdb_id}_{i}_{st.session_state.search_offset}", size="small"):
                        delete_source_info(imdb_id)
                        st.rerun()

            with c2:
                # Настройки скачивания конкретного клипа
                with st.expander("🛠 Настройки сдвига и рассинхрона", expanded=False):
                    manual_offset = st.number_input("Ручной сдвиг (+/- сек):", min_value=-600.0, max_value=600.0, value=float(saved_offset), step=0.5, key=f"man_{imdb_id}_{i}_{st.session_state.search_offset}")
                
                if st.button("⬇️ Подготовить и Скачать клип", key=f"dl_{imdb_id}_{start_srt}_{i}_{st.session_state.search_offset}", use_container_width=True, type="secondary"):
                    start_sec = max(0, srt_to_seconds(start_srt) - pad_start + manual_offset)
                    end_sec = srt_to_seconds(end_srt) + pad_end + manual_offset
                    duration = max(1, end_sec - start_sec)
                    
                    # Ожидаемое имя файла
                    safe_name = "".join(c for c in f"{title}_{year}" if c.isalnum() or c in " _-").strip().replace(" ", "_")
                    expected_file = os.path.join(CLIPS_DIR, f"{safe_name}_clip.mp4")
                    if os.path.exists(expected_file): os.remove(expected_file)

                    # Логика запуска magnet_get.py (остается вашей, добавляем красивый прогресс)
                    with st.status("⏳ Загрузка и нарезка видео...", expanded=True) as status:
                        st.write("Инициализация скрипта-загрузчика...")
                        cmd = [sys.executable, "-u", "magnet_get.py", 
                               "--title", str(title), "--orig_title", str(orig_title or ""), 
                               "--year", str(safe_int(year)), "--type", str(m_type), 
                               "--season", str(safe_int(season)), "--episode", str(safe_int(ep)), 
                               "--start", seconds_to_hms(start_sec), "--duration", str(int(duration)), 
                               "--source", source_pref]
                        if saved_source: cmd.extend(["--force_source", saved_source])

                        try:
                            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                            for line in process.stdout:
                                st.code(line.strip(), language="log")
                            process.wait(timeout=DOWNLOAD_TIMEOUT)
                        except Exception as e:
                            st.error(f"Ошибка процесса: {e}")

                        if os.path.exists(expected_file) and os.path.getsize(expected_file) > 1024:
                            status.update(label="✅ Успешно скачано!", state="complete")
                            st.video(expected_file)
                            with open(expected_file, "rb") as file:
                                st.download_button("💾 Сохранить файл на ПК", data=file, file_name=os.path.basename(expected_file), mime="video/mp4", type="primary")
                        else:
                            status.update(label="❌ Ошибка скачивания", state="error")

    # --- КНОПКА ПАГИНАЦИИ ---
    st.markdown("---")
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        if st.button("🔄 Загрузить еще 10 сцен...", use_container_width=True):
            st.session_state.search_offset += RESULTS_PER_PAGE
            with st.spinner("Подгружаем еще..."):
                more_results = perform_search(
                    st.session_state.last_query, st.session_state.search_mode, RESULTS_PER_PAGE, st.session_state.search_offset,
                    st.session_state.min_rating, st.session_state.t_type, st.session_state.c_filter,
                    st.session_state.genre_filter, st.session_state.specific_movie
                )
                if more_results:
                    st.session_state.search_results.extend(more_results)
                    st.rerun()
                else:
                    st.info("Больше результатов нет.")