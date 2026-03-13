#app.py
import os
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
import gc
import urllib.parse

# --- ИНИЦИАЛИЗАЦИЯ МОРФОЛОГИИ (NLTK + PYMORPHY3) ---
try:
    import nltk
    from nltk.stem.snowball import SnowballStemmer
    try: nltk.data.find('corpora/wordnet')
    except LookupError:
        nltk.download('wordnet', quiet=True)
        nltk.download('omw-1.4', quiet=True)
    try: nltk.data.find('tokenizers/punkt')
    except LookupError:
        nltk.download('punkt', quiet=True)
    ru_stemmer = SnowballStemmer("russian")
except Exception:
    ru_stemmer = None

try:
    import pymorphy3
    morph = pymorphy3.MorphAnalyzer()
except ImportError:
    morph = None
    print("⚠️ ВНИМАНИЕ: Установите pymorphy3 (pip install pymorphy3) для идеального русского поиска!")

# --- НАСТРОЙКИ ---
DB_NAME = 'movies_master.sqlite'
CLIPS_DIR = 'clips'
DOWNLOAD_TIMEOUT = 300
SETTINGS_FILE = "user_settings.json"
RESULTS_PER_PAGE = 10 
SUB_ID_RANGE = 5000 

os.makedirs(CLIPS_DIR, exist_ok=True)

OPENROUTER_API_KEY = "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"
LLM_MODELS_FALLBACK = [
    "meta-llama/llama-3.3-70b-instruct:free",         
    "google/gemini-2.5-flash:free",                   
    "mistralai/mistral-small-24b-instruct-2501:free", 
    "qwen/qwen-2.5-7b-instruct:free",
    "gryphe/mythomax-l2-13b:free"
]

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
    "ai_scenario": "💍 Свадебный (юмор и реакция на 1 слово)"
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except: pass
    return dict(DEFAULT_SETTINGS)

def save_settings(settings_dict):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings_dict, f, ensure_ascii=False, indent=2)
    except: pass

if "settings_loaded" not in st.session_state:
    st.session_state.settings_loaded = True
    for key, val in load_settings().items():
        st.session_state[key] = val

if "search_results" not in st.session_state: st.session_state.search_results = []
if "search_offset" not in st.session_state: st.session_state.search_offset = 0
if "last_query" not in st.session_state: st.session_state.last_query = ""
if "search_query_input" not in st.session_state: st.session_state.search_query_input = ""
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
    if st.session_state.get("search_query_input", "").strip():
        st.session_state.last_query = st.session_state.search_query_input
        st.session_state.trigger_search = True 
    else:
        st.session_state.search_results = []

def on_download_settings_change():
    save_settings({k: st.session_state[k] for k in DEFAULT_SETTINGS if k in st.session_state})

# =====================================================================
# 🧠 ИИ АГЕНТ (OPENROUTER)
# =====================================================================
def extract_json_from_llm(text):
    try:
        start = text.find('[')
        end = text.rfind(']') + 1
        if start != -1 and end != -1:
            return json.loads(text[start:end])
    except: pass
    return []

def call_openrouter(system_prompt, user_prompt, log_widget=None):
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    combined_prompt = f"ИНСТРУКЦИЯ:\n{system_prompt}\n\nЗАДАЧА:\n{user_prompt}"

    for model in LLM_MODELS_FALLBACK:
        if log_widget: log_widget.write(f"⏳ Думает ИИ: `{model}`...")
        try:
            payload = json.dumps({"model": model, "messages": [{"role": "user", "content": combined_prompt}], "temperature": 0.5})
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, data=payload, timeout=15)
            if resp.status_code == 200:
                if log_widget: log_widget.write(f"✅ Успешно ({model})!")
                return resp.json()['choices'][0]['message']['content'].strip()
        except: continue
    if log_widget: log_widget.error("❌ Все ИИ модели перегружены.")
    return None

# =====================================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ 
# =====================================================================
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

