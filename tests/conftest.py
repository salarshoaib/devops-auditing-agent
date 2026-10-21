from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repo import RepoTarget, Repository  # noqa: E402


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def sample_repo(tmp_path: Path) -> Repository:
    """A small git repository with realistic defects for tool tests."""
    root = tmp_path / "demo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src").mkdir()

    (root / "README.md").write_text("# Demo\n\nA demo project.\n", encoding="utf-8")
    (root / "requirements.txt").write_text("requests>=2.0\nflask\n", encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (root / "app.py").write_text(
        "import os\n"
        "API_KEY = \"sk-abcdefghijklmnopqrstuvwxyz012345\"\n"
        "DEBUG = True\n"
        "\n"
        "def run():\n"
        "    print('debug')\n"
        "    return 1\n",
        encoding="utf-8",
    )
    (root / "config.py").write_text(
        "DATA_DIR = r'C:\\Users\\bob\\project\\data'\n", encoding="utf-8"
    )
    (root / "tests" / "test_app.py").write_text(
        "from app import run\n\n\ndef test_run():\n    assert run() == 1\n",
        encoding="utf-8",
    )
    (root / "src" / "util.py").write_text("def helper():\n    return 'ok'\n", encoding="utf-8")
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\non: push\npermissions: write-all\njobs:\n"
        "  test:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - run: pytest || true\n        continue-on-error: true\n",
        encoding="utf-8",
    )

    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "Initial commit")
    (root / "src" / "util.py").write_text("def helper():\n    return 'changed'\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "Change helper")

    return Repository(
        target=RepoTarget(owner="acme", name="demo"),
        root=root.resolve(),
        head_sha="0123456789abcdef",
        branch="main",
        commit_date="2026-01-01T00:00:00+00:00",
    )
