"""Report validation, normalisation, redaction and serialisation."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from rules import RULES_BY_ID, SEVERITIES, SEVERITY_RANK, default_severity, rule_recommendation
from secrets import redact_secrets

RULE_ID_SEARCH = re.compile(r"\b([A-Z]{2,4})[-_ ]?(\d{3})\b")
RULE_FAMILIES = (
    {"CFG-002", "HYG-004"},  # committed editor/IDE directory
    {"CI-004", "SEC-006"},  # mutable action reference
    {"CI-002", "SEC-004"},  # unsafe workflow trigger
)
CRITICAL_RULES = {"SEC-001", "SEC-002", "SEC-004"}
CATEGORY_SEVERITY_CAP = {"code_quality": "medium", "collaboration": "medium"}
REPO_WIDE_RULES = {"TST-001", "CI-001", "HYG-001", "COL-002", "BLD-003", "DEP-004"}
MAX_VIOLATIONS = 40
GENERIC_RECOMMENDATION = "Review the finding and remediate it following the linked evidence."


class ReportError(ValueError):
    """Raised when the report cannot be serialised."""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _clean_path(value: Any) -> str | None:
    text = _clean_text(value)
    if not text or text.lower() in {"null", "none", "n/a", "-"}:
        return None
    text = text.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    return text or None


def _clean_line(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "-"}:
        return None
    match = re.search(r"\d+", text)
    if not match:
        return None
    number = int(match.group(0))
    return number if number > 0 else None


def _normalise_rule_id(value: Any) -> str:
    text = _clean_text(value).upper()
    match = RULE_ID_SEARCH.search(text)
    if match:
        candidate = f"{match.group(1)}-{match.group(2)}"
        if candidate in RULES_BY_ID:
            return candidate
    return "GEN-001"


def normalise_violation(
    raw: dict[str, Any],
    path_exists: Callable[[str], bool],
    notes: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return a schema-compliant violation, or ``None`` when unusable."""
    if not isinstance(raw, dict):
        return None

    description = _clean_text(raw.get("description") or raw.get("title"))
    if not description:
        return None

    rule_id = _normalise_rule_id(raw.get("rule_id") or raw.get("id"))
    known_rule = RULES_BY_ID.get(rule_id)

    category = _clean_text(raw.get("category")).lower().replace(" ", "_").replace("-", "_")
    if known_rule:
        category = known_rule.category
    elif category not in {rule.category for rule in RULES_BY_ID.values()} | {"repository_hygiene"}:
        category = "repository_hygiene"

    severity = _clean_text(raw.get("severity")).lower()
    if severity not in SEVERITIES:
        severity = default_severity(rule_id) or "medium"
    if rule_id in CRITICAL_RULES:
        severity = "critical"
    cap = CATEGORY_SEVERITY_CAP.get(category)
    if cap and SEVERITY_RANK[severity] < SEVERITY_RANK[cap]:
        severity = cap

    file = _clean_path(raw.get("file") or raw.get("path") or raw.get("file_path"))
    if file and not path_exists(file):
        if notes is not None:
            notes.append(f"Repository path not found, file set to null: {file}")
        file = None
    line = _clean_line(raw.get("line") or raw.get("line_number"))
    if file is None or rule_id in REPO_WIDE_RULES:
        file = None
        line = None

    evidence = _clean_text(raw.get("evidence"))
    if not evidence:
        evidence = "(no evidence captured by the agent)"
    elif file and line and str(line) not in evidence:
        evidence = f"{evidence} (at {file}:{line})"

    recommendation = _clean_text(raw.get("recommendation"))
    if not recommendation:
        recommendation = rule_recommendation(rule_id) or GENERIC_RECOMMENDATION

    return {
        "rule_id": rule_id,
        "category": category,
        "severity": severity,
        "file": file,
        "line": line,
        "description": redact_secrets(description),
        "evidence": redact_secrets(evidence),
        "recommendation": redact_secrets(recommendation),
    }


def build_report(
    repository_url: str,
    raw_violations: Iterable[Any],
    path_exists: Callable[[str], bool],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    collected: list[dict[str, Any]] = []
    seen_ids: set[tuple[str, str | None, int | None, str]] = set()
    seen_descriptions: set[tuple[str | None, str]] = set()

    for raw in raw_violations:
        violation = normalise_violation(raw, path_exists, notes)
        if violation is None:
            continue
        key = (
            violation["rule_id"],
            violation["file"],
            violation["line"],
            violation["description"].lower(),
        )
        description_key = (violation["file"], violation["description"].lower())
        if key in seen_ids or description_key in seen_descriptions:
            continue
        seen_ids.add(key)
        seen_descriptions.add(description_key)
        collected.append(violation)

    collected.sort(
        key=lambda item: (
            SEVERITY_RANK.get(item["severity"], 9),
            item["rule_id"],
            item["file"] or "",
            item["line"] or 0,
        )
    )

    collected = _drop_overlapping(collected)

    if len(collected) > MAX_VIOLATIONS:
        collected = collected[:MAX_VIOLATIONS]
        if notes is not None:
            notes.append(f"Report truncated to {MAX_VIOLATIONS} violations.")

    return {
        "repository": repository_url,
        "violations": collected,
    }


def _similar(first: str, second: str) -> bool:
    """Cheap token-overlap test used to collapse duplicate root causes."""
    tokens_a = {word for word in re.findall(r"[a-z0-9_.-]{4,}", first.lower())}
    tokens_b = {word for word in re.findall(r"[a-z0-9_.-]{4,}", second.lower())}
    if not tokens_a or not tokens_b:
        return False
    overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    return overlap >= 0.6


def _drop_overlapping(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove findings that describe an already-reported root cause.

    Two findings collapse when they point at the same file/line, or when the
    same file and category carry near-identical descriptions. The list is sorted
    by severity first, so the most serious finding survives.
    """
    kept: list[dict[str, Any]] = []
    for candidate in violations:
        duplicate = False
        for existing in kept:
            same_anchor = (
                candidate["file"] is not None
                and candidate["file"] == existing["file"]
                and candidate["line"] is not None
                and candidate["line"] == existing["line"]
            )
            same_issue = (
                candidate["file"] is not None
                and candidate["file"] == existing["file"]
                and candidate["category"] == existing["category"]
                and _similar(candidate["description"], existing["description"])
            )
            same_family = (
                candidate["file"] is not None
                and candidate["file"] == existing["file"]
                and any(
                    candidate["rule_id"] in family and existing["rule_id"] in family
                    for family in RULE_FAMILIES
                )
            )
            if same_anchor or same_issue or same_family:
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept


def write_report(path: str | Path, report: dict[str, Any]) -> Path:
    destination = Path(path).expanduser()
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReportError(f"Cannot create output directory {parent}: {exc}") from exc

    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    try:
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(parent),
            prefix=".report-",
            suffix=".json",
        )
        with handle:
            handle.write(payload)
        os.replace(handle.name, destination)
    except OSError as exc:
        raise ReportError(f"Cannot write report to {destination}: {exc}") from exc
    return destination
