from __future__ import annotations

from pathlib import Path

import pytest

from repo import RepoTarget, Repository
from redaction import REDACTED, redact_secrets
from tools import ToolBox, ToolError, tool_schemas


@pytest.fixture
def toolbox(sample_repo):
    return ToolBox(repository=sample_repo)


def test_list_files_lists_repository(toolbox):
    output = toolbox.list_files()
    assert "app.py" in output
    assert "tests/test_app.py" in output
    assert ".github/workflows/ci.yml" in output


def test_list_files_pattern(toolbox):
    output = toolbox.list_files(pattern="*.yml")
    assert "ci.yml" in output
    assert "app.py" not in output


def test_list_files_narrowed_to_directory(toolbox):
    output = toolbox.list_files(path="tests")
    assert "test_app.py" in output
    assert "app.py (" not in output.replace("test_app.py (", "")


def test_list_files_missing_path(toolbox):
    assert "ERROR" in toolbox.execute("list_files", {"path": "does-not-exist"})


def test_read_file_numbers_lines(toolbox):
    output = toolbox.read_file("app.py", 1, 2)
    assert "1: import os" in output.replace("     ", "")
    assert "lines 1-2 of 7" in output


def test_read_file_respects_range_and_truncation(toolbox):
    output = toolbox.read_file("app.py", 2, 2)
    assert "API_KEY" in output
    assert "import os" not in output


def test_read_file_rejects_traversal(toolbox):
    result = toolbox.execute("read_file", {"path": "../secret.txt"})
    assert result.startswith("ERROR:")


def test_read_file_missing(toolbox):
    result = toolbox.execute("read_file", {"path": "nope.py"})
    assert result.startswith("ERROR:")


def test_read_file_directory_points_to_listing(toolbox):
    output = toolbox.read_file("tests")
    assert "directory" in output.lower()


def test_search_code_regex(toolbox):
    output = toolbox.search_code(r"DEBUG\s*=")
    assert "app.py:3" in output


def test_search_code_glob(toolbox):
    output = toolbox.search_code("debug", glob="*.yml")
    assert "No matches" in output


def test_search_code_plain_pattern_is_a_substring_search(toolbox):
    output = toolbox.search_code("return 1")
    assert "app.py:7" in output
    assert "literal substring" in output


def test_search_code_is_case_insensitive_by_default(toolbox):
    output = toolbox.search_code("debug")
    assert "app.py:3" in output


def test_search_code_uppercase_pattern_matches_lowercase_text(toolbox):
    output = toolbox.search_code("DEBUG")
    assert "app.py:6" in output


def test_search_code_invalid_regex_returns_an_error(toolbox):
    result = toolbox.execute("search_code", {"pattern": "C:\\Users"})
    assert result.startswith("ERROR:")
    assert "Invalid regular expression" in result
    assert "Fix the pattern" in result


def test_search_code_windows_path_pattern(toolbox):
    output = toolbox.search_code(r"[A-Za-z]:[\\/]+Users")
    assert "config.py:1" in output


def test_search_code_redacts_secret_values(toolbox):
    output = toolbox.search_code("API_KEY")
    assert "app.py:2" in output
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in output
    assert REDACTED in output


def test_search_code_empty_pattern(toolbox):
    result = toolbox.execute("search_code", {"pattern": ""})
    assert result.startswith("ERROR:")


def test_git_log_returns_history(toolbox):
    output = toolbox.git_log(limit=5)
    assert "Change helper" in output
    assert "Initial commit" in output


def test_git_log_path_filter(toolbox):
    output = toolbox.git_log(limit=5, path="app.py")
    assert "Initial commit" in output
    assert "Change helper" not in output


def test_git_log_missing_path(toolbox):
    result = toolbox.execute("git_log", {"path": "missing.py"})
    assert result.startswith("ERROR:")


