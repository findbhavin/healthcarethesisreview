"""
guidelines_loader.py
Loads and parses review_guidelines.yaml, builds the Claude system prompt,
and provides journal-specific context.

SMEs can edit review_guidelines.yaml without touching any Python code.
The loader is imported by review_agent.py and re-reads the YAML on each
application start (or on-demand reload via /admin/reload-guidelines).
"""

import os
import yaml
import logging
from functools import lru_cache
from datetime import datetime

logger = logging.getLogger(__name__)

GUIDELINES_PATH = os.path.join(os.path.dirname(__file__), "review_guidelines.yaml")


def _load_yaml() -> dict:
    """Read and parse the YAML file. Raises clear errors on syntax issues."""
    with open(GUIDELINES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "stages" not in data:
        raise ValueError(
            f"review_guidelines.yaml is missing required 'stages' key. "
            f"Please check {GUIDELINES_PATH}"
        )
    return data


def _build_stage_text(stage_key: str, stage: dict) -> str:
    """Convert a single stage dict into a formatted prompt section."""
    lines = [f"{stage_key.upper().replace('_', ' ')} — {stage['name'].upper()}"]
    if stage.get("description"):
        lines.append(stage["description"].strip())
    if stage.get("checks"):
        for check in stage["checks"]:
            lines.append(f"- {check}")
    if stage.get("scope_fit_options"):
        options = " / ".join(stage["scope_fit_options"])
        lines.append(f'Provide a "Scope Fit" rating: {options}')
    if stage.get("severity_labels"):
        labels = " / ".join(stage["severity_labels"])
        lines.append(f"List each issue with severity: {labels}")
    if stage.get("decision_options"):
        lines.append("Provide one of the following decisions:")
        for opt in stage["decision_options"]:
            lines.append(f'  - {opt["label"]}: {opt["criteria"]}')
    if stage.get("instruction"):
        lines.append(stage["instruction"].strip())
    return "\n".join(lines)


def build_system_prompt(journal_name: str = "") -> str:
    """
    Build the full Claude system prompt from the YAML guidelines.
    Optionally inject journal-specific context if journal_name is provided.
    """
    data = _load_yaml()

    role = data.get("role", "You are a senior medical journal peer reviewer.")

    # Build stage sections
    stage_sections = []
    for stage_key in sorted(data["stages"].keys()):
        stage = data["stages"][stage_key]
        stage_sections.append(_build_stage_text(stage_key, stage))

    # Journal-specific context
    journal_context = ""
    if journal_name:
        overrides = data.get("journal_overrides", {})
        # Try exact match then case-insensitive
        jdata = overrides.get(journal_name) or overrides.get(journal_name.upper())
        if not jdata:
            # Fuzzy match: check if any key is contained in journal_name
            for key, val in overrides.items():
                if key.upper() in journal_name.upper() or journal_name.upper() in key.upper():
                    jdata = val
                    break
        if jdata:
            scope = jdata.get("scope", "")
            ref_style = jdata.get("reference_style", "")
            wl = jdata.get("word_limits", {})
            wl_text = ", ".join(f"{k}: {v}" for k, v in wl.items()) if wl else ""
            req = jdata.get("required_sections", [])
            req_text = ", ".join(req) if req else ""
            full_name = jdata.get("full_name", journal_name)
            display = f"{journal_name} — {full_name}" if full_name != journal_name else journal_name
            journal_context = (
                f"\nJOURNAL-SPECIFIC REQUIREMENTS ({display}):\n"
                + (f"- Scope: {scope}\n" if scope else "")
                + (f"- Reference style: {ref_style}\n" if ref_style else "")
                + (f"- Word limits: {wl_text}\n" if wl_text else "")
                + (f"- Required sections: {req_text}\n" if req_text else "")
            )
        else:
            journal_context = f"\nTarget journal: {journal_name}\n"

    output_format = data.get("output_format", "")

    system_prompt = (
        f"{role.strip()}\n\n"
        + "\n\n".join(stage_sections)
        + (f"\n\n{journal_context}" if journal_context else "")
        + (f"\n\n{output_format.strip()}" if output_format else "")
    )
    return system_prompt


def get_metadata() -> dict:
    """Return metadata from the guidelines file (version, last_updated, etc.)."""
    data = _load_yaml()
    return data.get("metadata", {})


def get_changelog() -> list:
    """Return the changelog list from the guidelines file."""
    data = _load_yaml()
    return data.get("changelog", [])


def get_journal_list() -> list[str]:
    """Return list of known journal short-names for the UI dropdown."""
    data = _load_yaml()
    return list(data.get("journal_overrides", {}).keys())


def validate_guidelines() -> dict:
    """
    Validate the guidelines YAML structure.
    Returns {"valid": True} or {"valid": False, "errors": [...]}
    """
    errors = []
    try:
        data = _load_yaml()
    except Exception as e:
        return {"valid": False, "errors": [str(e)]}

    required_top_keys = ["role", "stages", "output_format"]
    for key in required_top_keys:
        if key not in data:
            errors.append(f"Missing required top-level key: '{key}'")

    stages = data.get("stages", {})
    for stage_key, stage in stages.items():
        if "name" not in stage:
            errors.append(f"Stage '{stage_key}' is missing 'name'")
        # A stage must have either 'checks' or 'decision_options' (stage_8 uses the latter)
        if "checks" not in stage and "decision_options" not in stage:
            errors.append(
                f"Stage '{stage_key}' is missing both 'checks' and 'decision_options'"
            )

    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "version": data.get("metadata", {}).get("version", "unknown")}
