import requests
import re
import os
import json
from auth.tokenStore import load_tokens, save_tokens
from auth.getTokens import refresh_and_store_tokens
#from helper_functions.helpers import load_tokens, save_tokens, refresh_access_token, loadComsPerProj, saveComsPerProj
from commitment_creation.procore_api_interaction.helper_functions.fuzzyNameMatching import subNameMatcher

def getContractTitle(company_id, proj_id, incriment):
    #function calls the endpoint to get the project number. It then returns the project title in the following format "SC{projectNumber} 01"
    data = load_tokens()

    response = requests.get(f"https://api.procore.com/rest/v1.0/projects/{proj_id}?company_id={company_id}", headers={
        'Authorization': f'Bearer {data["access_token"]}',
        "Accept": "application/json",
        "Procore-Company-Id": str(company_id), 
    })

    print("Status:", response.status_code)
    if(response.status_code == 401):
        print("need to refresh access token. will try again")
        data = refresh_and_store_tokens()
        response = requests.get(f"https://api.procore.com/rest/v1.0/projects/{proj_id}?company_id={company_id}", headers={
            'Authorization': f'Bearer {data["access_token"]}',
            "Accept": "application/json",
            "Procore-Company-Id": str(company_id), 
        })
        print("Status:", response.status_code)
    if(response.status_code != 200):
        return
    
    
    response_data = response.json()
    cleaned = response_data["project_number"].replace("-", "")
    if incriment <10:
        incriment = f"0{incriment}"
    ans = f"SC{cleaned}-{incriment}"
    return ans





def getNumCommitments(company_id, project_id):
    data = load_tokens()
    url = f"https://api.procore.com/rest/v2.0/companies/{company_id}/projects/{project_id}/commitment_contracts"

    headers = {
        "Authorization": f'Bearer {data["access_token"]}',
        "Procore-Company-Id": str(company_id),
        "Accept": "application/json",
    }

    last_seq = 0
    page = 1
    per_page = 100

    while True:
        params = {
            "filters[type]": "WorkOrderContract",
            "page": page,
            "per_page": per_page,
        }

        resp = requests.get(url, params=params, headers=headers)

        if resp.status_code == 401:
            data = refresh_and_store_tokens()
            headers["Authorization"] = f'Bearer {data["access_token"]}'
            resp = requests.get(url, params=params, headers=headers)

        if resp.status_code != 200:
            print("commitment_contracts paging stopped:",
                  resp.status_code, resp.text[:300])
            break  # return best value found so far

        payload = resp.json()
        contracts = payload.get("data", []) if isinstance(payload, dict) else payload

        if not contracts:
            break  # no more pages

        for contract in contracts:
            num_str = (contract.get("number") or "").strip()
            m = re.search(r"(\d+)\s*$", num_str)
            if m:
                last_seq = max(last_seq, int(m.group(1)))

        # if we got fewer than per_page, we’re done
        if len(contracts) < per_page:
            break

        page += 1

    return last_seq

def getVendorUsers(company_id, project_id, vendor_id):
    #this function will return a list of the user_id's for each user associated with the given vendor_id

    data = load_tokens()

    url = f"https://api.procore.com/rest/v1.0/projects/{project_id}/users"

    params = {
        "filters[vendor_id]": [vendor_id]
    }

    response = requests.get(url, params=params, headers={
        'Authorization': f'Bearer {data["access_token"]}',
        "Procore-Company-Id": str(company_id), 
        "Accept": "application/json"
    })

    print("Status:", response.status_code)
    if(response.status_code == 401):
        print("need to refresh access token. will try again")
        data = refresh_and_store_tokens()
        response = requests.get(url, params=params, headers={
            'Authorization': f'Bearer {data["access_token"]}',
            "Procore-Company-Id": str(company_id), 
            "Accept": "application/json"
        })
        print("Status:", response.status_code)
    
    if(response.status_code != 200):
        return []
    
    rawData = response.json()
    ans = []
    for item in rawData:
        ans.append(str(item["id"]))

    return ans



def getSubNameMatch(company_id, project_id, subName):

    data = load_tokens()

    url = f"https://api.procore.com/rest/v1.1/projects/{project_id}/vendors"

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
    
    rawData = response.json()
    return subNameMatcher(rawData, subName)





#LINE ITEM FUNCTIONS BELOW

def getWbsCodes(company_id, project_id):
    data = load_tokens()

    url = f"https://api.procore.com/rest/v1.0/projects/{project_id}/work_breakdown_structure/wbs_codes"

    headers = {
        "Authorization": f'Bearer {data["access_token"]}',
        "Procore-Company-Id": str(company_id),
        "Accept": "application/json",
        #"Content-Type": "application/json",
    }

    response = requests.get(url,headers=headers)

    print(f"Status Code: {response.status_code}")
    if(response.status_code == 401):
        print("have to refresh access tokens")
        data = refresh_and_store_tokens()

        response = requests.get(url,headers=headers)
        print(f"Status Code: {response.status_code}")
    
    if response.status_code not in (200, 201): 
        return None
    
    rawData = response.json()
    return rawData


