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
import bencode 
from tqdm import tqdm

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
        "source_web": 400,
    },
    "clip": {
        "output_folder": "clips"      
    },
    "trackers": [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.demonii.com:1337/announce",
        "udp://tracker.torrent.eu.org:451/announce"
    ]
}

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
    if not os.path.exists(path): os.makedirs(path)

# --- ГЕНЕРАТОР УМНЫХ ЗАПРОСОВ ---
def generate_search_queries(title_ru, title_orig, year, m_type, season, episode):
    """
    Генерирует список запросов с фоллбэками. 
    Если первый не найдет торрент, скрипт попробует второй.
    """
    queries = []
    y = int(year)
    
    if m_type == 'movie' or int(season) == 0:
        # 1. Приоритет: Русское название + Год
        if y > 0: queries.append(f"{title_ru} {y}")
        
        # 2. Фоллбэк: Оригинальное название + Год
        if title_orig and title_orig.lower() != title_ru.lower():
            if y > 0: queries.append(f"{title_orig} {y}")
            
        # 3. Фоллбэк: Просто русское название (без года, если год указан криво)
        queries.append(title_ru)
        
    else:
        # ДЛЯ СЕРИАЛОВ
        s_num, e_num = int(season), int(episode)
        
        # 1. Русское название + сезон
        queries.append(f"{title_ru} {s_num} сезон")
        
        # 2. Оригинальное название + сезон (часто так релизят Web-DL)
        if title_orig and title_orig.lower() != title_ru.lower():
            queries.append(f"{title_orig} S{s_num:02d}")
            
        # 3. Точный поиск серии
        queries.append(f"{title_ru} S{s_num:02d}E{e_num:02d}")
        
    # Убираем дубликаты, сохраняя порядок
    seen = set()
    return [q for q in queries if not (q in seen or seen.add(q))]
# --------------------------------

def get_bencode_val(d, key):
    if isinstance(key, str):
        res = d.get(key)
        if res is not None: return res
        return d.get(key.encode('utf-8'))
    return d.get(key)

def find_episode_index(torrent_path, target_episode):
    try:
        with open(torrent_path, 'rb') as f:
            meta = bencode.decode(f.read())
        info = get_bencode_val(meta, 'info')
        if not info: return 0
        files = get_bencode_val(info, 'files')
        if not files: return 0
        
        video_files = []
        for idx, f_dict in enumerate(files):
            path_list = get_bencode_val(f_dict, 'path')
            if not path_list: continue
            
            full_name = "/".join(p.decode('utf-8', 'ignore') if isinstance(p, bytes) else p for p in path_list).lower()
            if full_name.endswith(('.mkv', '.mp4', '.avi')):
                video_files.append((idx, full_name))
        
        video_files.sort(key=lambda x: x[1])
        if 0 < target_episode <= len(video_files):
            return video_files[target_episode - 1][0]
        else:
            return video_files[0][0]
    except: return 0

# =====================================================================
# 🔴 МОДУЛЬ YOUTUBE
# =====================================================================
def try_youtube(query, start_time, duration_secs, output_path):
    print(f"\n🔴 [YOUTUBE] Ищем: '{query}'...")
    start_sec = time_to_seconds(start_time)
    required_min_duration = start_sec + duration_secs
    try:
        result = subprocess.run(["yt-dlp", f"ytsearch5:{query} полный фильм", "--dump-json", "--no-warnings"], capture_output=True, text=True, check=True)
        videos = [json.loads(line) for line in result.stdout.strip().split('\n') if line]
    except: return False

    best_video_id = None
    for v in videos:
        if v.get("duration", 0) > required_min_duration:
            best_video_id = v.get("id")
            break
    if not best_video_id: return False

    end_time = seconds_to_time(required_min_duration)
    print(f"🚀 Вырезаем фрагмент с YouTube...")
    dl_result = subprocess.run([
        "yt-dlp", "--quiet", "--progress", "--download-sections", f"*{start_time}-{end_time}",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--force-keyframes-at-cuts", "-o", output_path,
        f"https://www.youtube.com/watch?v={best_video_id}"
    ])
    return dl_result.returncode == 0 and os.path.exists(output_path)

