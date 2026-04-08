from typing import Dict, Any, Optional
from flask import session

SESSION_KEY = "procore_tokens"

def save_tokens(tokens: Dict[str, Any]) -> None:
    # tokens usually includes: access_token, refresh_token, expires_in, token_type, etc.
    session[SESSION_KEY] = tokens

def load_tokens() -> Optional[Dict[str, Any]]:
    return session.get(SESSION_KEY)