def getWbsSegment(company_id, project_id, segment_type):
    data = load_tokens()
    url = f"https://api.procore.com/rest/v1.0/projects/{project_id}/work_breakdown_structure/segments"

    headers = {
        "Authorization": f"Bearer {data['access_token']}",
        "Procore-Company-Id": str(company_id),
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 401:
        data = refresh_and_store_tokens()
        headers["Authorization"] = f"Bearer {data['access_token']}"
        response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print("Failed to fetch WBS segments:", response.status_code, response.text)
        return None

    segments = response.json()

    
    for seg in segments:
        if seg.get("type") == segment_type:
            return seg.get("id")

    # If not found
    print(f"{segment_type} segment not found in WBS segments")
    return None

def getMatchingCostCodeSegmentItemId(company_id, project_id, segment_id, cost_code):
    data = load_tokens()

    url = f"https://api.procore.com/rest/v1.0/projects/{project_id}/work_breakdown_structure/segments/{segment_id}/segment_items"

    headers = {
        "Authorization": f"Bearer {data['access_token']}",
        "Procore-Company-Id": str(company_id),
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 401:
        data = refresh_and_store_tokens()
        headers["Authorization"] = f"Bearer {data['access_token']}"
        response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print("Failed to fetch WBS segment items:", response.status_code, response.text)
        return None

    segment_items = response.json()
    
    for item in segment_items:
        if item.get("path_code") == cost_code:
            return item.get("id")

    
    # If not found
    print(f"Cost Code segment item not found with corresponding cost code. status code {response.status_code}")
    return None

def getMatchingCommitmentSegmentItemId(company_id, project_id, segment_id):
    data = load_tokens()

    url = f"https://api.procore.com/rest/v1.0/projects/{project_id}/work_breakdown_structure/segments/{segment_id}/segment_items"

    headers = {
        "Authorization": f"Bearer {data['access_token']}",
        "Procore-Company-Id": str(company_id),
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 401:
        data = data = refresh_and_store_tokens()
        headers["Authorization"] = f"Bearer {data['access_token']}"
        response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print("Failed to fetch segment items:", response.status_code, response.text)
        return None


    
    
    for item in response.json():
        if item.get("code") == 'S':
            return item.get("id")

    print(f"Segment item not found for S")
    return None




def createWbsCode(company_id, project_id, cost_code):

    #This will be used to create a Wbs code and then it will be used in the addLineItem function. 
    #How its gonna work:
    #1. Call the List Project Segment Items endpoint documentation found here: https://developers.procore.com/reference/rest/segment-items?version=latest
    #2. Loop thru each item until we find an item with a "code" value that matches the cost_code variable in this function
    #3. Use the project segment item id and the hardcoded id for the 

    data = load_tokens()

    cost_code_segment = getWbsSegment(company_id, project_id, "cost_code")
    cost_code_segment_item_id = getMatchingCostCodeSegmentItemId(company_id, project_id, cost_code_segment, cost_code)
    if cost_code_segment_item_id is None:
        return
    
    line_item_type_segment_id = getWbsSegment(company_id, project_id, "line_item_type")
    line_item_segment_id = getMatchingCommitmentSegmentItemId(company_id, project_id, line_item_type_segment_id)


    url = f"https://api.procore.com/rest/v1.0/projects/{project_id}/work_breakdown_structure/wbs_codes"

    headers = {
        "Authorization": f"Bearer {data['access_token']}",
        "Procore-Company-Id": str(company_id),
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    body = {
        "segment_items": [
            {
                "segment_id":cost_code_segment,
                "segment_item_id":cost_code_segment_item_id
            },
            {
                "segment_id":line_item_type_segment_id,
                "segment_item_id":line_item_segment_id
            },
            
        ]
    }

    response = requests.post(url, json=body, headers=headers)

    print(f"Status Code: {response.status_code}, {response.text}")
    if(response.status_code == 401):
        data = data = refresh_and_store_tokens()
        headers["Authorization"] = f"Bearer {data['access_token']}"
        response = requests.post(url, json=body, headers=headers)
        print("Status after refresh:", response.status_code, response.text)

    if response.status_code not in (200, 201): 
        return None
    
    rawData = response.json()
    return rawData
    
     
#print(createWbsCode("4264340", "116704", "16-100"))

def addLineItem(company_id, project_id, commitment_contract_id, cost_code, amount):
    data = load_tokens()

    url = f"https://api.procore.com/rest/v2.0/companies/{company_id}/projects/{project_id}/commitment_contracts/{commitment_contract_id}/line_items"

    

    headers = {
        "Authorization": f"Bearer {data['access_token']}",
        "Procore-Company-Id": str(company_id),
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    wbs_code_data = createWbsCode(company_id, project_id, cost_code)
    if wbs_code_data is None:
        return
    wbs_code = wbs_code_data.get("id")
    print(f"WBS CODE IS {wbs_code}")
    amt = str(float(amount))

    body = {
        "wbs_code_id": str(wbs_code), #<--- This one is for 01-740.S specifically, we will be creating a  createWbsCode function that will allow us to actually create the  wbs codes that we need
        "amount": amt,
    }

    response = requests.post(url, json=body, headers=headers)

    print(f"Status Code: {response.status_code}, {response.text}")
    if(response.status_code == 401):
        data = data = refresh_and_store_tokens()
        headers["Authorization"] = f"Bearer {data['access_token']}"
        response = requests.post(url, json=body, headers=headers)
        print("Status after refresh:", response.status_code, response.text)

    if response.status_code not in (200, 201): 
        return None
    
    rawData = response.json()
    return rawData

'''
try:
    ans = getSubNameMatch("4264340", "116704", "Dragorgul", load_tokens())

    print(ans)
    
    
except Exception as e:
    print(e)
'''

