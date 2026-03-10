import streamlit as st
import sqlite3
import pandas as pd
import re
import datetime
import subprocess
import sys
import os
import time
import chromadb
from sentence_transformers import SentenceTransformer
import torch

# --- НАСТРОЙКИ ---
DB_NAME = 'movies_master.sqlite'
CHROMA_DIR = './chroma_db'
CLIPS_DIR = 'clips'

st.set_page_config(page_title="AI-Режиссер Монтажа", page_icon="🎬", layout="wide")

# =====================================================================
# 🧠 ИИ И БД (КЭШИРОВАНИЕ ДЛЯ СКОРОСТИ)
# =====================================================================
@st.cache_resource(show_spinner=False)
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
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И РАБОТА С БД
# =====================================================================
def srt_to_seconds(srt_time_str):
    srt_time_str = srt_time_str.replace('.', ',')
    time_part, ms_part = srt_time_str.split(',')
    h, m, s = map(int, time_part.split(':'))
    return h * 3600 + m * 60 + s + int(ms_part) / 1000.0

def seconds_to_hms(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

@st.cache_data
def get_movie_titles():
    if not os.path.exists(DB_NAME): return []
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT DISTINCT title_ru FROM movies ORDER BY title_ru ASC", conn)
    conn.close()
    return df['title_ru'].tolist()

def get_expected_filename(title, year, m_type, season, episode):
    if m_type == 'tv' and int(season) > 0:
        safe_name = f"{title}_S{int(season):02d}E{int(episode):02d}"
    else:
        safe_name = f"{title}_{year}" if int(year) > 0 else title
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in " _-").strip().replace(" ", "_")
    return os.path.join(CLIPS_DIR, f"{safe_name}_clip.mp4")

