# Developer Guide — AI-Assisted Peer Review System

**Audience:** Developers, DevOps engineers, system administrators, SME guideline editors

---

## Architecture Overview

```
Browser
  │
  │  HTTP (multipart upload / JSON / SSE / file download)
  ▼
app.py  (Flask)
  │
  ├── UI pages
  │     GET  /                        → index.html (review submission)
  │     GET  /guidelines-page         → guidelines.html (formatted viewer)
  │     GET  /admin                   → admin.html (guidelines editor)
  │
  ├── Core review
  │     POST /review                  → run_review() → SSE stream
  │     GET  /download/<id>           → generate_report() → PDF bytes
  │     GET  /review/<id>/poll        → poll review status (reconnect)
  │     GET  /health                  → {"status": "ok"}
  │
  ├── Guidelines (public, read-only)
  │     GET  /guidelines/full         → complete structured JSON for UI
  │     GET  /guidelines/metadata     → version + metadata
  │     GET  /guidelines/journals     → configured journal list
  │     GET  /guidelines/changelog    → YAML changelog
  │     POST /guidelines/validate     → validate YAML structure
  │
  ├── Payment (Razorpay, optional)
  │     GET  /payment/config          → enabled flag + public key
  │     POST /payment/create-order    → create Razorpay order
  │     POST /payment/verify          → HMAC-SHA256 signature check
  │     GET  /payment/test            → sandbox test page (dev only)
  │
  └── Admin (session-authenticated)
        POST /admin/login              → authenticate
        POST /admin/logout             → end session
        GET  /admin/check-auth         → check session
        POST /admin/credentials        → change username/password
        POST /admin/reload-guidelines  → hot-reload YAML (public)
        GET  /admin/guidelines/raw     → raw YAML text ★ auth required
        POST /admin/guidelines/save    → validate + save YAML ★ auth required
        POST /admin/guidelines/nlp-update → AI-assisted YAML update ★ auth required
        GET  /admin/guidelines/versions   → GCS version history ★ auth required
        POST /admin/guidelines/push-to-gcs → push to Cloud Storage ★ auth required
        POST /admin/guidelines/revert     → revert to previous version ★ auth required
        GET  /admin/guidelines/version/<f> → download specific version ★ auth required
       │
       ├── review_agent.py
       │     extract_text()        ← python-docx / pdfplumber / UTF-8
       │     run_review()          ← Claude Sonnet 4.6 API (streaming)
       │
       ├── report_generator.py
       │     generate_report()     ← reportlab PDF builder
       │
       └── guidelines/
             guidelines_loader.py  ← YAML → system prompt + structured JSON
             review_guidelines.yaml ← SME-editable review framework
```

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Runtime |
| pip | Any | Package management |
| Docker | 20+ | Container build |
| gcloud CLI | Latest | GCP deployment |
| Anthropic API key | — | Claude AI access |

---

## Local Development Setup

