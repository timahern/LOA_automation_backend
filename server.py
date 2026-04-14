from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter.errors import RateLimitExceeded
from flask_session import Session
from datetime import timedelta
import tempfile
import os

from extensions import limiter
from routes.loa import loa_bp
from routes.auth import auth_bp
from routes.procore import procore_bp
from routes.billing import billing_bp
from routes.rag import rag_bp


PROCORE_CLIENT_ID = os.getenv("PROCORE_CLIENT_ID")
PROCORE_CLIENT_SECRET = os.getenv("PROCORE_CLIENT_SECRET")

if not PROCORE_CLIENT_ID or not PROCORE_CLIENT_SECRET:
    raise RuntimeError("Procore credentials not loaded from env")


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = os.path.join(tempfile.gettempdir(), "flask_sessions")
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)

os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)
Session(app)

limiter.init_app(app)

frontend_url = os.getenv("FRONTEND_URL")
CORS(
    app,
    resources={r"/*": {"origins": frontend_url}},
    supports_credentials=True,
    expose_headers=["Content-Disposition"],
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "x-api-key"],
)

app.register_blueprint(loa_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(procore_bp)
app.register_blueprint(billing_bp)
app.register_blueprint(rag_bp)


@app.errorhandler(RateLimitExceeded)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded. Please wait and try again."}), 429


@app.before_request
def check_api_key():
    if request.method == "OPTIONS":
        return
    if (
        request.path.startswith("/auth")
        or request.path.startswith("/oauth")
        or request.path == "/favicon.ico"
    ):
        return

    expected_key = os.getenv("API_KEY")
    actual_key = request.headers.get("x-api-key")
    if actual_key != expected_key:
        return jsonify({"error": "Unauthorized"}), 401


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
