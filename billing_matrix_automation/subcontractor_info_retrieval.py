from pathlib import Path
import requests
import re
import os
import json
import sqlite3
from datetime import date, datetime, timedelta
from auth.getTokens import refresh_and_store_tokens
from auth.tokenStore import load_tokens
#from billing_matrix_automation.billing_matrix_creator import build_billing_matrix_xlsx
from pathlib import Path


#NEED FUNCTIONS FOR THE FOLLOWING:
#1 DONE: get list of all WorkOrderContracts for a given project

#2 DONE: get list of all billing periods for a project

#3 DONE: for a given sub, look at all their invoices, if they have an invoice that matches the given billing period, 
#3(cont.) DONE: store that invoice number, amount to be paid and percentage billed of their contract. If none for that billing period
#3(cont.) DONE: save the invoice number and percentage billed for the most recent billing period before the specified period.
#3(cont.) DONE: if the req is in this billing period and the summary["balance_to_finish_including_retainage"]=0, this will be "retainage released"
#3(cont.) DONE: if the req is before this billing period and the summary["balance_to_finish_including_retainage"]=0, this will be "retainage released in previous req"

#4: WAITING FOR INFO FROM FRANK fetch the subcontractor's expiration dates for their G/L, Workers Comp, Automobile, and Umbrella insurance.

#5: DONE function that brings it all together. uses other functions to get data for the following:
#         - list of all subs for a project. For each sub
#               - Sub name (from #1)
#               - contract title (from #1)
#               - LOA status (signed or not signed) (probably from #1)
#               - Trade (probably from #1)
#               - Cost Code (probably from #1)
#               - Requisition number (from #3, might be null if no invoices at or before specified billing period)
#               - Payment due this period (from #3, will be null if no invoices this period)
#               - % Complete (from #3, might be null if no invoices at or before specified billing period)
#               - Notes. Will be one of 3 things (from #3):
#                   - "retainage released"
#                   - "retainage released in previous req"
#                   - null


#----------------------------------------------------------------------------------
#func 1 logic

def getCommitmentCostCode(company_id, project_id, commitment_id):
    data = load_tokens()

    url = f"https://api.procore.com/rest/v2.0/companies/{company_id}/projects/{project_id}/commitment_contracts/{commitment_id}/line_items"

    response = requests.get(url, headers={
        'Authorization': f'Bearer {data["access_token"]}',
        "Procore-Company-Id": str(company_id), 
        "Accept": "application/json"
    })

    print("Status:", response.status_code)
    if(response.status_code == 401):
        print("need to refresh access token. will try again")
        data = refresh_and_store_tokens()
        response = requests.get(url, headers={
            'Authorization': f'Bearer {data["access_token"]}',
            "Procore-Company-Id": str(company_id), 
            "Accept": "application/json"
        })
        print("Status:", response.status_code)
    
    if(response.status_code != 200):
        return None
    
    response_json = response.json()
    response_data = response_json.get('data')


    if len(response_data) > 0:
        wbs_code = response_data[0].get('wbs_code').get('flat_code')
        if '.' in wbs_code:
            prefix = wbs_code.split(".")[0]
        else:
            prefix = wbs_code[:6]
        return prefix
    else:
        return None

def getSubName(company_id, project_id, vendor_id):
    data = load_tokens()

    url = f"https://api.procore.com/rest/v1.1/projects/{project_id}/vendors/{vendor_id}"


    response = requests.get(url, headers={
        'Authorization': f'Bearer {data["access_token"]}',
        "Procore-Company-Id": str(company_id), 
        "Accept": "application/json"
    })

    print("Status:", response.status_code)
    if(response.status_code == 401):
        print("need to refresh access token. will try again")
        data = refresh_and_store_tokens()
        response = requests.get(url, headers={
            'Authorization': f'Bearer {data["access_token"]}',
            "Procore-Company-Id": str(company_id), 
            "Accept": "application/json"
        })
        print("Status:", response.status_code)
    
    if(response.status_code != 200):
        return None

    vendor_info = response.json()
    return vendor_info.get('name')


