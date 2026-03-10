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

# Диапазон ID для ограничения поиска в пределах одного файла субтитров
# Типичный файл субтитров = 500-2000 строк, берём с запасом
SUB_ID_RANGE = 5000

st.set_page_config(page_title="AI-Режиссер Монтажа", page_icon="🎬", layout="wide")

# =====================================================================
# 💾 ПЕРСИСТЕНТНЫЕ НАСТРОЙКИ
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
    saved = load_settings()
    for key, val in saved.items():
        if key not in st.session_state:
            st.session_state[key] = val


def on_settings_change():
    current = {}
    for key in DEFAULT_SETTINGS:
        if key in st.session_state:
            current[key] = st.session_state[key]
    save_settings(current)


# =====================================================================
# 🧠 ИИ И БД
# =====================================================================
@st.cache_resource(show_spinner=False)
def load_ai_model():
    device = 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    return SentenceTransformer('intfloat/multilingual-e5-base', device=device)


@st.cache_resource(show_spinner=False)
def load_chroma_client():
    if not os.path.exists(CHROMA_DIR):
        return None
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
        if ',' in srt_time_str:
            time_part, ms_part = srt_time_str.split(',', 1)
        else:
            time_part, ms_part = srt_time_str, '0'
        parts = time_part.split(':')
        if len(parts) == 3:
            h, m, s = map(int, parts)
        elif len(parts) == 2:
            h, m, s = 0, int(parts[0]), int(parts[1])
        elif len(parts) == 1:
            h, m, s = 0, 0, int(parts[0])
        else:
            return 0.0
        return h * 3600 + m * 60 + s + int(ms_part) / 1000.0
    except (ValueError, IndexError, TypeError):
        return 0.0


def seconds_to_hms(seconds):
    try:
        return str(datetime.timedelta(seconds=int(seconds)))
    except (ValueError, TypeError):
        return "0:00:00"


def safe_int(value, default=0):
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default


@st.cache_data
def get_movie_titles():
    if not os.path.exists(DB_NAME):
        return []
    try:
        conn = sqlite3.connect(DB_NAME)
        df = pd.read_sql_query(
            "SELECT DISTINCT title_ru FROM movies ORDER BY title_ru ASC", conn
        )
        conn.close()
        return df['title_ru'].tolist()
    except Exception:
        return []


def get_expected_filename(title, year, m_type, season, episode):
    season_int = safe_int(season)
    episode_int = safe_int(episode)
    year_int = safe_int(year)
    if m_type == 'tv' and season_int > 0:
        safe_name = f"{title}_S{season_int:02d}E{episode_int:02d}"
    else:
        safe_name = f"{title}_{year_int}" if year_int > 0 else str(title)
    safe_name = "".join(
        c for c in safe_name if c.isalnum() or c in " _-"
    ).strip().replace(" ", "_")
    return os.path.join(CLIPS_DIR, f"{safe_name}_clip.mp4")


# --- ФУНКЦИИ ПРИВЯЗКИ ИСТОЧНИКОВ ---
def get_saved_source_info(imdb_id):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='movie_sources'"
        )
        if not cursor.fetchone():
            conn.close()
            return None, 0.0
        cursor.execute(
            "SELECT source_id, offset_sec FROM movie_sources WHERE imdb_id = ?",
            (imdb_id,),
        )
        row = cursor.fetchone()
        conn.close()
        return (row[0], row[1]) if row else (None, 0.0)
    except Exception:
        return None, 0.0


