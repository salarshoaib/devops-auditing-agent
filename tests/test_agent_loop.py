from __future__ import annotations

import json

from agent_loop import AuditorAgent
from llm import AssistantTurn
from repo import RepoTarget, Repository
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

    def chat(
        self,
        messages,
        tools=None,
        force_tool=None,
        temperature=0.0,
        retries=None,
    ):
        self.calls.append(
            {"messages": list(messages), "force_tool": force_tool, "retries": retries}
        )
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
            _tool_turn("submit_report", {"violations": [VIOLATION]}, call_id="submit-2"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert result.steps == 3
    assert result.report["repository"] == "https://github.com/acme/demo"
    assert len(result.report["violations"]) == 1
    assert result.report["violations"][0]["rule_id"] == "SEC-001"
    assert [entry["tool"] for entry in result.trace] == [
        "read_file",
        "submit_report",
        "submit_report",
    ]


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
            _tool_turn("submit_report", {"violations": []}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-2"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert result.steps == 3  # prose, submission, then resubmit after the nudge


def test_agent_handles_text_protocol_tool_calls(sample_repo):
    call = json.dumps({"tool": "read_file", "arguments": {"path": "app.py"}})
    agent = _agent(
        sample_repo,
        [
            _text_turn(call),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-2"),
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
            _tool_turn("submit_report", {"violations": [VIOLATION]}, call_id="submit-3"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert result.steps == 3
    assert [entry["error"] for entry in result.trace] == [True, True, False]
    assert result.report["violations"][0]["evidence"] == VIOLATION["evidence"]


def test_agent_normalises_bad_paths(sample_repo):
    bad = dict(VIOLATION, file="ghost.py", line=99)
    agent = _agent(
        sample_repo,
        [
            _tool_turn("submit_report", {"violations": [bad]}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": [bad]}, call_id="submit-2"),
        ],
    )
    result = agent.run()
    violation = result.report["violations"][0]
    assert violation["file"] is None
    assert violation["line"] is None


def test_completeness_nudge_lists_unread_required_files(sample_repo):
    agent = _agent(
        sample_repo,
        [
            _tool_turn("submit_report", {"violations": []}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-2"),
        ],
    )
    result = agent.run()

    assert result.submitted is True
    assert result.report["violations"] == []
    nudges = [
        message["content"]
        for call in agent.llm.calls
        for message in call["messages"]
        if message.get("role") == "tool"
        and "read these files in full" in message.get("content", "")
    ]
    assert nudges
    text = nudges[-1]
    for path in (".github/workflows/ci.yml", "requirements.txt", ".gitignore"):
        assert path in text
    assert "not a finding requirement" in text


def test_completeness_nudge_is_silent_when_required_files_are_read(sample_repo):
    (sample_repo.root / "Dockerfile").write_text(
        "FROM python:3.12-slim\nUSER app\n", encoding="utf-8"
    )
    agent = _agent(
        sample_repo,
        [
            _tool_turn("read_file", {"path": "requirements.txt"}, call_id="r1"),
            _tool_turn("read_file", {"path": ".gitignore"}, call_id="r2"),
            _tool_turn("read_file", {"path": ".github/workflows/ci.yml"}, call_id="r3"),
            _tool_turn("read_file", {"path": "Dockerfile"}, call_id="r4"),
            _tool_turn("read_file", {"path": "config.py"}, call_id="r5"),
            _tool_turn("git_log", {"limit": 5}, call_id="g1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-2"),
        ],
    )
    result = agent.run()

    assert result.submitted is True
    assert agent.coverage_nudges == 0
    tool_messages = [
        message.get("content", "")
        for call in agent.llm.calls
        for message in call["messages"]
        if message.get("role") == "tool"
    ]
    assert not any("read these files in full" in text for text in tool_messages)


def test_completeness_nudge_is_skipped_after_the_deadline(sample_repo):
    agent = _agent(sample_repo, [])
    agent.time_limit = 60
    assert agent._coverage_review_request()

    agent._started = 0.0  # the wall-clock budget is long spent
    assert agent._unread_required_files()
    assert agent._coverage_review_request() is None


def test_completeness_nudge_lists_at_most_ten_files(tmp_path):
    root = tmp_path / "many"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "sub").mkdir()
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (root / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "on: push\njobs: {}\n", encoding="utf-8"
    )
    for index in range(12):
        (root / "sub" / f"config-{index}.toml").write_text("[app]\n", encoding="utf-8")
    repository = Repository(
        target=RepoTarget(owner="acme", name="many"),
        root=root.resolve(),
        head_sha="",
        branch="main",
        commit_date="",
    )
    agent = _agent(repository, [])

    unread = agent._unread_required_files()
    assert len(unread) > 10  # more required files than the listing cap

    request = agent._required_reading_request()
    assert request
    listing = request.split("read these files in full: ", 1)[1].split(". They exist", 1)[0]
    listed = [item.strip() for item in listing.split(",") if item.strip()]

    assert len(listed) == 10
    # Repo-root files come first, then workflow files, then everything else.
    assert listed[0] == ".gitignore"
    assert listed[1] == "requirements.txt"
    assert listed[2] == ".github/workflows/ci.yml"
    listed_sub = [item for item in listed if item.startswith("sub/")]
    assert all(item.startswith("sub/") for item in listed[3:])
    assert len(listed_sub) == 7  # only part of the sub-directory tail is shown
    assert len(listed_sub) < len([u for u in unread if u.startswith("sub/")])


def test_no_completeness_nudge_after_the_deadline(sample_repo):
    agent = _agent(
        sample_repo,
        [
            _tool_turn("submit_report", {"violations": []}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-2"),
        ],
    )
    agent.time_limit = 1e-6  # the budget is spent before the first step
    result = agent.run()

    assert result.submitted is True
    tool_messages = [
        message.get("content", "")
        for call in agent.llm.calls
        for message in call["messages"]
        if message.get("role") == "tool"
    ]
    assert not any("read these files in full" in text for text in tool_messages)


def test_agent_nudges_workflow_review_before_accepting(sample_repo):
    agent = _agent(
        sample_repo,
        [
            _tool_turn("read_file", {"path": ".github/workflows/ci.yml"}),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-2"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-3"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert result.steps == 4
    assert [entry["tool"] for entry in result.trace] == [
        "read_file",
        "submit_report",
        "submit_report",
        "submit_report",
    ]
    assert [entry["error"] for entry in result.trace] == [False, True, True, False]
    assert "triggers" in result.trace[1]["result_preview"]
    assert result.report["violations"] == []


def test_agent_nudges_workflow_review_even_when_a_workflow_has_a_finding(sample_repo):
    workflow_finding = dict(
        VIOLATION,
        rule_id="SEC-003",
        category="security",
        severity="high",
        file=".github/workflows/ci.yml",
        line=3,
        description="Workflow grants write-all permissions",
        evidence="permissions: write-all",
    )
    agent = _agent(
        sample_repo,
        [
            _tool_turn("read_file", {"path": ".github/workflows/ci.yml"}),
            _tool_turn("submit_report", {"violations": [workflow_finding]}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": [workflow_finding]}, call_id="submit-2"),
            _tool_turn("submit_report", {"violations": [workflow_finding]}, call_id="submit-3"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert agent.workflow_nudges == 1
    assert [entry["error"] for entry in result.trace] == [False, True, True, False]
    assert result.report["violations"][0]["rule_id"] == "SEC-003"


def test_workflow_nudge_lists_every_read_workflow_and_the_checklist(sample_repo):
    (sample_repo.root / ".github" / "workflows" / "release.yml").write_text(
        "name: Release\non: push\njobs: {}\n", encoding="utf-8"
    )
    finding = dict(
        VIOLATION,
        rule_id="SEC-003",
        category="security",
        file=".github/workflows/ci.yml",
        line=3,
        description="Workflow grants write-all permissions",
        evidence="permissions: write-all",
    )
    agent = _agent(
        sample_repo,
        [
            _tool_turn("read_file", {"path": ".github/workflows/ci.yml"}, call_id="r1"),
            _tool_turn("read_file", {"path": ".github/workflows/release.yml"}, call_id="r2"),
            _tool_turn("submit_report", {"violations": [finding]}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": [finding]}, call_id="submit-2"),
            _tool_turn("submit_report", {"violations": [finding]}, call_id="submit-3"),
        ],
    )
    result = agent.run()

    assert result.submitted is True
    nudge = [
        message["content"]
        for call in agent.llm.calls
        for message in call["messages"]
        if message.get("role") == "tool" and "workflow file you read" in message.get("content", "")
    ][-1]
    assert ".github/workflows/ci.yml" in nudge
    assert ".github/workflows/release.yml" in nudge
    for element in ("triggers", "permissions", "action references", "untrusted event data",
                    "piped", "version pins", "credentials", "cannot fail the job"):
        assert element in nudge
    assert "A file with no defects needs no output" in nudge


def test_coverage_nudge_lists_only_unread_candidates(sample_repo):
    agent = _agent(
        sample_repo,
        [
            _tool_turn("read_file", {"path": "config.py"}, call_id="r1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-2"),
        ],
    )
    result = agent.run()

    assert result.submitted is True
    assert agent.coverage_nudges == 1
    nudge = [
        message["content"]
        for call in agent.llm.calls
        for message in call["messages"]
        if message.get("role") == "tool"
        and "read these files in full" in message.get("content", "")
    ][-1]
    assert "requirements.txt" in nudge
    assert "config.py" not in nudge
    assert result.report["violations"] == []


def test_coverage_nudge_caps_candidate_list(sample_repo):
    for index in range(20):
        (sample_repo.root / f"settings_{index}.ini").write_text("x = 1\n", encoding="utf-8")
    agent = _agent(sample_repo, [])
    assert len(agent._unread_candidates()) == 12


def test_coverage_nudge_asks_for_git_log_when_history_not_seen(sample_repo):
    agent = _agent(
        sample_repo,
        [
            _tool_turn("read_file", {"path": "config.py"}, call_id="r1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-2"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    nudge = [
        message["content"]
        for call in agent.llm.calls
        for message in call["messages"]
        if message.get("role") == "tool" and "git_log" in message.get("content", "")
    ][-1]
    assert "Call `git_log` once" in nudge
    assert "limit" in nudge
    assert "30" in nudge


def test_coverage_nudge_omits_git_log_after_history_was_read(sample_repo):
    agent = _agent(
        sample_repo,
        [
            _tool_turn("read_file", {"path": "requirements.txt"}, call_id="r1"),
            _tool_turn("read_file", {"path": ".gitignore"}, call_id="r2"),
            _tool_turn("read_file", {"path": ".github/workflows/ci.yml"}, call_id="r3"),
            _tool_turn("git_log", {"limit": 5}, call_id="g1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-2"),
            _tool_turn("submit_report", {"violations": []}, call_id="submit-3"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    nudges = [
        message["content"]
        for call in agent.llm.calls
        for message in call["messages"]
        if message.get("role") == "tool" and "not findings" in message.get("content", "")
    ]
    assert nudges
    assert "config.py" in nudges[-1]
    assert "git_log" not in nudges[-1]


def test_wall_clock_limit_finalises_with_partial_report(sample_repo):
    agent = _agent(
        sample_repo,
        [_tool_turn("submit_report", {"violations": []}, call_id="submit-final")],
        max_steps=10,
    )
    agent.time_limit = 1e-9
    result = agent.run()
    assert any("Wall-clock limit" in note for note in result.notes)


def test_workflow_nudge_caps_the_file_list(sample_repo):
    agent = _agent(sample_repo, [])
    agent._workflow_files_read = {
        f".github/workflows/w{index:02d}.yml" for index in range(20)
    }
    listed = agent._workflow_review_list()
    assert len(listed) == 15


def test_trace_records_submitted_violations(sample_repo):
    raw = dict(VIOLATION, description="Hardcoded key AKIAIOSFODNN7EXAMPLE in app.py")
    agent = _agent(
        sample_repo,
        [
            _tool_turn("submit_report", {"violations": [raw]}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": [raw]}, call_id="submit-2"),
        ],
    )
    result = agent.run()
    entry = result.trace[-1]
    assert entry["tool"] == "submit_report"
    assert entry["arguments"]["violations"] == [
        {
            "rule_id": "SEC-001",
            "file": "app.py",
            "line": 2,
            "description": "Hardcoded key [REDACTED] in app.py",
        }
    ]
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(result.trace)


def test_trace_records_violations_during_finalisation(sample_repo):
    turns = [_tool_turn("read_file", {"path": "app.py"}, call_id=f"c{index}") for index in range(3)]
    turns.append(_tool_turn("submit_report", {"violations": [VIOLATION]}, call_id="submit-final"))
    agent = _agent(sample_repo, turns, max_steps=3)
    result = agent.run()
    entry = [item for item in result.trace if item["tool"] == "submit_report"][-1]
    assert entry["arguments"]["violations"][0]["rule_id"] == "SEC-001"
    assert entry["arguments"]["violations"][0]["file"] == "app.py"


def test_agent_nudges_unaddressed_workflow_file(sample_repo):
    (sample_repo.root / ".github" / "workflows" / "release.yml").write_text(
        "name: Release\non: push\njobs: {}\n", encoding="utf-8"
    )
    workflow_finding = dict(
        VIOLATION,
        rule_id="SEC-003",
        category="security",
        severity="high",
        file=".github/workflows/ci.yml",
        line=3,
        description="Workflow grants write-all permissions",
        evidence="permissions: write-all",
    )
    agent = _agent(
        sample_repo,
        [
            _tool_turn("read_file", {"path": ".github/workflows/ci.yml"}, call_id="r1"),
            _tool_turn("read_file", {"path": ".github/workflows/release.yml"}, call_id="r2"),
            _tool_turn("submit_report", {"violations": [workflow_finding]}, call_id="submit-1"),
            _tool_turn("submit_report", {"violations": [workflow_finding]}, call_id="submit-2"),
            _tool_turn("submit_report", {"violations": [workflow_finding]}, call_id="submit-3"),
        ],
    )
    result = agent.run()
    assert result.submitted is True
    assert [entry["error"] for entry in result.trace] == [False, False, True, True, False]
    assert agent.workflow_nudges == 1
