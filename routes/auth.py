from flask import Blueprint, request, redirect, session, jsonify
from flask_cors import cross_origin
from urllib.parse import urlencode
import os

import config
from auth.createState import create_state
from auth.getTokens import exchange_code_for_tokens
from auth.tokenStore import save_tokens, load_tokens

auth_bp = Blueprint("auth", __name__)

PROCORE_CLIENT_ID = os.getenv("PROCORE_CLIENT_ID")
PROCORE_CLIENT_SECRET = os.getenv("PROCORE_CLIENT_SECRET")


@auth_bp.get("/auth/procore")
def auth_procore():
    state = create_state()
    session["oauth_state"] = state
    session["return_to"] = request.args.get("return_to", "/")

    params = {
        "response_type": "code",
        "client_id": PROCORE_CLIENT_ID,
        "redirect_uri": "https://apmtoolsbackend.art/auth/callback",
        "state": state,
    }
    url = f"{config.AUTH_URL}?{urlencode(params)}"
    return redirect(url)


@auth_bp.get("/auth/callback")
def oauth_callback():
    code = request.args.get("code")
    returned_state = request.args.get("state")

    if not code:
        return "Missing code", 400

    expected_state = session.get("oauth_state")
    if not expected_state or returned_state != expected_state:
        return "Invalid state", 400

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


@auth_bp.get("/auth/status")
@cross_origin(origin=os.getenv("FRONTEND_URL"), supports_credentials=True)
def auth_status():
    tokens = load_tokens()
    return jsonify({"authed": bool(tokens), "scope": tokens.get("scope") if tokens else None})
