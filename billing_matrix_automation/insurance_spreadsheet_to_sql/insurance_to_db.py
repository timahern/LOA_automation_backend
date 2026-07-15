import sys
import time
import sqlite3
import os
import requests
from pathlib import Path
from datetime import date, datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from procore_copilot.procore_auth import load_tokens, refresh_access_token

# ── Configuration ──────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent
DB_PATH    = BASE_DIR / "vendor_insurance.db"
COMPANY_ID = "71927"
BASE_URL   = "https://api.procore.com"
# ──────────────────────────────────────────────────────────────────────────────

INSURANCE_TYPE_MAP = {
    "Umbrella/Excess":         "umbrella_insurance",
    "General Liability":       "general_liability_insurance",
    "Automobile Liability":    "automobile_insurance",
    "Workers Compensation":    "workers_comp_insurance",
    "Pollution/Environmental": "pollution_insurance",
}


# ── HTTP helpers (same rate-limit pattern as billing_data_fetcher.py) ──────────

def _headers(tokens):
    return {
        "Authorization": f"Bearer {tokens['access_token']}",
        "Procore-Company-Id": COMPANY_ID,
        "Accept": "application/json",
    }


def _make_request(url, tokens, params=None):
    resp = requests.get(url, headers=_headers(tokens), params=params)

    if resp.status_code == 401:
        print("  Token expired, refreshing...")
        new_tokens = refresh_access_token(tokens)
        tokens.update(new_tokens)
        resp = requests.get(url, headers=_headers(tokens), params=params)

    retries = 0
    while resp.status_code == 429:
        if retries >= 5:
            raise RuntimeError("Procore rate limit hit 5 times in a row — aborting.")
        reset_ts = int(resp.headers.get("X-Rate-Limit-Reset", 0))
        wait = max(reset_ts - int(time.time()), 0) + 2 if reset_ts else 15 * (2 ** retries)
        print(f"  [429] rate limited — waiting {wait}s... (attempt {retries + 1}/5)")
        time.sleep(wait)
        retries += 1
        resp = requests.get(url, headers=_headers(tokens), params=params)

    # Proactively pause if we're about to exhaust the rate-limit window.
    # Cap at 60s — the 429 retry loop handles actual hard limits. Without the
    # cap, Procore's long-window reset timestamp (hourly/daily) causes waits
    # of 1000+ seconds on the proactive check alone.
    remaining = int(resp.headers.get("X-Rate-Limit-Remaining", 999))
    reset_ts  = int(resp.headers.get("X-Rate-Limit-Reset", 0))
    if remaining <= 2 and reset_ts:
        wait = min(max(reset_ts - int(time.time()), 0) + 2, 60)
        print(f"  Rate limit low ({remaining} remaining) — pausing {wait}s until reset...")
        time.sleep(wait)

    return resp


def _fetch_all_pages(url, tokens, params=None):
    """Paginate through a Procore list endpoint and return all results."""
    all_items  = []
    page       = 1
    base_params = dict(params or {})
    base_params["per_page"] = 100

    while True:
        base_params["page"] = page
        resp = _make_request(url, tokens, params=base_params)
        if resp.status_code != 200:
            print(f"  Warning: {url} returned {resp.status_code} on page {page}")
            break
        batch = resp.json()
        if isinstance(batch, dict):
            batch = batch.get("data", [])
        if not batch:
            break
        all_items.extend(batch)
        total = int(resp.headers.get("Total", 0))
        if total and len(all_items) >= total:
            break
        page += 1

    return all_items


# ── Date helper ────────────────────────────────────────────────────────────────

