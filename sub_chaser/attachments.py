#Uploading paperwork back onto a Procore invoice.
#
#Closes the Sub-Chaser loop: we tell the APM who's missing an AIA or waiver, the
#sub emails the signed copy back, and this puts it on the invoice in Procore
#without leaving the tool.
#
#Procore's contract (from the requisitions API docs):
#   PATCH /rest/v1.0/requisitions/{id}
#   "To upload attachments you must upload the entire payload as
#    multipart/form-data content-type and specify each parameter as form-data
#    together with attachments[] as files."
#So this is NOT a json request — project_id/commitment_id go as form fields and
#each file goes as an `attachments[]` part.
#
#TWO THINGS TO KNOW BEFORE USING THIS:
#
#1. PERMISSIONS. Per the docs, a non-admin can only update an invoice that is
#   the MOST RECENT one on its contract AND is in status draft or
#   revise_and_resubmit. Admins can update any. The invoices Sub-Chaser chases
#   are usually under_review/approved, so this will often be admin-only.
#   canAttach() reads the invoice's own action_policy so the UI can disable the
#   control instead of failing at upload time.
#
#2. APPEND vs REPLACE. The docs do NOT say whether attachments[] on a PATCH adds
#   to the existing attachments or replaces them. If it replaces, uploading
#   would DESTROY the sub's original submission. Until that's proven by
#   testAttachmentBehavior() against a throwaway invoice, treat uploads as
#   potentially destructive and do not point this at a live invoice.

import requests

from sub_chaser.procore_fetch import getAccessToken, refreshAccessToken, procoreGet

BASE = "https://api.procore.com"


def getRequisition(company_id, project_id, requisition_id, tokens=None):

    #one invoice, including its attachments[] and action_policy

    response = procoreGet(
        f"{BASE}/rest/v1.0/requisitions/{requisition_id}",
        company_id,
        params={"project_id": project_id},
        tokens=tokens,
    )
    if response.status_code != 200:
        return None
    return response.json()


def listAttachments(requisition):

    #[{id, name, url}] for whatever is currently on the invoice

    out = []
    for a in (requisition or {}).get("attachments") or []:
        out.append({
            "id": a.get("id"),
            "name": a.get("name") or a.get("filename"),
            "url": a.get("url"),
        })
    return out


def canAttach(requisition):

    #does THIS user have permission to update THIS invoice? procore returns an
    #action_policy with an {allowed, tooltip} verdict per field, so we can grey
    #the button out with a real reason instead of guessing from status.

    policy = (requisition or {}).get("action_policy") or {}
    controls = policy.get("controls") or {}
    update = controls.get("update")

    if isinstance(update, dict):
        #field-shaped control: look for an attachments verdict, else any verdict
        attachments = update.get("attachments")
        if isinstance(attachments, dict) and "allowed" in attachments:
            return bool(attachments.get("allowed")), attachments.get("tooltip")
        if "allowed" in update:
            return bool(update.get("allowed")), update.get("tooltip")
        for value in update.values():
            if isinstance(value, dict) and "allowed" in value:
                return bool(value.get("allowed")), value.get("tooltip")

    #no policy in the response — unknown rather than a hard no
    return None, "no action_policy on the response"


def uploadAttachments(company_id, project_id, requisition_id, commitment_id, files, tokens=None):

    #files: [(filename, bytes, content_type), ...]
    #returns the updated requisition json.
    #
    #multipart, NOT json — requests sets the boundary itself, so never set
    #Content-Type by hand here.

    def send(access_token):
        multipart = [
            ("attachments[]", (name, content, content_type))
            for (name, content, content_type) in files
        ]
        return requests.patch(
            f"{BASE}/rest/v1.0/requisitions/{requisition_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Procore-Company-Id": str(company_id),
                "Accept": "application/json",
            },
            data={"project_id": project_id, "commitment_id": commitment_id},
            files=multipart,
            timeout=120,
        )

    response = send(getAccessToken(tokens))
    print("Status:", response.status_code)

    if response.status_code == 401:
        response = send(refreshAccessToken(tokens))
        print("Status:", response.status_code)

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Attachment upload failed ({response.status_code}): {response.text[:400]}"
        )

    return response.json()


def makeTestPdf(label):

    #a tiny valid one-page PDF, so the behavior test doesn't need a real file

    text = f"SUB-CHASER TEST FILE - {label} - safe to delete"
    content = f"BT /F1 12 Tf 40 120 Td ({text}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = "%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n"
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n")

    return out.encode("latin-1")


def testAttachmentBehavior(company_id, project_id, requisition_id, tokens=None):

    #THE SAFETY TEST. Uploads one tiny file to the given invoice and reports
    #whether procore APPENDED it or REPLACED everything.
    #
    #Run this against a THROWAWAY/DRAFT invoice only — if the answer turns out
    #to be REPLACE, this call itself destroys that invoice's attachments.

    before_req = getRequisition(company_id, project_id, requisition_id, tokens=tokens)
    if before_req is None:
        return {"error": f"Could not read requisition {requisition_id}"}

    before = listAttachments(before_req)
    allowed, tooltip = canAttach(before_req)

    if allowed is False:
        return {
            "verdict": "NOT PERMITTED",
            "can_attach": False,
            "reason": tooltip,
            "invoice_status": before_req.get("status"),
            "attachments_before": before,
        }

    commitment_id = before_req.get("commitment_id")
    pdf = makeTestPdf(f"req{requisition_id}")

    try:
        uploadAttachments(
            company_id, project_id, requisition_id, commitment_id,
            [("subchaser-attachment-test.pdf", pdf, "application/pdf")],
            tokens=tokens,
        )
    except Exception as e:
        return {
            "verdict": "UPLOAD FAILED",
            "error": str(e),
            "can_attach": allowed,
            "invoice_status": before_req.get("status"),
            "attachments_before": before,
        }

    after_req = getRequisition(company_id, project_id, requisition_id, tokens=tokens)
    after = listAttachments(after_req)

    before_ids = {a["id"] for a in before}
    after_ids = {a["id"] for a in after}
    survived = before_ids & after_ids

    if len(before) == 0:
        verdict = "INCONCLUSIVE - invoice had no attachments to begin with; retest one that does"
    elif survived == before_ids and len(after) > len(before):
        verdict = "APPEND - existing attachments survived (safe to build on)"
    elif not survived:
        verdict = "REPLACE - existing attachments were WIPED (do NOT use on live invoices)"
    else:
        verdict = "PARTIAL - some prior attachments went missing; investigate before using"

    return {
        "verdict": verdict,
        "can_attach": allowed,
        "permission_note": tooltip,
        "invoice_status": before_req.get("status"),
        "count_before": len(before),
        "count_after": len(after),
        "attachments_before": before,
        "attachments_after": after,
        "surviving_ids": sorted(survived),
    }
