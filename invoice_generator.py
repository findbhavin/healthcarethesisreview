"""
invoice_generator.py
Generates a professional PDF invoice for completed Razorpay payments.
Uses reportlab (same dependency as the review report generator).

Supports two call patterns:
  1. generate_invoice(review_id, invoice_data)  — from GET /invoice/<id>
  2. generate_invoice(...)  with keyword args    — from POST /payment/send-invoice
"""

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

C_BRAND  = colors.HexColor("#00c9b1")
C_DARK   = colors.HexColor("#0f1117")
C_GREY   = colors.HexColor("#555555")
C_LIGHT  = colors.HexColor("#f5f5f5")
C_BORDER = colors.HexColor("#d0d0d0")


def generate_invoice(
    review_id_or_none=None,
    invoice_data_or_none=None,
    *,
    invoice_number: str = "",
    payment_id: str = "",
    order_id: str = "",
    amount_paise: int = 0,
    currency: str = "INR",
    customer_email: str = "",
    manuscript_title: str = "",
    payment_date: datetime | None = None,
) -> bytes:
    """Return PDF bytes for a payment invoice.

    Accepts either positional args (review_id, invoice_data dict) for the
    download endpoint, or keyword args for the email endpoint.
    """
    if invoice_data_or_none and isinstance(invoice_data_or_none, dict):
        d = invoice_data_or_none
        invoice_number = invoice_number or d.get("invoice_id", "")
        payment_id     = payment_id or d.get("payment_id", "")
        order_id       = order_id or d.get("order_id", "")
        amount_paise   = amount_paise or int(d.get("amount_paise", 0))
        currency       = d.get("currency", currency)
        customer_email = customer_email or d.get("customer_email", "")
        manuscript_title = manuscript_title or d.get("manuscript_title", "Manuscript Review")
        paid_str = d.get("paid_at_utc")
        if paid_str and not payment_date:
            try:
                payment_date = datetime.fromisoformat(paid_str)
            except (ValueError, TypeError):
                pass

    pay_date = payment_date or datetime.now(timezone.utc)
    amount_display = f"{amount_paise / 100:,.2f}"
    currency_symbol = "\u20b9" if currency == "INR" else currency + " "

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    s_title = ParagraphStyle(
        "InvTitle", parent=styles["Heading1"],
        fontSize=22, textColor=C_BRAND, spaceAfter=4,
    )
    s_subtitle = ParagraphStyle(
        "InvSub", parent=styles["Normal"],
        fontSize=10, textColor=C_GREY, spaceAfter=18,
    )
    s_normal = ParagraphStyle(
        "InvNormal", parent=styles["Normal"],
        fontSize=10, textColor=C_DARK, leading=14,
    )
    s_small = ParagraphStyle(
        "InvSmall", parent=styles["Normal"],
        fontSize=8.5, textColor=C_GREY, leading=12,
    )
    s_right = ParagraphStyle("InvRight", parent=s_normal, alignment=TA_RIGHT)
    s_center = ParagraphStyle("InvCenter", parent=s_normal, alignment=TA_CENTER)
    s_bold = ParagraphStyle("InvBold", parent=s_normal, fontName="Helvetica-Bold")

    elements = []

    # ── Header ────────────────────────────────────────────────────────────
    header_data = [
        [
            Paragraph("Health Care Expert Reviews", s_title),
            Paragraph("<b>INVOICE</b>", ParagraphStyle(
                "InvTag", parent=s_normal, fontSize=14,
                textColor=C_BRAND, alignment=TA_RIGHT,
                fontName="Helvetica-Bold",
            )),
        ],
        [
            Paragraph("AI-Powered Peer Review Service", s_subtitle),
            Paragraph(f"#{invoice_number}", ParagraphStyle(
                "InvNum", parent=s_normal, fontSize=10,
                textColor=C_GREY, alignment=TA_RIGHT,
            )),
        ],
    ]
    header_table = Table(header_data, colWidths=[10 * cm, 6 * cm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(HRFlowable(width="100%", thickness=1, color=C_BRAND))
    elements.append(Spacer(1, 0.6 * cm))

    # ── Invoice details (two-column) ──────────────────────────────────────
    details_left = [Paragraph("<b>Billed To</b>", s_bold)]
    if customer_email:
        details_left.append(Paragraph(customer_email, s_normal))

    details_data = [
        [
            Paragraph("<b>Billed To</b>", s_bold),
            Paragraph("<b>Invoice Details</b>", ParagraphStyle(
                "InvDetR", parent=s_bold, alignment=TA_RIGHT,
            )),
        ],
        [
            Paragraph(customer_email or "—", s_normal),
            Paragraph(f"Date: {pay_date.strftime('%d %b %Y')}", s_right),
        ],
        [
            Paragraph("", s_normal),
            Paragraph(f"Payment ID: {payment_id}", s_right),
        ],
        [
            Paragraph("", s_normal),
            Paragraph(f"Order ID: {order_id}", s_right),
        ],
    ]
    details_table = Table(details_data, colWidths=[8 * cm, 8 * cm])
    details_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(details_table)
    elements.append(Spacer(1, 0.8 * cm))

    # ── Line items table ──────────────────────────────────────────────────
    safe_title = manuscript_title[:70] if manuscript_title else "Manuscript Review"
    line_items = [
        [
            Paragraph("<b>Description</b>", s_bold),
            Paragraph("<b>Qty</b>", ParagraphStyle("q", parent=s_bold, alignment=TA_CENTER)),
            Paragraph("<b>Amount</b>", ParagraphStyle("a", parent=s_bold, alignment=TA_RIGHT)),
        ],
        [
            Paragraph(
                f"AI Peer Review Report<br/>"
                f"<font size=8 color='#555555'>Manuscript: {safe_title}</font>",
                s_normal,
            ),
            Paragraph("1", s_center),
            Paragraph(f"{currency_symbol}{amount_display}", s_right),
        ],
    ]
    items_table = Table(line_items, colWidths=[10.5 * cm, 2 * cm, 3.5 * cm])
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_LIGHT),
        ("TEXTCOLOR", (0, 0), (-1, 0), C_DARK),
        ("GRID", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 0.3 * cm))

    # ── Totals ────────────────────────────────────────────────────────────
    totals_data = [
        [
            "", "",
            Paragraph("<b>Subtotal</b>", s_right),
            Paragraph(f"{currency_symbol}{amount_display}", s_right),
        ],
        [
            "", "",
            Paragraph("<b>Tax</b>", s_right),
            Paragraph(f"{currency_symbol}0.00", s_right),
        ],
        [
            "", "",
            Paragraph(
                "<b>Total Paid</b>",
                ParagraphStyle("tp", parent=s_bold, alignment=TA_RIGHT, fontSize=12),
            ),
            Paragraph(
                f"<b>{currency_symbol}{amount_display}</b>",
                ParagraphStyle("tv", parent=s_bold, alignment=TA_RIGHT, fontSize=12, textColor=C_BRAND),
            ),
        ],
    ]
    totals_table = Table(totals_data, colWidths=[5 * cm, 3 * cm, 4 * cm, 4 * cm])
    totals_table.setStyle(TableStyle([
        ("LINEABOVE", (2, 0), (-1, 0), 0.5, C_BORDER),
        ("LINEABOVE", (2, 2), (-1, 2), 1, C_BRAND),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 10),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 1.2 * cm))

    # ── Payment status badge ──────────────────────────────────────────────
    badge_data = [[
        Paragraph(
            "\u2713  PAID",
            ParagraphStyle(
                "badge", parent=s_normal, fontSize=12,
                textColor=colors.HexColor("#006600"),
                fontName="Helvetica-Bold", alignment=TA_CENTER,
            ),
        )
    ]]
    badge_table = Table(badge_data, colWidths=[6 * cm])
    badge_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#e8f5e9")),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    badge_wrapper = Table([[badge_table]], colWidths=[16 * cm])
    badge_wrapper.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    elements.append(badge_wrapper)
    elements.append(Spacer(1, 1 * cm))

    # ── Footer ────────────────────────────────────────────────────────────
    elements.append(HRFlowable(width="100%", thickness=0.5, color=C_BORDER))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph(
        "This is a computer-generated invoice and does not require a signature.<br/>"
        "For questions, reply to the email this invoice was attached to.",
        s_small,
    ))
    elements.append(Spacer(1, 0.2 * cm))
    elements.append(Paragraph(
        "Health Care Expert Reviews \u2014 AI-Powered Peer Review Service",
        ParagraphStyle("foot", parent=s_small, alignment=TA_CENTER, textColor=C_BRAND),
    ))

    doc.build(elements)
    return buf.getvalue()
