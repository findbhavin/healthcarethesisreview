"""
gcs_uploader.py
Uploads peer review PDF reports to Google Cloud Storage for persistent
offline access. The bucket name is read from the GCS_BUCKET environment
variable. If not set, uploading is silently skipped.

Also provides rule-file versioning in GCS:
  rules/
    versions.json       — version index with metadata
    v<version>.yaml     — archived rule file snapshots
    current.yaml        — always the active version
"""

import os
import json
import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

RULES_PREFIX = "rules/"
RULES_INDEX  = RULES_PREFIX + "versions.json"
RULES_CURRENT = RULES_PREFIX + "current.yaml"


# ---------------------------------------------------------------------------
# Rule versioning helpers
# ---------------------------------------------------------------------------

def _get_storage_client_and_bucket():
    """Return (client, bucket) or raise if GCS is not configured."""
    from google.cloud import storage as gcs
    client = gcs.Client()
    bucket = client.bucket(GCS_BUCKET)
    return client, bucket


def _get_versions_index(bucket) -> list:
    """Download and parse versions.json from GCS. Returns [] if not found."""
    blob = bucket.blob(RULES_INDEX)
    if not blob.exists():
        return []
    raw = blob.download_as_text(encoding="utf-8")
    return json.loads(raw)


def _put_versions_index(bucket, versions: list) -> None:
    """Overwrite versions.json in GCS with the supplied list."""
    blob = bucket.blob(RULES_INDEX)
    blob.upload_from_string(
        json.dumps(versions, indent=2, ensure_ascii=False),
        content_type="application/json",
    )


def push_rule_version(yaml_content: str, version: str, author: str = "admin") -> dict:
    """
    Upload a new rule version to GCS.
    Stores it as rules/v<version>.yaml, copies it to rules/current.yaml,
    and updates rules/versions.json index.
    """
    if not GCS_BUCKET:
        return {"success": False, "error": "GCS_BUCKET not configured."}
    try:
        _, bucket = _get_storage_client_and_bucket()
        safe_ver = version.replace("/", "-").replace(" ", "_")
        filename  = f"v{safe_ver}.yaml"
        blob_name = RULES_PREFIX + filename

        blob = bucket.blob(blob_name)
        blob.upload_from_string(yaml_content.encode("utf-8"), content_type="text/yaml")

        cur = bucket.blob(RULES_CURRENT)
        cur.upload_from_string(yaml_content.encode("utf-8"), content_type="text/yaml")

        versions = _get_versions_index(bucket)
        for v in versions:
            v["is_current"] = False
        versions.append({
            "version":     version,
            "filename":    filename,
            "blob":        blob_name,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "author":      author,
            "size_bytes":  len(yaml_content.encode("utf-8")),
            "is_current":  True,
        })
        _put_versions_index(bucket, versions)

        logger.info(f"Rule version {version} pushed to GCS as {blob_name}")
        return {"success": True, "filename": filename, "version": version}
    except Exception as e:
        logger.warning(f"push_rule_version failed: {e}")
        return {"success": False, "error": str(e)}


def list_rule_versions() -> list:
    """Return all rule versions from GCS (newest first)."""
    if not GCS_BUCKET:
        return []
    try:
        _, bucket = _get_storage_client_and_bucket()
        versions = _get_versions_index(bucket)
        return list(reversed(versions))
    except Exception as e:
        logger.warning(f"list_rule_versions failed: {e}")
        return []


def get_rule_version(filename: str) -> str | None:
    """Download the YAML content of a specific rule version from GCS."""
    if not GCS_BUCKET:
        return None
    try:
        _, bucket = _get_storage_client_and_bucket()
        blob = bucket.blob(RULES_PREFIX + filename)
        if not blob.exists():
            return None
        return blob.download_as_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"get_rule_version({filename}) failed: {e}")
        return None