def test_duplicate_calls_are_cached(toolbox):
    first = toolbox.execute("read_file", {"path": "app.py", "start_line": 1, "end_line": 3})
    second = toolbox.execute("read_file", {"path": "app.py", "start_line": 1, "end_line": 3})
    assert second.startswith("[duplicate call")
    assert first in second


def test_unknown_tool_is_an_error(toolbox):
    result = toolbox.execute("delete_repo", {})
    assert result.startswith("ERROR:")


def test_candidate_files_include_build_and_container_files(toolbox, sample_repo):
    root = sample_repo.root
    (root / "Dockerfile").write_text("FROM python:latest\n", encoding="utf-8")
    (root / "Dockerfile.dev").write_text("FROM python:3.12\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "Makefile").write_text("all:\n\techo hi\n", encoding="utf-8")

    candidates = toolbox.candidate_files()

    container = candidates["build and container files"]
    assert "Dockerfile" in container
    assert "Dockerfile.dev" in container
    assert "docker-compose.yml" in container
    assert "Makefile" in container


def test_candidate_files_do_not_treat_ordinary_sources_as_container_files(
    toolbox, sample_repo
):
    container = toolbox.candidate_files()["build and container files"]
    assert "app.py" not in container
    assert "config.py" not in container


def test_candidate_files_are_tree_derived(toolbox, sample_repo):
    root = sample_repo.root
    (root / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text("{}\n", encoding="utf-8")
    (root / "tests" / "settings_helper.py").write_text("value = 1\n", encoding="utf-8")

    candidates = toolbox.candidate_files()

    assert "requirements.txt" in candidates["dependency manifests"]
    assert "requirements-dev.txt" in candidates["dependency manifests"]
    assert "config.py" in candidates["configuration-like files"]
    assert ".vscode/settings.json" in candidates["editor/IDE settings"]
    flattened = [path for paths in candidates.values() for path in paths]
    assert not any(path.startswith("tests/") for path in flattened)


def test_overview_lists_candidates_but_labels_them_as_not_findings(toolbox):
    overview = toolbox.overview()
    assert "Files worth inspecting" in overview
    assert "not findings" in overview
    assert "requirements.txt" in overview
    assert "config.py" in overview


def test_overview_reports_markers(toolbox):
    overview = toolbox.overview()
    assert "acme/demo" in overview
    assert "README" in overview
    assert "CI workflows" in overview
    assert "ci.yml" in overview


def test_overview_reports_manifest_signals(toolbox):
    overview = toolbox.overview()
    assert "requirements.txt: 2 direct entries, 2 without an exact == pin" in overview


def test_overview_reads_package_json(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"express": "^4.0.0"}, "devDependencies": {"jest": "^29"}}',
        encoding="utf-8",
    )
    repo = Repository(
        target=RepoTarget(owner="acme", name="node"),
        root=tmp_path,
        head_sha="deadbeef",
        branch="main",
        commit_date="2026-01-01T00:00:00+00:00",
    )
    toolbox = ToolBox(repository=repo)
    assert "package.json: 1 runtime, 1 dev dependencies" in toolbox.overview()


def test_tool_schemas_are_well_formed():
    schemas = tool_schemas()
    names = [schema["function"]["name"] for schema in schemas]
    assert names == ["list_files", "read_file", "search_code", "git_log", "submit_report"]
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["type"] == "object"
    submit = schemas[-1]["function"]["parameters"]
    assert submit["required"] == ["violations"]


