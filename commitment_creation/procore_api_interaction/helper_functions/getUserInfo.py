import requests
import re
import os 
import json
from auth.getTokens import refresh_and_store_tokens
from auth.tokenStore import load_tokens


def getProjects(company_id):
    data = load_tokens()
    if not data or "access_token" not in data:
        raise PermissionError("Not authenticated with Procore. Please log in again.")


    url = f"https://api.procore.com/rest/v1.0/companies/{company_id}/projects"


    response = requests.get(url,headers={
        'Authorization': f'Bearer {data["access_token"]}',
        'Accept': "application/json",
        'Procore-Company-Id': company_id,
    })

    print(f"Status Code: {response.status_code}")
    if(response.status_code == 401):
        print("have to refresh access tokens")
        data = refresh_and_store_tokens()

        response = requests.get(url,headers={
        'Authorization': f'Bearer {data["access_token"]}',
        'Accept': "application/json",
        'Procore-Company-Id': str(company_id), 
    })
        print(f"Status Code: {response.status_code}")
    if(response.status_code != 200):
        return None
    

    projects = response.json()
    proj_list = []
    for proj in projects:
        proj_list.append({'project_id':proj['id'], 'project_name': proj['name']})
    return proj_list


def getCompaniesAndProjects():
    data = load_tokens()
    if not data or "access_token" not in data:
        raise PermissionError("Not authenticated with Procore. Please log in again.")
    url = "https://api.procore.com/rest/v1.0/companies"

    response = requests.get(url,headers={
        'Authorization': f'Bearer {data["access_token"]}',
        'Accept': "application/json",
    })

    if(response.status_code == 401):
        data = refresh_and_store_tokens()

        response = requests.get(url,headers={
            'Authorization': f'Bearer {data["access_token"]}',
            'Accept': "application/json",
        })
    if(response.status_code != 200):
        return
    
    company_list = []
    companies = response.json()
    for company in companies:
        company_list.append({'id':company['id'], 'company_name': company['name'], 'projects': []})


    for company in company_list:
        company['projects'] = getProjects(str(company['id']))
    


    return company_list
    

    
    