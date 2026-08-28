#Change detection for Sub-Chaser 2000 — "has this invoice changed since we last
#analyzed it?"
#
#The point is to skip paying for a vision call on an invoice whose verdict we
#already know. That only works if we can reliably tell when an invoice has been
#touched, which is harder than it looks:
#
#  - A requisition's own timestamp may NOT bump when a sub adds an attachment,
#    because attachments are separate prostore_file resources.
#  - So we don't trust one field. The fingerprint covers the requisition's
#    timestamp AND every attachment's id + timestamp. Add, replace or remove a
#    file and the hash moves either way.
#  - It fails SAFE: a field we can't read is simply absent from the hash
#    material, and an unreadable invoice re-analyzes rather than serving a
#    stale verdict.
#
#This was NOT designed from the docs — it was settled empirically with the
#/sub-chaser/phase0/timestamps probe (snapshot() + diffSnapshots()), and the
#result was the pessimistic one: Procore leaves the requisition's `updated_at`
#untouched when an attachment is added. See the PROVEN note on STAMP_KEYS.
#Re-run that probe if Procore's behavior ever seems to have changed.

import hashlib
import json
import re


#candidate timestamp fields, widest plausible net. Procore is inconsistent
#across api versions, so the probe looks for anything date-shaped rather than
#naming one field and hoping.
STAMP_KEY_PATTERN = re.compile(
    r"(updated|modified|changed|revised|created|_at$|_date$|^date)", re.IGNORECASE
)

#the fields the FINGERPRINT actually uses, settled by the probe (see PROVEN
#below). Deliberately narrower than what the probe reports: `payment_date` and
#`billing_date` move for reasons that have nothing to do with paperwork, and
#re-analyzing on those would just burn tokens. `created_at` never moves.
STAMP_KEYS = ("updated_at", "submitted_at")

#PROVEN Aug 26, 2026 — project 3254966, period 4011840, req 17146589
#(Vessel Construction Services), by adding one attachment in Procore between two
#probe runs:
#
#  * The requisition's `updated_at` did NOT move. NOTHING on the requisition
#    moved. Adding an attachment is invisible in the parent's timestamps.
#    => comparing a last-modified date alone is NOT a usable cache signal here.
#  * Attachments carry NO date fields whatsoever (`stamps_seen.on_attachment`
#    came back empty), so there is no per-file timestamp to lean on either.
#  * What DID change: the attachment id list (a new id, 6445556761) and the
#    count. That is the only reliable signal, and it's what saves us.
#  * REMOVING that attachment again was likewise invisible on the requisition
#    and likewise caught by the id list — so both directions are proven.
#  * The period's other 5 invoices hashed identically across all four runs — no
#    false invalidations.
#
#RESIDUAL GAP: a file replaced in place, keeping the same attachment id, would
#be undetectable from the requisition payload. Uploads appear to mint a fresh
#prostore_file id, so this is believed not to happen — but it is unproven, and
#it is the one way a cached verdict could go stale.

#bump this whenever VISION_PROMPT in vision.py changes in a way that could
#change a verdict. It belongs in the cache key, so old rows expire on their own
#instead of silently answering with the previous prompt's opinion.
PROMPT_VERSION = 1


def isStampKey(key):
    return bool(STAMP_KEY_PATTERN.search(str(key)))


def collectStamps(obj):

    #every date-looking top-level field on a dict, as {key: value}. Diagnostic
    #only — this is what the probe prints so we can SEE which field moves.
    #Scalars only; nested dicts/lists aren't timestamps.

    found = {}
    for key, value in (obj or {}).items():
        if isStampKey(key) and not isinstance(value, (dict, list)):
            found[key] = value
    return found


def attachmentParts(requisition):

    #one entry per attachment: id, name, and whatever timestamps it carries.
    #Sorted by id so the fingerprint is stable regardless of response ordering.

    parts = []
    for attachment in (requisition.get("attachments") or []):
        parts.append({
            "id": attachment.get("id"),
            "name": attachment.get("name") or attachment.get("filename"),
            "stamps": collectStamps(attachment),
        })
    return sorted(parts, key=lambda p: str(p.get("id")))


