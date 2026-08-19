from flask import Blueprint, request, jsonify, send_file
from flask_cors import cross_origin
import io
import os

from billing_matrix_automation.subcontractor_info_retrieval import getBillingPeriods, getWorkOrderContracts
from sub_chaser.procore_fetch import (
    getRequisitionsForPeriod, cleanRequisition, getRequisitionCommitmentId,
    getWorkOrderContractsLite,
)
from sub_chaser.recipients import (
    getProjectDirectory, buildDirectoryIndex, resolveRecipients,
)
from sub_chaser.pdf_compile import compileInvoicePdf
from sub_chaser.jobs import (
    start_mock_job, start_real_job, get_job_status, get_job_tokens, get_invoice_pdf,
)
from sub_chaser import worker_tokens
from auth.tokenStore import save_tokens

sub_chaser_bp = Blueprint("sub_chaser", __name__)


#ANALYSIS JOB endpoints — the contract the UI polls against.
#Start a job, then poll it ~1s; rows stream from queued -> reading -> done.
#Send {"mock": true} to run canned verdicts instead (frontend work, no
#Procore calls and no AI spend).


@sub_chaser_bp.post("/sub-chaser/analyze")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def start_analysis():
    try:
        payload = request.get_json(silent=True) or {}
        company_id = payload.get("company_id")
        project_id = payload.get("project_id")
        period_id = payload.get("period_id")

        if not company_id or not project_id or not period_id:
            return jsonify({"error": "Missing company_id, project_id or period_id"}), 400

        if payload.get("mock"):
            return jsonify({"job_id": start_mock_job(company_id, project_id, period_id)}), 202

        #capture the signed-in user's Procore tokens HERE, while we're still in a
        #request — the worker threads can't reach the flask session
        tokens = worker_tokens.fromSession()

        job_id = start_real_job(company_id, project_id, period_id, tokens)
        return jsonify({"job_id": job_id}), 202

    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sub_chaser_bp.get("/sub-chaser/jobs/<job_id>")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def poll_job(job_id):
    try:
        status = get_job_status(job_id)
        if status is None:
            return jsonify({"error": "Unknown job_id"}), 404

        #if a worker refreshed the user's Procore tokens mid-job, write them back
        #into the session now that we're in a request again
        tokens = get_job_tokens(job_id)
        if tokens is not None and tokens.refreshed:
            save_tokens(tokens.snapshot())
            tokens.refreshed = False

        return jsonify(status), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


#PHASE 0 de-risk endpoints — prove the data path before building the real tool.
#Auth is the standard flow: sign in via /auth/procore first (session tokens),
#and send the x-api-key header like every other route.


