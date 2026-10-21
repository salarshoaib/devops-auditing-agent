from __future__ import annotations

import json

import pytest

from reporting import (
    MAX_VIOLATIONS,
    build_report,
    normalise_violation,
    write_report,
)


def _exists(path: str) -> bool:
    return path in {"app.py", ".github/workflows/ci.yml", "requirements.txt"}


def test_violation_keeps_all_fields():
    raw = {
        "rule_id": "SEC-001",
        "category": "security",
        "severity": "critical",
        "file": "app.py",
        "line": 2,
        "description": "Hardcoded API key",
        "evidence": "line 2 assigns a literal key",
        "recommendation": "Use env vars",
    }
    violation = normalise_violation(raw, _exists)
    assert set(violation) == {
        "rule_id",
        "category",
        "severity",
        "file",
        "line",
        "description",
        "evidence",
        "recommendation",
    }
    assert violation["line"] == 2


def test_severity_and_category_are_normalised():
    raw = {
        "rule_id": "QUA-001",
        "category": "Code Quality",
        "severity": "weird",
        "file": "app.py",
        "line": "7",
        "description": "Debug leftovers",
        "evidence": "print at line 7",
        "recommendation": "",
    }
    violation = normalise_violation(raw, _exists)
    assert violation["category"] == "code_quality"
    assert violation["severity"] == "low"
    assert violation["line"] == 7
    assert violation["recommendation"]


def test_critical_rules_cannot_be_downgraded():
    raw = {
        "rule_id": "SEC-002",
        "category": "security",
        "severity": "low",
        "file": "app.py",
        "line": 1,
        "description": "Committed .env with live key",
        "evidence": ".env tracked",
        "recommendation": "Rotate",
    }
    assert normalise_violation(raw, _exists)["severity"] == "critical"


def test_style_categories_are_capped():
    raw = {
        "rule_id": "QUA-002",
        "category": "code_quality",
        "severity": "critical",
        "file": None,
        "line": None,
        "description": "Debug statement left in source",
        "evidence": "print('debug') in app.py",
        "recommendation": "Remove it",
    }
    assert normalise_violation(raw, _exists)["severity"] == "medium"


def _dot_exists(path: str) -> bool:
    return path in {".env", ".github/workflows/ci.yml", ".vscode/settings.json"}


@pytest.mark.parametrize(
    "path",
    [".env", ".github/workflows/ci.yml", ".vscode/settings.json", "./app.py"],
)
def test_dot_paths_are_preserved(path):
    raw = {
        "rule_id": "SEC-002",
        "category": "security",
        "severity": "critical",
        "file": path,
        "line": 1,
        "description": "Committed secret file",
        "evidence": "file is tracked",
        "recommendation": "Remove it",
    }
    violation = normalise_violation(raw, _dot_exists)
    expected = "app.py" if path.startswith("./") else path
    assert violation["file"] == (expected if expected in {".env", ".github/workflows/ci.yml", ".vscode/settings.json"} else None)


def test_repository_wide_rules_drop_file_anchor():
    raw = {
        "rule_id": "TST-001",
        "category": "testing",
        "severity": "high",
        "file": "app.py",
        "line": 1,
        "description": "No automated tests exist",
        "evidence": "no test files found",
        "recommendation": "Add tests",
    }
    violation = normalise_violation(raw, _exists)
    assert violation["file"] is None
    assert violation["line"] is None


def test_unknown_file_is_nulled():
    notes: list[str] = []
    raw = {
        "rule_id": "SEC-001",
        "category": "security",
        "severity": "critical",
        "file": "ghost.py",
        "line": 12,
        "description": "Hardcoded key",
        "evidence": "line 12",
        "recommendation": "Rotate",
    }
    violation = normalise_violation(raw, _exists, notes)
    assert violation["file"] is None
    assert violation["line"] is None
    assert notes


def test_unknown_rule_falls_back_to_generic_id():
    raw = {
        "rule_id": "NOPE-999",
        "category": "testing",
        "severity": "medium",
        "file": None,
        "line": None,
        "description": "Something odd",
        "evidence": "seen in tree",
        "recommendation": "Fix",
    }
    violation = normalise_violation(raw, _exists)
    assert violation["rule_id"] == "GEN-001"


def test_items_without_description_are_dropped():
    assert normalise_violation({"rule_id": "SEC-001"}, _exists) is None
    assert normalise_violation({"description": "   "}, _exists) is None


