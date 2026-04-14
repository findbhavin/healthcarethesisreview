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
from app import app, _review_store

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

SAMPLE_REVIEW_RESULT = {
    "manuscript_title": "Test Osteoporosis Study",
    "filename": "test.docx",
    "word_count": 120,
    "decision": "Major revision",
    "journal_name": "NJCM",
    "review_text": MOCK_REVIEW_TEXT,
}


def _parse_sse(response_data: bytes) -> list[dict]:
    """Parse SSE response body into a list of event dicts."""
    events = []
    for line in response_data.decode("utf-8", errors="replace").split("\n"):
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _make_stream_mock(text: str):
    """
    Create a mock for client.messages.stream() that yields text as a
    context manager with a .text_stream attribute.
    """
    mock_stream = MagicMock()
    mock_stream.text_stream = iter([text])
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)
    return mock_stream


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
    def test_review_docx_returns_sse_stream(self, mock_anthropic_cls):
        """Full review with a real DOCX but mocked Claude API — checks SSE events."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.stream.return_value = _make_stream_mock(MOCK_REVIEW_TEXT)

        with open(SAMPLE_DOCX, "rb") as f:
            docx_bytes = f.read()

        with patch("app._resolve_ai_config", return_value={
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "anthropic_api_key": "sk-ant-test-key",
            "gemini_api_key": "",
        }):
            resp = self.client.post(
                "/review",
                data={
                    "file": (io.BytesIO(docx_bytes), "test_manuscript.docx"),
                    "journal_name": "NJCM",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn("text/event-stream", resp.content_type)

        events = _parse_sse(resp.data)
        done_events = [e for e in events if e.get("type") == "done"]
        self.assertEqual(len(done_events), 1)
        done = done_events[0]
        self.assertIn("review_id", done)
        self.assertIn("decision", done)
        self.assertIn("word_count", done)
        self.assertEqual(done["decision"], "Major revision")

    @patch("review_agent.anthropic.Anthropic")
    def test_review_txt_content(self, mock_anthropic_cls):
        """Review with a plain-text file — checks SSE done event."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.stream.return_value = _make_stream_mock(MOCK_REVIEW_TEXT)

        txt_content = b"Title: Sample Article\n\nAbstract: A study of X in Y population."

        with patch("app._resolve_ai_config", return_value={
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "anthropic_api_key": "sk-ant-test-key",
            "gemini_api_key": "",
        }):
            resp = self.client.post(
                "/review",
                data={"file": (io.BytesIO(txt_content), "sample.txt")},
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200)
        events = _parse_sse(resp.data)
        done_events = [e for e in events if e.get("type") == "done"]
        self.assertEqual(len(done_events), 1)
        self.assertIn("review_id", done_events[0])

    def test_review_missing_api_key_returns_error_event(self):
        """Without configured provider key, an SSE error event should be returned."""
        with open(SAMPLE_DOCX, "rb") as f:
            docx_bytes = f.read()

        env_without_key = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            resp = self.client.post(
                "/review",
                data={"file": (io.BytesIO(docx_bytes), "test.docx")},
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 200)  # SSE always starts 200
        events = _parse_sse(resp.data)
        error_events = [e for e in events if e.get("type") == "error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("GEMINI_API_KEY", error_events[0]["error"])


class TestDownloadRoute(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_download_unknown_id_returns_404(self):
        resp = self.client.get("/download/nonexistent-review-id")
        self.assertEqual(resp.status_code, 404)

    def test_download_after_seeding_store_returns_pdf(self):
        """Seed the review store directly and verify PDF download."""
        review_id = "test-direct-seed-id"
        _review_store[review_id] = SAMPLE_REVIEW_RESULT

        download_resp = self.client.get(f"/download/{review_id}")
        self.assertEqual(download_resp.status_code, 200)
        self.assertIn("pdf", download_resp.content_type)
        # PDF magic bytes
        self.assertTrue(download_resp.data.startswith(b"%PDF"))

        del _review_store[review_id]


class TestDownloadFilename(unittest.TestCase):
    """Verify the PDF download filename is sanitised from the manuscript title."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_download_content_disposition_contains_pdf(self):
        review_id = "test-filename-check-id"
        _review_store[review_id] = dict(SAMPLE_REVIEW_RESULT,
                                        manuscript_title="My Test/Manuscript\\Title")
        resp = self.client.get(f"/download/{review_id}")
        self.assertEqual(resp.status_code, 200)
        cd = resp.headers.get("Content-Disposition", "")
        self.assertIn(".pdf", cd)
        # Slashes and backslashes must be sanitised
        self.assertNotIn("/", cd.split("filename=")[-1])
        del _review_store[review_id]


class TestGuidelinesFullRoute(unittest.TestCase):
    """Tests for GET /guidelines/full — the structured JSON endpoint used by the UI."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_guidelines_full_returns_200(self):
        resp = self.client.get("/guidelines/full")
        self.assertEqual(resp.status_code, 200)

    def test_guidelines_full_is_json(self):
        resp = self.client.get("/guidelines/full")
        data = json.loads(resp.data)
        self.assertIsInstance(data, dict)

    def test_guidelines_full_has_required_top_level_keys(self):
        resp = self.client.get("/guidelines/full")
        data = json.loads(resp.data)
        for key in ("metadata", "stages", "journals", "changelog", "role"):
            self.assertIn(key, data, f"Missing top-level key: {key}")

    def test_guidelines_full_stages_count(self):
        resp = self.client.get("/guidelines/full")
        data = json.loads(resp.data)
        self.assertEqual(len(data["stages"]), 8)

    def test_guidelines_full_each_stage_has_weight_and_rubric(self):
        """Verifies the fields consumed by the improved guidelines display UI."""
        resp = self.client.get("/guidelines/full")
        data = json.loads(resp.data)
        for stage in data["stages"]:
            self.assertIn("weight", stage,
                          f"Stage {stage.get('number')} missing 'weight' (needed by WRS display)")
            self.assertIn("max_score", stage,
                          f"Stage {stage.get('number')} missing 'max_score'")
            self.assertIn("score_rubric", stage,
                          f"Stage {stage.get('number')} missing 'score_rubric'")

    def test_guidelines_full_weights_sum_to_100(self):
        resp = self.client.get("/guidelines/full")
        data = json.loads(resp.data)
        total = sum(s["weight"] for s in data["stages"])
        self.assertEqual(total, 100)

    def test_guidelines_full_journals_list_nonempty(self):
        resp = self.client.get("/guidelines/full")
        data = json.loads(resp.data)
        self.assertGreater(len(data["journals"]), 0)


class TestGuidelinesPage(unittest.TestCase):
    """Tests for GET /guidelines-page — the human-readable guidelines viewer."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_guidelines_page_returns_200(self):
        resp = self.client.get("/guidelines-page")
        self.assertEqual(resp.status_code, 200)

    def test_guidelines_page_is_html(self):
        resp = self.client.get("/guidelines-page")
        self.assertIn(b"<!DOCTYPE html>", resp.data)

    def test_guidelines_page_contains_wrs_section(self):
        """The improved UI includes a WRS formula section."""
        resp = self.client.get("/guidelines-page")
        self.assertIn(b"wrs-card", resp.data)

    def test_guidelines_page_contains_rubric_styles(self):
        """Score rubric CSS classes must be present for the collapsible tables."""
        resp = self.client.get("/guidelines-page")
        self.assertIn(b"rubric-table", resp.data)
        self.assertIn(b"rubric-toggle", resp.data)

    def test_guidelines_page_contains_weight_badge_styles(self):
        """Weight circle badges CSS class must be present."""
        resp = self.client.get("/guidelines-page")
        self.assertIn(b"weight-circle", resp.data)

    def test_guidelines_page_loads_guidelines_full_api(self):
        """JS in the page must fetch /guidelines/full."""
        resp = self.client.get("/guidelines-page")
        self.assertIn(b"/guidelines/full", resp.data)


class TestPollRoute(unittest.TestCase):
    """Tests for GET /review/<review_id>/poll — reconnect after network drop."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_poll_unknown_id_returns_404(self):
        resp = self.client.get("/review/nonexistent-id/poll")
        self.assertEqual(resp.status_code, 404)
        data = json.loads(resp.data)
        self.assertEqual(data["status"], "not_found")

    def test_poll_running_review_returns_status(self):
        review_id = "test-poll-running-id"
        _review_store[review_id] = {"status": "running", "accumulated_text": "Partial…"}
        resp = self.client.get(f"/review/{review_id}/poll")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["status"], "running")
        self.assertIn("accumulated_text", data)
        del _review_store[review_id]

    def test_poll_done_review_returns_full_payload(self):
        review_id = "test-poll-done-id"
        _review_store[review_id] = {
            "status": "done",
            "manuscript_title": "Poll Test Manuscript",
            "decision": "Minor revision",
            "word_count": 2500,
            "weighted_score": 72.5,
            "stage_scores": {"1": 8, "2": 9},
            "wrs_parts": "S1×8 + S2×12",
            "review_text": "Full review text here.",
        }
        resp = self.client.get(f"/review/{review_id}/poll")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["status"], "done")
        self.assertIn("review_id", data)
        self.assertIn("decision", data)
        self.assertIn("word_count", data)
        self.assertIn("weighted_score", data)
        del _review_store[review_id]


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
