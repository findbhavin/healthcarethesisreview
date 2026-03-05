# AI-Assisted Peer Review System

A web application that automates the 8-stage editorial peer review of healthcare journal manuscripts using Claude AI (claude-opus-4-6). Editors upload a manuscript (DOCX, PDF, or TXT) and receive a structured review report covering methodology, references, ethics, and an editorial recommendation.

---

## Quick Start

```bash
# 1. Clone and enter the repo
git clone https://github.com/findbhavin/healthcarethesisreview
cd healthcarethesisreview

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your Anthropic API key
export ANTHROPIC_API_KEY="sk-ant-..."

# 4. Run the app
python app.py

# 5. Open http://localhost:8080
```

---

## Project Structure

```
healthcarethesisreview/
├── app.py                      Flask web server + all HTTP routes
├── review_agent.py             Text extraction + Claude API call
├── report_generator.py         DOCX report generation
├── index.html                  Single-page web UI
├── requirements.txt            Python dependencies
├── Dockerfile                  GCP Cloud Run container definition
│
├── guidelines/
│   ├── review_guidelines.yaml  ← SME-editable review framework
│   └── guidelines_loader.py    Loads YAML → builds Claude system prompt
│
├── docs/                       Reference checklists & guidelines (PDF/DOCX)
│   ├── CONSORT 2025 editable checklist.docx
│   ├── PRISMA_2020_checklist.docx
│   ├── STROBE checklist.pdf
│   ├── ICMJE_Guidelines.pdf
│   ├── Peer_Reviewer_Guidelines.pdf
│   ├── NJCM_Recommendations_Guideline.pdf
│   ├── Manuscript_Language_Guideline.pdf
│   └── Author_Revision_Report_Form.docx
│
├── inputs/                     Sample manuscripts for testing
├── tests/                      Automated test suite (57 tests)
│   ├── test_guidelines_loader.py
│   ├── test_text_extraction.py
│   ├── test_report_generator.py
│   └── test_app_routes.py
│
├── USER_GUIDE.md
└── DEVELOPER_GUIDE.md
```

---

## The 8-Stage Review Framework

| Stage | Name | Key Checks |
|-------|------|-----------|
| 1 | Initial Editorial Screening | Completeness, ORCID, CRediT, ethics statement |
| 2 | Scope & Novelty Check | Journal fit · Scope Fit: Strong / Marginal / Out of Scope |
| 3 | Methodology Review | Study design · CONSORT/PRISMA/STROBE · biases |
| 4 | Results & Data Integrity | Tables vs text · p-values · CIs |
| 5 | Discussion & Conclusions | Overclaiming · limitations · significance |
| 6 | References | Missing citations · fabricated refs · formatting |
| 7 | Ethical & Integrity Checks | Ethics approval · plagiarism · authorship |
| 8 | Overall Recommendation | Accept / Minor / Major / Reject / Resubmit |

All checks are defined in **`guidelines/review_guidelines.yaml`** — no code changes needed to update them.

---

## Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

Expected: **57 passed** (no API key required — Claude is mocked in tests).

---

## GCP Deployment

See [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for full Cloud Run deployment instructions.

---

## Updating Review Guidelines

See [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for how SMEs can edit `guidelines/review_guidelines.yaml`.
