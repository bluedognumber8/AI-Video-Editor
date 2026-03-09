import argparse
import requests
from bs4 import BeautifulSoup
import urllib.parse
import pickle
import os
import subprocess
import time
import urllib.request
import socket
import json

# =====================================================================
# ⚙️ НАСТРОЙКИ (КОНФИГУРАЦИЯ)
# =====================================================================
CONFIG = {
    "rutracker": {
        "username": "mkiisklaa",
        "password": "'ffaCt!$M972sQU",
        "cookie_file": "rutracker_cookies.pkl"
    },
    "search": {
        "priority": ["youtube", "torrent"], 
        "min_seeds": 5,              
        "max_size_gb": 25.0           
    },
    "scoring": {
        "res_1080p": 1000,
        "res_720p": 300,
        "source_bdrip_bluray": 500,
        "source_web": 400,
        "penalty_4k": -2000,          
        "penalty_huge_size": -1500    
    },
    "clip": {
        "output_folder": "clips"      
    },
    "trackers": [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.demonii.com:1337/announce",
        "udp://open.tracker.cl:1337/announce"
    ]
}

# =====================================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================

def time_to_seconds(time_str):
    h, m, s = map(int, time_str.split(':'))
    return h * 3600 + m * 60 + s

def seconds_to_time(seconds):
    return f"{int(seconds)//3600:02}:{int((seconds)%3600)//60:02}:{int(seconds)%60:02}"

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def generate_search_queries(title, m_type, season, episode):
    """Умная генерация поисковых фраз (Особенно важно для сериалов)"""
    if m_type == 'movie' or int(season) == 0:
        return [title, f"{title} полный фильм"]
    
    s_num, e_num = int(season), int(episode)
    return [
        f"{title} S{s_num:02d}E{e_num:02d}",      # Priority 1: Web-DL/релизы по сериям
        f"{title} {s_num} сезон {e_num} серия",  # Priority 2: Русское название серии
        f"{title} сезон {s_num}",                # Priority 3: Весь сезон целиком
    ]

# =====================================================================
# 🔴 МОДУЛЬ YOUTUBE
# =====================================================================

def try_youtube(query, start_time, duration_secs, output_path):
    print(f"🔴 [YOUTUBE] Ищем: '{query}'...")
    start_sec = time_to_seconds(start_time)
    required_min_duration = start_sec + duration_secs

    search_cmd = [
        "yt-dlp", f"ytsearch5:{query} полный фильм", "--dump-json", "--no-warnings"
    ]
    
    try:
        result = subprocess.run(search_cmd, capture_output=True, text=True, check=True)
        videos = [json.loads(line) for line in result.stdout.strip().split('\n') if line]
    except Exception as e:
        print(f"⚠️ ОШИБКА поиска YouTube: {e}")
        return False

    best_video_id, best_video_title = None, None
    for v in videos:
        v_duration = v.get("duration", 0)
        if v_duration > required_min_duration:
            best_video_id = v.get("id")
            best_video_title = v.get("title")
            print(f"✅ Найдено видео: {best_video_title[:60]}... ({v_duration//60} мин)")
            break

    if not best_video_id:
        print("❌ На YouTube нет видео нужной длины.")
        return False

    end_time = seconds_to_time(required_min_duration)
    print(f"🚀 Вырезаем фрагмент прямо с серверов YouTube...")
    
    download_cmd = [
        "yt-dlp",
        "--download-sections", f"*{start_time}-{end_time}",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--force-keyframes-at-cuts",
        "-o", output_path,
        f"https://www.youtube.com/watch?v={best_video_id}"
    ]
    dl_result = subprocess.run(download_cmd)
    
    return dl_result.returncode == 0 and os.path.exists(output_path)

# =====================================================================
# 🔵 МОДУЛЬ TORRENT / RUTRACKER
# =====================================================================

def evaluate_torrent(title, seeds, size_gb):
    score = seeds  
    title_lower = title.lower()
    sc = CONFIG["scoring"]
    
    if "1080p" in title_lower: score += sc["res_1080p"]
    elif "720p" in title_lower: score += sc["res_720p"]
    if "bdrip" in title_lower or "blu-ray" in title_lower: score += sc["source_bdrip_bluray"]
    elif "web-dl" in title_lower or "webrip" in title_lower: score += sc["source_web"]
    if "4k" in title_lower or "2160p" in title_lower: score += sc["penalty_4k"]
    if size_gb > CONFIG["search"]["max_size_gb"]: score += sc["penalty_huge_size"]

    return score

