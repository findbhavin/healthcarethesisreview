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
    get_full_guidelines,
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


class TestGetFullGuidelines(unittest.TestCase):
    """Tests for get_full_guidelines() — the structured JSON payload used by the UI."""

    def setUp(self):
        self.data = get_full_guidelines()

    # ── Top-level structure ────────────────────────────────────────────────

    def test_returns_dict(self):
        self.assertIsInstance(self.data, dict)

    def test_has_metadata_key(self):
        self.assertIn("metadata", self.data)

    def test_has_stages_key(self):
        self.assertIn("stages", self.data)

    def test_has_journals_key(self):
        self.assertIn("journals", self.data)

    def test_has_changelog_key(self):
        self.assertIn("changelog", self.data)

    def test_has_role_key(self):
        self.assertIn("role", self.data)
        self.assertIsInstance(self.data["role"], str)
        self.assertGreater(len(self.data["role"]), 50)

    # ── Stages ────────────────────────────────────────────────────────────

    def test_stages_is_list(self):
        self.assertIsInstance(self.data["stages"], list)

    def test_eight_stages_present(self):
        self.assertEqual(len(self.data["stages"]), 8,
                         f"Expected 8 stages, got {len(self.data['stages'])}")

    def test_stages_sorted_by_number(self):
        numbers = [int(s["number"]) for s in self.data["stages"]]
        self.assertEqual(numbers, sorted(numbers))

    def test_each_stage_has_required_fields(self):
        required = ("key", "number", "name", "description", "checks",
                    "weight", "max_score", "score_rubric")
        for stage in self.data["stages"]:
            for field in required:
                self.assertIn(field, stage,
                              f"Stage {stage.get('number')} missing field '{field}'")

    def test_stage_weights_sum_to_100(self):
        total = sum(s["weight"] for s in self.data["stages"])
        self.assertEqual(total, 100,
                         f"Stage weights sum to {total}, expected 100")

    def test_stage_8_weight_is_zero(self):
        stage_8 = next(s for s in self.data["stages"] if s["number"] == "8")
        self.assertEqual(stage_8["weight"], 0,
                         "Stage 8 is derived — weight should be 0")

    def test_stage_3_is_highest_weight(self):
        """Methodology (Stage 3) carries 25% — the highest single weight."""
        stage_3 = next(s for s in self.data["stages"] if s["number"] == "3")
        max_weight = max(s["weight"] for s in self.data["stages"])
        self.assertEqual(stage_3["weight"], max_weight)

    def test_each_stage_has_checks_list(self):
        for stage in self.data["stages"]:
            self.assertIsInstance(stage["checks"], list,
                                  f"Stage {stage['number']} checks must be a list")
            if stage["number"] != "8":  # Stage 8 may have no checks
                self.assertGreater(len(stage["checks"]), 0,
                                   f"Stage {stage['number']} has no checks")

    def test_score_rubric_is_dict(self):
        for stage in self.data["stages"]:
            self.assertIsInstance(stage["score_rubric"], dict,
                                  f"Stage {stage['number']} score_rubric must be a dict")

    def test_score_rubric_nonempty_for_scored_stages(self):
        for stage in self.data["stages"]:
            if stage["weight"] > 0:
                self.assertGreater(len(stage["score_rubric"]), 0,
                                   f"Stage {stage['number']} (weight {stage['weight']}%) has empty rubric")

    def test_max_score_is_10_for_scored_stages(self):
        """Stage 8 is derived (weight=0, max_score=0); all other stages score out of 10."""
        for stage in self.data["stages"]:
            if stage["weight"] > 0:
                self.assertEqual(stage["max_score"], 10,
                                 f"Stage {stage['number']} max_score should be 10")

    # ── Journals ──────────────────────────────────────────────────────────

    def test_journals_is_list(self):
        self.assertIsInstance(self.data["journals"], list)

    def test_njcm_journal_present(self):
        keys = [j["key"] for j in self.data["journals"]]
        self.assertIn("NJCM", keys)

    def test_each_journal_has_required_fields(self):
        required = ("key", "full_name", "scope", "reference_style",
                    "word_limits", "required_sections")
        for journal in self.data["journals"]:
            for field in required:
                self.assertIn(field, journal,
                              f"Journal {journal.get('key')} missing field '{field}'")

    def test_njcm_has_vancouver_style(self):
        njcm = next(j for j in self.data["journals"] if j["key"] == "NJCM")
        self.assertEqual(njcm["reference_style"], "Vancouver")

    def test_njcm_word_limits_are_positive(self):
        njcm = next(j for j in self.data["journals"] if j["key"] == "NJCM")
        for article_type, limit in njcm["word_limits"].items():
            self.assertGreater(limit, 0,
                               f"NJCM word limit for '{article_type}' must be positive")

    # ── Changelog ─────────────────────────────────────────────────────────

    def test_changelog_is_list(self):
        self.assertIsInstance(self.data["changelog"], list)

    def test_changelog_has_entries(self):
        self.assertGreater(len(self.data["changelog"]), 0)

    def test_changelog_entries_have_version_date_changes(self):
        for entry in self.data["changelog"]:
            for field in ("version", "date", "changes"):
                self.assertIn(field, entry,
                              f"Changelog entry missing '{field}'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
