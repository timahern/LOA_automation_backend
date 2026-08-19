# sub_chaser — Context

## What this is
**Sub-Chaser 2000** — a new APMToolbox tool that, for a chosen project + billing
period, pulls that period's subcontractor invoices from Procore, runs AI vision
on each merged invoice PDF to check for a completed/signed/notarized AIA and
Waiver & Release of Lien, resolves non-compliant subs to contacts via the
Project Directory, and gives the user a prefilled "draft email" link per sub.
Prototype: session-held results, no DB, no PDF storage, no auto-send.

## Auth — the house pattern, nothing new
User signs in via Procore OAuth (`routes/auth.py`); tokens live in the Flask
session (`auth/tokenStore.py`). Feature code calls `load_tokens()` and, on 401,
`refresh_and_store_tokens()` — same as `subcontractor_info_retrieval.py`.
NEVER use the SSM/service tokens here — always the signed-in user's tokens.

## Reused from elsewhere (do not re-implement)
- `getBillingPeriods()`, `getWorkOrderContracts()` — `billing_matrix_automation/subcontractor_info_retrieval.py`
  (the commitment `number` from work order contracts IS the SC number)
- Merged-PDF compile pattern — adapted from `Procore_Sharepoint_Sync/app/tools/invoices.py`
- Frontend conventions — `LOA_Automation_FrontEnd/loa-automation-frontend` (Vite,
  react-router page + navbar link, `VITE_API_BASE_URL` + `x-api-key` header,
  `credentials: "include"`)

## Files
- `procore_fetch.py` — `getRequisitionsForPeriod()` (server-side `filters[period_id]`),
  `getWorkOrderContractsLite()` (thread-safe SC-number lookup), `cleanRequisition()`
- `pdf_compile.py` — `compileInvoicePdf()`: POST `single_pdf_compilers` → poll
  `job_url` → download signed result URL → `compressPdf()` (fail-safe JPEG downsample)
- `vision.py` — `analyze(pdf_bytes) -> {aia, lien, confidence, notes}`; the exact
  proven prompt; Claude takes PDFs natively so there's no page→image step; never
  raises (failures come back as `uncertain`)
- `worker_tokens.py` — carries the signed-in user's OAuth tokens into worker
  threads (flask `session` is request-scoped and unreachable from a thread)
- `jobs.py` — `start_real_job()` / `start_mock_job()` / `get_job_status()`;
  `ThreadPoolExecutor(6)`, in-memory job dict, rows stream queued→reading→done
- `test_vision.py` — desk CLI: run `analyze()` on a local PDF, no Flask needed
- `routes/sub_chaser.py` — `POST /sub-chaser/analyze` (`{"mock": true}` for canned
  data), `GET /sub-chaser/jobs/<id>`, plus the Phase 0 de-risk endpoints

## Auth inside background threads (important)
Worker threads CANNOT call `load_tokens()` — no request context. The route calls
`worker_tokens.fromSession()` while still in the request and hands the resulting
`WorkerTokens` to the job; every fetch function takes an optional `tokens=` for
this. Refresh uses `auth.getTokens.refresh_access_token()` (plain HTTP, no
session); the poll route writes rotated tokens back into the session.

## Verified API facts
- `GET /rest/v1.1/requisitions?project_id=&filters[period_id]=` — server-side
  period filter (also supports `filters[commitment_id]`)
- Requisition rows carry `vendor_name`, `billing_date`, `status`, `attachments[]`,
  `total_claimed_amount` and a commitment linkage (`commitment_id`/`contract`)
- PDF compile: `POST /rest/v1.0/requisitions/{id}/single_pdf_compilers?polling=true&project_id=`
  body `{"files":[{"type":"cover_sheet","id":""},{"type":"prostore_file","id":...,"url":...}]}`
  → poll `job_url` → `result.url` is a signed storage URL (no auth header)

## Build phases
- **Phase 0 (DONE — verified Aug 7, 2026 on Tatte 3254966):** linkage + PDF
  de-risk endpoints. Confirmed: `commitment_id` is a direct requisition field;
  v2.0 commitment ids are strings vs requisition ints (join must str() both);
  `summary.current_payment_due` is the real "due this period" number
  (`total_claimed_amount` is 0.00 on retainage-release/FINAL reqs);
  `attachment_count == 0` reqs can skip vision → auto non-compliant.
  Local frontend testing needs `.env.development` (VITE_API_BASE_URL=localhost:5000).
