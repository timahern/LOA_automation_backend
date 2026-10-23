# invoice_intake — Context

## What this is
`POST /api/invoice-intake` — called by a **Power Automate** flow that watches APM
inboxes. The flow's AI Builder prompt classifies an emailed AIA G702/G703 and/or
lien waiver, extracts fields, and posts them + the base64 file here. We find the
one matching invoice (requisition) in Procore and attach the file to it.
Original brief: `invoice-intake-prompt.md`.

**The one rule:** if any stage can't narrow to exactly one thing, FAIL (HTTP 422)
with an explanation. Never attach to a guessed invoice.

## Auth — SSM service tokens, NOT the user session
No browser → no Flask session. Uses the shared Procore tokens in AWS SSM
(`/procore/access_token`, `/procore/refresh_token`) via
`procore_copilot/procore_auth.py`, same as `insurance_to_db.py`. `SsmTokens` has
the same `access_token()/refresh()` shape as sub_chaser's `WorkerTokens`, so
sub_chaser's `procoreGet()` / `uploadAttachments()` are reused unchanged.
Refresh is serialized with a lock and re-reads SSM first (the refresh token
rotates; the insurance cron shares it).
Inbound auth is just the global `x-api-key` check in `server.py` — the flow sends
the same `API_KEY` header.

## Files
- `procore_service.py` — SsmTokens, `COMPANY_ID`, paginated fetchers
- `matching.py` — pure-Python stages (no API calls), `Trace`, `MatchFailure`
- `intake.py` — `run_intake(payload) -> (status, body)`: validate → match → attach
- `routes/invoice_intake.py` — the blueprint

## Matching (as built)
1. **Project** — normalized exact `project_number` (`25057G` == `25-057-G`). Number
   is deliberately NOT fuzzy: 1-digit-off numbers score ~89. Falls back to
   `project_name` fuzzy (token_sort ≥85); >1 hit = fail. Active projects only.
2. **Subcontract** — exact normalized SC number wins outright. Else fuzzy ≥85,
   BUT each fuzzy hit is rejected if `subcontractor_name` is given and the
   vendor scores <50 against it (`SC25057G 17` vs `18` scores 91 and is usually
   a different sub). Else vendor-name fuzzy ≥85. Name normalization drops
   punctuation + Inc/LLC/Corp/etc.
   **Shortened sub names:** if every word of the document's sub name is in
   the Procore vendor name and it's 2+ words, it counts as a full match
   ("Apex Electrical" -> APEX ELECTRICAL CONTRACTING, which only scores 71).
   One word ("Apex") never qualifies. Sub names only; project names stay strict.
   Two subs matching is fine: the invoice stage still has to find exactly one.
3/4. **Invoice** — one cascade over every matched subcontract's invoices:
   period → application_number → amount_due. Unique hit wins; several hits
   narrow the pool for the next field; zero hits skip that field (probably
   misread). Ends ambiguous or with nothing matched → fail. A non-amount match
   whose `current_payment_due` disagrees with `amount_due` → warning, proceeds.
   **Period** = the midpoint of the sub's period falls inside the invoice's
   billing period (subs rarely align to the day). Falls back to `billing_date`.
5. **Attach** — PATCH multipart via `uploadAttachments`. Skips (200,
   `already_attached`) if a same-named file is already on the invoice (flow
   retries). After upload, verifies every pre-existing attachment id survived.

## Verified API facts (Oct 23, 2026, Tatte 3254966, read-only)
- v2.0 `commitment_contracts` returns `vendor: {"id": "..."}` only — **no name**.
  We join one `GET /rest/v1.1/projects/{id}/vendors` call instead.
- v1.1 requisitions: `number` (app #), `period_id`, `billing_date`,
  `commitment_id` (int), `summary.current_payment_due`, `attachments[]`.
- `billing_periods` returned all 12 periods unpaginated.
- Real dry runs matched correctly by name+app#, by exact SC+period, by project
  name+amount.

## PROVEN live Oct 23, 2026 — uploads APPEND, and work on approved invoices
Uploaded `fake invoice.pdf` to req 16319486 (Tatte 3254966, NASCO INC., app #6,
status **approved**) with the SSM tokens. The existing attachment (id
6581727509) kept its id and stayed put; the new file was added beside it.
So: `attachments[]` on PATCH **appends**, and the SSM token's user CAN attach to
approved invoices (the non-admin restriction doesn't bite). The post-upload
"did anything go missing" check stays in as a tripwire.

## Response
200 `{success, attached, matched{project, invoice}, warnings, extracted, trace}`
· 400 validation · 422 match failure (`stage`, `error`, `details` with candidates)
· 502 Procore/upload rejected · 500 attachments went missing / unexpected.
`"dry_run": true` in the payload matches without uploading (file optional).

## Deploying
- `rapidfuzz` must be installed on EC2 (`pip install rapidfuzz`).
- Instance role needs SSM read+write on `/procore/*` (already true if the
  insurance cron runs there).
- Base64 PDFs are big: check nginx `client_max_body_size` (default 1MB!).
- New blueprint → restart the Flask service.
