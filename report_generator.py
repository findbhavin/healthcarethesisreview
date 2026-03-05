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

STAGE_HEADERS = ["STAGE 1","STAGE 2","STAGE 3","STAGE 4","STAGE 5","STAGE 6","STAGE 7","STAGE 8"]

STAGE_TITLES = {
    "STAGE 1": "Initial Editorial Screening",
    "STAGE 2": "Scope and Novelty Check",
    "STAGE 3": "Methodology Review",
    "STAGE 4": "Results and Data Integrity",
    "STAGE 5": "Discussion and Conclusions",
    "STAGE 6": "References",
    "STAGE 7": "Ethical and Integrity Checks",
    "STAGE 8": "Overall Editorial Recommendation",
}


def _decision_color(decision: str):
    d = (decision or "").lower()
    if "accept as is" in d or d == "accept": return C_ACCEPT
    if "minor"  in d: return C_AMBER
    if "major"  in d: return C_MAJOR
    if "reject" in d: return C_REJECT
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
    """Escape special XML chars for Paragraph content."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
_RE_SEVERITY_TAG   = re.compile(r"^\[?(MAJOR|MINOR|SUGGESTION)\]?\s*[-:]?\s*", re.IGNORECASE)


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

        # ── Stage heading line (e.g. "STAGE 3: METHODOLOGY REVIEW") ──────
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

        # ── Decision line ──────────────────────────────────────────────────
        if _RE_DECISION_LINE.match(stripped):
            decision_text = stripped.split(":", 1)[1].strip() if ":" in stripped else stripped
            dec_color = _decision_color(decision_text)
            hex_c = dec_color.hexval()
            items.append(Paragraph(
                f'<b>Decision: <font color="{hex_c}">{_esc(decision_text)}</font></b>',
                decision_style,
            ))
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
        1: "Initial Editorial Screening",
        2: "Scope & Novelty",
        3: "Methodology Review",
        4: "Results & Data Integrity",
        5: "Discussion & Conclusions",
        6: "References",
        7: "Ethical & Integrity",
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

    for stage_num in range(1, 8):
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
        "WRS = Σ(Stage Score × Stage Weight) ÷ 10  |  Weights: S1:8%, S2:12%, S3:25%, S4:20%, S5:15%, S6:7%, S7:13%",
        small_style,
    ))
    story.append(Spacer(1, 12))
    return story


def generate_report(review_result: dict) -> bytes:
    """
    Generate a PDF report from the review_result dict produced by run_review().
    Returns raw bytes of the PDF.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2*cm, bottomMargin=2*cm,
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
    decision        = review_result.get("decision", "See report")
    manuscript_title = review_result.get("manuscript_title", "—")
    dec_color       = _decision_color(decision)

    info_data = [
        [Paragraph("Manuscript Title",     label_style), Paragraph(_esc(manuscript_title), value_style)],
        [Paragraph("File Name",            label_style), Paragraph(_esc(review_result.get("filename","—")), value_style)],
        [Paragraph("Word Count (approx.)", label_style), Paragraph(str(review_result.get("word_count","—")), value_style)],
        [Paragraph("Target Journal",       label_style), Paragraph(_esc(review_result.get("journal_name","Not specified") or "Not specified"), value_style)],
        [Paragraph("Date of Review",       label_style), Paragraph(date.today().strftime("%B %d, %Y"), value_style)],
        [Paragraph("Reviewer",             label_style), Paragraph("AI-Assisted Editorial Review System", value_style)],
        [Paragraph("Editorial Decision",   label_style), Paragraph(f'<font color="{dec_color.hexval()}">{_esc(decision)}</font>', value_style)],
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

    # ── Weighted Review Score card ─────────────────────────────────────────
    scorecard_items = _build_scorecard(review_result, styles)
    story.extend(scorecard_items)

    # ── Stage sections ─────────────────────────────────────────────────────
    story.append(Paragraph("Review Findings", h2_style))
    story.append(HRFlowable(width="100%", thickness=0.8, color=C_BLUE_LIGHT, spaceAfter=8))

    stages = _split_into_stages(review_result.get("review_text",""))

    stage_scores = review_result.get("stage_scores") or {}

    for stage_key in STAGE_HEADERS:
        stage_content = stages.get(stage_key, "")
        if not stage_content:
            continue

        title = STAGE_TITLES.get(stage_key, stage_key)

        # Stage number for score lookup (e.g. "STAGE 3" → 3)
        stage_num_match = re.search(r"\d+", stage_key)
        stage_num = int(stage_num_match.group()) if stage_num_match else None
        stage_score = stage_scores.get(stage_num) if stage_num else None

        # Build heading with inline score badge if available
        if stage_score is not None and stage_num != 8:
            sc = _score_color(stage_score)
            heading_html = (
                f'<b>{_esc(stage_key)}: {_esc(title)}</b>'
                f'&nbsp;&nbsp;<font color="{sc.hexval()}" size="10">({stage_score}/10)</font>'
            )
        else:
            heading_html = f"<b>{_esc(stage_key)}: {_esc(title)}</b>"

        heading = Paragraph(heading_html, h2_style)

        # Coloured left-border box per stage (background highlight strip)
        body_items = [
            heading,
            HRFlowable(width="100%", thickness=0.6, color=C_BLUE_LIGHT, spaceAfter=5),
        ]

        # Render full stage body using the detailed renderer
        body_items.extend(
            _render_stage_body(
                stage_content, styles,
                body_style, body_major, body_minor, body_sug,
                h2_style, h3_style,
            )
        )

        # Keep heading + HR + first 2 content items together to avoid
        # an orphan heading at bottom of a page
        keep_count = min(4, len(body_items))
        story.append(KeepTogether(body_items[:keep_count]))
        for item in body_items[keep_count:]:
            story.append(item)
        story.append(Spacer(1, 12))

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

    # ── Footer ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 20))
    story.append(Paragraph(
        "This report was generated by an AI-assisted peer review system. "
        "It is intended to support, not replace, human editorial judgment.",
        footer_style,
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()