```bash
# Clone
git clone https://github.com/findbhavin/healthcarethesisreview
cd healthcarethesisreview

# Install dependencies
pip install -r requirements.txt
pip install pytest   # for running tests

# Set environment variables
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
export FLASK_DEBUG=true   # optional: enables auto-reload

# Start the development server
python app.py
# → http://localhost:8080
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude AI reviews |
| `RAZORPAY_KEY_ID` | No | — | Razorpay public key — enables payment modal |
| `RAZORPAY_KEY_SECRET` | No | — | Razorpay secret key — used for HMAC verification (server-side only) |
| `GCS_BUCKET` | No | — | Google Cloud Storage bucket name — enables guideline version history |
| `FLASK_SECRET_KEY` | No | random | Flask session secret — set a fixed value in production so admin sessions survive restarts |
| `PORT` | No | `8080` | Port for Flask/gunicorn |
| `FLASK_DEBUG` | No | `false` | Enable Flask debug mode (dev only) |

If `RAZORPAY_KEY_ID` or `RAZORPAY_KEY_SECRET` is absent, payment is silently disabled — users see a free download button.

**Never commit API keys to Git.** Use Cloud Run secrets or a `.env` file (excluded by `.gitignore`).

### Quick-start `.env` (local development)

```bash
ANTHROPIC_API_KEY=sk-ant-your-key-here
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx      # optional — get from Razorpay Dashboard
RAZORPAY_KEY_SECRET=your_secret_here        # optional
FLASK_SECRET_KEY=dev-secret-change-in-prod
FLASK_DEBUG=true
```

---

## Running Tests

```bash
python -m pytest tests/ -v
```

All tests pass with no API key required — the Claude API is mocked in tests. Razorpay is also mocked; no sandbox account is needed.

### Test Modules

| File | What It Covers |
|------|---------------|
| `test_guidelines_loader.py` | YAML loading, validation, prompt building, `get_full_guidelines()`, journal overrides, weights, rubrics |
| `test_text_extraction.py` | DOCX/PDF/TXT extraction using real sample files |
| `test_report_generator.py` | PDF output validity, content, decision colours |
| `test_app_routes.py` | Core Flask routes, `/guidelines/full`, `/guidelines-page`, `/review/<id>/poll`, error handlers, SSE stream |
| `test_payment.py` | `/payment/config`, `/payment/create-order`, `/payment/verify` (HMAC logic), `/payment/test` sandbox page |
| `test_admin.py` | Admin login/logout, session auth, `/admin/guidelines/raw`, save, credential validation |

### Running a single test file

```bash
python -m pytest tests/test_app_routes.py -v
```

### Running a single test

```bash
python -m pytest tests/test_app_routes.py::TestReviewRoute::test_review_docx_returns_result -v
```

---

## Updating Review Guidelines

### How It Works

The review framework is stored in `guidelines/review_guidelines.yaml`. When a review is triggered, `guidelines_loader.py` reads this file and dynamically builds the Claude system prompt. **No code changes are needed to update the review logic.**

### Who Can Edit It

Any SME, editor, or administrator with access to the Git repository can edit the YAML file using a text editor (VS Code, Notepad++, etc.).

### Step-by-Step: Adding a New Check to a Stage

1. Open `guidelines/review_guidelines.yaml`
2. Find the relevant stage (e.g., `stage_3`)
3. Add a new bullet to the `checks` list:

```yaml
stage_3:
  checks:
    - "Existing check..."
    - "New check: Confirm that confounding variables are accounted for"   # ← add here
```

4. Update the `version` and `last_updated` fields in `metadata:`
5. Add an entry to the `changelog:` section at the bottom:

```yaml
changelog:
  - version: "1.4"
    date: "2026-04-01"
    author: "Dr. Smith"
    changes: "Added confounding variable check to Stage 3"
```

6. Commit and push the change:

```bash
git add guidelines/review_guidelines.yaml
git commit -m "guidelines: add confounding variable check to Stage 3 (v1.4)"
git push
```

7. **Restart the application** (or call `POST /admin/reload-guidelines`) to apply the change.

---

### Step-by-Step: Adding a New Journal

Add an entry under `journal_overrides:` in the YAML:

```yaml
journal_overrides:
  IJPH:
    full_name: "Indian Journal of Public Health"
    scope: "Public health, epidemiology, health policy in India"
    word_limits:
      original_research: 2500
      review_article: 4000
    reference_style: "Vancouver"
    required_sections:
      - "Abstract (structured)"
      - "Keywords"
      - "Introduction"
      - "Methods"
      - "Results"
      - "Discussion"
      - "References"
```

The journal key (`IJPH`) is what users type in the **Target Journal** field on the web interface.

---

### Validating the YAML After Edits

Before committing, validate the YAML:

```bash
# Option 1: Python script
python3 -c "
from guidelines.guidelines_loader import validate_guidelines
result = validate_guidelines()
print(result)
"

