Отличное завершение текущего этапа! Вы не только построили рабочий инструмент, но и задокументировали его.

Сначала я дам вам **небольшое обновление кода**, о котором вы просили (настройка хронометража: отступы до и после фразы), а затем — **идеальный, подробный `README.md`**, который отражает _реальную_, текущую архитектуру вашего крутого проекта.

---

### 1. Обновление кода: Настройка хронометража (Отступы)

Нам нужно изменить только Мастер-Контроллер. Я добавил в него запросы: _"Сколько секунд захватить ДО начала фразы?"_ и _"Сколько ПОСЛЕ?"_.

Замените код в файле **`03_search_and_download.py`**:

<details>
<summary>Показать обновленный код 03_search_and_download.py</summary>

```python
import sqlite3
import re
import datetime
import subprocess
import sys

DB_NAME = 'movies_master.sqlite'

def srt_to_seconds(srt_time_str):
    srt_time_str = srt_time_str.replace('.', ',')
    time_part, ms_part = srt_time_str.split(',')
    h, m, s = map(int, time_part.split(':'))
    ms = int(ms_part)
    return h * 3600 + m * 60 + s + ms / 1000.0

def seconds_to_hms(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

def search_jokes(keyword, limit=7, min_rating=0.0, target_type='all', country_filter='all', genre_filter=None):
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
        WHERE s.id IN (
            SELECT rowid FROM subtitles_fts WHERE text MATCH ?
        )
    '''

    if min_rating > 0:
        sql += ' AND m.rating >= ?'
        params.append(min_rating)

    if target_type in ['movie', 'tv']:
        sql += ' AND m.type = ?'
        params.append(target_type)

    if country_filter == 'ru':
        sql += " AND (m.countries LIKE '%RU%' OR m.countries LIKE '%SU%')"
    elif country_filter == 'foreign':
        sql += " AND (m.countries NOT LIKE '%RU%' AND m.countries NOT LIKE '%SU%' OR m.countries IS NULL)"

    if genre_filter:
        sql += " AND m.genres LIKE ?"
        params.append(f"%{genre_filter}%")

    sql += '''
        GROUP BY m.imdb_id, SUBSTR(s.start_time, 1, 5)
        ORDER BY m.rating DESC
        LIMIT ?
    '''
    params.append(limit)

    cursor.execute(sql, params)
    results = cursor.fetchall()
    conn.close()
    return results

# ДОБАВЛЕНЫ ПАРАМЕТРЫ pad_start И pad_end
def download_clip(title, orig_title, year, imdb_id, start_srt, end_srt, m_type, season, episode, source_pref, pad_start, pad_end):
    # ПРИМЕНЯЕМ ПОЛЬЗОВАТЕЛЬСКИЕ ОТСТУПЫ
    start_sec = max(0, srt_to_seconds(start_srt) - pad_start)
    end_sec = srt_to_seconds(end_srt) + pad_end
    duration = end_sec - start_sec
    start_hms = seconds_to_hms(start_sec)

    print("\n" + "="*60)
    print("🎬 ПЕРЕДАЮ ЗАДАЧУ ДВИЖКУ СКАЧИВАНИЯ 🎬")
    print("="*60)

    command = [
        sys.executable, "magnet_get.py",
        "--title", title,
        "--orig_title", orig_title,
        "--year", str(year),
        "--type", str(m_type),
        "--season", str(season),
        "--episode", str(episode),
        "--start", start_hms,
        "--duration", str(int(duration)),
        "--source", source_pref
    ]

    subprocess.run(command)

def main():
    print("="*60)
    print(" 🤖 AI-РЕЖИССЕР МОНТАЖА (v2.7 Гибкий тайминг) ")
    print("="*60)

    while True:
        word = input("\n📝 Введите слово/фразу (или 'q' для выхода): ").strip()
        if word.lower() == 'q': break
        if not word: continue

        print("\n⚙️  НАСТРОЙКИ ПОИСКА (Нажмите Enter, чтобы пропустить):")
        rating_input = input("   ⭐️ Мин. рейтинг (например, 7.5): ").strip()
        min_r = float(rating_input) if rating_input.replace('.', '', 1).isdigit() else 0.0

        type_input = input("   🎞  Где ищем? (1 - Фильмы, 2 - Сериалы, Enter - Везде): ").strip()
        t_type = 'all'
        if type_input == '1': t_type = 'movie'
        elif type_input == '2': t_type = 'tv'

        country_input = input("   🌍 Производство? (1 - Наше [RU/SU], 2 - Зарубежное, Enter - Везде): ").strip()
        c_filter = 'all'
        if country_input == '1': c_filter = 'ru'
        elif country_input == '2': c_filter = 'foreign'

        genre_input = input("   🎭 Жанр? (1-Комедия, 2-Драма, 3-Боевик, 4-Фантастика, 5-Ужасы, Enter-Любой): ").strip()
        g_filter = None
        if genre_input == '1': g_filter = 'Comedy'
        elif genre_input == '2': g_filter = 'Drama'
        elif genre_input == '3': g_filter = 'Action'
        elif genre_input == '4': g_filter = 'Sci-Fi'
        elif genre_input == '5': g_filter = 'Horror'
        elif genre_input: g_filter = genre_input

        print("\n🔍 Ищу в локальной базе...\n")
        results = search_jokes(word, limit=7, min_rating=min_r, target_type=t_type, country_filter=c_filter, genre_filter=g_filter)

        if not results:
            print("❌ По вашим фильтрам ничего не найдено.")
            continue

        for i, row in enumerate(results, 1):
            title, year, genres, rating, m_type, season, ep, start, end, text, imdb_id, countries, orig_title = row
            c_disp = countries if countries else "Unknown"

            if m_type == 'tv' and season > 0:
                display_title = f"📺 {title} (S{season:02d}E{ep:02d})"
            else:
                display_title = f"🎬 {title} ({year})"

            text_hl = re.sub(f'(?i)({word}[а-яА-Яa-zA-Z]*)', r'【\1】', text)

            print(f"[{i}] {display_title} | 🌍 {c_disp} | ★ {rating} | {genres}")
            print(f"    ⏱ {start} --> {end}")
            print(f"    💬 {text_hl}\n")

        choice = input("Какой номер качаем? (Enter - пропустить поиск): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            selected = results[int(choice)-1]

            src_input = input("Где искать видео? (1 - Везде, 2 - ТОЛЬКО Торрент): ").strip()
            pref = "torrent" if src_input == "2" else "all"

            # --- НОВЫЙ БЛОК: НАСТРОЙКА ХРОНОМЕТРАЖА ---
            print("\n✂️  Настройка отрезка (по умолчанию берем 3 секунды до фразы и 3 после):")
            pad_start_in = input("Сколько секунд захватить ДО начала фразы? (Enter = 3): ").strip()
            pad_end_in = input("Сколько секунд захватить ПОСЛЕ конца фразы? (Enter = 3): ").strip()

            p_start = float(pad_start_in) if pad_start_in.replace('.','',1).isdigit() else 3.0
            p_end = float(pad_end_in) if pad_end_in.replace('.','',1).isdigit() else 3.0
            # ------------------------------------------

            download_clip(
                title=selected[0],
                orig_title=selected[12],
                year=selected[1],
                imdb_id=selected[10],
                start_srt=selected[7],
                end_srt=selected[8],
                m_type=selected[4],
                season=selected[5],
                episode=selected[6],
                source_pref=pref,
                pad_start=p_start, # Передаем отступы
                pad_end=p_end
            )

if __name__ == '__main__':
    main()
```

