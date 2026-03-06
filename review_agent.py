"""
review_agent.py
Core AI review logic — extracts manuscript text, builds the system prompt
from guidelines/review_guidelines.yaml, and calls the Claude API.

The review framework (stages, checks, decision options) is entirely defined
in review_guidelines.yaml. Editors and SMEs can update that file without
touching this code.
"""

import os
import re
import logging
import anthropic
from guidelines.guidelines_loader import build_system_prompt, get_stage_weights

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


# Cap very long manuscripts to keep token usage and latency reasonable.
_MAX_MANUSCRIPT_CHARS = 80_000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_MANUSCRIPT_CHARS:
        return text
    logger.warning(
        f"Manuscript is {len(text):,} chars — truncating to {_MAX_MANUSCRIPT_CHARS:,} "
        "to reduce latency. Consider splitting very long documents."
    )
    return text[:_MAX_MANUSCRIPT_CHARS] + "\n\n[NOTE: Manuscript truncated at 80,000 characters for processing.]"


# ---------------------------------------------------------------------------
# Score Extraction
# ---------------------------------------------------------------------------

def _extract_stage_scores(review_text: str) -> dict:
    """
    Parse per-stage scores from the review text.
    Looks for patterns like:
      Score: 7/10  or  Score: 8/10  (within each STAGE block)

    Returns a dict:
      {
        "stage_scores": {1: 7, 2: 9, 3: 6, 4: 8, 5: 7, 6: 9, 7: 10},
        "weighted_score": 72.5,
        "wrs_display": "7×8 + 9×12 + ..."
      }
    """
    weights = get_stage_weights()
    stage_scores: dict[int, int] = {}

    # Find each STAGE N block and look for Score: X/10 within it
    stage_pattern = re.compile(
        r"STAGE\s+(\d)\s*:.*?(?=STAGE\s+\d\s*:|END OF REVIEW|$)",
        re.DOTALL | re.IGNORECASE,
    )
    score_pattern = re.compile(r"Score\s*:\s*(\d+)\s*/\s*10", re.IGNORECASE)

    for match in stage_pattern.finditer(review_text):
        stage_num = int(match.group(1))
        block_text = match.group(0)
        score_match = score_pattern.search(block_text)
        if score_match:
            stage_scores[stage_num] = min(10, max(0, int(score_match.group(1))))

    # Also try to extract from a "Weighted Review Score" line in Stage 8
    wrs_line_match = re.search(
        r"Weighted Review Score.*?:\s*([\d.]+)\s*/\s*100",
        review_text, re.IGNORECASE,
    )

    # Compute weighted score from parsed stage scores
    weighted_sum = 0.0
    max_possible = 0.0
    wrs_parts = []
    for stage_num in range(1, 8):  # Stages 1–7 are scored; Stage 8 is derived
        weight = weights.get(stage_num, 0)
        score = stage_scores.get(stage_num)
        if score is not None and weight > 0:
            weighted_sum += score * weight
            max_possible += 10 * weight
            wrs_parts.append(f"S{stage_num}={score}×{weight}")

    if max_possible > 0:
        weighted_score = round(weighted_sum / 10, 1)
    else:
        # Fall back to the parsed WRS line if available
        weighted_score = float(wrs_line_match.group(1)) if wrs_line_match else None

    return {
        "stage_scores": stage_scores,
        "weighted_score": weighted_score,
        "wrs_parts": " + ".join(wrs_parts),
    }


# ---------------------------------------------------------------------------
# Review Runner
# ---------------------------------------------------------------------------

