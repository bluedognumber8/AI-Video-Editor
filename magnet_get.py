# magnet_get.py
"""
CLI-утилита для скачивания фрагментов видео из YouTube, RuTube и торрентов (RuTracker).
Использует yt-dlp, ffmpeg и TorrServer для мгновенного стриминга нужных кусков торрента.
"""

import argparse
import requests
from bs4 import BeautifulSoup
import urllib.parse
import os
import sys
import subprocess
import time
import socket
import json
import re
import logging
import tempfile
import shutil
import atexit
import signal

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(handler)

# WHY: загружаем .env если есть python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import bencodepy
except ImportError:
    logger.warning("bencodepy не установлен. Парсинг сериалов будет недоступен. Выполните: pip install bencodepy")
    bencodepy = None

# Определяем имя бинарника TorrServer по умолчанию в зависимости от ОС
# Так как вы используете пакет из AUR, по умолчанию берем глобальную команду
DEFAULT_TS_BINARY = "torrserver"

# --- НАСТРОЙКИ ---
CONFIG = {
    "rutracker": {
        "username": os.environ.get("RUTRACKER_USERNAME", ""),
        "password": os.environ.get("RUTRACKER_PASSWORD", ""),
        "cookie_file": "rutracker_cookies.json",
    },
    "torrserver": {
        "binary_path": os.environ.get("TORRSERVER_PATH", DEFAULT_TS_BINARY),
    },
    "search": {
        "priority": ["youtube", "rutube", "torrent"],
        "min_seeds": 5,
        "max_size_gb": 100.0, # Увеличили лимит для сериалов, так как качаем не весь файл
    },
    "scoring": {
        "res_1080p": 1000,
        "res_720p": 300,
        "source_web": 400,
    },
    "clip": {
        "output_folder": "clips",
        "ffmpeg_timeout": 180,
    },
    "domains": ["rutracker.org", "rutracker.net", "rutracker.nl"],
}

# Track engine instances for cleanup on exit
_engines = []
def _cleanup_engines():
    for e in _engines:
        try: e.stop()
        except Exception: pass
atexit.register(_cleanup_engines)
def _signal_handler(signum, frame):
    _cleanup_engines()
    sys.exit(1)
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# =====================================================================
# 🛠 УТИЛИТЫ
# =====================================================================

def time_to_seconds(time_str):
    """Преобразует строку времени HH:MM:SS в секунды."""
    try:
        parts = time_str.split(":")
        if len(parts) == 3:
            return int(float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2]))
        elif len(parts) == 2:
            return int(float(parts[0]) * 60 + float(parts[1]))
        return int(float(parts[0]))
    except (ValueError, IndexError, AttributeError):
        return 0


def seconds_to_time(seconds):
    """Преобразует секунды в строку HH:MM:SS."""
    try:
        s = max(0, int(seconds))
        return f"{s // 3600:02}:{s % 3600 // 60:02}:{s % 60:02}"
    except (ValueError, TypeError):
        return "00:00:00"


