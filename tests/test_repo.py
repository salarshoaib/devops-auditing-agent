from __future__ import annotations

from pathlib import Path

import pytest

import repo as repo_module
from repo import (
    RepoError,
    RepoTarget,
    clone_repository,
    github_repo_url,
    iter_repo_files,
    parse_repo_url,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://github.com/acme/demo", RepoTarget("acme", "demo")),
        ("https://github.com/acme/demo.git", RepoTarget("acme", "demo")),
        ("https://www.github.com/acme/demo/", RepoTarget("acme", "demo")),
        ("git@github.com:acme/demo.git", RepoTarget("acme", "demo")),
        ("acme/demo", RepoTarget("acme", "demo")),
        ("  acme/demo  ", RepoTarget("acme", "demo")),
    ],
)
def test_parse_repo_url_variants(value, expected):
    assert parse_repo_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "https://gitlab.com/acme/demo",
        "https://github.com/acme",
        "not a url",
        "https://github.com/acme/demo/tree/main",
    ],
)
def test_parse_repo_url_rejects_invalid(value):
    with pytest.raises(RepoError):
        parse_repo_url(value)


def test_repo_target_urls():
    target = parse_repo_url("acme/demo")
    assert target.slug == "acme/demo"
    assert target.https_url == "https://github.com/acme/demo.git"
    assert github_repo_url("https://github.com/acme/demo.git") == "https://github.com/acme/demo"


def test_path_traversal_is_blocked(sample_repo):
    for bad in ["../outside.txt", "../../etc/passwd", "C:\\Windows\\win.ini", "/etc/passwd"]:
        with pytest.raises(ValueError):
            sample_repo.path_for(bad)


def test_git_directory_is_blocked(sample_repo):
    with pytest.raises(ValueError):
        sample_repo.path_for(".git/config")


def test_path_for_accepts_nested_path(sample_repo):
    resolved = sample_repo.path_for("src/util.py")
    assert resolved.name == "util.py"
    assert sample_repo.relative(resolved) == "src/util.py"


def test_iter_repo_files_skips_git(sample_repo):
    files = [sample_repo.relative(path) for path in iter_repo_files(sample_repo.root)]
    assert "app.py" in files
    assert all(".git/" not in name for name in files)


def _fake_run(returncode, stdout="", stderr=""):
    class Result:
        pass

    result = Result()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_clone_failure_names_url_cause_and_first_stderr_line(monkeypatch):
    def failing_run(args, **kwargs):
        return _fake_run(
            128,
            stderr=(
                "usage: git clone [<options>] [--] <repo> [<dir>]\n"
                "\n"
                "fatal: repository not found\n"
            ),
        )

    monkeypatch.setattr(repo_module.subprocess, "run", failing_run)
    with pytest.raises(RepoError) as excinfo:
        clone_repository("https://github.com/acme/missing")
    message = str(excinfo.value)
    assert "https://github.com/acme/missing.git" in message
    assert "does not exist, or it is private" in message
    assert "repository not found" in message
    assert "usage: git clone" not in message


def test_later_git_failure_uses_the_same_format(monkeypatch, sample_repo):
    def failing_run(args, **kwargs):
        return _fake_run(128, stderr="fatal: could not read Username for 'https://github.com'\n")

    monkeypatch.setattr(repo_module.subprocess, "run", failing_run)
    toolbox_repo = sample_repo
    with pytest.raises(RepoError) as excinfo:
        repo_module._run_git(
            ["rev-parse", "HEAD"],
            cwd=toolbox_repo.root,
            url="https://github.com/acme/demo.git",
        )
    message = str(excinfo.value)
    assert "git rev-parse failed" in message
    assert "https://github.com/acme/demo.git" in message
    assert "private or needs authentication" in message
    assert "could not read Username" in message
