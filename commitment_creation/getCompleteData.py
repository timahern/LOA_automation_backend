from commitment_creation.gpt_api_interaction.buyout_and_b1_analysis import extract_exhibit_b1_date, extract_buyout_data
from commitment_creation.gpt_api_interaction.exhibit_b_analysis import extract_exhibit_b_date

def subDataBuilder(subData, company_id, project_id, ex_b_date):
    buyoutInfo = extract_buyout_data(subData.get("buyout_page1")) if subData.get("buyout_page1") else {}
    exB1Date = extract_exhibit_b1_date(subData.get("exhibit_b1_page1")) if subData.get("exhibit_b1_page1") else None

    return {
        "vendor_selected": buyoutInfo.get("vendor_selected"),
        "trade": buyoutInfo.get("trade"),
        "cost_code": buyoutInfo.get("cost_code"),
        "subcontract_amount": buyoutInfo.get("subcontract_amount"),

        "exhibit_a_length": subData.get("exhibit_a_length"),
        "exhibit_b_length": subData.get("exhibit_b_length"),
        "exhibit_b_date": ex_b_date,
        "exhibit_b1_length": subData.get("exhibit_b1_length"),
        "exhibit_b1_date": exB1Date,
        "exhibit_c_length": subData.get("exhibit_c_length"),
        "exhibit_d_length": subData.get("exhibit_d_length"),
        "exhibit_h_length": subData.get("exhibit_h_length"),  
        "loa_id": subData.get("loa_id"),

        "company_id": company_id,
        "project_id": project_id,
    }

def getAnalyzedData(incompleteData, company_id, project_id):

    ex_b_pg1_b64 = incompleteData[0].get("exhibit_b_page1")
    if ex_b_pg1_b64:
        exhibit_b_date = extract_exhibit_b_date(ex_b_pg1_b64)

    ans = []
    for subData in incompleteData:
        completeSubData = subDataBuilder(subData, company_id, project_id, exhibit_b_date)
        ans.append(completeSubData)

    return ans

    
    