</details>

---

### 2. Полный `README.md` вашего проекта

Скопируйте этот текст в файл `README.md` в корне вашей папки.

````markdown
# 🎬 AI-Режиссер Монтажа (Smart Meme Downloader)

Локальная система для автоматического поиска смешных фраз, цитат и мемов по огромной базе кино-субтитров с последующим моментальным скачиванием нужного фрагмента видео (через торренты или YouTube).

Проект создан как умный ассистент режиссера монтажа: избавляет от необходимости хранить терабайты фильмов на жестком диске и искать нужные кадры вручную.

---

## 🏛 Архитектура проекта

Проект разделен на две независимые части: **Сборка Базы (Data Pipeline)** и **Движок Поиска/Скачивания**.

### 1. Ядро Данных (Мозг)

- **Сырье:** Дампы `OpenSubtitles` (до 2026 года) и базы метаданных `IMDb` / `TMDB`.
- **Очистка:** На входе жестко отсекаются нерелевантные форматы (`Shorts`, `Adult`, `Documentary`, `News`).
- **Умная нарезка:** Субтитры склеиваются не по строкам, а по алгоритму **Sliding Window** (Временное окно 15 сек + Детектор смены сцен по тишине > 3 сек + Смысловой нахлест в 2 фразы).
- **Хранилище:** `SQLite` с включенным индексом `FTS5` для сверхбыстрого полнотекстового поиска с учетом окончаний.

