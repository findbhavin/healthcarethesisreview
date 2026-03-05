"""
app.py
Flask web application for AI-assisted journal article peer review.
Deployable on GCP Cloud Run.

Routes
------
GET  /                          Serve the web UI (index.html)
GET  /health                    Health check (used by Cloud Run)
POST /review                    Submit a manuscript for AI peer review
GET  /download/<review_id>      Download the DOCX report
GET  /guidelines/metadata       Return current guidelines version/metadata
GET  /guidelines/journals       Return list of known journals
POST /guidelines/validate       Validate the current guidelines YAML
POST /admin/reload-guidelines   Hot-reload guidelines without restart
"""

import os
import uuid
import logging
import io

from flask import Flask, request, jsonify, send_file

from review_agent import run_review, extract_text
from report_generator import generate_report
from guidelines.guidelines_loader import (
    get_metadata,
    get_changelog,
    get_journal_list,
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
    Accept a manuscript file and run the 8-stage AI peer review.

    Form fields:
      file         : required — PDF, DOCX, or TXT
      journal_name : optional — target journal (e.g. NJCM, BMJ, PLOS ONE)
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

    try:
        file_bytes = file.read()
        logger.info(f"Received '{file.filename}' ({len(file_bytes):,} bytes) journal='{journal_name}'")

        result = run_review(file_bytes, file.filename, journal_name=journal_name)

        review_id = str(uuid.uuid4())
        _review_store[review_id] = result

        return jsonify({
            "review_id":        review_id,
            "manuscript_title": result["manuscript_title"],
            "decision":         result["decision"],
            "word_count":       result["word_count"],
            "review_text":      result["review_text"],
        }), 200

    except ValueError as e:
        logger.warning(f"Validation error: {e}")
        return jsonify({"error": str(e)}), 422
    except RuntimeError as e:
        logger.error(f"Runtime error: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.exception("Unexpected error during review")
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500


# ---------------------------------------------------------------------------
# Report download
# ---------------------------------------------------------------------------

@app.route("/download/<review_id>", methods=["GET"])
def download_report(review_id: str):
    """Download the DOCX peer review report for a completed review."""
    result = _review_store.get(review_id)
    if not result:
        return jsonify({
            "error": "Review not found or expired. Please run the review again."
        }), 404

    try:
        docx_bytes = generate_report(result)
        safe_title = (
            result.get("manuscript_title", "review")[:40]
            .replace(" ", "_")
            .replace("/", "-")
            .replace("\\", "-")
        )
        download_name = f"PeerReview_{safe_title}.docx"
        return send_file(
            io.BytesIO(docx_bytes),
            mimetype=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            as_attachment=True,
            download_name=download_name,
        )
    except Exception as e:
        logger.exception("Failed to generate DOCX report")
        return jsonify({"error": f"Report generation failed: {e}"}), 500


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