def test_redact_secrets_variants():
    assert "AKIA" not in redact_secrets("AWS key AKIAIOSFODNN7EXAMPLE")
    assert "ghp_" not in redact_secrets("token ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    masked = redact_secrets('password = "hunter2secret"')
    assert "hunter2secret" not in masked
    assert "password" in masked
    # Environment references are not secrets.
    assert "${DATABASE_URL}" in redact_secrets("url = ${DATABASE_URL}")


def test_search_code_rejects_empty_pattern(toolbox):
    with pytest.raises(ToolError) as excinfo:
        toolbox.search_code("   ")
    message = str(excinfo.value)
    assert "non-empty pattern" in message


def test_git_log_on_empty_history_does_not_crash(tmp_path):
    import subprocess

    root = tmp_path / "historyless"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    repo = Repository(
        target=RepoTarget(owner="acme", name="historyless"),
        root=root.resolve(),
        head_sha="",
        branch="main",
        commit_date="2026-01-01T00:00:00+00:00",
    )
    toolbox = ToolBox(repository=repo)
    output = toolbox.git_log(limit=5)
    assert "No commits found" in output


def test_required_reading_files_come_from_the_tree(sample_repo):
    (sample_repo.root / "Dockerfile").write_text(
        "FROM python:3.12-slim\nUSER app\n", encoding="utf-8"
    )
    (sample_repo.root / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    (sample_repo.root / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    toolbox = ToolBox(repository=sample_repo)

    required = toolbox.required_reading_files()

    assert ".github/workflows/ci.yml" in required
    assert "requirements.txt" in required
    assert "requirements-dev.txt" in required
    assert ".gitignore" in required
    assert "Dockerfile" in required
    # Build files that are not container definitions are candidates, not required reading.
    assert "Makefile" not in required


def test_configuration_shaped_names_are_config_candidates(tmp_path: Path):
    root = tmp_path / "cfg"
    (root / "settings").mkdir(parents=True)
    (root / "local.cfg").write_text("[app]\ndebug = true\n", encoding="utf-8")
    (root / "secrets.yml").write_text("password: hunter2fake\n", encoding="utf-8")
    (root / "settings" / "application.properties").write_text(
        "db.password=hunter2fake\n", encoding="utf-8"
    )
    repository = Repository(
        target=RepoTarget(owner="acme", name="cfg"),
        root=root.resolve(),
        head_sha="",
        branch="main",
        commit_date="",
    )

    candidates = ToolBox(repository=repository).candidate_files()["configuration-like files"]

    assert "local.cfg" in candidates
    assert "secrets.yml" in candidates
    assert "settings/application.properties" in candidates


def test_example_tree_manifests_stay_candidates_but_are_not_required(tmp_path: Path):
    root = tmp_path / "ex"
    (root / "examples" / "demo").mkdir(parents=True)
    (root / "examples" / "demo" / "requirements.txt").write_text(
        "flask\n", encoding="utf-8"
    )
    (root / "requirements.txt").write_text("requests\n", encoding="utf-8")
    repository = Repository(
        target=RepoTarget(owner="acme", name="ex"),
        root=root.resolve(),
        head_sha="",
        branch="main",
        commit_date="",
    )
    toolbox = ToolBox(repository=repository)

    required = toolbox.required_reading_files()
    candidates = toolbox.candidate_files()

    # The example tree must not consume required-reading budget...
    assert "examples/demo/requirements.txt" not in required
    assert "requirements.txt" in required
    # ...but it stays a candidate the model may still choose to open.
    assert "examples/demo/requirements.txt" in candidates["dependency manifests"]


def test_source_files_named_after_settings_are_not_required_reading(tmp_path: Path):
    root = tmp_path / "code"
    root.mkdir()
    (root / "settings.py").write_text("DEBUG = True\n", encoding="utf-8")
    (root / "local.cfg").write_text("[app]\ndebug = true\n", encoding="utf-8")
    repository = Repository(
        target=RepoTarget(owner="acme", name="code"),
        root=root.resolve(),
        head_sha="",
        branch="main",
        commit_date="",
    )
    toolbox = ToolBox(repository=repository)

    required = toolbox.required_reading_files()
    candidates = toolbox.candidate_files()["configuration-like files"]

    assert "settings.py" not in required  # source code, not a config file
    assert "local.cfg" in required
    assert "settings.py" in candidates  # still worth a look as a candidate
