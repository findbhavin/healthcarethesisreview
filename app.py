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
POST /payment/create-order      Create a Razorpay payment order (₹50 per document)
POST /payment/verify            Verify Razorpay payment signature and mark review as paid
GET  /guidelines/full           Return complete guidelines data (for UI)
GET  /guidelines/metadata       Return current guidelines version/metadata
GET  /guidelines/journals       Return list of known journals
GET  /guidelines/changelog      Return guidelines changelog
POST /guidelines/validate       Validate the current guidelines YAML
POST /admin/reload-guidelines   Hot-reload guidelines without restart

Environment variables
---------------------
AI_PROVIDER            : optional — default provider ("gemini" or "anthropic")
AI_MODEL               : optional — default model name for selected provider
GEMINI_API_KEY         : optional — Gemini API key (required when provider=gemini)
ANTHROPIC_API_KEY      : optional — Anthropic API key (required when provider=anthropic)
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
import secrets
from functools import wraps

from flask import Flask, request, jsonify, send_file, Response, stream_with_context, session

from review_agent import run_review, extract_text, stream_review, generate_text
from report_generator import generate_report
from gcs_uploader import (
    upload_report,
    push_rule_version,
    list_rule_versions,
    get_rule_version,
    revert_rule_version,
)
from guidelines.guidelines_loader import (
    get_metadata,
    get_changelog,
    get_journal_list,
    get_full_guidelines,
    validate_guidelines,
    get_guidelines_raw,
    save_guidelines_yaml,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
# Secret key: override with FLASK_SECRET_KEY env var in production
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

# ---------------------------------------------------------------------------
# Admin credentials (stored in admin_config.json, editable at runtime)
# ---------------------------------------------------------------------------
_ADMIN_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "admin_config.json")


