#Verdict cache for Sub-Chaser 2000 — don't pay to analyze the same unchanged
#invoice twice.
#
#A period's invoices barely change between runs: an APM re-runs the tool through
#the month to see who has finally sent their paperwork in, and most rows are
#identical every time. Re-reading all of them costs a vision call apiece for an
#answer we already had.
#
#So: keep the verdict, keyed by the invoice AND a fingerprint of its current
#state (sub_chaser/freshness.py). On the next run we re-fingerprint; same
#fingerprint means nothing was submitted or removed since, so the stored verdict
#still stands and we skip both the PDF compile and the AI call.
#
#WHY A FINGERPRINT AND NOT A TIMESTAMP: Procore does NOT bump a requisition's
#updated_at when an attachment is added or removed — proven, see the PROVEN note
#in freshness.py. A last-modified comparison would never fire, and the cache
#would serve a stale "hasn't sent it in" verdict forever. Don't switch it back.
#
#Follows the same shape as billing_matrix_automation's insurance_to_db.py:
#a plain sqlite file with init/upsert, no ORM.

import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone

from sub_chaser.freshness import PROMPT_VERSION

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "sub_chaser_verdicts.db")

#analysis runs on a ThreadPoolExecutor, so several threads write at once. Each
#call opens its own connection (a sqlite connection is not safely shared across
#threads) and writes are serialized through this lock on top of WAL, which
#already allows concurrent readers alongside one writer.
_WRITE_LOCK = threading.Lock()

_INIT_LOCK = threading.Lock()
_initialized = False


def _connect(db_path=None):

    #ALWAYS use this inside `with closing(...)`. sqlite3's own connection
    #context manager commits the transaction but leaves the connection OPEN,
    #which leaks a file handle per call — and with six analysis threads hitting
    #this per invoice, that adds up fast.

    connection = sqlite3.connect(db_path or DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    #readers don't block on the writer, which matters with 6 analysis threads
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init_db(db_path=None):

    #create the table if it isn't there. Safe to call repeatedly.

    with closing(_connect(db_path)) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS verdicts (
                company_id      TEXT NOT NULL,
                project_id      TEXT NOT NULL,
                requisition_id  TEXT NOT NULL,
                prompt_version  INTEGER NOT NULL,
                fingerprint     TEXT NOT NULL,
                aia             TEXT NOT NULL,
                lien            TEXT NOT NULL,
                confidence      REAL,
                notes           TEXT,
                --diagnostic only, deliberately NOT part of the key: it answers
                --"which model said this?" when a verdict looks wrong
                model           TEXT,
                analyzed_at     TEXT NOT NULL,
                PRIMARY KEY (company_id, project_id, requisition_id, prompt_version)
            )
        """)


def _ensureInit(db_path=None):
    global _initialized
    if _initialized:
        return
    with _INIT_LOCK:
        if not _initialized:
            init_db(db_path)
            _initialized = True


def isCacheable(verdict):

    #Only confident yes/no answers are worth keeping.
    #
    #An "uncertain" is nearly always transient — the PDF was too big, the API
    #call failed, a scan was illegible, the model declined. Storing that would
    #freeze a non-answer in place and it would never be retried, which is the
    #one way this cache could actively make the tool worse. Uncertain rows are
    #re-analyzed every run, on purpose.

    if not verdict:
        return False
    return verdict.get("aia") in ("yes", "no") and verdict.get("lien") in ("yes", "no")


def getVerdict(company_id, project_id, requisition_id, fingerprint,
               prompt_version=PROMPT_VERSION, db_path=None):

    #the stored verdict IF it was produced from this exact invoice state.
    #Returns None on a miss, a changed fingerprint, or any error — a broken
    #cache must degrade to "analyze it again", never to a wrong answer.

    try:
        _ensureInit(db_path)
        with closing(_connect(db_path)) as connection:
            row = connection.execute("""
                SELECT fingerprint, aia, lien, confidence, notes, model, analyzed_at
                FROM verdicts
                WHERE company_id = ? AND project_id = ? AND requisition_id = ?
                  AND prompt_version = ?
            """, (str(company_id), str(project_id), str(requisition_id),
                  int(prompt_version))).fetchone()

        if row is None:
            return None

        #the invoice changed since we judged it — attachments added or removed
        if row["fingerprint"] != fingerprint:
            return None

        return {
            "aia": row["aia"],
            "lien": row["lien"],
            "confidence": row["confidence"],
            "notes": row["notes"],
            "model": row["model"],
            "analyzed_at": row["analyzed_at"],
        }

    except Exception as e:
        print(f"[verdict_cache] read failed, treating as miss: {e}")
        return None


def putVerdict(company_id, project_id, requisition_id, fingerprint, verdict,
               model=None, prompt_version=PROMPT_VERSION, db_path=None):

    #store a verdict against the invoice state that produced it. Uncacheable
    #(uncertain) verdicts are dropped. Never raises — failing to cache is a cost
    #problem, not a correctness one, and must not sink a running job.

    if not isCacheable(verdict):
        return False

    try:
        _ensureInit(db_path)
        with _WRITE_LOCK:
            with closing(_connect(db_path)) as connection, connection:
                connection.execute("""
                    INSERT INTO verdicts (company_id, project_id, requisition_id,
                                          prompt_version, fingerprint, aia, lien,
                                          confidence, notes, model, analyzed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_id, project_id, requisition_id, prompt_version)
                    DO UPDATE SET
                        fingerprint = excluded.fingerprint,
                        aia         = excluded.aia,
                        lien        = excluded.lien,
                        confidence  = excluded.confidence,
                        notes       = excluded.notes,
                        model       = excluded.model,
                        analyzed_at = excluded.analyzed_at
                """, (
                    str(company_id), str(project_id), str(requisition_id),
                    int(prompt_version), fingerprint,
                    verdict.get("aia"), verdict.get("lien"),
                    verdict.get("confidence"), verdict.get("notes"),
                    model, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ))
        return True

    except Exception as e:
        print(f"[verdict_cache] write failed: {e}")
        return False


def stats(company_id=None, project_id=None, db_path=None):

    #how many verdicts are stored, for the diagnostics endpoint

    try:
        _ensureInit(db_path)
        query = "SELECT COUNT(*) AS n FROM verdicts"
        params = []
        if company_id and project_id:
            query += " WHERE company_id = ? AND project_id = ?"
            params = [str(company_id), str(project_id)]

        with closing(_connect(db_path)) as connection:
            return {"cached_verdicts": connection.execute(query, params).fetchone()["n"],
                    "db_path": db_path or DB_PATH}

    except Exception as e:
        return {"error": str(e), "db_path": db_path or DB_PATH}


def clear(company_id=None, project_id=None, db_path=None):

    #wipe the cache, or just one project's slice of it. For testing and for the
    #"re-read everything from scratch" escape hatch.

    _ensureInit(db_path)
    query = "DELETE FROM verdicts"
    params = []
    if company_id and project_id:
        query += " WHERE company_id = ? AND project_id = ?"
        params = [str(company_id), str(project_id)]

    with _WRITE_LOCK:
        with closing(_connect(db_path)) as connection, connection:
            return connection.execute(query, params).rowcount
