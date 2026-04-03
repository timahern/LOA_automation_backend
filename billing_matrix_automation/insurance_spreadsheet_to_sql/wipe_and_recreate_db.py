import csv
from pathlib import Path
import sqlite3
import os
from datetime import date, datetime

# ── Configuration ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "Project_Insurance.csv"
DB_PATH = BASE_DIR / "vendor_insurance.db"
# ──────────────────────────────────────────────────────────────────────────────

INSURANCE_TYPE_MAP = {
    "Umbrella/Excess":          "umbrella_insurance",
    "General Liability":        "general_liability_insurance",
    "Automobile Liability":     "automobile_insurance",
    "Workers Compensation":     "workers_comp_insurance",
    "Pollution/Environmental":  "pollution_insurance",
}


def parse_csv(filepath):
    records = {}

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            project_number = row["Project > Number"].strip()
            project_name   = row["Project > Name"].strip()
            proj_end_date  = row["Project > Projected Finish Date"].strip()
            apm            = row["Project > APM"].strip()
            vendor_name    = row["Company (Vendor) > Name"].strip()
            exp_date       = row["Company Project Insurance > Expiration Date"].strip()
            ins_type       = row["Company Project Insurance > Insurance Type"].strip()

            key = (project_number, vendor_name)

            if key not in records:
                records[key] = {
                    "project_number":              project_number,
                    "project_name":                project_name,
                    "proj_end_date":               proj_end_date or None,
                    "APM":                         apm,
                    "vendor_name":                 vendor_name,
                    "umbrella_insurance":          None,
                    "general_liability_insurance": None,
                    "automobile_insurance":        None,
                    "workers_comp_insurance":      None,
                    "pollution_insurance":         None,
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


def wipe_and_init_db(db_path):
    """Drops the table entirely and recreates it fresh."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS vendor_insurance")
    conn.execute("""
        CREATE TABLE vendor_insurance (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_number              TEXT,
            project_name                TEXT,
            proj_end_date               TEXT,
            APM                         TEXT,
            vendor_name                 TEXT,
            umbrella_insurance          TEXT,
            general_liability_insurance TEXT,
            automobile_insurance        TEXT,
            workers_comp_insurance      TEXT,
            pollution_insurance         TEXT,
            updated                     TEXT,
            UNIQUE(project_number, vendor_name)
        )
    """)
    conn.commit()
    conn.close()


def populate_db(records, db_path):
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()

    for rec in records:
        cur.execute("""
            INSERT INTO vendor_insurance (
                project_number, project_name, proj_end_date, APM, vendor_name,
                umbrella_insurance, general_liability_insurance,
                automobile_insurance, workers_comp_insurance,
                pollution_insurance, updated
            ) VALUES (
                :project_number, :project_name, :proj_end_date, :APM, :vendor_name,
                :umbrella_insurance, :general_liability_insurance,
                :automobile_insurance, :workers_comp_insurance,
                :pollution_insurance, :updated
            )
        """, rec)

    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM vendor_insurance").fetchone()[0]
    conn.close()
    return total


if __name__ == "__main__":
    print(f"[{date.today()}] Starting full insurance DB rebuild...")

    print(f"  Parsing CSV: {CSV_PATH}")
    records = parse_csv(CSV_PATH)
    print(f"  Unique (project, vendor) combinations found: {len(records)}")

    print(f"  Wiping and recreating database: {DB_PATH}")
    wipe_and_init_db(DB_PATH)

    print(f"  Populating database...")
    total = populate_db(records, DB_PATH)
    print(f"  Total rows inserted: {total}")

    print("Done.")