def _iso_to_slash(date_str):
    """'YYYY-MM-DD' or 'YYYY-MM-DDT...' → 'M/D/YYYY'. Returns None if blank."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return f"{dt.month}/{dt.day}/{dt.year}"
    except ValueError:
        return None


# ── Procore data fetchers ──────────────────────────────────────────────────────

def get_all_projects(tokens):
    """Return all active projects across all pages."""
    projects = _fetch_all_pages(
        f"{BASE_URL}/rest/v1.1/projects",
        tokens,
        params={
            "company_id": COMPANY_ID,
            "view": "extended",
            "filters[by_status]": "Active",
        },
    )
    return projects


def get_apms_for_project(project_id, tokens):
    """
    Return (apm_names, apm_emails) for users whose permission template
    starts with 'Assistant Project Manager'.
    Names formatted as 'LastName FirstName', comma-joined for multiples.
    """
    resp = _make_request(
        f"{BASE_URL}/rest/v1.0/projects/{project_id}/users",
        tokens,
        params={"company_id": COMPANY_ID, "per_page": 100},
    )
    if resp.status_code != 200:
        return None, None

    names  = []
    emails = []
    for u in resp.json():
        tmpl = u.get("permission_template") or {}
        tmpl_name = tmpl.get("name", "") if isinstance(tmpl, dict) else str(tmpl)
        if tmpl_name.startswith("Assistant Project Manager"):
            last  = (u.get("last_name")  or "").strip()
            first = (u.get("first_name") or "").strip()
            names.append(f"{last} {first}".strip())
            emails.append(u.get("email_address") or "")

    return (", ".join(names) or None, ", ".join(emails) or None)


def get_vendors_for_project(project_id, tokens):
    """Return [{id, name}] for all vendors on the project."""
    vendors = _fetch_all_pages(
        f"{BASE_URL}/rest/v1.1/projects/{project_id}/vendors",
        tokens,
        params={"company_id": COMPANY_ID},
    )
    return [{"id": v["id"], "name": v.get("name", "")} for v in vendors]


def get_vendor_insurances(project_id, vendor_id, tokens):
    """Return list of insurance records for one vendor on one project."""
    resp = _make_request(
        f"{BASE_URL}/rest/v1.0/projects/{project_id}/vendors/{vendor_id}/insurances",
        tokens,
        params={"company_id": COMPANY_ID, "view": "extended"},
    )
    if resp.status_code != 200:
        return []
    return resp.json()


# ── Main fetch — replaces parse_csv ───────────────────────────────────────────

_FINISH_DATE_FIELDS = [
    "projected_finish_date",
    "completion_date",
    "estimated_completion_date",
]

def _best_finish_date(proj):
    """Return the latest finish date across all three Procore date fields as a date object."""
    candidates = []
    for field in _FINISH_DATE_FIELDS:
        val = proj.get(field)
        if not val:
            continue
        try:
            candidates.append(datetime.strptime(val[:10], "%Y-%m-%d").date())
        except ValueError:
            pass
    return max(candidates) if candidates else None


def _within_window(end_date, cutoff):
    """Return True if end_date is None (unknown) or on/after the cutoff."""
    if end_date is None:
        return True
    return end_date >= cutoff


def fetch_from_procore(tokens):
    records = {}
    today   = date.today().isoformat()

    projects = get_all_projects(tokens)
    print(f"  Active projects fetched: {len(projects)}")

    cutoff = date.today() - timedelta(days=90)
    projects = [
        p for p in projects
        if _within_window(_best_finish_date(p), cutoff)
    ]
    print(f"  Projects within window (end date >= {cutoff}): {len(projects)}")

    for proj in projects:
        project_id     = proj["id"]
        project_number = proj.get("project_number") or ""
        project_name   = proj.get("name") or ""
        best_end       = _best_finish_date(proj)
        proj_end_date  = f"{best_end.month}/{best_end.day}/{best_end.year}" if best_end else None

        print(f"  [{project_number}] {project_name}")

        apm_name, apm_email = get_apms_for_project(project_id, tokens)
        vendors = get_vendors_for_project(project_id, tokens)

        for vendor in vendors:
            vendor_id   = vendor["id"]
            vendor_name = vendor["name"]
            key         = (project_number, vendor_name)

            ins_records = get_vendor_insurances(project_id, vendor_id, tokens)

            if not ins_records:
                continue

            if key not in records:
                records[key] = {
                    "project_number":              project_number,
                    "project_name":                project_name,
                    "proj_end_date":               proj_end_date,
                    "APM":                         apm_name,
                    "APM_email":                   apm_email,
                    "vendor_name":                 vendor_name,
                    "umbrella_insurance":          None,
                    "general_liability_insurance": None,
                    "automobile_insurance":        None,
                    "workers_comp_insurance":      None,
                    "pollution_insurance":         None,
                    "updated":                     today,
                }

            for ins in ins_records:
                col      = INSURANCE_TYPE_MAP.get(ins.get("insurance_type") or "")
                exp_date = _iso_to_slash(ins.get("expiration_date"))
                if not col or not exp_date:
                    continue
                existing = records[key][col]
                if existing is None:
                    records[key][col] = exp_date
                else:
                    try:
                        if (datetime.strptime(exp_date, "%m/%d/%Y") >
                                datetime.strptime(existing, "%m/%d/%Y")):
                            records[key][col] = exp_date
                    except ValueError:
                        pass

    return list(records.values())


# ── DB functions ───────────────────────────────────────────────────────────────

def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_insurance (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_number              TEXT,
            project_name                TEXT,
            proj_end_date               TEXT,
            APM                         TEXT,
            APM_email                   TEXT,
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


def migrate_db(db_path):
    """Adds new columns to an existing DB if they aren't there yet."""
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(vendor_insurance)")}

    new_cols = {
        "project_name":        "TEXT",
        "proj_end_date":       "TEXT",
        "pollution_insurance": "TEXT",
        "APM_email":           "TEXT",
    }
    for col, col_type in new_cols.items():
        if col not in existing_cols:
            cur.execute(f"ALTER TABLE vendor_insurance ADD COLUMN {col} {col_type}")
            print(f"  Migrated: added column '{col}'")

    conn.commit()
    conn.close()


