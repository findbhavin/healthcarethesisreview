"""
report_generator.py
Generates a formatted PDF peer review report from the AI review output.
Uses reportlab (pure-Python, no system dependencies — works on Cloud Run).

The PDF includes:
  • Manuscript info table (title, file, word count, date, decision)
  • Weighted Review Score (WRS) scorecard with per-stage bar charts
  • 8 stage review sections with severity colour-coding
  • Guidelines Applied appendix (version, stage list, journal requirements)
"""

import io
import re
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.graphics import renderPDF

from guidelines.guidelines_loader import get_full_guidelines, get_stage_weights

# ── Colours ────────────────────────────────────────────────────────────────
C_BLUE       = colors.HexColor("#1A5C96")
C_BLUE_LIGHT = colors.HexColor("#DCE8F5")
C_BLUE_BG    = colors.HexColor("#F0F4FA")
C_MAJOR      = colors.HexColor("#CC0000")
C_MINOR      = colors.HexColor("#B86E00")
C_SUGGEST    = colors.HexColor("#007000")
C_ACCEPT     = colors.HexColor("#006600")
C_REJECT     = colors.HexColor("#AA0000")
C_AMBER      = colors.HexColor("#B86E00")
C_GREY       = colors.HexColor("#555555")
C_WHITE      = colors.white
C_DARK       = colors.HexColor("#1A1A2E")

C_SCORE_HIGH  = colors.HexColor("#006600")   # ≥ 8/10
C_SCORE_MED   = colors.HexColor("#B86E00")   # 5–7/10
C_SCORE_LOW   = colors.HexColor("#CC0000")   # < 5/10
C_SCORE_BG    = colors.HexColor("#E8F0E8")   # bar background

STAGE_HEADERS = [
    "STAGE 1", "STAGE 2", "STAGE 3", "STAGE 4", "STAGE 5", "STAGE 6",
    "STAGE 7", "STAGE 8", "STAGE 9", "STAGE 10", "STAGE 11",
]

STAGE_TITLES = {
    "STAGE 1":  "Initial Editorial Screening",
    "STAGE 2":  "Scope and Novelty Assessment",
    "STAGE 3":  "Overall Manuscript Quality Assessment",
    "STAGE 4":  "Abstract Review",
    "STAGE 5":  "Introduction Review",
    "STAGE 6":  "Methodology Review",
    "STAGE 7":  "Results and Data Integrity Review",
    "STAGE 8":  "Discussion and Conclusions Review",
    "STAGE 9":  "References Review",
    "STAGE 10": "Ethics and Integrity Checks",
    "STAGE 11": "Final Review Recommendation",
}


def _decision_color(decision: str):
    d = (decision or "").lower()
    if "accept as is" in d or d == "accept": return C_ACCEPT
    if "minor revision" in d or d == "minor": return C_AMBER
    if "major revision" in d or d == "major": return C_MAJOR
    if "rejection" in d or "reject" in d: return C_REJECT
    return C_DARK


def _split_into_stages(review_text: str) -> dict:
    stages = {}
    current_key = "PREAMBLE"
    current_lines = []
    for line in review_text.splitlines():
        matched = None
        for sh in STAGE_HEADERS:
            if line.strip().upper().startswith(sh):
                matched = sh
                break
        if matched:
            stages[current_key] = "\n".join(current_lines).strip()
            current_key = matched
            current_lines = [line]
        else:
            current_lines.append(line)
    stages[current_key] = "\n".join(current_lines).strip()
    return stages


def _severity_color(text: str):
    m = re.search(r"\b(MAJOR|MINOR|SUGGESTION)\b", text, re.IGNORECASE)
    if not m:
        return None
    sev = m.group(1).upper()
    return C_MAJOR if sev == "MAJOR" else C_AMBER if sev == "MINOR" else C_SUGGEST


def _esc(text: str) -> str:
    """Sanitize Unicode and escape special XML chars for ReportLab Paragraph content."""
    text = _sanitize(text or "")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Map common Unicode characters to their Latin-1 / ASCII equivalents so that
# ReportLab's built-in Helvetica font (Latin-1 only) can render them without
# producing black-rectangle glyphs.
_UNICODE_REPLACEMENTS = str.maketrans({
    # Dashes and hyphens
    "–": "-",    # en dash
    "—": "-",    # em dash
    "―": "-",    # horizontal bar
    "−": "-",    # minus sign
    # Quotes
    "‘": "'",    # left single quotation
    "’": "'",    # right single quotation
    "‚": ",",    # single low-9 quotation
    "“": '"',    # left double quotation
    "”": '"',    # right double quotation
    "„": '"',    # double low-9 quotation
    "‹": "<",    # single left angle quotation
    "›": ">",    # single right angle quotation
    # Ellipsis and dots
    "…": "...",  # ellipsis
    "‧": ".",    # hyphenation point
    # Arrows
    "→": "->",   # rightwards arrow
    "←": "<-",   # leftwards arrow
    "↔": "<->",  # left right arrow
    "▶": ">",    # black right-pointing triangle
    "▸": ">",    # black right-pointing small triangle
    "►": ">",    # black right-pointing pointer
    "◄": "<",    # black left-pointing pointer
    "‣": ">",    # triangular bullet
    # Bullets and boxes
    "•": "-",    # bullet
    "‣": "-",    # triangular bullet
    "▪": "-",    # black small square
    "▫": "-",    # white small square
    "■": "[x]",  # black square (checkbox ticked)
    "□": "[ ]",  # white square (checkbox empty)
    "☐": "[ ]",  # ballot box
    "☑": "[x]",  # ballot box with check
    "☒": "[x]",  # ballot box with X
    "✓": "(ok)", # check mark
    "✔": "(ok)", # heavy check mark
    "✕": "(x)",  # multiplication x
    "✖": "(x)",  # heavy multiplication x
    "✗": "(x)",  # ballot x
    "✘": "(x)",  # heavy ballot x
    # Spaces
    " ": " ",    # non-breaking space
    "​": "",     # zero-width space
    "‌": "",     # zero-width non-joiner
    "‍": "",     # zero-width joiner
    "﻿": "",     # BOM / zero-width no-break space
    # Mathematical
    "°": " deg", # degree sign (already Latin-1 but included for clarity)
    "±": "+/-",  # plus-minus
    "×": "x",    # multiplication sign
    "÷": "/",    # division sign
    "≤": "<=",   # less-than or equal
    "≥": ">=",   # greater-than or equal
    "≈": "~",    # almost equal
    "≠": "!=",   # not equal
    # Misc punctuation
    "†": "+",    # dagger
    "‡": "++",   # double dagger
    "·": ".",    # middle dot
    "′": "'",    # prime
    "″": "''",   # double prime
})


def _sanitize(text: str) -> str:
    """
    Replace Unicode characters that Helvetica (Latin-1) cannot render,
    then drop any remaining non-Latin-1 characters to prevent black rectangles.
    """
    if not text:
        return text
    text = text.translate(_UNICODE_REPLACEMENTS)
    # Drop any remaining non-Latin-1 characters (encode to latin-1, replace unknowns)
    return text.encode("latin-1", errors="replace").decode("latin-1").replace("?", " ").rstrip()


# ── Line-type classifiers ──────────────────────────────────────────────────