def get_magnet(query):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    c_file = CONFIG["rutracker"]["cookie_file"]

    if os.path.exists(c_file):
        with open(c_file, 'rb') as f: session.cookies.update(pickle.load(f))

    active_domain, is_logged_in = None, False
    for domain in ["rutracker.org", "rutracker.net"]:
        try:
            resp = session.get(f"https://{domain}/forum/privmsg.php?folder=inbox", allow_redirects=False, timeout=5)
            if resp.status_code == 200: active_domain = domain; is_logged_in = True; break
            elif resp.status_code in [301, 302]: active_domain = domain; break
        except: pass

    if not active_domain:
        print("❌ Не удалось подключиться к RuTracker.")
        return None

    if not is_logged_in:
        print("🔐 Входим в аккаунт...")
        session.post(f"https://{active_domain}/forum/login.php", data={
            "login_username": CONFIG["rutracker"]["username"], 
            "login_password": CONFIG["rutracker"]["password"], 
            "login": "Вход"
        }, timeout=15)
        if not any(c.name == 'bb_session' for c in session.cookies):
            print("❌ Неверный логин или пароль."); return None
        with open(c_file, 'wb') as f: pickle.dump(session.cookies, f)

    search_url = f"https://{active_domain}/forum/tracker.php?nm={urllib.parse.quote(query.encode('windows-1251'))}&o=10&s=2"
    soup = BeautifulSoup(session.get(search_url, timeout=15).content.decode('windows-1251'), 'html.parser')
    valid_torrents = []

    for row in soup.select("tr.hl-tr"):
        title_tag = row.select_one("a.tLink")
        seed_tag, size_tag = row.select_one(".seedmed"), row.select_one(".tor-size")
        
        if title_tag:
            title = title_tag.text.strip()
            try: seeds = int(seed_tag.text.strip()) if seed_tag else 0
            except: seeds = 0
            
            size_gb = 0
            if size_tag and size_tag.has_attr('data-ts_text'):
                try: size_gb = int(size_tag['data-ts_text']) / (1024**3)
                except: pass

            if seeds >= CONFIG["search"]["min_seeds"]:
                score = evaluate_torrent(title, seeds, size_gb)
                valid_torrents.append({"title": title, "href": title_tag.get('href'), "seeds": seeds, "size_gb": size_gb, "score": score})

    if not valid_torrents:
        print(f"❌ Нет раздач (или сидов меньше {CONFIG['search']['min_seeds']}).")
        return None

    valid_torrents.sort(key=lambda x: x['score'], reverse=True)
    best = valid_torrents[0]
    print(f"✅ ВЫБРАН ТОРРЕНТ: {best['title'][:60]}... (Сидов: {best['seeds']})")

    topic_resp = session.get(f"https://{active_domain}/forum/{best['href']}", timeout=15).content.decode('windows-1251')
    magnet_tag = BeautifulSoup(topic_resp, 'html.parser').select_one("a.magnet-link")
    
    if not magnet_tag: return None
    
    magnet = magnet_tag.get("href")
    for tr in CONFIG["trackers"]: magnet += f"&tr={urllib.parse.quote(tr)}"
    return magnet

def try_torrent(query, start_time, duration_secs, output_path):
    print(f"🔵 [TORRENT] Ищем: '{query}' на RuTracker...")
    
    magnet = get_magnet(query)
    if not magnet: return False
    
    port = get_free_port()
    stream_url = f"http://127.0.0.1:{port}/"
    
    print("🚀 Запускаем Peerflix (Ожидание пиров до 90 сек)...")
    log_file = open("peerflix_debug.log", "w", encoding="utf-8")
    peerflix = subprocess.Popen(["peerflix", magnet, "--port", str(port)], stdout=log_file, stderr=subprocess.STDOUT)
    
    server_ready = False
    try:
        for _ in range(90):
            if peerflix.poll() is not None:
                print("❌ Peerflix упал. Смотри лог.")
                return False
            try:
                urllib.request.urlopen(urllib.request.Request(stream_url, method='HEAD'), timeout=2)
                server_ready = True; break
            except: time.sleep(1)
                
        if not server_ready:
            print("❌ Торрент не ответил (возможно нет сидов/пиров).")
            return False

        print("✅ Буферизация пошла! Вырезаем фрагмент через FFmpeg...")
        time.sleep(3) 
        
        duration_str = seconds_to_time(duration_secs)
        ffmpeg_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", 
            "-ss", start_time, "-i", stream_url, "-t", duration_str,
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-y", output_path
        ]
        result = subprocess.run(ffmpeg_cmd)
        return result.returncode == 0

    finally:
        peerflix.terminate()
        try: peerflix.wait(timeout=3)
        except: peerflix.kill()
        if not log_file.closed: log_file.close()

# =====================================================================
# 🚀 ГЛАВНЫЙ БЛОК УПРАВЛЕНИЯ
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Скачивание и обрезка видео")
    parser.add_argument("--title", required=True, help="Название фильма/сериала")
    parser.add_argument("--type", default="movie", help="Тип: movie или tv")
    parser.add_argument("--season", default="0", help="Номер сезона")
    parser.add_argument("--episode", default="0", help="Номер серии")
    parser.add_argument("--start", required=True, help="Таймкод (HH:MM:SS)")
    parser.add_argument("--duration", required=True, type=int, help="Длительность (сек)")
    args = parser.parse_args()

    ensure_dir(CONFIG["clip"]["output_folder"])
    
    # Красивое имя файла
    if args.type == 'tv' and int(args.season) > 0:
        safe_name = f"{args.title}_S{int(args.season):02d}E{int(args.episode):02d}"
    else:
        safe_name = args.title
        
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in " _-").strip().replace(" ", "_")
    output_file = os.path.join(CONFIG["clip"]["output_folder"], f"{safe_name}_clip.mp4")

    print(f"🎬 ЗАПРОС: {args.title} | Таймкод: {args.start} | Длина: {args.duration}s")

    queries = generate_search_queries(args.title, args.type, args.season, args.episode)
    success = False

    for source in CONFIG["search"]["priority"]:
        print(f"\n🔄 ИСТОЧНИК: [{source.upper()}]")
        for query in queries:
            if source == "youtube":
                success = try_youtube(query, args.start, args.duration, output_file)
            elif source == "torrent":
                success = try_torrent(query, args.start, args.duration, output_file)
            
            if success: break
        if success: break

    if success:
        print(f"\n🎉 ГОТОВО! Видео сохранено: {os.path.abspath(output_file)}\n")
    else:
        print("\n❌ ОШИБКА: Видео не найдено или не удалось скачать.\n")

if __name__ == "__main__":
    main()