def upsert_db(records, db_path):
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
                    project_name                = :project_name,
                    proj_end_date               = :proj_end_date,
                    APM                         = :APM,
                    APM_email                   = :APM_email,
                    umbrella_insurance          = :umbrella_insurance,
                    general_liability_insurance = :general_liability_insurance,
                    automobile_insurance        = :automobile_insurance,
                    workers_comp_insurance      = :workers_comp_insurance,
                    pollution_insurance         = :pollution_insurance,
                    updated                     = :updated
                WHERE project_number = :project_number
                  AND vendor_name    = :vendor_name
            """, rec)
            updated += 1
        else:
            cur.execute("""
                INSERT INTO vendor_insurance (
                    project_number, project_name, proj_end_date,
                    APM, APM_email, vendor_name,
                    umbrella_insurance, general_liability_insurance,
                    automobile_insurance, workers_comp_insurance,
                    pollution_insurance, updated
                ) VALUES (
                    :project_number, :project_name, :proj_end_date,
                    :APM, :APM_email, :vendor_name,
                    :umbrella_insurance, :general_liability_insurance,
                    :automobile_insurance, :workers_comp_insurance,
                    :pollution_insurance, :updated
                )
            """, rec)
            inserted += 1

    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM vendor_insurance").fetchone()[0]
    conn.close()
    return total, inserted, updated


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[{date.today()}] Starting insurance DB update from Procore...")

    print("  Loading tokens from AWS SSM...")
    tokens = load_tokens()
    print("  Tokens loaded.")

    print(f"  Fetching data from Procore (company {COMPANY_ID})...")
    records = fetch_from_procore(tokens)
    print(f"  Unique (project, vendor) combinations found: {len(records)}")

    print(f"  Initializing database: {DB_PATH}")
    init_db(db_path=DB_PATH)

    print("  Running migrations...")
    migrate_db(db_path=DB_PATH)

    print("  Upserting records...")
    total, inserted, updated = upsert_db(records, DB_PATH)
    print(f"  New rows inserted:     {inserted}")
    print(f"  Existing rows updated: {updated}")
    print(f"  Total rows in DB:      {total}")

    print("Done.")
