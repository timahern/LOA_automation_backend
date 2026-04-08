from flask import Blueprint, request, send_file, jsonify, session
from flask_cors import cross_origin
from io import BytesIO
import zipfile
import os

from loaListGenerator import LoaGenerator
from pdfHelpers import merge_uploaded_pdfs, get_pdf_page_count, pdf_first_page_to_data_url
from buyoutBatchToB1 import extract_b1_batch_from_uploads
from extensions import limiter

loa_bp = Blueprint("loa", __name__)


def get_frontend_url():
    return os.getenv("FRONTEND_URL")


@loa_bp.route('/generate-loas', methods=['POST', 'OPTIONS'])
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
@limiter.limit("5 per minute")
def generate_loas():
    files = request.files

    try:
        exA = files['exhibit_a']
        exB = files['exhibit_b']
        exB1_list = request.files.getlist('exhibit_b1')
        exC_files = request.files.getlist('exhibit_c')
        exD = files['exhibit_d']
        exH_files = request.files.getlist('exhibit_h')

        if not exC_files:
            return {'error': 'At least one Exhibit C is required'}, 400

        exC = merge_uploaded_pdfs(exC_files) if len(exC_files) > 1 else exC_files[0]

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

        return send_file(zip_file, mimetype='application/zip', as_attachment=True, download_name='LOAs.zip')

    except Exception as e:
        return {'error': str(e)}, 400


@loa_bp.route('/get-subs-metadata', methods=['GET', 'OPTIONS'])
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
@limiter.limit("30 per minute")
def get_subs_metadata():
    subs_metadata = session.get("subs_metadata")
    if not subs_metadata:
        return jsonify({"error": "No subs metadata found. Run /generate-loas first."}), 400
    return jsonify({"subs_metadata": subs_metadata}), 200


@loa_bp.route('/extract-b1s', methods=['POST', 'OPTIONS'])
@cross_origin(origin=os.getenv("FRONTEND_URL"))
@limiter.limit("5 per minute")
def extract_b1s():
    if request.method == "OPTIONS":
        return '', 200

    try:
        uploaded_files = request.files.getlist('buyout_files')
        b1_pdfs = extract_b1_batch_from_uploads(uploaded_files)

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for pdf in b1_pdfs:
                if pdf:
                    zipf.writestr(getattr(pdf, 'filename', 'Unknown_B1.pdf'), pdf.read())

        zip_buffer.seek(0)
        return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name='Extracted_B1s.zip')

    except Exception as e:
        return {'error': str(e)}, 400
