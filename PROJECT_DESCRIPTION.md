# AI Video Editor — Project Description

> **Also known as**: Smart Meme Downloader / AI-Режиссер Монтажа
> **Purpose**: Search a massive database of movie & TV subtitles for specific words/phrases, then instantly download the corresponding video clips as `.mp4` — without storing full movies locally.

---

## 🧠 Core Concept

A local self-contained system with two independent pipelines:

1. **Data Pipeline** — Scrapes, cleans, indexes subtitle data from public sources into a local SQLite database with FTS5 full-text search.
2. **Search & Download Engine** — Provides a web UI (Streamlit) and CLI to search subtitles, find matching scenes, and clip them from YouTube / RuTube / torrents.

---

## 📊 Database (`movies_master.sqlite`)

| Metric | Value |
|---|---|
| **File size** | ~9.9 GB |
| **Total movies/TV shows** | ~96,807 |
| **Distinct titles with subtitles** | ~96,803 |
| **Subtitle entries** | ~24,280,750 |
| **Movies (type=movie)** | ~27,220 |
| **TV shows (type=tv)** | ~69,587 |
| **Year range** | 0–2027 |
| **Titles with rating ≥ 8.0** | ~26,997 |
| **Russian/Soviet productions (RU/SU)** | ~3,507 |

### Schema

```sql
-- Main movies table
CREATE TABLE movies (
    imdb_id TEXT PRIMARY KEY,
    tmdb_id INTEGER,
    type TEXT,               -- 'movie' | 'tv'
    title_ru TEXT,           -- Russian title
    title_original TEXT,     -- Original title
    year INTEGER,
    genres TEXT,             -- e.g. "Comedy,Drama,Romance"
    countries TEXT,          -- e.g. "US, GB, RU"
    rating REAL,             -- IMDb rating 0.0–10.0
    season INTEGER,          -- For TV shows
    episode INTEGER,         -- For TV episodes
    poster_url TEXT
);

-- Subtitles table
CREATE TABLE subtitles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imdb_id TEXT,            -- FK to movies
    start_time TEXT,         -- SRT format: "00:01:23,456"
    end_time TEXT,           -- SRT format: "00:01:26,789"
    text TEXT                -- Subtitle text (Russian)
);

-- Full-text search index (FTS5)
CREATE VIRTUAL TABLE subtitles_fts USING fts5(
    text,
    content='subtitles',
    content_rowid='id'
);

-- Favorites (user bookmarks)
CREATE TABLE favorites (
    imdb_id TEXT,
    sub_id INTEGER,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(imdb_id, sub_id)
);

-- Search history
CREATE TABLE search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT,
    mode TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Source bindings (manual YouTube/torrent source overrides)
CREATE TABLE movie_bindings (
    imdb_id TEXT PRIMARY KEY,
    source_id TEXT
);

-- Timing offsets (sync correction per source)
CREATE TABLE source_offsets (
    source_id TEXT PRIMARY KEY,
    offset_sec REAL
);
```

### Indexes

- `idx_subtitles_imdb` — on `subtitles(imdb_id)`
- Primary key indexes on `movies`, `favorites`, `movie_bindings`, `source_offsets`

### Top Countries

| Country | Count |
|---|---|
| US | 40,884 |
| Unknown | 14,160 |
| GB | 4,950 |
| JP | 3,116 |
| SU (Soviet Union) | 2,049 |
| GB, US | 1,737 |
| RU | 1,173 |

### Top Genres

