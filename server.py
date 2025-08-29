from flask import Flask, request, send_file, jsonify
from loaListGenerator import LoaGenerator  
from flask_cors import CORS, cross_origin
from dotenv import load_dotenv
import os
from io import BytesIO
import zipfile
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded

load_dotenv()

app = Flask(__name__)

print(f"Loaded FRONTEND_URL: {os.getenv('FRONTEND_URL')}")

limiter = Limiter(key_func=get_remote_address)
limiter.init_app(app)

frontend_url = os.getenv("FRONTEND_URL")
CORS(app,
     resources={r"/*": {"origins": frontend_url}},
     supports_credentials=False,
     expose_headers=["Content-Disposition"],
     methods=["GET", "POST", "OPTIONS"],
     allow_headers=["Content-Type", "Authorization"])
#CORS(app)

@app.errorhandler(RateLimitExceeded)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded. Please wait and try again."}), 429

@app.before_request
def check_api_key():
    if request.method == "OPTIONS":
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
        response.headers["Access-Control-Allow-Origin"] = "https://d1ti7vs3ibhxlw.cloudfront.net"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response
    return jsonify({"message": "POST received"})


@app.route('/generate-loas', methods=['POST', 'OPTIONS'])
@cross_origin(origin=frontend_url)
@limiter.limit("5 per minute") 
def generate_loas():
    files = request.files

    try:
        exA = files['exhibit_a']
        exB = files['exhibit_b']
        exB1_list = request.files.getlist('exhibit_b1')
        exC = files['exhibit_c']
        exD = files['exhibit_d']
        exH = files.get('exhibit_h')  # ✅ Use get() here instead of ['key']

        generator = LoaGenerator(exA, exB, exB1_list, exC, exD, exH)
        zip_file = generator.generate_loas_zip()

        return send_file(
            zip_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='LOAs.zip'
        )

    except Exception as e:
        return {'error': str(e)}, 400
    

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



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
