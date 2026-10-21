"""Guards the committed example report against schema drift."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rules import CATEGORIES, RULES_BY_ID, SEVERITIES

SAMPLE = Path(__file__).resolve().parents[1] / "sample_report.json"
FIELDS = {
    "rule_id",
    "category",
    "severity",
    "file",
    "line",
    "description",
    "evidence",
    "recommendation",
}


def test_sample_report_has_required_shape():
    report = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert set(report) == {"repository", "violations"}
    assert isinstance(report["repository"], str)
    assert report["repository"].startswith("https://github.com/")
    assert isinstance(report["violations"], list)
    assert report["violations"], "the sample should demonstrate at least one finding"


def test_sample_violations_follow_schema():
    report = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for violation in report["violations"]:
        assert set(violation) == FIELDS
        assert violation["severity"] in SEVERITIES
        assert violation["category"] in CATEGORIES
        assert violation["rule_id"] == "GEN-001" or violation["rule_id"] in RULES_BY_ID
        assert violation["file"] is None or isinstance(violation["file"], str)
        if violation["file"] is None:
            assert violation["line"] is None
        else:
            assert violation["line"] is None or violation["line"] > 0
        for field in ("description", "evidence", "recommendation"):
            assert isinstance(violation[field], str) and violation[field].strip()