def seconds_to_hms(seconds):
    return str(datetime.timedelta(seconds=int(seconds))) if seconds >= 0 else "0:00:00"

def safe_int(val, default=0):
    try: return int(val) if val is not None else default
    except: return default

@st.cache_data
def get_movie_titles():
    if not os.path.exists(DB_NAME): return []
    try:
        with sqlite3.connect(DB_NAME) as conn: return pd.read_sql_query("SELECT DISTINCT title_ru FROM movies ORDER BY title_ru ASC", conn)['title_ru'].tolist()
    except: return []

def get_surrounding_context(imdb_id, target_start_sec, target_sub_id, window_sec=90):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT start_time, text FROM subtitles WHERE imdb_id = ? AND id BETWEEN ? AND ? ORDER BY start_time ASC", (imdb_id, target_sub_id - SUB_ID_RANGE, target_sub_id + SUB_ID_RANGE))
            rows = cursor.fetchall()
    except: return []

    context_items, seen_texts = [], set()
    for r_start, r_text in rows:
        r_sec = srt_to_seconds(r_start)
        if abs(r_sec - target_start_sec) <= window_sec and r_text not in seen_texts:
            seen_texts.add(r_text)
            time_display = str(r_start)[:8] if r_start else "00:00:00"
            context_items.append({"sec": r_sec, "label": f"[{time_display}] {str(r_text)[:70]}...", "text": r_text, "time_str": time_display, "is_target": abs(r_sec - target_start_sec) < 5})
    return context_items

def search_phrase_in_movie(imdb_id, phrase, anchor_sub_id):
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT start_time, text FROM subtitles WHERE imdb_id = ? AND id BETWEEN ? AND ? AND text LIKE ? ORDER BY start_time LIMIT 20", (imdb_id, anchor_sub_id - SUB_ID_RANGE, anchor_sub_id + SUB_ID_RANGE, f"%{phrase}%"))
            return cursor.fetchall()
    except: return []

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
    results = []
    try:
        res = subprocess.run(["yt-dlp", f"ytsearch10:{query}", "--dump-json", "--no-warnings"], capture_output=True, text=True, timeout=15)
        for line in res.stdout.strip().split("\n"):
            if line:
                v = json.loads(line)
                if v.get("duration", 0) >= min_duration:
                    results.append({"label": f"🔴 [YT] {v.get('title')} ({seconds_to_hms(v.get('duration'))})", "id": f"youtube:{v.get('id')}"})
    except: pass

    try:
        url = f"https://rutube.ru/api/search/video/?query={urllib.parse.quote(query)}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
        for v in resp.get("results", []):
            dur_str = v.get("duration", "0")
            dur_sec = srt_to_seconds(dur_str) if ":" in str(dur_str) else int(dur_str)
            if dur_sec == 0 or dur_sec >= min_duration:
                vid_url = v.get("video_url")
                if vid_url:
                    if not vid_url.startswith("http"): vid_url = "https://rutube.ru" + vid_url
                    results.append({"label": f"🟢 [RuTube] {v.get('title')} ({dur_str})", "id": f"rutube:{vid_url}"})
    except: pass
    return results

# =====================================================================
# 🔍 ПОИСКОВЫЕ АЛГОРИТМЫ
# =====================================================================
def build_sql_filters(min_rating, t_type, country_filter, genre_filter, specific_movie, params):
    sql = ""
    if min_rating > 0: sql += " AND m.rating >= ?"; params.append(min_rating)
    if t_type != "Все": sql += " AND m.type = ?"; params.append("movie" if t_type == "Фильмы" else "tv")
    if country_filter == "Наше (RU/SU)": sql += " AND (m.countries LIKE '%RU%' OR m.countries LIKE '%SU%')"
    elif country_filter == "Зарубежное": sql += " AND (m.countries NOT LIKE '%RU%' AND m.countries NOT LIKE '%SU%' OR m.countries IS NULL)"
    if genre_filter != "Любой": sql += " AND m.genres LIKE ?"; params.append(f"%{genre_filter}%")
    if specific_movie != "Все фильмы": sql += " AND m.title_ru = ?"; params.append(specific_movie)
    return sql

