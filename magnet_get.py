import argparse
import requests
from bs4 import BeautifulSoup
import urllib.parse
import pickle
import os
import sys
import subprocess
import time
import urllib.request
import socket
import json

try:
    import bencode
except ImportError:
    try:
        import bencodepy as bencode
    except ImportError:
        bencode = None

# --- НАСТРОЙКИ ---
CONFIG = {
    "rutracker": {
        "username": "mkiisklaa",
        "password": "'ffaCt!$M972sQU",
        "cookie_file": "rutracker_cookies.pkl",
    },
    "search": {
        "priority": ["torrent", "youtube"],
        "min_seeds": 5,
        "max_size_gb": 35.0,
    },
    "scoring": {
        "res_1080p": 1000,
        "res_720p": 300,
        "source_web": 400,
    },
    "clip": {
        "output_folder": "clips",
    },
    "trackers": [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://9.rarbg.com:2810/announce",
        "udp://tracker.openbittorrent.com:80/announce",
        "http://tracker.openbittorrent.com:80/announce",
        "udp://exodus.desync.com:6969/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://open.demonii.com:1337/announce"
    ],
    "domains": ["rutracker.org", "rutracker.net", "rutracker.nl"],
}

# =====================================================================
# 🛠 УТИЛИТЫ
# =====================================================================
def time_to_seconds(time_str):
    try:
        parts = time_str.split(":")
        if len(parts) == 3:
            h, m, s = map(float, parts)
            return int(h * 3600 + m * 60 + s)
        elif len(parts) == 2:
            m, s = map(float, parts)
            return int(m * 60 + s)
        return int(float(parts[0]))
    except (ValueError, IndexError):
        return 0

def seconds_to_time(seconds):
    try:
        s = int(seconds)
        return f"{s // 3600:02}:{s % 3600 // 60:02}:{s % 60:02}"
    except (ValueError, TypeError):
        return "00:00:00"

def get_free_port():
    # 100% безопасный и параллельный способ получения свободного порта от ОС
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def check_dependency(cmd_name):
    try:
        cmd = "where" if sys.platform == "win32" else "which"
        result = subprocess.run([cmd, cmd_name], capture_output=True, text=True)
        return result.returncode == 0
    except Exception: return False

def check_all_dependencies(sources):
    missing = []
    if not check_dependency("ffmpeg"): missing.append("ffmpeg")
    if "torrent" in sources and not check_dependency("peerflix"): missing.append("peerflix (npm install -g peerflix)")
    if "youtube" in sources and not check_dependency("yt-dlp"): missing.append("yt-dlp (pip install yt-dlp)")
    if missing:
        print(f"❌ Не установлены: {', '.join(missing)}")
        return False
    return True

# =====================================================================
# 🔑 АВТОРИЗАЦИЯ RUTRACKER
# =====================================================================
def load_cookies(session):
    c_file = CONFIG["rutracker"]["cookie_file"]
    if os.path.exists(c_file):
        try:
            with open(c_file, "rb") as f: session.cookies.update(pickle.load(f))
            return True
        except: return False
    return False

def save_cookies(session):
    try:
        with open(CONFIG["rutracker"]["cookie_file"], "wb") as f: pickle.dump(session.cookies, f)
    except: pass

def find_working_domain(session):
    for domain in CONFIG["domains"]:
        try:
            r = session.get(f"https://{domain}/forum/index.php", timeout=8, allow_redirects=True)
            if r.status_code == 200: return domain
        except: continue
    return None

def do_login(session, domain):
    try:
        session.post(f"https://{domain}/forum/login.php", data={"login_username": CONFIG["rutracker"]["username"], "login_password": CONFIG["rutracker"]["password"], "login": "Вход"}, timeout=10)
        if "bb_session" in session.cookies.get_dict():
            save_cookies(session)
            return True
    except: pass
    return False