_RE_SCORE_LINE     = re.compile(r"^Score\s*:\s*(\d+)\s*/\s*10", re.IGNORECASE)
_RE_WRS_LINE       = re.compile(r"^Weighted Review Score", re.IGNORECASE)
_RE_DECISION_LINE  = re.compile(r"^Decision\s*:", re.IGNORECASE)
_RE_SUMMARY_LINE   = re.compile(r"^Summary\s*:", re.IGNORECASE)
_RE_SCOPE_LINE     = re.compile(r"^Scope Fit\s*:", re.IGNORECASE)
_RE_BREAKDOWN_LINE = re.compile(r"^Score Breakdown\s*:", re.IGNORECASE)
_RE_SEPARATOR      = re.compile(r"^-{3,}$")
_RE_BULLET         = re.compile(r"^[-•*]\s+")
_RE_NUMBERED       = re.compile(r"^\d+\.\s+")
_RE_SUB_HEADING    = re.compile(r"^[A-Z][A-Za-z &/()]+:\s*$")   # e.g. "Key Required Revisions:"
_RE_INLINE_LABEL   = re.compile(r"^([A-Z][A-Za-z &/()]+):\s+(.+)$")  # e.g. "Decision: Major revision"
_RE_SEVERITY_TAG   = re.compile(r"^\[?(MAJOR|MINOR|INFO|SUGGESTION)\]?\s*[-:]?\s*", re.IGNORECASE)


def _render_stage_body(stage_content: str, styles,
                        body_style, body_major, body_minor, body_sug,
                        h2_style, h3_style) -> list:
    """
    Convert stage content text into a rich list of reportlab Flowables.
    Handles: score badges, decision boxes, bullet/numbered lists,
    severity-tagged items, sub-headings, scope fit, separators, and plain text.
    All text is preserved — nothing is skipped.
    """

    score_badge_style = ParagraphStyle(
        "ScoreBadge", parent=styles["Normal"],
        fontSize=10, fontName="Helvetica-Bold",
        spaceBefore=4, spaceAfter=4,
    )
    decision_style = ParagraphStyle(
        "DecisionBox", parent=styles["Normal"],
        fontSize=11, fontName="Helvetica-Bold",
        spaceBefore=6, spaceAfter=6,
        leftIndent=8, borderPad=6,
    )
    summary_style = ParagraphStyle(
        "Summary", parent=styles["Normal"],
        fontSize=9.5, leading=14, fontName="Helvetica-Oblique",
        spaceBefore=3, spaceAfter=3,
    )
    sub_heading_style = ParagraphStyle(
        "SubH", parent=styles["Normal"],
        fontSize=10, fontName="Helvetica-Bold",
        textColor=C_BLUE, spaceBefore=8, spaceAfter=3,
    )
    bullet_style = ParagraphStyle(
        "Bullet", parent=styles["Normal"],
        fontSize=9.5, leading=14, spaceAfter=2,
        leftIndent=14, firstLineIndent=-10,
    )
    numbered_style = ParagraphStyle(
        "Numbered", parent=styles["Normal"],
        fontSize=9.5, leading=14, spaceAfter=2,
        leftIndent=20, firstLineIndent=-14,
    )
    scope_style = ParagraphStyle(
        "Scope", parent=styles["Normal"],
        fontSize=10, fontName="Helvetica-Bold",
        spaceBefore=4, spaceAfter=4,
    )
    inline_label_style = ParagraphStyle(
        "InlineLabel", parent=styles["Normal"],
        fontSize=9.5, leading=14, spaceAfter=3,
    )

    items = []
    lines = stage_content.splitlines()
    i = 0

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        i += 1

        # ── Skip empty lines → small spacer ───────────────────────────────
        if not stripped:
            items.append(Spacer(1, 4))
            continue

        # ── Separator lines (---) → thin HR ───────────────────────────────
        if _RE_SEPARATOR.match(stripped):
            items.append(HRFlowable(width="100%", thickness=0.5,
                                    color=C_BLUE_LIGHT, spaceBefore=4, spaceAfter=4))
            continue

        # ── Stage heading line (e.g. "STAGE 6: METHODOLOGY REVIEW") ──────
        matched_stage = any(stripped.upper().startswith(sh) for sh in STAGE_HEADERS)
        if matched_stage:
            # Already rendered as the section heading; skip this line
            continue

        # ── Score line (Score: 7/10) ───────────────────────────────────────
        m = _RE_SCORE_LINE.match(stripped)
        if m:
            score_val = int(m.group(1))
            sc = _score_color(score_val)
            sc_hex = sc.hexval()
            label = f'<font color="{sc_hex}">&#x25CF;</font> <b>Score: <font color="{sc_hex}">{score_val}/10</font></b>'
            # Add rating label
            if score_val >= 9:   rating = "Excellent"
            elif score_val >= 7: rating = "Good"
            elif score_val >= 5: rating = "Adequate"
            elif score_val >= 3: rating = "Poor"
            else:                rating = "Critical"
            label += f' <font color="{C_GREY.hexval()}">— {rating}</font>'
            items.append(Paragraph(label, score_badge_style))
            continue

        # ── Weighted Review Score line ─────────────────────────────────────
        if _RE_WRS_LINE.match(stripped):
            items.append(Paragraph(f"<b>{_esc(stripped)}</b>", score_badge_style))
            continue

        # ── Score Breakdown line ───────────────────────────────────────────
        if _RE_BREAKDOWN_LINE.match(stripped):
            items.append(Paragraph(_esc(stripped),
                         ParagraphStyle("BD", parent=styles["Normal"],
                                        fontSize=8.5, textColor=C_GREY,
                                        fontName="Helvetica-Oblique",
                                        spaceAfter=3)))
            continue

        # ── Decision line (hidden — internal only) ─────────────────────────
        if _RE_DECISION_LINE.match(stripped):
            continue

        # ── Scope Fit line ─────────────────────────────────────────────────
        if _RE_SCOPE_LINE.match(stripped):
            scope_val = stripped.split(":", 1)[1].strip() if ":" in stripped else stripped
            if "strong" in scope_val.lower():
                sc_color = C_ACCEPT
            elif "out of scope" in scope_val.lower():
                sc_color = C_REJECT
            else:
                sc_color = C_AMBER
            items.append(Paragraph(
                f'<b>Scope Fit: <font color="{sc_color.hexval()}">{_esc(scope_val)}</font></b>',
                scope_style,
            ))
            continue

        # ── Summary line ───────────────────────────────────────────────────
        if _RE_SUMMARY_LINE.match(stripped):
            rest = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if rest:
                items.append(Paragraph(f"<b>Summary:</b> <i>{_esc(rest)}</i>", summary_style))
            else:
                items.append(Paragraph(f"<b>Summary:</b>", sub_heading_style))
            continue

        # ── Standalone sub-headings (e.g. "Key Required Revisions:") ──────
        if _RE_SUB_HEADING.match(stripped):
            items.append(Paragraph(f"<b>{_esc(stripped)}</b>", sub_heading_style))
            continue

        # ── Severity-tagged items: MAJOR / MINOR / SUGGESTION ─────────────
        sev_match = _RE_SEVERITY_TAG.match(stripped)
        if sev_match:
            sev_word = sev_match.group(1).upper()
            rest_of_line = stripped[sev_match.end():].strip()
            if sev_word == "MAJOR":
                tag_hex = C_MAJOR.hexval()
                st = body_major
            elif sev_word == "MINOR":
                tag_hex = C_AMBER.hexval()
                st = body_minor
            elif sev_word in ("INFO", "SUGGESTION"):
                tag_hex = C_SUGGEST.hexval()
                st = body_sug
            else:
                tag_hex = C_SUGGEST.hexval()
                st = body_sug
            label = f'<font color="{tag_hex}"><b>[{sev_word}]</b></font> {_esc(rest_of_line)}'
            items.append(Paragraph(label, st))
            continue

        # ── Inline label:value lines (e.g. "Manuscript Type: RCT") ────────
        # Only match if there's actual content after the colon
        m2 = _RE_INLINE_LABEL.match(stripped)
        if m2 and len(m2.group(1)) <= 40:
            lbl  = m2.group(1)
            val  = m2.group(2)
            # Don't re-process known special labels handled above
            skip_labels = {"Score", "Decision", "Scope Fit", "Summary",
                           "Weighted Review Score", "Score Breakdown",
                           "Reviewer", "Date of Review", "Manuscript Title",
                           "Manuscript Type"}
            if lbl not in skip_labels:
                items.append(Paragraph(
                    f"<b>{_esc(lbl)}:</b> {_esc(val)}",
                    inline_label_style,
                ))
                continue

        # ── Bullet points ─────────────────────────────────────────────────
        if _RE_BULLET.match(stripped):
            content = _RE_BULLET.sub("", stripped)
            # Check if this bullet itself has a severity colour
            sev_color = _severity_color(content)
            if sev_color == C_MAJOR:
                st = ParagraphStyle("BulMaj", parent=bullet_style, textColor=C_MAJOR)
            elif sev_color == C_AMBER:
                st = ParagraphStyle("BulMin", parent=bullet_style, textColor=C_AMBER)
            elif sev_color == C_SUGGEST:
                st = ParagraphStyle("BulSug", parent=bullet_style, textColor=C_SUGGEST)
            else:
                st = bullet_style
            items.append(Paragraph(f"&#x2022; {_esc(content)}", st))
            continue

        # ── Numbered list items ───────────────────────────────────────────
        if _RE_NUMBERED.match(stripped):
            # Preserve the number prefix
            content = stripped
            sev_color = _severity_color(content)
            if sev_color == C_MAJOR:
                st = ParagraphStyle("NumMaj", parent=numbered_style, textColor=C_MAJOR)
            elif sev_color == C_AMBER:
                st = ParagraphStyle("NumMin", parent=numbered_style, textColor=C_AMBER)
            elif sev_color == C_SUGGEST:
                st = ParagraphStyle("NumSug", parent=numbered_style, textColor=C_SUGGEST)
            else:
                st = numbered_style
            items.append(Paragraph(_esc(content), st))
            continue

        # ── Everything else: plain body text, severity-colour if applicable
        sev_color = _severity_color(stripped)
        if sev_color == C_MAJOR:
            st = body_major
        elif sev_color == C_AMBER:
            st = body_minor
        elif sev_color == C_SUGGEST:
            st = body_sug
        else:
            st = body_style
        items.append(Paragraph(_esc(stripped), st))

    return items


