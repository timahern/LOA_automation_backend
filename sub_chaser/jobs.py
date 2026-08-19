#Analysis jobs for Sub-Chaser 2000.
#
#A job = "analyze every sub invoice in one billing period". It runs on background
#threads so the UI never blocks, and the frontend polls get_job_status() ~1s to
#watch rows fill in.
#
#   start_real_job(...) -> job_id      real fetch + concurrent vision
#   start_mock_job(...) -> job_id      canned verdicts, for frontend work
#   get_job_status(job_id) -> {status, total, done, elapsed, results:[...]}
#
#Each result row moves queued -> reading -> done. The invoice facts (sc number,
#vendor, status, amount) are known from the fetch, so they're filled in from the
#start; only the aia/lien verdict waits on the AI.
#
#Why concurrent: each invoice costs a Procore PDF compile (10-30s, server-side)
#plus a vision call. Sequentially a 12-invoice period would take minutes; run in
#parallel it lands in roughly the slowest single invoice.
#
#Session-held only — nothing is persisted (per the build brief), and the invoice
#PDFs are analyzed then discarded, never stored.

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sub_chaser.procore_fetch import (
    getRequisitionsForPeriod,
    getWorkOrderContractsLite,
    cleanRequisition,
)
from sub_chaser.pdf_compile import compileInvoicePdf
from sub_chaser.vision import analyze
from sub_chaser.recipients import (
    getProjectDirectory, buildDirectoryIndex, resolveRecipients,
)

#procore allows 3,600 req/hr per token and each compile polls every 3s, so keep
#the fan-out modest
MAX_WORKERS = 6

#jobs live in memory for the life of the flask process
_JOBS = {}
_JOBS_LOCK = threading.Lock()

#The merged invoice PDF each sub actually submitted, kept in memory for the life
#of the job so the chase email can attach THEIR paperwork back to them (a sub
#missing a notarized AIA needs their own AIA to sign, not a blank template).
#Still never written to disk — the file of record stays in Procore.
_JOB_PDFS = {}


# ── real job ─────────────────────────────────────────────────────────────────

def start_real_job(company_id, project_id, period_id, tokens):

    #kick off the fetch+analyze in the background and return immediately.
    #`tokens` is a WorkerTokens holding the signed-in user's Procore tokens
    #(captured by the route — flask's session isn't reachable from a thread).

    job_id = uuid.uuid4().hex

    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "status": "running",
            "started_at": time.time(),
            "elapsed": 0,
            "error": None,
            "results": [],
            "tokens": tokens,
            "company_id": company_id,
            "project_id": project_id,
            "period_id": period_id,
        }
        _JOB_PDFS[job_id] = {}

    threading.Thread(
        target=_runJob,
        args=(job_id, company_id, project_id, period_id, tokens),
        daemon=True,
    ).start()

    return job_id


def _runJob(job_id, company_id, project_id, period_id, tokens):
    try:
        #1. the period's invoices + the commitment list to resolve SC numbers
        requisitions = getRequisitionsForPeriod(company_id, project_id, period_id, tokens=tokens)
        if requisitions is None:
            _failJob(job_id, "Could not load invoices for this billing period")
            return

        contracts = getWorkOrderContractsLite(company_id, project_id, tokens=tokens)

        #the whole project directory in ONE call, indexed by company, so each
        #invoice's contacts are a dict lookup rather than a request
        directory_index = {}
        try:
            directory_index = buildDirectoryIndex(
                getProjectDirectory(company_id, project_id, tokens=tokens)
            )
        except Exception as e:
            print(f"[job {job_id}] directory lookup failed: {e}")

        #2. seed a row per invoice so the table renders immediately
        rows = []
        for requisition in requisitions:
            row = cleanRequisition(requisition, contracts)
            row["state"] = "queued"
            #vendor-record fallback only fires for companies the directory
            #didn't cover, so it costs a call per gap rather than per invoice
            row["recipients"] = resolveRecipients(
                directory_index,
                row.get("vendor_id"),
                company_id=company_id,
                project_id=project_id,
                tokens=tokens,
            )
            rows.append(row)

        _updateJob(job_id, results=rows)

        if not rows:
            _finishJob(job_id)
            return

        #3. compile + analyze every invoice concurrently
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for requisition, row in zip(requisitions, rows):
                pool.submit(_analyzeOne, job_id, company_id, project_id, requisition, row, tokens)

        _finishJob(job_id)

    except PermissionError as e:
        _failJob(job_id, str(e))
    except Exception as e:
        print(f"[job {job_id}] failed: {e}")
        _failJob(job_id, str(e))


