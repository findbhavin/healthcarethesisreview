"""
review_agent.py
Core AI review logic — extracts manuscript text, builds the system prompt
from guidelines/review_guidelines.yaml, and calls the Claude API.

The review framework (stages, checks, decision options) is entirely defined
in review_guidelines.yaml. Editors and SMEs can update that file without
touching this code.
"""

import os
import logging
import anthropic
from guidelines.guidelines_loader import build_system_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text Extraction
# ---------------------------------------------------------------------------

def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract plain text from a DOCX file (paragraphs + tables)."""
    import io
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    parts = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(
                cell.text.strip() for cell in row.cells if cell.text.strip()
            )
            if row_text:
                parts.append(row_text)
    return "\n\n".join(parts)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract plain text from a PDF file using pdfplumber."""
    import io
    import pdfplumber

    parts = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                parts.append(page_text)
    return "\n\n".join(parts)


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Route to the appropriate extractor based on file extension."""
    lower = filename.lower()
    if lower.endswith(".docx"):
        return extract_text_from_docx(file_bytes)
    elif lower.endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)
    elif lower.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="replace")
    else:
        raise ValueError(
            f"Unsupported file type: '{filename}'. "
            "Please upload a DOCX, PDF, or TXT file."
        )


# ---------------------------------------------------------------------------
# Review Runner
# ---------------------------------------------------------------------------

def run_review(file_bytes: bytes, filename: str, journal_name: str = "") -> dict:
    """
    Run the AI peer review on an uploaded manuscript file.

    Parameters
    ----------
    file_bytes   : Raw bytes of the uploaded file
    filename     : Original filename (used to detect format)
    journal_name : Optional target journal name (used to inject journal-specific
                   requirements from review_guidelines.yaml)

    Returns
    -------
    dict with keys:
      manuscript_title : str
      review_text      : str   (full structured report)
      word_count       : int   (approximate)
      decision         : str   (parsed from report)
      filename         : str
    """
    manuscript_text = extract_text(file_bytes, filename)
    if not manuscript_text.strip():
        raise ValueError(
            "Could not extract any readable text from the uploaded file. "
            "Please ensure the file is not encrypted or image-only."
        )

    word_count = len(manuscript_text.split())
    logger.info(
        f"Extracted {word_count} words from '{filename}' "
        f"(journal='{journal_name or 'not specified'}')"
    )

    # Build system prompt from guidelines YAML (picks up any SME edits)
    system_prompt = build_system_prompt(journal_name=journal_name)

    user_message = (
        f"Please conduct a full peer review of the following manuscript:\n\n"
        f"--- MANUSCRIPT BEGIN ---\n{manuscript_text}\n--- MANUSCRIPT END ---"
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Set it before starting the application."
        )

    client = anthropic.Anthropic(api_key=api_key)

    logger.info("Sending manuscript to Claude API for review...")
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    review_text = message.content[0].text
    logger.info("Review completed successfully.")

    # Parse decision from report
    decision = "See report"
    for line in review_text.splitlines():
        if line.strip().lower().startswith("decision:"):
            decision = line.split(":", 1)[1].strip()
            break

    # Parse manuscript title from report
    manuscript_title = filename
    for line in review_text.splitlines():
        if "manuscript title:" in line.lower():
            candidate = line.split(":", 1)[1].strip()
            if candidate and candidate not in ("Not provided", "[Not provided]", "—"):
                manuscript_title = candidate
            break

    return {
        "manuscript_title": manuscript_title,
        "review_text": review_text,
        "word_count": word_count,
        "decision": decision,
        "filename": filename,
        "journal_name": journal_name,
    }