def perform_search(query_text, search_mode, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, log_widget=None):
    if search_mode == "По словам (Быстро ⚡️)":
        if log_widget: log_widget.write("⚡ Выполняем точный поиск по базе субтитров...")
        return _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie)
    else: 
        return _search_ai_agent(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, log_widget)

def _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie):
    try: conn = sqlite3.connect(DB_NAME); cursor = conn.cursor()
    except: return []

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
            
    search_query = " AND ".join(search_terms) if search_terms else "*"
    params = [search_query]

    sql = f"""
        WITH top_matches AS (SELECT rowid FROM subtitles_fts WHERE text MATCH ? ORDER BY rank LIMIT 2000),
        ranked AS (
            SELECT m.title_ru, m.year, m.genres, m.rating, m.type, m.season, m.episode,
                   s.start_time, s.end_time, s.text, m.imdb_id, m.countries, m.title_original, s.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.imdb_id, SUBSTR(LTRIM(LOWER(s.text), ' .-,:;!?…"'''), 1, 20) 
                       ORDER BY s.start_time ASC
                   ) AS rn
            FROM top_matches tm JOIN subtitles s ON s.id = tm.rowid JOIN movies m ON s.imdb_id = m.imdb_id WHERE 1=1
    """
    sql += build_sql_filters(min_rating, t_type, country_filter, genre_filter, specific_movie, params)
    sql += f") SELECT * FROM ranked WHERE rn = 1 ORDER BY rating DESC LIMIT {limit} OFFSET {offset}"
    
    try: 
        cursor.execute(sql, params)
        return [(row, 100.0) for row in cursor.fetchall()]
    except Exception as e: 
        return []
    finally: conn.close()

def _search_ai_agent(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie, log_widget=None):
    if offset == 0:
        scenario = st.session_state.get("ai_scenario", "🎬 Стандартный (поиск по описанию)")
        if "Свадебный" in scenario:
            system_p = """Ты гениальный комедийный режиссер видеомонтажа. Гость на свадьбе говорит ОДНО слово (пожелание). 
Придумай 3 смешные, абсурдные или легендарные цитаты из известного кино (СССР/Россия), которые станут идеальной реакцией на это слово (на контрасте или прямой ассоциации).
ОТВЕТЬ СТРОГО В ФОРМАТЕ JSON (массив из 3 объектов). НЕ ПИШИ НИКАКИХ СЛОВ ДО ИЛИ ПОСЛЕ JSON. ТОЛЬКО МАССИВ КВАДРАТНЫХ СКОБОК:
[{"quote": "только сама точная цитата без кавычек", "movie": "название фильма", "reasoning": "твоя режиссерская задумка - почему это смешно"}]"""
            user_p = f"Слово гостя: {query_text}"
        else:
            system_p = """Ты режиссер. Пользователь описывает сцену. Придумай 3 короткие фразы, которые герои могли бы сказать в такой ситуации.
ОТВЕТЬ СТРОГО В ФОРМАТЕ JSON. НЕ ПИШИ НИКАКИХ СЛОВ ДО ИЛИ ПОСЛЕ JSON. ТОЛЬКО МАССИВ КВАДРАТНЫХ СКОБОК:
[{"quote": "цитата", "movie": "предполагаемый жанр", "reasoning": "почему эта фраза подходит"}]"""
            user_p = f"Опиши сцену: {query_text}"

        llm_response = call_openrouter(system_p, user_p, log_widget)
        st.session_state.llm_ideas = extract_json_from_llm(llm_response) if llm_response else []

    results = []
    if getattr(st.session_state, 'llm_ideas', []):
        if log_widget: log_widget.write("⚡ **Шаг 2:** Ищем сгенерированные фразы в базе...")
        for idea in st.session_state.llm_ideas:
            if idea.get('quote'):
                results.extend(_search_fts(idea['quote'], limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie))
    else:
        if log_widget: log_widget.write("⚠️ ИИ не смог выдать данные. Выполняем точный поиск по вашему слову...")
        results = _search_fts(query_text, limit, offset, min_rating, t_type, country_filter, genre_filter, specific_movie)
            
    if log_widget: log_widget.write("🧹 Фильтруем дубликаты текстов...")
    seen_phrases, final_res = set(), []
    for row in sorted(results, key=lambda x: x[1], reverse=True):
        clean_text = re.sub(r'[^\w\s]', '', str(row[0][9]).lower()).strip()[:20]
        unique_key = (row[0][10], clean_text)
        if unique_key not in seen_phrases:
            seen_phrases.add(unique_key)
            final_res.append(row)
            
    return final_res[:limit]