# =====================================================================
# 🔵 МОДУЛЬ TORRENT 
# =====================================================================
def evaluate_torrent(title, seeds, size_gb, is_tv):
    title_lower = title.lower()
    bad_versions = ["director's cut", "directors cut", "режиссерская", "extended", "расширенная", "unrated", "uncut", "без цензуры", "special edition", "open matte"]
    if any(bad in title_lower for bad in bad_versions): return -100000
    if "4k" in title_lower or "2160p" in title_lower: return -50000
    if "remux" in title_lower or "bdremux" in title_lower: return -50000
    if not is_tv and size_gb > 20.0: return -50000
    if is_tv and size_gb > 40.0: return -50000

    score = seeds * 1000  
    sc = CONFIG["scoring"]
    if "1080p" in title_lower: score += sc["res_1080p"]
    elif "720p" in title_lower: score += sc["res_720p"]
    if "web-dl" in title_lower or "webrip" in title_lower: score += sc["source_web"]
    if "theatrical" in title_lower: score += 500
    score -= int(size_gb * 10)
    return score

def get_torrent_file_and_index(query, target_episode, is_tv):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    c_file = CONFIG["rutracker"]["cookie_file"]
    if os.path.exists(c_file):
        with open(c_file, 'rb') as f: session.cookies.update(pickle.load(f))

    active_domain = None
    for domain in ["rutracker.org", "rutracker.net"]:
        try:
            if session.get(f"https://{domain}/forum/privmsg.php?folder=inbox", timeout=5).status_code == 200:
                active_domain = domain; break
        except: pass
    if not active_domain: return None, 0

    if not any(c.name == 'bb_session' for c in session.cookies):
        session.post(f"https://{active_domain}/forum/login.php", data={"login_username": CONFIG["rutracker"]["username"], "login_password": CONFIG["rutracker"]["password"], "login": "Вход"}, timeout=15)
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
            try: size_gb = int(size_tag['data-ts_text']) / (1024**3) if size_tag else 0
            except: size_gb = 0

            if seeds >= CONFIG["search"]["min_seeds"]:
                score = evaluate_torrent(title, seeds, size_gb, is_tv)
                if score > 0: 
                    href = title_tag.get('href')
                    topic_id = href.split('t=')[1] if 't=' in href else None
                    valid_torrents.append({"title": title, "topic_id": topic_id, "seeds": seeds, "size_gb": size_gb, "score": score})

    if not valid_torrents: return None, 0

    valid_torrents.sort(key=lambda x: x['score'], reverse=True)
    best = valid_torrents[0]
    print(f"   ✅ НАШЕЛ: {best['title'][:50]}... (Сиды: {best['seeds']})")

    torrent_path = f"temp_{best['topic_id']}.torrent"
    try:
        with open(torrent_path, "wb") as f:
            f.write(session.get(f"https://{active_domain}/forum/dl.php?t={best['topic_id']}", timeout=10).content)
    except: return None, 0

    file_index = find_episode_index(torrent_path, target_episode) if target_episode > 0 else 0
    return torrent_path, file_index