- **Phase 1 (DONE):** `SubChaserPage.jsx` + `.css`, nav entry `/sub-chaser`.
  Real project/period pickers (reuses `ProjectModalForMatrix` + `BillingPeriodModal`,
  which gained a `confirmLabel` prop). Streaming table with invoice status +
  amount, blank-form chips (drag-to-attach via the `DownloadURL` DataTransfer —
  Chromium only, and only into DESKTOP Outlook), owner's exact email copy in three
  variants, and an unresolvable `ATTACH-AIA…` pseudo-recipient that blocks Send
  until the user deals with it. Compose link is `mailto:` **on purpose** — it opens
  desktop Outlook, the only target drag-to-attach can reach.
  Blank PDFs go in `public/forms/` (see the README there).
- **Phase 2 (BUILT, needs desk verification):** real fetch + concurrent vision.
  Needs `ANTHROPIC_API_KEY` in `.env`. Optional overrides:
  `SUB_CHASER_VISION_MODEL` (default `claude-opus-5`),
  `SUB_CHASER_VISION_EFFORT` (default `medium`).
  Invoices with zero attachments skip the compile and the AI call entirely —
  nothing submitted is a verdict on its own.
- **Phase 3 (BUILT, needs desk verification):** contact resolution in
  `recipients.py`. Project directory (`GET /rest/v1.0/projects/{id}/users`) is
  fetched ONCE per run and indexed by company id, so per-invoice lookup is a dict
  hit, not a request. Falls back to the vendor record's own `email_address` only
  for companies the directory missed. Contacts are
  `{name, email, title, source}`; inactive users, missing/invalid emails, and
  duplicate addresses (case-insensitive) are dropped. Company linkage is read
  from `vendor{}` / `company{}` / `vendor_id` / `company_id` — shape varies.
  All contacts go in `To` (v1 per brief); the billing/AP title filter exists as
  `BILLING_TITLE_PATTERN` + `APPLY_BILLING_FILTER = False` — flip to enable.
  Verify with `POST /sub-chaser/recipients/diagnostics`.

## Attachments — the sub's own paperwork, not blank forms
The chase email attaches the invoice package the sub ACTUALLY submitted (pulled
from Procore), not a blank template: someone missing a notarized AIA needs their
own AIA to sign, and someone missing only a waiver needs their signed AIA for
reference. The analysis pass caches each merged PDF in memory (`_JOB_PDFS`) for
the life of the job; `GET /sub-chaser/jobs/<job_id>/invoice/<req_id>/pdf` serves
it (recompiling from Procore if absent). Still never written to disk.

That route is **exempt from the x-api-key middleware** (`server.py`) because the
browser fetches it directly for drag-to-attach and downloads, where no custom
header can be set — the random 128-bit `job_id` in the path is the capability.

### Drag-to-attach (the real fix)
`DownloadURL` (`"<mime>:<filename>:<absolute-url>"`) is the only web mechanism
that drops a real file into Outlook. Chromium fetches that URL **during the
drag** — so it must be absolute AND **same-origin**, or the fetch fails
*silently* and Outlook pastes a hyperlink instead.

**That cross-origin fetch was the bug.** The page runs on `:5173`, the API on
`:5000`, and the PDF URL was built from `VITE_API_BASE_URL` — cross-origin, so
the drag could never work. (The earlier "it just inserts a link" symptom was this
failing and degrading to the URI fallback, not `text/uri-list` being set.)

