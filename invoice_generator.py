"""
invoice_generator.py
Generate a standalone PDF invoice document after a successful payment.
"""

from datetime import datetime, timezone
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def _fmt_inr_from_paise(amount_paise: int) -> str:
    return f"₹{amount_paise / 100:.2f}"


def generate_invoice(review_id: str, invoice_data: dict) -> bytes:
    """
    Build an invoice PDF for a paid review.

    Required fields in invoice_data:
      invoice_id, payment_id, order_id, amount_paise, currency
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left = 18 * mm
    y = height - 20 * mm

    c.setTitle(f"Invoice_{invoice_data.get('invoice_id', review_id)}")
    c.setFont("Helvetica-Bold", 18)
    c.drawString(left, y, "INVOICE")
    y -= 10 * mm

    c.setFont("Helvetica", 11)
    c.drawString(left, y, "AI-Assisted Peer Review System")
    y -= 6 * mm
    c.drawString(left, y, "Service: Manuscript Peer Review Report")
    y -= 10 * mm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(left, y, "Billing Details")
    y -= 7 * mm
    c.setFont("Helvetica", 10)

    paid_at = invoice_data.get("paid_at_utc") or datetime.now(timezone.utc).isoformat()
    items = [
        ("Invoice ID", invoice_data.get("invoice_id", "")),
        ("Review ID", review_id),
        ("Order ID", invoice_data.get("order_id", "")),
        ("Payment ID", invoice_data.get("payment_id", "")),
        ("Paid At (UTC)", paid_at),
        ("Currency", invoice_data.get("currency", "INR")),
        ("Amount", _fmt_inr_from_paise(int(invoice_data.get("amount_paise", 0)))),
        ("Description", invoice_data.get("description", "Peer Review PDF Download")),
    ]
    for label, value in items:
        c.drawString(left, y, f"{label}: {value}")
        y -= 6 * mm

    y -= 4 * mm
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(left, y, "This is a system-generated invoice and does not require a signature.")

    c.showPage()
    c.save()
    return buffer.getvalue()
