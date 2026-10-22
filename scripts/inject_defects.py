"""Prepare a throw-away repository with seeded defects for a recall test.

The script shallow-clones a public repository, replaces its history with a
fresh two-commit history (baseline + seeded defects), and prints the path of
the prepared clone. It **never pushes**: publishing the fixture is a manual
step, because the agent only audits public GitHub URLs.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM = "https://github.com/encode/httpx"
FIXTURE_NAME = "httpx-recall-fixture"

PR_BOT = """\
name: PR bot
on: pull_request_target

permissions: write-all

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@master
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - run: pip install . && python scripts/check.py
"""

TESTS_EXTRA = """\
name: Extra tests
on: push

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install pytest && pytest || true
        continue-on-error: true
"""

SETTINGS = """\
DB_HOST = "localhost"
API_TOKEN = "ghp_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE1234"
"""

PATHS = """\
DATA_DIR = "/home/johndoe/projects/data"
CACHE_DIR = r"C:\\Users\\john\\AppData\\cache"
"""

ENV_FILE = "SECRET_KEY=not-a-real-secret\nDEBUG=True\n"


def _rmtree(path: Path) -> None:
    """Remove *path*, clearing the read-only bit git sets on Windows objects."""

    def retry(function, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_fixture(destination: Path) -> Path:
    """Clone *UPSTREAM*, seed the defects, and return the fixture root."""
    if destination.exists():
        raise SystemExit(f"Destination already exists: {destination}")
    subprocess.run(
        ["git", "clone", "--depth", "1", "--no-tags", "--quiet", UPSTREAM, str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )

    _rmtree(destination / ".git")
    _git(destination, "init", "-q", "-b", "main")
    _git(destination, "config", "user.email", "fixture@example.invalid")
    _git(destination, "config", "user.name", "Recall Fixture")
    _git(destination, "add", "-A")
    _git(destination, "commit", "-q", "-m", "Baseline: upstream snapshot")

    _write(destination, ".github/workflows/pr-bot.yml", PR_BOT)
    _write(destination, ".github/workflows/tests-extra.yml", TESTS_EXTRA)
    _write(destination, "app/settings_local.py", SETTINGS)
    _write(destination, "app/paths.py", PATHS)
    _write(destination, ".env", ENV_FILE)

    (destination / "build").mkdir(exist_ok=True)
    (destination / "build" / "out.bin").write_bytes(b"\x00\x01generated\x00")
    (destination / "__pycache__").mkdir(exist_ok=True)
    (destination / "__pycache__" / "a.pyc").write_bytes(b"\x00compiled\x00")

    _git(
        destination,
        "add",
        "-f",
        ".env",
        "build/out.bin",
        "__pycache__/a.pyc",
    )
    _git(destination, "add", "-A")
    _git(destination, "commit", "-q", "-m", "Seed recall defects (FAKE data)")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        help="Directory for the prepared clone (default: a new temp directory).",
    )
    args = parser.parse_args()

    destination = Path(args.dest) if args.dest else Path(tempfile.mkdtemp(prefix="recall-fixture-")) / "repo"
    root = build_fixture(destination)

    print(f"Prepared repository: {root}")
    print("Nothing was pushed. To publish it for a recall run:")
    print()
    print(f'  cd "{root}"')
    print(f"  git remote add origin https://github.com/<your-account>/{FIXTURE_NAME}.git")
    print("  git push -u origin main")
    print()
    print("Then audit and score:")
    print()
    print(f"  python agent.py --repo https://github.com/<your-account>/{FIXTURE_NAME} --output report.json")
    print("  python scripts/score.py report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
