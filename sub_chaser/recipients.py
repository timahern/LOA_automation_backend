#Resolving a subcontractor to the people we email.
#
#In order of preference:
#
#   1. INVOICE CONTACTS (primary) — the "Invoice Contacts" field the APM fills in
#      on the subcontract in Procore. These are exactly the people the APM chose
#      to handle invoicing, so they're the right To line. Only the single-
#      contract endpoint returns them (GET /rest/v1.0/work_order_contracts/{id},
#      the list endpoint doesn't), so it's one call per subcontract that billed
#      this period — jobs.py fetches each once, not once per invoice.
#      Verified Oct 27, 2026: 23 of 23 readable subcontracts on Tatte 3254966 had it filled in.
#   2. PROJECT DIRECTORY (fallback) — everyone at that company in
#      /rest/v1.0/projects/{id}/users. Fetched ONCE per run, indexed by vendor id.
#   3. VENDOR RECORD (last resort) — the company's own email_address.

import re

from sub_chaser.procore_fetch import procoreGet

EMAIL_PATTERN = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")

#APMs mark retired contacts by renaming them, e.g. "Milly Santiago - DO NOT USE"
DO_NOT_USE_PATTERN = re.compile(r"do\s*not\s*use", re.I)


def _cleanEmail(value):
    #"<joshk@kazimarindustrial.com" (seen in real data) -> "joshk@kazimarindustrial.com"
    return (value or "").strip().strip("<>").strip()


def _contact(name, email, title, source):

    #-> a contact dict, or None if it's unusable (bad email / marked DO NOT USE)

    email = _cleanEmail(email)
    if not EMAIL_PATTERN.match(email):
        return None
    if DO_NOT_USE_PATTERN.search(name or ""):
        return None
    return {
        "name": (name or "").strip() or email,
        "email": email,
        "title": (title or "").strip(),
        "source": source,
    }


#── 1. invoice contacts on the subcontract ─────────────────────────────────────

def getInvoiceContacts(company_id, project_id, commitment_id, tokens=None):

    #the subcontract's Invoice Contacts. returns [] when the field is empty and
    #None when the fetch itself failed — the caller falls back either way, but
    #the log says which.

    if not commitment_id:
        return None

    response = procoreGet(
        f"https://api.procore.com/rest/v1.0/work_order_contracts/{commitment_id}",
        company_id,
        params={"project_id": project_id},
        tokens=tokens,
    )
    if response.status_code != 200:
        print(f"[recipients] invoice contacts fetch failed for commitment {commitment_id} "
              f"({response.status_code})")
        return None

    contacts = []
    for person in (response.json() or {}).get("invoice_contacts") or []:
        contact = _contact(
            person.get("name"),
            person.get("email_address") or person.get("email"),
            person.get("job_title"),
            "invoice contacts",
        )
        if contact:
            contacts.append(contact)
    return contacts


#── 2. project directory ───────────────────────────────────────────────────────

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

        name = " ".join(part for part in [
            (user.get("first_name") or "").strip(),
            (user.get("last_name") or "").strip(),
        ] if part)
        contact = _contact(name, user.get("email_address") or user.get("email"),
                           user.get("job_title"), "directory")
        if not contact:
            continue

        index.setdefault(str(vendor_id), []).append(contact)

    return index


#── 3. vendor record ───────────────────────────────────────────────────────────

def getVendorFallbackContact(company_id, project_id, vendor_id, tokens=None):

    #the company's own email on the vendor record — used when nothing else
    #turned anyone up

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
    return _contact(vendor.get("name"), vendor.get("email_address") or vendor.get("email"),
                    "", "vendor record")


#── putting it together ────────────────────────────────────────────────────────

def resolveRecipients(directory_index, vendor_id, invoice_contacts=None,
                      company_id=None, project_id=None, tokens=None):

    #every contact we'd email for this invoice: the subcontract's invoice
    #contacts if it has any, else that company's people in the project
    #directory, else the vendor record's own address.

    contacts = list(invoice_contacts or [])

    if not contacts and vendor_id:
        contacts = list(directory_index.get(str(vendor_id)) or [])

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
