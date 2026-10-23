#The matching stages for Invoice Intake. Pure Python — no API calls in here,
#everything is fetched up front by intake.py and passed in.
#
#THE ONE RULE: if we can't narrow to exactly one thing with confidence, fail.
#A failed run costs an APM two minutes; a file on the wrong invoice costs a lot
#more and nobody notices it.
#
#Every function takes a Trace and writes down what it tried and what scored
#what, so a failure in Power Automate's run history explains itself.

import re
from datetime import date, datetime, timedelta

from rapidfuzz import fuzz

from sub_chaser.procore_fetch import getRequisitionCommitmentId, parseAmount

THRESHOLD = 85

#a fuzzy SC-number match must not contradict the sub's name. a 1-digit misread
#("SC25057G 17" vs "SC25057G 18") scores ~91 and can be a DIFFERENT sub's contract
NAME_CONFLICT_SCORE = 50

AMOUNT_TOLERANCE = 0.01

_CORP_SUFFIXES = {"inc", "llc", "corp", "corporation", "co", "company", "ltd",
                  "lp", "llp", "pc", "pllc", "the"}


class MatchFailure(Exception):

    def __init__(self, stage, message, details=None):
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.details = details or {}


class Trace:

    def __init__(self):
        self.lines = []
        self.warnings = []

    def log(self, stage, message):
        line = f"[{stage}] {message}"
        print("invoice-intake", line)
        self.lines.append(line)

    def warn(self, stage, message):
        self.log(stage, f"WARNING: {message}")
        self.warnings.append(message)


#── normalizers ─────────────────────────────────────────────────────────────────

def normalize_code(value):
    #"SC25057G-18" / "sc25057g 18" -> "SC25057G18"
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def normalize_name(value):
    #"ABC Electric, Inc." -> "abc electric"
    text = str(value or "").lower().replace("&", " and ")
    words = re.sub(r"[^a-z0-9 ]", " ", text).split()
    return " ".join(w for w in words if w not in _CORP_SUFFIXES)


def name_score(a, b):
    a, b = normalize_name(a), normalize_name(b)
    if not a or not b:
        return 0.0
    return fuzz.token_sort_ratio(a, b)


def vendor_name_score(doc_name, procore_name):

    #subs often write a shorter name on the AIA than the one in Procore
    #("Apex Electrical" vs "APEX ELECTRICAL CONTRACTING" scores only 71).
    #so if EVERY word of the document's name is in the Procore name, and it's at
    #least 2 words, count it as a full match. one word ("Apex") is too vague.
    #if that ends up matching two subs, the invoice stage still has to narrow
    #it to exactly one invoice or the run fails — it can't attach to a guess.
    #
    #sub names only — project names stay strict, or "Sweetgreen Buildout"
    #would match every Sweetgreen job.

    doc_words = normalize_name(doc_name).split()
    procore_words = set(normalize_name(procore_name).split())
    if len(doc_words) >= 2 and set(doc_words) <= procore_words:
        return 100.0
    return name_score(doc_name, procore_name)


def parse_app_number(value):
    #"App #3", "3", "0003", 3 -> 3
    if value is None:
        return None
    digits = re.findall(r"\d+", str(value))
    return int(digits[0]) if digits else None


def _parse_date(text):
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_period(value):

    #-> (start_date, end_date) or None
    #handles "10/01/2026-10/31/2026", "10/1/26 to 10/31/26", a single date,
    #and "October 2026" (taken as the whole month)

    if not value:
        return None
    text = str(value).strip()

    dates = re.findall(r"\d{1,4}[/-]\d{1,2}[/-]\d{1,4}", text)
    parsed = [d for d in (_parse_date(x) for x in dates) if d]
    if len(parsed) >= 2:
        return min(parsed[0], parsed[1]), max(parsed[0], parsed[1])
    if len(parsed) == 1:
        return parsed[0], parsed[0]

    for fmt in ("%B %Y", "%b %Y"):
        try:
            start = datetime.strptime(text, fmt).date()
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            return start, next_month - timedelta(days=1)
        except ValueError:
            pass
    return None


