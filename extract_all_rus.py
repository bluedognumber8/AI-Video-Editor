#!/usr/bin/env python3
import sqlite3
import zipfile
import io
import os

def extract_all(db_path, output_dir='./rus_subs'):
    os.makedirs(output_dir, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT num, name, content FROM zipfiles")
    total = 0
    
    for num, name, content in cursor.fetchall():
        try:
            # Create folder for each subtitle
            sub_dir = f"{output_dir}/{num}"
            os.makedirs(sub_dir, exist_ok=True)
            
            # Extract ZIP from BLOB
            with zipfile.ZipFile(io.BytesIO(content), 'r') as z:
                z.extractall(sub_dir)
            
            total += 1
            if total % 100 == 0:
                print(f"Extracted {total} subtitles...")
                
        except Exception as e:
            print(f"Failed {num}: {e}")
    
    print(f"Done! Extracted {total} subtitles to {output_dir}/")
    conn.close()

if __name__ == "__main__":
    extract_all("rus.db")