Fixed by: a dev-server proxy (`vite.config.js` proxies **`/sub-chaser/jobs`** →
Flask — deliberately deeper than the `/sub-chaser` page route, because proxying
that prefix sends the OAuth callback `/sub-chaser?procore=authed` to Flask and
breaks sign-in with `{"error":"Unauthorized"}`) so
the app can hand out a **relative** path; `VITE_SAME_ORIGIN_API=false` opts back
out to absolute URLs where no proxy exists (open/download still work, drag
won't). **Production needs the same path prefix routed to the backend by the
CDN/reverse proxy**, otherwise set that flag.

Backend serves it as `Content-Disposition: attachment` with an explicit filename,
`Content-Length`, and `Cache-Control: no-store`. Reusable component:
`src/components/DraggablePdf.jsx`. Chrome/Edge + desktop Outlook only — Firefox
and Safari don't implement DownloadURL, and Outlook Web is inconsistent.

**Known gap:** a sub with zero attachments has no submitted paperwork to send
back — the endpoint returns just Procore's cover sheet. Those are exactly the
subs who need a BLANK form instead. `public/forms/` + its README are still in
the frontend for that case, unused for now.

## FINAL vs PARTIAL invoice → which lien waiver the sub owes
Line 9 of the AIA ("Balance to finish, including retainage (Line 3 less Line 6)")
is `summary.balance_to_finish_including_retainage` on the requisition. `$0.00`
means FINAL, anything else PARTIAL — `isFinalInvoice()` in `procore_fetch.py`
(parsed numerically, tolerant of `"0"`, `"$0.00"`, `"1,234.50"`). Returns **None**
when the summary is missing, and the UI then shows no waiver chip and says it
couldn't tell, rather than naming the wrong form.

Drives three things: the `FINAL` chip in the table, which template chip appears
on the draft card, and the unresolvable To-line token —
`ATTACH-AIA-AND-FINAL-WAIVER` / `ATTACH-FINAL-WAIVER` /
`ATTACH-AIA-AND-PARTIAL-WAIVER` / `ATTACH-PARTIAL-WAIVER` (drops the FINAL/PARTIAL
word when unknown). Note the requisition also carries a raw `final` boolean —
unused; the owner specified the Line 9 rule, and it's the authoritative one.

Waiver templates live in the frontend at `public/forms/final-lien-waiver.docx`
and `partial-lien-waiver.docx` (see that folder's README; overridable with
`VITE_FINAL_WAIVER_URL` / `VITE_PARTIAL_WAIVER_URL`).

## Deploying to EC2 — what this tool needs that the rest of the app doesn't

**New pip packages** (nothing else in the backend imports these):
```
pip install anthropic pillow
```
- `anthropic` — **required**. The vision analysis won't run without it.
  Needs a version supporting `output_config` (0.104+ verified).
- `pillow` — *optional but recommended*. Only used to shrink scanned invoice
  PDFs before they go to the model; `compressPdf()` catches ImportError and
  returns the original bytes, so it degrades safely. Without it, big scans stay
  big and can hit the 22MB analysis ceiling (those come back "uncertain").

Already present, no action: `requests`, `flask`, `flask_cors`, `dotenv`, `fitz`
(PyMuPDF — `pdfHelpers.py` already uses it).

**New .env entries:**
```
ANTHROPIC_API_KEY=sk-ant-...        # REQUIRED
SUB_CHASER_VISION_MODEL=claude-opus-5    # optional, this is the default
SUB_CHASER_VISION_EFFORT=medium          # optional, this is the default
```
⚠️ `ANTHROPIC_API_KEY` is read implicitly by the Anthropic SDK — it never appears
as `os.getenv(...)` in this code, so a grep for env vars WILL MISS IT.

Already in the EC2 .env, no action: `PROCORE_CLIENT_ID`, `PROCORE_CLIENT_SECRET`,
`FRONTEND_URL`, `API_KEY`.

**Frontend note:** the prod build sets `VITE_SAME_ORIGIN_API=false`, so invoice-PDF
URLs point straight at `apmtoolsbackend.art`. No CloudFront behavior needed. (The
alternative — a `/sub-chaser/jobs/*` behavior on distribution `EFM82AQNI5RIQ` →
`FlaskAPIOrigin` — would make them same-origin; the pattern must be
`/sub-chaser/jobs/*`, never `/sub-chaser/*`, which would break the OAuth callback.)

**Restart the Flask service after pulling** — new blueprint, so a reload is required.

## Out of scope (per brief)
SharePoint sync, storing PDFs, persistent verdict DB, automated sending,
billing/AP-contact title filter (structure only), production hardening.
