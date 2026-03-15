#!/bin/bash

echo "🧹 Cleaning up project directory..."

# 1. Create necessary directories
mkdir -p dev_archive/data_scripts
mkdir -p dev_archive/raw_data
mkdir -p dev_archive/old_dbs
mkdir -p logs
mkdir -p temp
mkdir -p clips

# 2. Move data scraping / DB building scripts
mv 0*.py check_*.py extract*.py fix_*.py setup_*.py test*.py transfer_*.py update_*.py enrich_*.py add_*.py dev_archive/data_scripts/ 2>/dev/null

# 3. Move raw TSV and txt data
mv *.tsv.gz subtitles_all.txt tree.md dev_archive/raw_data/ 2>/dev/null

# 4. Move intermediate / old databases (Keep movies_master.sqlite!)
mv dbs/ chroma_db/ rus.db opensubs.db config.db dev_archive/old_dbs/ 2>/dev/null

# 5. Move messy logs and temp torrents to their new homes
mv torrserver_debug_*.log logs/ 2>/dev/null
mv temp_*.torrent temp/ 2>/dev/null
mv clips/*_log.txt logs/ 2>/dev/null

echo "✅ Cleanup complete! All dev files safely moved to 'dev_archive/'."
echo "⚠️ Make sure you DO NOT commit 'dev_archive', 'logs', 'temp', or 'clips' to git."