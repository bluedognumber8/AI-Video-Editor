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

CONFIG = {
    "rutracker": {
        "username": "mkiisklaa",
        "password": "'ffaCt!$M972sQU",
        "cookie_file": "rutracker_cookies.pkl"
    },
    "search": {
        "priority": ["torrent", "youtube"], 
        "min_seeds": 5,              
        "max_size_gb": 35.0           
    },
    "scoring": {
        "res_1080p": 1000, "res_720p": 300, "source_web": 400,
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
    h, m, s = map(float, time_str.split(':'))
    return int(h * 3600 + m * 60 + s)

def seconds_to_time(seconds):
    return f"{int(seconds)//3600:02}:{int(seconds)%3600//60:02}:{int(seconds)%60:02}"

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def ensure_dir(path):
    if not os.path.exists(path): os.makedirs(path)

def generate_search_queries(title_ru, title_orig, year, m_type, season, episode):
    queries = []
    y = int(year)
    if m_type == 'movie' or int(season) == 0:
        if y > 0: queries.append(f"{title_ru} {y}")
        if title_orig and title_orig.lower() != title_ru.lower():
            if y > 0: queries.append(f"{title_orig} {y}")
        queries.append(title_ru)
    else:
        s_num, e_num = int(season), int(episode)
        queries.append(f"{title_ru} {s_num} сезон")
        if title_orig and title_orig.lower() != title_ru.lower():
            queries.append(f"{title_orig} S{s_num:02d}")
        queries.append(f"{title_ru} S{s_num:02d}E{e_num:02d}")
    seen = set()
    return [q for q in queries if not (q in seen or seen.add(q))]

def get_bencode_val(d, key):
    if isinstance(key, str):
        res = d.get(key)
        if res is not None: return res
        return d.get(key.encode('utf-8'))
    return d.get(key)

def find_episode_index(torrent_path, target_episode):
    try:
        with open(torrent_path, 'rb') as f: meta = bencode.decode(f.read())
        info = get_bencode_val(meta, 'info')
        if not info: return 0
        files = get_bencode_val(info, 'files')
        if not files: return 0
        video_files = []
        for idx, f_dict in enumerate(files):
            path_list = get_bencode_val(f_dict, 'path')
            if not path_list: continue
            full_name = "/".join(p.decode('utf-8', 'ignore') if isinstance(p, bytes) else p for p in path_list).lower()
            if full_name.endswith(('.mkv', '.mp4', '.avi')): video_files.append((idx, full_name))
        video_files.sort(key=lambda x: x[1])
        if 0 < target_episode <= len(video_files): return video_files[target_episode - 1][0]
        else: return video_files[0][0]
    except: return 0

def run_clean_ffmpeg(stream_url, start_time, duration_secs, output_path):
    duration_str = seconds_to_time(duration_secs)
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-v", "error", "-stats",
        "-i", stream_url, "-ss", start_time, "-t", duration_str,
        "-map", "0:v:0", "-map", "0:a:0?", "-sn",
        "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", output_path
    ]
    print("   🚀 Режем видео... ", end="", flush=True)
    try:
        process = subprocess.run(ffmpeg_cmd, timeout=120, capture_output=True, text=True)
        if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            print("Готово!")
            return True
        else:
            print("Ошибка нарезки.")
            return False
    except subprocess.TimeoutExpired:
        print("Таймаут (зависло).")
        return False

def do_youtube_download(video_id, start_time, duration_secs, output_path):
    end_time = seconds_to_time(time_to_seconds(start_time) + duration_secs)
    print("   🚀 Скачивание фрагмента с YouTube...")
    download_cmd = [
        "yt-dlp", "--quiet", "--progress", "--download-sections", f"*{start_time}-{end_time}",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--force-keyframes-at-cuts", "-o", output_path, f"https://www.youtube.com/watch?v={video_id}"
    ]
    dl_result = subprocess.run(download_cmd)
    if dl_result.returncode == 0 and os.path.exists(output_path):
        print(f"###SOURCE_FOUND###:youtube:{video_id}")
        return True
    return False

def try_youtube(query, start_time, duration_secs, output_path):
    print(f"\n🔴 [YOUTUBE] Поиск: '{query}'")
    required_min_duration = time_to_seconds(start_time) + duration_secs
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
    return do_youtube_download(best_video_id, start_time, duration_secs, output_path)

def do_torrent_download(topic_id, start_time, duration_secs, output_path, target_episode, active_domain):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    with open(CONFIG["rutracker"]["cookie_file"], 'rb') as f: session.cookies.update(pickle.load(f))
    
    torrent_path = f"temp_{topic_id}.torrent"
    try:
        with open(torrent_path, "wb") as f:
            f.write(session.get(f"https://{active_domain}/forum/dl.php?t={topic_id}", timeout=10).content)
    except: return False

    file_index = find_episode_index(torrent_path, target_episode) if target_episode > 0 else 0
    port = get_free_port()
    stream_url = f"http://127.0.0.1:{port}/"
    
    peerflix_cmd = ["peerflix", torrent_path, "--index", str(file_index), "--port", str(port), "--quiet"]
    for tr in CONFIG["trackers"]: peerflix_cmd.extend(["--tracker", tr])
    peerflix = subprocess.Popen(peerflix_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print("   ⏳ Подключение к пирам... ", end="", flush=True)
    server_ready = False
    try:
        for _ in range(40): 
            if peerflix.poll() is not None: return False
            try:
                urllib.request.urlopen(urllib.request.Request(stream_url, method='HEAD'), timeout=2)
                server_ready = True
                print("Успех!")
                break
            except: time.sleep(1)
                
        if not server_ready: return False
        time.sleep(3) 
        
        if run_clean_ffmpeg(stream_url, start_time, duration_secs, output_path):
            print(f"###SOURCE_FOUND###:torrent:{topic_id}")
            return True
        return False
    finally:
        peerflix.terminate()
        try: peerflix.wait(timeout=3)
        except: peerflix.kill()
        if os.path.exists(torrent_path): os.remove(torrent_path)

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

def try_torrent(query, start_time, duration_secs, output_path, target_episode, is_tv):
    print(f"\n🔵 [TORRENT] Поиск: '{query}'")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    c_file = CONFIG["rutracker"]["cookie_file"]
    if os.path.exists(c_file):
        with open(c_file, 'rb') as f: session.cookies.update(pickle.load(f))

    active_domain = None
    for domain in ["rutracker.org", "rutracker.net"]:
        try:
            if session.get(f"https://{domain}/forum/privmsg.php?folder=inbox", timeout=5).status_code == 200:
                active_domain = domain; break
        except: pass
    if not active_domain: return False

    search_url = f"https://{active_domain}/forum/tracker.php?nm={urllib.parse.quote(query.encode('windows-1251'))}&o=10&s=2"
    soup = BeautifulSoup(session.get(search_url, timeout=15).content.decode('windows-1251'), 'html.parser')
    valid_torrents = []

    for row in soup.select("tr.hl-tr"):
        title_tag = row.select_one("a.tLink")
        if title_tag:
            title = title_tag.text.strip()
            try: seeds = int(row.select_one(".seedmed").text.strip()) if row.select_one(".seedmed") else 0
            except: seeds = 0
            try: size_gb = int(row.select_one(".tor-size")['data-ts_text']) / (1024**3) if row.select_one(".tor-size") else 0
            except: size_gb = 0

            if seeds >= CONFIG["search"]["min_seeds"]:
                score = evaluate_torrent(title, seeds, size_gb, is_tv)
                if score > 0: 
                    href = title_tag.get('href')
                    topic_id = href.split('t=')[1] if 't=' in href else None
                    if topic_id: valid_torrents.append({"topic_id": topic_id, "score": score})

    if not valid_torrents: return False
    valid_torrents.sort(key=lambda x: x['score'], reverse=True)
    
    for t_info in valid_torrents[:3]:
        if do_torrent_download(t_info['topic_id'], start_time, duration_secs, output_path, target_episode, active_domain):
            return True
    return False

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
    parser.add_argument("--force_source", default="") # НОВЫЙ АРГУМЕНТ (ПРИВЯЗКА!)
    args = parser.parse_args()

    is_tv = (args.type == 'tv')
    target_ep = int(args.episode) if is_tv else 0
    ensure_dir(CONFIG["clip"]["output_folder"])
    
    if is_tv and int(args.season) > 0: safe_name = f"{args.title}_S{int(args.season):02d}E{target_ep:02d}"
    else: safe_name = f"{args.title}_{args.year}" if int(args.year) > 0 else args.title
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in " _-").strip().replace(" ", "_")
    output_file = os.path.join(CONFIG["clip"]["output_folder"], f"{safe_name}_clip.mp4")

    print(f"🎬 Вырезаем {args.duration}с. начиная с {args.start}")

    # === РЕЖИМ ПРЯМОГО СКАЧИВАНИЯ ПО ПРИВЯЗАННОМУ ИСТОЧНИКУ ===
    if args.force_source:
        s_type, s_id = args.force_source.split(':', 1)
        print(f"📌 Используем жестко привязанный источник: {s_type} (ID: {s_id})...")
        if s_type == 'youtube':
            if do_youtube_download(s_id, args.start, args.duration, output_file): sys.exit(0)
        elif s_type == 'torrent':
            active_domain = "rutracker.org"
            if do_torrent_download(s_id, args.start, args.duration, output_file, target_ep, active_domain): sys.exit(0)
        print("❌ Привязанный источник больше не работает. Начинаем новый поиск!")

    # === ОБЫЧНЫЙ РЕЖИМ ПОИСКА ===
    if args.source == "torrent": CONFIG["search"]["priority"] = ["torrent"]
    elif args.source == "youtube": CONFIG["search"]["priority"] = ["youtube"]
    else: CONFIG["search"]["priority"] = ["torrent", "youtube"] if is_tv else ["youtube", "torrent"]
            
    queries = generate_search_queries(args.title, args.orig_title, args.year, args.type, args.season, args.episode)
    success = False

    for source in CONFIG["search"]["priority"]:
        for query in queries:
            if source == "youtube": success = try_youtube(query, args.start, args.duration, output_file)
            elif source == "torrent": success = try_torrent(query, args.start, args.duration, output_file, target_ep, is_tv)
            if success: break
        if success: break

    if not success: sys.exit(1)

if __name__ == "__main__":
    main()