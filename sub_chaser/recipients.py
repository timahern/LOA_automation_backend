#Resolving a subcontractor company to the people we email.
#
#Each invoice already carries its vendor (company) from the Procore API. This
#turns that company into contacts + email addresses:
#
#   1. PROJECT DIRECTORY (primary) — /rest/v1.0/projects/{id}/users. Every person
#      on the job, each linked to the company they work for. One call covers every
#      sub on the project, so we fetch it ONCE per analysis run and index it by
#      vendor id.
#   2. VENDOR RECORD (fallback) — the company's own email_address, used when the
#      directory has nobody for that company.
#
#v1 puts ALL of a company's contacts in the To line (per the build brief). The
#title filter for billing/AP-only contacts is structured below but deliberately
#NOT applied yet — flip APPLY_BILLING_FILTER when you want it.

import re

from sub_chaser.procore_fetch import procoreGet

#for a later "only email the billing people" mode. structured, not switched on.
BILLING_TITLE_PATTERN = re.compile(r"billing|account|office|insurance", re.I)
APPLY_BILLING_FILTER = False

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def getProjectDirectory(company_id, project_id, tokens=None):

    #everyone in the project directory. one call, then we index it in memory.

    response = procoreGet(
        f"https://api.procore.com/rest/v1.0/projects/{project_id}/users",
        company_id,
        params={"company_id": company_id, "per_page": 300},
        tokens=tokens,
    )

    if response.status_code != 200:
        print(f"[recipients] directory fetch failed ({response.status_code})")
        return []

    return response.json() or []


def _vendorIdOf(user):

    #which company a directory person belongs to. procore has used a few shapes
    #for this over the years, so check the known ones.

    for key in ("vendor", "company"):
        value = user.get(key)
        if isinstance(value, dict) and value.get("id"):
            return value.get("id")
    for key in ("vendor_id", "company_id"):
        if user.get(key):
            return user.get(key)
    return None


def _contactFrom(user):

    #one directory person -> a contact, or None if there's no usable email

    email = (user.get("email_address") or user.get("email") or "").strip()
    if not EMAIL_PATTERN.match(email):
        return None

    name = " ".join(part for part in [
        (user.get("first_name") or "").strip(),
        (user.get("last_name") or "").strip(),
    ] if part).strip()

    return {
        "name": name or email,
        "email": email,
        "title": (user.get("job_title") or "").strip(),
        "source": "directory",
    }


def buildDirectoryIndex(users):

    #vendor_id -> [contact, ...]. inactive people are skipped; a company can
    #legitimately have several contacts (we've seen up to ~9).

    index = {}
    for user in users or []:
        if user.get("is_active") is False:
            continue

        vendor_id = _vendorIdOf(user)
        if not vendor_id:
            continue

        contact = _contactFrom(user)
        if not contact:
            continue

        index.setdefault(str(vendor_id), []).append(contact)

    return index


def getVendorFallbackContact(company_id, project_id, vendor_id, tokens=None):

    #the company's own email on the vendor record — used when the project
    #directory has nobody for them

    if not vendor_id:
        return None

    response = procoreGet(
        f"https://api.procore.com/rest/v1.1/projects/{project_id}/vendors/{vendor_id}",
        company_id,
        tokens=tokens,
    )
    if response.status_code != 200:
        return None

    vendor = response.json() or {}
    email = (vendor.get("email_address") or vendor.get("email") or "").strip()
    if not EMAIL_PATTERN.match(email):
        return None

    return {
        "name": vendor.get("name") or "",
        "email": email,
        "title": "",
        "source": "vendor record",
    }


def resolveRecipients(directory_index, vendor_id, company_id=None, project_id=None, tokens=None):

    #every contact we'd email for this company. directory first; if that's empty
    #fall back to the vendor record's own address.

    contacts = list(directory_index.get(str(vendor_id)) or []) if vendor_id else []

    if APPLY_BILLING_FILTER:
        billing = [c for c in contacts if BILLING_TITLE_PATTERN.search(c.get("title") or "")]
        if billing:  #never filter down to nobody
            contacts = billing

    if not contacts and company_id and project_id:
        fallback = getVendorFallbackContact(company_id, project_id, vendor_id, tokens=tokens)
        if fallback:
            contacts = [fallback]

    #de-dupe on email, keep order
    seen = set()
    unique = []
    for contact in contacts:
        key = contact["email"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(contact)

    return unique
