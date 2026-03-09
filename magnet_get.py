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

# === НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ ===
USERNAME = "mkiisklaa"
PASSWORD = "'ffaCt!$M972sQU"
DEFAULT_QUERY = "Терминатор 2 Судный день 1080p"
COOKIE_FILE = "rutracker_cookies.pkl"

# === ПРИОРИТЕТЫ КАЧЕСТВА ДЛЯ СТРИМИНГА ===
MIN_SEEDS = 10 
MAX_STREAMING_SIZE_GB = 25.0  # Файлы тяжелее 25 ГБ тяжело стримить "на лету"

PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce"
]

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def enrich_magnet(magnet_link):
    for tr in PUBLIC_TRACKERS:
        magnet_link += f"&tr={urllib.parse.quote(tr)}"
    return magnet_link

def evaluate_torrent(title, seeds, size_gb):
    """
    Оценивает торрент. Чем выше балл, тем больше вероятность, что он будет выбран.
    Баланс между качеством, размером и сидами.
    """
    score = seeds  # Базовые баллы = количество сидов

    title_lower = title.lower()
    
    # Приоритет разрешению
    if "1080p" in title_lower:
        score += 1000
    elif "720p" in title_lower:
        score += 300 # 720p лучше, чем ничего, но хуже 1080p
    
    # Приоритет источнику (качество картинки)
    if "bdrip" in title_lower or "blu-ray" in title_lower:
        score += 500
    elif "web-dl" in title_lower or "webrip" in title_lower:
        score += 400
        
    # Избегаем 4K (слишком тяжело для потокового FFmpeg), если явно не просили
    if "4k" in title_lower or "2160p" in title_lower:
        score -= 2000 

    # Штрафуем слишком огромные файлы (Remux-ы по 50-80 ГБ)
    if size_gb > MAX_STREAMING_SIZE_GB:
        # Сильный штраф, но если сидов 500+, он все равно может выиграть
        score -= 1500 

    return score

def get_magnet(username, password, query):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"})

    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, 'rb') as f:
            session.cookies.update(pickle.load(f))

    domains = ["rutracker.org", "rutracker.net"]
    active_domain = None
    is_logged_in = False
    
    for domain in domains:
        try:
            resp = session.get(f"https://{domain}/forum/privmsg.php?folder=inbox", allow_redirects=False, timeout=10)
            if resp.status_code == 200:
                active_domain = domain; is_logged_in = True; break
            elif resp.status_code in [301, 302]: 
                active_domain = domain; break
        except requests.RequestException:
            pass

    if not active_domain:
        print("❌ ОШИБКА: Не удалось подключиться к RuTracker.")
        return None

    if not is_logged_in:
        print("🔐 Входим в аккаунт...")
        session.post(f"https://{active_domain}/forum/login.php", data={"login_username": username, "login_password": password, "login": "Вход"}, timeout=15)
        if not any(c.name == 'bb_session' for c in session.cookies):
            print("❌ ОШИБКА: Неверный логин или пароль."); return None
        with open(COOKIE_FILE, 'wb') as f:
            pickle.dump(session.cookies, f)

    print(f"🔍 Ищем: '{query}'...")
    search_url = f"https://{active_domain}/forum/tracker.php?nm={urllib.parse.quote(query.encode('windows-1251'))}&o=10&s=2"
    
    search_resp = session.get(search_url, timeout=15)
    search_resp.encoding = 'windows-1251'
    soup = BeautifulSoup(search_resp.text, 'html.parser')

    rows = soup.select("tr.hl-tr")
    valid_torrents = []

    for row in rows:
        title_tag = row.select_one("a.tLink")
        seed_tag = row.select_one(".seedmed")
        size_tag = row.select_one(".tor-size") # RuTracker хранит размер тут
        
        if title_tag:
            title = title_tag.text.strip()
            href = title_tag.get('href')
            
            try: seeds = int(seed_tag.text.strip()) if seed_tag else 0
            except ValueError: seeds = 0
            
            # Извлекаем размер в ГБ (data-ts_text содержит размер в байтах)
            size_gb = 0
            if size_tag and size_tag.has_attr('data-ts_text'):
                try: size_gb = int(size_tag['data-ts_text']) / (1024**3)
                except ValueError: pass

            if seeds >= MIN_SEEDS:
                score = evaluate_torrent(title, seeds, size_gb)
                valid_torrents.append({
                    "title": title, "href": href, "seeds": seeds, 
                    "size_gb": size_gb, "score": score
                })

    if not valid_torrents:
        print(f"❌ ОШИБКА: Нет раздач с количеством сидов >= {MIN_SEEDS}.")
        return None

    # Сортируем по очкам (score) по убыванию
    valid_torrents.sort(key=lambda x: x['score'], reverse=True)

    print("\n🏆 Топ-3 найденных раздач по нашему рейтингу:")
    for i, t in enumerate(valid_torrents[:3], 1):
        print(f"{i}. [Баллы: {t['score']}] Сиды: {t['seeds']} | Размер: {t['size_gb']:.1f} ГБ | {t['title'][:70]}...")

    best_torrent = valid_torrents[0]
    print(f"\n✅ ВЫБРАН ПОБЕДИТЕЛЬ: {best_torrent['title'][:60]}...")

    topic_resp = session.get(f"https://{active_domain}/forum/{best_torrent['href']}", timeout=15)
    topic_resp.encoding = 'windows-1251'
    magnet_tag = BeautifulSoup(topic_resp.text, 'html.parser').select_one("a.magnet-link")
    
    if not magnet_tag:
        return None
        
    return enrich_magnet(magnet_tag.get("href"))


