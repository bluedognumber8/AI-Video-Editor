import sqlite3
import zipfile
import io
import sys

def extract_subtitle(db_path, movie_name, output_dir='./subs'):
    import os
    os.makedirs(output_dir, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Search for movie
    cursor.execute("""
        SELECT id, name, content, format, movie_name, year 
        FROM zipfiles 
        WHERE movie_name LIKE ? 
        LIMIT 5
    """, (f'%{movie_name}%',))
    
    rows = cursor.fetchall()
    
    for row in rows:
        sub_id, name, content, fmt, mov_name, year = row
        print(f"Found: {mov_name} ({year}) - {name}")
        
        # Save ZIP and extract
        zip_path = f"{output_dir}/{sub_id}.zip"
        with open(zip_path, 'wb') as f:
            f.write(content)
        
        # Extract subtitle from ZIP
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(f"{output_dir}/{sub_id}/")
        
        print(f"Extracted to: {output_dir}/{sub_id}/")
    
    conn.close()

# Usage
extract_subtitle('rus.db', 'Inception')