def getWorkOrderContracts(company_id, project_id):

    #func #1,  gets all work order contracts for a job. Returns a list of the
    #commitmentID, commitment status, commitment number, trade, cost code, and subcontractor name

    data = load_tokens()

    params = {
        "filters[type]": "WorkOrderContract",
        "per_page": 100,
        "page": 1,
    }
   
    response = requests.get(f"https://api.procore.com/rest/v2.0/companies/{company_id}/projects/{project_id}/commitment_contracts", headers={
        'Authorization': f'Bearer {data["access_token"]}',
        "Accept": "application/json",
        "Procore-Company-Id": str(company_id)
    }, params=params)

    print("Status:", response.status_code)
    if(response.status_code == 401):
        print("need to refresh access token. will try again")
        data = refresh_and_store_tokens()
        response = requests.get(f"https://api.procore.com/rest/v2.0/companies/{company_id}/projects/{project_id}/commitment_contracts", headers={
            'Authorization': f'Bearer {data["access_token"]}',
            "Accept": "application/json",
            "Procore-Company-Id": str(company_id)
        },params=params)
        print("Status:", response.status_code)
    if(response.status_code != 200):
        return
    
    
    response_data = response.json()

    data = response_data.get('data')
    ans = []

    BASE_DIR = Path(__file__).resolve().parent
    json_path = BASE_DIR / "cost_codes.json"

    with open(json_path, "r") as f:
        COST_CODES = json.load(f)

    for sub in data:
        #print(sub)
        vendor = sub.get('vendor')
        vendor_id = vendor.get('id')
        vendor_name = getSubName(company_id, project_id, vendor_id)
        cost_code = getCommitmentCostCode(company_id, project_id, sub.get('id'))

        sub_data = {
            'commitment_status': sub.get('status'), 
            'commitment_number': sub.get('number'), 
            'commitment_id': sub.get('id'),
            'subcontractor_name': vendor_name,
            'vendor_id': vendor_id,
            'cost_code': None,
            'trade': None,
        }

        if cost_code:
            sub_data['cost_code'] = cost_code
            
            sub_data['trade'] = COST_CODES.get(cost_code)
            


        ans.append(sub_data)

    return ans




#----------------------------------------------------------------------------------
#func 2 logic


def get_month_from_start_date(start_date: str) -> str:
    date_obj = datetime.strptime(start_date, "%Y-%m-%d")
    return date_obj.strftime("%B")

def get_year_from_start_date(start_date: str) -> str:
    date_obj = datetime.strptime(start_date, "%Y-%m-%d")
    return date_obj.strftime("%Y")


def getBillingPeriods(company_id, project_id):

    #given a certain project, this returns a list of each billing period (sorted by first to last)
    #each object in the list contains the periodID, due date, start date, end date, position, and month

    data = load_tokens()

    url = f"https://api.procore.com/rest/v1.0/projects/{project_id}/billing_periods"

    response = requests.get(url, headers={
        'Authorization': f'Bearer {data["access_token"]}',
        "Procore-Company-Id": str(company_id), 
        "Accept": "application/json"
    })

    print("Status:", response.status_code)
    if(response.status_code == 401):
        print("need to refresh access token. will try again")
        data = refresh_and_store_tokens()
        response = requests.get(url, headers={
            'Authorization': f'Bearer {data["access_token"]}',
            "Procore-Company-Id": str(company_id), 
            "Accept": "application/json"
        })
        print("Status:", response.status_code)
    
    if(response.status_code != 200):
        return None
    
    response_json = response.json()
    if not response_json:
        raise ValueError(f"No billing periods found for project")
    elif len(response_json) == 0:
        raise ValueError(f"No billing periods found for project")

    sorted_billing_periods = sorted(response_json, key=lambda x: x['position'])

    cleaned_sorted_billing_periods = []

    for period in sorted_billing_periods:
        cleaned = {
            'period_id': period.get('id'),
            'due_date': period.get('due_date'), 
            'end_date': period.get('end_date'),
            'start_date': period.get('start_date'),
            'position': period.get('position'),
            'month': get_month_from_start_date(period.get('start_date')),
            'year': get_year_from_start_date(period.get('start_date')),
        }

        cleaned_sorted_billing_periods.append(cleaned)

    return cleaned_sorted_billing_periods



#----------------------------------------------------------------------------------
#func 3 logic


