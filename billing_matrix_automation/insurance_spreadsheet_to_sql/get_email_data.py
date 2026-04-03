from datetime import datetime
from dateutil.relativedelta import relativedelta
import sqlite3

#Point of this file is to gather the email data for each apm. There will be a function in this file that goes thru the sqlite db,
#    and compiles a map containing all the data needed for the emails. That map data will be in the following format
#
#email_map = {
#    <apm_id>: {
#          apm_id: <apm_id>,
#          apm_name: <apm_name>,
#          apm_email: <apm_email>,
#          projects_with_notifs: [
#               {
#                   proj_id: <proj_id>,
#                   proj_name: <proj_name>,
#                   proj_number: <proj_number>,
#                   expired_insurances: [
#                       {
#                           vendor_name: <vendor_name>,
#                           vendor_id: <vendor_id>,
#                           'umbrella_insurance': None, 'general_liability_insurance': None, 'automobile_insurance': None, 'workers_comp_insurance': None,
#                       }
#                   ]
#               }
#          ],
#                  
#    }   
# }




def name_to_email(procore_name: str) -> str | None:
    parts = procore_name.strip().split()
    if len(parts) >= 2:
        return f"{parts[1][0].lower()}{parts[0].lower()}@dgccapital.com"
    return None

def is_project_active(proj_end_date: str) -> bool:
    if not proj_end_date or not str(proj_end_date).strip():
        return True
    try:
        end_dt = datetime.strptime(str(proj_end_date).strip(), "%m/%d/%Y")
        cutoff = end_dt + relativedelta(months=3)
        return datetime.today() <= cutoff
    except ValueError:
        return True

REQUIRED_INSURANCE_COLS = [
    "umbrella_insurance",
    "general_liability_insurance",
    "automobile_insurance",
    "workers_comp_insurance",
]

def vendor_needs_notif(row) -> bool:
    """
    Returns True if the vendor should be included in expired_insurances:
    - At least one of ANY insurance type is expired (past today), OR
    - At least one of the 4 required types is missing (None)
    Pollution insurance is ignored entirely.
    """
    today = datetime.today()

    all_ins_cols = REQUIRED_INSURANCE_COLS + ["pollution_insurance"]

    for col in all_ins_cols:
        val = row[col]
        if not val:
            continue
        try:
            if datetime.strptime(str(val).strip(), "%m/%d/%Y") < today:
                return True  # at least one is expired
        except ValueError:
            continue

    for col in REQUIRED_INSURANCE_COLS:
        if not row[col]:
            return True  # missing a required type

    return False

def build_email_map_from_db(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM vendor_insurance").fetchall()
    conn.close()

    email_map = {}

    for row in rows:
        if not row["APM"] or not row["APM"].strip():
            continue

        if not is_project_active(row["proj_end_date"]):
            continue

        if not vendor_needs_notif(row):
            continue

        apm_names = [n.strip() for n in row["APM"].split(",")]

        for apm_name in apm_names:
            if apm_name not in email_map:
                email_map[apm_name] = {
                    "apm_name":             apm_name,
                    "apm_email":            name_to_email(apm_name),
                    "projects_with_notifs": {}
                }

            projs = email_map[apm_name]["projects_with_notifs"]
            proj_key = row["project_number"]

            if proj_key not in projs:
                projs[proj_key] = {
                    "proj_name":          row["project_name"],
                    "proj_number":        row["project_number"],
                    "proj_end_date":      row["proj_end_date"],
                    "expired_insurances": []
                }

            projs[proj_key]["expired_insurances"].append({
                "vendor_name":                 row["vendor_name"],
                "umbrella_insurance":          row["umbrella_insurance"],
                "general_liability_insurance": row["general_liability_insurance"],
                "automobile_insurance":        row["automobile_insurance"],
                "workers_comp_insurance":      row["workers_comp_insurance"],
                "pollution_insurance":         row["pollution_insurance"],
            })

    for apm_data in email_map.values():
        apm_data["projects_with_notifs"] = list(apm_data["projects_with_notifs"].values())

    return email_map

try:
    ans = build_email_map_from_db(r"C:\Users\tahern\Desktop\Procore Production Automation\insurance_spreadsheet_to_sql\vendor_insurance.db")
    print(ans)
except Exception as e:
    print(e)