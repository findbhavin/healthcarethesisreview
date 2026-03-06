"""
app.py
Flask web application for AI-assisted journal article peer review.
Deployable on GCP Cloud Run.

Routes
------
GET  /                          Serve the web UI (index.html)
GET  /guidelines-page           Serve the Guidelines page (guidelines.html)
GET  /health                    Health check (used by Cloud Run)
POST /review                    Stream 8-stage AI peer review via SSE (text/event-stream)
GET  /download/<review_id>      Download the PDF review report
POST /payment/create-order      Create a Razorpay payment order (₹100 per document)
POST /payment/verify            Verify Razorpay payment signature and mark review as paid
GET  /guidelines/full           Return complete guidelines data (for UI)
GET  /guidelines/metadata       Return current guidelines version/metadata
GET  /guidelines/journals       Return list of known journals
GET  /guidelines/changelog      Return guidelines changelog
POST /guidelines/validate       Validate the current guidelines YAML
POST /admin/reload-guidelines   Hot-reload guidelines without restart

Environment variables
---------------------
ANTHROPIC_API_KEY      : required — Anthropic API key
GCS_BUCKET             : optional — GCS bucket name for persistent PDF storage
RAZORPAY_KEY_ID        : optional — Razorpay API key (enables payment)
RAZORPAY_KEY_SECRET    : optional — Razorpay API secret (enables payment)
"""

import os
import uuid
import json
import logging
import io
import hmac
import hashlib

from flask import Flask, request, jsonify, send_file, Response, stream_with_context

from review_agent import run_review, extract_text, stream_review
from report_generator import generate_report
from gcs_uploader import upload_report
from guidelines.guidelines_loader import (
    get_metadata,
    get_changelog,
    get_journal_list,
    get_full_guidelines,
    validate_guidelines,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}

# In-memory review store (keyed by review_id).
# For multi-instance Cloud Run, replace with Cloud Storage or Firestore.
_review_store: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Payment configuration (Razorpay)
# ---------------------------------------------------------------------------
# Razorpay is an open-source-friendly payment gateway supporting INR and
# international currencies (USD, EUR, GBP, AED, SGD, etc.).
# Docs: https://razorpay.com/docs/
RAZORPAY_KEY_ID     = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# Payment amount in paise (100 INR = 10000 paise)
PAYMENT_AMOUNT_PAISE   = 10_000  # ₹100
PAYMENT_CURRENCY       = "INR"
PAYMENT_DESCRIPTION    = "Peer Review Report Download — ₹100 per document"

PAYMENT_ENABLED = bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


