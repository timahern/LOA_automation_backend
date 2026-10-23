#Procore access for Invoice Intake.
#
#WHY SSM TOKENS (and not the signed-in user's session tokens like Sub-Chaser):
#this endpoint is called by a Power Automate flow, not a browser. there is no
#user and no flask session, so we use the shared service tokens in AWS SSM —
#the same ones billing_matrix_automation/insurance_spreadsheet_to_sql/insurance_to_db.py
#uses (procore_copilot/procore_auth.py). whatever that Procore user can see and
#edit is what this endpoint can see and edit.
#
#SsmTokens has the same access_token()/refresh() shape as sub_chaser's
#WorkerTokens, so sub_chaser's procoreGet() and uploadAttachments() take it as-is.

import threading

from sub_chaser.procore_fetch import procoreGet

COMPANY_ID = "71927"
BASE = "https://api.procore.com"

#procore rotates the refresh token on every refresh, so two requests refreshing
#at once would leave one holding a dead token. serialize refreshes in-process.
_REFRESH_LOCK = threading.Lock()


class SsmTokens:

    #procore_auth is imported here rather than at the top: it raises on import
    #when PROCORE_COPILOT_CLIENT_ID/SECRET are missing from .env, and at module
    #level that would take the whole server down instead of just this endpoint

    def __init__(self):
        from procore_copilot.procore_auth import load_tokens
        self._tokens = load_tokens()

    def access_token(self):
        return self._tokens["access_token"]

    def refresh(self):
        from procore_copilot.procore_auth import load_tokens, refresh_access_token
        with _REFRESH_LOCK:
            #another request (or the insurance cron) may have refreshed already —
            #if SSM holds a newer token than ours, just use that one
            latest = load_tokens()
            if latest["access_token"] != self._tokens["access_token"]:
                self._tokens = latest
                return self._tokens["access_token"]

            self._tokens = refresh_access_token(latest)
            return self._tokens["access_token"]


class ProcoreError(Exception):
    pass


def _get(url, tokens, params=None):
    response = procoreGet(url, COMPANY_ID, params=params, tokens=tokens)
    if response.status_code != 200:
        raise ProcoreError(f"GET {url} returned {response.status_code}: {response.text[:300]}")
    return response


def _get_all_pages(url, tokens, params=None):

    #v1 endpoints return a bare list (+ Total header); v2 wraps it in {"data": [...]}

    items = []
    page = 1
    base_params = dict(params or {})
    base_params["per_page"] = 100

    while True:
        base_params["page"] = page
        response = _get(url, tokens, params=base_params)
        batch = response.json()
        if isinstance(batch, dict):
            batch = batch.get("data") or []
        items.extend(batch)

        total = int(response.headers.get("Total", 0) or 0)
        if not batch or len(batch) < 100 or (total and len(items) >= total):
            break
        page += 1

    return items


def get_projects(tokens):
    #active projects only — same filter insurance_to_db uses
    return _get_all_pages(
        f"{BASE}/rest/v1.1/projects",
        tokens,
        params={"company_id": COMPANY_ID, "filters[by_status]": "Active"},
    )


def get_subcontracts(project_id, tokens):

    #all subcontracts (WorkOrderContracts) on the project in one paginated call.
    #same endpoint + filter as subcontractor_info_retrieval.getWorkOrderContracts.
    #
    #v2.0 only gives vendor {"id": "..."} — no name (verified on Tatte 3254966).
    #getWorkOrderContracts looks each name up one call at a time; instead we pull
    #the project's whole vendor list once and join on id.

    rows = _get_all_pages(
        f"{BASE}/rest/v2.0/companies/{COMPANY_ID}/projects/{project_id}/commitment_contracts",
        tokens,
        params={"filters[type]": "WorkOrderContract"},
    )
    vendors = _get_all_pages(
        f"{BASE}/rest/v1.1/projects/{project_id}/vendors",
        tokens,
        params={"company_id": COMPANY_ID},
    )
    vendor_names = {str(v.get("id")): v.get("name") for v in vendors}

    return [{
        "commitment_id": row.get("id"),
        "number": row.get("number"),
        "title": row.get("title"),
        "status": row.get("status"),
        "vendor_name": vendor_names.get(str((row.get("vendor") or {}).get("id"))),
    } for row in rows]


def get_billing_periods(project_id, tokens):
    #{period_id: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}
    rows = _get(f"{BASE}/rest/v1.0/projects/{project_id}/billing_periods", tokens).json()
    return {
        str(p.get("id")): {"start": p.get("start_date"), "end": p.get("end_date")}
        for p in rows or []
    }


def get_requisitions(project_id, commitment_id, tokens):
    #every invoice on one subcontract, any status/period
    return _get_all_pages(
        f"{BASE}/rest/v1.1/requisitions",
        tokens,
        params={"project_id": project_id, "filters[commitment_id]": commitment_id},
    )
