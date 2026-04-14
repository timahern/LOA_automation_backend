import os

from flask import Blueprint, request, jsonify, send_file, session
from flask_cors import cross_origin

rag_bp = Blueprint("rag", __name__, url_prefix="/rag")

SESSION_KEY = "rag_authed"

ORIGIN = os.getenv("FRONTEND_URL", "").strip()


def _require_rag_auth():
    if not session.get(SESSION_KEY):
        return jsonify({"error": "Unauthorized — passcode required"}), 401
    return None


@rag_bp.route("/unlock", methods=["POST", "OPTIONS"])
@cross_origin(origin=ORIGIN, supports_credentials=True)
def unlock():
    data = request.get_json(silent=True)
    if not data or "passcode" not in data:
        return jsonify({"error": "Missing 'passcode' field"}), 400

    expected = os.getenv("RAG_PASSCODE", "")
    if not expected:
        return jsonify({"error": "Server misconfiguration: RAG_PASSCODE not set"}), 500

    if data["passcode"] != expected:
        return jsonify({"error": "Incorrect passcode"}), 403

    session[SESSION_KEY] = True
    return jsonify({"success": True})


@rag_bp.route("/check-auth", methods=["GET", "OPTIONS"])
@cross_origin(origin=ORIGIN, supports_credentials=True)
def check_auth():
    return jsonify({"authenticated": bool(session.get(SESSION_KEY))})


@rag_bp.route("/chat", methods=["POST", "OPTIONS"])
@cross_origin(origin=ORIGIN, supports_credentials=True)
def chat():
    err = _require_rag_auth()
    if err:
        return err

    data = request.get_json(silent=True)
    if not data or "question" not in data:
        return jsonify({"error": "Missing required field: 'question'"}), 400

    question = data["question"].strip()
    if not question:
        return jsonify({"error": "Question cannot be empty"}), 400

    source  = data.get("source", "procedure-manual")
    history = data.get("history", [])

    try:
        from rag.query import query
        result = query(question, source=source, history=history)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@rag_bp.route("/page-image/<source>/<int:page>", methods=["GET", "OPTIONS"])
@cross_origin(origin=ORIGIN, supports_credentials=True)
def page_image(source, page):
    err = _require_rag_auth()
    if err:
        return err

    if not source.replace("-", "").replace("_", "").isalnum():
        return jsonify({"error": "Invalid source name"}), 400

    from rag import s3_helper

    if s3_helper.is_configured():
        url = s3_helper.get_page_image_url(source, page)
        if not url:
            return jsonify({"error": "Page image not found"}), 404
        return jsonify({"url": url})

    # Local dev fallback: serve from filesystem
    base_dir   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    image_path = os.path.join(base_dir, "static", "page-images", source, f"page_{page:03d}.png")

    if not os.path.isfile(image_path):
        return jsonify({"error": "Page image not found"}), 404

    return send_file(image_path, mimetype="image/png")