### 2. Движок Скачивания (Мускулы)

- `yt-dlp` для вырезания фрагментов с YouTube "на лету".
- `peerflix` + `ffmpeg` для стриминга торрентов. Скрипт умеет обходить блокировки трекеров, заглядывать внутрь `.torrent` файлов для выбора правильной серии сериала и имеет защиту от "мертвых" раздач (таймаут 90 сек и переход к следующему сидеру).

---

## 🚀 Как использовать (Инструкция)

### Повседневная работа (Поиск и скачивание)

Запустите Мастер-Контроллер:

```bash
python 03_search_and_download.py
```
````

1. Введите искомое слово (например: `ложь` или `кофе`).
2. Настройте фильтры (Жанр, Страна производства, Рейтинг).
3. Выберите понравившуюся цитату из выдачи.
4. Настройте хронометраж (сколько секунд захватить до и после фразы).
5. Скрипт скачает готовый `.mp4` файл в папку `/clips/`.

### Инструкция по сборке Базы с нуля (Disaster Recovery)

Если база `movies_master.sqlite` удалена, выполните пайплайн сборки строго в таком порядке:

1. **`python 01_extract_ultimate.py`** — Извлекает тексты из основного 120 ГБ дампа `opensubs.db` по карте `subtitles_all.txt`.
2. **`python 01b_extract_new_ultimate.py`** — Находит все новые дампы в папке (2023-2026), парсит `.nfo` и подгружает новые субтитры.
3. **`python 02_enrich_metadata.py`** — Оффлайн-обогащение. Читает `title.basics/akas/ratings`. Находит идеальные кириллические названия фильмов (отсекая транслит) и проставляет рейтинги.
4. _(Опционально)_ **`python 02c_fetch_countries_tmdb.py`** — (Требуется VPN). Проходится по фильмам и вытягивает 100% точные страны производства (RU, SU, US и т.д.) через API TMDB.

---

## 🔮 Roadmap (Планы на будущее)

Проект находится на стадии `V 2.7` (Точный поиск по тексту с фильтрами).
Дальнейшие шаги эволюции:

### Этап 1: Семантический Векторный Поиск (Уровень ИИ)

Переход от поиска по "буквам" к поиску по "смыслу" (контексту).

- **Инструменты:** `ChromaDB` (Векторная БД) + локальная модель `SentenceTransformers` (`paraphrase-multilingual`).
- **Как будет работать:** Вся база текстов будет переведена в математические векторы. Это позволит искать сцены по вайбу: _"смеется над неудачником"_, _"грустное расставание"_, даже если этих слов нет в субтитрах.

### Этап 2: AI-Режиссер (Анализ голоса)

- **Инструменты:** `faster-whisper` + локальная LLM (`Ollama` / `Llama 3`).
- **Как будет работать:** Пользователь загружает сырое видео (например, тост гостя на свадьбе). Whisper переводит голос в текст с таймкодами. LLM находит смешные места (панчлайны), сама придумывает запросы (например: _"Ищем шутку про алкоголь"_) и отправляет их в нашу `ChromaDB`.

### Этап 3: Графический Интерфейс (UI) и Интеграция

- Создание Web-интерфейса на `Streamlit` с выводом постеров фильмов.
- Система "Избранного" (Коллекция мемов с тегами в JSON).
- Автоматическая генерация `.xml` файлов для экспорта готового таймлайна прямо в Adobe Premiere Pro.

```

---

**Поздравляю! Вы проделали невероятный путь.** Теперь у вас есть мощная кодовая база и четкий план. Когда будете готовы перейти к Векторным базам (ChromaDB) — дайте знать, я уже подготовил концепт!
```