# =====================================================================
# 🎨 ИНТЕРФЕЙС И САЙДБАР
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

    specific_movie = st.selectbox("📌 В конкретном кино:", all_movies, index=current_movie_idx, key="specific_movie", on_change=on_settings_change)
    t_type = st.radio("🎞 Тип медиа:", ["Все", "Фильмы", "Сериалы"], horizontal=True, key="t_type", on_change=on_settings_change)
    c_filter = st.radio("🌍 Страна:", ["Все", "Наше (RU/SU)", "Зарубежное"], key="c_filter", on_change=on_settings_change)
    genre_filter = st.selectbox("🎭 Жанр:", ["Любой", "Comedy", "Drama", "Action", "Sci-Fi", "Horror", "Romance", "Crime"], key="genre_filter", on_change=on_settings_change)
    min_rating = st.slider("⭐️ Мин. рейтинг IMDb:", 0.0, 10.0, step=0.1, key="min_rating", on_change=on_settings_change)

    st.markdown("---")
    st.header("✂️ Хронометраж")
    pad_start = st.number_input("Секунд ДО фразы:", 0.0, 120.0, step=5.0, key="pad_start", on_change=on_download_settings_change)
    pad_end = st.number_input("Секунд ПОСЛЕ фразы:", 0.0, 120.0, step=5.0, key="pad_end", on_change=on_download_settings_change)
    source_pref = st.radio("🌐 Источник:", ["all", "youtube", "rutube", "torrent"], format_func=lambda x: {"all": "Везде (YT -> RuTube -> Tor)", "youtube": "Только YouTube", "rutube": "Только RuTube", "torrent": "Только Torrent"}[x], key="source_pref", on_change=on_download_settings_change)

# =====================================================================
# 📥 МЕНЕДЖЕР ЗАГРУЗОК
# =====================================================================
if st.session_state.active_downloads:
    with st.expander(f"📥 Менеджер загрузок (Активных: {len([t for t in st.session_state.active_downloads.values() if t['status'] == 'running'])})", expanded=True):
        
        c_btn1, c_btn2 = st.columns(2)
        with c_btn1: st.button("🔄 Обновить статусы", use_container_width=True)
        with c_btn2:
            if st.button("📂 Открыть папку с видео (clips)", use_container_width=True, type="secondary"):
                abs_path = os.path.abspath(CLIPS_DIR)
                if sys.platform == "win32": os.startfile(abs_path)
                elif sys.platform == "darwin": subprocess.Popen(["open", abs_path])
                else: subprocess.Popen(["xdg-open", abs_path])
        
        st.markdown("---")
        cols = st.columns(3)
        for idx, (task_id, task) in enumerate(list(st.session_state.active_downloads.items())):
            col = cols[idx % 3]
            with col:
                with st.container(border=True):
                    st.write(f"**{task['title']}**")
                    if 'quote' in task: st.info(f"🗣️ *«{task['quote']}»*")
                        
                    if task['status'] == 'running':
                        if task['process'].poll() is None:
                            st.caption("⏳ Идет скачивание...")
                            try:
                                with open(task['log_file'], 'r', encoding='utf-8') as f:
                                    lines = f.readlines()
                                    if lines: st.code("".join([l for l in lines if l.strip()][-6:]), language="log")
                            except: pass
                            if st.button("Остановить ❌", key=f"stop_{task_id}"):
                                task['process'].terminate()
                                task['status'] = 'stopped'
                                st.rerun()
                        else:
                            if os.path.exists(task['file_path']) and os.path.getsize(task['file_path']) > 1024: task['status'] = 'success'
                            else: task['status'] = 'error'
                            st.rerun()
                    elif task['status'] == 'success':
                        st.success("✅ Готово!")
                        st.video(task['file_path'])
                        with open(task['file_path'], "rb") as file:
                            st.download_button("💾 Сохранить", data=file, file_name=os.path.basename(task['file_path']), mime="video/mp4", key=f"dl_btn_{task_id}", type="primary")
                        if st.button("Удалить из списка", key=f"clear_{task_id}"):
                            del st.session_state.active_downloads[task_id]
                            st.rerun()
                    elif task['status'] in ['error', 'stopped']:
                        st.error("❌ Ошибка/Отменено")
                        try:
                            with open(task['log_file'], 'r', encoding='utf-8') as f:
                                st.code("".join(f.readlines()[-5:]), language="log")
                        except: pass
                        if st.button("Убрать", key=f"clear_{task_id}"):
                            del st.session_state.active_downloads[task_id]
                            st.rerun()