# Option 2: API endpoint (if app is running)
curl -X POST http://localhost:8080/guidelines/validate
```

If the validation returns `{"valid": true}`, the guidelines are well-formed.

---

## API Reference

### `POST /review`

Accepts a manuscript file and returns the AI review.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File | Yes | Manuscript file (PDF, DOCX, TXT; max 50 MB) |
| `journal_name` | String | No | Target journal short name (e.g., `NJCM`) |

**Response 200 OK:**
```json
{
  "review_id": "550e8400-e29b-41d4-a716-446655440000",
  "manuscript_title": "Prevalence of Osteoporosis...",
  "decision": "Major revision",
  "word_count": 3412,
  "review_text": "---\nPEER REVIEW REPORT\n..."
}
```

**Error responses:**

| Code | Cause |
|------|-------|
| 400 | No file uploaded or unsupported format |
| 422 | Cannot extract text from file |
| 500 | API key missing or Claude API error |

---

### `GET /guidelines/full`

Returns the complete structured guidelines payload consumed by the public guidelines viewer (`/guidelines-page`) and the admin UI.

```json
{
  "metadata": { "version": "2.0", "last_updated": "2026-03-05", ... },
  "role": "You are a senior medical journal editor...",
  "stages": [
    {
      "key": "stage_1", "number": "1", "name": "Initial Editorial Screening",
      "weight": 8, "max_score": 10,
      "score_rubric": { "9-10": "All items present", ... },
      "checks": [...], "decision_options": [...], "instruction": "..."
    },
    ...
  ],
  "journals": [ { "key": "NJCM", "full_name": "...", "scope": "...", ... } ],
  "changelog": [ { "version": "2.0", "date": "2026-03-05", "changes": "..." } ]
}
```

The `weight` and `score_rubric` fields power the WRS formula card and collapsible rubric tables on the guidelines viewer page.

---

### `GET /download/<review_id>`

Downloads the PDF report for a completed review.

**Response 200:** PDF file (application/pdf)
**Response 404:** Review ID not found (session expired or invalid)

---

### `GET /guidelines/metadata`

Returns version and metadata of the current guidelines.

```json
{
  "version": "1.3",
  "last_updated": "2026-03-05",
  "maintained_by": "Editorial Board",
  "journal_name": "NJCM and affiliated journals"
}
```

---

### `GET /guidelines/journals`

Returns the list of journals with configured overrides.

```json
{"journals": ["NJCM", "BMJ", "PLOS_ONE"]}
```

---

### `GET /guidelines/changelog`

Returns the full changelog from the YAML.

---

### `POST /guidelines/validate`

Validates the current `review_guidelines.yaml` structure.

**Response 200:** `{"valid": true, "version": "1.3"}`
**Response 422:** `{"valid": false, "errors": ["Stage 'stage_3' is missing 'checks'"]}`

---

### `POST /admin/reload-guidelines`

Hot-reloads the guidelines YAML without restarting the application. Validates first. This endpoint is public (no session required) to support CI/CD pipelines.

---

### Payment endpoints

See **PAYMENT_GATEWAY.md** for the full payment API reference.

**Sandbox test page:** `GET /payment/test` — renders an HTML page with Razorpay test card numbers and a live payment flow test. Only useful during development with test keys. Do not link to this page from the production UI.

---

## GCP Cloud Run Deployment

### 1. Enable Required APIs

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com
```

### 2. Store the API Key as a Secret

```bash
echo -n "sk-ant-your-key-here" | \
  gcloud secrets create ANTHROPIC_API_KEY --data-file=-
```

### 3. Build and Push the Container

```bash
PROJECT_ID=$(gcloud config get-value project)

gcloud builds submit \
  --tag gcr.io/$PROJECT_ID/healthcarethesisreview \
  .
```

### 4. Deploy to Cloud Run

```bash
gcloud run deploy healthcarethesisreview \
  --image gcr.io/$PROJECT_ID/healthcarethesisreview \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --min-instances 0 \
  --max-instances 3 \
  --port 8080
```

**Key flags:**

| Flag | Value | Reason |
|------|-------|--------|
| `--timeout 300` | 5 minutes | Claude can take 60–90 s on long manuscripts |
| `--memory 1Gi` | 1 GB | pdfplumber and python-docx need RAM |
| `--allow-unauthenticated` | — | Public web UI; remove for internal use |
| `--min-instances 0` | 0 | Scale to zero when idle (cost saving) |

### 5. Verify Deployment

```bash
SERVICE_URL=$(gcloud run services describe healthcarethesisreview \
  --region us-central1 --format 'value(status.url)')

curl "$SERVICE_URL/health"
# {"status": "ok"}
```

### 6. Updating the Application

```bash
# After code changes:
gcloud builds submit --tag gcr.io/$PROJECT_ID/healthcarethesisreview .
gcloud run deploy healthcarethesisreview \
  --image gcr.io/$PROJECT_ID/healthcarethesisreview \
  --region us-central1
```