def _score_color(score: int):
    if score >= 8:
        return C_SCORE_HIGH
    if score >= 5:
        return C_SCORE_MED
    return C_SCORE_LOW


def _build_score_bar(score: int, max_score: int = 10, bar_width: float = 120, height: float = 10) -> Drawing:
    """Return a small horizontal bar chart Drawing for a single stage score."""
    d = Drawing(bar_width, height)
    # Background bar
    d.add(Rect(0, 0, bar_width, height, fillColor=C_SCORE_BG, strokeColor=None))
    # Score bar
    filled = (score / max_score) * bar_width if max_score else 0
    d.add(Rect(0, 0, filled, height, fillColor=_score_color(score), strokeColor=None))
    return d


def _build_scorecard(review_result: dict, styles) -> list:
    """
    Build the Weighted Review Score section flowables.
    Returns a list of reportlab Flowable objects.
    """
    stage_scores: dict = review_result.get("stage_scores") or {}
    weighted_score = review_result.get("weighted_score")
    wrs_parts = review_result.get("wrs_parts", "")

    if not stage_scores:
        return []

    weights = get_stage_weights()

    h2_style = ParagraphStyle(
        "ScoreH2", parent=styles["Heading2"],
        textColor=C_BLUE, fontSize=12, spaceBefore=14, spaceAfter=6,
    )
    label_style = ParagraphStyle("ScLbl", parent=styles["Normal"], fontSize=9, fontName="Helvetica-Bold")
    small_style  = ParagraphStyle("ScSml", parent=styles["Normal"], fontSize=8, textColor=C_GREY)
    right_style  = ParagraphStyle("ScRt",  parent=styles["Normal"], fontSize=9, alignment=TA_RIGHT)

    story = []
    story.append(Paragraph("Weighted Review Score (WRS)", h2_style))
    story.append(HRFlowable(width="100%", thickness=0.8, color=C_BLUE_LIGHT, spaceAfter=8))

    # WRS summary badge
    if weighted_score is not None:
        wrs_val = float(weighted_score)
        wrs_color = C_SCORE_HIGH if wrs_val >= 75 else (C_SCORE_MED if wrs_val >= 50 else C_SCORE_LOW)
        wrs_hex = wrs_color.hexval()
        wrs_para = Paragraph(
            f'<b>Overall Weighted Review Score: <font color="{wrs_hex}">{wrs_val:.1f} / 100</font></b>',
            ParagraphStyle("WRSBig", parent=styles["Normal"], fontSize=13, spaceBefore=4, spaceAfter=4),
        )
        story.append(wrs_para)
        if wrs_parts:
            story.append(Paragraph(f"Formula: {_esc(wrs_parts)} ÷ 10 = {wrs_val:.1f}", small_style))
        story.append(Spacer(1, 8))

    # Per-stage score table with mini bars
    stage_short_names = {
        1:  "Initial Editorial Screening",
        2:  "Scope & Novelty",
        3:  "Overall Quality",
        4:  "Abstract Review",
        5:  "Introduction Review",
        6:  "Methodology Review",
        7:  "Results & Data Integrity",
        8:  "Discussion & Conclusions",
        9:  "References Review",
        10: "Ethics & Integrity",
    }

    bar_w = 100
    bar_h = 9

    table_data = [
        [
            Paragraph("<b>Stage</b>", label_style),
            Paragraph("<b>Score</b>", label_style),
            Paragraph("<b>Weight</b>", label_style),
            Paragraph("<b>Contribution</b>", label_style),
            Paragraph("<b>Visual</b>", label_style),
        ]
    ]

    for stage_num in range(1, 11):
        score = stage_scores.get(stage_num)
        if score is None:
            continue
        weight = weights.get(stage_num, 0)
        contribution = round(score * weight / 10, 1)
        sc = _score_color(score)
        sc_hex = sc.hexval()
        bar = _build_score_bar(score, 10, bar_w, bar_h)
        table_data.append([
            Paragraph(f"<b>S{stage_num}:</b> {_esc(stage_short_names.get(stage_num, ''))}", small_style),
            Paragraph(f'<font color="{sc_hex}"><b>{score}/10</b></font>', label_style),
            Paragraph(f"{weight}%", small_style),
            Paragraph(f"{contribution}", small_style),
            bar,
        ])

    if len(table_data) > 1:
        col_widths = [5.5*cm, 1.5*cm, 1.5*cm, 2*cm, bar_w + 4]
        score_table = Table(table_data, colWidths=col_widths)
        score_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), C_BLUE),
            ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
            ("BACKGROUND",    (0, 1), (-1, -1), C_BLUE_BG),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_BLUE_BG]),
            ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#B0C8E0")),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ]))
        story.append(score_table)

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "WRS = Σ(Stage Score × Stage Weight) ÷ 10  |  "
        "Weights: S1:5%, S2:10%, S3:5%, S4:5%, S5:8%, S6:25%, S7:20%, S8:12%, S9:5%, S10:5%",
        small_style,
    ))
    story.append(Spacer(1, 12))
    return story


