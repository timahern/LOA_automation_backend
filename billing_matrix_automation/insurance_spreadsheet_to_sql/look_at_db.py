from pathlib import Path
import sqlite3

BASE_DIR = Path(__file__).resolve().parent
#CSV_PATH = BASE_DIR / "Project_Insurance.csv"
DB_PATH = BASE_DIR / "vendor_insurance.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT * FROM vendor_insurance").fetchall()
for row in rows:
    print(dict(row))

conn.close()