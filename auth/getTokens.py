import os
from dotenv import load_dotenv
import requests
from typing import Dict, Any
from requests.auth import HTTPBasicAuth
from auth.tokenStore import load_tokens, save_tokens


load_dotenv()


PROCORE_CLIENT_ID = os.getenv("PROCORE_CLIENT_ID")
PROCORE_CLIENT_SECRET = os.getenv("PROCORE_CLIENT_SECRET")

TOKEN_URL = "https://login.procore.com/oauth/token"

def exchange_code_for_tokens(
    token_url: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str
) -> Dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    resp = requests.post(
        token_url,
        data=data,
        auth=HTTPBasicAuth(client_id, client_secret),
        timeout=20
    )
    if not resp.ok:
        raise RuntimeError(f"Token exchange failed: {resp.status_code} {resp.text}")
    return resp.json()

def refresh_access_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    refresh_token: str
) -> Dict[str, Any]:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    resp = requests.post(token_url, data=data, timeout=20)
    if not resp.ok:
        raise RuntimeError(f"Refresh failed: {resp.status_code} {resp.text}")
    return resp.json()

def refresh_and_store_tokens() -> Dict[str, Any]:
    tokens = load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        raise PermissionError("No refresh token available. Re-auth required.")

    refreshed = refresh_access_token(
        token_url=TOKEN_URL,
        client_id=PROCORE_CLIENT_ID,
        client_secret=PROCORE_CLIENT_SECRET,
        refresh_token=tokens["refresh_token"],
    )

    # Required
    tokens["access_token"] = refreshed["access_token"]

    # Optional rotation
    if refreshed.get("refresh_token"):
        tokens["refresh_token"] = refreshed["refresh_token"]

    # Optional metadata (only set if present)
    if "expires_in" in refreshed:
        tokens["expires_in"] = refreshed["expires_in"]
    if "created_at" in refreshed:
        tokens["created_at"] = refreshed["created_at"]
    if "scope" in refreshed:
        tokens["scope"] = refreshed["scope"]
    if "token_type" in refreshed:
        tokens["token_type"] = refreshed["token_type"]

    save_tokens(tokens)
    return tokens