def _build_section_findings_table(review_result: dict, styles) -> list:
    """
    Build a compact "Section-by-Section Review Summary" table.

    Columns: Stage | Section Finding | Priority | Score
    One row per MAJOR / MINOR / SUGGESTION item extracted from the review text.
    Placed after the scorecard, before the detailed stage bodies.
    """
    review_text  = review_result.get("review_text", "")
    stage_scores = review_result.get("stage_scores") or {}
    items = _extract_revision_items(review_text)
    if not items:
        return []

    h2_style = ParagraphStyle(
        "SFT_H2", parent=styles["Heading2"],
        textColor=C_BLUE, fontSize=12, spaceBefore=14, spaceAfter=6,
    )
    cell_hdr = ParagraphStyle(
        "SFT_Hdr", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica-Bold", textColor=C_WHITE,
    )
    cell_sml = ParagraphStyle(
        "SFT_Sml", parent=styles["Normal"],
        fontSize=8.5, leading=12,
    )
    cell_pri_maj = ParagraphStyle("SFT_Maj", parent=styles["Normal"],
                                   fontSize=8, fontName="Helvetica-Bold", textColor=C_MAJOR)
    cell_pri_min = ParagraphStyle("SFT_Min", parent=styles["Normal"],
                                   fontSize=8, fontName="Helvetica-Bold", textColor=C_AMBER)
    cell_pri_sug = ParagraphStyle("SFT_Sug", parent=styles["Normal"],
                                   fontSize=8, fontName="Helvetica-Bold", textColor=C_SUGGEST)
    cell_sc  = ParagraphStyle(
        "SFT_Sc", parent=styles["Normal"],
        fontSize=8, alignment=TA_CENTER,
    )

    def pri_style(p):
        if p == "MAJOR":      return cell_pri_maj
        if p == "MINOR":      return cell_pri_min
        return cell_pri_sug

    def pri_color(p):
        if p == "MAJOR":      return C_MAJOR
        if p == "MINOR":      return C_AMBER
        if p == "SUGGESTION": return C_SUGGEST
        return C_DARK

    # stage label → (short label, stage number)
    _label_to_num = {
        "Stage 1": 1, "Stage 2": 2, "Stage 3": 3, "Stage 4": 4,
        "Stage 5": 5, "Stage 6": 6, "Stage 7": 7, "Stage 8": 8,
        "Stage 9": 9, "Stage 10": 10, "Stage 11": 11,
    }

    story = []
    story.append(Paragraph("Section-by-Section Review Summary", h2_style))
    story.append(HRFlowable(width="100%", thickness=0.8, color=C_BLUE_LIGHT, spaceAfter=6))

    # Header row
    tbl_data = [[
        Paragraph("<b>Stage</b>", cell_hdr),
        Paragraph("<b>Finding / Comment</b>", cell_hdr),
        Paragraph("<b>Priority</b>", cell_hdr),
        Paragraph("<b>Stage Score</b>", cell_hdr),
    ]]

    prev_stage = None
    for item in items:
        sec_label = item["section"]       # e.g. "Stage 3 — Methodology"
        priority  = item["priority"]
        comment   = item["comment"]

        # Extract stage number from the label
        stage_num = None
        for lbl, num in _label_to_num.items():
            if sec_label.startswith(lbl):
                stage_num = num
                break

        # Score cell — show only on first row for each stage (row-span look)
        if stage_num and stage_num != prev_stage:
            sc = stage_scores.get(stage_num)
            if sc is not None and stage_num != 11:
                sc_color = _score_color(sc)
                score_text = f'<font color="{sc_color.hexval()}"><b>{sc}/10</b></font>'
            else:
                score_text = "—"
            prev_stage = stage_num
        else:
            score_text = ""

        # Stage short label
        short = sec_label.split(" — ", 1)[-1] if " — " in sec_label else sec_label

        tbl_data.append([
            Paragraph(_esc(short), cell_sml),
            Paragraph(_esc(comment), cell_sml),
            Paragraph(
                f'<font color="{pri_color(priority).hexval()}"><b>{priority}</b></font>',
                pri_style(priority),
            ),
            Paragraph(score_text, cell_sc),
        ])

    # Table — col widths to fit A4 body (15.5 cm usable)
    col_w = [3.5*cm, 8.0*cm, 2.0*cm, 2.0*cm]
    tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0), C_BLUE),
        ("TEXTCOLOR",      (0, 0), (-1, 0), C_WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_BLUE_BG]),
        ("GRID",           (0, 0), (-1, -1), 0.3, colors.HexColor("#B0C8E0")),
        ("VALIGN",         (0, 0), (-1, -1), "TOP"),
        ("ALIGN",          (2, 0), (2, -1), "CENTER"),
        ("ALIGN",          (3, 0), (3, -1), "CENTER"),
        ("TOPPADDING",     (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 14))
    return story


def _extract_revision_items(review_text: str) -> list[dict]:
    """
    Parse the review text and extract all actionable findings into a flat list:
      [{"section": "STAGE 3: Methodology Review",
        "comment": "No power calculation provided.",
        "priority": "MAJOR"},  ...]

    Covers MAJOR / MINOR / SUGGESTION tags and numbered items from
    "Key Required Revisions" in Stage 8.
    """
    items = []
    stages = _split_into_stages(review_text)
    sev_re = re.compile(r"^\[?(MAJOR|MINOR|INFO|SUGGESTION)\]?\s*[-:]?\s*(.+)", re.IGNORECASE)
    numbered_re = re.compile(r"^\d+\.\s+(.+)")

    stage_display = {
        "STAGE 1":  "Stage 1 — Initial Editorial Screening",
        "STAGE 2":  "Stage 2 — Scope & Novelty",
        "STAGE 3":  "Stage 3 — Overall Quality",
        "STAGE 4":  "Stage 4 — Abstract Review",
        "STAGE 5":  "Stage 5 — Introduction Review",
        "STAGE 6":  "Stage 6 — Methodology",
        "STAGE 7":  "Stage 7 — Results & Data Integrity",
        "STAGE 8":  "Stage 8 — Discussion & Conclusions",
        "STAGE 9":  "Stage 9 — References",
        "STAGE 10": "Stage 10 — Ethics & Integrity",
        "STAGE 11": "Stage 11 — Final Recommendation",
    }

    for stage_key in STAGE_HEADERS:
        block = stages.get(stage_key, "")
        if not block:
            continue
        section_label = stage_display.get(stage_key, stage_key)

        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            m = sev_re.match(stripped)
            if m:
                sev = m.group(1).upper()
                if sev == "SUGGESTION":
                    sev = "INFO"
                comment = m.group(2).strip()
                if comment:
                    items.append({
                        "section": section_label,
                        "comment": comment,
                        "priority": sev,
                    })
                continue

            # Numbered items inside "Key Required Revisions" (Stage 11)
            if stage_key == "STAGE 11":
                m2 = numbered_re.match(stripped)
                if m2:
                    items.append({
                        "section": section_label,
                        "comment": m2.group(1).strip(),
                        "priority": "MAJOR",
                    })

    return items


def _priority_color(priority: str):
    p = priority.upper()
    if p == "MAJOR":                    return C_MAJOR
    if p == "MINOR":                    return C_AMBER
    if p in ("INFO", "SUGGESTION"):     return C_SUGGEST
    return C_DARK


# Maps manuscript sections → review stages (per Instruction-1.docx section mapping)
_SECTION_TO_STAGE = {
    "SECTION 1 — TITLE":                            "Stage 1 — Initial Editorial Screening",
    "SECTION 2 — KEYWORDS":                         "Stage 1 — Initial Editorial Screening",
    "SECTION 3 — ABSTRACT":                         "Stage 4 — Abstract Review",
    "SECTION 4 — INTRODUCTION":                     "Stage 5 — Introduction Review",
    "SECTION 5 — METHODS":                          "Stage 6 — Methodology Review",
    "SECTION 6 — RESULTS":                          "Stage 7 — Results & Data Integrity",
    "SECTION 7 — DISCUSSION AND CONCLUSIONS":       "Stage 8 — Discussion & Conclusions",
    "SECTION 8 — REFERENCES":                       "Stage 9 — References Review",
    "SECTION 9 — TABLES AND FIGURES":               "Stage 7 — Results (Tables & Figures)",
    "SECTION 10 — GENERAL AND MANUSCRIPT-WIDE COMMENTS": "Stages 1, 2, 3 & 10 — Editorial, Scope, Quality & Ethics",
}

