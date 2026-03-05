"""
tests/test_guidelines_loader.py
Unit tests for the guidelines loader — no API key required.
"""

import sys
import os
import unittest

# Make sure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from guidelines.guidelines_loader import (
    build_system_prompt,
    get_metadata,
    get_changelog,
    get_journal_list,
    validate_guidelines,
)


class TestGuidelinesLoader(unittest.TestCase):

    def test_validate_returns_valid(self):
        result = validate_guidelines()
        self.assertTrue(result["valid"], f"Guidelines validation failed: {result.get('errors')}")

    def test_validate_has_version(self):
        result = validate_guidelines()
        self.assertIn("version", result)

    def test_metadata_has_required_fields(self):
        meta = get_metadata()
        self.assertIn("version", meta)
        self.assertIn("last_updated", meta)
        self.assertIn("maintained_by", meta)

    def test_changelog_is_list(self):
        changelog = get_changelog()
        self.assertIsInstance(changelog, list)
        self.assertGreater(len(changelog), 0)

    def test_changelog_entries_have_required_fields(self):
        changelog = get_changelog()
        for entry in changelog:
            self.assertIn("version", entry, "Changelog entry missing 'version'")
            self.assertIn("date", entry, "Changelog entry missing 'date'")
            self.assertIn("changes", entry, "Changelog entry missing 'changes'")

    def test_journal_list_is_list(self):
        journals = get_journal_list()
        self.assertIsInstance(journals, list)

    def test_njcm_in_journal_list(self):
        journals = get_journal_list()
        self.assertIn("NJCM", journals)

    def test_build_system_prompt_no_journal(self):
        prompt = build_system_prompt()
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 200)
        # All 8 stages should appear
        for i in range(1, 9):
            self.assertIn(f"STAGE {i}", prompt.upper(), f"Stage {i} missing from system prompt")

    def test_build_system_prompt_with_njcm(self):
        prompt = build_system_prompt(journal_name="NJCM")
        self.assertIn("NJCM", prompt)
        self.assertIn("Vancouver", prompt)

    def test_build_system_prompt_with_unknown_journal(self):
        # Unknown journal should still produce a valid prompt
        prompt = build_system_prompt(journal_name="UnknownJournal2099")
        self.assertIsInstance(prompt, str)
        self.assertIn("UnknownJournal2099", prompt)

    def test_system_prompt_contains_output_format(self):
        prompt = build_system_prompt()
        self.assertIn("PEER REVIEW REPORT", prompt)
        self.assertIn("Decision:", prompt)


class TestGuidelinesContent(unittest.TestCase):
    """Verify that specific required checks are present in the guidelines."""

    def setUp(self):
        self.prompt = build_system_prompt()

    def test_consort_mentioned(self):
        self.assertIn("CONSORT", self.prompt)

    def test_prisma_mentioned(self):
        self.assertIn("PRISMA", self.prompt)

    def test_strobe_mentioned(self):
        self.assertIn("STROBE", self.prompt)

    def test_ethics_stage_present(self):
        self.assertIn("plagiarism", self.prompt.lower())

    def test_references_stage_checks_completeness(self):
        self.assertIn("fabricated", self.prompt.lower())

    def test_credit_taxonomy_mentioned(self):
        self.assertIn("CRediT", self.prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