| Genre | Count |
|---|---|
| Comedy | 7,546 |
| Drama | 7,337 |
| Crime, Drama, Mystery | 6,197 |
| Action, Adventure, Animation | 4,670 |
| Comedy, Drama | 4,431 |

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   DATA PIPELINE (Build DB)                    │
│                                                                │
│  01_extract_ultimate.py      ← 120GB OpenSubtitles dump      │
│  01b_extract_new_ultimate.py ← 2023–2026 .nfo dumps          │
│  02_enrich_metadata.py       ← IMDb title.basics/akas/ratings│
│  02c_fetch_countries_tmdb.py ← TMDB API (optional, VPN)      │
│  fix_tvshows_titles.py       ← Fix TV episode classification│
│                  ↓                                              │
│          movies_master.sqlite (9.9 GB)                        │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                SEARCH & DOWNLOAD ENGINE                       │
│                                                                │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────────┐ │
│  │  Web UI      │    │  CLI         │    │  AI Agent       │ │
│  │  app.py      │    │  03_search_  │    │  ai_agent.py    │ │
│  │  (Streamlit) │    │  and_download│    │  (OpenRouter)   │ │
│  │  + auth.py   │    │  .py         │    │                 │ │
│  └──────┬───────┘    └──────┬───────┘    └────────┬────────┘ │
│         │                   │                      │          │
│         └──────────┬────────┴──────────────────────┘          │
│                    ↓                                          │
│         ┌─────────────────────┐                               │
│         │    magnet_get.py    │  ← Unified download engine    │
│         └──────┬──────┬───────┘                               │
│                │      │                                       │
│     ┌──────────┘      └──────────────┐                        │
│     ↓                                ↓                        │
│  YouTube / RuTube              TorrServer + ffmpeg            │
│  (yt-dlp)                      (RuTracker torrents)           │
│                                                                │
│     ↓                                ↓                        │
│              clips/  ← ready .mp4 files                       │
└──────────────────────────────────────────────────────────────┘
```

---

## 🔧 Components Detail

### 1. `app.py` (Streamlit Web UI) — 1,082 lines

The main user interface. All features accessible from a single-page app with tabs.

**Pages/Tabs:**
- **🔍 Search Results** — Displays search results with movie info, highlighted quotes, context viewer, download button, sync correction, source switching
- **⭐ Favorites** — Bookmarked moments (heart system)
- **🕰 Search History** — Clickable previous searches
- **🧠 Prompt Lab** — Edit/experiment with AI prompt strategies

**Search Modes:**
- **Fast Mode (По словам)** — Direct SQLite FTS5 search with Russian morphology (pymorphy3 + SnowballStemmer)
- **AI Agent Mode (RAG Pipeline)** — LLM-powered query expansion → FTS5 search → LLM result ranking

**Filters:**
| Filter | Options |
|---|---|
| Media type | Все / Фильмы / Сериалы |
| Country | Все / Наше (RU/SU) / Зарубежное |
| Genre | Любой / Comedy / Drama / Action / Sci-Fi / Horror / Romance / Crime / Animation |
| Min rating | Slider 0.0–10.0 |
| Specific movie | Dropdown with all 96K titles |
| Exact phrase toggle | Checkbox or wrap query in `"..."` |
| Timing padding | Seconds before/after the phrase (download) |
| Source preference | All / YouTube only / RuTube only / Torrent only |

**Key UX Features:**
- Sticky search bar at top
- Staggered loading (10 results per page, "Show more" button)
- Auto-updating download manager (fragment-based polling)
- Inline context dialog showing surrounding subtitles ±45s
- Desync correction tool — search by heard phrase or manual offset slider
- Source switching — find alternative videos for a movie
- Keyword highlighting in results (golden background)
- Secure password auth (`.env` based)

**Download Manager:**
- Max 5 concurrent downloads
- Real-time status from log tailing (encoding progress, peer status)
- Download, preview, and cleanup inside the UI
- Restart after offset correction

### 2. `ai_agent.py` (LLM Integration) — 226 lines

**Provider**: OpenRouter API (multi-model fallback chain) + optional custom OpenAI-compatible endpoint

**Fallback Model Chain** (21 models tried in order):
Hermes-3-405B → GPT-OSS-120B → Nemotron-3 → Llama-3.3-70B → Qwen3 → DeepSeek-R1 → DeepSeek-V3 → Gemini-2.5-Pro → Gemma-3-27B → Mistral-Small-3.1 → ... → openrouter/auto

**Two AI Functions:**

1. **`generate_search_queries(query_text)`** — Query Expansion
   - Takes a user's natural description (e.g. "someone sneezes")
   - Uses a **user-editable prompt strategy** (stored in `prompts_history.json`)
   - Generates 50–70 short search phrases (1–6 words) likely to match actual subtitles
   - Responses forced to JSON array format
   - Current active prompt: тест11(GOOD) — generates 50–70 real movie quotes by association

2. **`rank_database_results(user_query, fts_results)`** — Result Reranking
   - Receives up to 200 candidate results from FTS5 search
   - An LLM (acting as "film editor") scores each by relevance to the user's intent
   - Returns best matching IDs; non-AI results appended as fallback
   - Filters out coincidental word matches (e.g. "puddle of blood" vs "fell in a puddle")

**Prompt Management:**
- `prompts_history.json` — stores 12+ prompt strategy versions
- Users edit prompts live in the UI (Prompt Lab tab)
- JSON format suffix automatically appended by script
- Last working model cached in `last_ai_model.json`

### 3. `magnet_get.py` (Download Engine) — 770 lines

Unified CLI downloader with 3 source types:

**Source Priority (configurable):**
1. **YouTube** → `yt-dlp` search + section download
2. **RuTube** → REST API search + `yt-dlp` section download
3. **Torrent (RuTracker)** → Web scraping + TorrServer streaming + ffmpeg clip extraction

**Torrent Pipeline (most complex):**
1. Login/restore session on `rutracker.org` via `requests` + BeautifulSoup
2. Search for torrents matching movie title, year, season/episode
3. Score/rank torrents by: seeds, resolution (1080p > 720p), source (WEB-DL), file size penalty
4. Download `.torrent` file (with authorization check)
5. **Smart episode matching** — Parse `.torrent` with bencodepy; match by `SxxExx` pattern or season folder; refuses to guess (safety)
6. Start TorrServer instance (or connect to existing system instance on port 8090)
7. Add torrent, identify correct file index
8. FFmpeg HTTP range request → clip output (libx264 + aac)

**Architectural highlights:**
- No full torrent download — only streams needed byte ranges
- Temp files cleaned up after each request
- Path traversal prevention
- Fallback: tries top-3 torrents, 90-second timeouts
- Tracker block evasion with multiple domains

### 4. `auth.py` (Authentication) — 69 lines

Simple password gate for Streamlit:
- Reads `APP_PASSWORD` from `.env` (default: `nazarov`)
- Styled login card with gradient background
- Session state persistence
- Enter key support via `st.form`

### 5. `install.py` (Installer) — 170 lines

One-shot setup:
- Creates venv + installs pip dependencies
- Downloads NLTK language models
- Installs system packages (ffmpeg, TorrServer via yay on Arch)
- Creates launcher scripts (start.sh / start.bat, update scripts)
- Configures `.env` with TorrServer path
- Architecture-aware (Arch auto-detected)

### 6. `02c_fetch_countries_tmdb.py` (Data Enrichment) — 136 lines

Batch enriches movies with production country data from TMDB API:
- Uses IMDb ID → TMDB find endpoint → fetch production countries
- Smart batching (saves every 50 records)
- Stop-crash: halts after 10 consecutive errors (VPN protection)
- Menu: movies only / TV only / all

### 7. `fix_tvshows_titles.py` (Data Fixer) — 104 lines

Post-processing tool that:
- Reads IMDb `title.episode.tsv.gz` to identify which records are TV episodes
- Corrects season/episode numbers for shows mislabeled as movies
- Updates Russian titles from IMDb `title.akas.tsv.gz`

---

## 🎯 Data Pipeline (Building the Database)

The pipeline is NOT included in this repository (scripts must be run in order):

| Step | Script | Input | Output |
|---|---|---|---|
| 1 | `01_extract_ultimate.py` | 120GB OpenSubtitles dump | Extracted subtitles |
| 2 | `01b_extract_new_ultimate.py` | 2023–2026 `.nfo` dumps | Fresh subtitles |
| 3 | `02_enrich_metadata.py` | IMDb `title.basics` / `akas` / `ratings` TSV | Movies with Cyrillic titles, ratings |
| 4 | `02c_fetch_countries_tmdb.py` | TMDB API | Production countries |

**Smart segmentation algorithm** during extraction:
- Sliding window (15 sec)
- Silence detection (>3 sec gap → split)
- 2-phrase semantic overlap
- Strips: Shorts, Adult, Documentary, News

---

## 🚀 Daily Usage Flow

```
1. User types a phrase: "кофе" or "I'll be back"
2. Optionally: enable exact match ("кавычки"), AI agent, filters
3. Search:
   - Fast: FTS5 query with Russian morphology (pymorphy3 lemmatization + Snowball stemming)
   - AI: LLM generates 50-70 related phrases → FTS5 search → LLM reranks top 200