# --- ФУНКЦИИ ПРИВЯЗКИ ИСТОЧНИКОВ И СМЕЩЕНИЙ ---
def get_saved_source_info(imdb_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='movie_sources'")
    if not cursor.fetchone():
        conn.close()
        return None, 0.0
    cursor.execute("SELECT source_id, offset_sec FROM movie_sources WHERE imdb_id = ?", (imdb_id,))
    row = cursor.fetchone()
    conn.close()
    if row: return row[0], row[1]
    return None, 0.0

def save_source_info(imdb_id, source_id, offset_sec):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS movie_sources (imdb_id TEXT PRIMARY KEY, source_id TEXT, offset_sec REAL)")
    cursor.execute("INSERT OR REPLACE INTO movie_sources (imdb_id, source_id, offset_sec) VALUES (?, ?, ?)", (imdb_id, source_id, offset_sec))
    conn.commit()
    conn.close()

def delete_source_info(imdb_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM movie_sources WHERE imdb_id = ?", (imdb_id,))
    conn.commit()
    conn.close()

# --- ФУНКЦИИ ЧТЕНИЯ КОНТЕКСТА ---
def get_surrounding_context(imdb_id, target_start_sec, target_sub_id, window_sec=90):
    """Ищет контекст только в пределах одного перевода (по ближним ID)"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    sql = '''
        SELECT start_time, text 
        FROM subtitles 
        WHERE imdb_id = ? AND id BETWEEN ? AND ?
        ORDER BY start_time ASC
    '''
    # Берем фразы только из этого же файла (+/- 500 строк от искомой)
    cursor.execute(sql, (imdb_id, target_sub_id - 500, target_sub_id + 500))
    rows = cursor.fetchall()
    conn.close()
    
    context_items = []
    seen_texts = set()
    
    for r_start, r_text in rows:
        r_sec = srt_to_seconds(r_start)
        if abs(r_sec - target_start_sec) <= window_sec:
            if r_text not in seen_texts:
                seen_texts.add(r_text)
                is_target = abs(r_sec - target_start_sec) < 5
                label = f"[{r_start[:8]}] {r_text[:70]}..."
                context_items.append({"sec": r_sec, "label": label, "text": r_text, "time_str": r_start[:8], "is_target": is_target})
    return context_items

def search_phrase_in_movie(imdb_id, phrase):
    """Глубокий поиск любой фразы в фильме"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    sql = "SELECT start_time, text FROM subtitles WHERE imdb_id = ? AND text LIKE ? ORDER BY start_time LIMIT 20"
    cursor.execute(sql, (imdb_id, f"%{phrase}%"))
    rows = cursor.fetchall()
    conn.close()
    return rows

# =====================================================================
# 🔍 ДВИЖКИ ПОИСКА
# =====================================================================
@st.cache_data(show_spinner=False)
def perform_search(query_text, search_mode, limit, min_rating, t_type, country_filter, genre_filter, specific_movie):
    if search_mode == "По словам (Быстро ⚡️)":
        conn = sqlite3.connect(DB_NAME)
        conn.execute("PRAGMA journal_mode=WAL;") 
        cursor = conn.cursor()
        search_query = f"{query_text}*"
        params = [search_query]
        
        # ДОБАВЛЕНО s.id в SELECT
        sql = '''
            WITH top_matches AS (
                SELECT rowid FROM subtitles_fts WHERE text MATCH ? ORDER BY rank LIMIT 500
            )
            SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
                   MIN(s.start_time), MAX(s.end_time), s.text, m.imdb_id, m.countries, m.title_original, s.id
            FROM top_matches tm
            JOIN subtitles s ON s.id = tm.rowid
            JOIN movies m ON s.imdb_id = m.imdb_id
            WHERE 1=1
        '''
                 
        if min_rating > 0: sql += ' AND m.rating >= ?'; params.append(min_rating)
        if t_type != 'Все': sql += ' AND m.type = ?'; params.append('movie' if t_type == 'Фильмы' else 'tv')
        if country_filter == 'Наше (RU/SU)': sql += " AND (m.countries LIKE '%RU%' OR m.countries LIKE '%SU%')"
        elif country_filter == 'Зарубежное': sql += " AND (m.countries NOT LIKE '%RU%' AND m.countries NOT LIKE '%SU%' OR m.countries IS NULL)"
        if genre_filter != 'Любой': sql += " AND m.genres LIKE ?"; params.append(f"%{genre_filter}%")
        if specific_movie != 'Все фильмы': sql += " AND m.title_ru = ?"; params.append(specific_movie)

        sql += ' GROUP BY m.imdb_id, SUBSTR(s.start_time, 1, 5) ORDER BY m.rating DESC LIMIT ?'
        params.append(limit)
        cursor.execute(sql, params)
        results = cursor.fetchall()
        conn.close()
        return [(row, 100.0) for row in results]
    else:
        if not chroma_collection: return []
        query_vector = ai_model.encode([f"query: {query_text}"], normalize_embeddings=True).tolist()
        
        and_conditions = []
        if min_rating > 0: and_conditions.append({"rating": {"$gte": min_rating}})
        if t_type != 'Все': and_conditions.append({"type": {"$eq": 'movie' if t_type == 'Фильмы' else 'tv'}})
        if country_filter == 'Наше (RU/SU)': and_conditions.append({"countries": {"$in": ["RU", "SU", "SUHH"]}})
        elif country_filter == 'Зарубежное': and_conditions.append({"countries": {"$nin": ["RU", "SU", "SUHH", "Unknown"]}})
        if genre_filter != 'Любой': and_conditions.append({"genres": {"$contains": genre_filter}})
            
        where_filters = and_conditions[0] if len(and_conditions) == 1 else {"$and": and_conditions} if len(and_conditions) > 1 else None
        fetch_limit = limit * 3 if specific_movie != 'Все фильмы' else limit
        
        try: chroma_result = chroma_collection.query(query_embeddings=query_vector, n_results=fetch_limit, include=["distances"], where=where_filters)
        except:
            time.sleep(0.5)
            try: chroma_result = chroma_collection.query(query_embeddings=query_vector, n_results=fetch_limit, include=["distances"], where=where_filters)
            except: return []

        found_ids, distances = chroma_result['ids'][0], chroma_result['distances'][0]
        if not found_ids: return []

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in found_ids)
        
        sql = f'''SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
                  s.start_time, s.end_time, s.text, m.imdb_id, m.countries, m.title_original, s.id
                  FROM subtitles s JOIN movies m ON s.imdb_id = m.imdb_id WHERE s.id IN ({placeholders})'''
        
        if specific_movie != 'Все фильмы': sql += " AND m.title_ru = ?"; found_ids.append(specific_movie) 
        cursor.execute(sql, found_ids)
        sqlite_rows = cursor.fetchall()
        conn.close()
        
        id_to_row = {str(row[13]): row for row in sqlite_rows} 
        final_results = []
        for f_id, dist in zip(chroma_result['ids'][0], distances):
            if f_id in id_to_row:
                final_results.append((id_to_row[f_id], round((1.0 - dist) * 100, 1)))
                if len(final_results) == limit: break
        return final_results

# =====================================================================
# 🎨 ИНТЕРФЕЙС (FRONTEND)
# =====================================================================

st.title("🎬 AI-Режиссер Монтажа (Студия 5.0)")

with st.sidebar:
    st.header("⚙️ Режим Поиска")
    search_mode = st.radio("Как будем искать?", ["По словам (Быстро ⚡️)", "По смыслу (Нейросеть 🧠)"])
    
    st.markdown("---")
    st.header("🎛 Фильтры")
    all_movies = ["Все фильмы"] + get_movie_titles()
    specific_movie = st.selectbox("📌 Искать в конкретном кино:", all_movies)
    t_type = st.radio("🎞 Тип медиа:", ["Все", "Фильмы", "Сериалы"], horizontal=True)
    c_filter = st.radio("🌍 Производство:", ["Все", "Наше (RU/SU)", "Зарубежное"])
    genre_filter = st.selectbox("🎭 Жанр:", ["Любой", "Comedy", "Drama", "Action", "Sci-Fi", "Horror", "Romance", "Crime"])
    min_rating = st.slider("⭐️ Минимальный рейтинг IMDb:", 0.0, 10.0, 0.0, 0.1)
    
    st.markdown("---")
    st.header("✂️ Хронометраж")
    pad_start = st.number_input("Секунд ДО фразы:", min_value=0.0, max_value=30.0, value=2.0, step=0.5)
    pad_end = st.number_input("Секунд ПОСЛЕ фразы:", min_value=0.0, max_value=30.0, value=2.0, step=0.5)
    source_pref = st.radio("🌐 Источник скачивания:", ["all", "torrent", "youtube"], format_func=lambda x: "Везде" if x=="all" else "Только Torrent" if x=="torrent" else "Только YouTube")

search_placeholder = "Введите точную цитату" if search_mode == "По словам (Быстро ⚡️)" else "Опишите сцену или эмоцию"

if "search_active" not in st.session_state:
    st.session_state.search_active = False

with st.form("search_form"):
    query = st.text_input("🔍 Поиск:", placeholder=search_placeholder)
    submit_search = st.form_submit_button("Найти мемы 🚀")

if submit_search:
    st.session_state.search_active = True

if st.session_state.search_active and query:
    with st.spinner("Ищу..."):
        results = perform_search(query, search_mode, 10, min_rating, t_type, c_filter, genre_filter, specific_movie)
    
    if not results:
        st.warning("По вашим фильтрам ничего не найдено.")
    else:
        st.success(f"Найдено результатов: {len(results)}")
        
        for i, (row, similarity) in enumerate(results):
            # РАСПАКОВКА 14 КОЛОНОК (ДОБАВЛЕН s_id)
            title, year, genres, rating, m_type, season, ep, start_srt, end_srt, text, imdb_id, countries, orig_title, s_id = row[:14]
            c_disp = countries if countries else "Unknown"
            display_title = f"📺 {title} (S{season:02d}E{ep:02d})" if m_type == 'tv' else f"🎬 {title} ({year})"
            
            text_html = re.sub(f'(?i)({query}[а-яА-Яa-zA-Z]*)', r'<mark style="background-color: yellow; color: black;"><b>\1</b></mark>', text) if search_mode == "По словам (Быстро ⚡️)" else text
            sim_badge = f" | 🧠 Смысл: {similarity}%" if search_mode == "По смыслу (Нейросеть 🧠)" else ""
            
            with st.expander(f"{display_title} | ★ {rating} | 🌍 {c_disp}{sim_badge}", expanded=True):
                col1, col2 = st.columns([2, 1])
                
                saved_source, saved_offset = get_saved_source_info(imdb_id)
                auto_offset, manual_offset = 0.0, float(saved_offset)
                
                with col1:
                    st.markdown(f"**Жанры:** {genres}")
                    st.markdown(f"**Таймкод:** `{start_srt}` ➡️ `{end_srt}`")
                    st.markdown(f"**Фраза:** {text_html}", unsafe_allow_html=True)
                    
                    if saved_source:
                        st.info(f"📌 **Источник закреплен!** ({saved_source[:20]}...). Смещение: **{saved_offset} сек.**")
                        if st.button("🗑 Отвязать торрент (искать заново)", key=f"reset_{imdb_id}_{i}"):
                            delete_source_info(imdb_id)
                            st.rerun()
                    
                    with st.expander("🛠 Рассинхрон? Режим авто-коррекции"):
                        target_sec = srt_to_seconds(start_srt)
                        
                        # --- ГЛУБОКИЙ ПОИСК ---
                        st.markdown("**🔍 Шаг 1: Глубокий поиск по фильму**")
                        deep_query = st.text_input("Введите любую фразу, которую услышали в скачанном видео:", key=f"dq_{imdb_id}_{i}")
                        
                        if deep_query:
                            deep_results = search_phrase_in_movie(imdb_id, deep_query)
                            if deep_results:
                                deep_options = {f"[{r[0][:8]}] {r[1]}": srt_to_seconds(r[0]) for r in deep_results}
                                heard_label = st.selectbox("Выберите точную фразу:", list(deep_options.keys()), key=f"ds_{imdb_id}_{i}")
                                auto_offset = target_sec - deep_options[heard_label]
                                st.success(f"🤖 Глубокий поиск вычислил сдвиг: **{'+' if auto_offset > 0 else ''}{auto_offset:.1f} сек.**")
                            else:
                                st.warning("Фраза не найдена в этом фильме.")
                                auto_offset = 0.0
                        else:
                            # --- СТАНДАРТНЫЙ КОНТЕКСТ (С ЗАЩИТОЙ ОТ ДУБЛЕЙ ПЕРЕВОДА) ---
                            context_items = get_surrounding_context(imdb_id, target_sec, s_id)
                            if context_items:
                                st.markdown("**ИЛИ выберите из ближайшего контекста:**")
                                for item in context_items:
                                    prefix = "🎯 " if item['is_target'] else "⚪ "
                                    st.markdown(f"{prefix} `[{item['time_str']}]` {item['text']}")
                                    
                                options = {item['label']: item['sec'] for item in context_items}
                                try: default_label = next(item['label'] for item in context_items if item['is_target'])
                                except: default_label = list(options.keys())[0]
                                    
                                heard_label = st.selectbox("Что РЕАЛЬНО прозвучало в видео?", list(options.keys()), index=list(options.keys()).index(default_label), key=f"sync_{imdb_id}_{i}")
                                auto_offset = target_sec - options[heard_label]
                                if auto_offset != 0:
                                    st.success(f"🤖 ИИ вычислил сдвиг: **{'+' if auto_offset > 0 else ''}{auto_offset:.1f} сек.**")
                            else:
                                auto_offset = 0.0
                                
                        st.markdown("---")
                        manual_offset = st.number_input("Шаг 2: Ручная подгонка (+/- сек):", min_value=-600.0, max_value=600.0, value=float(saved_offset), step=0.5, key=f"man_{imdb_id}_{i}")
                    
                with col2:
                    final_offset = auto_offset + manual_offset
                    
                    if st.button("⬇️ Скачать клип", key=f"dl_{imdb_id}_{start_srt}_{i}", use_container_width=True):
                        start_sec = max(0, srt_to_seconds(start_srt) - pad_start + final_offset)
                        end_sec = srt_to_seconds(end_srt) + pad_end + final_offset
                        duration = end_sec - start_sec
                        start_hms = seconds_to_hms(start_sec)
                        
                        expected_file = get_expected_filename(title, year, m_type, season, ep)
                        if os.path.exists(expected_file): os.remove(expected_file)
                        
                        st.markdown("**Консоль скачивания:**")
                        log_container = st.empty()
                        
                        cmd = [
                            sys.executable, "-u", "magnet_get.py",
                            "--title", title, "--orig_title", str(orig_title), "--year", str(year),
                            "--type", str(m_type), "--season", str(season), "--episode", str(ep),
                            "--start", start_hms, "--duration", str(int(duration)), "--source", source_pref
                        ]
                        
                        if saved_source:
                            cmd.extend(["--force_source", saved_source])
                        
                        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
                        full_log = ""
                        used_source = saved_source 
                        
                        for line in process.stdout:
                            clean_line = line.replace('\r', '').strip()
                            if clean_line:
                                if clean_line.startswith("###SOURCE_FOUND###:"):
                                    used_source = clean_line.split("###SOURCE_FOUND###:")[1]
                                    continue
                                    
                                full_log += clean_line + "\n"
                                log_container.code("\n".join(full_log.strip().split("\n")[-10:]), language="log")
                        process.wait()
                        
                        if process.returncode == 0 and os.path.exists(expected_file):
                            st.success("✅ Готово!")
                            if used_source:
                                save_source_info(imdb_id, used_source, final_offset)
                                
                            st.video(expected_file)
                            with open(expected_file, "rb") as file:
                                st.download_button("💾 Сохранить в проект", data=file, file_name=os.path.basename(expected_file), mime="video/mp4", key=f"save_{imdb_id}_{i}")
                        else:
                            st.error("❌ Ошибка скачивания. Проверьте логи в консоли.")