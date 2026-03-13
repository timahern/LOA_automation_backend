import csv
import sqlite3
import os
from datetime import date, datetime
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

CSV_PATH = BASE_DIR / "Project_Insurance.csv"
DB_PATH = BASE_DIR / "vendor_insurance.db"
# ──────────────────────────────────────────────────────────────────────────────

INSURANCE_TYPE_MAP = {
    "Umbrella/Excess":      "umbrella_insurance",
    "General Liability":    "general_liability_insurance",
    "Automobile Liability": "automobile_insurance",
    "Workers Compensation": "workers_comp_insurance",
}


def parse_csv(filepath):
    """
    Reads the CSV and groups rows by (project_number, vendor_name).
    For each insurance type, keeps the latest expiration date when there
    are multiple policy rows for the same type.
    Vendors with no insurance data get all NULL insurance columns.
    """
    records = {}

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            project_number = row["Project > Number"].strip()
            apm            = row["Project > APM"].strip()
            vendor_name    = row["Company (Vendor) > Name"].strip()
            exp_date       = row["Company Project Insurance > Expiration Date"].strip()
            ins_type       = row["Company Project Insurance > Insurance Type"].strip()

            key = (project_number, vendor_name)

            if key not in records:
                records[key] = {
                    "project_number":              project_number,
                    "APM":                         apm,
                    "vendor_name":                 vendor_name,
                    "umbrella_insurance":          None,
                    "general_liability_insurance": None,
                    "automobile_insurance":        None,
                    "workers_comp_insurance":      None,
                    "updated":                     date.today().isoformat(),
                }

            col = INSURANCE_TYPE_MAP.get(ins_type)
            if col and exp_date:
                existing = records[key][col]
                try:
                    new_dt = datetime.strptime(exp_date, "%m/%d/%Y")
                    if existing is None:
                        records[key][col] = exp_date
                    else:
                        existing_dt = datetime.strptime(existing, "%m/%d/%Y")
                        if new_dt > existing_dt:
                            records[key][col] = exp_date
                except ValueError:
                    if existing is None:
                        records[key][col] = exp_date

    return list(records.values())


def init_db(db_path):
    """
    Creates the table if it doesn't exist yet. Only runs DDL on first run.
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_insurance (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_number              TEXT,
            APM                         TEXT,
            vendor_name                 TEXT,
            umbrella_insurance          TEXT,
            general_liability_insurance TEXT,
            automobile_insurance        TEXT,
            workers_comp_insurance      TEXT,
            updated                     TEXT,
            UNIQUE(project_number, vendor_name)
        )
    """)
    conn.commit()
    conn.close()


def upsert_db(records, db_path):
    """
    Upserts records from the current CSV into the DB.
    - Rows in the CSV → inserted (if new) or updated (if existing).
      updated column is set to today's date either way.
    - Rows NOT in the CSV → untouched. Their stale updated date
      indicates they've aged out of the 12-month window.
    """
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()

    inserted = 0
    updated  = 0

    for rec in records:
        existing = cur.execute("""
            SELECT id FROM vendor_insurance
            WHERE project_number = ? AND vendor_name = ?
        """, (rec["project_number"], rec["vendor_name"])).fetchone()

        if existing:
            cur.execute("""
                UPDATE vendor_insurance SET
                    APM                         = :APM,
                    umbrella_insurance          = :umbrella_insurance,
                    general_liability_insurance = :general_liability_insurance,
                    automobile_insurance        = :automobile_insurance,
                    workers_comp_insurance      = :workers_comp_insurance,
                    updated                     = :updated
                WHERE project_number = :project_number
                  AND vendor_name    = :vendor_name
            """, rec)
            updated += 1
        else:
            cur.execute("""
                INSERT INTO vendor_insurance (
                    project_number, APM, vendor_name,
                    umbrella_insurance, general_liability_insurance,
                    automobile_insurance, workers_comp_insurance,
                    updated
                ) VALUES (
                    :project_number, :APM, :vendor_name,
                    :umbrella_insurance, :general_liability_insurance,
                    :automobile_insurance, :workers_comp_insurance,
                    :updated
                )
            """, rec)
            inserted += 1

    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM vendor_insurance").fetchone()[0]
    conn.close()
    return total, inserted, updated


if __name__ == "__main__":
    print(f"[{date.today()}] Starting insurance DB update...")

    print(f"  Parsing CSV: {CSV_PATH}")
    records = parse_csv(CSV_PATH)
    print(f"  Unique (project, vendor) combinations found: {len(records)}")

    print(f"  Initializing database: {DB_PATH}")
    init_db(DB_PATH)

    print(f"  Upserting records...")
    total, inserted, updated = upsert_db(records, DB_PATH)
    print(f"  New rows inserted:     {inserted}")
    print(f"  Existing rows updated: {updated}")
    print(f"  Total rows in DB:      {total}")

    print("Done.")
