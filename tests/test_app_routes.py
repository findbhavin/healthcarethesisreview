"""
tests/test_app_routes.py
Integration tests for the Flask app routes.
The Claude API call is mocked — no real API key required.
"""

import sys
import os
import io
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as flask_app_module
from app import app

INPUTS_DIR = os.path.join(os.path.dirname(__file__), "..", "inputs")
SAMPLE_DOCX = os.path.join(
    INPUTS_DIR, "njcm-review-assignment-6266-Article+Text-28605.docx"
)

MOCK_REVIEW_TEXT = """\
---
PEER REVIEW REPORT
Manuscript Title: Test Osteoporosis Study
Manuscript Type: Cross-sectional observational study
Date of Review: 2026-03-05
Reviewer: AI-Assisted Editorial Review System
---

STAGE 1: INITIAL EDITORIAL SCREENING
The manuscript includes a title, abstract, keywords, and main sections.
Missing: ORCID IDs, CRediT taxonomy, cover letter.

STAGE 2: SCOPE AND NOVELTY CHECK
Scope Fit: Strong
The topic is within scope. Research question is clearly stated.

STAGE 3: METHODOLOGY REVIEW
MAJOR: Sample size calculation not described.
MINOR: Blinding not applicable but not explicitly stated.

STAGE 4: RESULTS AND DATA INTEGRITY
Results are clearly presented in tables. Minor numerical inconsistency in Table 2.

STAGE 5: DISCUSSION AND CONCLUSIONS
Conclusions align with results. Limitations are discussed.

STAGE 6: REFERENCES
References appear genuine and relevant. 2 unsupported factual claims in Introduction.

STAGE 7: ETHICAL AND INTEGRITY CHECKS
Ethics approval stated. No concerns about integrity.

STAGE 8: OVERALL EDITORIAL RECOMMENDATION
Decision: Major revision
Summary: The study addresses an important topic but requires a sample size justification
and correction of minor inconsistencies before acceptance.
Key Required Revisions:
1. Add sample size calculation rationale
2. Provide ORCID IDs for all authors
3. Correct numerical inconsistency in Table 2

---
END OF REVIEW REPORT
---
"""


def _make_mock_anthropic_response(text: str):
    """Create a minimal mock that matches the anthropic client response structure."""
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


class TestHealthRoute(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_health_returns_200(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_health_returns_ok(self):
        data = json.loads(resp := self.client.get("/health").data)
        self.assertEqual(data["status"], "ok")


class TestIndexRoute(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_index_returns_200(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_index_contains_html(self):
        resp = self.client.get("/")
        self.assertIn(b"<!DOCTYPE html>", resp.data)

    def test_index_contains_review_title(self):
        resp = self.client.get("/")
        self.assertIn(b"Peer Review", resp.data)


class TestGuidelinesRoutes(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_guidelines_metadata_returns_200(self):
        resp = self.client.get("/guidelines/metadata")
        self.assertEqual(resp.status_code, 200)

    def test_guidelines_metadata_has_version(self):
        resp = self.client.get("/guidelines/metadata")
        data = json.loads(resp.data)
        self.assertIn("version", data)

    def test_guidelines_journals_returns_list(self):
        resp = self.client.get("/guidelines/journals")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("journals", data)
        self.assertIsInstance(data["journals"], list)

    def test_guidelines_validate_returns_valid(self):
        resp = self.client.post("/guidelines/validate")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["valid"])

    def test_admin_reload_returns_200(self):
        resp = self.client.post("/admin/reload-guidelines")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data["reloaded"])

    def test_guidelines_changelog_returns_list(self):
        resp = self.client.get("/guidelines/changelog")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("changelog", data)


class TestReviewRoute(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_review_no_file_returns_400(self):
        resp = self.client.post("/review")
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_review_unsupported_format_returns_400(self):
        resp = self.client.post(
            "/review",
            data={"file": (io.BytesIO(b"fake"), "manuscript.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)

    @patch("review_agent.anthropic.Anthropic")
    def test_review_docx_returns_result(self, mock_anthropic_cls):
        """Full review with a real DOCX but mocked Claude API."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_mock_anthropic_response(
            MOCK_REVIEW_TEXT
        )

        with open(SAMPLE_DOCX, "rb") as f:
            docx_bytes = f.read()

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-key"}):
            resp = self.client.post(
                "/review",
                data={
                    "file": (io.BytesIO(docx_bytes), "test_manuscript.docx"),
                    "journal_name": "NJCM",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        data = json.loads(resp.data)
        self.assertIn("review_id", data)
        self.assertIn("decision", data)
        self.assertIn("review_text", data)
        self.assertIn("word_count", data)
        self.assertEqual(data["decision"], "Major revision")

    @patch("review_agent.anthropic.Anthropic")
    def test_review_txt_content(self, mock_anthropic_cls):
        """Review with a plain-text file."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_mock_anthropic_response(
            MOCK_REVIEW_TEXT
        )

        txt_content = b"Title: Sample Article\n\nAbstract: A study of X in Y population."

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-key"}):
            resp = self.client.post(
                "/review",
                data={"file": (io.BytesIO(txt_content), "sample.txt")},
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("review_id", data)

    def test_review_missing_api_key_returns_500(self):
        """Without ANTHROPIC_API_KEY, the server should return 500."""
        with open(SAMPLE_DOCX, "rb") as f:
            docx_bytes = f.read()

        env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            resp = self.client.post(
                "/review",
                data={"file": (io.BytesIO(docx_bytes), "test.docx")},
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 500)
        data = json.loads(resp.data)
        self.assertIn("ANTHROPIC_API_KEY", data["error"])


class TestDownloadRoute(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_download_unknown_id_returns_404(self):
        resp = self.client.get("/download/nonexistent-review-id")
        self.assertEqual(resp.status_code, 404)

    @patch("review_agent.anthropic.Anthropic")
    def test_download_after_review_returns_docx(self, mock_anthropic_cls):
        """End-to-end: review then download."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_mock_anthropic_response(
            MOCK_REVIEW_TEXT
        )

        with open(SAMPLE_DOCX, "rb") as f:
            docx_bytes = f.read()

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-key"}):
            review_resp = self.client.post(
                "/review",
                data={"file": (io.BytesIO(docx_bytes), "test.docx")},
                content_type="multipart/form-data",
            )
        review_id = json.loads(review_resp.data)["review_id"]

        download_resp = self.client.get(f"/download/{review_id}")
        self.assertEqual(download_resp.status_code, 200)
        self.assertIn(
            "openxmlformats-officedocument",
            download_resp.content_type,
        )
        # Should be a valid DOCX (starts with PK zip magic bytes)
        self.assertTrue(download_resp.data[:2] == b"PK")


class TestErrorHandlers(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_404_returns_json(self):
        resp = self.client.get("/nonexistent-route")
        self.assertEqual(resp.status_code, 404)
        data = json.loads(resp.data)
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
