from __future__ import annotations

from typing import Any

from reporting import build_report

WF = ".github/workflows/ci.yml"


def _v(
    rule: str,
    file: str | None,
    line: int | None,
    desc: str,
    evidence: str = "x",
) -> dict[str, Any]:
    return {
        "rule_id": rule,
        "file": file,
        "line": line,
        "description": desc,
        "evidence": evidence,
        "severity": "high",
        "recommendation": "fix it",
    }


def _run(raws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return build_report("https://github.com/o/r", raws, lambda path: True)["violations"]


def test_family_members_on_different_lines_survive():
    raws = [
        _v("CI-002", WF, 3, "Workflow uses pull_request_target"),
        _v("SEC-004", WF, 11, "Checkout runs untrusted pull request code"),
    ]

    assert len(_run(raws)) == 2


def test_same_rule_same_line_collapses():
    raws = [
        _v("CI-002", WF, 3, "Workflow uses pull_request_target"),
        _v("CI-002", WF, 3, "Untrusted trigger with write access"),
    ]

    assert len(_run(raws)) == 1


def test_same_rule_different_lines_survives():
    raws = [
        _v("CI-002", WF, 3, "Workflow uses pull_request_target"),
        _v("CI-002", WF, 40, "Second workflow uses pull_request_target"),
    ]

    assert len(_run(raws)) == 2


def test_family_members_on_same_line_collapse():
    raws = [
        _v("CI-004", WF, 8, "Action pinned to a mutable ref"),
        _v("SEC-006", WF, 8, "Unpinned action reference"),
    ]

    assert len(_run(raws)) == 1


def test_missing_line_falls_back_to_evidence():
    same = [
        _v("CI-002", WF, None, "Unsafe trigger", "pull_request_target runs untrusted code"),
        _v("SEC-004", WF, None, "Untrusted code execution", "pull_request_target runs untrusted code"),
    ]
    different = [
        _v("CI-002", WF, None, "Unsafe trigger", "pull_request_target trigger"),
        _v("SEC-004", WF, None, "Broad token scope", "write-all permissions token"),
    ]

    assert len(_run(same)) == 1
    assert len(_run(different)) == 2


def test_two_credentials_same_file_survive():
    raws = [
        _v("SEC-001", "app/settings.py", 10, "Hardcoded AWS access key"),
        _v("SEC-001", "app/settings.py", 20, "Hardcoded GitHub token"),
    ]

    assert len(_run(raws)) == 2


def test_three_distinct_workflow_defects_survive():
    raws = [
        _v("CI-002", WF, 2, "Workflow uses pull_request_target trigger"),
        _v("SEC-003", WF, 3, "Workflow grants write-all permissions"),
        _v("CI-004", WF, 8, "Action checkout pinned to a mutable version"),
    ]

    violations = _run(raws)

    assert len(violations) == 3
    assert sorted(item["rule_id"] for item in violations) == ["CI-002", "CI-004", "SEC-003"]
