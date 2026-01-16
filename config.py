import os

PROCORE_CLIENT_ID = os.getenv("PROCORE_CLIENT_ID", "")
PROCORE_CLIENT_SECRET = os.getenv("PROCORE_CLIENT_SECRET", "")

# Choose ONE for now. Start with localhost.
BASE_URL = "https://apmtools.art"

REDIRECT_URI = f"{BASE_URL}/auth/callback"

AUTH_URL = "https://login.procore.com/oauth/authorize"
TOKEN_URL = "https://login.procore.com/oauth/token"

# Flask session secret (required for storing state)
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")