def _analyzeOne(job_id, company_id, project_id, requisition, row, tokens):

    #one invoice: mark it reading, pull the merged PDF, run vision, record the
    #verdict. never raises — a failure lands as an "uncertain" row so one bad
    #invoice can't sink the period.

    requisition_id = row.get("requisition_id")

    try:
        #no attachments means no paperwork was submitted at all — that's a
        #verdict on its own, so skip the compile and the AI call entirely
        if not (requisition.get("attachments") or []):
            _setRow(job_id, requisition_id, {
                "state": "done",
                "aia": "no",
                "lien": "no",
                "confidence": 1.0,
                "notes": "no attachments on invoice — nothing submitted",
            })
            return

        _setRow(job_id, requisition_id, {"state": "reading"})

        pdf_bytes = compileInvoicePdf(company_id, project_id, requisition, tokens=tokens)

        #hold onto it: if this sub turns out to be non-compliant, the chase email
        #attaches this exact package back to them
        with _JOBS_LOCK:
            if job_id in _JOB_PDFS:
                _JOB_PDFS[job_id][requisition_id] = pdf_bytes

        verdict = analyze(pdf_bytes)

        _setRow(job_id, requisition_id, {"state": "done", "has_pdf": True, **verdict})

    except Exception as e:
        print(f"[job {job_id}] invoice {requisition_id} failed: {e}")
        _setRow(job_id, requisition_id, {
            "state": "done",
            "aia": "uncertain",
            "lien": "uncertain",
            "confidence": 0.0,
            "notes": f"could not analyze: {e}",
        })


# ── job state ────────────────────────────────────────────────────────────────

def _updateJob(job_id, **fields):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _setRow(job_id, requisition_id, fields):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        for row in job["results"]:
            if row.get("requisition_id") == requisition_id:
                row.update(fields)
                break


def _finishJob(job_id):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["status"] = "done"
            job["elapsed"] = round(time.time() - job["started_at"], 1)


def _failJob(job_id, message):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["status"] = "error"
            job["error"] = message
            job["elapsed"] = round(time.time() - job["started_at"], 1)


def get_job_status(job_id):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None

        if job.get("mock"):
            return _mockStatus(job)

        results = [dict(row) for row in job["results"]]
        done = sum(1 for row in results if row.get("state") == "done")

        return {
            "status": job["status"],
            "error": job.get("error"),
            "total": len(results),
            "done": done,
            "elapsed": job["elapsed"] if job["status"] != "running"
                       else round(time.time() - job["started_at"], 1),
            "results": results,
        }


def get_invoice_pdf(job_id, requisition_id):

    #the merged PDF this sub submitted, for attaching to their chase email.
    #cached from the analysis pass; recompiled from Procore if it isn't there
    #(e.g. a zero-attachment invoice, which was never compiled).

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None, None
        cached = (_JOB_PDFS.get(job_id) or {}).get(requisition_id)
        context = (job.get("company_id"), job.get("project_id"),
                   job.get("period_id"), job.get("tokens"))

    row_label = f"invoice_{requisition_id}.pdf"
    if cached:
        return cached, row_label

    company_id, project_id, period_id, tokens = context
    if not company_id:
        return None, None

    requisitions = getRequisitionsForPeriod(company_id, project_id, period_id, tokens=tokens) or []
    requisition = next((r for r in requisitions if r.get("id") == requisition_id), None)
    if not requisition:
        return None, None

    pdf_bytes = compileInvoicePdf(company_id, project_id, requisition, tokens=tokens)

    with _JOBS_LOCK:
        if job_id in _JOB_PDFS:
            _JOB_PDFS[job_id][requisition_id] = pdf_bytes

    return pdf_bytes, row_label


def get_job_tokens(job_id):

    #so a polling route can write refreshed tokens back into the flask session

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return job.get("tokens") if job else None


# ── mock job (frontend development) ──────────────────────────────────────────
#Kept from phase 1 so the UI can be worked on without burning Procore calls or
#AI spend. Send {"mock": true} to /sub-chaser/analyze to use it.

_MOCK_TIMINGS = [
    (0.4, 2.1), (0.6, 2.9), (0.8, 3.4), (1.0, 4.0),
    (1.2, 4.7), (1.4, 5.1), (1.6, 5.9), (1.8, 6.4),
]

