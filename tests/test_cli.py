from __future__ import annotations

import json

import agent as agent_module
from agent_loop import AuditResult
from repo import RepoTarget


class FakeRepository:
    target = RepoTarget(owner="acme", name="demo")
    head_sha = "0" * 40
    branch = "main"

    def __init__(self) -> None:
        self.cleaned = False

    def cleanup(self) -> None:
        self.cleaned = True


class StubAgent:
    created = 0
    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        StubAgent.created += 1
        StubAgent.last_kwargs = kwargs

    def run(self) -> AuditResult:
        return AuditResult(
            report={"repository": "https://github.com/acme/demo", "violations": []},
            steps=4,
            submitted=True,
            notes=[],
            trace=[{"step": 1, "tool": "list_files", "arguments": {}, "result_preview": "x", "error": False}],
        )


def _no_env(monkeypatch):
    monkeypatch.setattr(agent_module, "load_environment", lambda: None)


def test_invalid_repo_url_fails_before_cloning(monkeypatch, tmp_path, capsys):
    _no_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    output = tmp_path / "report.json"
    code = agent_module.main(["--repo", "https://gitlab.com/acme/demo", "--output", str(output)])
    assert code == 1
    assert "error:" in capsys.readouterr().err
    assert not output.exists()


def test_missing_api_key_fails_with_helpful_message(monkeypatch, tmp_path, capsys):
    _no_env(monkeypatch)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    code = agent_module.main(["--repo", "acme/demo", "--output", str(tmp_path / "report.json")])
    assert code == 1
    assert "LLM_API_KEY" in capsys.readouterr().err


def test_successful_run_writes_report_and_trace(monkeypatch, tmp_path, capsys):
    _no_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    fake_repo = FakeRepository()
    monkeypatch.setattr(agent_module, "clone_repository", lambda url: fake_repo)
    monkeypatch.setattr(agent_module, "AuditorAgent", StubAgent)

    output = tmp_path / "nested" / "report.json"
    trace = tmp_path / "trace.json"
    code = agent_module.main(
        ["--repo", "https://github.com/acme/demo", "--output", str(output), "--trace", str(trace)]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report == {"repository": "https://github.com/acme/demo", "violations": []}
    trace_payload = json.loads(trace.read_text(encoding="utf-8"))
    assert trace_payload["tool_calls"][0]["tool"] == "list_files"
    assert fake_repo.cleaned is True
    assert "Wrote" in capsys.readouterr().out


def test_max_steps_resolution(monkeypatch):
    monkeypatch.delenv("MAX_STEPS", raising=False)
    assert agent_module._resolve_max_steps(None) == agent_module.DEFAULT_MAX_STEPS
    monkeypatch.setenv("MAX_STEPS", "12")
    assert agent_module._resolve_max_steps(None) == 12
    assert agent_module._resolve_max_steps(5) == 5
    monkeypatch.setenv("MAX_STEPS", "not-a-number")
    assert agent_module._resolve_max_steps(None) == agent_module.DEFAULT_MAX_STEPS