### 7. Updating Guidelines Only (No Code Change)

```bash
# Edit the YAML locally, then:
git add guidelines/review_guidelines.yaml
git commit -m "guidelines: v1.4 — add new Stage 3 check"
git push

# Rebuild and redeploy (YAML is baked into the image)
gcloud builds submit --tag gcr.io/$PROJECT_ID/healthcarethesisreview .
gcloud run deploy healthcarethesisreview --image gcr.io/$PROJECT_ID/healthcarethesisreview --region us-central1
```

> **Alternative:** Mount the YAML from Cloud Storage so updates take effect without rebuilding the image. Contact the development team to set this up.

---

## Production Considerations

### In-Memory Review Store

`app.py` stores completed reviews in a Python dict (`_review_store`). This means:
- Reviews are **lost on restart** or if Cloud Run scales to a new instance
- On multi-instance deployments, a review run on instance A cannot be downloaded from instance B

**For production with multiple users:** Replace `_review_store` with Cloud Firestore or Cloud Storage. Contact the development team to implement this.

### Authentication

Admin credential-sensitive routes (`/admin/guidelines/raw`, `/admin/guidelines/save`, etc.) require an active admin session (POST `/admin/login` with `admin_config.json` credentials).

`/admin/reload-guidelines` is intentionally **public** to support CI/CD hot-reload without storing admin credentials in deployment scripts. If you need to restrict it, add an `X-Admin-Token` header check:

```python
@app.route("/admin/reload-guidelines", methods=["POST"])
def reload_guidelines():
    token = request.headers.get("X-Admin-Token")
    if token != os.environ.get("ADMIN_TOKEN"):
        return jsonify({"error": "Unauthorized"}), 401
    ...
```

**Admin credentials in Docker:** `admin_config.json` is baked into the container image at build time. Password changes made via the admin UI are written to the file inside the running container but are **lost on the next deployment**. For persistent credentials, mount the file from Cloud Storage or store the credentials in Secret Manager.

### Logging

Logs are written to stdout/stderr and are automatically captured by Google Cloud Logging when deployed on Cloud Run. View logs:

```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=healthcarethesisreview" \
  --limit 50 --format "table(timestamp, jsonPayload.message)"
```

---

## File Format Support

| Format | Library | Notes |
|--------|---------|-------|
| `.docx` | python-docx | Paragraphs + tables extracted |
| `.pdf` | pdfplumber | Text-layer PDFs only; scanned/image PDFs will return empty text |
| `.txt` | built-in | UTF-8 decoded |
| Others | — | Returns HTTP 400 |

**Scanned PDFs:** The application does not include OCR. Advise authors to submit DOCX or a PDF generated from Word/LaTeX.

---

## Adding a New File Format

1. Add the extension to `ALLOWED_EXTENSIONS` in `app.py`
2. Add an extraction function to `review_agent.py`:

```python
def extract_text_from_rtf(file_bytes: bytes) -> str:
    # your extraction logic
    ...
```

3. Add a branch in `extract_text()`:

```python
elif lower.endswith(".rtf"):
    return extract_text_from_rtf(file_bytes)
```

4. Add the pip dependency to `requirements.txt`
5. Add a test case to `tests/test_text_extraction.py`

---

## Dependency Versions

| Package | Version | Purpose |
|---------|---------|---------|
| flask | 3.0.3 | Web server |
| anthropic | 0.40.0 | Claude API client |
| python-docx | 1.1.2 | DOCX read + write |
| pdfplumber | 0.11.4 | PDF text extraction |
| reportlab | 4.2.5 | PDF report generation |
| gunicorn | 23.0.0 | Production WSGI server |
| pyyaml | 6.0.1 | YAML parsing for guidelines |
| razorpay | 1.4.2 | Payment gateway SDK (optional) |
| google-cloud-storage | 2.18.2 | Guidelines version history in GCS (optional) |

To upgrade a dependency, update `requirements.txt`, run `pip install -r requirements.txt`, run `python -m pytest tests/ -v` to confirm no regressions, then rebuild the container.

---

## Branching Strategy

| Branch | Purpose |
|--------|---------|
| `master` / `main` | Production-ready code |
| `claude/ai-article-review-interface-*` | AI-assisted feature development |

Always run tests before merging to `main`.
