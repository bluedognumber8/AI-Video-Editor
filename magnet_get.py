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
import re
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

# =====================================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================

def time_to_seconds(time_str):
    parts = time_str.split(':')
    if len(parts) == 3:
        h, m, s = map(float, parts)
        return int(h * 3600 + m * 60 + s)
    elif len(parts) == 2:
        m, s = map(float, parts)
        return int(m * 60 + s)
    return int(float(time_str))

def seconds_to_time(seconds):
    return f"{int(seconds)//3600:02}:{int(seconds)%3600//60:02}:{int(seconds)%60:02}"

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

# --- ПАРСЕР СЕРИЙ ИЗ ТОРРЕНТА ---
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
            full_name = "/".join(
                p.decode('utf-8', 'ignore') if isinstance(p, bytes) else p
                for p in path_list
            ).lower()
            if full_name.endswith(('.mkv', '.mp4', '.avi')):
                video_files.append((idx, full_name))

        video_files.sort(key=lambda x: x[1])

        if 0 < target_episode <= len(video_files):
            print(f"      [Парсер] Найдена нужная серия: {video_files[target_episode - 1][1]}")
            return video_files[target_episode - 1][0]
        else:
            return video_files[0][0]
    except Exception:
        return 0

# =====================================================================
# 🔴 МОДУЛЬ YOUTUBE
# =====================================================================

def try_youtube(query, start_time, duration_secs, output_path):
    print(f"\n🔴 [YOUTUBE] Ищем: '{query}'...")
    start_sec = time_to_seconds(start_time)
    required_min_duration = start_sec + duration_secs

    search_cmd = [
        "yt-dlp", f"ytsearch5:{query} полный фильм",
        "--dump-json", "--no-warnings"
    ]

    try:
        result = subprocess.run(search_cmd, capture_output=True, text=True, check=True)
        videos = [json.loads(line) for line in result.stdout.strip().split('\n') if line]
    except Exception:
        return False

    best_video_id = None
    for v in videos:
        if v.get("duration", 0) > required_min_duration:
            best_video_id = v.get("id")
            print(f"✅ Найдено подходящее видео: {v.get('title')[:60]}... ({v.get('duration')//60} мин)")
            break

    if not best_video_id:
        return False

    end_time = seconds_to_time(required_min_duration)
    print(f"🚀 Вырезаем фрагмент {start_time} - {end_time} с YouTube...")

    download_cmd = [
        "yt-dlp", "--quiet", "--progress",
        "--download-sections", f"*{start_time}-{end_time}",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--force-keyframes-at-cuts", "-o", output_path,
        f"https://www.youtube.com/watch?v={best_video_id}"
    ]

    dl_result = subprocess.run(download_cmd)
    return dl_result.returncode == 0 and os.path.exists(output_path)

# =====================================================================
# 🎬 FFMPEG РАБОТА С ПРОГРЕССОМ
# =====================================================================

def run_ffmpeg_with_progress(ffmpeg_cmd, duration_secs, label=""):
    process = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace'
    )

    last_update_time = time.time()
    
    with open("ffmpeg_debug.log", "w", encoding="utf-8") as debug_log:
        debug_log.write(f"COMMAND: {' '.join(ffmpeg_cmd)}\n\n")

        with tqdm(total=duration_secs, desc=f"   💾 {label}", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} сек", leave=True) as pbar:
            current_time = 0

            for line in iter(process.stdout.readline, ''):
                debug_log.write(line)
                debug_log.flush()

                match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})", line)
                if match:
                    last_update_time = time.time()
                    t_str = match.group(1)
                    h, m, s = t_str.split(':')
                    sec = int(h) * 3600 + int(m) * 60 + float(s)

                    inc = sec - current_time
                    if inc > 0:
                        if current_time + inc > duration_secs:
                            pbar.update(duration_secs - current_time)
                            current_time = duration_secs
                        else:
                            pbar.update(inc)
                            current_time += inc

                # Защита от бесконечного зависания FFMPEG на битом кадре
                if time.time() - last_update_time > 60:
                    process.kill()
                    return False, "timeout"

    process.wait()
    if process.returncode == 0:
        return True, "done"
    return False, "error"

# =====================================================================
# 🔵 МОДУЛЬ TORRENT (С ПРОВЕРКОЙ БУФЕРИЗАЦИИ И ПЕРЕБОРОМ ТОП-3)
# =====================================================================