@app.route("/guidelines-page", methods=["GET"])
def guidelines_page():
    html_path = os.path.join(os.path.dirname(__file__), "guidelines.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


@app.route("/guidelines/full", methods=["GET"])
def guidelines_full():
    """Return the complete structured guidelines for UI rendering."""
    try:
        return jsonify(get_full_guidelines()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Core review endpoint
# ---------------------------------------------------------------------------

@app.route("/review", methods=["POST"])
def review():
    """
    Accept a manuscript file and stream the 8-stage AI peer review via SSE.

    Form fields:
      file         : required — PDF, DOCX, or TXT
      journal_name : optional — target journal (e.g. NJCM, BMJ, PLOS ONE)
      article_type : optional — e.g. "original research", "RCT", "systematic review"
      journal_tier : optional — e.g. "high-impact", "mid-tier specialist"

    Response: text/event-stream with JSON lines:
      data: {"type":"chunk","text":"..."}
      data: {"type":"done","review_id":"...","manuscript_title":"...","decision":"...","word_count":N}
      data: {"type":"error","error":"..."}
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded. Please attach your manuscript."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected."}), 400
    if not allowed_file(file.filename):
        return jsonify({
            "error": "Unsupported file type. Please upload PDF, DOCX, or TXT."
        }), 400

    journal_name = request.form.get("journal_name", "").strip()
    article_type = request.form.get("article_type", "").strip()
    journal_tier = request.form.get("journal_tier", "").strip()
    file_bytes = file.read()
    filename = file.filename
    logger.info(
        f"Received '{filename}' ({len(file_bytes):,} bytes) "
        f"journal='{journal_name}' article_type='{article_type}' tier='{journal_tier}'"
    )

    def generate():
        review_id = str(uuid.uuid4())
        for event in stream_review(
            file_bytes, filename,
            journal_name=journal_name,
            article_type=article_type,
            journal_tier=journal_tier,
        ):
            if event["type"] == "done":
                result = event["result"]
                _review_store[review_id] = result

                # Generate PDF and upload to GCS for persistent offline access
                gcs_url = None
                try:
                    pdf_bytes = generate_report(result)
                    gcs_url = upload_report(review_id, pdf_bytes, result["manuscript_title"])
                except Exception as exc:
                    logger.warning(f"PDF/GCS step failed (non-fatal): {exc}")

                payload = {
                    "type": "done",
                    "review_id": review_id,
                    "manuscript_title": result["manuscript_title"],
                    "decision": result["decision"],
                    "word_count": result["word_count"],
                    "weighted_score": result.get("weighted_score"),
                    "stage_scores": result.get("stage_scores", {}),
                    "wrs_parts": result.get("wrs_parts", ""),
                }
                if gcs_url:
                    payload["gcs_url"] = gcs_url
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                yield f"data: {json.dumps(event)}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Report download
# ---------------------------------------------------------------------------

@app.route("/download/<review_id>", methods=["GET"])
def download_report(review_id: str):
    """Download the PDF peer review report for a completed review."""
    result = _review_store.get(review_id)
    if not result:
        return jsonify({
            "error": "Review not found or expired. Please run the review again."
        }), 404

    try:
        pdf_bytes = generate_report(result)
        safe_title = (
            result.get("manuscript_title", "review")[:40]
            .replace(" ", "_")
            .replace("/", "-")
            .replace("\\", "-")
        )
        download_name = f"PeerReview_{safe_title}.pdf"
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        logger.exception("Failed to generate PDF report")
        return jsonify({"error": f"Report generation failed: {e}"}), 500


# ---------------------------------------------------------------------------
# Payment endpoints (Razorpay)
# ---------------------------------------------------------------------------

@app.route("/payment/config", methods=["GET"])
def payment_config():
    """Return public payment configuration to the frontend."""
    return jsonify({
        "enabled": PAYMENT_ENABLED,
        "key_id": RAZORPAY_KEY_ID if PAYMENT_ENABLED else "",
        "amount": PAYMENT_AMOUNT_PAISE,
        "currency": PAYMENT_CURRENCY,
        "description": PAYMENT_DESCRIPTION,
        "amount_display": "₹100",
    })


@app.route("/payment/create-order", methods=["POST"])
def payment_create_order():
    """
    Create a Razorpay order for downloading a review PDF.

    JSON body: {"review_id": "..."}
    Returns: {"order_id": "...", "amount": 10000, "currency": "INR", "key_id": "..."}
    """
    if not PAYMENT_ENABLED:
        return jsonify({"error": "Payment gateway not configured."}), 503

    data = request.get_json(silent=True) or {}
    review_id = data.get("review_id", "")
    if not review_id or review_id not in _review_store:
        return jsonify({"error": "Invalid or expired review ID."}), 400

    try:
        import razorpay
        client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        order = client.order.create({
            "amount": PAYMENT_AMOUNT_PAISE,
            "currency": PAYMENT_CURRENCY,
            "receipt": f"review_{review_id[:16]}",
            "notes": {
                "review_id": review_id,
                "product": "Peer Review PDF Download",
            },
        })
        logger.info(f"Razorpay order created: {order['id']} for review {review_id}")
        return jsonify({
            "order_id": order["id"],
            "amount": PAYMENT_AMOUNT_PAISE,
            "currency": PAYMENT_CURRENCY,
            "key_id": RAZORPAY_KEY_ID,
            "review_id": review_id,
        })
    except Exception as e:
        logger.exception("Failed to create Razorpay order")
        return jsonify({"error": f"Payment order creation failed: {e}"}), 500


@app.route("/payment/verify", methods=["POST"])
def payment_verify():
    """
    Verify Razorpay payment signature and mark the review as paid.

    JSON body:
      {
        "razorpay_order_id": "...",
        "razorpay_payment_id": "...",
        "razorpay_signature": "...",
        "review_id": "..."
      }
    Returns: {"verified": true, "review_id": "..."}
    """
    if not PAYMENT_ENABLED:
        return jsonify({"error": "Payment gateway not configured."}), 503

    data = request.get_json(silent=True) or {}
    order_id   = data.get("razorpay_order_id", "")
    payment_id = data.get("razorpay_payment_id", "")
    signature  = data.get("razorpay_signature", "")
    review_id  = data.get("review_id", "")

    if not all([order_id, payment_id, signature, review_id]):
        return jsonify({"error": "Missing payment verification fields."}), 400

    # Verify HMAC-SHA256 signature: key=secret, msg=order_id|payment_id
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        logger.warning(f"Payment signature mismatch for order {order_id}")
        return jsonify({"error": "Payment verification failed — signature mismatch."}), 400

    # Mark review as paid
    if review_id in _review_store:
        _review_store[review_id]["payment_verified"] = True
        _review_store[review_id]["payment_id"] = payment_id
        logger.info(f"Payment verified for review {review_id}, payment {payment_id}")

    return jsonify({"verified": True, "review_id": review_id})


# ---------------------------------------------------------------------------
# Guidelines admin endpoints (no auth needed for read; add auth for write)
# ---------------------------------------------------------------------------

@app.route("/guidelines/metadata", methods=["GET"])
def guidelines_metadata():
    """Return the current version and metadata of the review guidelines."""
    try:
        return jsonify(get_metadata()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/guidelines/changelog", methods=["GET"])
def guidelines_changelog():
    """Return the guidelines changelog."""
    try:
        return jsonify({"changelog": get_changelog()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/guidelines/journals", methods=["GET"])
def guidelines_journals():
    """Return the list of journals with specific override configurations."""
    try:
        return jsonify({"journals": get_journal_list()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/guidelines/validate", methods=["POST"])
def guidelines_validate():
    """Validate the current review_guidelines.yaml structure."""
    try:
        result = validate_guidelines()
        status = 200 if result["valid"] else 422
        return jsonify(result), status
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/reload-guidelines", methods=["POST"])
def reload_guidelines():
    """
    Hot-reload the guidelines YAML without restarting the app.
    Validates first; rejects if the YAML is invalid.
    (Add authentication middleware before using in production.)
    """
    result = validate_guidelines()
    if not result["valid"]:
        return jsonify({
            "reloaded": False,
            "errors": result.get("errors", [])
        }), 422
    return jsonify({
        "reloaded": True,
        "message": "Guidelines will be applied to the next review request.",
        "version": result.get("version"),
    }), 200


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(413)
def request_too_large(e):
    return jsonify({"error": "File is too large. Maximum allowed size is 50 MB."}), 413


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found."}), 404


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logger.info(f"Starting server on port {port} (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)