# Canonical section order matching the Author_Revision_Report_Form.docx
_MANUSCRIPT_SECTIONS = [
    "SECTION 1 — TITLE",
    "SECTION 2 — KEYWORDS",
    "SECTION 3 — ABSTRACT",
    "SECTION 4 — INTRODUCTION",
    "SECTION 5 — METHODS",
    "SECTION 6 — RESULTS",
    "SECTION 7 — DISCUSSION AND CONCLUSIONS",
    "SECTION 8 — REFERENCES",
    "SECTION 9 — TABLES AND FIGURES",
    "SECTION 10 — GENERAL AND MANUSCRIPT-WIDE COMMENTS",
]

_RE_MS_SECTION = re.compile(
    r"SECTION\s+(\d+)\s*[—\-–]+\s*(.+)", re.IGNORECASE
)
_RE_MS_ITEM = re.compile(
    r"^\d+\.\s*\|?\s*(MAJOR|MINOR|INFO|SUGGESTION)\s*[:\|]?\s*(.+)", re.IGNORECASE
)
# New table-format line: "1. | comment text | MAJOR"
_RE_MS_TABLE_ITEM = re.compile(
    r"^(\d+)\.\s*\|\s*(.+?)\s*\|\s*(MAJOR|MINOR|INFO|SUGGESTION)\s*$", re.IGNORECASE
)


def _extract_manuscript_section_items(review_text: str) -> dict[str, list[dict]]:
    """
    Parse the AUTHOR REVISION REPORT block from the review text.
    Returns an OrderedDict keyed by canonical section label, each value a list of:
      {"number": int, "priority": str, "comment": str}

    If no AUTHOR REVISION REPORT block is found, returns an empty dict.
    """
    from collections import OrderedDict

    # Find the PRE-SUBMISSION REVIEW REPORT or AUTHOR REVISION REPORT block
    start_m = re.search(
        r"PRE-SUBMISSION REVIEW REPORT|AUTHOR REVISION REPORT",
        review_text, re.IGNORECASE,
    )
    if not start_m:
        return {}
    end_m = re.search(
        r"END OF (AUTHOR REVISION|PRE-SUBMISSION) REVIEW REPORT|"
        r"AI-Assisted Editorial Review System",
        review_text[start_m.end():], re.IGNORECASE,
    )
    block_end = (start_m.end() + end_m.start()) if end_m else len(review_text)
    block = review_text[start_m.end(): block_end]

    result: dict[str, list[dict]] = OrderedDict()
    current_section = None
    item_counter = 0

    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Check for a SECTION N — heading
        sec_m = _RE_MS_SECTION.match(stripped)
        if sec_m:
            # Normalise to canonical form
            sec_num = int(sec_m.group(1))
            canonical = next(
                (s for s in _MANUSCRIPT_SECTIONS if s.startswith(f"SECTION {sec_num} ")),
                f"SECTION {sec_num} — {sec_m.group(2).strip().upper()}"
            )
            current_section = canonical
            item_counter = 0
            result.setdefault(current_section, [])
            continue

        if current_section is None:
            continue

        # Skip "NONE" lines
        if stripped.upper() == "NONE":
            continue

        # Parse table-format line: "1. | comment | MAJOR"
        tbl_m = _RE_MS_TABLE_ITEM.match(stripped)
        if tbl_m:
            priority = tbl_m.group(3).upper()
            if priority == "SUGGESTION":
                priority = "INFO"
            comment = tbl_m.group(2).strip()
            if comment and comment not in ("[comment]", "Comment / Revision Required"):
                item_counter += 1
                result[current_section].append({
                    "number": item_counter,
                    "priority": priority,
                    "comment": comment,
                })
            continue

        # Parse legacy numbered comment line: "1. MAJOR: ..."
        item_m = _RE_MS_ITEM.match(stripped)
        if item_m:
            priority = item_m.group(1).upper()
            if priority == "SUGGESTION":
                priority = "INFO"
            comment = item_m.group(2).strip()
            if comment:
                item_counter += 1
                result[current_section].append({
                    "number": item_counter,
                    "priority": priority,
                    "comment": comment,
                })
        else:
            # Continuation line or plain text — append to last item if any
            if result[current_section]:
                result[current_section][-1]["comment"] += " " + stripped

    # Remove sections with no items
    return OrderedDict((k, v) for k, v in result.items() if v)


def _build_section_comment_tables(review_result: dict, story: list, styles,
                                  h2_style, h3_style, body_style) -> None:
    """
    Append per-section comment tables (matching the DOCX Author Revision Report
    format) to *story*.  Each manuscript section gets its own table with columns:
    No. | Comment / Revision Required | Priority

    The 'Author Response' column from the DOCX is intentionally excluded.
    """
    from reportlab.platypus import PageBreak

    review_text = review_result.get("review_text", "")
    sections_map = _extract_manuscript_section_items(review_text)

    if not sections_map:
        stage_items = _extract_revision_items(review_text)
        if not stage_items:
            return
        from collections import OrderedDict
        sections_map = OrderedDict()
        for item in stage_items:
            sec = item["section"]
            sections_map.setdefault(sec, []).append({
                "number": len(sections_map.get(sec, [])) + 1,
                "priority": item["priority"],
                "comment": item["comment"],
            })
    if not sections_map:
        return

    cell_hdr = ParagraphStyle(
        "SCHdr", parent=styles["Normal"],
        fontSize=8.5, fontName="Helvetica-Bold", textColor=C_WHITE,
    )
    cell_body = ParagraphStyle(
        "SCBody", parent=styles["Normal"],
        fontSize=8.5, leading=13, spaceAfter=1,
    )
    cell_center = ParagraphStyle(
        "SCCenter", parent=cell_body, alignment=TA_CENTER,
    )
    sec_title_style = ParagraphStyle(
        "SCSecTitle", parent=styles["Normal"],
        fontSize=9.5, fontName="Helvetica-Bold", textColor=C_BLUE,
        spaceBefore=14, spaceAfter=4,
    )

    story.append(PageBreak())
    story.append(Paragraph("Section-by-Section Reviewer Comments", h2_style))
    story.append(HRFlowable(width="100%", thickness=1, color=C_BLUE, spaceAfter=10))

    col_w = [1.0*cm, 11.5*cm, 3.0*cm]

    for section_label, items in sections_map.items():
        sec_display = section_label
        m = re.match(r"(SECTION\s+\d+)\s*[—\-–]+\s*(.+)", section_label, re.IGNORECASE)
        if m:
            sec_display = f"{m.group(1).upper()} — {m.group(2).strip().upper()}"

        story.append(Paragraph(sec_display, sec_title_style))

        tbl_data = [[
            Paragraph("<b>No.</b>", cell_hdr),
            Paragraph("<b>Comment / Revision Required</b>", cell_hdr),
            Paragraph("<b>Priority</b>", cell_hdr),
        ]]

        for item in items:
            priority = item["priority"]
            pc = _priority_color(priority)
            tbl_data.append([
                Paragraph(str(item["number"]), cell_center),
                Paragraph(_esc(item["comment"]), cell_body),
                Paragraph(
                    f'<font color="{pc.hexval()}"><b>{_esc(priority)}</b></font>',
                    cell_center,
                ),
            ])

        tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",     (0, 0), (-1, 0), C_BLUE),
            ("TEXTCOLOR",      (0, 0), (-1, 0), C_WHITE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_BLUE_BG]),
            ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor("#B0C8E0")),
            ("VALIGN",         (0, 0), (-1, -1), "TOP"),
            ("ALIGN",          (0, 0), (0, -1), "CENTER"),
            ("ALIGN",          (2, 0), (2, -1), "CENTER"),
            ("TOPPADDING",     (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 6),
            ("LEFTPADDING",    (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
        ]))
        story.append(tbl)

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BLUE_LIGHT,
                            spaceBefore=4, spaceAfter=6))
    story.append(Paragraph(
        "Section-by-Section Reviewer Comments  |  AI-Assisted Editorial Review System",
        ParagraphStyle("SCFooter", parent=styles["Normal"],
                       fontSize=7.5, textColor=C_GREY,
                       fontName="Helvetica-Oblique", alignment=1),
    ))