def revert_rule_version(filename: str) -> dict:
    """Revert to a previously stored rule version."""
    if not GCS_BUCKET:
        return {"success": False, "error": "GCS_BUCKET not configured."}
    try:
        _, bucket = _get_storage_client_and_bucket()
        src_blob = bucket.blob(RULES_PREFIX + filename)
        if not src_blob.exists():
            return {"success": False, "error": f"{filename} not found in GCS."}

        yaml_content = src_blob.download_as_text(encoding="utf-8")

        cur = bucket.blob(RULES_CURRENT)
        cur.upload_from_string(yaml_content.encode("utf-8"), content_type="text/yaml")

        versions = _get_versions_index(bucket)
        reverted_version = None
        for v in versions:
            v["is_current"] = (v["filename"] == filename)
            if v["filename"] == filename:
                reverted_version = v.get("version", filename)
        _put_versions_index(bucket, versions)

        logger.info(f"Reverted rule to {filename} in GCS")
        return {"success": True, "version": reverted_version, "filename": filename}
    except Exception as e:
        logger.warning(f"revert_rule_version({filename}) failed: {e}")
        return {"success": False, "error": str(e)}


def get_current_rule_from_gcs() -> str | None:
    """Download the current rule YAML from GCS (rules/current.yaml)."""
    if not GCS_BUCKET:
        return None
    try:
        _, bucket = _get_storage_client_and_bucket()
        blob = bucket.blob(RULES_CURRENT)
        if not blob.exists():
            return None
        return blob.download_as_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"get_current_rule_from_gcs failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Dunning management — pending payment staging
# ---------------------------------------------------------------------------

PENDING_PREFIX = "pending/"
PENDING_TTL_HOURS = 24


def stage_pending_payment(review_id: str, email: str, pdf_bytes: bytes, meta: dict) -> bool:
    """
    Stage a pending-payment record to GCS for up to 24 hours.
    Stores pending/{review_id}/report.pdf and pending/{review_id}/meta.json.
    Returns True on success, False if GCS not configured or upload fails.
    """
    if not GCS_BUCKET:
        return False
    try:
        _, bucket = _get_storage_client_and_bucket()
        prefix = f"{PENDING_PREFIX}{review_id}/"

        meta_payload = {
            "email": email,
            "staged_at_utc": datetime.now(timezone.utc).isoformat(),
            "ttl_hours": PENDING_TTL_HOURS,
            **{k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool, type(None)))},
        }
        bucket.blob(prefix + "meta.json").upload_from_string(
            json.dumps(meta_payload, ensure_ascii=False),
            content_type="application/json",
        )
        bucket.blob(prefix + "report.pdf").upload_from_string(
            pdf_bytes, content_type="application/pdf"
        )
        logger.info(f"Staged pending payment for review {review_id} (email: {email})")
        return True
    except Exception as e:
        logger.warning(f"stage_pending_payment failed: {e}")
        return False


def get_pending_payment(review_id: str) -> dict | None:
    """
    Retrieve staged pending-payment data for review_id.
    Returns dict with keys: email, pdf_bytes, meta — or None if not found/expired.
    """
    if not GCS_BUCKET:
        return None
    try:
        _, bucket = _get_storage_client_and_bucket()
        prefix = f"{PENDING_PREFIX}{review_id}/"

        meta_blob = bucket.blob(prefix + "meta.json")
        if not meta_blob.exists():
            return None
        meta = json.loads(meta_blob.download_as_text(encoding="utf-8"))

        staged_at = datetime.fromisoformat(meta["staged_at_utc"])
        age_hours = (datetime.now(timezone.utc) - staged_at).total_seconds() / 3600
        if age_hours > PENDING_TTL_HOURS:
            logger.info(f"Pending payment for {review_id} expired ({age_hours:.1f}h old)")
            delete_pending_payment(review_id)
            return None

        pdf_blob = bucket.blob(prefix + "report.pdf")
        pdf_bytes = pdf_blob.download_as_bytes() if pdf_blob.exists() else None

        return {"email": meta.get("email"), "pdf_bytes": pdf_bytes, "meta": meta}
    except Exception as e:
        logger.warning(f"get_pending_payment({review_id}) failed: {e}")
        return None


def delete_pending_payment(review_id: str) -> None:
    """Remove staged pending-payment blobs from GCS (best-effort, non-fatal)."""
    if not GCS_BUCKET:
        return
    try:
        _, bucket = _get_storage_client_and_bucket()
        prefix = f"{PENDING_PREFIX}{review_id}/"
        for suffix in ("meta.json", "report.pdf"):
            blob = bucket.blob(prefix + suffix)
            if blob.exists():
                blob.delete()
        logger.info(f"Deleted pending payment staging for review {review_id}")
    except Exception as e:
        logger.warning(f"delete_pending_payment({review_id}) failed: {e}")


# ---------------------------------------------------------------------------
# Review PDF upload
# ---------------------------------------------------------------------------

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
