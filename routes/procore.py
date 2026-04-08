from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
import os

from commitment_creation.procore_api_interaction.endpointTesting import makeRequest
from commitment_creation.procore_api_interaction.helper_functions.getProjectData import getContractTitle, getNumCommitments, addLineItem
from commitment_creation.procore_api_interaction.helper_functions.getUserInfo import getCompaniesAndProjects
from commitment_creation.getCompleteData import getAnalyzedData

procore_bp = Blueprint("procore", __name__)


@procore_bp.get("/procore/companies-projects")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def companies_and_projects():
    try:
        data = getCompaniesAndProjects()
        return jsonify(data), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@procore_bp.post("/procore/analyze")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def procore_analyze():
    try:
        payload = request.get_json(silent=True) or {}
        company_id = payload.get("company_id")
        project_id = payload.get("project_id")
        subs_metadata = payload.get("subs_metadata")

        if not company_id or not project_id:
            return jsonify({"error": "Missing company_id or project_id"}), 400
        if not isinstance(subs_metadata, list):
            return jsonify({"error": "subs_metadata must be a list"}), 400

        analyzed = getAnalyzedData(subs_metadata, str(company_id), str(project_id))

        return jsonify({
            "company_id": company_id,
            "project_id": project_id,
            "subs_metadata": analyzed
        }), 200

    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@procore_bp.post("/create-commitments")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def create_commitments():
    subs = request.get_json(silent=True)

    if not isinstance(subs, list) or len(subs) == 0:
        return jsonify({"ok": False, "error": "Request body must be a non-empty JSON list of subcontract objects."}), 400

    first = subs[0]
    company_id = first.get("company_id")
    project_id = first.get("project_id")

    if not company_id or not project_id:
        return jsonify({"ok": False, "error": "First item must include company_id and project_id."}), 400

    base_suffix = getNumCommitments(company_id, project_id)
    suffix = base_suffix + 1

    print(f"\n=== /create-commitments START === company_id={company_id} project_id={project_id} base_suffix={base_suffix} starting_suffix={suffix} total={len(subs)}")

    results = []

    for idx, sub in enumerate(subs):
        sub_info = dict(sub)
        contract_number = getContractTitle(company_id, project_id, suffix)
        sub_info["contract_number"] = contract_number

        print(f"\n[{idx+1}/{len(subs)}] Trying contract_number={contract_number} vendor={sub_info.get('vendor_selected')} title={sub_info.get('cost_code')} {sub_info.get('trade')}")

        try:
            resp = makeRequest(sub_info)
        except Exception as e:
            print(f"❌ makeRequest EXCEPTION idx={idx} contract_number={contract_number} error={e}")
            results.append({
                "index": idx,
                "vendor_selected": sub_info.get("vendor_selected"),
                "contract_number": contract_number,
                "success": False,
                "status_code": None,
                "error": str(e),
            })
            continue

        print(f"➡️ Procore status_code={resp.status_code} for contract_number={contract_number}")

        if resp.status_code != 201:
            print("➡️ Procore response text (why it failed):")
            print(getattr(resp, "text", "")[:2000])

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_text": getattr(resp, "text", "")}

        success = (resp.status_code == 201)
        line_item_success = False

        if success:
            try:
                line_item_added = addLineItem(
                    company_id, project_id,
                    resp_json.get("data").get("id"),
                    sub_info.get("cost_code"),
                    sub_info.get("subcontract_amount")
                )
                if line_item_added.get("data", {}).get("wbs_code", {}).get("flat_code"):
                    print("LINE ITEM SUCCESSFULLY ADDED!!!")
                    line_item_success = True
                else:
                    print("LINE ITEM FAILED TO ADD")
            except Exception as e:
                print("LINE ITEM FAILED TO ADD")

        results.append({
            "index": idx,
            "vendor_selected": sub_info.get("vendor_selected"),
            "trade": sub_info.get("trade"),
            "cost_code": sub_info.get("cost_code"),
            "contract_number": contract_number,
            "success": success,
            "status_code": resp.status_code,
            "procore_response": resp_json,
            "line_item_success": line_item_success,
        })

        if success:
            print(f"✅ Created commitment {contract_number}. Incrementing suffix -> {suffix+1}")
            suffix += 1
        else:
            print(f"❌ NOT created {contract_number}. NOT incrementing suffix (no skipping).")

    print(f"\n=== /create-commitments END === created={sum(1 for r in results if r['success'])} failed={sum(1 for r in results if not r['success'])}")

    return jsonify({
        "ok": True,
        "company_id": company_id,
        "project_id": project_id,
        "starting_suffix": base_suffix,
        "results": results,
        "created_count": sum(1 for r in results if r["success"]),
        "failed_count": sum(1 for r in results if not r["success"]),
    }), 200
