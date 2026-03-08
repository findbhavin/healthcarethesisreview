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

    Returns {"success": True, "filename": "v2.1.yaml"}  or
            {"success": False, "error": "..."}
    """
    if not GCS_BUCKET:
        return {"success": False, "error": "GCS_BUCKET not configured."}
    try:
        _, bucket = _get_storage_client_and_bucket()
        safe_ver = version.replace("/", "-").replace(" ", "_")
        filename  = f"v{safe_ver}.yaml"
        blob_name = RULES_PREFIX + filename

        # Upload versioned copy
        blob = bucket.blob(blob_name)
        blob.upload_from_string(yaml_content.encode("utf-8"),
                                content_type="text/yaml")

        # Update current.yaml
        cur = bucket.blob(RULES_CURRENT)
        cur.upload_from_string(yaml_content.encode("utf-8"),
                               content_type="text/yaml")

        # Update version index
        versions = _get_versions_index(bucket)
        # Mark all previous as not-current
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
    """
    Return all rule versions from GCS (newest first).
    Each entry: {version, filename, uploaded_at, author, size_bytes, is_current}
    Returns [] if GCS not configured or index missing.
    """
    if not GCS_BUCKET:
        return []
    try:
        _, bucket = _get_storage_client_and_bucket()
        versions = _get_versions_index(bucket)
        return list(reversed(versions))   # newest first
    except Exception as e:
        logger.warning(f"list_rule_versions failed: {e}")
        return []


def get_rule_version(filename: str) -> str | None:
    """
    Download the YAML content of a specific rule version from GCS.
    Returns None if GCS not configured, blob missing, or error.
    """
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
    """
    Revert to a previously stored rule version.
    Copies rules/<filename> → rules/current.yaml and updates the index.

    Returns {"success": True, "version": "..."} or {"success": False, "error": "..."}
    """
    if not GCS_BUCKET:
        return {"success": False, "error": "GCS_BUCKET not configured."}
    try:
        _, bucket = _get_storage_client_and_bucket()
        src_blob = bucket.blob(RULES_PREFIX + filename)
        if not src_blob.exists():
            return {"success": False, "error": f"{filename} not found in GCS."}

        yaml_content = src_blob.download_as_text(encoding="utf-8")

        # Overwrite current.yaml
        cur = bucket.blob(RULES_CURRENT)
        cur.upload_from_string(yaml_content.encode("utf-8"),
                               content_type="text/yaml")

        # Update index — mark only this version as current
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
    """
    Download the current rule YAML from GCS (rules/current.yaml).
    Returns None if GCS not configured, file missing, or error.
    """
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