def _iso(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


#── stage 1: project ───────────────────────────────────────────────────────────

def _project_label(p):
    return {"id": p.get("id"), "project_number": p.get("project_number"), "name": p.get("name")}


def match_project(projects, project_number, project_name, trace):

    stage = "project"
    trace.log(stage, f"{len(projects)} active projects; looking for number={project_number!r} name={project_name!r}")

    if project_number:
        wanted = normalize_code(project_number)
        hits = [p for p in projects if wanted and normalize_code(p.get("project_number")) == wanted]
        trace.log(stage, f"project_number {project_number!r} (normalized {wanted!r}): {len(hits)} exact match(es)")
        if len(hits) == 1:
            trace.log(stage, f"MATCHED by number -> {_project_label(hits[0])}")
            return hits[0]
        if len(hits) > 1:
            #two projects can't really share a number, but if they do, the name can decide
            trace.warn(stage, f"{len(hits)} projects share number {project_number!r}; trying name among them")
            projects = hits
        else:
            trace.log(stage, "no project with that number; falling back to project_name")

    if not project_name:
        raise MatchFailure(stage, "Could not identify the project: "
                           + ("project_number did not match any active project and no project_name was given"
                              if project_number else "neither project_number nor project_name was provided"),
                           {"project_number": project_number})

    scored = sorted(((name_score(project_name, p.get("name")), p) for p in projects),
                    key=lambda x: x[0], reverse=True)
    top = [{"score": round(s, 1), **_project_label(p)} for s, p in scored[:5]]
    trace.log(stage, f"project_name {project_name!r} top scores: {top}")

    hits = [p for s, p in scored if s >= THRESHOLD]
    if len(hits) == 1:
        trace.log(stage, f"MATCHED by name -> {_project_label(hits[0])}")
        return hits[0]
    if len(hits) > 1:
        raise MatchFailure(stage, f"project_name {project_name!r} matches {len(hits)} projects at "
                           f"{THRESHOLD}%+ confidence — too ambiguous to pick one",
                           {"candidates": top})
    raise MatchFailure(stage, f"No project matched number {project_number!r} or name {project_name!r} "
                       f"at {THRESHOLD}%+ confidence", {"closest": top})


#── stage 2: subcontract(s) ────────────────────────────────────────────────────

def _sub_label(c, score=None):
    out = {"commitment_id": c.get("commitment_id"), "number": c.get("number"),
           "vendor_name": c.get("vendor_name"), "title": c.get("title")}
    if score is not None:
        out["score"] = round(score, 1)
    return out


def match_subcontracts(subcontracts, subcontract_number, subcontractor_name, trace):

    stage = "subcontract"
    trace.log(stage, f"{len(subcontracts)} subcontracts on project; looking for "
                     f"number={subcontract_number!r} sub={subcontractor_name!r}")

    if subcontract_number:
        wanted = normalize_code(subcontract_number)

        #an exact (normalized) number match beats any fuzzy one outright
        exact = [c for c in subcontracts if wanted and normalize_code(c.get("number")) == wanted]
        if exact:
            for c in exact:
                if subcontractor_name and vendor_name_score(subcontractor_name, c.get("vendor_name")) < NAME_CONFLICT_SCORE:
                    trace.warn(stage, f"SC {c.get('number')} matched exactly but its vendor "
                                      f"{c.get('vendor_name')!r} doesn't look like {subcontractor_name!r}")
            trace.log(stage, f"MATCHED {len(exact)} by exact number -> {[_sub_label(c) for c in exact]}")
            return exact

        scored = sorted(((fuzz.ratio(wanted, normalize_code(c.get("number"))), c) for c in subcontracts),
                        key=lambda x: x[0], reverse=True)
        trace.log(stage, f"subcontract_number fuzzy top scores: {[_sub_label(c, s) for s, c in scored[:5]]}")

        hits = []
        for s, c in scored:
            if s < THRESHOLD:
                break
            if subcontractor_name:
                ns = vendor_name_score(subcontractor_name, c.get("vendor_name"))
                if ns < NAME_CONFLICT_SCORE:
                    trace.log(stage, f"rejected SC {c.get('number')} (number score {s:.1f}): vendor "
                                     f"{c.get('vendor_name')!r} vs {subcontractor_name!r} only scores {ns:.1f}")
                    continue
            hits.append(c)

        if hits:
            trace.log(stage, f"MATCHED {len(hits)} by fuzzy number -> {[_sub_label(c) for c in hits]}")
            return hits
        trace.log(stage, "no confident subcontract_number match; falling back to subcontractor_name")

    if not subcontractor_name:
        raise MatchFailure(stage, "Could not identify the subcontract: "
                           + ("subcontract_number had no confident match and no subcontractor_name was given"
                              if subcontract_number else "neither subcontract_number nor subcontractor_name was provided"),
                           {"subcontracts_on_project": [_sub_label(c) for c in subcontracts]})

    scored = sorted(((vendor_name_score(subcontractor_name, c.get("vendor_name")), c) for c in subcontracts),
                    key=lambda x: x[0], reverse=True)
    trace.log(stage, f"subcontractor_name top scores: {[_sub_label(c, s) for s, c in scored[:5]]}")

    hits = [c for s, c in scored if s >= THRESHOLD]
    if hits:
        trace.log(stage, f"MATCHED {len(hits)} by vendor name -> {[_sub_label(c) for c in hits]}")
        return hits

    raise MatchFailure(stage, f"No subcontract matched number {subcontract_number!r} or sub "
                       f"{subcontractor_name!r} at {THRESHOLD}%+ confidence",
                       {"closest": [_sub_label(c, s) for s, c in scored[:5]]})


#── stages 3 + 4: the invoice ──────────────────────────────────────────────────
#Stage 3 (one subcontract) and Stage 4 (several) are the same cascade — Stage 4
#just runs it over the invoices of every matched subcontract at once.

def _period_range(req, billing_periods):
    period_id = (req.get("period_id") or req.get("billing_period_id")
                 or (req.get("billing_period") or {}).get("id"))
    bp = billing_periods.get(str(period_id)) if period_id else None
    if bp:
        return _iso(bp.get("start")), _iso(bp.get("end"))
    return None, None


def _period_matches(req, wanted, billing_periods):

    #the sub's period rarely lines up to the day with DGC's billing period
    #(9/26–10/25 vs 10/1–10/31), so we match on where the MIDDLE of the sub's
    #period falls. periods don't overlap, so the midpoint lands in exactly one.

    start, end = _period_range(req, billing_periods)
    mid = wanted[0] + (wanted[1] - wanted[0]) / 2
    if start and end:
        return start <= mid <= end

    #no billing period on the invoice — fall back to its billing date
    billing_date = _iso(req.get("billing_date"))
    return bool(billing_date) and wanted[0] <= billing_date <= wanted[1]


def _amount(req):
    return parseAmount((req.get("summary") or {}).get("current_payment_due"))


def _invoice_label(req, billing_periods, commitments_by_id):
    start, end = _period_range(req, billing_periods)
    commitment = commitments_by_id.get(str(getRequisitionCommitmentId(req))) or {}
    return {
        "requisition_id": req.get("id"),
        "application_number": req.get("number"),
        "invoice_number": req.get("invoice_number"),
        "status": req.get("status"),
        "period": f"{start} to {end}" if start else None,
        "billing_date": req.get("billing_date"),
        "current_payment_due": (req.get("summary") or {}).get("current_payment_due"),
        "subcontract_number": commitment.get("number"),
        "vendor_name": req.get("vendor_name") or commitment.get("vendor_name"),
    }


def match_invoice(invoices, commitments, period, application_number, amount_due,
                  billing_periods, trace):

    stage = "invoice"
    commitments_by_id = {str(c.get("commitment_id")): c for c in commitments}
    label = lambda r: _invoice_label(r, billing_periods, commitments_by_id)

    trace.log(stage, f"{len(invoices)} invoice(s) across {len(commitments)} subcontract(s): "
                     f"{[label(r) for r in invoices]}")

    if not invoices:
        raise MatchFailure(stage, "The matched subcontract(s) have no invoices in Procore",
                           {"subcontracts": [_sub_label(c) for c in commitments]})

    wanted_period = parse_period(period)
    wanted_app = parse_app_number(application_number)
    wanted_amount = parseAmount(amount_due)

    if period and not wanted_period:
        trace.warn(stage, f"could not parse period {period!r}; skipping period matching")
    if amount_due and wanted_amount is None:
        trace.warn(stage, f"could not parse amount_due {amount_due!r}; skipping amount matching")

    criteria = []
    if wanted_period:
        criteria.append(("period", lambda r: _period_matches(r, wanted_period, billing_periods)))
    if wanted_app is not None:
        criteria.append(("application_number", lambda r: parse_app_number(r.get("number")) == wanted_app))
    if wanted_amount is not None:
        criteria.append(("amount_due", lambda r: _amount(r) is not None
                         and abs(_amount(r) - wanted_amount) <= AMOUNT_TOLERANCE))

    if not criteria:
        raise MatchFailure(stage, "No period, application_number or amount_due to identify the invoice with",
                           {"invoices": [label(r) for r in invoices]})

    #cascade: each criterion narrows the field. a unique hit wins; several hits
    #narrow the candidates for the next criterion; zero hits are ignored (that
    #field is probably misread) and the next criterion gets its turn.
    candidates = invoices
    narrowed = False
    used = None
    for name, test in criteria:
        hits = [r for r in candidates if test(r)]
        trace.log(stage, f"by {name}: {len(hits)} of {len(candidates)} candidate(s) match "
                         f"-> {[r.get('id') for r in hits]}")
        if len(hits) == 1:
            candidates, narrowed, used = hits, True, name
            break
        if len(hits) > 1:
            candidates, narrowed = hits, True

    if not narrowed:
        raise MatchFailure(stage, f"No invoice matched period={period!r}, application_number="
                           f"{application_number!r} or amount_due={amount_due!r}",
                           {"invoices": [label(r) for r in invoices]})
    if len(candidates) > 1:
        raise MatchFailure(stage, f"{len(candidates)} invoices still match after trying every field "
                           "— too ambiguous to pick one",
                           {"candidates": [label(r) for r in candidates]})

    winner = candidates[0]

    #soft confirmation — the amount disagreeing doesn't stop us, but say so loudly
    if used != "amount_due" and wanted_amount is not None:
        actual = _amount(winner)
        if actual is None or abs(actual - wanted_amount) > AMOUNT_TOLERANCE:
            trace.warn(stage, f"amount_due on the document ({amount_due}) does not match the invoice's "
                              f"current payment due ({(winner.get('summary') or {}).get('current_payment_due')})")

    trace.log(stage, f"MATCHED by {used or 'combined narrowing'} -> {label(winner)}")
    return winner, label(winner)
