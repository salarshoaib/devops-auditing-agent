from __future__ import annotations

import json

from agent_loop import AuditorAgent
from llm import AssistantTurn
from tools import ToolBox


def _tool_turn(name: str, arguments: dict, call_id: str = "call-1") -> AssistantTurn:
    raw = json.dumps(arguments)
    return AssistantTurn(
        content="",
        tool_calls=[{"id": call_id, "name": name, "arguments": arguments, "raw_arguments": raw}],
        raw_message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": raw},
                }
            ],
        },
    )


def _text_turn(content: str) -> AssistantTurn:
    return AssistantTurn(content=content, raw_message={"role": "assistant", "content": content})


class ScriptedLLM:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []
        self.forced = []

    def chat(self, messages, tools=None, force_tool=None, temperature=0.0):
        self.calls.append({"messages": list(messages), "force_tool": force_tool})
        self.forced.append(force_tool)
        if not self.turns:
            return _text_turn("out of turns")
        return self.turns.pop(0)


def _agent(sample_repo, turns, max_steps=10):
    return AuditorAgent(
        toolbox=ToolBox(repository=sample_repo),
        llm=ScriptedLLM(turns),
        max_steps=max_steps,
        log=lambda message: None,
    )


VIOLATION = {
    "rule_id": "SEC-001",
    "category": "security",
    "severity": "critical",
    "file": "app.py",
    "line": 2,
    "description": "Hardcoded API key committed in source",
    "evidence": "app.py line 2 assigns a literal API key value",
    "recommendation": "Load the key from an environment variable and rotate it",
}


def test_agent_calls_tool_then_submits_report(sample_repo):
    agent = _agent(
        sample_repo,
        [
            _tool_turn("read_file", {"path": "app.py", "start_line": 1, "end_line": 5}),
            _tool_turn("submit_report", {"violations": [VIOLATION]}, call_id="submit-1"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert result.steps == 2
    assert result.report["repository"] == "https://github.com/acme/demo"
    assert len(result.report["violations"]) == 1
    assert result.report["violations"][0]["rule_id"] == "SEC-001"
    assert [entry["tool"] for entry in result.trace] == ["read_file", "submit_report"]


def test_agent_accepts_report_from_text(sample_repo):
    payload = {"violations": [VIOLATION]}
    agent = _agent(sample_repo, [_text_turn(f"```json\n{json.dumps(payload)}\n```")])
    result = agent.run()
    assert result.submitted is False
    assert result.report["violations"][0]["rule_id"] == "SEC-001"


def test_agent_recovers_from_invalid_submission(sample_repo):
    agent = _agent(
        sample_repo,
        [
            _tool_turn("submit_report", {"violations": "not-a-list"}),
            _tool_turn("submit_report", {"violations": [VIOLATION]}, call_id="submit-2"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert result.steps == 2
    assert len(result.report["violations"]) == 1


def test_agent_nudges_prose_replies(sample_repo):
    agent = _agent(
        sample_repo,
        [
            _text_turn("I will start by looking at the repository."),
            _tool_turn("submit_report", {"violations": []}),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert result.steps == 2  # prose, then nudge + submission


def test_agent_handles_text_protocol_tool_calls(sample_repo):
    call = json.dumps({"tool": "read_file", "arguments": {"path": "app.py"}})
    agent = _agent(
        sample_repo,
        [
            _text_turn(call),
            _tool_turn("submit_report", {"violations": []}),
        ],
    )
    result = agent.run()
    assert result.trace[0]["tool"] == "read_file"
    assert result.submitted is True


def test_agent_forces_finalisation_at_budget(sample_repo):
    turns = [_tool_turn("read_file", {"path": "app.py"}, call_id=f"c{index}") for index in range(3)]
    turns.append(_tool_turn("submit_report", {"violations": []}, call_id="submit-final"))
    agent = _agent(sample_repo, turns, max_steps=3)
    result = agent.run()
    assert result.submitted is True
    assert "submit_report" in [entry["tool"] for entry in result.trace]


def test_agent_reports_empty_list_when_model_gives_nothing(sample_repo):
    agent = _agent(sample_repo, [_text_turn("No JSON here."), _text_turn("Still nothing."), _text_turn("Nope.")])
    result = agent.run()
    assert result.report["violations"] == []
    assert result.submitted is False


def test_agent_requests_evidence_before_accepting(sample_repo):
    no_evidence = dict(VIOLATION, evidence="")
    agent = _agent(
        sample_repo,
        [
            _tool_turn("submit_report", {"violations": [no_evidence]}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": [VIOLATION]}, call_id="submit-2"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert result.steps == 2
    assert [entry["error"] for entry in result.trace] == [True, False]
    assert result.report["violations"][0]["evidence"] == VIOLATION["evidence"]


def test_agent_normalises_bad_paths(sample_repo):
    bad = dict(VIOLATION, file="ghost.py", line=99)
    agent = _agent(sample_repo, [_tool_turn("submit_report", {"violations": [bad]})])
    result = agent.run()
    violation = result.report["violations"][0]
    assert violation["file"] is None
    assert violation["line"] is None