def _build_author_revision_report(review_result: dict, story: list, styles,
                                  h2_style, h3_style, body_style,
                                  label_style, value_style) -> None:
    """
    Append a consolidated Review Comments and Recommendations table to `story`.

    Columns: Sr No | Section in the Input Document | Review Comments and Recommendations

    Comments are sourced from the structured AUTHOR REVISION REPORT block that
    Claude emits at the end of its review.  Falls back to stage-based extraction
    if no such block is present.
    """
    from collections import OrderedDict
    from reportlab.platypus import PageBreak

    review_text = review_result.get("review_text", "")

    # ── Prefer manuscript-section items from the AUTHOR REVISION REPORT block
    sections_map = _extract_manuscript_section_items(review_text)

    # Fall back: extract from stage blocks (legacy / if Claude omits the ARR block)
    if not sections_map:
        stage_items = _extract_revision_items(review_text)
        if not stage_items:
            return
        for item in stage_items:
            sec = item["section"]
            sections_map.setdefault(sec, []).append({
                "number": len(sections_map.get(sec, [])) + 1,
                "priority": item["priority"],
                "comment": item["comment"],
            })

    if not sections_map:
        return

    manuscript_title = review_result.get("manuscript_title", "—")
    decision         = review_result.get("decision", "—")
    dec_color        = _decision_color(decision)

    cell_label = ParagraphStyle(
        "ARCellLbl", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica-Bold",
    )
    small_style = ParagraphStyle(
        "ARSmall", parent=styles["Normal"],
        fontSize=8.5, leading=13, spaceAfter=1,
    )

    # ── New page + header ──────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Section 2 — Review Comments and Recommendations", h2_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_BLUE, spaceAfter=10))

    # ── Manuscript info strip ──────────────────────────────────────────────
    ms_info = [
        [Paragraph("Manuscript Title", label_style),
         Paragraph(_esc(manuscript_title), value_style)],
        [Paragraph("Editorial Decision", label_style),
         Paragraph(f'<font color="{dec_color.hexval()}"><b>{_esc(decision)}</b></font>',
                   value_style)],
    ]
    ms_table = Table(ms_info, colWidths=[4*cm, 11.5*cm])
    ms_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), C_BLUE_LIGHT),
        ("BACKGROUND",    (1, 0), (1, -1), C_BLUE_BG),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#B0C8E0")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    story.append(ms_table)
    story.append(Spacer(1, 14))

    # ── Build a single flat table with sequential Sr No ────────────────────
    # Columns: Sr No (0.8 cm) | Section in the Input Document (4 cm) |
    #          Review Comments and Recommendations (10.7 cm)
    col_w_table = [0.8*cm, 4.0*cm, 10.7*cm]

    tbl_data = [[
        Paragraph("<b>Sr No</b>", cell_label),
        Paragraph("<b>Section in the<br/>Input Document</b>", cell_label),
        Paragraph("<b>Review Comments and Recommendations</b>", cell_label),
    ]]

    sr_no = 0
    for section_label, sec_items in sections_map.items():
        # Friendly short label for the section column
        # e.g. "SECTION 5 — METHODS" → "Methods"
        sec_display = section_label
        m = re.match(r"SECTION\s+\d+\s*[—\-–]+\s*(.+)", section_label, re.IGNORECASE)
        if m:
            sec_display = m.group(1).strip().title()

        for item in sec_items:
            sr_no += 1
            priority = item["priority"]
            pc_hex = _priority_color(priority).hexval()

            # Priority badge prepended to the comment text
            comment_html = (
                f'<font color="{pc_hex}"><b>[{_esc(priority)}]</b></font> '
                f'{_esc(item["comment"])}'
            )

            tbl_data.append([
                Paragraph(str(sr_no), small_style),
                Paragraph(_esc(sec_display), small_style),
                Paragraph(comment_html, small_style),
            ])

    tbl = Table(tbl_data, colWidths=col_w_table, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0), C_BLUE),
        ("TEXTCOLOR",      (0, 0), (-1, 0), C_WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_BLUE_BG]),
        ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor("#B0C8E0")),
        ("VALIGN",         (0, 0), (-1, -1), "TOP"),
        ("ALIGN",          (0, 0), (0, -1), "CENTER"),
        ("TOPPADDING",     (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 8),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
        ("TOPPADDING",     (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, 0), 5),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 12))

    # ── Footer ─────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.8, color=C_BLUE_LIGHT,
                            spaceBefore=6, spaceAfter=8))
    story.append(Paragraph(
        "Review Comments and Recommendations  |  AI-Assisted Editorial Review System",
        ParagraphStyle("ARFooter2", parent=styles["Normal"],
                       fontSize=7.5, textColor=C_GREY,
                       fontName="Helvetica-Oblique", alignment=1),
    ))