def run_review(file_bytes: bytes, filename: str, journal_name: str = "",
               article_type: str = "", journal_tier: str = "") -> dict:
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

    manuscript_text = _truncate(manuscript_text)
    word_count = len(manuscript_text.split())
    logger.info(
        f"Extracted {word_count} words from '{filename}' "
        f"(journal='{journal_name or 'not specified'}')"
    )

    # Build system prompt from guidelines YAML (picks up any SME edits)
    system_prompt = build_system_prompt(journal_name=journal_name)

    # Build context header for article type and journal tier
    context_lines = []
    if article_type:
        context_lines.append(f"Article Type: {article_type}")
    if journal_tier:
        context_lines.append(f"Target Journal Tier: {journal_tier}")
    context_header = ("\n".join(context_lines) + "\n\n") if context_lines else ""

    user_message = (
        f"{context_header}"
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
        model="claude-sonnet-4-6",
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

    scores = _extract_stage_scores(review_text)

    return {
        "manuscript_title": manuscript_title,
        "review_text": review_text,
        "word_count": word_count,
        "decision": decision,
        "filename": filename,
        "journal_name": journal_name,
        "article_type": article_type,
        "journal_tier": journal_tier,
        "stage_scores": scores["stage_scores"],
        "weighted_score": scores["weighted_score"],
        "wrs_parts": scores["wrs_parts"],
    }


def stream_review(file_bytes: bytes, filename: str, journal_name: str = "",
                  article_type: str = "", journal_tier: str = ""):
    """
    Streaming version of run_review. Yields dicts for SSE:
      {"type": "chunk", "text": "..."}          — incremental text
      {"type": "done",  "result": {...}}         — final result dict
      {"type": "error", "error": "..."}          — on failure

    The caller (Flask route) is responsible for JSON-encoding each dict.
    """
    try:
        manuscript_text = extract_text(file_bytes, filename)
        if not manuscript_text.strip():
            yield {"type": "error", "error": "Could not extract text from the file. Ensure it is not encrypted or image-only."}
            return

        manuscript_text = _truncate(manuscript_text)
        word_count = len(manuscript_text.split())
        logger.info(
            f"[stream] Extracted {word_count} words from '{filename}' "
            f"(journal='{journal_name or 'not specified'}')"
        )

        system_prompt = build_system_prompt(journal_name=journal_name)
        context_lines = []
        if article_type:
            context_lines.append(f"Article Type: {article_type}")
        if journal_tier:
            context_lines.append(f"Target Journal Tier: {journal_tier}")
        context_header = ("\n".join(context_lines) + "\n\n") if context_lines else ""
        user_message = (
            f"{context_header}"
            f"Please conduct a full peer review of the following manuscript:\n\n"
            f"--- MANUSCRIPT BEGIN ---\n{manuscript_text}\n--- MANUSCRIPT END ---"
        )

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            yield {"type": "error", "error": "ANTHROPIC_API_KEY is not configured."}
            return

        client = anthropic.Anthropic(api_key=api_key)
        full_text_parts = []

        logger.info("[stream] Streaming review from Claude API...")
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text_chunk in stream.text_stream:
                full_text_parts.append(text_chunk)
                yield {"type": "chunk", "text": text_chunk}

        review_text = "".join(full_text_parts)
        logger.info("[stream] Review stream completed.")

        # Parse decision
        decision = "See report"
        for line in review_text.splitlines():
            if line.strip().lower().startswith("decision:"):
                decision = line.split(":", 1)[1].strip()
                break

        # Parse title
        manuscript_title = filename
        for line in review_text.splitlines():
            if "manuscript title:" in line.lower():
                candidate = line.split(":", 1)[1].strip()
                if candidate and candidate not in ("Not provided", "[Not provided]", "—"):
                    manuscript_title = candidate
                break

        scores = _extract_stage_scores(review_text)

        result = {
            "manuscript_title": manuscript_title,
            "review_text": review_text,
            "word_count": word_count,
            "decision": decision,
            "filename": filename,
            "journal_name": journal_name,
            "article_type": article_type,
            "journal_tier": journal_tier,
            "stage_scores": scores["stage_scores"],
            "weighted_score": scores["weighted_score"],
            "wrs_parts": scores["wrs_parts"],
        }
        yield {"type": "done", "result": result}

    except Exception as exc:
        logger.exception("[stream] Error during streaming review")
        yield {"type": "error", "error": str(exc)}
