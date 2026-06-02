import gzip
import csv
import sys

csv.field_size_limit(sys.maxsize)

EPISODE_FILE = "dev_archive/raw_data/title.episode.tsv.gz"
BASICS_FILE = "dev_archive/raw_data/title.basics.tsv.gz"

print("🔍 Searching IMDb raw files for the truth...\n")

# 1. Check Basics (What type are they?)
with gzip.open(BASICS_FILE, 'rt', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    for row in reader:
        if row['tconst'] in ['tt28429213', 'tt29084212']:
            print(f"🎬 ID: {row['tconst']}")
            print(f"   Name: {row['primaryTitle']}")
            print(f"   Type: {row['titleType']}  <-- This is what IMDb says!")
            print("-" * 40)

# 2. Check Episodes (Are they linked?)
with gzip.open(EPISODE_FILE, 'rt', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter='\t')
    for row in reader:
        if row['tconst'] == 'tt29084212':
            print(f"📺 Episode Link Found!")
            print(f"   Episode ID: {row['tconst']}")
            print(f"   Belongs to Parent Show: {row['parentTconst']}")
            print(f"   Season {row['seasonNumber']}, Ep {row['episodeNumber']}")
            print("-" * 40)