def extract_clip_from_magnet(magnet_link, start_time="00:20:00", duration="00:00:30", output_file="clip.mp4"):
    port = get_free_port()
    stream_url = f"http://127.0.0.1:{port}/"
    
    print("\n🚀 Запускаем торрент-движок (Peerflix)...")
    print(f"🔌 Локальный порт: {port}")
    print("📝 Лог: peerflix_debug.log")
    
    log_file = open("peerflix_debug.log", "w", encoding="utf-8")
    
    peerflix = subprocess.Popen(
        ["peerflix", magnet_link, "--port", str(port)],
        stdout=log_file,
        stderr=subprocess.STDOUT
    )
    
    print("⏳ Соединяемся с пирами (до 90 сек)...")
    server_ready = False
    
    try:
        for _ in range(90):
            if peerflix.poll() is not None:
                print(f"\n❌ ОШИБКА: Peerflix упал (Код {peerflix.returncode}). Смотри логи.")
                return

            try:
                urllib.request.urlopen(urllib.request.Request(stream_url, method='HEAD'), timeout=2)
                server_ready = True
                break
            except Exception:
                time.sleep(1)
                
        if not server_ready:
            print("\n❌ ОШИБКА: Торрент так и не ответил. Смотри peerflix_debug.log")
            return

        print("\n✅ Метаданные получены! Начинаем буферизацию...")
        time.sleep(3) 
        
        print(f"🎥 Режем видео ({duration}) начиная с {start_time}...")
        
        ffmpeg_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", 
            "-ss", start_time, "-i", stream_url, "-t", duration,
            "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-y", output_file
        ]

        result = subprocess.run(ffmpeg_cmd)
        
        if result.returncode == 0:
            print(f"\n🎉 УСПЕХ! Видеофрагмент сохранен: {os.path.abspath(output_file)}")
        else:
            print("\n❌ ОШИБКА FFmpeg: Не удалось обрезать видео. Возможно, поврежден поток.")

    except KeyboardInterrupt:
        print("\n🛑 Процесс прерван.")
    finally:
        print("🧹 Очистка...")
        peerflix.terminate()
        try: peerflix.wait(timeout=3)
        except subprocess.TimeoutExpired: peerflix.kill()
        if not log_file.closed: log_file.close()

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    
    magnet = get_magnet(USERNAME, PASSWORD, query)
    
    if magnet:
        print("\n" + "="*60)
        print("🔗 МАГНЕТ-ССЫЛКА (для проверки в qBittorrent):")
        print(magnet)
        print("="*60 + "\n")

        extract_clip_from_magnet(magnet, start_time="00:20:00", duration="00:00:30", output_file="my_movie_clip.mp4")