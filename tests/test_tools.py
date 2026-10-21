from __future__ import annotations

import pytest

from secrets import REDACTED, redact_secrets
from tools import ToolBox, tool_schemas


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


def test_search_code_literal_fallback(toolbox):
    output = toolbox.search_code("print(")
    assert "app.py:6" in output


def test_search_code_invalid_regex_falls_back_to_literal(toolbox):
    output = toolbox.search_code("C:\\Users")
    assert "config.py" in output


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


def test_overview_reports_markers(toolbox):
    overview = toolbox.overview()
    assert "acme/demo" in overview
    assert "README" in overview
    assert "CI workflows" in overview
    assert "ci.yml" in overview


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
