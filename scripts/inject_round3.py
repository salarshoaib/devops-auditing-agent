"""Prepare a held-out repository with seeded defects (round 3).

Same flow as :mod:`inject_round2`: shallow-clone a public project, replace its
history with a baseline commit plus a seeded-defect commit, and print the path of
the prepared clone. It **never pushes**; publishing is a manual step.

The seeded variants differ from the earlier rounds (different upstream project,
different defect shapes) so a recall run measures the agent rather than a
memorised fixture. All seeded credential material is obviously fake.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from inject_defects import _git, _rmtree, _write

UPSTREAM = "https://github.com/psf/requests"
FIXTURE_NAME = "requests-recall-round3"

CI = """\
name: CI
on: pull_request_target

permissions: write-all

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.ref }}
      - run: python -m pytest
"""

COMMENT = """\
name: Comment
on: issue_comment

jobs:
  reply:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: eval "${{ github.event.comment.body }}"
"""

DOCS = """\
name: Docs
on:
  push:
    branches: [nonexistent-branch]

jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest -x || echo "tests skipped"
"""

DOCKERFILE = """\
FROM python:latest

ENV API_SECRET=sk-FAKEFAKEFAKEFAKEFAKEFAKEFAKE1234

USER root

COPY . /app
WORKDIR /app
CMD ["python", "-c", "print('ok')"]
"""

IDEA_WORKSPACE = """\
<?xml version="1.0" encoding="UTF-8"?>
<project version="4">
  <component name="PropertiesComponent">
    <option name="settings.editorPath" value="/Users/dev/projects/app" />
  </component>
</project>
"""

LOCAL_SETTINGS = """\
DEBUG = True
password = hunter2fake
"""

PIPFILE = """\
[packages]
requests = "*"
flask = "*"

[dev-packages]
pytest = "*"
"""

ENV_PRODUCTION = "DB_PASSWORD=fakefake123\n"

GITIGNORE_APPENDIX = """\

# seeded: environment files are intentionally tracked here
!.env
!.env.*
"""


FILES: dict[str, str] = {
    ".github/workflows/ci.yml": CI,
    ".github/workflows/comment.yml": COMMENT,
    ".github/workflows/docs.yml": DOCS,
    "Dockerfile": DOCKERFILE,
    ".idea/workspace.xml": IDEA_WORKSPACE,
    "settings/local.cfg": LOCAL_SETTINGS,
    "Pipfile": PIPFILE,
    ".env.production": ENV_PRODUCTION,
}

FORCE_ADDED = (".idea/workspace.xml", ".env.production")


def _append_gitignore(root: Path) -> None:
    """Append negations so environment files are no longer ignored."""
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing.rstrip("\n") + GITIGNORE_APPENDIX, encoding="utf-8")


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

    for relative, content in FILES.items():
        _write(destination, relative, content)
    _append_gitignore(destination)

    _git(destination, "add", "-f", *FORCE_ADDED)
    _git(destination, "add", "-A")
    _git(destination, "commit", "-q", "-m", "Seed round-3 recall defects (FAKE data)")
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
        else Path(tempfile.mkdtemp(prefix="recall-round3-")) / "repo"
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
        "--output inj_round3.json --trace inj_round3_trace.json"
    )
    print("  python scripts/score.py inj_round3.json scripts/truth_round3.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