def _build_concluding_remarks(review_result: dict, story: list, styles,
                              h2_style, h3_style, body_style) -> None:
    """
    Append a 'Concluding Remarks' section to `story`.

    Includes:
      • Editorial decision (colour-coded)
      • Summary narrative (from Stage 8)
      • What the author must address (Key Required Revisions)
      • What the author should consider (MINOR/SUGGESTION items summary)
      • Note about resubmission being treated as a fresh review
    """
    from reportlab.platypus import PageBreak

    review_text = review_result.get("review_text", "")
    decision    = review_result.get("decision", "—")
    dec_color   = _decision_color(decision)

    # Parse Stage 11 for summary and key revisions (Final Recommendation stage)
    stages = _split_into_stages(review_text)
    stage11_text = stages.get("STAGE 11", "") or stages.get("STAGE 8", "")

    summary_text = ""
    key_revisions: list[str] = []
    in_revisions = False

    for line in stage11_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("summary:"):
            summary_text = stripped.split(":", 1)[1].strip()
        elif re.match(r"key required revisions", stripped, re.IGNORECASE):
            in_revisions = True
        elif in_revisions and re.match(r"\d+\.", stripped):
            rev = re.sub(r"^\d+\.\s*", "", stripped)
            if rev:
                key_revisions.append(rev)
        elif in_revisions and stripped.startswith("-"):
            rev = stripped.lstrip("-").strip()
            if rev:
                key_revisions.append(rev)

    # Styles
    decision_box_style = ParagraphStyle(
        "CRDecision", parent=styles["Normal"],
        fontSize=13, fontName="Helvetica-Bold",
        spaceBefore=8, spaceAfter=8,
    )
    summary_style = ParagraphStyle(
        "CRSummary", parent=body_style,
        fontSize=10, leading=16, spaceAfter=6,
        fontName="Helvetica-Oblique",
    )
    bullet_style = ParagraphStyle(
        "CRBullet", parent=body_style,
        fontSize=9.5, leading=14, spaceAfter=4,
        leftIndent=14, firstLineIndent=-10,
    )
    note_style = ParagraphStyle(
        "CRNote", parent=body_style,
        fontSize=9, textColor=C_GREY,
        fontName="Helvetica-Oblique", spaceBefore=14, spaceAfter=4,
        borderPad=8,
    )
    small_label = ParagraphStyle(
        "CRSmallLbl", parent=styles["Normal"],
        fontSize=10, fontName="Helvetica-Bold",
        textColor=C_BLUE, spaceBefore=12, spaceAfter=4,
    )

    story.append(PageBreak())
    story.append(Paragraph("Concluding Remarks", h2_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_BLUE, spaceAfter=12))

    # Summary
    if summary_text:
        story.append(Paragraph(f'<i>{_esc(summary_text)}</i>', summary_style))
    story.append(Spacer(1, 8))

    # What the author must address
    if key_revisions:
        story.append(Paragraph("What the Author Must Address", small_label))
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_BLUE_LIGHT, spaceAfter=6))
        for idx, rev in enumerate(key_revisions, 1):
            story.append(Paragraph(
                f'<font color="{C_MAJOR.hexval()}"><b>{idx}.</b></font> {_esc(rev)}',
                bullet_style,
            ))
        story.append(Spacer(1, 8))

    # MAJOR items from all stages (if no key revisions were parsed)
    if not key_revisions:
        major_items = [
            item for item in _extract_revision_items(review_text)
            if item["priority"] == "MAJOR"
        ]
        if major_items:
            story.append(Paragraph("What the Author Must Address", small_label))
            story.append(HRFlowable(width="100%", thickness=0.5, color=C_BLUE_LIGHT, spaceAfter=6))
            for idx, item in enumerate(major_items, 1):
                story.append(Paragraph(
                    f'<font color="{C_MAJOR.hexval()}"><b>{idx}.</b></font> '
                    f'<b>[{_esc(item["section"].split(" — ")[-1])}]</b> {_esc(item["comment"])}',
                    bullet_style,
                ))
            story.append(Spacer(1, 8))

    # Optional improvements
    minor_items = [
        item for item in _extract_revision_items(review_text)
        if item["priority"] in ("MINOR", "SUGGESTION")
    ]
    if minor_items:
        story.append(Paragraph("What the Author Should Consider", small_label))
        story.append(HRFlowable(width="100%", thickness=0.5, color=C_BLUE_LIGHT, spaceAfter=6))
        for item in minor_items[:8]:  # cap at 8 to keep this concise
            story.append(Paragraph(
                f'<font color="{C_AMBER.hexval()}">&#x25B8;</font> {_esc(item["comment"])}',
                bullet_style,
            ))
        if len(minor_items) > 8:
            story.append(Paragraph(
                f"<i>…and {len(minor_items) - 8} additional minor comment(s) — "
                "see Review Comments and Recommendations table above for full details.</i>",
                ParagraphStyle("CRMore", parent=body_style, fontSize=8.5,
                               textColor=C_GREY, fontName="Helvetica-Oblique"),
            ))
        story.append(Spacer(1, 8))

    # Resubmission note
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_BLUE_LIGHT,
                            spaceBefore=10, spaceAfter=10))
    story.append(Paragraph(
        "&#x1F4CC; <b>Note on Resubmission:</b>",
        ParagraphStyle("NoteHdr", parent=styles["Normal"],
                       fontSize=10, fontName="Helvetica-Bold",
                       textColor=C_BLUE, spaceAfter=4),
    ))
    story.append(Paragraph(
        "Authors are encouraged to address all comments and resubmit a revised manuscript. "
        "The authors may optionally return for a new AI-assisted review after revisions are made; "
        "however, any such resubmission will be treated as an entirely <b>fresh review</b> — "
        "previous scores and comments will not carry over. This ensures an unbiased evaluation "
        "of the revised work.",
        note_style,
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "This report was generated by an AI-Assisted Editorial Review System and is intended "
        "to support, not replace, human editorial judgment.",
        ParagraphStyle("CRFooter", parent=styles["Normal"],
                       fontSize=8, textColor=C_GREY,
                       fontName="Helvetica-Oblique", alignment=1, spaceBefore=10),
    ))


def _build_sample_report(story: list, styles, review_result: dict, h2_style, body_style) -> None:
    """Build a teaser/sample report with score summary and limited observations."""
    score = review_result.get("weighted_score")
    score_text = f"{score}/100" if score is not None else "Not available"
    decision = review_result.get("decision", "See full report")
    manuscript_title = review_result.get("manuscript_title", "—")

    notice_style = ParagraphStyle(
        "SampleNotice", parent=styles["Normal"],
        fontSize=10, leading=15, textColor=C_DARK,
    )
    card_label = ParagraphStyle(
        "SampleCardLabel", parent=styles["Normal"],
        fontSize=9, fontName="Helvetica-Bold", textColor=C_BLUE,
    )
    card_val = ParagraphStyle(
        "SampleCardVal", parent=styles["Normal"],
        fontSize=11, fontName="Helvetica-Bold", textColor=C_DARK,
    )

    story.append(Paragraph("Sample Preview Report", h2_style))
    story.append(Paragraph(
        "This free preview includes only a score snapshot and selected observations. "
        "Download the paid full report to unlock stage-wise analysis, detailed revisions, "
        "guideline appendix, and complete recommendations.",
        notice_style,
    ))
    story.append(Spacer(1, 10))

    summary_tbl = Table(
        [
            [Paragraph("Manuscript", card_label), Paragraph(_esc(manuscript_title), card_val)],
            [Paragraph("Weighted Review Score", card_label), Paragraph(_esc(score_text), card_val)],
        ],
        colWidths=[5 * cm, 10.5 * cm],
    )
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), C_BLUE_LIGHT),
        ("BACKGROUND", (1, 0), (1, -1), C_BLUE_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0C8E0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Major Observations (Preview)", h2_style))
    items = [i for i in _extract_revision_items(review_result.get("review_text", "")) if i["priority"] == "MAJOR"][:2]
    if not items:
        items = _extract_revision_items(review_result.get("review_text", ""))[:2]
    if items:
        for idx, item in enumerate(items, 1):
            story.append(Paragraph(
                f"{idx}. <b>{_esc(item['section'])}</b> — {_esc(item['comment'])}",
                body_style,
            ))
    else:
        story.append(Paragraph(
            "1. <b>Overall observation</b> — A full section-wise evaluation is available and "
            "includes actionable manuscript improvement points.",
            body_style,
        ))
    story.append(Paragraph(
        "The complete list of observations is included in the paid full report.",
        body_style,
    ))

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=1, color=C_BLUE_LIGHT, spaceBefore=4, spaceAfter=8))
    story.append(Paragraph(
        "You can return to this web session before it expires and complete payment to access "
        "the full peer review report with all stages, revision checklist, and "
        "manuscript-specific recommendations.",
        notice_style,
    ))
    story.append(Paragraph(
        "This review session is temporary. If your session times out, you will need to run "
        "the analysis again before downloading.",
        ParagraphStyle("SampleTimeout", parent=styles["Normal"], fontSize=9, textColor=C_GREY),
    ))