4. Results shown: movie title, year, rating, genre, country, highlighted subtitle text, timestamp
5. User adjusts padding (±30 sec default) and sync offset if needed
6. Optional: view surrounding context (45 sec window)
7. Click "Download" → magnet_get.py finds source → clip saved to clips/
8. Preview, re-download with corrected offset, or download .mp4
```

---

## 🛠 Tech Stack

| Category | Technology |
|---|---|
| **Frontend** | Streamlit (single-page, tabs, custom CSS) |
| **Database** | SQLite + FTS5 full-text search |
| **Full-text search** | FTS5 with Russian morphology enhancements |
| **Russian NLP** | pymorphy3, NLTK SnowballStemmer |
| **AI / LLM** | OpenRouter API (21 model fallback chain), custom endpoint support |
| **Video sources** | YouTube (yt-dlp), RuTube (API + yt-dlp), RuTracker (TorrServer + ffmpeg) |
| **Torrent** | TorrServer, bencodepy, BeautifulSoup |
| **Auth** | .env-based password, Streamlit session state |
| **Config** | `.env`, `settings.json` (TorrServer), `user_settings.json` (UI prefs) |
| **Installation** | Auto-detects Arch/Ubuntu/macOS/Windows |

---

## 📁 Project Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit web UI — main user interface |
| `ai_agent.py` | LLM integration — OpenRouter-powered semantic query expansion + result ranking |
| `magnet_get.py` | Unified download engine — YouTube, RuTube, torrent clip extraction |
| `auth.py` | Simple password authentication for Streamlit |
| `install.py` | One-click environment setup and dependency installer |
| `02c_fetch_countries_tmdb.py` | TMDB country metadata enrichment |
| `fix_tvshows_titles.py` | Utility to fix TV show title formatting |
| `test_imdb_bug.py` | Test script for IMDb data edge cases |
| `prompts_history.json` | Stored AI prompt strategies (user-editable) |
| `last_ai_model.json` | Caches last working OpenRouter model |
| `user_settings.json` | Last used UI filter settings |
| `settings.json` | TorrServer BitTorrent engine configuration |
| `requirements.txt` | Python dependencies |
| `.env` | Secrets (API keys, credentials) |
| `.env.example` | Template for environment configuration |
| `logs/` | Download task logs, TorrServer debug logs |
| `clips/` | Downloaded video clips output directory |
| `temp/` | Temporary .torrent files (auto-cleaned) |

---

## 🔐 Configuration

```env
# Required for torrent downloads
RUTRACKER_USERNAME=your_username
RUTRACKER_PASSWORD=your_password

