"""
gcs_uploader.py
Uploads peer review PDF reports to Google Cloud Storage for persistent
offline access. The bucket name is read from the GCS_BUCKET environment
variable. If not set, uploading is silently skipped.
"""

import os
import logging
from datetime import date

logger = logging.getLogger(__name__)

GCS_BUCKET = os.environ.get("GCS_BUCKET", "")


def upload_report(review_id: str, pdf_bytes: bytes, manuscript_title: str) -> str | None:
    """
    Upload pdf_bytes to GCS and return a signed URL (valid 7 days).
    Returns None if GCS_BUCKET is not configured or upload fails.
    """
    if not GCS_BUCKET:
        logger.info("GCS_BUCKET not set — skipping cloud storage upload.")
        return None

    try:
        from google.cloud import storage
        from datetime import timedelta

        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)

        safe_title = (
            (manuscript_title or "review")[:40]
            .replace(" ", "_")
            .replace("/", "-")
            .replace("\\", "-")
        )
        today = date.today().strftime("%Y-%m-%d")
        blob_name = f"reviews/{today}/{review_id}/{safe_title}.pdf"

        blob = bucket.blob(blob_name)
        blob.upload_from_string(pdf_bytes, content_type="application/pdf")

        # Generate a signed URL valid for 7 days
        signed_url = blob.generate_signed_url(
            expiration=timedelta(days=7),
            method="GET",
            version="v4",
        )
        logger.info(f"Uploaded review PDF to gs://{GCS_BUCKET}/{blob_name}")
        return signed_url

    except Exception as e:
        logger.warning(f"GCS upload failed (non-fatal): {e}")
        return None