def try_torrent(query, start_time, duration_secs, output_path, target_episode, is_tv):
    print(f"\n🔵 [TORRENT] Пробую запрос: '{query}'")
    torrent_path, file_index = get_torrent_file_and_index(query, target_episode, is_tv)
    
    if not torrent_path: 
        print("   ❌ Ничего подходящего по этому запросу.")
        return False
    
    port = get_free_port()
    stream_url = f"http://127.0.0.1:{port}/"
    
    peerflix_cmd = ["peerflix", torrent_path, "--index", str(file_index), "--port", str(port)]
    for tr in CONFIG["trackers"]: peerflix_cmd.extend(["--tracker", tr])
        
    peerflix = subprocess.Popen(peerflix_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    server_ready = False
    try:
        for _ in range(40): 
            if peerflix.poll() is not None: return False
            try:
                urllib.request.urlopen(urllib.request.Request(stream_url, method='HEAD'), timeout=2)
                server_ready = True; break
            except: time.sleep(1)
                
        if not server_ready: return False

        print("   ✅ Соединение установлено. Начинаем нарезку (Ждите)...")
        time.sleep(3) 
        duration_str = seconds_to_time(duration_secs)
        
        ffmpeg_cmd = [
            "ffmpeg", "-hide_banner", "-stats", 
            "-i", stream_url, "-ss", start_time, "-t", duration_str,
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-y", output_path
        ]

        try:
            result = subprocess.run(ffmpeg_cmd, timeout=180)
            if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                return True
            return False
        except subprocess.TimeoutExpired:
            return False
    finally:
        peerflix.terminate()
        try: peerflix.wait(timeout=3)
        except: peerflix.kill()
        if os.path.exists(torrent_path): os.remove(torrent_path)

# =====================================================================
# 🚀 ГЛАВНЫЙ КОНТРОЛЛЕР
# =====================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--orig_title", default="") # НОВЫЙ АРГУМЕНТ
    parser.add_argument("--year", default="0")
    parser.add_argument("--type", default="movie")
    parser.add_argument("--season", default="0")
    parser.add_argument("--episode", default="0")
    parser.add_argument("--start", required=True)
    parser.add_argument("--duration", required=True, type=int)
    parser.add_argument("--source", default="all")
    args = parser.parse_args()

    if args.source == "torrent": CONFIG["search"]["priority"] = ["torrent"]
    elif args.source == "youtube": CONFIG["search"]["priority"] = ["youtube"]
    else: CONFIG["search"]["priority"] = ["torrent", "youtube"] if args.type == 'tv' else ["youtube", "torrent"]
            
    is_tv = (args.type == 'tv')
    target_ep = int(args.episode) if is_tv else 0
    ensure_dir(CONFIG["clip"]["output_folder"])
    
    # ГЕНЕРИРУЕМ СПИСОК ЗАПРОСОВ
    queries = generate_search_queries(args.title, args.orig_title, args.year, args.type, args.season, args.episode)
    
    if is_tv and int(args.season) > 0:
        safe_name = f"{args.title}_S{int(args.season):02d}E{target_ep:02d}"
    else:
        safe_name = f"{args.title}_{args.year}"
        
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in " _-").strip().replace(" ", "_")
    output_file = os.path.join(CONFIG["clip"]["output_folder"], f"{safe_name}_clip.mp4")

    print("="*60)
    print(f"🎬 ЗАДАЧА: Вырезать {args.duration} сек начиная с {args.start}")
    print("="*60)

    success = False
    
    # ПЕРЕБИРАЕМ ИСТОЧНИКИ И ЗАПРОСЫ
    for source in CONFIG["search"]["priority"]:
        print(f"\n================ ИСТОЧНИК: [{source.upper()}] ================")
        
        for query in queries:
            if source == "youtube":
                success = try_youtube(query, args.start, args.duration, output_file)
            elif source == "torrent":
                success = try_torrent(query, args.start, args.duration, output_file, target_ep, is_tv)
                
            if success: break # Нашли по одному из запросов - выходим из цикла запросов!
            
        if success: break # Нашли в одном из источников - выходим из главного цикла!

    if success:
        print(f"\n🎉 ГОТОВО! Видео сохранено: {os.path.abspath(output_file)}\n")
    else:
        print("\n❌ ПРОВАЛ: Все запросы и источники оказались мертвы.\n")

if __name__ == "__main__":
    main()