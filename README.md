# AI Video Editor — Smart Meme Downloader

A local system for searching funny phrases, quotes, and memes across a massive movie/TV subtitle database, with instant video clip downloading via torrents, YouTube, or RuTube.

No need to store terabytes of movies locally. Just type a phrase, pick a result, and get a ready-to-use `.mp4` clip.

---

## Architecture

The project has two independent parts: **Data Pipeline** (building the subtitle database) and **Search & Download Engine** (the daily-driver tooling).

### 1. Data Pipeline ("The Brain")

Scrapes, cleans, and indexes subtitle data from public sources into a local SQLite database.

| Step | Script | Purpose |
|---|---|---|
| 1 | `01_extract_ultimate.py` | Extracts text from the main 120 GB OpenSubtitles dump (`opensubs.db`) using a subtitle map file |
| 2 | `01b_extract_new_ultimate.py` | Finds newer dumps (2023–2026), parses `.nfo` files, loads fresh subtitles |
| 3 | `02_enrich_metadata.py` | Offline enrichment — reads IMDb `title.basics` / `akas` / `ratings` datasets, assigns correct Cyrillic titles and ratings |
| 4 (opt.) | `02c_fetch_countries_tmdb.py` | Pulls exact production countries (RU, SU, US, etc.) from TMDB API (VPN required) |

**Key features:**
- **Smart segmentation** — Subtitles are merged using a sliding-window algorithm (15 sec window + silence detection > 3 sec + 2-phrase semantic overlap)
- **FTS5 full-text search** — SQLite FTS5 index for fast, fuzzy, Russian-language-aware text search
- **Format filtering** — Strips irrelevant content (Shorts, Adult, Documentary, News) at ingest time

The final output is `movies_master.sqlite`, a self-contained database with movies, TV shows, subtitles, ratings, genres, and countries.

### 2. Search & Download Engine ("The Muscles")

Two interfaces, one backend:

#### Web UI (Streamlit)
```bash
./start.sh                    # or: streamlit run app.py
```
- Password-protected web interface
- Search by word/phrase across the entire subtitle database
- Smart filters: rating, genre, country, media type (movie/TV)
- AI-assisted search: LLM-powered semantic query expansion (via OpenRouter)
- Results with title, year, rating, genre, country, subtitle snippet with highlighting
- Adjustable clip timing (padding before/after the phrase)
- Download queue with progress tracking
- Favorites / bookmarking system
- Search history
- Context viewer — see surrounding subtitles for any result

#### CLI Master Controller
```bash
python 03_search_and_download.py
```
- Interactive terminal interface
- Same search capabilities as the web UI
- Configurable timing offsets before/after the spoken phrase

#### Download Backend (`magnet_get.py`)
The unified download engine supports three sources in priority order:

1. **YouTube** — via `yt-dlp`, extracts clips on-the-fly
2. **RuTube** — via `yt-dlp`
3. **Torrents (RuTracker)** — via `TorrServer` + `ffmpeg`
   - Logs in to RuTracker, searches for releases
   - Parses `.torrent` files to pick the correct episode for TV shows
   - Streams only the needed byte range (no full download)
   - Fallback logic: 90-second timeout, retry with next seeder
   - Tracker block evasion support

---

## Quick Start

### Prerequisites
- Python 3.10+
- [TorrServer](https://github.com/YouRoK/TorrServer) (for torrent downloads — installed automatically)
- `ffmpeg` (system package)
- `yt-dlp` (installed via pip)

### Installation
```bash
python install.py
```
This will:
1. Create a Python virtual environment (`venv/`)
2. Install all dependencies (see `requirements.txt`)
3. Download and configure TorrServer
4. Create launcher scripts (`start.sh` / `start.bat`)
5. Create required directories (`clips/`, `logs/`, `temp/`)

### Configuration
Copy `.env.example` to `.env` and fill in:
```env
# Required for torrent downloads
RUTRACKER_USERNAME=your_username
RUTRACKER_PASSWORD=your_password

# Required for AI-assisted search (OpenRouter)
OPENROUTER_API_KEY=your_api_key

# TorrServer path (auto-configured by installer)
TORRSERVER_PATH=torrserver
```

### Building the Database
If `movies_master.sqlite` does not exist, run the pipeline in order:

```bash
python 01_extract_ultimate.py        # Step 1: extract from main dump
python 01b_extract_new_ultimate.py   # Step 2: extract from newer dumps
python 02_enrich_metadata.py          # Step 3: enrich with IMDb metadata
python 02c_fetch_countries_tmdb.py    # Step 4 (optional): TMDB country data
```

### Daily Use
```bash
# Web interface
./start.sh

# Or CLI
python 03_search_and_download.py
```

1. Enter a word or phrase (e.g., "кофе", "ложь")
2. Apply filters (genre, country, rating, media type)
3. Pick a quote from the results
4. Adjust timing (seconds before/after the phrase)
5. The engine downloads a ready `.mp4` clip into `clips/`

---

## Project Structure

| File | Purpose |
|---|---|
| `app.py` | Streamlit web UI (1088 lines) — main user interface |
| `magnet_get.py` | Download engine — YouTube, RuTube, torrent clip extraction |
| `ai_agent.py` | LLM integration — OpenRouter-powered semantic query expansion |
| `auth.py` | Simple password authentication for Streamlit |
| `install.py` | One-click environment setup and dependency installer |
| `02c_fetch_countries_tmdb.py` | TMDB country metadata enrichment |
| `fix_tvshows_titles.py` | Utility to fix TV show title formatting |
| `test_imdb_bug.py` | Test script for IMDb data edge cases |
| `03_search_and_download.py` | CLI master controller |
| `settings.json` | BitTorrent engine configuration |
| `requirements.txt` | Python dependencies |
| `clips/` | Downloaded video output directory |
| `logs/` | Application logs |

---

## Tech Stack

- **Frontend**: Streamlit
- **Database**: SQLite with FTS5 full-text search
- **AI**: OpenRouter API (multi-model fallback — Llama, Gemini, DeepSeek, Qwen, etc.)
- **Video Download**: yt-dlp, ffmpeg, TorrServer, peerflix
- **Auth**: Simple .env password check
- **Torrent**: RuTracker scraping, bencodepy parsing, BitTorrent streaming

---

## Roadmap

- **Phase 1: Semantic Vector Search** — ChromaDB + SentenceTransformers for meaning-based (not keyword) scene search
- **Phase 2: AI Director** — faster-whisper speech-to-text + local LLM for raw video analysis and automatic meme detection
- **Phase 3: GUI Enhancements** — Streamlit UI with movie posters, favorites collections, Adobe Premiere Pro XML timeline export