def get_free_port():
    """Находит свободный порт на localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def ensure_dir(path):
    """Создаёт директорию если не существует."""
    os.makedirs(path, exist_ok=True)


def sanitize_filename(name, max_length=80):
    """WHY: предотвращение path traversal и невалидных имён файлов."""
    safe = re.sub(r'[^\w\s\-]', '', str(name), flags=re.UNICODE)
    safe = safe.strip().replace(" ", "_")
    if not safe:
        safe = "unnamed"
    return safe[:max_length]


def validate_path_within_dir(file_path, allowed_dir):
    """WHY: предотвращение path traversal."""
    abs_allowed = os.path.abspath(allowed_dir)
    abs_path = os.path.abspath(file_path)
    if not abs_path.startswith(abs_allowed + os.sep) and abs_path != abs_allowed:
        raise ValueError(f"Path traversal detected: {file_path}")
    return abs_path


# =====================================================================
# 🔍 ПРОВЕРКА ЗАВИСИМОСТЕЙ
# =====================================================================

def check_dependency(cmd_name):
    """Проверяет что CLI-утилита доступна в PATH."""
    try:
        find_cmd = "where" if sys.platform == "win32" else "which"
        return subprocess.run(
            [find_cmd, cmd_name],
            capture_output=True, text=True
        ).returncode == 0
    except FileNotFoundError:
        return False


def check_all_dependencies(sources):
    """Проверяет все необходимые зависимости для выбранных источников."""
    missing = []
    if not check_dependency("ffmpeg"):
        missing.append("ffmpeg")
    
    if "torrent" in sources:
        ts_path = CONFIG["torrserver"]["binary_path"]
        # Проверяем либо локальный путь, либо наличие в PATH
        if not os.path.exists(ts_path) and not check_dependency(ts_path):
            missing.append(
                f"TorrServer (не найден по пути: {ts_path}). "
                "Установите через AUR (torrserver-bin) или скачайте с GitHub."
            )
            
    if ("youtube" in sources or "rutube" in sources):
        if not check_dependency("yt-dlp"):
            missing.append("yt-dlp")
            
    if missing:
        logger.error(f"Не установлены зависимости:\n  - " + "\n  - ".join(missing))
        return False
    return True


# =====================================================================
# 🔐 АВТОРИЗАЦИЯ RUTRACKER
# =====================================================================

def load_cookies(session):
    cookie_file = CONFIG["rutracker"]["cookie_file"]
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                cookies_data = json.load(f)
                if isinstance(cookies_data, dict):
                    # Legacy format: flat dict name→value
                    for name, value in cookies_data.items():
                        session.cookies.set(name, value)
                elif isinstance(cookies_data, list):
                    # New format: list of cookie dicts with metadata
                    for c in cookies_data:
                        session.cookies.set(c["name"], c["value"],
                            domain=c.get("domain", ""),
                            path=c.get("path", "/"),
                            secure=c.get("secure", False))
                return True
        except (json.JSONDecodeError, IOError, KeyError) as e:
            logger.warning(f"Ошибка загрузки cookies: {e}")
    return False


def save_cookies(session):
    cookie_file = CONFIG["rutracker"]["cookie_file"]
    try:
        with open(cookie_file, 'w', encoding='utf-8') as f:
            cookie_list = []
            for cookie in session.cookies:
                cookie_list.append({
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure
                })
            json.dump(cookie_list, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"Ошибка сохранения cookies: {e}")


def do_login(session, domain):
    username = CONFIG["rutracker"]["username"]
    password = CONFIG["rutracker"]["password"]

    if not username or not password:
        logger.error("RUTRACKER_USERNAME и RUTRACKER_PASSWORD не заданы в переменных окружения")
        return False

    try:
        session.post(
            f"https://{domain}/forum/login.php",
            data={"login_username": username, "login_password": password, "login": "Вход"},
            timeout=10
        )
        if "bb_session" in session.cookies.get_dict():
            save_cookies(session)
            logger.info(f"Успешная авторизация на {domain}")
            return True
    except requests.RequestException as e:
        logger.warning(f"Ошибка входа на {domain}: {e}")
    return False


def ensure_rutracker_session():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    load_cookies(session)

    for domain in CONFIG["domains"]:
        try:
            r = session.get(f"https://{domain}/forum/privmsg.php?folder=inbox", timeout=8, allow_redirects=True)
            if r.status_code == 200 and "login" not in r.url.lower():
                logger.info(f"Сессия RuTracker активна ({domain})")
                return session, domain
        except requests.RequestException:
            continue

        if do_login(session, domain):
            return session, domain

    logger.error("Не удалось авторизоваться на RuTracker")
    return None, None


# =====================================================================
# 🔍 ПОИСК И ОЦЕНКА ТОРРЕНТОВ
# =====================================================================

def generate_search_queries(title_ru, title_orig, year, m_type, season, episode):
    queries = []
    y = int(year)

    if m_type == "movie" or int(season) == 0:
        if y > 0: queries.append(f"{title_ru} {y}")
        if title_orig and title_orig.lower() != title_ru.lower():
            if y > 0: queries.append(f"{title_orig} {y}")
        queries.append(title_ru)
    else:
        s, e = int(season), int(episode)
        queries.append(f"{title_ru} {s} сезон")
        if title_orig and title_orig.lower() != title_ru.lower():
            queries.append(f"{title_orig} {s} сезон")
            queries.append(f"{title_orig} S{s:02d}")
        queries.append(f"{title_ru} S{s:02d}E{e:02d}")

    seen = set()
    return [q for q in queries if not (q in seen or seen.add(q))]


def evaluate_torrent(title, seeds, size_gb, is_tv):
    t = title.lower()

    bad_keywords = ["director's cut", "режиссерская", "extended", "расширенная", "unrated", "4k", "2160p", "remux"]
    if any(bad in t for bad in bad_keywords):
        return -100000

    score = seeds * 1000

    if "1080p" in t: score += CONFIG["scoring"]["res_1080p"]
    elif "720p" in t: score += CONFIG["scoring"]["res_720p"]
    if "web-dl" in t or "webrip" in t: score += CONFIG["scoring"]["source_web"]

    # Штрафуем слишком гигантские файлы, но для сериалов делаем поблажку (там бывают паки по 60 ГБ)
    penalty = int(size_gb * 5) if is_tv else int(size_gb * 10)
    score -= penalty

    return score


# =====================================================================
# 🎬 YOUTUBE / RUTUBE — СТРИМИНГОВЫЕ ИСТОЧНИКИ
# =====================================================================

def do_stream_download(video_url, start_time, duration_secs, output_path, platform_name):
    end_time = seconds_to_time(time_to_seconds(start_time) + duration_secs)
    logger.info(f"Скач��вание фрагмента с {platform_name.upper()}...")
    
    download_cmd = [
        "yt-dlp", "--quiet", "--progress",
        "--download-sections", f"*{start_time}-{end_time}",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--force-keyframes-at-cuts", "-o", output_path, video_url
    ]

    try:
        result = subprocess.run(download_cmd, timeout=120)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            logger.info(f"Файл скачан: {output_path}")
            return True
        else:
            try: os.remove(output_path)
            except OSError: pass
    except subprocess.TimeoutExpired:
        logger.warning("Таймаут при скачивании через yt-dlp (120с)")
    return False


def try_youtube(query, start_time, duration_secs, output_path):
    logger.info(f"[YOUTUBE] Поиск: '{query}'")
    req_dur = time_to_seconds(start_time) + duration_secs

    try:
        res = subprocess.run(
            ["yt-dlp", f"ytsearch5:{query} фильм", "--dump-json", "--no-warnings"],
            capture_output=True, text=True, timeout=15
        )
        if res.returncode != 0: return False

        for line in res.stdout.strip().split("\n"):
            if line.strip():
                try: 
                    v = json.loads(line)
                    if v.get("duration", 0) > req_dur:
                        logger.info(f"  ВЫБРАНО ВИДЕО: {v.get('title', 'Без названия')}")
                        url = f"https://www.youtube.com/watch?v={v.get('id')}"
                        return do_stream_download(url, start_time, duration_secs, output_path, "youtube")
                except json.JSONDecodeError: continue
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError): pass
    return False


def try_rutube(query, start_time, duration_secs, output_path):
    logger.info(f"[RUTUBE] Поиск: '{query}'")
    req_dur = time_to_seconds(start_time) + duration_secs

    try:
        url = f"https://rutube.ru/api/search/video/?query={urllib.parse.quote(query)}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        results = resp.json().get("results", [])
        
        for v in results:
            dur_str = str(v.get("duration", "0"))
            dur_sec = time_to_seconds(dur_str) if ":" in dur_str else int(dur_str)

            if dur_sec == 0 or dur_sec > req_dur:
                video_url = v.get("video_url")
                if video_url:
                    if not video_url.startswith("http"): video_url = "https://rutube.ru" + video_url
                    logger.info(f"  ВЫБРАНО ВИДЕО: {v.get('title', 'Без названия')}")
                    return do_stream_download(video_url, start_time, duration_secs, output_path, "rutube")
    except (requests.RequestException, json.JSONDecodeError, OSError): pass
    return False


# =====================================================================
# 🔵 TORRENT — TORRSERVER + FFMPEG (HTTP RANGE REQUESTS)
# =====================================================================

class TorrServerEngine:
    def __init__(self, binary_path):
        self.binary_path = binary_path
        self.process = None
        self.db_dir = None
        self.use_system = False
        
        # 1. Сначала пытаемся подключиться к системному TorrServer (порт 8090)
        try:
            if requests.get("http://127.0.0.1:8090/echo", timeout=1).status_code == 200:
                logger.info("✅ Найден системный TorrServer на порту 8090. Подключаемся к нему!")
                self.port = 8090
                self.base_url = f"http://127.0.0.1:{self.port}"
                self.use_system = True
                # Register for cleanup
                _engines.append(self)
                return
        except requests.ConnectionError:
            pass

        # 2. Если системного нет, поднимаем свой (с временной БД)
        logger.info("Системный TorrServer не найден. Поднимаем изолированный инстанс...")
        self.port = get_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.db_dir = tempfile.mkdtemp(prefix=f"ts_db_{self.port}_")
        # Register for cleanup
        _engines.append(self)

    def start(self):
        if self.use_system:
            return True # Системный сервер уже запущен

        logger.info(f"Запуск TorrServer на порту {self.port}...")
        ensure_dir("logs") # <--- ADD THIS
        log_file = f"logs/torrserver_debug_{self.port}.log" # <--- NEW
        self.log_file_handle = open(log_file, "w", encoding="utf-8")
        
        cmd = [self.binary_path, "-p", str(self.port), "-d", self.db_dir]
        self.process = subprocess.Popen(cmd, stdout=self.log_file_handle, stderr=subprocess.STDOUT)

        for _ in range(30):
            if self.process.poll() is not None:
                raise RuntimeError("TorrServer моментально завершил работу.")
            try:
                if requests.get(f"{self.base_url}/echo", timeout=1).status_code == 200:
                    return True
            except requests.ConnectionError:
                time.sleep(0.5)
        
        raise RuntimeError("Таймаут запуска TorrServer")

    def stop(self):
        if self.use_system:
            return # Мы не имеем права убивать системный сервер пользователя
            
        if self.process:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired): self.process.kill()
            self.process = None
            
        if hasattr(self, 'log_file_handle') and self.log_file_handle and not self.log_file_handle.closed:
            self.log_file_handle.close()

        if self.db_dir:
            shutil.rmtree(self.db_dir, ignore_errors=True)

    def __del__(self):
        """Safety net: cleanup if stop() was not called."""
        if self.db_dir and os.path.exists(self.db_dir):
            try: shutil.rmtree(self.db_dir, ignore_errors=True)
            except Exception: pass

    def add_torrent(self, torrent_path):
        logger.info("Загрузка торрента в сервер...")
        with open(torrent_path, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/torrent/upload", 
                files={'file': f},
                timeout=10
            )
            resp.raise_for_status()
            
            try: 
                t_hash = resp.json().get("hash", "")
            except (json.JSONDecodeError, KeyError, IndexError): 
                text = resp.text
                t_hash = text.split("btih:")[1].split("&")[0] if "btih:" in text else text.strip('" \n')
            
            t_hash = t_hash.lower()
            logger.info(f"✅ Торрент в базе! Hash: {t_hash}")
            return t_hash

    def remove_torrent(self, torrent_hash):
        try:
            requests.post(f"{self.base_url}/torrents", json={"action": "rem", "hash": torrent_hash}, timeout=2)
            logger.info("🧹 Торрент удален из базы сервера.")
        except (requests.RequestException, KeyError):
            pass

    def find_file_index(self, torrent_path, target_season, target_episode):
        """
        УМНЫЙ ЛОКАЛЬНЫЙ ПАРСИНГ .TORRENT ФАЙЛА
        Защищает от скачивания случайных серий.
        """
        if target_episode == 0:
            logger.info("🎬 Режим: ФИЛЬМ. Используем индекс по умолчанию (1).")
            return 1

        logger.info(f"📺 Режим: СЕРИАЛ. Ищем Сезон {target_season}, Эпизод {target_episode}...")
        logger.info("⚡ Локальный парсинг .torrent файла (мгновенно)...")

        if not bencodepy:
            logger.error("❌ Библиотека bencodepy не установлена! Без неё точный поиск серий невозможен.")
            raise ValueError("Требуется bencodepy")

        try:
            with open(torrent_path, 'rb') as f:
                torrent_data = bencodepy.decode(f.read())
            
            info = torrent_data.get(b'info', {})
            
            if b'files' not in info:
                logger.warning("Торрент состоит только из одного файла. Берем индекс 1.")
                return 1

            v_files = []
            # Индексы файлов в TorrServer строго соответствуют порядку в .torrent файле (начиная с 1)
            for idx, file_info in enumerate(info[b'files'], start=1):
                length = file_info.get(b'length', 0)
                path_list = file_info.get(b'path', [])
                if not path_list: continue
                
                full_path = "/".join([p.decode('utf-8', 'ignore') for p in path_list])
                
                # Видеофайлы > 50 MB
                if full_path.lower().endswith(('.mkv', '.mp4', '.avi', '.ts', '.m4v')) and length > 50 * 1024 * 1024:
                    v_files.append({'id': idx, 'path': full_path, 'length': length})

            logger.info(f"📂 Внутри раздачи найдено {len(v_files)} видеофайлов.")

            s_pad, e_pad = f"{target_season:02d}", f"{target_episode:02d}"
            
            # --- ПЛАН А: Точный поиск (S06E20) ---
            patterns = [
                rf"s{s_pad}e{e_pad}", rf"s{target_season}e{e_pad}", 
                rf"{s_pad}x{e_pad}", rf"{target_season}x{e_pad}",
                rf"сезон {target_season}.*серия {target_episode}"
            ]
            
            for vf in v_files:
                if any(re.search(p, vf['path'].lower()) for p in patterns):
                    logger.info(f"🎯 ТОЧНОЕ СОВПАДЕНИЕ! Выбран файл: {vf['path']} (ID для сервера: {vf['id']})")
                    return vf['id']

            # --- ПЛАН Б: Поиск по папке сезона ---
            logger.warning("⚠️ Формат SxxExx не найден. Ищем по папке сезона...")
            season_files = []
            s_folders = [rf"s{s_pad}", rf"season\s*{target_season}", rf"сезон\s*{target_season}"]
            
            for vf in v_files:
                if any(re.search(p, vf['path'].lower()) for p in s_folders):
                    season_files.append(vf)
            
            if season_files:
                season_files.sort(key=lambda x: x['path'])
                if 0 < target_episode <= len(season_files):
                    chosen = season_files[target_episode - 1]
                    logger.info(f"✅ Найдена папка сезона. Взят файл №{target_episode}: {chosen['path']} (ID: {chosen['id']})")
                    return chosen['id']

            # --- ПЛАН В: ОШИБКА (защита от скачивания мусора) ---
            logger.error("❌ НЕ УДАЛОСЬ УВЕРЕННО ОПРЕДЕЛИТЬ СЕРИЮ!")
            logger.error("Скрипт отказывается качать случайный файл.")
            logger.error("Первые 10 файлов раздачи для отладки:")
            for vf in v_files[:10]:
                logger.error(f"  - {vf['path']}")
                
            raise ValueError(f"Серия S{s_pad}E{e_pad} не найдена в торренте.")

        except Exception as e:
            logger.error(f"Ошибка парсинга .torrent файла: {e}")
            raise

    def download_clip(self, torrent_hash, file_index, start_time, duration_secs, output_path):
        # Формат ссылки как в плейлисте m3u, video.mkv - просто заглушка, сервер ее игнорирует
        stream_url = f"{self.base_url}/stream/video.mkv?link={torrent_hash}&index={file_index}&play"
        
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
            "-ss", start_time, 
            "-i", stream_url, 
            "-t", str(duration_secs),
            "-map", "0:v:0", "-map", "0:a:0?", 
            "-c:v", "libx264", "-preset", "fast", 
            "-c:a", "aac", 
            output_path
        ]
        
        logger.info(f"Запуск FFmpeg: HTTP Stream -> Fast Seek ({start_time})")
        logger.info(f"Стрим URL: {stream_url}")
        
        try:
            # Щедрый таймаут, так как FFmpeg "разбудит" сервер и заставит его искать пиров
            result = subprocess.run(ffmpeg_cmd, timeout=CONFIG["clip"]["ffmpeg_timeout"])
            
            if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                return True
            else:
                logger.error("FFmpeg не смог скачать/обрезать файл (ошибка или пустой файл).")
                return False
        except subprocess.TimeoutExpired:
            logger.error("Таймаут скачивания в FFmpeg.")
            return False


def do_torrent_download(
    topic_id, torrent_title, start_time, duration_secs,
    output_path, target_season, target_episode, session, active_domain
):
    ensure_dir("temp") # <--- ADD THIS
    torrent_path = f"temp/temp_{topic_id}_{os.getpid()}.torrent" # <--- NEW
    engine = None
    t_hash = None

    try:
        logger.info(f"СКАЧИВАНИЕ РАЗДАЧИ: {torrent_title[:80]}")

        # 1. Скачиваем .torrent файл
        resp = session.get(f"https://{active_domain}/forum/dl.php?t={topic_id}", timeout=15)
        if resp.status_code != 200:
            logger.error("Ошибка скачивания .torrent файла")
            return False
            
        # Проверка на то, что скачалась не HTML страница (слетела авторизация)
        header = resp.content[:10]
        if not header.startswith(b'd8:') and not header.startswith(b'd4:') and not header.startswith(b'd13:'):
            logger.error("❌ СКАЧАННЫЙ ФАЙЛ НЕ ЯВЛЯЕТСЯ ТОРРЕНТОМ! (Слетела авторизация RuTracker)")
            logger.error("РЕШЕНИЕ: Удалите файл rutracker_cookies.json")
            return False

        with open(torrent_path, "wb") as f:
            f.write(resp.content)

        # 2. Поднимаем/подключаем TorrServer
        ts_binary = CONFIG["torrserver"]["binary_path"]
        engine = TorrServerEngine(binary_path=ts_binary)
        engine.start()

        # 3. Находим индекс файла ЛОКАЛЬНО (моментально)
        file_idx = engine.find_file_index(torrent_path, target_season, target_episode)

        # 4. Добавляем торрент в сервер
        t_hash = engine.add_torrent(torrent_path)

        # 5. Режем клип через FFmpeg + HTTP Streaming
        return engine.download_clip(t_hash, file_idx, start_time, duration_secs, output_path)

    except Exception as e:
        logger.error(f"Ошибка торрент-загрузки: {e}")
        return False

    finally:
        # Уборка
        if engine:
            if t_hash:
                engine.remove_torrent(t_hash)
            engine.stop()
        if os.path.exists(torrent_path):
            try: os.remove(torrent_path)
            except OSError: pass


def try_torrent(query, start_time, duration_secs, output_path, target_season, target_episode, is_tv):
    logger.info(f"[TORRENT] Поиск: '{query}'")

    session, active_domain = ensure_rutracker_session()
    if not session:
        return False

    try:
        search_url = f"https://{active_domain}/forum/tracker.php?nm={urllib.parse.quote(query.encode('windows-1251'))}&o=10&s=2"
        resp = session.get(search_url, timeout=15)
        soup = BeautifulSoup(resp.content.decode("windows-1251", errors="ignore"), "html.parser")
    except requests.RequestException as e:
        logger.error(f"Ошибка поиска: {e}")
        return False

    valid_torrents = []
    for row in soup.select("tr.hl-tr"):
        title_tag = row.select_one("a.tLink")
        if not title_tag: continue
        title = title_tag.text.strip()

        try: seeds = int(row.select_one(".seedmed").text.strip())
        except (AttributeError, ValueError, TypeError): seeds = 0

        try: size_gb = int(row.select_one(".tor-size")["data-ts_text"]) / (1024 ** 3)
        except (AttributeError, ValueError, TypeError, KeyError): size_gb = 0

        if seeds >= CONFIG["search"]["min_seeds"]:
            score = evaluate_torrent(title, seeds, size_gb, is_tv)
            if score > 0:
                try: tid = title_tag["href"].split("t=")[1].split("&")[0]
                except (IndexError, KeyError, ValueError): continue
                valid_torrents.append({
                    "topic_id": tid, "title": title, "score": score,
                    "seeds": seeds, "size_gb": round(size_gb, 1)
                })

    if not valid_torrents:
        logger.info("  Подходящие торренты не найдены")
        return False

    valid_torrents.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"  Найдено подходящих: {len(valid_torrents)}. Топ-3:")
    
    for idx, t in enumerate(valid_torrents[:3]):
        logger.info(f"    {idx+1}. {t['title'][:60]}... (Сидов: {t['seeds']}, {t['size_gb']} GB)")

    for idx, t_info in enumerate(valid_torrents[:3]):
        logger.info(f"  ► Попытка {idx + 1}/3: {t_info['title'][:60]}...")
        if do_torrent_download(
            t_info["topic_id"], t_info["title"], start_time, duration_secs, 
            output_path, target_season, target_episode, session, active_domain
        ):
            return True

    return False


# =====================================================================
# 🚀 MAIN
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Скачивание фрагментов видео из YouTube, RuTube и торрентов (через TorrServer)")
    parser.add_argument("--title", required=True, help="Название фильма/сериала")
    parser.add_argument("--orig_title", default="", help="Оригинальное название")
    parser.add_argument("--year", default="0", help="Год выпуска")
    parser.add_argument("--type", default="movie", choices=["movie", "tv"], help="Тип: movie или tv")
    parser.add_argument("--season", default="0", help="Номер сезона")
    parser.add_argument("--episode", default="0", help="Номер эпизода")
    parser.add_argument("--start", required=True, help="Время начала (HH:MM:SS)")
    parser.add_argument("--duration", required=True, type=int, help="Длительность (сек)")
    parser.add_argument("--source", default="all", choices=["all", "youtube", "rutube", "torrent"], help="Источник")
    parser.add_argument("--force_source", default="", help="Принудительный источник (type:id)")
    parser.add_argument("--output", default="", help="Путь к выходному файлу")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("🎬 AI-Режиссер Монтажа — Загрузчик клипов (TorrServer Edition)")
    logger.info("=" * 60)

    is_tv = args.type == "tv"
    target_ep = int(args.episode) if is_tv else 0
    target_season = int(args.season) if is_tv else 0

    sources = [args.source] if args.source != "all" else ["youtube", "rutube", "torrent"]

    if not check_all_dependencies(sources):
        sys.exit(1)

    ensure_dir(CONFIG["clip"]["output_folder"])

    if args.output:
        output_file = args.output
        try: validate_path_within_dir(output_file, os.path.dirname(output_file) or CONFIG["clip"]["output_folder"])
        except ValueError as e:
            logger.error(f"Ошибка безопасности: {e}")
            sys.exit(1)
    else:
        name_part = f"{args.title}_S{target_season:02d}E{target_ep:02d}" if is_tv and target_season > 0 else (f"{args.title}_{args.year}" if int(args.year) > 0 else args.title)
        output_file = os.path.join(CONFIG["clip"]["output_folder"], f"{sanitize_filename(name_part)}_clip.mp4")

    # --- Принудительный источник ---
    if args.force_source:
        logger.info(f"Принудительный источник: {args.force_source}")
        parts = args.force_source.split(":", 1)
        if len(parts) == 2:
            s_type, s_id = parts
            if s_type in ("youtube", "rutube"):
                if do_stream_download(s_id, args.start, args.duration, output_file, s_type):
                    sys.exit(0)
            elif s_type == "torrent":
                session, active_domain = ensure_rutracker_session()
                if session and do_torrent_download(s_id, "Привязанный торрент", args.start, args.duration, output_file, target_season, target_ep, session, active_domain):
                    sys.exit(0)

    # --- Глобальный поиск ---
    CONFIG["search"]["priority"] = sources
    queries = generate_search_queries(args.title, args.orig_title, args.year, args.type, args.season, args.episode)

    success = False
    for source in sources:
        if success: break
        logger.info(f"\n{'='*40}\nИсточник: {source.upper()}\n{'='*40}")

        for query in queries:
            if source == "youtube": success = try_youtube(query, args.start, args.duration, output_file)
            elif source == "rutube": success = try_rutube(query, args.start, args.duration, output_file)
            elif source == "torrent": success = try_torrent(query, args.start, args.duration, output_file, target_season, target_ep, is_tv)
            if success: break

    # --- Результат ---
    logger.info("\n" + "=" * 60)
    if success and os.path.exists(output_file):
        logger.info(f"✅ УСПЕХ! Клип сохранен: {output_file} ({os.path.getsize(output_file) / 1024:.0f} KB)")
        sys.exit(0)
    else:
        logger.error("❌ ОШИБКА: Не удалось скачать клип ни из одного источника.")
        sys.exit(1)

if __name__ == "__main__":
    main()