def save_source_info(imdb_id, source_id, offset_sec):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS movie_sources "
            "(imdb_id TEXT PRIMARY KEY, source_id TEXT, offset_sec REAL)"
        )
        cursor.execute(
            "INSERT OR REPLACE INTO movie_sources (imdb_id, source_id, offset_sec) "
            "VALUES (?, ?, ?)",
            (imdb_id, source_id, offset_sec),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def delete_source_info(imdb_id):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM movie_sources WHERE imdb_id = ?", (imdb_id,)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# --- КОНТЕКСТ И ПОИСК ФРАЗ (ТОЛЬКО ТЕКУЩИЙ ФАЙЛ СУБТИТРОВ) ---
def get_surrounding_context(imdb_id, target_start_sec, target_sub_id, window_sec=90):
    """
    Ищет контекст ТОЛЬКО в пределах одного файла субтитров.
    Ограничивает по диапазону ID вокруг target_sub_id,
    чтобы не смешивать разные переводы.
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT start_time, text
            FROM subtitles
            WHERE imdb_id = ?
              AND id BETWEEN ? AND ?
            ORDER BY start_time ASC
            """,
            (imdb_id, target_sub_id - SUB_ID_RANGE, target_sub_id + SUB_ID_RANGE),
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception:
        return []

    context_items = []
    seen_texts = set()
    for r_start, r_text in rows:
        r_sec = srt_to_seconds(r_start)
        if abs(r_sec - target_start_sec) <= window_sec and r_text not in seen_texts:
            seen_texts.add(r_text)
            is_target = abs(r_sec - target_start_sec) < 5
            time_display = str(r_start)[:8] if r_start else "00:00:00"
            text_display = str(r_text)[:70] if r_text else ""
            context_items.append({
                "sec": r_sec,
                "label": f"[{time_display}] {text_display}...",
                "text": r_text,
                "time_str": time_display,
                "is_target": is_target,
            })
    return context_items


def search_phrase_in_movie(imdb_id, phrase, anchor_sub_id):
    """
    Глубокий поиск фразы ТОЛЬКО в текущем файле субтитров.
    anchor_sub_id — ID строки из результата поиска,
    ограничивает поиск диапазоном ±SUB_ID_RANGE.
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT start_time, text
            FROM subtitles
            WHERE imdb_id = ?
              AND id BETWEEN ? AND ?
              AND text LIKE ?
            ORDER BY start_time
            LIMIT 20
            """,
            (
                imdb_id,
                anchor_sub_id - SUB_ID_RANGE,
                anchor_sub_id + SUB_ID_RANGE,
                f"%{phrase}%",
            ),
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


# =====================================================================
# 🔍 ПОИСК
# =====================================================================
@st.cache_data(show_spinner=False)
def perform_search(
    query_text, search_mode, limit, min_rating,
    t_type, country_filter, genre_filter, specific_movie,
):
    if search_mode == "По словам (Быстро ⚡️)":
        return _search_fts(
            query_text, limit, min_rating, t_type,
            country_filter, genre_filter, specific_movie,
        )
    else:
        return _search_semantic(
            query_text, limit, min_rating, t_type,
            country_filter, genre_filter, specific_movie,
        )


def _search_fts(
    query_text, limit, min_rating, t_type,
    country_filter, genre_filter, specific_movie,
):
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
    except Exception:
        return []

    search_query = f"{query_text}*"
    params = [search_query]

    sql = """
        WITH top_matches AS (
            SELECT rowid FROM subtitles_fts
            WHERE text MATCH ? ORDER BY rank LIMIT 500
        ),
        ranked AS (
            SELECT m.title_ru, m.year, m.genres, m.rating, m.type,
                   m.season, m.episode,
                   s.start_time, s.end_time, s.text,
                   m.imdb_id, m.countries, m.title_original, s.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.imdb_id, SUBSTR(s.start_time, 1, 5)
                       ORDER BY s.start_time ASC
                   ) AS rn
            FROM top_matches tm
            JOIN subtitles s ON s.id = tm.rowid
            JOIN movies m ON s.imdb_id = m.imdb_id
            WHERE 1=1
    """

    if min_rating > 0:
        sql += " AND m.rating >= ?"
        params.append(min_rating)
    if t_type != "Все":
        sql += " AND m.type = ?"
        params.append("movie" if t_type == "Фильмы" else "tv")
    if country_filter == "Наше (RU/SU)":
        sql += " AND (m.countries LIKE '%RU%' OR m.countries LIKE '%SU%')"
    elif country_filter == "Зарубежное":
        sql += (
            " AND (m.countries NOT LIKE '%RU%' "
            "AND m.countries NOT LIKE '%SU%' OR m.countries IS NULL)"
        )
    if genre_filter != "Любой":
        sql += " AND m.genres LIKE ?"
        params.append(f"%{genre_filter}%")
    if specific_movie != "Все фильмы":
        sql += " AND m.title_ru = ?"
        params.append(specific_movie)

    sql += """
        )
        SELECT title_ru, year, genres, rating, type, season, episode,
               start_time, end_time, text, imdb_id, countries,
               title_original, id
        FROM ranked WHERE rn = 1
        ORDER BY rating DESC LIMIT ?
    """
    params.append(limit)

    try:
        cursor.execute(sql, params)
        results = cursor.fetchall()
        conn.close()
        return [(row, 100.0) for row in results]
    except Exception:
        conn.close()
        return []


def _search_semantic(
    query_text, limit, min_rating, t_type,
    country_filter, genre_filter, specific_movie,
):
    if not chroma_collection:
        return []

    query_vector = ai_model.encode(
        [f"query: {query_text}"], normalize_embeddings=True
    ).tolist()

    and_conditions = []
    if min_rating > 0:
        and_conditions.append({"rating": {"$gte": min_rating}})
    if t_type != "Все":
        and_conditions.append(
            {"type": {"$eq": "movie" if t_type == "Фильмы" else "tv"}}
        )
    if country_filter == "Наше (RU/SU)":
        and_conditions.append({"countries": {"$in": ["RU", "SU", "SUHH"]}})
    elif country_filter == "Зарубежное":
        and_conditions.append(
            {"countries": {"$nin": ["RU", "SU", "SUHH", "Unknown"]}}
        )
    if genre_filter != "Любой":
        and_conditions.append({"genres": {"$contains": genre_filter}})

    if len(and_conditions) == 1:
        where_filters = and_conditions[0]
    elif len(and_conditions) > 1:
        where_filters = {"$and": and_conditions}
    else:
        where_filters = None

    fetch_limit = limit * 3 if specific_movie != "Все фильмы" else limit

    try:
        chroma_result = chroma_collection.query(
            query_embeddings=query_vector,
            n_results=fetch_limit,
            include=["distances"],
            where=where_filters,
        )
    except Exception:
        time.sleep(0.5)
        try:
            chroma_result = chroma_collection.query(
                query_embeddings=query_vector,
                n_results=fetch_limit,
                include=["distances"],
                where=where_filters,
            )
        except Exception:
            return []

    found_ids = list(chroma_result["ids"][0])
    distances = chroma_result["distances"][0]
    if not found_ids:
        return []

    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
    except Exception:
        return []

    params = list(found_ids)
    placeholders = ",".join("?" for _ in params)
    sql = f"""
        SELECT m.title_ru, m.year, m.genres, m.rating, m.type,
               m.season, m.episode,
               s.start_time, s.end_time, s.text,
               m.imdb_id, m.countries, m.title_original, s.id
        FROM subtitles s
        JOIN movies m ON s.imdb_id = m.imdb_id
        WHERE s.id IN ({placeholders})
    """
    if specific_movie != "Все фильмы":
        sql += " AND m.title_ru = ?"
        params.append(specific_movie)

    try:
        cursor.execute(sql, params)
        sqlite_rows = cursor.fetchall()
        conn.close()
    except Exception:
        conn.close()
        return []

    id_to_row = {str(row[13]): row for row in sqlite_rows}
    final_results = []
    for f_id, dist in zip(chroma_result["ids"][0], distances):
        if f_id in id_to_row:
            final_results.append(
                (id_to_row[f_id], round((1.0 - dist) * 100, 1))
            )
            if len(final_results) == limit:
                break
    return final_results


# =====================================================================
# 🎨 ИНТЕРФЕЙС
# =====================================================================
st.title("🎬 AI-Режиссер Монтажа (Студия 5.0)")

with st.sidebar:
    st.header("⚙️ Режим Поиска")
    search_mode = st.radio(
        "Как будем искать?",
        ["По словам (Быстро ⚡️)", "По смыслу (Нейросеть 🧠)"],
        key="search_mode",
        on_change=on_settings_change,
    )

    st.markdown("---")
    st.header("🎛 Фильтры")
    all_movies = ["Все фильмы"] + get_movie_titles()
    saved_movie = st.session_state.get("specific_movie", "Все фильмы")
    if saved_movie not in all_movies:
        st.session_state["specific_movie"] = "Все фильмы"
    specific_movie = st.selectbox(
        "📌 Искать в конкретном кино:",
        all_movies,
        key="specific_movie",
        on_change=on_settings_change,
    )
    t_type = st.radio(
        "🎞 Тип медиа:",
        ["Все", "Фильмы", "Сериалы"],
        horizontal=True,
        key="t_type",
        on_change=on_settings_change,
    )
    c_filter = st.radio(
        "🌍 Производство:",
        ["Все", "Наше (RU/SU)", "Зарубежное"],
        key="c_filter",
        on_change=on_settings_change,
    )
    genre_filter = st.selectbox(
        "🎭 Жанр:",
        [
            "Любой", "Comedy", "Drama", "Action",
            "Sci-Fi", "Horror", "Romance", "Crime",
        ],
        key="genre_filter",
        on_change=on_settings_change,
    )
    min_rating = st.slider(
        "⭐️ Минимальный рейтинг IMDb:",
        0.0, 10.0, step=0.1,
        key="min_rating",
        on_change=on_settings_change,
    )

    st.markdown("---")
    st.header("✂️ Хронометраж")
    pad_start = st.number_input(
        "Секунд ДО фразы:",
        min_value=0.0, max_value=120.0, step=5.0,
        key="pad_start",
        on_change=on_settings_change,
    )
    pad_end = st.number_input(
        "Секунд ПОСЛЕ фразы:",
        min_value=0.0, max_value=120.0, step=5.0,
        key="pad_end",
        on_change=on_settings_change,
    )
    source_pref = st.radio(
        "🌐 Источник скачивания:",
        ["all", "torrent", "youtube"],
        format_func=lambda x: {
            "all": "Везде",
            "torrent": "Только Torrent",
            "youtube": "Только YouTube",
        }[x],
        key="source_pref",
        on_change=on_settings_change,
    )

search_placeholder = (
    "Введите точную цитату"
    if search_mode == "По словам (Быстро ⚡️)"
    else "Опишите сцену или эмоцию"
)

if "search_active" not in st.session_state:
    st.session_state.search_active = False

with st.form("search_form"):
    query = st.text_input("🔍 Поиск:", placeholder=search_placeholder)
    submit_search = st.form_submit_button("Найти мемы 🚀")

if submit_search:
    st.session_state.search_active = True

if st.session_state.search_active and query:
    with st.spinner("Ищу..."):
        results = perform_search(
            query, search_mode, 10, min_rating,
            t_type, c_filter, genre_filter, specific_movie,
        )

    if not results:
        st.warning("По вашим фильтрам ничего не найдено.")
    else:
        st.success(f"Найдено результатов: {len(results)}")

        for i, (row, similarity) in enumerate(results):
            (
                title, year, genres, rating, m_type, season, ep,
                start_srt, end_srt, text, imdb_id, countries,
                orig_title, s_id,
            ) = row[:14]

            season_int = safe_int(season)
            ep_int = safe_int(ep)
            year_int = safe_int(year)
            rating_val = rating if rating else 0.0
            c_disp = countries if countries else "Unknown"
            genres_disp = genres if genres else "N/A"
            text_disp = text if text else ""
            orig_title_str = str(orig_title) if orig_title else ""

            if m_type == "tv":
                display_title = (
                    f"📺 {title} (S{season_int:02d}E{ep_int:02d})"
                )
            else:
                display_title = f"🎬 {title} ({year_int})"

            if search_mode == "По словам (Быстро ⚡️)":
                text_html = re.sub(
                    f"(?i)({re.escape(query)}[а-яА-Яa-zA-Z]*)",
                    r'<mark style="background-color: yellow; '
                    r'color: black;"><b>\1</b></mark>',
                    text_disp,
                )
            else:
                text_html = text_disp

            sim_badge = (
                f" | 🧠 Смысл: {similarity}%"
                if search_mode == "По смыслу (Нейросеть 🧠)"
                else ""
            )

            with st.expander(
                f"{display_title} | ★ {rating_val} | "
                f"🌍 {c_disp}{sim_badge}",
                expanded=(i == 0),
            ):
                col1, col2 = st.columns([2, 1])
                saved_source, saved_offset = get_saved_source_info(imdb_id)
                auto_offset = 0.0
                manual_offset = float(saved_offset)

                with col1:
                    st.markdown(f"**Жанры:** {genres_disp}")
                    st.markdown(
                        f"**Таймкод:** `{start_srt}` ➡️ `{end_srt}`"
                    )
                    st.markdown(
                        f"**Фраза:** {text_html}", unsafe_allow_html=True
                    )

                    if saved_source:
                        st.info(
                            f"📌 **Источник закреплен!** "
                            f"({str(saved_source)[:30]}...). "
                            f"Смещение: **{saved_offset} сек.**"
                        )
                        if st.button(
                            "🗑 Отвязать торрент (искать заново)",
                            key=f"reset_{imdb_id}_{i}",
                        ):
                            delete_source_info(imdb_id)
                            st.rerun()

                    with st.expander(
                        "🛠 Рассинхрон? Режим авто-коррекции"
                    ):
                        target_sec = srt_to_seconds(start_srt)

                        st.markdown(
                            "**🔍 Шаг 1: Глубокий поиск по фильму**"
                        )
                        deep_query = st.text_input(
                            "Введите любую фразу, которую "
                            "услышали в скачанном видео:",
                            key=f"dq_{imdb_id}_{i}",
                        )

                        if deep_query:
                            # Ищем ТОЛЬКО в текущем файле субтитров
                            deep_results = search_phrase_in_movie(
                                imdb_id, deep_query, s_id
                            )
                            if deep_results:
                                deep_options = {
                                    f"[{r[0][:8]}] {r[1]}": srt_to_seconds(
                                        r[0]
                                    )
                                    for r in deep_results
                                }
                                heard_label = st.selectbox(
                                    "Выберите точную фразу:",
                                    list(deep_options.keys()),
                                    key=f"ds_{imdb_id}_{i}",
                                )
                                auto_offset = (
                                    target_sec - deep_options[heard_label]
                                )
                                sign = "+" if auto_offset > 0 else ""
                                st.success(
                                    f"🤖 Глубокий поиск вычислил сдвиг: "
                                    f"**{sign}{auto_offset:.1f} сек.**"
                                )
                            else:
                                st.warning(
                                    "Фраза не найдена в этом "
                                    "файле субтитров."
                                )
                                auto_offset = 0.0
                        else:
                            # Контекст — тоже только текущий файл
                            context_items = get_surrounding_context(
                                imdb_id, target_sec, s_id
                            )
                            if context_items:
                                st.markdown(
                                    "**ИЛИ выберите из ближайшего "
                                    "контекста:**"
                                )
                                for item in context_items:
                                    prefix = (
                                        "🎯 "
                                        if item["is_target"]
                                        else "⚪ "
                                    )
                                    st.markdown(
                                        f"{prefix} "
                                        f"`[{item['time_str']}]` "
                                        f"{item['text']}"
                                    )

                                options = {
                                    item["label"]: item["sec"]
                                    for item in context_items
                                }
                                try:
                                    default_label = next(
                                        item["label"]
                                        for item in context_items
                                        if item["is_target"]
                                    )
                                except StopIteration:
                                    default_label = list(
                                        options.keys()
                                    )[0]

                                heard_label = st.selectbox(
                                    "Что РЕАЛЬНО прозвучало в видео?",
                                    list(options.keys()),
                                    index=list(options.keys()).index(
                                        default_label
                                    ),
                                    key=f"sync_{imdb_id}_{i}",
                                )
                                auto_offset = (
                                    target_sec - options[heard_label]
                                )
                                if auto_offset != 0:
                                    sign = (
                                        "+" if auto_offset > 0 else ""
                                    )
                                    st.success(
                                        f"🤖 ИИ вычислил сдвиг: "
                                        f"**{sign}{auto_offset:.1f} "
                                        f"сек.**"
                                    )
                            else:
                                auto_offset = 0.0

                        st.markdown("---")
                        manual_offset = st.number_input(
                            "Шаг 2: Ручная подгонка (+/- сек):",
                            min_value=-600.0,
                            max_value=600.0,
                            value=float(saved_offset),
                            step=0.5,
                            key=f"man_{imdb_id}_{i}",
                        )

                with col2:
                    final_offset = auto_offset + manual_offset

                    if st.button(
                        "⬇️ Скачать клип",
                        key=f"dl_{imdb_id}_{start_srt}_{i}",
                        use_container_width=True,
                    ):
                        start_sec = max(
                            0,
                            srt_to_seconds(start_srt)
                            - pad_start
                            + final_offset,
                        )
                        end_sec = (
                            srt_to_seconds(end_srt)
                            + pad_end
                            + final_offset
                        )
                        duration = max(1, end_sec - start_sec)
                        start_hms = seconds_to_hms(start_sec)

                        expected_file = get_expected_filename(
                            title, year, m_type, season, ep
                        )
                        if os.path.exists(expected_file):
                            os.remove(expected_file)

                        st.markdown("**Консоль скачивания:**")
                        log_container = st.empty()
                        progress_bar = st.progress(
                            0, text="Запуск..."
                        )

                        cmd = [
                            sys.executable,
                            "-u",
                            "magnet_get.py",
                            "--title", str(title),
                            "--orig_title", orig_title_str,
                            "--year", str(year_int),
                            "--type", str(m_type),
                            "--season", str(season_int),
                            "--episode", str(ep_int),
                            "--start", start_hms,
                            "--duration", str(int(duration)),
                            "--source", source_pref,
                        ]
                        if saved_source:
                            cmd.extend(
                                ["--force_source", saved_source]
                            )

                        try:
                            process = subprocess.Popen(
                                cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True,
                                bufsize=1,
                            )
                        except FileNotFoundError:
                            st.error(
                                "❌ Не удалось запустить magnet_get.py"
                            )
                            continue

                        full_log = ""
                        used_source = saved_source
                        dl_start_time = time.time()
                        timed_out = False

                        try:
                            for line in process.stdout:
                                elapsed = time.time() - dl_start_time
                                if elapsed > DOWNLOAD_TIMEOUT:
                                    process.kill()
                                    timed_out = True
                                    break

                                clean_line = (
                                    line.replace("\r", "").strip()
                                )
                                if not clean_line:
                                    continue

                                if clean_line.startswith(
                                    "###SOURCE_FOUND###:"
                                ):
                                    used_source = clean_line.split(
                                        "###SOURCE_FOUND###:"
                                    )[1]
                                    continue

                                full_log += clean_line + "\n"
                                last_lines = "\n".join(
                                    full_log.strip().split("\n")[-12:]
                                )
                                log_container.code(
                                    last_lines, language="log"
                                )

                                cl = clean_line.lower()
                                if "поиск" in cl or "search" in cl:
                                    progress_bar.progress(
                                        10,
                                        text="🔍 Поиск источника...",
                                    )
                                elif (
                                    "подключение" in cl
                                    or "connect" in cl
                                ):
                                    progress_bar.progress(
                                        30,
                                        text="⏳ Подключение к пирам...",
                                    )
                                elif (
                                    "успех" in cl or "success" in cl
                                ):
                                    progress_bar.progress(
                                        50,
                                        text="📡 Буферизация...",
                                    )
                                elif "режем" in cl or "cut" in cl:
                                    progress_bar.progress(
                                        70,
                                        text="✂️ Нарезка видео...",
                                    )
                                elif "готово" in cl or "done" in cl:
                                    progress_bar.progress(
                                        100, text="✅ Готово!"
                                    )
                                elif (
                                    "скачивание" in cl
                                    or "download" in cl
                                ):
                                    progress_bar.progress(
                                        40,
                                        text="⬇️ Скачивание...",
                                    )
                        except Exception as e:
                            st.error(
                                f"Ошибка чтения процесса: {e}"
                            )
                        finally:
                            try:
                                process.kill()
                            except Exception:
                                pass
                            try:
                                process.wait(timeout=5)
                            except Exception:
                                pass

                        if timed_out:
                            st.error(
                                f"⏰ Превышен таймаут скачивания "
                                f"({DOWNLOAD_TIMEOUT // 60} мин)."
                            )
                        elif (
                            os.path.exists(expected_file)
                            and os.path.getsize(expected_file) > 1024
                        ):
                            progress_bar.progress(
                                100, text="✅ Готово!"
                            )
                            st.success("✅ Клип готов!")
                            if used_source:
                                save_source_info(
                                    imdb_id,
                                    used_source,
                                    final_offset,
                                )
                            st.video(expected_file)
                            with open(expected_file, "rb") as file:
                                st.download_button(
                                    "💾 Сохранить в проект",
                                    data=file,
                                    file_name=os.path.basename(
                                        expected_file
                                    ),
                                    mime="video/mp4",
                                    key=f"save_{imdb_id}_{i}",
                                )
                        else:
                            progress_bar.progress(
                                0, text="❌ Ошибка"
                            )
                            st.error(
                                "❌ Ошибка скачивания. "
                                "Проверьте логи выше."
                            )