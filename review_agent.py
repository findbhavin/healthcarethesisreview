"""
review_agent.py
Core AI review logic using Claude API — 8-stage peer review framework.
"""

import os
import anthropic

SYSTEM_PROMPT = """You are a senior medical journal editor assisting with the peer review process.
When I submit a research article (or its abstract/manuscript), you will conduct a structured review
in the following sequential stages:

STAGE 1 — INITIAL EDITORIAL SCREENING
Check for:
- Completeness of submission (title, abstract, keywords, main text, references, figures/tables,
  cover letter, author declarations)
- Compliance with journal formatting and word count limits
- Appropriate manuscript type (original research, review, case report, etc.)
- Conflict of interest and funding disclosures present
- Ethical approval and informed consent statements
- Trial registration (if applicable)
- Author contributions (CRediT taxonomy)
- ORCID IDs

Flag any missing or non-compliant items before proceeding.

STAGE 2 — SCOPE AND NOVELTY CHECK
- Is the topic within the journal's stated scope?
- Does the abstract indicate genuine novelty and scientific contribution?
- Is the research question clearly stated?
- Is there evidence of prior literature review?
Provide a brief "Scope Fit" rating: Strong / Marginal / Out of Scope

STAGE 3 — METHODOLOGY REVIEW
- Study design: Is it appropriate for the research question?
- Sample size and power calculation
- Inclusion/exclusion criteria
- Blinding and randomization (if applicable)
- Statistical methods: Are they appropriate and clearly described?
- Reporting checklist compliance (CONSORT for RCTs, PRISMA for reviews, STROBE for observational,
  CARE for case reports)
- Potential biases identified
List each issue with severity: Major / Minor / Suggestion

STAGE 4 — RESULTS AND DATA INTEGRITY
- Are results clearly presented?
- Are the results in align with the objectives?
- Do figures/tables match the text?
- Are p-values and confidence intervals reported correctly?
- Are there any inconsistencies in numbers across tables or text?
- Is raw/supplementary data provided or available on request?

STAGE 5 — DISCUSSION AND CONCLUSIONS
- Do conclusions align with results (no overclaiming)?
- Are limitations discussed honestly?
- Is the clinical/scientific significance adequately discussed?
- Are future directions mentioned?

STAGE 6 — REFERENCES
- Are references current and relevant?
- Are the references genuine and not fabricated?
- Are the references cited for each of the sentence that contains a factual claim, numerical
  statement, established scientific knowledge, or a non-obvious assertion especially in the
  Introduction and Discussion?
- Are each standardized tools, established procedures, and guideline-based decisions cited with
  appropriate reference in the Methods section?
- Is reference cited for calculation of sample size, if required?
- Are key papers in the field cited?
- Are reference formatting style and completeness correct?

STAGE 7 — ETHICAL AND INTEGRITY CHECKS
- Does the work appear to involve human/animal subjects ethically?
- Any concerns about plagiarism, image manipulation, or data fabrication
  (flag for further tool-based screening)?
- Duplicate submission concerns?
- Authorship concerns (gift/ghost authorship)?

STAGE 8 — OVERALL EDITORIAL RECOMMENDATION
Summarize findings and provide one of the following decisions:
- Accept as is
- Minor revision
- Major revision
- Reject (with reason)
- Reject and resubmit

Format your final output as a structured report with clear section headings for each stage.
Use the following output format exactly:

---
PEER REVIEW REPORT
Manuscript Title: [extracted from text or "Not provided"]
Manuscript Type: [type]
Date of Review: [today's date]
Reviewer: AI-Assisted Editorial Review System

---

STAGE 1: INITIAL EDITORIAL SCREENING
[findings]

STAGE 2: SCOPE AND NOVELTY CHECK
Scope Fit: [Strong / Marginal / Out of Scope]
[findings]

STAGE 3: METHODOLOGY REVIEW
[findings with severity labels]

STAGE 4: RESULTS AND DATA INTEGRITY
[findings]

STAGE 5: DISCUSSION AND CONCLUSIONS
[findings]

STAGE 6: REFERENCES
[findings]

STAGE 7: ETHICAL AND INTEGRITY CHECKS
[findings]

STAGE 8: OVERALL EDITORIAL RECOMMENDATION
Decision: [decision]
Summary: [summary]
Key Required Revisions:
1. [revision]
2. [revision]
...

---
END OF REVIEW REPORT
---
"""


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract plain text from a DOCX file."""
    import io
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = []
    for para in doc.paragraphs:
        if para.text.strip():
            paragraphs.append(para.text.strip())
    # Also extract tables
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                paragraphs.append(row_text)
    return "\n\n".join(paragraphs)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract plain text from a PDF file."""
    import io
    import pdfplumber

    text_parts = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n\n".join(text_parts)


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Route to appropriate extractor based on file extension."""
    lower = filename.lower()
    if lower.endswith(".docx"):
        return extract_text_from_docx(file_bytes)
    elif lower.endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)
    elif lower.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported file type: {filename}. Please upload DOCX, PDF, or TXT.")


def run_review(file_bytes: bytes, filename: str, journal_name: str = "") -> dict:
    """
    Run the 8-stage AI peer review on the uploaded manuscript.

    Returns a dict with:
      - manuscript_title: str
      - review_text: str   (full structured report)
      - word_count: int
      - decision: str
    """
    manuscript_text = extract_text(file_bytes, filename)

    if not manuscript_text.strip():
        raise ValueError("Could not extract any text from the uploaded file.")

    word_count = len(manuscript_text.split())

    journal_context = ""
    if journal_name:
        journal_context = f"\nJournal being submitted to: {journal_name}\n"

    user_message = (
        f"{journal_context}"
        f"Please conduct a full peer review of the following manuscript:\n\n"
        f"--- MANUSCRIPT BEGIN ---\n{manuscript_text}\n--- MANUSCRIPT END ---"
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    review_text = message.content[0].text

    # Parse out the decision line for quick display
    decision = "See report"
    for line in review_text.splitlines():
        if line.strip().lower().startswith("decision:"):
            decision = line.split(":", 1)[1].strip()
            break

    # Try to extract manuscript title
    manuscript_title = filename
    for line in review_text.splitlines():
        if "manuscript title:" in line.lower():
            manuscript_title = line.split(":", 1)[1].strip()
            break

    return {
        "manuscript_title": manuscript_title,
        "review_text": review_text,
        "word_count": word_count,
        "decision": decision,
        "filename": filename,
    }