def fingerprintParts(requisition, stamp_keys=STAMP_KEYS):

    #the exact material the hash is built from. Kept separate from the hash so
    #the probe can show WHY two fingerprints differ — a bare hash tells you
    #something changed but never what.

    stamps = collectStamps(requisition)

    return {
        "requisition_id": requisition.get("id"),
        #only the keys we've decided to trust, in a fixed order
        "requisition_stamps": {k: stamps.get(k) for k in stamp_keys if k in stamps},
        #a bare count catches an add/remove even if no timestamp moves at all
        "attachment_count": len(requisition.get("attachments") or []),
        "attachments": [
            {"id": p["id"], "stamps": {k: p["stamps"].get(k)
                                       for k in stamp_keys if k in p["stamps"]}}
            for p in attachmentParts(requisition)
        ],
    }


def fingerprint(requisition, stamp_keys=STAMP_KEYS):

    #stable hash of the parts above. Same invoice, untouched -> same hash.

    parts = fingerprintParts(requisition, stamp_keys)
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cacheKey(company_id, project_id, requisition_id, prompt_version=PROMPT_VERSION):

    #what a cached verdict is keyed on. prompt_version is IN the key on purpose:
    #editing VISION_PROMPT must not keep serving verdicts the old prompt made.

    return {
        "company_id": str(company_id),
        "project_id": str(project_id),
        "requisition_id": str(requisition_id),
        "prompt_version": int(prompt_version),
    }


def snapshot(requisitions, stamp_keys=STAMP_KEYS):

    #probe output for a whole period: requisition_id -> everything visible about
    #its freshness. Diff two of these across an edit in Procore and the answer
    #to "does adding an attachment bump anything?" falls out.

    rows = {}
    for requisition in (requisitions or []):
        req_id = str(requisition.get("id"))
        rows[req_id] = {
            "vendor_name": requisition.get("vendor_name"),
            "status": requisition.get("status"),
            #EVERY date-ish field, not just the trusted ones — this is how we
            #discover the right field instead of assuming it
            "all_requisition_stamps": collectStamps(requisition),
            "attachments": attachmentParts(requisition),
            "fingerprint": fingerprint(requisition, stamp_keys),
            "fingerprint_parts": fingerprintParts(requisition, stamp_keys),
        }
    return rows


def diffSnapshots(before, after):

    #what actually moved between two probe runs. This IS the test result: add an
    #attachment in Procore between runs and read `invoices_changed`.

    before = before or {}
    after = after or {}

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = []

    for req_id in sorted(set(before) & set(after)):
        old, new = before[req_id], after[req_id]

        old_stamps = old.get("all_requisition_stamps") or {}
        new_stamps = new.get("all_requisition_stamps") or {}

        stamp_changes = {}
        for key in sorted(set(old_stamps) | set(new_stamps)):
            if old_stamps.get(key) != new_stamps.get(key):
                stamp_changes[key] = {"before": old_stamps.get(key),
                                      "after": new_stamps.get(key)}

        old_attachments = {str(a["id"]): a for a in (old.get("attachments") or [])}
        new_attachments = {str(a["id"]): a for a in (new.get("attachments") or [])}
        attachment_changes = {
            "added": sorted(set(new_attachments) - set(old_attachments)),
            "removed": sorted(set(old_attachments) - set(new_attachments)),
            "stamps_changed": [
                aid for aid in sorted(set(old_attachments) & set(new_attachments))
                if old_attachments[aid]["stamps"] != new_attachments[aid]["stamps"]
            ],
        }

        fingerprint_moved = old.get("fingerprint") != new.get("fingerprint")

        if stamp_changes or fingerprint_moved or any(attachment_changes.values()):
            changed.append({
                "requisition_id": req_id,
                "vendor_name": new.get("vendor_name"),
                #the headline: did our cache-invalidation signal actually fire?
                "fingerprint_moved": fingerprint_moved,
                "requisition_stamps_changed": stamp_changes,
                "attachment_changes": attachment_changes,
                "attachment_count": {"before": len(old.get("attachments") or []),
                                     "after": len(new.get("attachments") or [])},
            })

    return {
        "invoices_added": added,
        "invoices_removed": removed,
        "invoices_changed": changed,
        "unchanged_count": len(set(before) & set(after)) - len(changed),
    }