def evaluate_torrent(title, seeds, size_gb, is_tv):
    title_lower = title.lower()
    bad_versions = [
        "director's cut", "directors cut", "режиссерская", "extended", "расширенная",
        "unrated", "uncut", "без цензуры", "special edition", "open matte"
    ]
    if any(bad in title_lower for bad in bad_versions): return -100000
    if "4k" in title_lower or "2160p" in title_lower: return -50000
    if "remux" in title_lower or "bdremux" in title_lower: return -50000

    if not is_tv and size_gb > 20.0: return -50000
    if is_tv and size_gb > 40.0: return -50000

    score = seeds * 1000
    if "1080p" in title_lower: score += CONFIG["scoring"]["res_1080p"]
    elif "720p" in title_lower: score += CONFIG["scoring"]["res_720p"]
    if "web-dl" in title_lower or "webrip" in title_lower: score += CONFIG["scoring"]["source_web"]
    score -= int(size_gb * 10)
    return score

def get_top_torrents(query, is_tv, limit=3):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    c_file = CONFIG["rutracker"]["cookie_file"]

    if os.path.exists(c_file):
        with open(c_file, 'rb') as f:
            session.cookies.update(pickle.load(f))

    active_domain, is_logged_in = None, False
    for domain in ["rutracker.org", "rutracker.net"]:
        try:
            resp = session.get(f"https://{domain}/forum/privmsg.php?folder=inbox", allow_redirects=False, timeout=5)
            if resp.status_code == 200: active_domain, is_logged_in = domain, True; break
            elif resp.status_code in [301, 302]: active_domain = domain; break
        except: pass

    if not active_domain:
        print("❌ Не удалось подключиться к RuTracker.")
        return [], session, active_domain

    if not is_logged_in:
        session.post(f"https://{active_domain}/forum/login.php", data={
            "login_username": CONFIG["rutracker"]["username"],
            "login_password": CONFIG["rutracker"]["password"],
            "login": "Вход"
        }, timeout=15)
        with open(c_file, 'wb') as f: pickle.dump(session.cookies, f)

    search_url = f"https://{active_domain}/forum/tracker.php?nm={urllib.parse.quote(query.encode('windows-1251'))}&o=10&s=2"
    soup = BeautifulSoup(session.get(search_url, timeout=15).content.decode('windows-1251'), 'html.parser')
    valid_torrents = []

    for row in soup.select("tr.hl-tr"):
        title_tag = row.select_one("a.tLink")
        if title_tag:
            title = title_tag.text.strip()
            seeds = int(row.select_one(".seedmed").text.strip()) if row.select_one(".seedmed") else 0
            size_gb = int(row.select_one(".tor-size")['data-ts_text']) / (1024 ** 3) if row.select_one(".tor-size") else 0

            if seeds >= CONFIG["search"]["min_seeds"]:
                score = evaluate_torrent(title, seeds, size_gb, is_tv)
                if score > 0:
                    href = title_tag.get('href')
                    topic_id = href.split('t=')[1] if 't=' in href else None
                    valid_torrents.append({"title": title, "topic_id": topic_id, "seeds": seeds, "size_gb": size_gb, "score": score})

    valid_torrents.sort(key=lambda x: x['score'], reverse=True)
    return valid_torrents[:limit], session, active_domain

