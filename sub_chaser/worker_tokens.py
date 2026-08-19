#Carries the SIGNED-IN USER'S Procore OAuth tokens into worker threads.
#
#Why this exists: flask's `session` only works inside a request. Sub-Chaser
#analyzes a period's invoices on background threads, and those threads cannot
#call load_tokens() — there's no request context, so it blows up.
#
#So the route (which IS in a request) reads the user's tokens out of the session
#and hands this object to the job. Same tokens as everywhere else in the app —
#the user's own, from Procore OAuth. Nothing new, nothing shared, no SSM.
#
#Refreshing works the same way: auth.getTokens.refresh_access_token() is a plain
#HTTP call with no session in it, so a worker can call it. The refreshed tokens
#are kept here and written back into the session on the next poll request.

import os
import threading

from auth.tokenStore import load_tokens
from auth.getTokens import refresh_access_token

TOKEN_URL = "https://login.procore.com/oauth/token"


class WorkerTokens:

    def __init__(self, tokens):
        self._tokens = dict(tokens or {})
        self._lock = threading.Lock()
        self.refreshed = False  #set when we rotate, so the route can re-save to the session

    def access_token(self):
        with self._lock:
            return self._tokens.get("access_token")

    def refresh(self):

        #refresh once even if several worker threads hit a 401 at the same time

        with self._lock:
            before = self._tokens.get("access_token")

            refreshed = refresh_access_token(
                token_url=TOKEN_URL,
                client_id=os.getenv("PROCORE_CLIENT_ID"),
                client_secret=os.getenv("PROCORE_CLIENT_SECRET"),
                refresh_token=self._tokens.get("refresh_token"),
            )

            #another thread already refreshed while we waited for the lock
            if self._tokens.get("access_token") != before:
                return self._tokens.get("access_token")

            self._tokens["access_token"] = refreshed["access_token"]
            if refreshed.get("refresh_token"):
                self._tokens["refresh_token"] = refreshed["refresh_token"]
            for key in ("expires_in", "created_at", "scope", "token_type"):
                if key in refreshed:
                    self._tokens[key] = refreshed[key]

            self.refreshed = True
            return self._tokens["access_token"]

    def snapshot(self):
        with self._lock:
            return dict(self._tokens)


def fromSession():

    #call this INSIDE a request (i.e. from a route) to capture the signed-in
    #user's tokens before handing work off to background threads

    tokens = load_tokens()
    if not tokens or not tokens.get("access_token"):
        raise PermissionError("Not signed in to Procore. Sign in and try again.")
    return WorkerTokens(tokens)
