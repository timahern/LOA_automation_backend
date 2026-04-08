from flask import Flask, request, send_file, jsonify, redirect, session, make_response
from urllib.parse import urlencode
from billing_matrix_automation.billing_matrix_creator import build_billing_matrix_xlsx
from billing_matrix_automation.subcontractor_info_retrieval import buildCompleteRowDataForEachCommitment, getBillingPeriods
from commitment_creation.procore_api_interaction.endpointTesting import makeRequest
from commitment_creation.procore_api_interaction.helper_functions.getProjectData import getContractTitle, getNumCommitments, addLineItem
from loaListGenerator import LoaGenerator
from flask_cors import CORS, cross_origin
from dotenv import load_dotenv
import os
from io import BytesIO
import zipfile
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded
from pdfHelpers import merge_uploaded_pdfs, get_pdf_page_count, pdf_first_page_to_data_url
import config
from auth.createState import create_state
from auth.getTokens import exchange_code_for_tokens
from auth.tokenStore import save_tokens, load_tokens
from flask_session import Session
from datetime import timedelta
import tempfile
from commitment_creation.procore_api_interaction.helper_functions.getUserInfo import getCompaniesAndProjects
from commitment_creation.getCompleteData import getAnalyzedData


load_dotenv()


PROCORE_CLIENT_ID = os.getenv("PROCORE_CLIENT_ID")
PROCORE_CLIENT_SECRET = os.getenv("PROCORE_CLIENT_SECRET")

if not PROCORE_CLIENT_ID or not PROCORE_CLIENT_SECRET:
    raise RuntimeError("Procore credentials not loaded from env")


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
print(f"Loaded FRONTEND_URL: {os.getenv('FRONTEND_URL')}")

# ✅ Server-side sessions (stores data on disk, cookie only holds a session id)
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = os.path.join(tempfile.gettempdir(), "flask_sessions")  # or any writable path
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "None"#these were commented out before
app.config["SESSION_COOKIE_SECURE"] = True#these were commented out before
app.config["SESSION_COOKIE_HTTPONLY"] = True#these were commented out before

# Optional but recommended: auto-expire session data
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)

os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)
Session(app)

limiter = Limiter(key_func=get_remote_address)
limiter.init_app(app)

frontend_url = os.getenv("FRONTEND_URL")
#CORS(app,
#     resources={r"/*": {"origins": frontend_url}},
#     supports_credentials=False,
 #    expose_headers=["Content-Disposition"],
#     methods=["GET", "POST", "OPTIONS"],
#     allow_headers=["Content-Type", "Authorization"])
CORS(
    app,
    resources={r"/*": {"origins": frontend_url}},
    supports_credentials=True,
    expose_headers=["Content-Disposition"],
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "x-api-key"],
)
#CORS(app)

@app.errorhandler(RateLimitExceeded)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded. Please wait and try again."}), 429

@app.before_request
def check_api_key():
    if request.method == "OPTIONS":
        return

    # ✅ Allow all auth-related routes AND browser junk
    if (
        request.path.startswith("/auth")
        or request.path.startswith("/oauth")
        or request.path == "/favicon.ico"
    ):
        return

    expected_key = os.getenv("API_KEY")
    actual_key = request.headers.get("x-api-key")
    print(f"Expected: {expected_key} | Got: {actual_key}")

    if actual_key != expected_key:
        return jsonify({"error": "Unauthorized"}), 401


@app.route("/test-env")
def test_env():
    theKey = os.getenv("API_KEY", "Not Found")
    return f"API_KEY is: {theKey}"

@app.route("/test-cors", methods=["OPTIONS", "POST"])
def test_cors():
    if request.method == "OPTIONS":
        response = make_response()
        response.headers["Access-Control-Allow-Origin"] = frontend_url
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response
    return jsonify({"message": "POST received"})


@app.route('/generate-loas', methods=['POST', 'OPTIONS'])
@cross_origin(origin=frontend_url, supports_credentials=True)
@limiter.limit("5 per minute")
def generate_loas():
    files = request.files

    try:
        exA = files['exhibit_a']
        exB = files['exhibit_b']
        exB1_list = request.files.getlist('exhibit_b1')
        exC_files = request.files.getlist('exhibit_c')
        exD = files['exhibit_d']
        exH_files = request.files.getlist('exhibit_h')  # ✅ Use get() here instead of ['key']

        if not exC_files:
            return {'error': 'At least one Exhibit C is required'}, 400

        if len(exC_files) > 1:
            exC = merge_uploaded_pdfs(exC_files)
        else:
            exC = exC_files[0]
        if len(exH_files) > 1:
            exH = merge_uploaded_pdfs(exH_files)
        elif len(exH_files) == 1:
            exH = exH_files[0]
        else:
            exH = None


        exA_len = get_pdf_page_count(exA)
        exB_len = get_pdf_page_count(exB)
        exC_len = get_pdf_page_count(exC)
        exD_len = get_pdf_page_count(exD)
        exH_len = get_pdf_page_count(exH)

        exB_pg1 = pdf_first_page_to_data_url(exB)

        lens_and_pg1 = {
            "exhibit_a_length": exA_len,
            "exhibit_b_length": exB_len,
            "exhibit_c_length": exC_len,
            "exhibit_d_length": exD_len,
            "exhibit_h_length": exH_len,
            "exhibit_b_page1": exB_pg1,
        }


        generator = LoaGenerator(exA, exB, exB1_list, exC, exD, exH, lens_and_pg1)
        zip_file = generator.generate_loas_zip()
        session["subs_metadata"] = generator.subs_metadata

        return send_file(
            zip_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='LOAs.zip'
        )

    except Exception as e:
        return {'error': str(e)}, 400