def generate_report(review_result: dict, sample_only: bool = False) -> bytes:
    """
    Generate a PDF report from the review_result dict produced by run_review().
    Returns raw bytes of the PDF.
    """
    # Resolve guidelines version for the page footer
    guidelines_version = review_result.get("guidelines_version", "")
    if not guidelines_version:
        try:
            from guidelines.guidelines_loader import get_guidelines_version
            guidelines_version = get_guidelines_version()
        except Exception:
            guidelines_version = "unknown"

    def _draw_page_footer(canvas, doc):
        """Stamp every page with a tiny rule-version footer."""
        canvas.saveState()
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(colors.HexColor("#999999"))
        footer_text = (
            f"Review Guidelines Version: {guidelines_version}  "
            f"|  AI-Assisted Editorial Review System  "
            f"|  Page {doc.page}"
        )
        page_width = A4[0]
        canvas.drawCentredString(page_width / 2.0, 0.65 * cm, footer_text)
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2*cm, bottomMargin=1.5*cm,
        title="Peer Review Report",
        author="AI-Assisted Editorial Review System",
    )

    styles = getSampleStyleSheet()
    story = []

    # ── Custom styles ──────────────────────────────────────────────────────
    title_style = ParagraphStyle(
        "ReportTitle", parent=styles["Title"],
        textColor=C_BLUE, fontSize=20, spaceAfter=4, alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "ReportSub", parent=styles["Normal"],
        textColor=C_GREY, fontSize=10, spaceAfter=16, alignment=TA_CENTER,
        fontName="Helvetica-Oblique",
    )
    h2_style = ParagraphStyle(
        "H2", parent=styles["Heading2"],
        textColor=C_BLUE, fontSize=12, spaceBefore=14, spaceAfter=6,
        borderPad=4,
    )
    h3_style = ParagraphStyle(
        "H3", parent=styles["Heading3"],
        textColor=C_BLUE, fontSize=10, spaceBefore=10, spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontSize=9.5, leading=14, spaceAfter=3,
    )
    body_major = ParagraphStyle("BodyMajor", parent=body_style, textColor=C_MAJOR)
    body_minor = ParagraphStyle("BodyMinor", parent=body_style, textColor=C_AMBER)
    body_sug   = ParagraphStyle("BodySug",   parent=body_style, textColor=C_SUGGEST)
    footer_style = ParagraphStyle(
        "Footer", parent=styles["Normal"],
        fontSize=8, textColor=C_GREY, alignment=TA_CENTER,
        fontName="Helvetica-Oblique", spaceBefore=20,
    )
    label_style = ParagraphStyle("Label", parent=styles["Normal"], fontSize=9.5, fontName="Helvetica-Bold")
    value_style = ParagraphStyle("Value", parent=styles["Normal"], fontSize=9.5)

    # ── Title block ────────────────────────────────────────────────────────
    story.append(Paragraph("PEER REVIEW REPORT", title_style))
    story.append(Paragraph("AI-Assisted Editorial Review System", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_BLUE, spaceAfter=12))

    # ── Manuscript info table ──────────────────────────────────────────────
    manuscript_title = review_result.get("manuscript_title", "—")

    info_data = [
        [Paragraph("Manuscript Title",     label_style), Paragraph(_esc(manuscript_title), value_style)],
        [Paragraph("File Name",            label_style), Paragraph(_esc(review_result.get("filename","—")), value_style)],
        [Paragraph("Word Count (approx.)", label_style), Paragraph(str(review_result.get("word_count","—")), value_style)],
        [Paragraph("Target Journal",       label_style), Paragraph(_esc(review_result.get("journal_name","Not specified") or "Not specified"), value_style)],
        [Paragraph("Date of Review",       label_style), Paragraph(date.today().strftime("%B %d, %Y"), value_style)],
        [Paragraph("Reviewer",             label_style), Paragraph("AI-Assisted Editorial Review System", value_style)],
    ]
    col_w = [4*cm, 11.5*cm]
    info_table = Table(info_data, colWidths=col_w)
    info_table.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (0,-1), C_BLUE_LIGHT),
        ("BACKGROUND",  (1,0), (1,-1), C_BLUE_BG),
        ("GRID",        (0,0), (-1,-1), 0.4, colors.HexColor("#B0C8E0")),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING",(0,0), (-1,-1), 8),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 16))

    # ── SECTION 1: Overall Category-Wise Scoring ───────────────────────────
    story.append(Paragraph("Section 1 — Overall Category-Wise Scoring", h2_style))
    story.append(HRFlowable(width="100%", thickness=0.8, color=C_BLUE_LIGHT, spaceAfter=8))
    scorecard_items = _build_scorecard(review_result, styles)
    story.extend(scorecard_items)

    if sample_only:
        _build_sample_report(story, styles, review_result, h2_style, body_style)
        doc.build(story, onFirstPage=_draw_page_footer, onLaterPages=_draw_page_footer)
        buf.seek(0)
        return buf.read()

    # ── Section-by-Section Reviewer Comments (per-section tables) ─────────
    _build_section_comment_tables(review_result, story, styles,
                                  h2_style, h3_style, body_style)

    # ── Guidelines Applied appendix ────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1, color=C_BLUE_LIGHT, spaceBefore=16, spaceAfter=8))
    story.append(Paragraph("Appendix: Guidelines Applied", h2_style))

    try:
        gl = get_full_guidelines()
        meta = gl.get("metadata", {})

        meta_data = [
            [Paragraph("Guidelines Version", label_style), Paragraph(_esc(str(meta.get("version","—"))), value_style)],
            [Paragraph("Last Updated",       label_style), Paragraph(_esc(str(meta.get("last_updated","—"))), value_style)],
            [Paragraph("Maintained By",      label_style), Paragraph(_esc(str(meta.get("maintained_by","—"))), value_style)],
            [Paragraph("Applies To",         label_style), Paragraph(_esc(str(meta.get("journal_name","—"))), value_style)],
        ]
        meta_table = Table(meta_data, colWidths=col_w)
        meta_table.setStyle(TableStyle([
            ("BACKGROUND",  (0,0), (0,-1), C_BLUE_LIGHT),
            ("BACKGROUND",  (1,0), (1,-1), C_BLUE_BG),
            ("GRID",        (0,0), (-1,-1), 0.4, colors.HexColor("#B0C8E0")),
            ("VALIGN",      (0,0), (-1,-1), "TOP"),
            ("TOPPADDING",  (0,0), (-1,-1), 5),
            ("BOTTOMPADDING",(0,0),(-1,-1), 5),
            ("LEFTPADDING", (0,0), (-1,-1), 8),
            ("RIGHTPADDING",(0,0), (-1,-1), 8),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 10))

        # Stage checklist
        story.append(Paragraph("Review Stages Applied", h3_style))
        for s in gl.get("stages", []):
            stage_label = f"Stage {s['number']}: {s['name']}"
            story.append(Paragraph(f"<b>{_esc(stage_label)}</b>", body_style))
            for check in s.get("checks", []):
                story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;&#x2713; {_esc(check)}", body_style))

        # Journal requirements (if a journal was set)
        journal_name = review_result.get("journal_name","").strip()
        if journal_name:
            for j in gl.get("journals", []):
                if j["key"].upper() == journal_name.upper() or journal_name.upper() in j["key"].upper():
                    story.append(Spacer(1, 8))
                    story.append(Paragraph(f"Journal Requirements — {_esc(j['full_name'])} ({_esc(j['key'])})", h3_style))
                    if j.get("scope"):
                        story.append(Paragraph(f"<b>Scope:</b> {_esc(j['scope'])}", body_style))
                    if j.get("reference_style"):
                        story.append(Paragraph(f"<b>Reference Style:</b> {_esc(j['reference_style'])}", body_style))
                    for k, v in (j.get("word_limits") or {}).items():
                        story.append(Paragraph(f"<b>Word limit ({k.replace('_',' ')}):</b> {v}", body_style))
                    if j.get("required_sections"):
                        secs = ", ".join(j["required_sections"])
                        story.append(Paragraph(f"<b>Required sections:</b> {_esc(secs)}", body_style))
                    break

    except Exception:
        story.append(Paragraph("Guidelines metadata could not be loaded.", body_style))

    # ── Concluding Remarks ──────────────────────────────────────────────────
    _build_concluding_remarks(review_result, story, styles,
                              h2_style, h3_style, body_style)

    doc.build(story, onFirstPage=_draw_page_footer, onLaterPages=_draw_page_footer)
    buf.seek(0)
    return buf.read()