# =====================================================================
# ГЛАВНЫЙ ЭКРАН И РЕЗУЛЬТАТЫ
# =====================================================================
st.text_input("🔍 Поиск фрагмента:", placeholder="Введите точную цитату или опишите сцену и нажмите Enter...", key="search_query_input", on_change=trigger_new_search)

col1, col2 = st.columns([1, 5])
with col1:
    if st.button("Найти 🚀", use_container_width=True, type="primary"): trigger_new_search()

if st.session_state.trigger_search:
    st.session_state.trigger_search = False
    with st.status("🔍 Запуск поиска и анализ...", expanded=True) as status_box:
        st.session_state.search_results = perform_search(
            st.session_state.last_query, st.session_state.search_mode, RESULTS_PER_PAGE, 0,
            st.session_state.min_rating, st.session_state.t_type, st.session_state.c_filter,
            st.session_state.genre_filter, st.session_state.specific_movie, log_widget=status_box
        )
        if st.session_state.search_results: status_box.update(label="✅ Анализ завершен! Сцены найдены.", state="complete", expanded=False)
        else: status_box.update(label="❌ Ничего не найдено", state="error", expanded=False)

if search_mode == "ИИ-Агент (OpenRouter 🤖)" and getattr(st.session_state, 'llm_ideas', []):
    st.markdown("### 🧠 Режиссерские задумки ИИ:")
    df_ideas = pd.DataFrame(st.session_state.llm_ideas)
    if not df_ideas.empty:
        df_ideas.columns = ["Фраза для поиска", "Ожидаемый фильм/Жанр", "Почему это круто (Задумка)"]
        st.table(df_ideas)
    st.markdown("---")

