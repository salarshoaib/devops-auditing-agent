from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import score  # noqa: E402


def _violation(file: str | None, description: str, evidence: str = "") -> dict:
    return {
        "rule_id": "SEC-001",
        "file": file,
        "line": 1,
        "description": description,
        "evidence": evidence,
    }


def test_file_matches_is_bidirectional_substring():
    assert score.file_matches("build/out.bin", "build/out.bin")
    assert score.file_matches("out.bin", "build/out.bin")
    assert score.file_matches("build/out.bin", "out.bin")
    assert not score.file_matches("build/out.bin", "app/paths.py")
    assert not score.file_matches("build/out.bin", None)


def test_text_matches_reads_description_and_evidence():
    violation = _violation("app/settings_local.py", "Secret committed", "token = ...")

    assert score.text_matches(["secret"], violation)
    assert score.text_matches(["token"], violation)
    assert not score.text_matches(["continue-on-error"], violation)


def test_score_counts_matches_misses_and_extras():
    truth = [
        {"file": ".env", "keywords": ["env", "secret"]},
        {"file": "app/paths.py", "keywords": ["path", "hardcoded"]},
    ]
    violations = [
        _violation(".env", "Committed .env file", "SECRET_KEY present"),
        _violation("README.md", "Missing usage section", "no instructions"),
    ]

    result = score.score(truth, violations)

    assert result["recall"] == (1, 2)
    assert result["precision"] == (1, 2)
    assert [entry["file"] for entry in result["missed"]] == ["app/paths.py"]
    assert [item["file"] for item in result["extra"]] == ["README.md"]


def test_one_violation_can_satisfy_only_its_own_entry():
    truth = [
        {"file": ".github/workflows/pr-bot.yml", "keywords": ["write-all"]},
        {"file": ".github/workflows/pr-bot.yml", "keywords": ["pull_request_target"]},
    ]
    violations = [
        _violation(
            ".github/workflows/pr-bot.yml",
            "Workflow grants write-all permissions",
            "permissions: write-all",
        )
    ]

    result = score.score(truth, violations)

    assert result["recall"] == (1, 2)
    assert result["precision"] == (1, 1)


def test_one_violation_cannot_satisfy_two_truths():
    truth = [
        {"file": ".github/workflows/pr-bot.yml", "keywords": ["pull_request_target"]},
        {"file": ".github/workflows/pr-bot.yml", "keywords": ["write-all"]},
    ]
    violations = [
        _violation(
            ".github/workflows/pr-bot.yml",
            "Unsafe trigger with write-all",
            "on: pull_request_target permissions: write-all",
        )
    ]

    result = score.score(truth, violations)

    assert result["recall"] == (1, 2)
    assert result["precision"] == (1, 1)
    assert [entry["keywords"] for entry in result["missed"]] == [["write-all"]]


def test_two_violations_can_satisfy_two_truths():
    truth = [
        {"file": ".github/workflows/pr-bot.yml", "keywords": ["pull_request_target"]},
        {"file": ".github/workflows/pr-bot.yml", "keywords": ["write-all"]},
    ]
    violations = [
        _violation(
            ".github/workflows/pr-bot.yml",
            "Workflow uses pull_request_target",
            "on: pull_request_target",
        ),
        _violation(
            ".github/workflows/pr-bot.yml",
            "Workflow grants write-all",
            "permissions: write-all",
        ),
    ]

    result = score.score(truth, violations)

    assert result["recall"] == (2, 2)
    assert result["precision"] == (2, 2)
    assert result["missed"] == []
    assert result["extra"] == []
