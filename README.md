# APM Toolbox – Backend

A Flask REST API powering the APM Toolbox — a suite of tools that automate subcontract document generation, Procore commitment creation, and billing matrix generation for construction project management workflows.

**Frontend repo:** [timahern/LOA_automation_frontend](https://github.com/timahern/LOA_automation_frontend)

---

## Features

### Subcontract Generator
Accepts uploaded exhibit PDFs (A, B, B.1, C, D, optional HASP), extracts subcontractor data using GPT-4o vision, and returns a ZIP of fully populated LOA documents ready for execution.

### B.1 Finder
Accepts one or more buyout PDFs, uses a trained Keras CNN model to identify and extract B.1 pages, and returns them as a ZIP.

### Procore Commitment Creation
Authenticates with Procore via OAuth 2.0, fuzzy-matches subcontractors to vendors in the selected project, and programmatically creates subcontract commitments with line items via the Procore API.

### Billing Matrix Generator
Pulls commitment and billing period data from Procore and generates a pre-filled Excel billing matrix spreadsheet for a selected project and billing period.

---

## Tech Stack

- **Python / Flask** — REST API
- **OpenAI GPT-4o** — Vision-based document parsing (buyout pages, exhibit dates)
- **Keras / TensorFlow** — Custom CNN model for B.1 page classification
- **Procore API** — OAuth 2.0, commitment creation, billing data retrieval
- **Flask-Session** — Server-side session management
- **Flask-Limiter** — Rate limiting
- **PyMuPDF / Pillow** — PDF rendering and image processing
- **SQLite** — Vendor insurance data storage
- **AWS EC2** — Deployment

---

## Architecture

```
server.py              — App setup, CORS, session config, blueprint registration
extensions.py          — Shared Flask-Limiter instance
routes/
  loa.py               — /generate-loas, /get-subs-metadata, /extract-b1s
  auth.py              — /auth/procore, /auth/callback, /auth/status
  procore.py           — /procore/companies-projects, /procore/analyze, /create-commitments
  billing.py           — /billing/periods, /billing/generate
auth/                  — Procore OAuth token exchange and session storage
commitment_creation/   — GPT-4o document analysis and Procore API interaction
billing_matrix_automation/ — Billing period retrieval and Excel generation
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- A Procore developer app (client ID + secret)
- An OpenAI API key

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure environment variables

Create a `.env` file in the project root:

```
FRONTEND_URL=your_frontend_url
API_KEY=your_internal_api_key
PROCORE_CLIENT_ID=your_procore_client_id
PROCORE_CLIENT_SECRET=your_procore_client_secret
OPENAI_API_KEY=your_openai_api_key
FLASK_SECRET_KEY=your_flask_secret_key
LOCAL_TOKEN_SAVER_CLIENT_ID=your_value
LOCAL_TOKEN_SAVER_CLIENT_SECRET=your_value
```

| Variable | Description |
|---|---|
| `FRONTEND_URL` | URL of the frontend app (used for CORS) |
| `API_KEY` | Internal key required on all non-auth requests via `x-api-key` header |
| `PROCORE_CLIENT_ID` | Procore OAuth app client ID |
| `PROCORE_CLIENT_SECRET` | Procore OAuth app client secret |
| `OPENAI_API_KEY` | OpenAI API key for GPT-4o document analysis |
| `FLASK_SECRET_KEY` | Secret key for Flask session signing |

### Run locally

```bash
python server.py
```

---

## How It Works

1. **Upload** — Frontend sends exhibit PDFs to `/generate-loas`
2. **Parse** — GPT-4o reads buyout page images to extract trade, vendor, cost code, and subcontract amount
3. **Generate** — LOA documents are populated and returned as a ZIP download
4. **Authenticate** — User connects to Procore via OAuth 2.0 (`/auth/procore`)
5. **Analyze** — `/procore/analyze` fuzzy-matches subcontractors to Procore vendors and cost codes
6. **Review** — Frontend presents each match for user confirmation
7. **Commit** — `/create-commitments` posts confirmed subcontracts to Procore as subcontract commitments with line items

---

## Author

[timahern](https://github.com/timahern)