@sub_chaser_bp.get("/sub-chaser/jobs/<job_id>/invoice/<int:requisition_id>/pdf")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def invoice_pdf(job_id, requisition_id):

    #The sub's OWN submitted invoice package, so the chase email can attach their
    #paperwork back to them rather than a blank template.
    #
    #This path is exempt from the x-api-key middleware (see server.py) because the
    #browser fetches it directly for drag-to-attach and file downloads, where no
    #custom header can be set. job_id is a random 128-bit hex that only the user
    #who started the job ever sees, so the URL is the capability.
    #
    #Drag-to-attach notes: Chromium fetches this URL DURING the drag and hands the
    #drop target a real file. That fetch needs the response to look like a
    #concrete downloadable file, so we send Content-Disposition: attachment with
    #an explicit filename. The page reaches this path same-origin (dev-server
    #proxy) — a cross-origin URL fails the drag-fetch silently.

    try:
        pdf_bytes, filename = get_invoice_pdf(job_id, requisition_id)
        if not pdf_bytes:
            return jsonify({"error": "No PDF available for that invoice"}), 404

        #?inline=1 renders it in a browser tab (the chip's "open" link); the
        #default forces a download with a proper filename, which is what the
        #"download" link and the drag-fetch need
        inline = request.args.get("inline") == "1"

        #send_file streams from the buffer rather than re-copying the bytes
        response = send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=not inline,
            download_name=filename,
            conditional=True,
        )
        disposition = "inline" if inline else "attachment"
        response.headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        response.headers["Content-Length"] = str(len(pdf_bytes))
        #the drag-fetch must not be served a stale/partial cached copy
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sub_chaser_bp.post("/sub-chaser/recipients/diagnostics")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def recipients_diagnostics():

    #Verify contact resolution against a real project: how many directory people
    #we found, which companies they map to, and which subs on this period end up
    #with nobody. Read-only.

    try:
        payload = request.get_json(silent=True) or {}
        company_id = payload.get("company_id")
        project_id = payload.get("project_id")
        period_id = payload.get("period_id")

        if not company_id or not project_id:
            return jsonify({"error": "Missing company_id or project_id"}), 400

        users = getProjectDirectory(company_id, project_id)
        index = buildDirectoryIndex(users)

        sample = users[0] if users else {}
        report = {
            "directory_user_count": len(users),
            "companies_with_contacts": len(index),
            #so we can confirm which field actually carries the company linkage
            "sample_user_keys": sorted(sample.keys()) if sample else [],
            "contacts_per_company": {
                vendor_id: len(contacts) for vendor_id, contacts in index.items()
            },
        }

        #if a period was given, show exactly who each of its subs would be emailed
        if period_id:
            requisitions = getRequisitionsForPeriod(company_id, project_id, period_id) or []
            contracts = getWorkOrderContractsLite(company_id, project_id)

            resolved = []
            for requisition in requisitions:
                row = cleanRequisition(requisition, contracts)
                contacts = resolveRecipients(
                    index, row.get("vendor_id"),
                    company_id=company_id, project_id=project_id,
                )
                resolved.append({
                    "sc_number": row.get("sc_number"),
                    "vendor_name": row.get("vendor_name"),
                    "vendor_id": row.get("vendor_id"),
                    "recipient_count": len(contacts),
                    "recipients": contacts,
                })

            report["period_subs"] = resolved
            report["subs_with_no_contacts"] = [
                r["vendor_name"] for r in resolved if r["recipient_count"] == 0
            ]

        return jsonify(report), 200

    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sub_chaser_bp.post("/sub-chaser/phase0/linkage")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def phase0_linkage():

    #for a project + billing period (defaults to the latest period), list the
    #period's invoices and their invoice -> commitment -> vendor linkage,
    #confirming SC number + company come straight from the API.

    try:
        payload = request.get_json(silent=True) or {}
        company_id = payload.get("company_id")
        project_id = payload.get("project_id")
        period_id = payload.get("period_id")

        if not company_id or not project_id:
            return jsonify({"error": "Missing company_id or project_id"}), 400

        periods = getBillingPeriods(company_id, project_id)
        if not periods:
            return jsonify({"error": "Failed to retrieve billing periods"}), 502

        if period_id:
            period = next((p for p in periods if p.get("period_id") == period_id), None)
            if not period:
                return jsonify({"error": f"No billing period with id {period_id}"}), 404
        else:
            period = periods[-1]
            period_id = period.get("period_id")

        work_order_contracts = getWorkOrderContracts(company_id, project_id)

        requisitions = getRequisitionsForPeriod(company_id, project_id, period_id)
        if requisitions is None:
            return jsonify({"error": "Failed to retrieve requisitions"}), 502

        rows = [cleanRequisition(r, work_order_contracts) for r in requisitions]

        return jsonify({
            "period": period,
            "invoice_count": len(rows),
            "invoices": rows,
            #so we can eyeball what other fields the requisition carries:
            "raw_first_requisition_keys": sorted(requisitions[0].keys()) if requisitions else [],
            #phase 0 diagnostics: pick an earlier period_id from here to re-test,
            #and compare these commitment ids against the invoices' commitment_id
            #to see why an sc_number join might miss
            "all_periods": [
                {"period_id": p.get("period_id"), "month": p.get("month"),
                 "year": p.get("year"), "position": p.get("position")}
                for p in periods
            ],
            "work_order_contracts": [
                {"commitment_id": c.get("commitment_id"),
                 "sc_number": c.get("commitment_number"),
                 "vendor": c.get("subcontractor_name"),
                 "status": c.get("commitment_status")}
                for c in (work_order_contracts or [])
            ],
            #send {"include_raw": true} to get the untouched procore response
            #for the period's requisitions (phase 0 field exploration)
            "raw_requisitions": requisitions if payload.get("include_raw") else None,
            "sc_join_diagnostics": {
                "invoice_commitment_ids": [str(getRequisitionCommitmentId(r)) for r in requisitions],
                "contract_commitment_ids": [str(c.get("commitment_id")) for c in (work_order_contracts or [])],
                "unmatched": [
                    str(getRequisitionCommitmentId(r)) for r in requisitions
                    if str(getRequisitionCommitmentId(r)) not in
                    [str(c.get("commitment_id")) for c in (work_order_contracts or [])]
                ],
            },
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sub_chaser_bp.post("/sub-chaser/phase0/pdf")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def phase0_pdf():

    #compile + download one requisition's merged PDF (cover sheet + attachments)
    #and return it, to prove the compile path with the user's own oauth tokens.
    #nothing is stored server-side.

    try:
        payload = request.get_json(silent=True) or {}
        company_id = payload.get("company_id")
        project_id = payload.get("project_id")
        period_id = payload.get("period_id")
        requisition_id = payload.get("requisition_id")

        if not company_id or not project_id or not period_id or not requisition_id:
            return jsonify({"error": "Missing company_id, project_id, period_id or requisition_id"}), 400

        requisitions = getRequisitionsForPeriod(company_id, project_id, period_id)
        if requisitions is None:
            return jsonify({"error": "Failed to retrieve requisitions"}), 502

        requisition = next((r for r in requisitions if r.get("id") == requisition_id), None)
        if not requisition:
            return jsonify({"error": f"No requisition {requisition_id} in period {period_id}"}), 404

        pdf_bytes = compileInvoicePdf(company_id, project_id, requisition)

        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=f"requisition_{requisition_id}.pdf"
        )

    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500
