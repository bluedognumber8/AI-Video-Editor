import sqlite3
import gzip
import csv
import os
import sys

# FIX FOR THE CSV CRASH: Allow massive fields
csv.field_size_limit(sys.maxsize)

DB_PATH = "movies_master.sqlite"
EPISODE_FILE = "dev_archive/raw_data/title.episode.tsv.gz"
BASICS_FILE = "dev_archive/raw_data/title.basics.tsv.gz"
AKAS_FILE = "dev_archive/raw_data/title.akas.tsv.gz"

def fix_tv_shows():
    if not os.path.exists(DB_PATH):
        print(f"❌ DB not found: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. Get ALL IDs from the database (Because some episodes are wrongly marked as movies!)
    cur.execute("SELECT imdb_id FROM movies")
    db_ids = {row[0] for row in cur.fetchall()}
    
    print(f"🔍 Found {len(db_ids)} total records in DB. Checking which ones are actually TV episodes...")

    # 2. Map episode ID -> Parent Show ID, Season, and Episode
    episodes_data = {}
    if os.path.exists(EPISODE_FILE):
        print(f"📂 Reading {EPISODE_FILE} (Finding mislabeled episodes)...")
        with gzip.open(EPISODE_FILE, 'rt', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                tconst = row.get('tconst')
                if tconst in db_ids:
                    episodes_data[tconst] = {
                        "parent": row.get('parentTconst'),
                        "s": row.get('seasonNumber', '0'),
                        "e": row.get('episodeNumber', '0')
                    }
    else:
        print(f"❌ Missing {EPISODE_FILE}. Cannot map episodes.")
        return

    parent_ids = {data["parent"] for data in episodes_data.values() if data["parent"]}
    print(f"🔗 Found {len(episodes_data)} TV Episodes in your DB! They belong to {len(parent_ids)} unique TV Shows.")

    # 3. Get Original Titles for the Parent Shows
    parent_titles = {}
    if os.path.exists(BASICS_FILE):
        print(f"📂 Reading {BASICS_FILE} (Fetching show names)...")
        with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                tconst = row.get('tconst')
                if tconst in parent_ids:
                    parent_titles[tconst] = {
                        "original": row.get('originalTitle') or row.get('primaryTitle'),
                        "ru": None
                    }
    
    # 4. Get Localized Russian Titles for the Parent Shows
    if os.path.exists(AKAS_FILE):
        print(f"📂 Reading {AKAS_FILE} (Fetching Russian translations - no crashing this time)...")
        with gzip.open(AKAS_FILE, 'rt', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                titleId = row.get('titleId')
                if titleId in parent_titles and row.get('region') == 'RU':
                    parent_titles[titleId]["ru"] = row.get('title')

    # 5. Update the Database! (Fix Type, Season, Episode, AND Title)
    print("💾 Saving fixes to database...")
    updates = 0
    for ep_id, data in episodes_data.items():
        parent_id = data["parent"]
        if parent_id in parent_titles:
            orig_title = parent_titles[parent_id]["original"]
            ru_title = parent_titles[parent_id]["ru"] or orig_title
            
            # Clean up season/episode numbers safely
            season = int(data["s"]) if data["s"] and data["s"].isdigit() else 0
            episode = int(data["e"]) if data["e"] and data["e"].isdigit() else 0

            cur.execute("""
                UPDATE movies 
                SET type = 'tv',
                    title_original = ?, 
                    title_ru = ?,
                    season = ?,
                    episode = ?
                WHERE imdb_id = ?
            """, (orig_title, ru_title, season, episode, ep_id))
            updates += 1

    conn.commit()
    conn.close()
    print(f"\n✅ SUCCESS! Fully repaired {updates} TV episodes.")
    print("They are now correctly marked as 'tv', have the correct Season/Episode numbers, and bear the real TV Show name!")

if __name__ == "__main__":
    fix_tv_shows()