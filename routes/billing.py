from flask import Blueprint, request, send_file, jsonify
from flask_cors import cross_origin
import os

from billing_matrix_automation.billing_matrix_creator import build_billing_matrix_xlsx
from billing_matrix_automation.subcontractor_info_retrieval import buildCompleteRowDataForEachCommitment, getBillingPeriods

billing_bp = Blueprint("billing", __name__)


@billing_bp.post("/billing/periods")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def billing_periods():
    try:
        payload = request.get_json(silent=True) or {}
        company_id = payload.get("company_id")
        project_id = payload.get("project_id")

        if not company_id or not project_id:
            return jsonify({"error": "Missing company_id or project_id"}), 400

        periods = getBillingPeriods(company_id, project_id)

        if periods is None:
            return jsonify({"error": "Failed to retrieve billing periods"}), 502

        return jsonify({
            "company_id": company_id,
            "project_id": project_id,
            "billing_periods": periods
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@billing_bp.post("/billing/generate")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def generate_billing_matrix():
    try:
        payload = request.get_json(silent=True) or {}
        company_id = payload.get("company_id")
        project_id = payload.get("project_id")
        position = payload.get("position")

        if not company_id or not project_id:
            return jsonify({"error": "Missing company_id or project_id"}), 400

        data = buildCompleteRowDataForEachCommitment(company_id, project_id, position)
        buffer = build_billing_matrix_xlsx(data, "template_billing_matrix.xlsx")

        return send_file(
            buffer,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="billing_matrix.xlsx"
        )

    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500
