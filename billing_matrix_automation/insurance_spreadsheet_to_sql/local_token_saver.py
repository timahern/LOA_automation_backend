#THIS IS NOT USED RIGHT NOW. WILL LIKELY BE USED AGAIN IF A PROCORE ACCOUNT FOR AUTOMATION IS CREATED

import requests
import boto3
import os

CLIENT_ID = os.getenv("LOCAL_TOKEN_SAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOCAL_TOKEN_SAVER_CLIENT_SECRET")

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


def get_valid_access_token() -> str:
    """Call this anywhere you need a token. Auto-refreshes if expired."""
    access_token, _ = load_tokens()

    test = requests.get(
        "https://api.procore.com/rest/v1.0/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if test.status_code == 401:
        return refresh_access_token()

    return access_token
