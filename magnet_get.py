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
        "udp://open.demonii.com:1337/announce",
        "udp://tracker.torrent.eu.org:451/announce",
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
    for port in range(9000, 9200):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(
                    socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
                )
                s.bind(("", port))
                return port
        except OSError:
            continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def kill_stale_peerflix():
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/f", "/im", "peerflix.exe"],
                capture_output=True,
            )
        else:
            subprocess.run(
                ["pkill", "-f", "peerflix"], capture_output=True
            )
    except Exception:
        pass


def check_dependency(cmd_name):
    try:
        cmd = "where" if sys.platform == "win32" else "which"
        result = subprocess.run(
            [cmd, cmd_name], capture_output=True, text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def check_all_dependencies(sources):
    missing = []
    if not check_dependency("ffmpeg"):
        missing.append("ffmpeg")
    if "torrent" in sources and not check_dependency("peerflix"):
        missing.append("peerflix (npm install -g peerflix)")
    if "youtube" in sources and not check_dependency("yt-dlp"):
        missing.append("yt-dlp (pip install yt-dlp)")
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
            with open(c_file, "rb") as f:
                session.cookies.update(pickle.load(f))
            return True
        except Exception as e:
            print(f"⚠️ Ошибка чтения cookie: {e}")
    return False


def save_cookies(session):
    c_file = CONFIG["rutracker"]["cookie_file"]
    try:
        with open(c_file, "wb") as f:
            pickle.dump(session.cookies, f)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения cookie: {e}")


def find_working_domain(session):
    for domain in CONFIG["domains"]:
        try:
            r = session.get(
                f"https://{domain}/forum/index.php",
                timeout=8,
                allow_redirects=True,
            )
            if r.status_code == 200:
                return domain
        except Exception:
            continue
    return None


def check_auth(session, domain):
    try:
        r = session.get(
            f"https://{domain}/forum/privmsg.php?folder=inbox",
            timeout=8,
            allow_redirects=True,
        )
        if r.status_code == 200 and "login" not in r.url.lower():
            return True
    except Exception:
        pass
    return False


def do_login(session, domain):
    try:
        login_data = {
            "login_username": CONFIG["rutracker"]["username"],
            "login_password": CONFIG["rutracker"]["password"],
            "login": "Вход",
        }
        session.post(
            f"https://{domain}/forum/login.php",
            data=login_data,
            timeout=15,
        )
        cookies_dict = session.cookies.get_dict()
        if "bb_session" in cookies_dict or "bb_data" in cookies_dict:
            save_cookies(session)
            return True
    except Exception as e:
        print(f"⚠️ Ошибка логина на {domain}: {e}")
    return False


def ensure_rutracker_session():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    load_cookies(session)

    domain = find_working_domain(session)
    if not domain:
        print(
            "❌ RuTracker недоступен "
            "(все домены заблокированы/недоступны)"
        )
        return None, None

    if check_auth(session, domain):
        return session, domain

    print("🔑 Cookie истекли, выполняем повторный вход...")
    if do_login(session, domain):
        print(f"✅ Авторизация успешна на {domain}")
        return session, domain

    for alt_domain in CONFIG["domains"]:
        if alt_domain == domain:
            continue
        try:
            r = session.get(
                f"https://{alt_domain}/forum/index.php", timeout=8
            )
            if r.status_code == 200 and do_login(session, alt_domain):
                print(f"✅ Авторизация успешна на {alt_domain}")
                return session, alt_domain
        except Exception:
            continue

    print("❌ Не удалось авторизоваться на RuTracker")
    return None, None


# =====================================================================
# 🔍 ПОИСКОВЫЕ ЗАПРОСЫ
# =====================================================================
def generate_search_queries(
    title_ru, title_orig, year, m_type, season, episode
):
    queries = []
    y = int(year)
    if m_type == "movie" or int(season) == 0:
        if y > 0:
            queries.append(f"{title_ru} {y}")
        if title_orig and title_orig.lower() != title_ru.lower():
            if y > 0:
                queries.append(f"{title_orig} {y}")
        queries.append(title_ru)
    else:
        s_num = int(season)
        e_num = int(episode)
        queries.append(f"{title_ru} {s_num} сезон")
        if title_orig and title_orig.lower() != title_ru.lower():
            queries.append(f"{title_orig} S{s_num:02d}")
        queries.append(f"{title_ru} S{s_num:02d}E{e_num:02d}")

    seen = set()
    return [q for q in queries if not (q in seen or seen.add(q))]


# =====================================================================
# 🗂 ТОРРЕНТ-ФАЙЛЫ
# =====================================================================
def get_bencode_val(d, key):
    if isinstance(d, dict):
        res = d.get(key)
        if res is not None:
            return res
        if isinstance(key, str):
            return d.get(key.encode("utf-8"))
        elif isinstance(key, bytes):
            return d.get(key.decode("utf-8", "ignore"))
    return None


def find_episode_index(torrent_path, target_episode):
    if not bencode:
        print("⚠️ bencode не установлен, используем индекс 0")
        return 0
    try:
        with open(torrent_path, "rb") as f:
            raw = f.read()
        if not raw or raw[0:1] != b"d":
            print("⚠️ Файл не является валидным торрентом")
            return 0

        meta = bencode.decode(raw)
        info = get_bencode_val(meta, "info")
        if not info:
            return 0
        files = get_bencode_val(info, "files")
        if not files:
            return 0

        video_files = []
        for idx, f_dict in enumerate(files):
            path_list = get_bencode_val(f_dict, "path")
            if not path_list:
                continue
            full_name = "/".join(
                (
                    p.decode("utf-8", "ignore")
                    if isinstance(p, bytes)
                    else str(p)
                )
                for p in path_list
            ).lower()
            if full_name.endswith(
                (".mkv", ".mp4", ".avi", ".ts", ".m4v")
            ):
                video_files.append((idx, full_name))

        video_files.sort(key=lambda x: x[1])
        if 0 < target_episode <= len(video_files):
            return video_files[target_episode - 1][0]
        elif video_files:
            return video_files[0][0]
        return 0
    except Exception as e:
        print(f"⚠️ Ошибка разбора торрента: {e}")
        return 0


def validate_torrent_file(torrent_path):
    if not os.path.exists(torrent_path):
        return False
    try:
        with open(torrent_path, "rb") as f:
            header = f.read(100)
        if not header:
            return False
        if header[0:1] != b"d":
            if b"<html" in header.lower() or b"<!doc" in header.lower():
                print(
                    "❌ Вместо торрента получена HTML-страница "
                    "(сессия истекла?)"
                )
            else:
                print(
                    f"❌ Файл не является торрентом "
                    f"(начало: {header[:10]})"
                )
            return False
        size = os.path.getsize(torrent_path)
        if size < 100:
            print(f"❌ Файл слишком маленький ({size} байт)")
            return False
        return True
    except Exception as e:
        print(f"❌ Ошибка проверки торрент-файла: {e}")
        return False


# =====================================================================
# ✂️ FFMPEG
# =====================================================================
def run_clean_ffmpeg(stream_url, start_time, duration_secs, output_path):
    duration_str = seconds_to_time(duration_secs)
    timeout = max(180, duration_secs * 15)

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
        process = subprocess.run(
            ffmpeg_cmd, timeout=timeout, capture_output=True, text=True
        )
        if (
            process.returncode == 0
            and os.path.exists(output_path)
            and os.path.getsize(output_path) > 1024
        ):
            print("Готово!")
            return True
        else:
            print("Ошибка нарезки.")
            if process.stderr:
                print(f"   ffmpeg: {process.stderr[:300]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"Таймаут ({timeout}с).")
        return False
    except FileNotFoundError:
        print("❌ ffmpeg не найден!")
        return False


# =====================================================================
# 🔴 YOUTUBE
# =====================================================================
def do_youtube_download(video_id, start_time, duration_secs, output_path):
    end_time = seconds_to_time(
        time_to_seconds(start_time) + duration_secs
    )
    print("   🚀 Скачивание фрагмента с YouTube...")
    download_cmd = [
        "yt-dlp", "--quiet", "--progress",
        "--download-sections", f"*{start_time}-{end_time}",
        "-f",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--force-keyframes-at-cuts",
        "-o", output_path,
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    try:
        dl_result = subprocess.run(download_cmd, timeout=120)
        if dl_result.returncode == 0 and os.path.exists(output_path):
            print(f"###SOURCE_FOUND###:youtube:{video_id}")
            return True
    except subprocess.TimeoutExpired:
        print("⏰ YouTube — таймаут")
    except FileNotFoundError:
        print("❌ yt-dlp не найден!")
    return False


def try_youtube(query, start_time, duration_secs, output_path):
    print(f"\n🔴 [YOUTUBE] Поиск: '{query}'")
    required_min_duration = time_to_seconds(start_time) + duration_secs
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                f"ytsearch5:{query} полный фильм",
                "--dump-json",
                "--no-warnings",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False
        videos = [
            json.loads(line)
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    except json.JSONDecodeError:
        return False

    best_video_id = None
    for v in videos:
        if v.get("duration", 0) > required_min_duration:
            best_video_id = v.get("id")
            break

    if not best_video_id:
        print("   Подходящее видео не найдено")
        return False

    return do_youtube_download(
        best_video_id, start_time, duration_secs, output_path
    )


# =====================================================================
# 🔵 TORRENT
# =====================================================================
def evaluate_torrent(title, seeds, size_gb, is_tv):
    title_lower = title.lower()
    bad_versions = [
        "director's cut", "directors cut", "режиссерская",
        "extended", "расширенная", "unrated", "uncut",
        "без цензуры", "special edition", "open matte",
    ]
    if any(bad in title_lower for bad in bad_versions):
        return -100000
    if "4k" in title_lower or "2160p" in title_lower:
        return -50000
    if "remux" in title_lower or "bdremux" in title_lower:
        return -50000
    if not is_tv and size_gb > 20.0:
        return -50000
    if is_tv and size_gb > 40.0:
        return -50000

    score = seeds * 1000
    sc = CONFIG["scoring"]
    if "1080p" in title_lower:
        score += sc["res_1080p"]
    elif "720p" in title_lower:
        score += sc["res_720p"]
    if "web-dl" in title_lower or "webrip" in title_lower:
        score += sc["source_web"]
    if "theatrical" in title_lower:
        score += 500
    score -= int(size_gb * 10)
    return score


def do_torrent_download(
    topic_id, start_time, duration_secs, output_path,
    target_episode, session, active_domain,
):
    torrent_path = f"temp_{topic_id}.torrent"
    peerflix_process = None

    try:
        print(f"   📥 Скачиваем .torrent (topic: {topic_id})...")
        try:
            resp = session.get(
                f"https://{active_domain}/forum/dl.php?t={topic_id}",
                timeout=15,
            )
            content = resp.content
        except requests.RequestException as e:
            print(f"   ❌ HTTP ошибка: {e}")
            return False

        if not content:
            print("   ❌ Пустой ответ от сервера")
            return False

        with open(torrent_path, "wb") as f:
            f.write(content)

        if not validate_torrent_file(torrent_path):
            print("   ⚠️ Повторная авторизация...")
            if do_login(session, active_domain):
                try:
                    resp = session.get(
                        f"https://{active_domain}"
                        f"/forum/dl.php?t={topic_id}",
                        timeout=15,
                    )
                    with open(torrent_path, "wb") as f:
                        f.write(resp.content)
                except Exception:
                    return False
                if not validate_torrent_file(torrent_path):
                    return False
            else:
                return False

        file_index = (
            find_episode_index(torrent_path, target_episode)
            if target_episode > 0
            else 0
        )

        port = get_free_port()
        stream_url = f"http://127.0.0.1:{port}/"

        peerflix_cmd = [
            "peerflix", torrent_path,
            "--index", str(file_index),
            "--port", str(port),
            "--quiet",
        ]
        for tr in CONFIG["trackers"]:
            peerflix_cmd.extend(["--tracker", tr])

        try:
            peerflix_process = subprocess.Popen(
                peerflix_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            print(
                "   ❌ peerflix не найден! "
                "npm install -g peerflix"
            )
            return False

        print(
            "   ⏳ Подключение к пирам... ",
            end="",
            flush=True,
        )
        server_ready = False
        for _ in range(60):
            if peerflix_process.poll() is not None:
                print("Процесс завершился!")
                return False
            try:
                req = urllib.request.Request(
                    stream_url, method="HEAD"
                )
                urllib.request.urlopen(req, timeout=2)
                server_ready = True
                print("Успех!")
                break
            except Exception:
                time.sleep(1)

        if not server_ready:
            print("Таймаут подключения!")
            return False

        time.sleep(5)

        if run_clean_ffmpeg(
            stream_url, start_time, duration_secs, output_path
        ):
            print(f"###SOURCE_FOUND###:torrent:{topic_id}")
            return True
        return False

    finally:
        if peerflix_process is not None:
            try:
                peerflix_process.terminate()
                peerflix_process.wait(timeout=5)
            except Exception:
                try:
                    peerflix_process.kill()
                    peerflix_process.wait(timeout=3)
                except Exception:
                    pass
        if os.path.exists(torrent_path):
            try:
                os.remove(torrent_path)
            except Exception:
                pass


def try_torrent(
    query, start_time, duration_secs, output_path,
    target_episode, is_tv,
):
    print(f"\n🔵 [TORRENT] Поиск: '{query}'")
    session, active_domain = ensure_rutracker_session()
    if not session or not active_domain:
        return False

    try:
        search_url = (
            f"https://{active_domain}/forum/tracker.php"
            f"?nm={urllib.parse.quote(query.encode('windows-1251'))}"
            f"&o=10&s=2"
        )
        resp = session.get(search_url, timeout=15)
        soup = BeautifulSoup(
            resp.content.decode("windows-1251", errors="ignore"),
            "html.parser",
        )
    except Exception as e:
        print(f"   ❌ Ошибка поиска: {e}")
        return False

    valid_torrents = []
    for row in soup.select("tr.hl-tr"):
        title_tag = row.select_one("a.tLink")
        if not title_tag:
            continue
        title = title_tag.text.strip()
        try:
            seeds_el = row.select_one(".seedmed")
            seeds = (
                int(seeds_el.text.strip()) if seeds_el else 0
            )
        except (ValueError, AttributeError):
            seeds = 0
        try:
            size_el = row.select_one(".tor-size")
            size_gb = (
                int(size_el["data-ts_text"]) / (1024 ** 3)
                if size_el and size_el.get("data-ts_text")
                else 0
            )
        except (ValueError, KeyError, TypeError):
            size_gb = 0

        if seeds >= CONFIG["search"]["min_seeds"]:
            score = evaluate_torrent(title, seeds, size_gb, is_tv)
            if score > 0:
                href = title_tag.get("href", "")
                if "t=" in href:
                    topic_id = href.split("t=")[1].split("&")[0]
                    valid_torrents.append({
                        "topic_id": topic_id,
                        "title": title,
                        "score": score,
                        "seeds": seeds,
                        "size_gb": round(size_gb, 1),
                    })

    if not valid_torrents:
        print("   Подходящие торренты не найдены")
        return False

    valid_torrents.sort(key=lambda x: x["score"], reverse=True)
    print(f"   Найдено {len(valid_torrents)}, пробуем топ-3:")

    for idx, t_info in enumerate(valid_torrents[:3]):
        print(
            f"   #{idx + 1}: {t_info['title'][:60]}... "
            f"(seeds:{t_info['seeds']}, "
            f"size:{t_info['size_gb']}GB)"
        )
        if do_torrent_download(
            t_info["topic_id"], start_time, duration_secs,
            output_path, target_episode, session, active_domain,
        ):
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
    args = parser.parse_args()

    is_tv = args.type == "tv"
    target_ep = int(args.episode) if is_tv else 0

    if args.source == "torrent":
        sources = ["torrent"]
    elif args.source == "youtube":
        sources = ["youtube"]
    else:
        sources = (
            ["torrent", "youtube"]
            if is_tv
            else ["youtube", "torrent"]
        )

    if not check_all_dependencies(sources):
        sys.exit(1)

    kill_stale_peerflix()
    ensure_dir(CONFIG["clip"]["output_folder"])

    if is_tv and int(args.season) > 0:
        safe_name = (
            f"{args.title}_S{int(args.season):02d}E{target_ep:02d}"
        )
    else:
        safe_name = (
            f"{args.title}_{args.year}"
            if int(args.year) > 0
            else args.title
        )
    safe_name = "".join(
        c for c in safe_name if c.isalnum() or c in " _-"
    ).strip().replace(" ", "_")
    output_file = os.path.join(
        CONFIG["clip"]["output_folder"], f"{safe_name}_clip.mp4"
    )

    print(f"🎬 Вырезаем {args.duration}с. начиная с {args.start}")

    # === ПРИВЯЗАННЫЙ ИСТОЧНИК ===
    if args.force_source:
        parts = args.force_source.split(":", 1)
        if len(parts) == 2:
            s_type, s_id = parts
            print(
                f"📌 Привязанный источник: {s_type} (ID: {s_id})..."
            )
            if s_type == "youtube":
                if do_youtube_download(
                    s_id, args.start, args.duration, output_file
                ):
                    sys.exit(0)
            elif s_type == "torrent":
                session, active_domain = ensure_rutracker_session()
                if session and active_domain:
                    if do_torrent_download(
                        s_id, args.start, args.duration,
                        output_file, target_ep,
                        session, active_domain,
                    ):
                        sys.exit(0)
                else:
                    print("❌ Авторизация не удалась")
            print(
                "⚠️ Привязанный источник не сработал. "
                "Новый поиск!"
            )
        else:
            print(
                f"⚠️ Неверный формат force_source: "
                f"{args.force_source}"
            )

    # === ОБЫЧНЫЙ ПОИСК ===
    CONFIG["search"]["priority"] = sources
    queries = generate_search_queries(
        args.title, args.orig_title, args.year,
        args.type, args.season, args.episode,
    )

    print(f"🔍 Запросы: {queries}")
    print(f"📋 Приоритет: {CONFIG['search']['priority']}")

    success = False
    for source in CONFIG["search"]["priority"]:
        if success:
            break
        for query in queries:
            if source == "youtube":
                success = try_youtube(
                    query, args.start, args.duration, output_file
                )
            elif source == "torrent":
                success = try_torrent(
                    query, args.start, args.duration,
                    output_file, target_ep, is_tv,
                )
            if success:
                break

    if success:
        print(f"\n✅ Клип сохранен: {output_file}")
        sys.exit(0)
    else:
        print("\n❌ Не удалось скачать клип.")
        sys.exit(1)


if __name__ == "__main__":
    main()