if st.session_state.search_results:
    st.success(f"Найдено сцен: {len(st.session_state.search_results)} (показана страница {st.session_state.search_offset//RESULTS_PER_PAGE + 1})")

    for i, (row, similarity) in enumerate(st.session_state.search_results):
        (title, year, genres, rating, m_type, season, ep, start_srt, end_srt, text, imdb_id, countries, orig_title, s_id) = row[:14]
        display_title = f"📺 {title} (S{safe_int(season):02d}E{safe_int(ep):02d})" if m_type == "tv" else f"🎬 {title} ({safe_int(year)})"
        
        text_html = text
        if search_mode == "По словам (Быстро ⚡️)" and st.session_state.last_query:
            words = [re.escape(w) for w in re.findall(r'\w+', st.session_state.last_query)]
            pattern = f"(?i)({'|'.join(words)})[а-яА-Яa-zA-Z]*" if words else ""
            try: text_html = re.sub(pattern, r'<mark style="background-color: #ffd700; color: #000; border-radius: 3px; padding: 0 2px;"><b>\g<0></b></mark>', text) if pattern else text
            except: pass

        with st.container(border=True):
            st.subheader(f"{display_title} | ★ {rating if rating else 0.0}")
            
            c1, c2 = st.columns([3, 2])
            saved_source, saved_offset = get_saved_source_info(imdb_id)
            auto_offset = 0.0
            
            with c1:
                st.markdown(f"**Таймкод:** `{start_srt}` ➡️ `{end_srt}`")
                st.markdown(f"**Цитата:** 🗣️ _{text_html}_", unsafe_allow_html=True)
                
                if saved_source:
                    st.info(f"📌 **Источник закреплен:** {str(saved_source)[:20]}... | Сдвиг: {saved_offset} сек.")
                    if st.button("🗑 Отвязать источник", key=f"reset_{imdb_id}_{i}_{st.session_state.search_offset}"):
                        delete_source_info(imdb_id)
                        st.rerun()

            with c2:
                # --- ВОССТАНОВЛЕННЫЙ БЛОК УМНОЙ СИНХРОНИЗАЦИИ ---
                with st.expander("🛠 Рассинхрон? (Синхронизация субтитров)", expanded=False):
                    target_sec = srt_to_seconds(start_srt)
                    
                    st.markdown("**🔍 Шаг 1: Искать точную фразу в файле**")
                    deep_query = st.text_input("Услышали другую фразу в видео?", key=f"dq_{imdb_id}_{i}_{st.session_state.search_offset}")
                    
                    if deep_query:
                        deep_results = search_phrase_in_movie(imdb_id, deep_query, s_id)
                        if deep_results:
                            deep_options = {f"[{r[0][:8]}] {r[1]}": srt_to_seconds(r[0]) for r in deep_results}
                            heard_label = st.selectbox("Выберите точную фразу:", list(deep_options.keys()), key=f"ds_{imdb_id}_{i}_{st.session_state.search_offset}")
                            auto_offset = target_sec - deep_options[heard_label]
                            st.success(f"🤖 Сдвиг: **{'+' if auto_offset > 0 else ''}{auto_offset:.1f} сек.**")
                        else: st.warning("Фраза не найдена.")
                    else:
                        context_items = get_surrounding_context(imdb_id, target_sec, s_id)
                        if context_items:
                            st.markdown("**ИЛИ выберите из ближайшего текста:**")
                            options = {item["label"]: item["sec"] for item in context_items}
                            default_label = next((item["label"] for item in context_items if item["is_target"]), list(options.keys())[0])
                            
                            heard_label = st.selectbox("Что прозвучало в видео?", list(options.keys()), index=list(options.keys()).index(default_label), key=f"sync_{imdb_id}_{i}_{st.session_state.search_offset}")
                            auto_offset = target_sec - options[heard_label]
                            if auto_offset != 0: st.success(f"🤖 Сдвиг: **{'+' if auto_offset > 0 else ''}{auto_offset:.1f} сек.**")

                    st.markdown("---")
                    st.caption("Если видео скачалось криво, измените это значение и нажмите СКАЧАТЬ заново:")
                    manual_offset = st.number_input("Шаг 2: Ручная подгонка (+/- сек):", min_value=-600.0, max_value=600.0, value=float(saved_offset), step=0.5, key=f"man_{imdb_id}_{i}_{st.session_state.search_offset}")

                # --- БЛОК ИНСПЕКТОРА ИСТОЧНИКОВ ---
                with st.expander("🔎 Качается мусор? (Выбор правильного видео)", expanded=False):
                    search_q = f"{title} {year} полный фильм" if m_type != "tv" else f"{title} S{safe_int(season):02d}E{safe_int(ep):02d}"
                    if st.button("Найти ролики на YT / RuTube", key=f"find_src_{imdb_id}_{i}"):
                        with st.spinner("Ищем видео..."):
                            st.session_state[f"sources_{imdb_id}_{i}"] = manual_video_search(search_q, srt_to_seconds(start_srt) + 30)
                    
                    saved_sources = st.session_state.get(f"sources_{imdb_id}_{i}", [])
                    if saved_sources:
                        source_options = {s["label"]: s["id"] for s in saved_sources}
                        selected_label = st.selectbox("Выберите оригинальный фильм/сериал:", list(source_options.keys()), key=f"sel_src_{imdb_id}_{i}")
                        
                        if st.button("💾 Закрепить этот источник", key=f"fix_src_{imdb_id}_{i}"):
                            save_source_info(imdb_id, source_options[selected_label], saved_offset)
                            st.success("Источник закреплен!")
                            st.rerun()

                final_offset = auto_offset + manual_offset
                start_sec = max(0, srt_to_seconds(start_srt) - pad_start + final_offset)
                end_sec = srt_to_seconds(end_srt) + pad_end + final_offset
                duration = max(1, end_sec - start_sec)

                # --- КНОПКА СКАЧИВАНИЯ ---
                if st.button("⬇️ АВТО-СКАЧИВАНИЕ (В очередь)", key=f"dl_auto_{imdb_id}_{start_srt}_{i}_{st.session_state.search_offset}", use_container_width=True, type="primary"):
                    
                    # Если пользователь подобрал новый сдвиг, сразу сохраняем его в БД
                    if final_offset != saved_offset:
                        save_source_info(imdb_id, saved_source if saved_source else "", final_offset)

                    task_id = f"task_{imdb_id}_{int(time.time())}"
                    
                    safe_query = "".join(c for c in st.session_state.last_query if c.isalnum() or c in " А-Яа-яЁёA-Za-z").strip().replace(" ", "_")[:20]
                    prefix = f"{safe_query}_" if safe_query else ""
                    safe_name = "".join(c for c in f"{title}_{year}" if c.isalnum() or c in " _-").strip().replace(" ", "_")
                    
                    expected_file = os.path.join(CLIPS_DIR, f"{prefix}{safe_name}_{task_id}.mp4")
                    
                    cmd = [sys.executable, "-u", "magnet_get.py", "--title", str(title), "--orig_title", str(orig_title or ""), 
                           "--year", str(safe_int(year)), "--type", str(m_type), "--season", str(safe_int(season)), "--episode", str(safe_int(ep)), 
                           "--start", seconds_to_hms(start_sec), "--duration", str(int(duration)), "--source", source_pref, "--output", expected_file]
                    if saved_source: cmd.extend(["--force_source", saved_source])

                    log_file_path = os.path.join(CLIPS_DIR, f"{task_id}_log.txt")
                    log_file = open(log_file_path, "w", encoding="utf-8")
                    process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)
                    
                    st.session_state.active_downloads[task_id] = {
                        "title": f"{display_title} [{start_srt}]", 
                        "quote": text, 
                        "process": process, 
                        "file_path": expected_file, 
                        "log_file": log_file_path, 
                        "status": "running"
                    }
                    st.toast("📥 Добавлено в очередь!")
                    st.rerun()

    # --- КНОПКА ПАГИНАЦИИ ---
    st.markdown("---")
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        if st.button("🔄 Загрузить еще 10 сцен...", use_container_width=True):
            st.session_state.search_offset += RESULTS_PER_PAGE
            with st.status("���� Подгружаем дополнительные сцены...", expanded=True) as load_more_status:
                more_results = perform_search(
                    st.session_state.last_query, st.session_state.search_mode, RESULTS_PER_PAGE, st.session_state.search_offset,
                    st.session_state.min_rating, st.session_state.t_type, st.session_state.c_filter,
                    st.session_state.genre_filter, st.session_state.specific_movie, log_widget=load_more_status
                )
                if more_results:
                    st.session_state.search_results.extend(more_results)
                    load_more_status.update(label="✅ Сцены подгружены!", state="complete", expanded=False)
                    st.rerun()
                else:
                    load_more_status.update(label="Больше результатов нет", state="error", expanded=False)
                    st.info("Больше результатов нет.")