def ensure_rutracker_session():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    load_cookies(session)

    domain = find_working_domain(session)
    if not domain: return None, None

    try:
        r = session.get(f"https://{domain}/forum/privmsg.php?folder=inbox", timeout=8, allow_redirects=True)
        if r.status_code == 200 and "login" not in r.url.lower(): return session, domain
    except: pass

    if do_login(session, domain): return session, domain
    return None, None

# =====================================================================
# 🔍 ПОИСКОВЫЕ ЗАПРОСЫ
# =====================================================================
def generate_search_queries(title_ru, title_orig, year, m_type, season, episode):
    queries, y = [], int(year)
    if m_type == "movie" or int(season) == 0:
        if y > 0: queries.append(f"{title_ru} {y}")
        if title_orig and title_orig.lower() != title_ru.lower():
            if y > 0: queries.append(f"{title_orig} {y}")
        queries.append(title_ru)
    else:
        s, e = int(season), int(episode)
        queries.append(f"{title_ru} {s} сезон")
        if title_orig and title_orig.lower() != title_ru.lower(): queries.append(f"{title_orig} S{s:02d}")
        queries.append(f"{title_ru} S{s:02d}E{e:02d}")
    seen = set()
    return [q for q in queries if not (q in seen or seen.add(q))]

# =====================================================================
# 🗂 ТОРРЕНТ-ФАЙЛЫ
# =====================================================================
def get_bencode_val(d, key):
    if isinstance(d, dict):
        if key in d: return d[key]
        if isinstance(key, str): return d.get(key.encode("utf-8"))
    return None

def find_episode_index(torrent_path, target_episode):
    if not bencode: return 0
    try:
        with open(torrent_path, "rb") as f: raw = f.read()
        if not raw or raw[0:1] != b"d": return 0
        info = get_bencode_val(bencode.decode(raw), "info")
        if not info or not get_bencode_val(info, "files"): return 0
        
        video_files = []
        for idx, f_dict in enumerate(get_bencode_val(info, "files")):
            path_list = get_bencode_val(f_dict, "path")
            if not path_list: continue
            full_name = "/".join((p.decode("utf-8", "ignore") if isinstance(p, bytes) else str(p)) for p in path_list).lower()
            if full_name.endswith((".mkv", ".mp4", ".avi", ".ts", ".m4v")): video_files.append((idx, full_name))
        
        video_files.sort(key=lambda x: x[1])
        if 0 < target_episode <= len(video_files): return video_files[target_episode - 1][0]
        elif video_files: return video_files[0][0]
        return 0
    except: return 0

# =====================================================================
# ✂️ FFMPEG
# =====================================================================
def run_clean_ffmpeg(stream_url, start_time, duration_secs, output_path):
    duration_str = seconds_to_time(duration_secs)
    timeout = 45 + duration_secs 

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-v", "error", "-stats",
        "-i", stream_url,
        "-ss", start_time,
        "-t", duration_str,
        "-map", "0:v:0", "-map", "0:a:0?", "-sn",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac",
        output_path,
    ]

    print("   🚀 Режем видео... ", end="", flush=True)
    try:
        process = subprocess.run(ffmpeg_cmd, timeout=timeout, capture_output=True, text=True)
        if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            print("Готово!")
            return True
        else:
            print("Ошибка нарезки (Мертвый пир?).")
            return False
    except subprocess.TimeoutExpired:
        print(f"Таймаут ffmpeg ({timeout}с). Торрент слишком медленный!")
        return False
    except FileNotFoundError: return False

