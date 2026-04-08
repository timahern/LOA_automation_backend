import secrets

def create_state() -> str:
    # 32 bytes -> URL-safe string, great for OAuth state
    return secrets.token_urlsafe(32)
