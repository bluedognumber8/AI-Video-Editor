import streamlit as st
import sqlite3
import pandas as pd
import re
import datetime
import subprocess
import sys
import os
import chromadb
from sentence_transformers import SentenceTransformer
import torch

# --- НАСТРОЙКИ ---
DB_NAME = 'movies_master.sqlite'
CHROMA_DIR = './chroma_db'
CLIPS_DIR = 'clips'

st.set_page_config(page_title="AI-Режиссер Монтажа", page_icon="🎬", layout="wide")

# =====================================================================
# 🧠 ИИ-КЭШИРОВАНИЕ (Грузится 1 раз при старте сервера)
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

# Инициализация ИИ (тихая)
ai_model = load_ai_model()
chroma_client = load_chroma_client()
if chroma_client:
    chroma_collection = chroma_client.get_collection(name="subtitles_semantic")
else:
    chroma_collection = None

# =====================================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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

# =====================================================================
# 🔍 ДВИЖКИ ПОИСКА (SQL И ИИ)
# =====================================================================

def search_sqlite(keyword, limit, min_rating, t_type, country_filter, genre_filter, specific_movie):
    """Точный поиск по словам (FTS5)"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    search_query = f"{keyword}*"
    params = [search_query]
    
    sql = '''
        SELECT 
            m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
            MIN(s.start_time), MAX(s.end_time), s.text, m.imdb_id, m.countries, m.title_original
        FROM subtitles s
        JOIN movies m ON s.imdb_id = m.imdb_id
        WHERE s.id IN (SELECT rowid FROM subtitles_fts WHERE text MATCH ?)
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
    
    # Добавляем 100% совпадение (так как это SQL), чтобы формат ответа был одинаковым
    return [(row, 100.0) for row in results]

def search_ai(query_text, limit, min_rating, t_type, country_filter, genre_filter, specific_movie):
    """Семантический поиск по смыслу (ChromaDB + SQLite) с защитой от блокировок"""
    if not chroma_collection: return []
    
    e5_query = f"query: {query_text}"
    query_vector = ai_model.encode([e5_query], normalize_embeddings=True).tolist()
    
    and_conditions = []
    if min_rating > 0: and_conditions.append({"rating": {"$gte": min_rating}})
    if t_type != 'Все': and_conditions.append({"type": {"$eq": 'movie' if t_type == 'Фильмы' else 'tv'}})
    if country_filter == 'Наше (RU/SU)': and_conditions.append({"countries": {"$in": ["RU", "SU", "SUHH"]}})
    elif country_filter == 'Зарубежное': and_conditions.append({"countries": {"$nin": ["RU", "SU", "SUHH", "Unknown"]}})
    if genre_filter != 'Любой': and_conditions.append({"genres": {"$contains": genre_filter}})
        
    where_filters = and_conditions[0] if len(and_conditions) == 1 else {"$and": and_conditions} if len(and_conditions) > 1 else None

    fetch_limit = limit * 3 if specific_movie != 'Все фильмы' else limit
    search_args = {"query_embeddings": query_vector, "n_results": fetch_limit, "include": ["distances"]}
    if where_filters: search_args["where"] = where_filters
        
    # --- ИСПРАВЛЕНИЕ: Защита от столкновения с фоновой записью ---
    try:
        chroma_result = chroma_collection.query(**search_args)
    except Exception as e:
        import time
        time.sleep(0.5) # Ждем полсекунды, пока фоновый скрипт допишет базу
        try:
            chroma_result = chroma_collection.query(**search_args)
        except Exception:
            st.warning("⚠️ Векторная база сейчас обновляется. Пожалуйста, подождите пару секунд и попробуйте снова.")
            return []
    # --------------------------------------------------------------

    found_ids = chroma_result['ids'][0]
    distances = chroma_result['distances'][0]
    
    if not found_ids: return []

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    placeholders = ','.join('?' for _ in found_ids)
    sql = f'''
        SELECT 
            m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
            s.start_time, s.end_time, s.text, m.imdb_id, m.countries, m.title_original, s.id
        FROM subtitles s
        JOIN movies m ON s.imdb_id = m.imdb_id
        WHERE s.id IN ({placeholders})
    '''
    
    if specific_movie != 'Все фильмы':
        sql += " AND m.title_ru = ?"
        found_ids.append(specific_movie) 
        
    cursor.execute(sql, found_ids)
    sqlite_rows = cursor.fetchall()
    conn.close()
    
    id_to_row = {str(row[13]): row for row in sqlite_rows} 
    
    final_results = []
    for f_id, dist in zip(chroma_result['ids'][0], distances):
        if f_id in id_to_row:
            similarity = round((1.0 - dist) * 100, 1)
            final_results.append((id_to_row[f_id], similarity))
            if len(final_results) == limit: break
            
    return final_results
# =====================================================================
# 🎨 ИНТЕРФЕЙС (FRONTEND)
# =====================================================================

st.title("🎬 AI-Режиссер Монтажа (Студия)")

# --- БОКОВАЯ ПАНЕЛЬ (ФИЛЬТРЫ) ---
with st.sidebar:
    st.header("⚙️ Режим Поиска")
    search_mode = st.radio("Как будем искать?", ["По словам (Быстро ⚡️)", "По смыслу (Нейросеть 🧠)"])
    
    if search_mode == "По смыслу (Нейросеть 🧠)" and not chroma_collection:
        st.error("Векторная база не найдена. Запустите скрипт `04_build_vector_db.py`!")
        
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
    pad_start = st.number_input("Секунд ДО фразы:", min_value=0.0, max_value=15.0, value=2.0, step=0.5)
    pad_end = st.number_input("Секунд ПОСЛЕ фразы:", min_value=0.0, max_value=15.0, value=2.0, step=0.5)
    
    st.markdown("---")
    source_pref = st.radio("🌐 Источник скачивания:", ["all", "torrent", "youtube"], format_func=lambda x: "Везде" if x=="all" else "Только Torrent" if x=="torrent" else "Только YouTube")

# --- ОСНОВНАЯ ЗОНА ---
search_placeholder = "Введите точную цитату (например: в чем сила брат)" if search_mode == "По словам (Быстро ⚡️)" else "Опишите сцену или эмоцию (например: неловкое молчание, злой сарказм)"
query = st.text_input("🔍 Поиск:", placeholder=search_placeholder)

if query:
    with st.spinner("Ищу..."):
        if search_mode == "По словам (Быстро ⚡️)":
            results = search_sqlite(query, 10, min_rating, t_type, c_filter, genre_filter, specific_movie)
        else:
            results = search_ai(query, 10, min_rating, t_type, c_filter, genre_filter, specific_movie)
    
    if not results:
        st.warning("По вашим фильтрам ничего не найдено.")
    else:
        st.success(f"Найдено результатов: {len(results)}")
        
        for i, (row, similarity) in enumerate(results):
            # Распаковка (одинакова для обоих движков)
            title, year, genres, rating, m_type, season, ep, start_srt, end_srt, text, imdb_id, countries, orig_title = row[:13]
            
            c_disp = countries if countries else "Unknown"
            display_title = f"📺 {title} (S{season:02d}E{ep:02d})" if m_type == 'tv' else f"🎬 {title} ({year})"
            
            # Подсветка слов
            if search_mode == "По словам (Быстро ⚡️)":
                text_html = re.sub(f'(?i)({query}[а-яА-Яa-zA-Z]*)', r'<mark style="background-color: yellow; color: black;"><b>\1</b></mark>', text)
            else:
                # В нейро-поиске слова могут не совпадать, поэтому просто выводим текст
                text_html = text
                
            sim_badge = f" | 🧠 Смысл: {similarity}%" if search_mode == "По смыслу (Нейросеть 🧠)" else ""
            
            with st.expander(f"{display_title} | ★ {rating} | 🌍 {c_disp}{sim_badge}", expanded=True):
                col1, col2 = st.columns([3, 1])
                
                with col1:
                    st.markdown(f"**Жанры:** {genres}")
                    st.markdown(f"**Таймкод:** `{start_srt}` ➡️ `{end_srt}`")
                    st.markdown(f"**Фраза:** {text_html}", unsafe_allow_html=True)
                    
                with col2:
                    btn_key = f"dl_{imdb_id}_{start_srt}_{i}"
                    if st.button("⬇️ Скачать клип", key=btn_key, use_container_width=True):
                        
                        start_sec = max(0, srt_to_seconds(start_srt) - pad_start)
                        end_sec = srt_to_seconds(end_srt) + pad_end
                        duration = end_sec - start_sec
                        start_hms = seconds_to_hms(start_sec)
                        
                        expected_file = get_expected_filename(title, year, m_type, season, ep)
                        if os.path.exists(expected_file): os.remove(expected_file)
                        
                        # --- НОВЫЙ БЛОК: ИНТЕРАКТИВНАЯ КОНСОЛЬ ---
                        st.markdown("**Процесс скачивания:**")
                        log_container = st.empty() # Пустой контейнер для логов
                        
                        cmd = [
                            sys.executable, "magnet_get.py",
                            "--title", title, "--orig_title", str(orig_title), "--year", str(year),
                            "--type", str(m_type), "--season", str(season), "--episode", str(ep),
                            "--start", start_hms, "--duration", str(int(duration)), "--source", source_pref
                        ]
                        
                        # Запускаем как Popen, чтобы читать вывод построчно!
                        process = subprocess.Popen(
                            cmd, 
                            stdout=subprocess.PIPE, 
                            stderr=subprocess.STDOUT, 
                            text=True,
                            bufsize=1, # Line-buffered (читаем по 1 строке)
                            universal_newlines=True
                        )
                        
                        full_log = ""
                        # Читаем лог в реальном времени, пока процесс работает
                        for line in process.stdout:
                            # Очищаем лог от консольных спецсимволов \r (перенос каретки от tqdm)
                            clean_line = line.replace('\r', '').strip()
                            if clean_line:
                                full_log += clean_line + "\n"
                                # Выводим в красивом черном "терминале"
                                log_container.code(full_log, language="log")
                        
                        process.wait() # Ждем завершения скрипта
                        # ----------------------------------------
                        
                        if process.returncode == 0 and os.path.exists(expected_file):
                            st.success("✅ Готово!")
                            st.video(expected_file)
                            with open(expected_file, "rb") as file:
                                st.download_button(
                                    "💾 Сохранить на диск", 
                                    data=file, 
                                    file_name=os.path.basename(expected_file), 
                                    mime="video/mp4", 
                                    key=f"save_{btn_key}"
                                )
                        else:
                            st.error("❌ Ошибка скачивания. Посмотрите логи выше, чтобы понять причину.")