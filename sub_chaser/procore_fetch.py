#Procore fetching for Sub-Chaser 2000.
#
#Auth works the same as everything else in this backend: the user signs in via
#Procore OAuth (routes/auth.py), their tokens live in the Flask session
#(auth/tokenStore.py), and here we just load_tokens() and refresh on 401 —
#exactly like billing_matrix_automation/subcontractor_info_retrieval.py.
#
#Billing periods and commitments are NOT re-implemented — routes reuse
#getBillingPeriods() and getWorkOrderContracts() from subcontractor_info_retrieval.

import requests
from auth.tokenStore import load_tokens
from auth.getTokens import refresh_and_store_tokens


#Every call below takes an optional `tokens`:
#   tokens=None  -> read the user's tokens straight from the flask session
#                   (routes, which run inside a request)
#   tokens=<WorkerTokens> -> the same user's tokens, captured by the route and
#                   handed to a background thread (flask session is unavailable there)
#Either way these are the SIGNED-IN USER'S Procore OAuth tokens.

def getAccessToken(tokens=None):
    if tokens is None:
        data = load_tokens()
        if not data or not data.get("access_token"):
            raise PermissionError("Not signed in to Procore. Sign in and try again.")
        return data["access_token"]
    return tokens.access_token()


def refreshAccessToken(tokens=None):
    print("need to refresh access token. will try again")
    if tokens is None:
        return refresh_and_store_tokens()["access_token"]
    return tokens.refresh()


def procoreGet(url, company_id, params=None, tokens=None):

    #one GET with the app's standard 401 -> refresh -> retry pattern

    def send(access_token):
        return requests.get(url, headers={
            'Authorization': f'Bearer {access_token}',
            "Procore-Company-Id": str(company_id),
            "Accept": "application/json"
        }, params=params, timeout=60)

    response = send(getAccessToken(tokens))
    print("Status:", response.status_code)

    if(response.status_code == 401):
        response = send(refreshAccessToken(tokens))
        print("Status:", response.status_code)

    return response


def getRequisitionsForPeriod(company_id, project_id, period_id, tokens=None):

    #all subcontractor invoices (requisitions) for one billing period, any status.
    #filters[period_id] is applied server-side, so this is exactly the period's invoices.
    #each row already carries its vendor + commitment linkage (structured, from the API).

    response = procoreGet(
        "https://api.procore.com/rest/v1.1/requisitions",
        company_id,
        params={
            "project_id": project_id,
            "filters[period_id]": period_id,
            "per_page": 100,
        },
        tokens=tokens,
    )

    if(response.status_code != 200):
        return None

    return response.json()


def getWorkOrderContractsLite(company_id, project_id, tokens=None):

    #commitment_id -> SC number + vendor for the project's work order contracts.
    #this is the thread-safe version of billing_matrix_automation's
    #getWorkOrderContracts: same endpoint, but no per-commitment cost-code or
    #vendor-name lookups (sub-chaser only needs the SC number, and the vendor
    #name is already on the requisition).

    response = procoreGet(
        f"https://api.procore.com/rest/v2.0/companies/{company_id}/projects/{project_id}/commitment_contracts",
        company_id,
        params={"filters[type]": "WorkOrderContract", "per_page": 100, "page": 1},
        tokens=tokens,
    )

    if(response.status_code != 200):
        return []

    ans = []
    for sub in response.json().get('data') or []:
        vendor = sub.get('vendor') or {}
        ans.append({
            'commitment_id': sub.get('id'),
            'commitment_number': sub.get('number'),
            'commitment_status': sub.get('status'),
            'subcontractor_name': vendor.get('name'),
            'vendor_id': vendor.get('id'),
        })

    return ans


def getRequisitionCommitmentId(requisition):

    #the commitment a requisition bills against. field name differs between
    #procore api versions, so check the known candidates.

    if requisition.get('commitment_id'):
        return requisition.get('commitment_id')
    if requisition.get('contract_id'):
        return requisition.get('contract_id')
    contract = requisition.get('contract') or {}
    return contract.get('id')


def parseAmount(value):

    #procore returns money as strings ("0.00", "1,234.50"). returns None if it
    #can't be read as a number, so callers can tell "zero" from "unknown".

    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def isFinalInvoice(requisition):

    #FINAL vs PARTIAL, which decides whether the sub owes a final or a partial
    #lien waiver.
    #
    #Line 9 on the AIA — "Balance to finish, including retainage (Line 3 less
    #Line 6)" — is summary.balance_to_finish_including_retainage. $0.00 means
    #nothing left to bill, i.e. this is their final invoice.
    #
    #Returns None when the summary is missing, so the UI can say "unknown"
    #rather than guessing wrong and naming the wrong waiver.

    summary = requisition.get('summary') or {}
    balance = parseAmount(summary.get('balance_to_finish_including_retainage'))
    if balance is None:
        return None
    return balance == 0


def cleanRequisition(requisition, work_order_contracts):

    #one row of invoice -> commitment -> vendor linkage for the UI.
    #work_order_contracts is the list from getWorkOrderContracts() — the
    #commitment number there IS the SC number.

    commitment_id = getRequisitionCommitmentId(requisition)

    #compare as strings — the v2.0 commitments endpoint returns ids as strings
    #while requisitions return them as ints
    matching_contract = None
    for contract in (work_order_contracts or []):
        if str(contract.get('commitment_id')) == str(commitment_id):
            matching_contract = contract
            break

    vendor_name = requisition.get('vendor_name')
    if not vendor_name:
        vendor_name = (requisition.get('vendor') or {}).get('name')
    if not vendor_name and matching_contract:
        vendor_name = matching_contract.get('subcontractor_name')

    summary = requisition.get('summary') or {}

    return {
        'requisition_id': requisition.get('id'),
        #'number' is the application/req number (1, 2, 3...); 'invoice_number'
        #is a free-text label (e.g. "FINAL") — keep both
        'application_number': requisition.get('number'),
        'invoice_number': requisition.get('invoice_number') or requisition.get('number'),
        'billing_date': requisition.get('billing_date'),
        'status': requisition.get('status'),
        'commitment_id': commitment_id,
        'sc_number': matching_contract.get('commitment_number') if matching_contract else None,
        'vendor_name': vendor_name,
        #the company id — what we resolve contacts against
        'vendor_id': (requisition.get('vendor_id')
                      or (requisition.get('vendor') or {}).get('id')
                      or (matching_contract.get('vendor_id') if matching_contract else None)),
        'attachment_count': len(requisition.get('attachments') or []),
        #current_payment_due (from the G702-style summary) is what's actually due
        #this period — same field the billing matrix uses. total_claimed_amount is
        #new work claimed, which is 0.00 on retainage-release / FINAL reqs.
        'current_payment_due': summary.get('current_payment_due'),
        'percent_complete': requisition.get('percent_complete'),
        'total_claimed_amount': requisition.get('total_claimed_amount'),
        #final vs partial — drives which lien waiver the sub owes.
        #True/False, or None when the summary didn't come through.
        'is_final': isFinalInvoice(requisition),
        'balance_to_finish': summary.get('balance_to_finish_including_retainage'),
    }