def test_secrets_are_redacted_in_report():
    raw = {
        "rule_id": "SEC-001",
        "category": "security",
        "severity": "critical",
        "file": "app.py",
        "line": 2,
        "description": "Hardcoded key",
        "evidence": 'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz012345"',
        "recommendation": "Rotate it",
    }
    violation = normalise_violation(raw, _exists)
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in violation["evidence"]


def test_duplicates_are_removed_and_sorted():
    base = {
        "rule_id": "SEC-001",
        "category": "security",
        "severity": "critical",
        "file": "app.py",
        "line": 2,
        "description": "Hardcoded key",
        "evidence": "line 2",
        "recommendation": "Rotate",
    }
    report = build_report(
        "https://github.com/acme/demo",
        [
            base,
            dict(base),
            {
                "rule_id": "HYG-002",
                "category": "repository_hygiene",
                "severity": "low",
                "file": None,
                "line": None,
                "description": "No .gitignore",
                "evidence": "root listing",
                "recommendation": "Add one",
            },
        ],
        _exists,
    )
    assert len(report["violations"]) == 2
    assert report["violations"][0]["severity"] == "critical"
    assert report["violations"][1]["rule_id"] == "HYG-002"


def test_rule_id_typos_are_recovered():
    base = {
        "category": "security",
        "severity": "critical",
        "file": "app.py",
        "line": 3,
        "description": "Hardcoded credential",
        "evidence": "line 3",
        "recommendation": "Rotate",
    }
    for raw_id in ("SEC_001", "sec-001", "SEC-001 - hardcoded key", " SEC-001 "):
        violation = normalise_violation(dict(base, rule_id=raw_id), _exists)
        assert violation["rule_id"] == "SEC-001"


def test_equivalent_rules_are_collapsed_per_file():
    base = {
        "category": "configuration",
        "file": ".vscode/settings.json",
        "evidence": "tracked settings file",
        "recommendation": "Ignore it",
    }
    report = build_report(
        "https://github.com/acme/demo",
        [
            dict(base, rule_id="CFG-002", severity="medium", line=2, description="Local IDE settings committed"),
            dict(base, rule_id="HYG-004", severity="medium", line=1, description="Editor directory committed"),
        ],
        lambda path: True,
    )
    assert len(report["violations"]) == 1


def test_same_anchor_keeps_the_more_severe_finding():
    base = {
        "category": "ci_cd",
        "file": ".github/workflows/ci.yml",
        "line": 13,
        "evidence": "actions/checkout@master",
    }
    report = build_report(
        "https://github.com/acme/demo",
        [
            dict(base, rule_id="CI-004", severity="low", description="Deprecated action version", recommendation="Upgrade"),
            dict(base, rule_id="SEC-006", severity="medium", description="Mutable action tag", recommendation="Pin to SHA"),
        ],
        _exists,
    )
    assert len(report["violations"]) == 1
    assert report["violations"][0]["rule_id"] == "SEC-006"


def test_same_file_different_lines_are_kept():
    base = {
        "rule_id": "SEC-001",
        "category": "security",
        "severity": "critical",
        "file": "app.py",
        "evidence": "literal credential",
        "recommendation": "Rotate",
    }
    report = build_report(
        "https://github.com/acme/demo",
        [
            dict(base, line=2, description="Hardcoded AWS key"),
            dict(base, line=7, description="Hardcoded Stripe key"),
        ],
        _exists,
    )
    assert len(report["violations"]) == 2


def test_report_is_capped():
    violations = [
        {
            "rule_id": "HYG-003",
            "category": "repository_hygiene",
            "severity": "medium",
            "file": None,
            "line": None,
            "description": f"Generated file {index}",
            "evidence": "tracked build output",
            "recommendation": "Ignore it",
        }
        for index in range(MAX_VIOLATIONS + 10)
    ]
    report = build_report("https://github.com/acme/demo", violations, _exists)
    assert len(report["violations"]) == MAX_VIOLATIONS


def test_empty_report_is_valid():
    report = build_report("https://github.com/acme/demo", [], _exists)
    assert report == {"repository": "https://github.com/acme/demo", "violations": []}


def test_write_report_creates_valid_json(tmp_path):
    report = {"repository": "https://github.com/acme/demo", "violations": []}
    destination = write_report(tmp_path / "nested" / "report.json", report)
    assert destination.exists()
    assert json.loads(destination.read_text(encoding="utf-8")) == report
    assert destination.read_text(encoding="utf-8").endswith("\n")
