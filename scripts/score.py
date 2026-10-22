"""Score an audit report against the seeded defect list.

Matching is deliberately generous: a truth entry counts as found when a
violation points at a file whose path contains the expected path (or vice
versa) *and* at least one keyword appears in the violation's description or
evidence. The point is to catch regressions in recall, not to grade wording.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

TRUTH_PATH = Path(__file__).with_name("truth.json")


def _normalise(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def file_matches(expected: str, actual: Any) -> bool:
    """Return True when either path contains the other."""
    expected_path = _normalise(expected)
    actual_path = _normalise(actual)
    if not expected_path or not actual_path:
        return False
    return expected_path in actual_path or actual_path in expected_path


def text_matches(keywords: Iterable[str], violation: dict[str, Any]) -> bool:
    """Return True when any keyword appears in the description or evidence."""
    haystack = _normalise(
        f"{violation.get('description', '')} {violation.get('evidence', '')}"
    )
    return any(_normalise(keyword) in haystack for keyword in keywords if keyword)


def matches(entry: dict[str, Any], violation: dict[str, Any]) -> bool:
    return file_matches(entry["file"], violation.get("file")) and text_matches(
        entry["keywords"], violation
    )


def score(
    truth: list[dict[str, Any]], violations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return recall/precision counts plus the missed entries and extra findings.

    Matching is one-to-one: each truth entry claims at most one violation, and a
    violation already claimed by an earlier truth entry cannot satisfy a later
    one. Entries are processed in file order (greedy).
    """
    matched = 0
    used: set[int] = set()
    missed: list[dict[str, Any]] = []
    for entry in truth:
        hit = next(
            (
                index
                for index, violation in enumerate(violations)
                if index not in used and matches(entry, violation)
            ),
            None,
        )
        if hit is None:
            missed.append(entry)
        else:
            used.add(hit)
            matched += 1
    extra = [violation for index, violation in enumerate(violations) if index not in used]
    return {
        "recall": (matched, len(truth)),
        "precision": (len(used), len(violations)),
        "missed": missed,
        "extra": extra,
    }


def _describe(violation: dict[str, Any]) -> str:
    location = violation.get("file") or "(repo-wide)"
    if violation.get("line"):
        location = f"{location}:{violation['line']}"
    description = str(violation.get("description", "")).strip()
    if len(description) > 100:
        description = description[:97] + "..."
    return f"{violation.get('rule_id', '?')} {location} - {description}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="Path to the audit report JSON.")
    parser.add_argument(
        "truth_file",
        nargs="?",
        default=str(TRUTH_PATH),
        metavar="TRUTH",
        help="Path to the truth file (default: truth.json next to this script).",
    )
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    truth = json.loads(Path(args.truth_file).read_text(encoding="utf-8"))
    violations = report.get("violations", [])

    result = score(truth, violations)
    found, expected = result["recall"]
    hit, total = result["precision"]
    print(f"Recall {found}/{expected}")
    print(f"Precision {hit}/{total}")

    print("MISSED")
    if result["missed"]:
        for entry in result["missed"]:
            print(f"  - {entry['file']} ({', '.join(entry['keywords'])})")
    else:
        print("  (none)")

    print("EXTRA")
    if result["extra"]:
        for violation in result["extra"]:
            print(f"  - {_describe(violation)}")
    else:
        print("  (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