# Required for AI-powered search
OPENROUTER_API_KEY=your_api_key

# Optional: Custom OpenAI-compatible endpoint (higher priority than OpenRouter)
CUSTOM_API_BASE_URL=http://192.168.88.17:8080/v1
CUSTOM_MODEL_NAME=deepseek-v4-flash
CUSTOM_API_KEY=

# Authentication for Streamlit UI
APP_PASSWORD=nazarov
SESSION_TIMEOUT=30

# TorrServer path
TORRSERVER_PATH=torrserver
```

---

## 🗺 Roadmap (Planned)

- **Phase 1: Semantic Vector Search** — ChromaDB + SentenceTransformers for meaning-based scene search (beyond keywords)
- **Phase 2: AI Director** — faster-whisper speech-to-text + local LLM for raw video analysis and automatic meme detection
- **Phase 3: GUI Enhancements** — Movie posters, favorites collections, Adobe Premiere Pro XML timeline export

---

## 📝 Notes

- The project name "AI Video Editor" is somewhat aspirational — the current version is primarily a **search-and-clip** tool, not a full video editor.
- The 10GB SQLite database is self-contained and portable.
- No full movies are stored locally — only the resulting `.mp4` clips (typically 30–120 seconds).
- Russian language is first-class: morphology-aware search (pymorphy3 lemmatization), Cyrillic regex, е/ё normalization.
- The AI prompt lab allows continuous refinement of search strategy without code changes.