_MOCK_RESULTS = [
    {"requisition_id": 90001, "sc_number": "SC25057G 01", "vendor_name": "BENCO INC",
     "application_number": 6, "invoice_number": "6", "attachment_count": 3,
     "status": "approved", "current_payment_due": "14250.00", "is_final": False,
     "aia": "yes", "lien": "yes", "confidence": 0.96,
     "notes": "completed AIA on pp. 2-4, lien waiver on p. 5",
     "recipients": [
         {"name": "Accounts Payable", "email": "ap@bencoinc.com", "title": "Billing", "source": "directory"},
         {"name": "Jim Smith", "email": "jsmith@bencoinc.com", "title": "Project Manager", "source": "directory"}]},
    {"requisition_id": 90002, "sc_number": "SC25057G 02", "vendor_name": "Kazimar Industrial Service",
     "application_number": 4, "invoice_number": "4", "attachment_count": 2,
     "status": "under_review", "current_payment_due": "8900.00", "is_final": False,
     "aia": "yes", "lien": "no", "confidence": 0.91,
     "notes": "completed AIA on pp. 2-3; no lien waiver found",
     "recipients": [
         {"name": "Dana Reyes", "email": "billing@kazimar.com", "title": "Office Manager", "source": "directory"}]},
    {"requisition_id": 90003, "sc_number": "SC25057G 03", "vendor_name": "VESSEL CONSTRUCTION SERVICES",
     "application_number": 5, "invoice_number": "5", "attachment_count": 4,
     "status": "under_review", "current_payment_due": "23475.50", "is_final": True,
     "aia": "no", "lien": "no", "confidence": 0.88,
     "notes": "only a blank AIA template found (pp. 1-2); no lien waiver",
     "recipients": [
         {"name": "Front Office", "email": "office@vesselconstruction.com", "title": "Office", "source": "directory"},
         {"name": "Mike Vessel", "email": "mvessel@vesselconstruction.com", "title": "Owner", "source": "directory"},
         {"name": "Accounting", "email": "ap@vesselconstruction.com", "title": "Accounting", "source": "directory"}]},
    {"requisition_id": 90004, "sc_number": "SC25057G 04", "vendor_name": "Apex Mechanical Corp",
     "application_number": 7, "invoice_number": "7", "attachment_count": 5,
     "status": "approved", "current_payment_due": "31200.00", "is_final": True,
     "aia": "yes", "lien": "yes", "confidence": 0.97,
     "notes": "completed AIA on pp. 3-5, lien waiver on p. 8",
     "recipients": [
         {"name": "Apex Mechanical Corp", "email": "accounts@apexmech.com", "title": "", "source": "vendor record"}]},
    {"requisition_id": 90005, "sc_number": "SC25057G 05", "vendor_name": "Hudson Valley Glass LLC",
     "application_number": 3, "invoice_number": "3", "attachment_count": 2,
     "status": "revise_and_resubmit", "current_payment_due": "5675.00", "is_final": False,
     "aia": "uncertain", "lien": "yes", "confidence": 0.52,
     "notes": "AIA scan on pp. 3-4 too illegible to judge; lien waiver on p. 6",
     "recipients": [
         {"name": "Hudson Valley Glass", "email": "info@hvglass.com", "title": "", "source": "directory"},
         {"name": "Payables", "email": "pay@hvglass.com", "title": "Accounts Payable", "source": "directory"}]},
    {"requisition_id": 90006, "sc_number": "SC25057G 06", "vendor_name": "Metro Fireproofing Inc",
     "application_number": 2, "invoice_number": "2", "attachment_count": 0,
     "status": "draft", "current_payment_due": "1350.00", "is_final": True,
     "aia": "no", "lien": "no", "confidence": 1.0,
     "notes": "no attachments on invoice — analysis skipped",
     #deliberately empty: exercises the "no contacts found" UI path
     "recipients": []},
    {"requisition_id": 90007, "sc_number": "SC25057G 07", "vendor_name": "Empire Steel Erectors",
     "application_number": 8, "invoice_number": "8", "attachment_count": 6,
     "status": "approved_for_payment", "current_payment_due": "18960.00", "is_final": True,
     "aia": "yes", "lien": "yes", "confidence": 0.94,
     "notes": "completed AIA on pp. 2-4, lien waiver on p. 10",
     "recipients": [
         {"name": "Empire AP", "email": "ap@empiresteel.com", "title": "Billing", "source": "directory"},
         {"name": "Dom Rossi", "email": "d.rossi@empiresteel.com", "title": "Superintendent", "source": "directory"}]},
    {"requisition_id": 90008, "sc_number": "SC25057G 08", "vendor_name": "Gotham Electric Co",
     "application_number": 5, "invoice_number": "5", "attachment_count": 3,
     "status": "under_review", "current_payment_due": "7420.00", "is_final": False,
     "aia": "no", "lien": "yes", "confidence": 0.9,
     "notes": "blank AIA only (p. 1); signed lien waiver on p. 7",
     "recipients": [
         {"name": "Gotham Billing", "email": "gotham.billing@gothamelectric.com", "title": "Billing", "source": "directory"}]},
]


def start_mock_job(company_id, project_id, period_id):
    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        _JOBS[job_id] = {"mock": True, "started_at": time.time()}
    return job_id


def _mockStatus(job):
    elapsed = time.time() - job["started_at"]

    results = []
    done_count = 0
    for row, (reading_at, lands_at) in zip(_MOCK_RESULTS, _MOCK_TIMINGS):
        if elapsed >= lands_at:
            done_count += 1
            results.append({**row, "state": "done"})
        else:
            #only the VERDICT fields wait — everything else comes from the fetch
            results.append({
                "requisition_id": row["requisition_id"],
                "sc_number": row["sc_number"],
                "vendor_name": row["vendor_name"],
                "application_number": row["application_number"],
                "invoice_number": row["invoice_number"],
                "attachment_count": row["attachment_count"],
                "status": row["status"],
                "current_payment_due": row["current_payment_due"],
                "is_final": row["is_final"],
                "state": "reading" if elapsed >= reading_at else "queued",
            })

    total = len(_MOCK_RESULTS)
    return {
        "status": "done" if done_count == total else "running",
        "error": None,
        "total": total,
        "done": done_count,
        "elapsed": round(min(elapsed, _MOCK_TIMINGS[-1][1]), 1),
        "results": results,
    }