# =====================================================================
# 🔴 YOUTUBE
# =====================================================================
def do_youtube_download(video_id, start_time, duration_secs, output_path):
    end_time = seconds_to_time(time_to_seconds(start_time) + duration_secs)
    print("   🚀 Скачивание фрагмента с YouTube...")
    download_cmd = [
        "yt-dlp", "--quiet", "--progress",
        "--download-sections", f"*{start_time}-{end_time}",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--force-keyframes-at-cuts", "-o", output_path,
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        if subprocess.run(download_cmd, timeout=120).returncode == 0 and os.path.exists(output_path):
            print(f"###SOURCE_FOUND###:youtube:{video_id}")
            return True
    except: pass
    return False

def try_youtube(query, start_time, duration_secs, output_path):
    print(f"\n🔴 [YOUTUBE] Поиск: '{query}'")
    req_dur = time_to_seconds(start_time) + duration_secs
    try:
        res = subprocess.run(["yt-dlp", f"ytsearch5:{query} полный фильм", "--dump-json", "--no-warnings"], capture_output=True, text=True, timeout=15)
        if res.returncode != 0: return False
        videos = [json.loads(line) for line in res.stdout.strip().split("\n") if line.strip()]
    except: return False

    for v in videos:
        if v.get("duration", 0) > req_dur:
            return do_youtube_download(v.get("id"), start_time, duration_secs, output_path)
    print("   Подходящее видео не найдено")
    return False

# =====================================================================
# 🔵 TORRENT (FAIL-FAST)
# =====================================================================
def evaluate_torrent(title, seeds, size_gb, is_tv):
    t = title.lower()
    if any(bad in t for bad in ["director's cut", "режиссерская", "extended", "расширенная", "unrated", "4k", "2160p", "remux"]): return -100000
    if not is_tv and size_gb > 20.0: return -50000
    
    score = seeds * 1000
    if "1080p" in t: score += CONFIG["scoring"]["res_1080p"]
    elif "720p" in t: score += CONFIG["scoring"]["res_720p"]
    if "web-dl" in t or "webrip" in t: score += CONFIG["scoring"]["source_web"]
    score -= int(size_gb * 10)
    return score

def do_torrent_download(topic_id, start_time, duration_secs, output_path, target_episode, session, active_domain):
    # УНИКАЛЬНОЕ ИМЯ ТОРРЕНТ ФАЙЛА (Защита от перезаписи соседями)
    torrent_path = f"temp_{topic_id}_{os.getpid()}.torrent"
    peerflix_process = None

    try:
        print(f"   📥 Торрент {topic_id}...")
        try:
            resp = session.get(f"https://{active_domain}/forum/dl.php?t={topic_id}", timeout=10)
            with open(torrent_path, "wb") as f: f.write(resp.content)
        except: return False

        if os.path.getsize(torrent_path) < 100: return False

        file_index = find_episode_index(torrent_path, target_episode) if target_episode > 0 else 0
        port = get_free_port()
        stream_url = f"http://127.0.0.1:{port}/"

        peerflix_cmd = ["peerflix", torrent_path, "--index", str(file_index), "--port", str(port), "--connections", "100", "--quiet"]
        for tr in CONFIG["trackers"]: peerflix_cmd.extend(["--tracker", tr])

        peerflix_process = subprocess.Popen(peerflix_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        print("   ⏳ Поиск пиров (макс 25 сек)... ", end="", flush=True)
        server_ready = False
        
        for _ in range(25): 
            if peerflix_process.poll() is not None: break
            try:
                urllib.request.urlopen(urllib.request.Request(stream_url, method="HEAD"), timeout=1)
                server_ready = True
                print("Связь установлена!")
                break
            except: time.sleep(1)

        if not server_ready:
            print("Нет живых сидов. Пропускаем!")
            return False

        time.sleep(2) 

        if run_clean_ffmpeg(stream_url, start_time, duration_secs, output_path):
            print(f"###SOURCE_FOUND###:torrent:{topic_id}")
            return True
        return False

    finally:
        if peerflix_process:
            try: peerflix_process.kill()
            except: pass
        if os.path.exists(torrent_path):
            try: os.remove(torrent_path)
            except: pass

def try_torrent(query, start_time, duration_secs, output_path, target_episode, is_tv):
    print(f"\n🔵 [TORRENT] Поиск: '{query}'")
    session, active_domain = ensure_rutracker_session()
    if not session: return False

    try:
        search_url = f"https://{active_domain}/forum/tracker.php?nm={urllib.parse.quote(query.encode('windows-1251'))}&o=10&s=2"
        soup = BeautifulSoup(session.get(search_url, timeout=10).content.decode("windows-1251", errors="ignore"), "html.parser")
    except: return False

    valid_torrents = []
    for row in soup.select("tr.hl-tr"):
        title_tag = row.select_one("a.tLink")
        if not title_tag: continue
        title = title_tag.text.strip()
        try: seeds = int(row.select_one(".seedmed").text.strip())
        except: seeds = 0
        try: size_gb = int(row.select_one(".tor-size")["data-ts_text"]) / (1024 ** 3)
        except: size_gb = 0

        if seeds >= CONFIG["search"]["min_seeds"]:
            score = evaluate_torrent(title, seeds, size_gb, is_tv)
            if score > 0:
                valid_torrents.append({
                    "topic_id": title_tag["href"].split("t=")[1].split("&")[0],
                    "title": title, "score": score, "seeds": seeds, "size_gb": round(size_gb, 1)
                })

    if not valid_torrents: return False
    valid_torrents.sort(key=lambda x: x["score"], reverse=True)

    for idx, t_info in enumerate(valid_torrents[:3]):
        print(f"   ► Попытка {idx + 1}/3: {t_info['title'][:50]}... (Сидов: {t_info['seeds']})")
        if do_torrent_download(t_info["topic_id"], start_time, duration_secs, output_path, target_episode, session, active_domain):
            return True

    return False

# =====================================================================
# 🚀 MAIN
# =====================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--orig_title", default="")
    parser.add_argument("--year", default="0")
    parser.add_argument("--type", default="movie")
    parser.add_argument("--season", default="0")
    parser.add_argument("--episode", default="0")
    parser.add_argument("--start", required=True)
    parser.add_argument("--duration", required=True, type=int)
    parser.add_argument("--source", default="all")
    parser.add_argument("--force_source", default="")
    parser.add_argument("--output", default="") # НОВЫЙ АРГУМЕНТ ДЛЯ ПАРАЛЛЕЛЬНОСТИ
    args = parser.parse_args()

    is_tv = args.type == "tv"
    target_ep = int(args.episode) if is_tv else 0
    sources = ["torrent"] if args.source == "torrent" else ["youtube"] if args.source == "youtube" else (["torrent", "youtube"] if is_tv else ["youtube", "torrent"])

    if not check_all_dependencies(sources): sys.exit(1)
    ensure_dir(CONFIG["clip"]["output_folder"])

    # Если app.py передал четкий путь, используем его, иначе генерируем стандартный
    if args.output:
        output_file = args.output
    else:
        safe_name = f"{args.title}_S{int(args.season):02d}E{target_ep:02d}" if is_tv and int(args.season) > 0 else (f"{args.title}_{args.year}" if int(args.year) > 0 else args.title)
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in " _-").strip().replace(" ", "_")
        output_file = os.path.join(CONFIG["clip"]["output_folder"], f"{safe_name}_clip.mp4")

    print(f"🎬 Вырезаем {args.duration}с. начиная с {args.start}")

    if args.force_source:
        parts = args.force_source.split(":", 1)
        if len(parts) == 2:
            s_type, s_id = parts
            if s_type == "youtube" and do_youtube_download(s_id, args.start, args.duration, output_file): sys.exit(0)
            elif s_type == "torrent":
                session, active_domain = ensure_rutracker_session()
                if session and do_torrent_download(s_id, args.start, args.duration, output_file, target_ep, session, active_domain): sys.exit(0)
            print("⚠️ Привязанный источник мертв. Запускаем глобальный поиск!")

    CONFIG["search"]["priority"] = sources
    queries = generate_search_queries(args.title, args.orig_title, args.year, args.type, args.season, args.episode)

    success = False
    for source in CONFIG["search"]["priority"]:
        if success: break
        for query in queries:
            success = try_youtube(query, args.start, args.duration, output_file) if source == "youtube" else try_torrent(query, args.start, args.duration, output_file, target_ep, is_tv)
            if success: break

    if success:
        print(f"\n✅ Клип сохранен: {output_file}")
        sys.exit(0)
    else:
        print("\n❌ Не удалось скачать клип ни из одного источника.")
        sys.exit(1)

if __name__ == "__main__":
    main()