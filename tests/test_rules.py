"""Rule-catalog guarantees for the workflow and configuration defect classes."""

from __future__ import annotations

import pytest

from reporting import REPO_WIDE_RULES, normalise_violation
from rules import RULES, RULES_BY_ID


def _normalise(rule_id: str) -> dict:
    violation = normalise_violation(
        {
            "rule_id": rule_id,
            "description": "A concrete defect.",
            "file": "config/prod.py",
            "line": 7,
        },
        lambda path: True,
    )
    assert violation is not None
    return violation


def test_script_injection_rule_keeps_category_and_severity():
    violation = _normalise("SEC-008")
    assert violation["category"] == "security"
    assert violation["severity"] == "high"
    assert (violation["file"], violation["line"]) == ("config/prod.py", 7)


def test_remote_script_pipe_rule_keeps_category_and_severity():
    violation = _normalise("SEC-009")
    assert violation["category"] == "security"
    assert violation["severity"] == "high"
    assert (violation["file"], violation["line"]) == ("config/prod.py", 7)


def test_embedded_credentials_rule_keeps_category_and_severity():
    violation = _normalise("SEC-010")
    assert violation["category"] == "security"
    assert violation["severity"] == "critical"
    assert (violation["file"], violation["line"]) == ("config/prod.py", 7)


def test_unpinned_ci_install_rule_keeps_category_and_severity():
    violation = _normalise("CI-005")
    assert violation["category"] == "ci_cd"
    assert violation["severity"] == "medium"
    assert (violation["file"], violation["line"]) == ("config/prod.py", 7)


def test_no_defect_class_is_repository_wide():
    for rule_id in ("SEC-008", "SEC-009", "SEC-010", "CI-005"):
        assert rule_id not in REPO_WIDE_RULES


def test_rule_ids_are_unique():
    ids = [rule.rule_id for rule in RULES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "rule_id",
    ["CI-005", "SEC-008", "SEC-009", "SEC-010", "CFG-002", "CFG-004", "DEP-002", "SEC-006"],
)
def test_defect_classes_have_distinct_catalog_entries(rule_id):
    rule = RULES_BY_ID[rule_id]
    assert rule.title
    assert rule.recommendation
    assert rule.severity in {"low", "medium", "high", "critical"}