def _load_admin_config() -> dict:
    default_config = {
        "username": "admin",
        "password": "prakash",
        "ai": {
            "provider": "gemini",
            "model": "gemini-2.5-pro",
            "gemini_api_key": "",
            "anthropic_api_key": "",
        },
    }
    if os.path.exists(_ADMIN_CONFIG_PATH):
        with open(_ADMIN_CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        loaded.setdefault("username", default_config["username"])
        loaded.setdefault("password", default_config["password"])
        loaded.setdefault("ai", {})
        loaded["ai"].setdefault("provider", default_config["ai"]["provider"])
        loaded["ai"].setdefault("model", default_config["ai"]["model"])
        loaded["ai"].setdefault("gemini_api_key", "")
        loaded["ai"].setdefault("anthropic_api_key", "")
        return loaded
    return default_config


def _save_admin_config(config: dict) -> None:
    with open(_ADMIN_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def _resolve_ai_config() -> dict:
    cfg = _load_admin_config().get("ai", {})
    provider = (cfg.get("provider") or os.environ.get("AI_PROVIDER") or "gemini").strip().lower()
    model = (cfg.get("model") or os.environ.get("AI_MODEL") or "").strip()
    if not model:
        model = "gemini-2.5-pro" if provider == "gemini" else "claude-sonnet-4-6"

    return {
        "provider": provider,
        "model": model,
        "gemini_api_key": (cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip(),
        "anthropic_api_key": (cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or "").strip(),
    }


def _admin_required(f):
    """Decorator: require admin session on API routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return jsonify({"error": "Authentication required.", "auth": False}), 401
        return f(*args, **kwargs)
    return decorated

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

# Payment amount in paise (50 INR = 5000 paise)
PAYMENT_AMOUNT_PAISE   = 5_000  # ₹50
PAYMENT_CURRENCY       = "INR"
PAYMENT_DESCRIPTION    = "Peer Review Report Download — ₹50 per document"

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
    ai_config = _resolve_ai_config()

    # Assign review_id BEFORE the generator so the client can use it
    # to resume/poll if the SSE connection drops mid-stream (e.g. phone lock).
    review_id = str(uuid.uuid4())
    _review_store[review_id] = {
        "status": "running",
        "accumulated_text": "",
        "filename": filename,
    }

    def generate():
        for event in stream_review(
            file_bytes, filename,
            journal_name=journal_name,
            article_type=article_type,
            journal_tier=journal_tier,
            ai_config=ai_config,
        ):
            if event["type"] == "chunk":
                # Accumulate text so poll endpoint can return partial progress
                _review_store[review_id]["accumulated_text"] += event["text"]
                yield f"data: {json.dumps(event)}\n\n"

            elif event["type"] == "done":
                result = event["result"]
                result["status"] = "done"
                _review_store[review_id].update(result)

                # Generate PDF and upload to GCS for persistent offline access
                gcs_url = None
                try:
                    pdf_bytes = generate_report(result)
                    gcs_url = upload_report(review_id, pdf_bytes, result["manuscript_title"])
                    _review_store[review_id]["gcs_url"] = gcs_url
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
            # review_id in header lets the client store it before any body
            # arrives — used for reconnect/poll if the stream drops (phone lock)
            "X-Review-Id": review_id,
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
# Review poll endpoint — for reconnecting after network drops
# ---------------------------------------------------------------------------

@app.route("/review/<review_id>/poll", methods=["GET"])
def poll_review(review_id: str):
    """
    Poll the status of a review by ID.  Used by the frontend to resume
    after a network disconnect (e.g. phone screen lock).

    Returns:
      {status: "running", accumulated_text: "..."}   — review still in progress
      {status: "done", review_id, manuscript_title, decision, word_count,
       weighted_score, stage_scores, wrs_parts, gcs_url?}  — complete
      {status: "not_found"}  — unknown ID (expired or never started)
    """
    entry = _review_store.get(review_id)
    if not entry:
        return jsonify({"status": "not_found"}), 404

    status = entry.get("status", "running")

    if status == "done":
        payload = {
            "status": "done",
            "review_id": review_id,
            "manuscript_title": entry.get("manuscript_title", ""),
            "decision": entry.get("decision", ""),
            "word_count": entry.get("word_count", 0),
            "weighted_score": entry.get("weighted_score"),
            "stage_scores": entry.get("stage_scores", {}),
            "wrs_parts": entry.get("wrs_parts", ""),
        }
        if entry.get("gcs_url"):
            payload["gcs_url"] = entry["gcs_url"]
        return jsonify(payload), 200

    # Still running — return accumulated text so far
    return jsonify({
        "status": "running",
        "accumulated_text": entry.get("accumulated_text", ""),
    }), 200


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
        "amount_display": "₹50",
    })


@app.route("/payment/create-order", methods=["POST"])
def payment_create_order():
    """
    Create a Razorpay order for downloading a review PDF.

    JSON body: {"review_id": "..."}
    Returns: {"order_id": "...", "amount": 5000, "currency": "INR", "key_id": "..."}
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
# Test payment page (sandbox only — for manual QA)
# ---------------------------------------------------------------------------

def _render_payment_test_page():
    """
    Sandbox test page for manually verifying the Razorpay payment flow.
    Creates a dummy review entry so a real payment order can be created.
    Shows Razorpay test card numbers for convenience.
    """
    test_review_id = "test-" + str(uuid.uuid4())[:8]
    _review_store[test_review_id] = {
        "status": "done",
        "manuscript_title": "Test Manuscript — Payment QA",
        "decision": "Major Revision Required",
        "word_count": 3500,
        "review_text": "This is a test review entry for payment QA.",
        "filename": "test_manuscript.pdf",
    }

    key_id   = RAZORPAY_KEY_ID if PAYMENT_ENABLED else ""
    amount   = PAYMENT_AMOUNT_PAISE
    currency = PAYMENT_CURRENCY
    enabled  = PAYMENT_ENABLED

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Payment Gateway Test — Sandbox</title>
<style>
  body {{
    font-family: system-ui, sans-serif;
    background: #0f1117; color: #e8eaf0;
    max-width: 640px; margin: 40px auto; padding: 24px;
  }}
  h1 {{ color: #00c9b1; }}
  .card {{
    background: #181c27; border: 1px solid #2a3050;
    border-radius: 12px; padding: 24px; margin: 16px 0;
  }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 8px 12px; border: 1px solid #2a3050; font-size: 14px; }}
  td:first-child {{ color: #7a8aa8; width: 40%; }}
  .btn {{
    display: block; width: 100%;
    background: #00c9b1; color: #0f1117;
    border: none; border-radius: 8px;
    padding: 14px; font-size: 16px; font-weight: 700;
    cursor: pointer; margin-top: 16px;
  }}
  .btn:disabled {{ background: #2a3050; color: #7a8aa8; cursor: not-allowed; }}
  .warn {{ background: #2a1010; border-color: #ff5c6a; color: #ff5c6a; border-radius: 8px; padding: 12px; }}
  .ok  {{ background: #0a2010; border-color: #3dd68c; color: #3dd68c; border-radius: 8px; padding: 12px; }}
  pre  {{ background: #242840; padding: 12px; border-radius: 8px; font-size: 12px; overflow: auto; }}
  .tag {{ background: #00c9b1; color: #0f1117; border-radius: 4px; padding: 2px 8px; font-weight: 700; font-size: 12px; }}
</style>
</head>
<body>
<h1>&#128296; Payment Gateway Test</h1>
<p style="color:#7a8aa8">Sandbox test page — no real money is charged.</p>

{"<div class='warn'>&#9888; Payment gateway is <b>NOT configured</b>. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET environment variables.</div>" if not enabled else ""}
{"<div class='ok'>&#10003; Payment gateway is <b>active</b>. Using test keys.</div>" if enabled else ""}

<div class="card">
  <h3 style="margin-top:0; color:#00c9b1">Test Details</h3>
  <table>
    <tr><td>Review ID</td><td><code>{test_review_id}</code></td></tr>
    <tr><td>Amount</td><td><b>&#8377;50</b> (5,000 paise)</td></tr>
    <tr><td>Currency</td><td>{currency}</td></tr>
    <tr><td>Key ID</td><td><code>{key_id or "not set"}</code></td></tr>
  </table>
  <button class="btn" id="payBtn" {'disabled' if not enabled else ''} onclick="startTestPayment()">
    &#128179; Pay &#8377;50 — Test Payment
  </button>
</div>

<div class="card">
  <h3 style="margin-top:0; color:#00c9b1">Razorpay Test Cards</h3>
  <table>
    <tr><td>Card Number</td><td><code>4111 1111 1111 1111</code> <span class="tag">Visa</span></td></tr>
    <tr><td>Expiry</td><td>Any future date (e.g. 12/26)</td></tr>
    <tr><td>CVV</td><td>Any 3 digits (e.g. 123)</td></tr>
    <tr><td>OTP</td><td><code>1234</code></td></tr>
  </table>
  <br>
  <table>
    <tr><td>UPI</td><td><code>success@razorpay</code> (always succeeds)</td></tr>
    <tr><td>UPI Failure</td><td><code>failure@razorpay</code> (always fails)</td></tr>
  </table>
</div>

<div class="card">
  <h3 style="margin-top:0; color:#00c9b1">Payment Flow</h3>
  <ol style="color:#7a8aa8; line-height:1.8">
    <li>Click the button above &#8594; creates a Razorpay order via <code>POST /payment/create-order</code></li>
    <li>Razorpay Checkout popup opens &#8594; enter test card / UPI details</li>
    <li>On success &#8594; calls <code>POST /payment/verify</code> with HMAC signature</li>
    <li>On verification &#8594; shows success message below</li>
  </ol>
</div>

<div id="result" style="display:none" class="card"></div>

<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
async function startTestPayment() {{
  document.getElementById('payBtn').disabled = true;
  document.getElementById('payBtn').textContent = 'Creating order...';
  const result = document.getElementById('result');
  result.style.display = 'none';

  try {{
    const orderResp = await fetch('/payment/create-order', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{review_id: '{test_review_id}'}})
    }});
    const order = await orderResp.json();
    if (!orderResp.ok) throw new Error(order.error || 'Order creation failed');

    const options = {{
      key: order.key_id,
      amount: order.amount,
      currency: order.currency,
      name: 'MedScript Reviewer',
      description: 'Test Payment — Peer Review PDF Download',
      order_id: order.order_id,
      handler: async function(response) {{
        const verifyResp = await fetch('/payment/verify', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{
            razorpay_order_id:  response.razorpay_order_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature,
            review_id: '{test_review_id}'
          }})
        }});
        const vData = await verifyResp.json();
        result.style.display = 'block';
        if (vData.verified) {{
          result.className = 'card ok';
          result.innerHTML = '<h3 style="margin-top:0">&#10003; Payment Verified Successfully!</h3>' +
            '<pre>' + JSON.stringify({{payment_id: response.razorpay_payment_id, order_id: response.razorpay_order_id}}, null, 2) + '</pre>';
        }} else {{
          result.className = 'card warn';
          result.innerHTML = '<b>Verification failed:</b> ' + (vData.error || 'Unknown error');
        }}
      }},
      modal: {{ ondismiss: function() {{
        document.getElementById('payBtn').disabled = false;
        document.getElementById('payBtn').textContent = 'Pay &#8377;50 — Test Payment';
      }} }},
      theme: {{ color: '#00c9b1' }}
    }};
    new Razorpay(options).open();
  }} catch(e) {{
    result.style.display = 'block';
    result.className = 'card warn';
    result.innerHTML = '<b>Error:</b> ' + e.message;
    document.getElementById('payBtn').disabled = false;
    document.getElementById('payBtn').textContent = 'Pay &#8377;50 — Test Payment';
  }}
}}
</script>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/payment/test", methods=["GET"])
def payment_test_page():
    """Backward-compatible public payment test page."""
    return _render_payment_test_page()


@app.route("/admin/payment-test", methods=["GET"])
@_admin_required
def admin_payment_test_page():
    """Admin-only payment test page for Razorpay sandbox QA."""
    return _render_payment_test_page()


# ---------------------------------------------------------------------------
# Admin UI + authenticated admin API
# ---------------------------------------------------------------------------

@app.route("/admin", methods=["GET"])
def admin_page():
    html_path = os.path.join(os.path.dirname(__file__), "admin.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/admin/check-auth", methods=["GET"])
def admin_check_auth():
    """Return 200 if admin is logged in, 401 otherwise."""
    if session.get("admin_authenticated"):
        return jsonify({"authenticated": True, "username": session.get("admin_username")}), 200
    return jsonify({"authenticated": False}), 401


@app.route("/admin/login", methods=["POST"])
def admin_login():
    """
    Authenticate an admin user.
    JSON body: {"username": "...", "password": "..."}
    """
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    cfg = _load_admin_config()
    if username == cfg.get("username") and password == cfg.get("password"):
        session["admin_authenticated"] = True
        session["admin_username"] = username
        logger.info(f"Admin login: {username}")
        return jsonify({"authenticated": True, "username": username}), 200

    logger.warning(f"Failed admin login attempt for username '{username}'")
    return jsonify({"error": "Invalid username or password."}), 401


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify({"logged_out": True}), 200


@app.route("/admin/credentials", methods=["POST"])
@_admin_required
def admin_change_credentials():
    """
    Change admin username and/or password.
    JSON body: {"username": "...", "password": "...", "confirm_password": "..."}
    """
    data = request.get_json(silent=True) or {}
    new_username = data.get("username", "").strip()
    new_password = data.get("password", "")
    confirm      = data.get("confirm_password", "")

    if not new_username:
        return jsonify({"error": "Username cannot be empty."}), 400
    if not new_password:
        return jsonify({"error": "Password cannot be empty."}), 400
    if new_password != confirm:
        return jsonify({"error": "Passwords do not match."}), 400
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400

    cfg = _load_admin_config()
    cfg["username"] = new_username
    cfg["password"] = new_password
    _save_admin_config(cfg)
    session["admin_username"] = new_username
    logger.info(f"Admin credentials updated to username '{new_username}'")
    return jsonify({"updated": True, "username": new_username}), 200


@app.route("/admin/ai-config", methods=["GET"])
@_admin_required
def admin_get_ai_config():
    """Get current AI provider/model settings (keys are masked)."""
    cfg = _resolve_ai_config()
    return jsonify({
        "provider": cfg["provider"],
        "model": cfg["model"],
        "has_gemini_api_key": bool(cfg["gemini_api_key"]),
        "has_anthropic_api_key": bool(cfg["anthropic_api_key"]),
    }), 200


@app.route("/admin/ai-config", methods=["POST"])
@_admin_required
def admin_set_ai_config():
    """Update AI provider/model/key settings."""
    data = request.get_json(silent=True) or {}
    provider = (data.get("provider") or "gemini").strip().lower()
    model = (data.get("model") or "").strip()
    gemini_api_key = (data.get("gemini_api_key") or "").strip()
    anthropic_api_key = (data.get("anthropic_api_key") or "").strip()

    if provider not in ("gemini", "anthropic"):
        return jsonify({"error": "provider must be 'gemini' or 'anthropic'."}), 400
    if not model:
        model = "gemini-2.5-pro" if provider == "gemini" else "claude-sonnet-4-6"

    cfg = _load_admin_config()
    cfg.setdefault("ai", {})
    cfg["ai"]["provider"] = provider
    cfg["ai"]["model"] = model
    if "gemini_api_key" in data:
        cfg["ai"]["gemini_api_key"] = gemini_api_key
    if "anthropic_api_key" in data:
        cfg["ai"]["anthropic_api_key"] = anthropic_api_key
    _save_admin_config(cfg)

    return jsonify({
        "updated": True,
        "provider": provider,
        "model": model,
        "has_gemini_api_key": bool(cfg["ai"].get("gemini_api_key")),
        "has_anthropic_api_key": bool(cfg["ai"].get("anthropic_api_key")),
    }), 200


# ---------------------------------------------------------------------------
# Guidelines public endpoints (read-only, no auth needed)
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
# Admin guidelines CRUD (disk + GCS versioning)
# ---------------------------------------------------------------------------

@app.route("/admin/guidelines/raw", methods=["GET"])
@_admin_required
def admin_guidelines_raw():
    """Return the raw YAML text of the active guidelines (GCS or disk)."""
    try:
        return jsonify({"yaml": get_guidelines_raw()}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/guidelines/save", methods=["POST"])
@_admin_required
def admin_guidelines_save():
    """
    Validate and save a new guidelines YAML to disk.
    JSON body: {"yaml": "...raw yaml text..."}
    Optionally auto-pushes to GCS if GCS_BUCKET is configured.
    """
    data = request.get_json(silent=True) or {}
    raw_yaml = data.get("yaml", "")
    if not raw_yaml.strip():
        return jsonify({"saved": False, "errors": ["Empty YAML provided."]}), 400

    result = save_guidelines_yaml(raw_yaml)
    if result.get("saved"):
        logger.info("Admin guidelines saved to disk.")
        # Auto-push to GCS if configured
        try:
            import yaml as _yaml
            meta = (_yaml.safe_load(raw_yaml) or {}).get("metadata", {})
            version = str(meta.get("version", "unknown"))
            author  = session.get("admin_username", "admin")
            gcs_res = push_rule_version(raw_yaml, version, author)
            result["gcs"] = gcs_res
        except Exception as exc:
            result["gcs"] = {"success": False, "error": str(exc)}
    return jsonify(result), 200 if result.get("saved") else 422


# ---------------------------------------------------------------------------
# Admin NLP-based rule update (uses Claude to interpret natural language)
# ---------------------------------------------------------------------------

@app.route("/admin/guidelines/nlp-update", methods=["POST"])
@_admin_required
def admin_guidelines_nlp_update():
    """
    Accept a natural-language description of a desired rule change.
    Claude interprets the request and returns a proposed updated YAML.

    JSON body: {"request": "Add a check for patient consent forms in Stage 7"}
    Returns:   {"proposed_yaml": "...", "summary": "...", "diff_hint": "..."}
    """
    data = request.get_json(silent=True) or {}
    nlp_request = (data.get("request") or "").strip()
    if not nlp_request:
        return jsonify({"error": "No update request provided."}), 400

    try:
        current_yaml = get_guidelines_raw()
    except Exception as e:
        return jsonify({"error": f"Could not load current guidelines: {e}"}), 500

    system_prompt = (
        "You are an expert medical journal editor and YAML editor. "
        "You will be given the current review guidelines YAML and a plain-English "
        "description of a desired change. "
        "Respond with:\n"
        "1. A <summary> block: one sentence describing what you changed.\n"
        "2. A <yaml> block: the complete updated YAML (valid YAML, same structure, "
        "   version number incremented by 0.1, today's date in last_updated, "
        "   a new changelog entry added).\n"
        "Output ONLY those two XML-style blocks, nothing else.\n"
        "Example:\n"
        "<summary>Added a check for patient consent forms to Stage 7.</summary>\n"
        "<yaml>\n...full yaml...\n</yaml>"
    )
    user_msg = (
        f"CURRENT GUIDELINES YAML:\n```yaml\n{current_yaml}\n```\n\n"
        f"REQUESTED CHANGE:\n{nlp_request}"
    )

    try:
        raw_response = generate_text(system_prompt, user_msg, ai_config=_resolve_ai_config())
    except Exception as e:
        logger.exception("NLP guideline update AI call failed")
        return jsonify({"error": f"AI call failed: {e}"}), 500

    # Parse <summary> and <yaml> blocks from response
    import re as _re
    summary_match = _re.search(r"<summary>(.*?)</summary>", raw_response,
                               _re.DOTALL | _re.IGNORECASE)
    yaml_match    = _re.search(r"<yaml>(.*?)</yaml>", raw_response,
                               _re.DOTALL | _re.IGNORECASE)

    if not yaml_match:
        return jsonify({
            "error": "AI response did not contain a valid <yaml> block.",
            "raw_response": raw_response[:2000],
        }), 500

    proposed_yaml = yaml_match.group(1).strip()
    summary       = summary_match.group(1).strip() if summary_match else "See proposed YAML."

    # Quick structural validation of the proposed YAML
    try:
        import yaml as _yaml
        _yaml.safe_load(proposed_yaml)
    except Exception as e:
        return jsonify({
            "error": f"AI-generated YAML is invalid: {e}",
            "proposed_yaml": proposed_yaml,
        }), 422

    logger.info(f"NLP guideline update generated. Summary: {summary}")
    return jsonify({
        "proposed_yaml": proposed_yaml,
        "summary": summary,
    }), 200


# ---------------------------------------------------------------------------
# Admin GCS rule versioning endpoints
# ---------------------------------------------------------------------------

@app.route("/admin/guidelines/versions", methods=["GET"])
@_admin_required
def admin_guidelines_versions():
    """List all rule versions stored in GCS."""
    versions = list_rule_versions()
    return jsonify({"versions": versions, "gcs_configured": bool(os.environ.get("GCS_BUCKET"))}), 200


@app.route("/admin/guidelines/push-to-gcs", methods=["POST"])
@_admin_required
def admin_guidelines_push_gcs():
    """
    Push the current disk YAML to GCS as a new versioned snapshot.
    JSON body (optional): {"version": "2.1", "author": "Dr. Smith"}
    """
    data = request.get_json(silent=True) or {}
    author = data.get("author") or session.get("admin_username", "admin")
    try:
        raw_yaml = get_guidelines_raw()
        import yaml as _yaml
        meta    = (_yaml.safe_load(raw_yaml) or {}).get("metadata", {})
        version = data.get("version") or str(meta.get("version", "unknown"))
        result  = push_rule_version(raw_yaml, version, author)
        return jsonify(result), 200 if result.get("success") else 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/admin/guidelines/revert", methods=["POST"])
@_admin_required
def admin_guidelines_revert():
    """
    Revert to a previously stored GCS rule version and sync it to disk.
    JSON body: {"filename": "v2.0.yaml"}
    """
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    if not filename:
        return jsonify({"success": False, "error": "filename is required."}), 400

    result = revert_rule_version(filename)
    if result.get("success"):
        # Also sync the reverted content back to disk
        yaml_content = get_rule_version(filename)
        if yaml_content:
            save_result = save_guidelines_yaml(yaml_content)
            result["disk_synced"] = save_result.get("saved", False)
        logger.info(f"Admin reverted guidelines to {filename}")
    return jsonify(result), 200 if result.get("success") else 500


@app.route("/admin/guidelines/version/<filename>", methods=["GET"])
@_admin_required
def admin_guidelines_get_version(filename: str):
    """Download a specific rule version YAML from GCS."""
    yaml_content = get_rule_version(filename)
    if yaml_content is None:
        return jsonify({"error": f"{filename} not found."}), 404
    return jsonify({"yaml": yaml_content, "filename": filename}), 200


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