def getInvoiceForPeriod(company_id, project_id, commitment_id, position, cleaned_sorted_billing_periods):

    #this function will take the company_id, project, id, billing period's position, and the list of cleaned and sorted billing periods from func 2
    #it will return one of 3 things:
    #     1. info on the requisition for that period, including amount due, req number, percent complete and notes
    #     2. info for the most previous req before specified period. with info about req number percent complete and notes.
    #     3. null values for all those data fields, because the sub has not submitted an invoice at all yet.
    
    data = load_tokens()

    url = f"https://api.procore.com/rest/v1.1/requisitions?project_id={project_id}"

    period_location = position-1
    period_id = cleaned_sorted_billing_periods[period_location].get('period_id')

    params = {
        "filters[commitment_id]": commitment_id,
        "filters[period_id]": period_id,
    }
   
    response = requests.get(url, headers={
        'Authorization': f'Bearer {data["access_token"]}',
        "Accept": "application/json",
        "Procore-Company-Id": str(company_id)
    }, params=params)

    print("Status:", response.status_code)
    if(response.status_code == 401):
        print("need to refresh access token. will try again")
        data = refresh_and_store_tokens()
        response = requests.get(url, headers={
            'Authorization': f'Bearer {data["access_token"]}',
            "Accept": "application/json",
            "Procore-Company-Id": str(company_id)
        },params=params)
        print("Status:", response.status_code)
    if(response.status_code != 200):
        return
    
    
    response_json = response.json()

    #IF there is an invoice for this period and the given sub, return the necessary info
    if len(response_json) > 0:

        invoice = response_json[0]
        summary = invoice.get('summary') or {}
        
        ans = {
            'percent_complete': invoice.get('percent_complete'),
            'current_payment_due': summary.get('current_payment_due'),
            'application_number': invoice.get('number'),
            'notes': '',
        }
        if summary:
            if summary.get('balance_to_finish_including_retainage') == '0.00':
                ans['notes'] = "Retainage released. "
        return ans
    
    #If the previous if statement didnt return, then we will move on to looking in previous reqs

    period_location -=1

    while period_location >= 0:
        period_id = cleaned_sorted_billing_periods[period_location].get('period_id')

        if not period_id:
            period_location -=1
            continue
        
        params = {
            "filters[commitment_id]": commitment_id,
            "filters[period_id]": period_id,
        }
    
        response = requests.get(url, headers={
            'Authorization': f'Bearer {data["access_token"]}',
            "Accept": "application/json",
            "Procore-Company-Id": str(company_id)
        }, params=params)

        print("Status:", response.status_code)
        if(response.status_code == 401):
            print("need to refresh access token. will try again")
            data = refresh_and_store_tokens()
            response = requests.get(url, headers={
                'Authorization': f'Bearer {data["access_token"]}',
                "Accept": "application/json",
                "Procore-Company-Id": str(company_id)
            },params=params)
            print("Status:", response.status_code)
        if(response.status_code != 200):
            return
        
        
        response_json = response.json()
        
        if len(response_json) > 0:

            invoice = response_json[0]
            summary = invoice.get('summary') or {}
            
            ans = {
                'percent_complete': invoice.get('percent_complete'),
                'current_payment_due': None,
                'application_number': invoice.get('number'),
                'notes': '',
            }
            if summary:
                if summary.get('balance_to_finish_including_retainage') == '0.00':
                    ans['notes'] = "Retainage released in previous reqs. "
            return ans
        
        period_location -=1

    return {'percent_complete': None, 'current_payment_due': None, 'application_number': None, 'notes': ''}
    




#---------------------------------------------------------------------------------------------------------------------
#Function 4 logic




BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "insurance_spreadsheet_to_sql" / "vendor_insurance.db"

def getVendorInsurance(project_number, sub_name):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    row = conn.execute("""
        SELECT umbrella_insurance, general_liability_insurance,
               automobile_insurance, workers_comp_insurance, updated
        FROM vendor_insurance
        WHERE project_number = ? AND vendor_name = ?
    """, (project_number, sub_name)).fetchone()

    conn.close()

    NOT_AVAILABLE = "Insurance Info Not Available"

    if row is None:
        return {
            "gl_insurance":           NOT_AVAILABLE,
            "auto_insurance":         NOT_AVAILABLE,
            "umbrella_insurance":     NOT_AVAILABLE,
            "workers_comp_insurance": NOT_AVAILABLE,
            "last_updated":           NOT_AVAILABLE,
        }

    return {
        "gl_insurance":           row["general_liability_insurance"] or NOT_AVAILABLE,
        "auto_insurance":         row["automobile_insurance"]        or NOT_AVAILABLE,
        "umbrella_insurance":     row["umbrella_insurance"]          or NOT_AVAILABLE,
        "workers_comp_insurance": row["workers_comp_insurance"]      or NOT_AVAILABLE,
        "last_updated":           row["updated"]                     or NOT_AVAILABLE,
    }




