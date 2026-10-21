from __future__ import annotations

import pytest

from repo import (
    RepoError,
    RepoTarget,
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