def try_torrent(query, start_time, duration_secs, output_path, target_episode, is_tv):
    print(f"\n🔵 [TORRENT] Ищем: '{query}' на RuTracker...")

    top_torrents, session, active_domain = get_top_torrents(query, is_tv, limit=3)
    if not top_torrents:
        print("❌ Нет подходящих раздач (возможно, слишком тяжелые или нет сидов).")
        return False
        
    print(f"   Найдено {len(top_torrents)} подходящих раздач. Начинаю перебор.")

    for attempt, torrent_info in enumerate(top_torrents, 1):
        print(f"\n▶️ ПОПЫТКА {attempt}/{len(top_torrents)}: {torrent_info['title'][:55]}... (Сидов: {torrent_info['seeds']} | {torrent_info['size_gb']:.1f} ГБ)")
        
        torrent_path = f"temp_{torrent_info['topic_id']}.torrent"
        try:
            with open(torrent_path, "wb") as f:
                f.write(session.get(f"https://{active_domain}/forum/dl.php?t={torrent_info['topic_id']}", timeout=10).content)
        except Exception as e:
            print(f"   ❌ Ошибка скачивания файла раздачи: {e}")
            continue

        file_index = find_episode_index(torrent_path, target_episode) if target_episode > 0 else 0
        port = get_free_port()
        stream_url = f"http://127.0.0.1:{port}/"

        peerflix_cmd = ["peerflix", torrent_path, "--index", str(file_index), "--port", str(port), "--quiet"]
        for tr in CONFIG["trackers"]: peerflix_cmd.extend(["--tracker", tr])

        log_file = open("peerflix_debug.log", "w", encoding="utf-8")
        peerflix = subprocess.Popen(peerflix_cmd, stdout=log_file, stderr=subprocess.STDOUT)

        print("   ⏳ Буферизация (ждем видео-данные от пиров)", end="", flush=True)
        server_ready = False

        try:
            # ФИЗИЧЕСКАЯ ПРОВЕРКА ДАННЫХ: Ждем до 90 секунд, пока скачается первый 1 КБ фильма
            for _ in range(90):
                if peerflix.poll() is not None:
                    print("\n   ❌ Peerflix упал.")
                    break
                try:
                    req = urllib.request.Request(stream_url)
                    with urllib.request.urlopen(req, timeout=3) as response:
                        chunk = response.read(1024)
                        if chunk:
                            server_ready = True
                            print(" ✅ Видео пошло!")
                            break
                except Exception:
                    print(".", end="", flush=True)
                    time.sleep(1)

            if not server_ready:
                print("\n   ❌ Торрент завис (пиры не отдают данные). Перехожу к следующему...")
                continue

            time.sleep(5) # Накопление буфера
            duration_str = seconds_to_time(duration_secs)

            # -rw_timeout 90000000 = Таймаут увеличен до 90 секунд!
            # -analyzeduration 100M -probesize 100M = Защита от краша на стримах без метаданных
            base_ffmpeg = [
                "ffmpeg", "-hide_banner", "-loglevel", "info",
                "-rw_timeout", "90000000",
                "-analyzeduration", "100M", "-probesize", "100M",
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"
            ]

            # Быстрая вырезка (-ss ДО -i)
            # -sn отключает субтитры, -map 0:v:0 -map 0:a:0? забирает только главную дорожку
            cmd_fast = base_ffmpeg + [
                "-ss", start_time, "-i", stream_url, "-t", duration_str,
                "-map", "0:v:0", "-map", "0:a:0?", "-sn",
                "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-y", output_path
            ]

            print("   🚀 Попытка 1: быстрый seek...")
            ok, reason = run_ffmpeg_with_progress(cmd_fast, duration_secs, "Быстрый")

            if ok and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                return True

            if reason in ["timeout", "error"]:
                print(f"\n   ⚠️ Быстрый seek не удался. Пробую надёжный режим (потребует скачивания видео с начала)...")
                
                # Надежная вырезка (-ss ПОСЛЕ -i)
                cmd_safe = base_ffmpeg + [
                    "-i", stream_url, "-ss", start_time, "-t", duration_str,
                    "-map", "0:v:0", "-map", "0:a:0?", "-sn",
                    "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-y", output_path
                ]

                ok, reason = run_ffmpeg_with_progress(cmd_safe, duration_secs, "Надёжный")

                if ok and os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                    return True

            print("\n   ❌ FFmpeg не смог обработать этот торрент. Перехожу к следующему...")

        except KeyboardInterrupt:
            print("\n   🛑 Остановлено пользователем.")
            return False
        finally:
            peerflix.terminate()
            try: peerflix.wait(timeout=3)
            except: peerflix.kill()
            if not log_file.closed: log_file.close()
            if os.path.exists(torrent_path): os.remove(torrent_path)

    print("\n❌ Все доступные торренты оказались битыми, зависшими или без пиров.")
    return False

# =====================================================================
# 🚀 ГЛАВНЫЙ КОНТРОЛЛЕР
# =====================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
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
    else:
        CONFIG["search"]["priority"] = ["torrent", "youtube"] if args.type == 'tv' else ["youtube", "torrent"]

    is_tv = (args.type == 'tv')
    target_ep = int(args.episode) if is_tv else 0
    ensure_dir(CONFIG["clip"]["output_folder"])

    if is_tv and int(args.season) > 0:
        query = f"{args.title} {int(args.season)} сезон"
        safe_name = f"{args.title}_S{int(args.season):02d}E{target_ep:02d}"
    else:
        query = f"{args.title} {args.year}" if int(args.year) > 0 else args.title
        safe_name = args.title

    safe_name = "".join(c for c in safe_name if c.isalnum() or c in " _-").strip().replace(" ", "_")
    output_file = os.path.join(CONFIG["clip"]["output_folder"], f"{safe_name}_clip.mp4")

    print("=" * 60)
    print(f"🎬 ЗАДАЧА: Найти '{query}', вырезать {args.duration} сек начиная с {args.start}")
    print("=" * 60)

    success = False
    for source in CONFIG["search"]["priority"]:
        if source == "youtube": success = try_youtube(query, args.start, args.duration, output_file)
        elif source == "torrent": success = try_torrent(query, args.start, args.duration, output_file, target_ep, is_tv)

        if success:
            print(f"\n🎉 ГОТОВО! Видео сохранено: {os.path.abspath(output_file)}\n")
            break
        else:
            print(f"⚠️ Источник [{source}] не справился. Переход к следующему...\n")

    if not success:
        print("❌ ОШИБКА: Ни один из источников не смог скачать и обрезать видео.")

if __name__ == "__main__":
    main()