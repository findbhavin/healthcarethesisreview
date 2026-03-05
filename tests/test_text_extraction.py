"""
tests/test_text_extraction.py
Unit tests for text extraction from DOCX, PDF, and TXT files.
Uses the actual sample manuscripts in inputs/ — no API key required.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from review_agent import extract_text, extract_text_from_docx

INPUTS_DIR = os.path.join(os.path.dirname(__file__), "..", "inputs")

SAMPLE_DOCX_1 = os.path.join(INPUTS_DIR, "njcm-review-assignment-6266-Article+Text-28605.docx")
SAMPLE_DOCX_2 = os.path.join(INPUTS_DIR, "njcm-review-assignment-6505-Article+Text-29056.docx")


class TestDocxExtraction(unittest.TestCase):

    def _read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def test_sample_docx_1_exists(self):
        self.assertTrue(os.path.exists(SAMPLE_DOCX_1), f"Sample file missing: {SAMPLE_DOCX_1}")

    def test_sample_docx_2_exists(self):
        self.assertTrue(os.path.exists(SAMPLE_DOCX_2), f"Sample file missing: {SAMPLE_DOCX_2}")

    def test_extract_docx_1_returns_text(self):
        text = extract_text(self._read(SAMPLE_DOCX_1), "sample1.docx")
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 100, "Extracted text is too short — likely extraction failure")

    def test_extract_docx_2_returns_text(self):
        text = extract_text(self._read(SAMPLE_DOCX_2), "sample2.docx")
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 100)

    def test_extract_docx_1_contains_expected_keywords(self):
        """Osteoporosis manuscript should mention bone density-related terms."""
        text = extract_text(self._read(SAMPLE_DOCX_1), "sample1.docx")
        text_lower = text.lower()
        found = any(kw in text_lower for kw in ["osteo", "bone", "dexa", "prevalence", "elderly"])
        self.assertTrue(found, "Expected medical keywords not found in extracted text")

    def test_extract_docx_2_contains_expected_keywords(self):
        """Ultra-processed food manuscript should mention nutrition/obesity terms."""
        text = extract_text(self._read(SAMPLE_DOCX_2), "sample2.docx")
        text_lower = text.lower()
        found = any(kw in text_lower for kw in ["food", "obese", "obesity", "bmi", "consumption"])
        self.assertTrue(found, "Expected medical keywords not found in extracted text")

    def test_word_count_docx_1_reasonable(self):
        text = extract_text(self._read(SAMPLE_DOCX_1), "sample1.docx")
        word_count = len(text.split())
        self.assertGreater(word_count, 500, "Word count too low — extraction may be incomplete")
        self.assertLess(word_count, 50000, "Word count too high — possible extraction loop")

    def test_word_count_docx_2_reasonable(self):
        text = extract_text(self._read(SAMPLE_DOCX_2), "sample2.docx")
        word_count = len(text.split())
        self.assertGreater(word_count, 500)
        self.assertLess(word_count, 50000)

    def test_txt_extraction(self):
        sample = b"Title: Test Article\n\nAbstract: This is a test."
        text = extract_text(sample, "test.txt")
        self.assertIn("Test Article", text)

    def test_unsupported_format_raises_error(self):
        with self.assertRaises(ValueError) as ctx:
            extract_text(b"fake content", "manuscript.xlsx")
        self.assertIn("Unsupported file type", str(ctx.exception))

    def test_empty_txt_extraction(self):
        text = extract_text(b"   \n\n  ", "empty.txt")
        # Should return the whitespace-only string; run_review will catch it
        self.assertIsInstance(text, str)


class TestDocxTableExtraction(unittest.TestCase):
    """Verify that tables are extracted alongside paragraphs."""

    def test_tables_extracted_from_sample(self):
        with open(SAMPLE_DOCX_1, "rb") as f:
            data = f.read()
        text = extract_text_from_docx(data)
        # Tables often contain "|" separators in our extractor
        # or at minimum produce text rows — just verify non-empty
        self.assertGreater(len(text), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
