"""Prepare a held-out repository with seeded defects (round 2).

Same flow as :mod:`inject_defects`: shallow-clone a public project, replace its
history with a baseline commit plus a seeded-defect commit, and print the path of
the prepared clone. It **never pushes**; publishing is a manual step.

All seeded credential material is obviously fake.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from inject_defects import _git, _rmtree, _write

UPSTREAM = "https://github.com/pallets/click"
FIXTURE_NAME = "click-recall-round2"

RELEASE = """\
name: Release
on:
  push:
    tags:
      - "v*"

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: curl -sSL https://example.com/install.sh | bash
"""

GREET = """\
name: Greet
on: pull_request

jobs:
  greet:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "Title: ${{ github.event.pull_request.title }}"
"""

LINT = """\
name: Lint
on:
  push:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    permissions: write-all
    steps:
      - uses: actions/checkout@v4
      - run: pip install ruff black
"""

DEPLOY = """\
name: Deploy
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: some-org/deploy-action@main
        env:
          AWS_SECRET_ACCESS_KEY: AKIAFAKEFAKEFAKEFAKE
"""

VSCODE = (
    '{"python.defaultInterpreterPath": '
    '"C:\\\\Users\\\\dev\\\\venv\\\\Scripts\\\\python.exe"}\n'
)

PROD = """\
DEBUG = True
DATABASE_URL = "postgres://admin:hunter2fake@10.1.2.3/db"
"""

REQUIREMENTS_DEV = """\
pytest
flask>=0
"""


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

    _write(destination, ".github/workflows/release.yml", RELEASE)
    _write(destination, ".github/workflows/greet.yml", GREET)
    _write(destination, ".github/workflows/lint.yml", LINT)
    _write(destination, ".github/workflows/deploy.yml", DEPLOY)
    _write(destination, ".vscode/settings.json", VSCODE)
    _write(destination, "config/prod.py", PROD)
    _write(destination, "requirements-dev.txt", REQUIREMENTS_DEV)

    _git(destination, "add", "-f", ".vscode/settings.json")
    _git(destination, "add", "-A")
    _git(destination, "commit", "-q", "-m", "Seed round-2 recall defects (FAKE data)")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        help="Directory for the prepared clone (default: a new temp directory).",
    )
    args = parser.parse_args()

    destination = (
        Path(args.dest)
        if args.dest
        else Path(tempfile.mkdtemp(prefix="recall-round2-")) / "repo"
    )
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
    print(
        f"  python agent.py --repo https://github.com/<your-account>/{FIXTURE_NAME} "
        "--output inj_round2.json --trace inj_round2_trace.json"
    )
    print("  python scripts/score.py inj_round2.json scripts/truth_round2.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
