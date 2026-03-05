"""
report_generator.py
Generates a formatted PDF peer review report from the AI review output.
Uses reportlab (pure-Python, no system dependencies — works on Cloud Run).

The PDF includes:
  • Manuscript info table (title, file, word count, date, decision)
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
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from guidelines.guidelines_loader import get_full_guidelines

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

    # ── Stage sections ─────────────────────────────────────────────────────
    story.append(Paragraph("Review Findings", h2_style))
    story.append(HRFlowable(width="100%", thickness=0.8, color=C_BLUE_LIGHT, spaceAfter=8))

    stages = _split_into_stages(review_result.get("review_text",""))

    for stage_key in STAGE_HEADERS:
        stage_content = stages.get(stage_key,"")
        if not stage_content:
            continue

        title = STAGE_TITLES.get(stage_key, stage_key)
        heading = Paragraph(f"<b>{stage_key}: {title}</b>", h2_style)

        lines = stage_content.splitlines()
        body_items = [heading]
        skip_first = True
        for line in lines:
            if skip_first and line.strip().upper().startswith(stage_key):
                skip_first = False
                continue
            skip_first = False
            stripped = line.strip()
            if not stripped:
                body_items.append(Spacer(1, 3))
                continue
            sev_color = _severity_color(stripped)
            if sev_color == C_MAJOR:
                st = body_major
            elif sev_color == C_AMBER:
                st = body_minor
            elif sev_color == C_SUGGEST:
                st = body_sug
            else:
                st = body_style
            body_items.append(Paragraph(_esc(stripped), st))

        story.append(KeepTogether(body_items[:4]))  # keep heading + first few lines together
        for item in body_items[4:]:
            story.append(item)
        story.append(Spacer(1, 8))

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
