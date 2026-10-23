#Runs one invoice-intake request end to end:
#   payload -> project -> subcontract(s) -> invoice -> attach file
#
#Returns (http_status, body). Every response carries the full matching trace so
#a failure in Power Automate's run history says exactly where and why.

import base64
import binascii
import os

from invoice_intake import procore_service as procore
from invoice_intake.matching import (
    MatchFailure, Trace, match_project, match_subcontracts, match_invoice,
)
from sub_chaser.attachments import uploadAttachments, listAttachments

DOCUMENT_TYPES = {"aia_with_waiver", "aia_only", "lien_waiver_only"}

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".heic": "image/heic",
}

#echoed back so the flow can log what the AI extracted next to what we matched
ECHO_FIELDS = ("document_type", "notarized", "subcontractor_name", "subcontract_number",
               "project_number", "project_name", "period", "application_number",
               "amount_due", "waiver_type", "info_complete", "sender_email", "file_name")


def _decode_file(file_base64):
    data = file_base64.strip()
    #tolerate a data-URI prefix ("data:application/pdf;base64,....")
    if data.startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    return base64.b64decode(data, validate=False)


def _fail(status, stage, error, trace, extracted, details=None):
    return status, {
        "success": False,
        "stage": stage,
        "error": error,
        "details": details or {},
        "warnings": trace.warnings,
        "extracted": extracted,
        "trace": trace.lines,
    }


def run_intake(payload):

    trace = Trace()
    extracted = {k: payload.get(k) for k in ECHO_FIELDS}
    dry_run = bool(payload.get("dry_run"))

    #── validate ─────────────────────────────────────────────────────────────
    document_type = payload.get("document_type")
    file_base64 = payload.get("file_base64")
    file_name = (payload.get("file_name") or "").strip()

    problems = []
    if document_type not in DOCUMENT_TYPES:
        problems.append(f"document_type must be one of {sorted(DOCUMENT_TYPES)}")
    if not file_base64 and not dry_run:
        problems.append("file_base64 is required")
    if not file_name:
        problems.append("file_name is required")
    if problems:
        return _fail(400, "validation", "; ".join(problems), trace, extracted)

    file_bytes = None
    if file_base64:
        try:
            file_bytes = _decode_file(file_base64)
        except (binascii.Error, ValueError) as e:
            return _fail(400, "validation", f"file_base64 is not valid base64: {e}", trace, extracted)
        if not file_bytes:
            return _fail(400, "validation", "file_base64 decoded to an empty file", trace, extracted)

    content_type = CONTENT_TYPES.get(os.path.splitext(file_name)[1].lower(), "application/octet-stream")

    trace.log("request", f"{document_type} from {payload.get('sender_email')!r}, file {file_name!r} "
                         f"({len(file_bytes or b'')} bytes, {content_type}), notarized={payload.get('notarized')}, "
                         f"info_complete={payload.get('info_complete')}, dry_run={dry_run}")
    if payload.get("missing_fields"):
        trace.warn("request", f"flow reported missing fields: {payload.get('missing_fields')}")

    try:
        tokens = procore.SsmTokens()

        #── stage 1: project ─────────────────────────────────────────────────
        project = match_project(procore.get_projects(tokens),
                                payload.get("project_number"), payload.get("project_name"), trace)
        project_id = project["id"]

        #── stage 2: subcontract(s) ──────────────────────────────────────────
        subcontracts = match_subcontracts(procore.get_subcontracts(project_id, tokens),
                                          payload.get("subcontract_number"),
                                          payload.get("subcontractor_name"), trace)

        #── stages 3/4: invoice ──────────────────────────────────────────────
        #one call per matched subcontract — almost always just one
        invoices = []
        for c in subcontracts:
            reqs = procore.get_requisitions(project_id, c["commitment_id"], tokens)
            trace.log("invoice", f"SC {c.get('number')} ({c.get('vendor_name')}): {len(reqs)} invoice(s)")
            invoices.extend(reqs)

        billing_periods = procore.get_billing_periods(project_id, tokens)
        requisition, invoice_label = match_invoice(
            invoices, subcontracts, payload.get("period"), payload.get("application_number"),
            payload.get("amount_due"), billing_periods, trace,
        )

    except MatchFailure as e:
        trace.log(e.stage, f"FAILED: {e.message}")
        return _fail(422, e.stage, e.message, trace, extracted, e.details)
    except procore.ProcoreError as e:
        trace.log("procore", f"FAILED: {e}")
        return _fail(502, "procore", str(e), trace, extracted)

    matched = {
        "project": {"id": project_id, "project_number": project.get("project_number"),
                    "name": project.get("name")},
        "invoice": invoice_label,
    }

    #── stage 5: attach ──────────────────────────────────────────────────────
    requisition_id = requisition.get("id")
    before = listAttachments(requisition)
    trace.log("attach", f"invoice {requisition_id} has {len(before)} attachment(s) now: "
                        f"{[a['name'] for a in before]}")

    if dry_run:
        trace.log("attach", "dry_run — matched but NOT uploading")
        return 200, {"success": True, "dry_run": True, "attached": False, "matched": matched,
                     "warnings": trace.warnings, "extracted": extracted, "trace": trace.lines}

    #Power Automate retries failed/timed-out actions; don't stack duplicate copies
    if any((a.get("name") or "").lower() == file_name.lower() for a in before):
        trace.warn("attach", f"{file_name!r} is already on this invoice — skipping upload")
        return 200, {"success": True, "attached": False, "already_attached": True, "matched": matched,
                     "warnings": trace.warnings, "extracted": extracted, "trace": trace.lines}

    try:
        updated = uploadAttachments(
            procore.COMPANY_ID, project_id, requisition_id,
            requisition.get("commitment_id"),
            [(file_name, file_bytes, content_type)],
            tokens=tokens,
        )
    except Exception as e:
        #most likely permissions: a non-admin can only edit the most recent invoice
        #on a contract, and only while it's draft / revise_and_resubmit
        trace.log("attach", f"FAILED: {e}")
        return _fail(502, "attach", f"Matched the invoice but Procore rejected the upload: {e}",
                     trace, extracted, {"matched": matched})

    after = listAttachments(updated)
    trace.log("attach", f"invoice {requisition_id} now has {len(after)} attachment(s): "
                        f"{[a['name'] for a in after]}")

    #Procore's docs don't say whether attachments[] on a PATCH appends or
    #replaces (see sub_chaser/attachments.py). check that nothing the invoice
    #already had went missing — if it did, say so as loudly as possible.
    missing = [a for a in before if a["id"] not in {x["id"] for x in after}]
    if missing:
        msg = (f"UPLOAD REPLACED EXISTING ATTACHMENTS — {len(missing)} file(s) that were on invoice "
               f"{requisition_id} are gone: {[a['name'] for a in missing]}. Re-upload them from the "
               "original email and stop using this endpoint until this is fixed.")
        trace.log("attach", msg)
        return _fail(500, "attach", msg, trace, extracted, {"matched": matched, "missing_attachments": missing})

    trace.log("attach", f"SUCCESS — attached {file_name!r} to invoice {requisition_id}")
    return 200, {
        "success": True,
        "attached": True,
        "matched": matched,
        "attachment_count_before": len(before),
        "attachment_count_after": len(after),
        "warnings": trace.warnings,
        "extracted": extracted,
        "trace": trace.lines,
    }
