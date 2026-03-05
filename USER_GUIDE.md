# User Guide — AI-Assisted Peer Review System

**Audience:** Journal editors, peer reviewers, editorial assistants

---

## What This System Does

This tool uses Claude AI to conduct an automated 8-stage peer review of a submitted journal manuscript. It reads your uploaded manuscript (DOCX, PDF, or TXT), analyses it against a structured editorial checklist, and produces:

1. An **on-screen review report** organised by stage
2. A downloadable **DOCX report** formatted for editorial records

The AI review is designed to *support* human editorial judgment — it identifies issues to investigate, not to replace the editor's final decision.

---

## Getting Started

### Step 1 — Open the Application

Navigate to the application URL in your web browser:
- Local (development): `http://localhost:8080`
- Cloud (production): your GCP Cloud Run URL

### Step 2 — Upload Your Manuscript

You can upload a manuscript in one of three ways:

| Method | How |
|--------|-----|
| Click to browse | Click the upload box and select your file |
| Drag and drop | Drag the file directly onto the upload box |
| File formats | PDF, DOCX (Word), or plain TXT |

**File size limit:** 50 MB maximum.

### Step 3 — Enter the Target Journal (Optional)

Type the journal name in the **Target Journal** field (e.g., `NJCM`, `BMJ`, `PLOS ONE`).

If the journal is configured in the system, the review will automatically apply journal-specific requirements:
- Word count limits
- Required section structure
- Reference style (e.g., Vancouver, APA)
- Scope definition

If the journal is not configured, a general review is performed.

### Step 4 — Run the Review

Click **Run Review**. The AI will analyse the manuscript through all 8 stages. This typically takes **60–90 seconds** depending on manuscript length.

A progress indicator will appear while the review is running.

### Step 5 — Read the Report

Once complete, the review report appears on screen, showing:

- **Editorial Decision Badge** — colour-coded:
  - Green: Accept as is
  - Amber: Minor revision
  - Orange-red: Major revision
  - Red: Reject / Reject and resubmit
- **Manuscript metadata** — title and word count
- **Full 8-stage report** — scrollable, with severity labels for each issue

### Step 6 — Download the DOCX Report

Click **Download DOCX Report** to save the formatted report to your computer. The report is suitable for:
- Storing in the editorial management system
- Sending to authors as initial editorial feedback
- Attaching to the peer review file

---

## Understanding the Review Stages

### Stage 1: Initial Editorial Screening
Checks whether the submission is *administratively complete* before any scientific review:
- Title, abstract, keywords, main text, references
- Author declarations, conflict of interest, funding
- Ethics approval, informed consent
- CRediT author contribution taxonomy
- ORCID IDs for all authors
- Trial registration (for RCTs)

> **What to do:** If items are flagged missing, request them from the authors before sending for peer review.

---

### Stage 2: Scope and Novelty Check
Assesses whether the manuscript:
- Fits within the journal's scope
- Makes a genuine scientific contribution
- States a clear research question
- Shows awareness of existing literature

A **Scope Fit** rating is given: `Strong`, `Marginal`, or `Out of Scope`.

> **What to do:** If rated "Out of Scope", reject with a brief note redirecting the authors.

---

### Stage 3: Methodology Review
The most detailed stage. Reviews:
- Study design appropriateness
- Sample size and power calculation
- Inclusion/exclusion criteria
- Blinding and randomisation
- Statistical methods
- Reporting checklist compliance (CONSORT, PRISMA, STROBE, CARE)
- Potential sources of bias

Each issue is labelled:
- **MAJOR** — must be resolved before acceptance
- **MINOR** — should be addressed but not necessarily blocks acceptance
- **SUGGESTION** — optional improvement

---

### Stage 4: Results and Data Integrity
Checks:
- Whether results are clearly and logically presented
- Alignment between stated objectives and reported results
- Consistency between tables, figures, and text
- Correct reporting of p-values and confidence intervals
- Availability of supplementary or raw data

---

### Stage 5: Discussion and Conclusions
Reviews whether:
- Conclusions are supported by the results (no overclaiming)
- Limitations are discussed honestly
- Clinical/scientific significance is adequately contextualised
- Future research directions are mentioned

---

### Stage 6: References
Checks:
- Every factual claim in the Introduction and Discussion is cited
- Methods section cites tools, procedures, and guidelines appropriately
- Sample size calculation has a reference (where applicable)
- References appear genuine (not hallucinated or fabricated)
- Key landmark papers in the field are included
- Reference formatting is consistent and complete

---

### Stage 7: Ethical and Integrity Checks
Flags:
- Missing ethics approval or informed consent statements
- Concerns about plagiarism, image manipulation, or data fabrication
- Potential duplicate submission
- Gift or ghost authorship concerns
- Patient privacy issues

> **What to do:** Any flag here should be escalated to the Editor-in-Chief before proceeding.

---

### Stage 8: Overall Editorial Recommendation

One of five decisions is given:

| Decision | Meaning |
|----------|---------|
| **Accept as is** | No significant issues; publish |
| **Minor revision** | Small corrections; no re-review needed |
| **Major revision** | Significant issues; authors revise + re-review |
| **Reject** | Fatal flaws that cannot be remedied |
| **Reject and resubmit** | Fundamental rework; welcome as a new submission |

A numbered list of **Key Required Revisions** is provided to guide the author response.

---

## Frequently Asked Questions

**Q: How long does a review take?**
Typically 60–90 seconds for a standard manuscript (2,000–4,000 words). Very long manuscripts may take up to 3 minutes.

**Q: Can I review a manuscript that is only an abstract?**
Yes. Upload the abstract as a TXT or DOCX file. Stages requiring full-text sections will note the limitation.

**Q: The report says "Not provided" for the manuscript title. Why?**
The title could not be extracted from the file. This is common with scanned PDFs. The review content is still complete.

**Q: Can I re-run a review on the same file?**
Yes — simply upload the file again and click Run Review.

**Q: Is the downloaded DOCX report editable?**
Yes. Open it in Word and add your own editorial comments before sharing.

**Q: Is my manuscript stored on the server?**
Manuscripts are held in memory for the duration of your session only. They are not written to disk or stored in a database. Closing the browser tab clears the session.

**Q: What if the journal I'm reviewing for is not in the dropdown?**
Leave the field blank or type the journal name freely. The review will still proceed using the general framework. Contact your system administrator to add journal-specific settings.

---

## Tips for Best Results

- **Use DOCX format** where possible — text extraction is most reliable.
- **Ensure the file is not password-protected** or image-only (scanned PDFs without OCR).
- **Provide the journal name** if it is configured — this adds journal-specific checks.
- **Read the full report** before making an editorial decision — the AI may flag issues that require editorial judgement.
- **The AI is a tool, not a decision-maker.** Always apply your own expertise before communicating with authors.