#---------------------------------------------------------------------------------------------------------------------
#Function 5 logic



def getProjectData(company_id, project_id):
    data = load_tokens()

    url = f"https://api.procore.com/rest/v1.0/projects/{project_id}?company_id={company_id}"

    

    response = requests.get(url, headers={
        'Authorization': f'Bearer {data["access_token"]}',
        "Procore-Company-Id": str(company_id), 
        "Accept": "application/json"
    })

    print("Status:", response.status_code)
    if(response.status_code == 401):
        print("need to refresh access token. will try again")
        data = refresh_and_store_tokens()
        response = requests.get(url, headers={
            'Authorization': f'Bearer {data["access_token"]}',
            #"Procore-Company-Id": str(company_id), 
            "Accept": "application/json"
        })
        print("Status:", response.status_code)
    
    if(response.status_code != 200):
        return None
    
    response_json = response.json()

    #print(response_json)
    return {
        "project_name": response_json.get('name'),
        "project_number": response_json.get('project_number'),
    }
    



#5: function that brings it all together. uses other functions to get data for the following:
#         - list of all subs for a project. For each sub
#               - Sub name (from #1)
#               - contract title (from #1)
#               - LOA status (signed or not signed) (probably from #1)
#               - Trade (probably from #1)
#               - Cost Code (probably from #1)
#               - Requisition number (from #3, might be null if no invoices at or before specified billing period)
#               - Payment due this period (from #3, will be null if no invoices this period)
#               - % Complete (from #3, might be null if no invoices at or before specified billing period)
#               - Notes. Will be one of 3 things (from #3):
#                   - "retainage released"
#                   - "retainage released in previous req"
#                   - null



def buildCompleteRowDataForEachCommitment(company_id, project_id, position):


    data = load_tokens()

    #each commitment will contain the Status, Commitment number, Sub Name, Cost code, Trade
    all_commitments = getWorkOrderContracts(company_id, project_id)

    cleaned_sorted_billing_periods = getBillingPeriods(company_id, project_id)

    billing_period = cleaned_sorted_billing_periods[position-1] or {}
    month = billing_period.get('month')
    year = billing_period.get('year')


    proj_data = getProjectData(company_id, project_id)

    rows_data = []

    for comm in all_commitments:

        commitment_id = comm.get('commitment_id')
        vendor_id = comm.get('vendor_id')

        #contains the percent_complete, current_payment_due, application_number, notes
        req_info = getInvoiceForPeriod(company_id, project_id, commitment_id, position, cleaned_sorted_billing_periods)
        notes = req_info.get('notes')


        sub_name = comm.get('subcontractor_name')
        #contains gl_insurance, auto_insurance, umbrella_insurance, workers_comp_insurance EXPIRATION DATES and the last time the 
        insurance_info = getVendorInsurance(proj_data.get("project_number"), sub_name)
        last_updated = insurance_info.get("last_updated")

        #if the last time the insurance info was updated was over a month ago. it adds a note to review this info.
        if last_updated != "Insurance Info Not Available":
            last_updated_date = datetime.strptime(last_updated, "%Y-%m-%d").date()
            if last_updated_date < date.today() - timedelta(days=30):
                notes = f"{notes} Review Insurance, info may be outdated"

        

        row_data = {
            "loa_status": comm.get('commitment_status'),
            "comm_no.": comm.get('commitment_number'),
            "subcontractor": sub_name,
            "trade": comm.get('trade'),
            "cost_code": comm.get('cost_code'),
            

            "sub_req_number": req_info.get('application_number'),
            "req_amount": req_info.get('current_payment_due'),
            "percent_complete": req_info.get('percent_complete'),
            "notes": notes,

            "gl_insurance": insurance_info.get('gl_insurance'),
            "auto_insurance": insurance_info.get('auto_insurance'),
            "umbrella_insurance": insurance_info.get('umbrella_insurance'),
            "workers_comp_insurance": insurance_info.get('workers_comp_insurance'),
        }

        rows_data.append(row_data)

    proj_data = getProjectData(company_id, project_id)

    proj_data["rows_data"] = rows_data
    proj_data["month"] = month
    proj_data["year"] = year
    proj_data["position"] = position

    print(proj_data)
    return proj_data





#data = buildCompleteRowDataForEachCommitment("71927", "3389950", 1)
#ans = build_billing_matrix_xlsx(data, "template_billing_matrix.xlsx", "new_billing_matrix.xlsx")
#print(ans)

    