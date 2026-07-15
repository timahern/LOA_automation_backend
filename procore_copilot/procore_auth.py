import os
import requests
import boto3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Separate Procore app from the web-login one (PROCORE_CLIENT_ID) — the SSM
# tokens were issued under this app, so refreshes must use these credentials.
CLIENT_ID = os.getenv("PROCORE_COPILOT_CLIENT_ID")
CLIENT_SECRET = os.getenv("PROCORE_COPILOT_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise RuntimeError("PROCORE_COPILOT_CLIENT_ID / PROCORE_COPILOT_CLIENT_SECRET not loaded from env")

ssm = boto3.client("ssm", region_name="us-east-2")  # change region if needed


def load_tokens() -> dict:
    access = ssm.get_parameter(Name="/procore/access_token", WithDecryption=True)
    refresh = ssm.get_parameter(Name="/procore/refresh_token", WithDecryption=True)
    return {
        "access_token": access["Parameter"]["Value"],
        "refresh_token": refresh["Parameter"]["Value"]
    }


def save_tokens(access_token: str, refresh_token: str):
    ssm.put_parameter(Name="/procore/access_token", Value=access_token,
                      Type="SecureString", Overwrite=True)
    ssm.put_parameter(Name="/procore/refresh_token", Value=refresh_token,
                      Type="SecureString", Overwrite=True)


def refresh_access_token(tokens: dict) -> dict:
    print("Refreshing access token...")

    resp = requests.post(
        "https://login.procore.com/oauth/token/",
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": tokens["refresh_token"],
        },
    )
    resp.raise_for_status()

    data = resp.json()
    save_tokens(data["access_token"], data["refresh_token"])
    print("Tokens refreshed and saved to Parameter Store.")
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"]
    }
