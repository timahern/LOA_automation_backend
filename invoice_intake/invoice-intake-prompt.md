# Invoice Intake Endpoint for APMToolbox

## Context

I have an existing Flask app called APMToolbox running on an EC2 instance (apmtoolbox.com). It's a suite of internal tools for DGC Capital, a general contracting firm. The app already integrates heavily with the Procore API (construction project management platform). I need a new endpoint added.

## What This Endpoint Does

I have a Power Automate flow that monitors APM email inboxes for incoming invoices (AIA G702/G703 Application for Payment forms) and lien waivers from subcontractors. An AI Builder prompt in the flow classifies the document and extracts structured fields. When it detects a valid, notarized invoice or lien waiver with complete info, it needs to send that data (plus the file) to this new endpoint, which will find the correct invoice in Procore and attach the file to it.

## Endpoint Spec

**Route:** `POST /api/invoice-intake`

**Auth:** Use whatever auth pattern the rest of APMToolbox uses (likely an API key check or internal auth middleware). 

**Request payload (JSON with base64 file):**
```json
{
  "document_type": "aia_with_waiver" | "aia_only" | "lien_waiver_only",
  "notarized": true,
  "subcontractor_name": "string or null",
  "subcontract_number": "string or null",
  "project_number": "string or null",
  "project_name": "string or null",
  "period": "string or null (e.g. '10/01/2026-10/31/2026')",
  "application_number": "string or null",
  "amount_due": "string or null (e.g. '$45,230.00')",
  "waiver_type": "conditional | unconditional | null",
  "info_complete": true,
  "missing_fields": [],
  "notes": "string or null",
  "file_base64": "base64 encoded PDF/image",
  "file_name": "original_filename.pdf",
  "sender_email": "who sent the original email"
}
```

**Response:** JSON with success/failure, what it matched to, and why it failed if it failed.

## Matching Logic (this is the core of the endpoint)

The matching works in 4 stages. At every stage, if we can't confidently find a unique match, the run FAILS and returns an error explaining what went wrong. Never attach a file to the wrong invoice.

### Stage 1: Find the Project

1. If `project_number` is provided, use it to find the project in Procore (fetch the project ID). Project numbers in Procore should be an exact or near-exact match.
2. If project_number fails or is null but `project_name` is provided, try matching by project name. Use fuzzy matching (fuzzywuzzy or rapidfuzz) but require HIGH confidence (85%+). Be aware that generic names like "Sweetgreen" might match multiple projects, so if there are multiple high-confidence matches, FAIL.
3. If neither project_number nor project_name is provided, or nothing matches, FAIL with a clear error message.
4. If project_number doesn't work, fall back to project_name before failing.

### Stage 2: Find the Subcontract(s)

Once we have the project ID, fetch ALL subcontracts (commitments) for that project in ONE API call.

1. First try matching by `subcontract_number` using fuzzy match. The AI sometimes misreads these (e.g., returns "C20576 18" when the real number is "SC25057G 18"). Require high confidence (85%+).
2. If subcontract_number is null or no confident match, try matching by `subcontractor_name` using fuzzy match (85%+ confidence). Match against the vendor/subcontractor name on each subcontract.
3. If neither produces any matches, FAIL.
4. If exactly ONE subcontract matches, proceed to Stage 3.
5. If MULTIPLE subcontracts match (e.g., same sub has multiple contracts on the project), proceed to Stage 4.

### Stage 3: Find the Invoice (single subcontract)

Fetch all invoices/requisitions for the matched subcontract in ONE API call.

Try to find the unique invoice in this order:

1. **By period:** If `period` is provided, find invoices whose billing period matches. If exactly one matches, use it. As a soft confirmation, check if `amount_due` matches too (log a warning if it doesn't, but still proceed). If multiple invoices match the period, continue to next method.
2. **By application_number:** If `application_number` is provided, find invoices with a matching application number. If exactly one matches, use it (soft-check amount_due). If multiple match or none match, continue.
3. **By amount_due:** If `amount_due` is provided, find invoices with a matching payment amount. Parse the currency string to a number for comparison. If exactly ONE matches, use it. If multiple match, FAIL (ambiguous).
4. If none of the above produce a unique match, FAIL.

### Stage 4: Find the Invoice (multiple subcontracts)

Fetch all invoices for ALL matched subcontracts (batch the API calls).

Apply the same cascading logic as Stage 3, but across all subcontracts' invoices:

1. **By period:** Check all invoices across all matched subcontracts. If exactly one invoice (from any subcontract) matches the period, use it (soft-check amount_due). If multiple match, narrow with application_number, then amount_due.
2. **By application_number:** Same approach. If exactly one invoice across all subcontracts has the matching app number, use it. If multiple, try to narrow with amount_due.
3. **By amount_due:** Last resort. If exactly one invoice across all subcontracts matches the amount, use it. If multiple or zero match, FAIL.
4. The goal is always to narrow down to exactly ONE invoice. If we can't, FAIL.

### Stage 5: Attach the File

Once we've identified the unique invoice, upload the file (decoded from base64) as an attachment to that invoice in Procore.

## Important Technical Notes

- **Minimize API calls.** Fetch data in bulk upfront (all projects if needed, all subcontracts for a project, all invoices per subcontract) and do the matching logic in Python. Don't make API calls inside loops if you can avoid it.
- **Fuzzy matching:** Use `rapidfuzz` (faster than fuzzywuzzy). Use `fuzz.ratio` or `fuzz.token_sort_ratio` for comparing strings. Threshold of 85 for high confidence matches.
- **Amount parsing:** The `amount_due` field comes as a string like "$45,230.00". Strip the `$` and commas, convert to float for comparison. Use a small tolerance (within $0.01) for float comparison.
- **Period matching:** The period comes as "MM/DD/YYYY-MM/DD/YYYY". Procore invoices have billing date fields. Parse and compare the date ranges.
- **Logging:** Log every step of the matching process so we can debug when things fail. Include what was tried, what matched, confidence scores, etc.
- **Error responses:** Return detailed error messages explaining exactly where the matching failed and what data was available. This helps the APM manually find the right invoice.

## Procore API Reference

Look at the existing codebase for how Procore API calls are made and it should be similar to how the the billing_matrix_automation/insurance_spreadsheet_to_sql/insurance_to_db.py function works as this one makes calls to the aws ssm to get the api key and tokens needed to call the Procore api. (there should be helper functions, auth handling, company_id, etc.). The relevant Procore API endpoints are likely:

- List projects: `GET /rest/v1.0/projects` (filter by company)
- List commitments (subcontracts): `GET /rest/v1.0/projects/{project_id}/commitments`  
- List requisitions (invoices/payment apps): `GET /rest/v1.0/projects/{project_id}/commitments/{commitment_id}/requisitions`
- Upload attachment: Check the Procore API docs for the correct attachment upload endpoint for requisitions

Check my existing code for the exact API patterns, base URLs, auth headers, and company ID. The app already has Procore integration so follow those patterns.

## File Structure

Add this as a new route file/blueprint if that's how the app is structured, or add it to whatever file handles API routes. Follow the existing codebase's patterns for:
- Route registration
- Error handling
- Auth middleware
- Procore API calls
- Response format

## What I Want

A working MVP endpoint that:
1. Accepts the POST payload described above
2. Runs through the matching logic to find the right Procore invoice
3. Attaches the file to that invoice
4. Returns a clear JSON response with what happened

Don't over-engineer it. This is an MVP. But make the matching logic robust and well-logged because that's where things will break in production.
