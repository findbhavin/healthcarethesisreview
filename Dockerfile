# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

# System dependencies for pdfplumber / python-docx
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# ── App stage ─────────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY review_agent.py .
COPY report_generator.py .
COPY gcs_uploader.py .
COPY index.html .
COPY guidelines.html .
COPY admin.html .

# Admin credentials — required for /admin login.
# Note: password changes made via the admin UI write to this file inside the
# running container and are lost on the next deployment. For persistent
# credentials across deployments, mount admin_config.json from a Cloud
# Storage bucket or use Cloud Run's Secret Manager integration.
COPY admin_config.json .

# Copy guidelines package (required by review_agent and report_generator)
COPY guidelines/ ./guidelines/

# Copy reference documents (checklists, guidelines)
COPY docs/ ./docs/

# Cloud Run sets PORT env var; default to 8080
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Use gunicorn with a single worker (Cloud Run scales via instances, not threads)
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