@app.route('/get-subs-metadata', methods=['GET', 'OPTIONS'])
@cross_origin(origin=frontend_url, supports_credentials=True)
@limiter.limit("30 per minute")
def get_subs_metadata():
    subs_metadata = session.get("subs_metadata")

    if not subs_metadata:
        return jsonify({"error": "No subs metadata found. Run /generate-loas first."}), 400

    return jsonify({"subs_metadata": subs_metadata}), 200



from buyoutBatchToB1 import extract_b1_batch_from_uploads  # ← your batch function

@app.route('/extract-b1s', methods=['POST', 'OPTIONS'])
@cross_origin(origin=frontend_url)
@limiter.limit("5 per minute")
def extract_b1s():

    if request.method == "OPTIONS":
        return '', 200

    try:
        uploaded_files = request.files.getlist('buyout_files')  # Expect 'buyout_files' key
        b1_pdfs = extract_b1_batch_from_uploads(uploaded_files)

        # Create ZIP of non-empty results
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for pdf in b1_pdfs:
                if pdf:  # skip None results
                    zipf.writestr(getattr(pdf, 'filename', 'Unknown_B1.pdf'), pdf.read())

        zip_buffer.seek(0)
        return send_file(
            zip_buffer,
            mimetype='application/zip',
            as_attachment=True,
            download_name='Extracted_B1s.zip'
        )

    except Exception as e:
        return {'error': str(e)}, 400


@app.get("/auth/procore")
def auth_procore():
    # 1) Generate + store state in session
    state = create_state()
    session["oauth_state"] = state
    session["return_to"] = request.args.get("return_to", "/")

    print("USING redirect_uri:", config.REDIRECT_URI)
    print("AUTH URL:", config.AUTH_URL)

    # 2) Build authorize URL
    params = {
        "response_type": "code",
        "client_id": PROCORE_CLIENT_ID,
        "redirect_uri": "https://apmtoolsbackend.art/auth/callback",
        "state": state,
    }
    url = f"{config.AUTH_URL}?{urlencode(params)}"
    return redirect(url)

@app.get("/auth/callback")
def oauth_callback():
    # Procore redirects here with ?code=...&state=...
    code = request.args.get("code")
    returned_state = request.args.get("state")

    if not code:
        return "Missing code", 400

    expected_state = session.get("oauth_state")
    if not expected_state or returned_state != expected_state:
        return "Invalid state", 400

    # Exchange code -> tokens (server-side with client_secret)
    tokens = exchange_code_for_tokens(
        token_url="https://login.procore.com/oauth/token",
        client_id=PROCORE_CLIENT_ID,
        client_secret=PROCORE_CLIENT_SECRET,
        redirect_uri="https://apmtoolsbackend.art/auth/callback",
        code=code,
    )

    save_tokens(tokens)

    return_to = session.pop("return_to", "/")
    return redirect(f"https://app.apmtoolbox.com{return_to}?procore=authed")


@app.get("/procore/companies-projects")
@cross_origin(origin=frontend_url, supports_credentials=True)
def companies_and_projects():
    try:
        data = getCompaniesAndProjects()
        return jsonify(data), 200
    except PermissionError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/procore/analyze")
@cross_origin(origin=frontend_url, supports_credentials=True)
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


@app.post("/create-commitments")
@cross_origin(origin=frontend_url, supports_credentials=True)
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

        # Print the Procore response body if NOT created
        if resp.status_code != 201:
            print("➡️ Procore response text (why it failed):")
            print(getattr(resp, "text", "")[:2000])  # cap output so it doesn't spam forever

        # Parse response JSON if possible
        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_text": getattr(resp, "text", "")}

        success = (resp.status_code == 201)

        line_item_success = False

        if success:
            try:
                line_item_added = addLineItem(company_id, project_id, resp_json.get("data").get("id"), sub_info.get("cost_code"),sub_info.get("subcontract_amount"))
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


@app.post("/billing/periods")
@cross_origin(origin=frontend_url, supports_credentials=True)
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



@app.post("/billing/generate")
@cross_origin(origin=frontend_url, supports_credentials=True)
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
            download_name=f"billing_matrix.xlsx"
        )

    except PermissionError as e:
        return jsonify({"error": str(e)}), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.get("/auth/status")
@cross_origin(origin=frontend_url, supports_credentials=True)
def auth_status():
    tokens = load_tokens()
    return jsonify({"authed": bool(tokens), "scope": tokens.get("scope") if tokens else None})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
