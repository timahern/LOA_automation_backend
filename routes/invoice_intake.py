from flask import Blueprint, request, jsonify
import traceback

from invoice_intake.intake import run_intake

invoice_intake_bp = Blueprint("invoice_intake", __name__)


#Called by the Power Automate invoice flow, not the frontend. Auth is the global
#x-api-key check in server.py — the flow sends the same API_KEY header.
#
#Pass "dry_run": true to run all the matching and get the answer back without
#uploading anything.

@invoice_intake_bp.post("/api/invoice-intake")
def invoice_intake():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"success": False, "stage": "validation",
                        "error": "Body must be a JSON object"}), 400

    try:
        status, body = run_intake(payload)
        return jsonify(body), status
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "stage": "server", "error": str(e)}), 500
