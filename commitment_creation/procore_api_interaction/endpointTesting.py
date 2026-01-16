import requests
import json
import os
from auth.getTokens import refresh_and_store_tokens
from auth.tokenStore import load_tokens
from commitment_creation.procore_api_interaction.helper_functions.getProjectData import getSubNameMatch, getVendorUsers
from commitment_creation.procore_api_interaction.helper_functions.helpers import getDescription
from datetime import datetime


'''
sub_info = {
    "vendor_selected": "Dragorgul",
    "trade": "HVAC",
    "cost_code": "15-500",
    "subcontract_amount": 7000,

    "exhibit_a_length": 4,
    "exhibit_b_length": 6,
    "exhibit_b_date": "12/23/2025",
    "exhibit_b1_length": 4,
    "exhibit_b1_date": "6/9/2025",
    "exhibit_c_length": 3,
    "exhibit_d_length": 4,

    "company_id": "4264340",
    "project_id": "116704",
    "contract_number": "SC23047G 01",
}
'''

def makeRequest(sub_info):
    company_id = sub_info["company_id"]
    project_id = sub_info["project_id"]
    data = load_tokens()

    #vendor is determined through getSubNameMatch function. 
    vendor_obj = getSubNameMatch(company_id, project_id, sub_info["vendor_selected"])
    
    if(vendor_obj):
        vendor_id = vendor_obj['id']
        bill_recipients_and_accessors = getVendorUsers(company_id, project_id, vendor_id)
        vendor_name = vendor_obj['company']
    else:
        vendor_id = None
        bill_recipients_and_accessors = None
        vendor_name = None

    #description is created    
    desc = getDescription(vendor_name, sub_info["exhibit_a_length"], sub_info["exhibit_b_date"], sub_info["exhibit_b_length"], sub_info["exhibit_b1_date"], sub_info["exhibit_b1_length"], sub_info["exhibit_c_length"], sub_info["exhibit_d_length"], sub_info["exhibit_h_length"])


    #getContractTitle(company_id, project_id, incriment, data)
    number = sub_info["contract_number"]

    #get the project being and finish dates
    start_date = sub_info.get("project_start_date") or None
    finish_date = sub_info.get("project_finish_date") or None
    

    body = {
        "type": "WorkOrderContract",
        "number": number, #DONE get from show project endpoint 
        "status": "Draft",
        "title": f"{sub_info['cost_code']} {sub_info['trade']}", #get from buyout cover sheet. Formatted like "<cost code> <Trade>"
        "vendor_id": vendor_id, #difficult, find vendor name and fuzzy match with vendor directory endpoint          
        "contract_date": datetime.now().strftime("%Y-%m-%d"),
        "description": desc, #probably will need to be a table, get dates from contract date and maybe analysis on b1 doc. Document lengths will also be used
        "executed": False,
        "signature_required": True,
        "billing_schedule_of_values_status": "draft",
        "retainage_percent": "5.0",
        "accounting_method": "amount",
        "allow_comments": False,
        "allow_markups": False,
        "enable_ssov": True,
        "change_order_level_of_detail": "line_item",
        "allow_payment_applications": True,
        "allow_payments": True,
        "display_materials_retainage": True,
        "display_work_retainage": True,
        "show_cost_code_on_pdf": True,
        "bill_recipient_ids": bill_recipients_and_accessors, #DONE get using project vendors endpoint and then use the project directory endpoint with vendor_id filter and **USE THE user_id NOT regular ID**
        "private": True,
        "show_line_items_to_non_admins": True, 
        "accessor_ids": bill_recipients_and_accessors, #DONE SAME AS BILL RECIPIENT ID
        
        
        "contract_estimated_completion_date": finish_date,
        "contract_start_date": start_date,
        
    }
    
    

    
    response = requests.post(f"https://api.procore.com/rest/v2.0/companies/{company_id}/projects/{project_id}/commitment_contracts", json=body, headers={
        'Authorization': f'Bearer {data["access_token"]}',
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Procore-Company-Id": str(company_id), 
    })

    print("Status:", response.status_code)
    if(response.status_code == 401):
        print("need to refresh access token. will try again")

        data = refresh_and_store_tokens()
        response = requests.post(f"https://api.procore.com/rest/v2.0/companies/{company_id}/projects/{project_id}/commitment_contracts", json=body, headers={
            'Authorization': f'Bearer {data["access_token"]}',
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Procore-Company-Id": str(company_id), 
        })
        print("Status:", response.status_code)


    # If Procore request failed decrease the contract number incriment by 1 before continuing so that we dont skip a number this will be done in route in server file tho. 
    
        
        
    return response


#response = makeRequest()

'''
try:
    data = response.json()
    print(data)
        
except:
    print(response.text)
''' 