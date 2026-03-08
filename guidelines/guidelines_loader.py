"""
guidelines_loader.py
Loads and parses review_guidelines.yaml, builds the Claude system prompt,
and provides journal-specific context.

SMEs can edit review_guidelines.yaml without touching any Python code.
The loader is imported by review_agent.py and re-reads the YAML on each
application start (or on-demand reload via /admin/reload-guidelines).

When GCS_BUCKET is configured the loader fetches rules/current.yaml from
GCS first and falls back to the local disk copy when GCS is unreachable.
This decouples the rule file from the application container so all
instances always use the latest published version from the storage bucket.
"""

import os
import yaml
import logging
from functools import lru_cache
from datetime import datetime

logger = logging.getLogger(__name__)

GUIDELINES_PATH = os.path.join(os.path.dirname(__file__), "review_guidelines.yaml")


def _parse_yaml_str(raw: str) -> dict:
    """Parse raw YAML text and validate the required 'stages' key."""
    data = yaml.safe_load(raw)
    if not data or "stages" not in data:
        raise ValueError("Guidelines YAML is missing required 'stages' key.")
    return data


def _load_yaml() -> dict:
    """
    Load and parse guidelines YAML.
    Priority: GCS current.yaml (when GCS_BUCKET is set) → local disk.
    Falls back to disk silently if GCS is unavailable.
    """
    if os.environ.get("GCS_BUCKET"):
        try:
            from gcs_uploader import get_current_rule_from_gcs
            raw = get_current_rule_from_gcs()
            if raw:
                logger.debug("Guidelines loaded from GCS current.yaml")
                return _parse_yaml_str(raw)
        except Exception as exc:
            logger.warning(
                f"GCS guidelines fetch failed — falling back to disk: {exc}"
            )

    # Local disk fallback
    with open(GUIDELINES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "stages" not in data:
        raise ValueError(
            f"review_guidelines.yaml is missing required 'stages' key. "
            f"Please check {GUIDELINES_PATH}"
        )
    return data


def get_guidelines_version() -> str:
    """Return the version string from the currently active guidelines."""
    try:
        data = _load_yaml()
        return str(data.get("metadata", {}).get("version", "unknown"))
    except Exception:
        return "unknown"


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


def get_full_guidelines() -> dict:
    """
    Return the complete guidelines data structured for UI rendering.
    Includes metadata, all stages (sorted), journal overrides, and changelog.
    """
    data = _load_yaml()

    stages_out = []
    for stage_key in sorted(data["stages"].keys()):
        stage = data["stages"][stage_key]
        num = stage_key.replace("stage_", "")
        stages_out.append({
            "key": stage_key,
            "number": num,
            "name": stage.get("name", stage_key),
            "description": (stage.get("description") or "").strip(),
            "checks": stage.get("checks", []),
            "scope_fit_options": stage.get("scope_fit_options", []),
            "severity_labels": stage.get("severity_labels", []),
            "decision_options": stage.get("decision_options", []),
            "instruction": (stage.get("instruction") or "").strip(),
            "weight": stage.get("weight", 0),
            "max_score": stage.get("max_score", 10),
            "score_rubric": stage.get("score_rubric", {}),
        })

    journals_out = []
    for key, jdata in data.get("journal_overrides", {}).items():
        journals_out.append({
            "key": key,
            "full_name": jdata.get("full_name", key),
            "scope": jdata.get("scope", ""),
            "reference_style": jdata.get("reference_style", ""),
            "word_limits": jdata.get("word_limits", {}),
            "required_sections": jdata.get("required_sections", []),
            "max_references": jdata.get("max_references", {}),
        })

    return {
        "metadata": data.get("metadata", {}),
        "role": (data.get("role") or "").strip(),
        "stages": stages_out,
        "journals": journals_out,
        "changelog": data.get("changelog", []),
    }


def get_stage_weights() -> dict:
    """
    Return a dict mapping stage number (int) to its weight (int).
    E.g. {1: 8, 2: 12, 3: 25, 4: 20, 5: 15, 6: 7, 7: 13, 8: 0}
    """
    data = _load_yaml()
    weights = {}
    for stage_key, stage in data.get("stages", {}).items():
        num = int(stage_key.replace("stage_", ""))
        weights[num] = stage.get("weight", 0)
    return weights


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


def get_guidelines_raw() -> str:
    """Return the raw YAML text of the guidelines file."""
    with open(GUIDELINES_PATH, "r", encoding="utf-8") as f:
        return f.read()


def save_guidelines_yaml(raw_yaml: str) -> dict:
    """
    Validate and atomically save raw YAML text to review_guidelines.yaml.
    Returns {"saved": True} or {"saved": False, "errors": [...]}
    """
    import tempfile
    import shutil

    # Parse YAML syntax
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        return {"saved": False, "errors": [f"YAML syntax error: {e}"]}

    if not data or not isinstance(data, dict):
        return {"saved": False, "errors": ["YAML must be a mapping at the top level."]}

    # Check required keys
    required = ["role", "stages", "output_format"]
    missing = [k for k in required if k not in data]
    if missing:
        return {"saved": False, "errors": [f"Missing required keys: {missing}"]}

    if "stages" not in data or not data["stages"]:
        return {"saved": False, "errors": ["'stages' must be a non-empty mapping."]}

    # Atomic write: temp file in same dir then rename
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(GUIDELINES_PATH), suffix=".yaml.tmp"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(raw_yaml)
        shutil.move(tmp_path, GUIDELINES_PATH)
        logger.info("Guidelines YAML saved successfully.")
        return {"saved": True}
    except Exception as e:
        return {"saved": False, "errors": [f"Write error: {e}"]}
