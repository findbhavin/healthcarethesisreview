"""
app.py
Flask web application for AI-assisted journal article peer review.
Deploy on GCP Cloud Run.
"""

import os
import uuid
import logging
from flask import Flask, request, jsonify, send_file, render_template_string
import io

from review_agent import run_review
from report_generator import generate_report

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB max upload

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# In-memory store for review results (keyed by review_id).
# For production, replace with Cloud Storage or Redis.
_review_store: dict[str, dict] = {}


@app.route("/", methods=["GET"])
def index():
    with open(os.path.join(os.path.dirname(__file__), "index.html"), "r") as f:
        return f.read()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/review", methods=["POST"])
def review():
    """
    Accepts a multipart/form-data POST with:
      - file: the manuscript file (PDF, DOCX, or TXT)
      - journal_name: (optional) name of the target journal
    Returns JSON with review_id, decision, and full review_text.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded. Please attach your manuscript."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    if not allowed_file(file.filename):
        return jsonify(
            {"error": "Unsupported file type. Please upload a PDF, DOCX, or TXT file."}
        ), 400

    journal_name = request.form.get("journal_name", "").strip()

    try:
        file_bytes = file.read()
        logger.info(f"Processing manuscript: {file.filename} ({len(file_bytes)} bytes)")

        result = run_review(file_bytes, file.filename, journal_name=journal_name)

        review_id = str(uuid.uuid4())
        _review_store[review_id] = result

        return jsonify(
            {
                "review_id": review_id,
                "manuscript_title": result["manuscript_title"],
                "decision": result["decision"],
                "word_count": result["word_count"],
                "review_text": result["review_text"],
            }
        ), 200

    except ValueError as e:
        logger.warning(f"Validation error: {e}")
        return jsonify({"error": str(e)}), 422
    except RuntimeError as e:
        logger.error(f"Runtime error: {e}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logger.exception("Unexpected error during review")
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500


@app.route("/download/<review_id>", methods=["GET"])
def download_report(review_id: str):
    """
    Download the DOCX report for a completed review.
    """
    result = _review_store.get(review_id)
    if not result:
        return jsonify({"error": "Review not found. Please run the review again."}), 404

    try:
        docx_bytes = generate_report(result)
        safe_title = (
            result.get("manuscript_title", "review")[:40]
            .replace(" ", "_")
            .replace("/", "-")
            .replace("\\", "-")
        )
        filename = f"PeerReview_{safe_title}.docx"

        return send_file(
            io.BytesIO(docx_bytes),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        logger.exception("Failed to generate report")
        return jsonify({"error": f"Report generation failed: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
