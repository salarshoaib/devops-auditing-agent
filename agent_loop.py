"""The agent loop: observe -> reason -> select tool -> execute -> interpret."""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from llm import AssistantTurn, LLMClient, extract_json_object, parse_text_tool_call
from prompts import (
    FINALIZE_PROMPT,
    NUDGE_PROMPT,
    REPAIR_PROMPT,
    SYSTEM_PROMPT,
)
from reporting import build_report
from redaction import redact_secrets
from tools import ToolBox, is_submission_tool, tool_schemas

MAX_TOOL_CALLS_PER_STEP = 8
MAX_TOOL_OUTPUT_CHARS = 24000
MAX_STEPS_HARD_LIMIT = 80
MAX_EVIDENCE_REPAIRS = 1
MAX_WORKFLOW_NUDGES = 1
MAX_WORKFLOW_FILES_LISTED = 15
MAX_COVERAGE_NUDGES = 1
MAX_COVERAGE_FILES_LISTED = 12

_WORKFLOW_PATH = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")

WORKFLOW_REVIEW_PROMPT = (
    "Before submitting, re-check every workflow file you read: {files}.\n"
    "Check each file against this list and report each defect as its own violation "
    "with its own line number:\n"
    "(1) triggers - which events run it and whether untrusted input is involved;\n"
    "(2) permissions - at workflow level AND job level, and whether they are "
    "least-privilege;\n"
    "(3) action references - each pinned to a commit SHA, not a mutable tag or branch;\n"
    "(4) untrusted event data (github.event.*, github.head_ref, issue/PR titles, "
    "bodies, branch names) interpolated into a run: script;\n"
    "(5) remote scripts downloaded and piped to a shell;\n"
    "(6) packages installed without version pins;\n"
    "(7) credentials in env: blocks or run: steps;\n"
    "(8) test or validation steps that cannot fail the job.\n"
    "A file with no defects needs no output. If you already checked all of this, "
    "call submit_report again with the same report."
)

COVERAGE_REVIEW_PROMPT = (
    "Before submitting, open the relevant files you have not read yet: {files}. "
    "They are candidates detected from the file tree, not findings: dependency "
    "manifests (including secondary dev/test manifests), configuration files, and "
    "committed editor/IDE settings. Read the ones that matter for this repository "
    "and report any defects you find, each as its own violation. Ignore the rest; "
    "if none are relevant, call submit_report again with the same report."
)


@dataclass
class AuditResult:
    report: dict[str, Any]
    steps: int
    submitted: bool
    notes: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


class AuditorAgent:
    def __init__(
        self,
        toolbox: ToolBox,
        llm: LLMClient,
        max_steps: int = 30,
        verbose: bool = False,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.toolbox = toolbox
        self.llm = llm
        self.max_steps = max(3, min(int(max_steps or 30), MAX_STEPS_HARD_LIMIT))
        self.verbose = verbose
        self._log = log or (lambda message: print(message, file=sys.stderr))
        self.steps = 0
        self.notes: list[str] = []
        self.trace: list[dict[str, Any]] = []
        self.evidence_repairs = 0
        self.workflow_nudges = 0
        self.coverage_nudges = 0
        self._workflow_files_read: set[str] = set()
        self._files_read: set[str] = set()
        self._messages: list[dict[str, Any]] = []
        self._schemas = tool_schemas()

    # -- public ----------------------------------------------------------
    def run(self) -> AuditResult:
        started = time.time()
        overview = self.toolbox.overview()
        self._messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Audit this repository. Work from the overview below, inspect the files "
                    "that can prove or disprove violations in each category, then submit the "
                    "final report with `submit_report`.\n\n" + overview
                ),
            },
        ]
        self._log(f"Observation: {len(overview.splitlines())} overview lines")

        repairs = 0
        while self.steps < self.max_steps:
            turn = self._chat()
            if not turn:
                break
            self._messages.append(turn.raw_message)

            if turn.tool_calls:
                report = self._handle_calls(turn.tool_calls)
                if report is not None:
                    return self._finish(report, submitted=True, started=started)
                continue

            report = self._report_from_text(turn.content)
            if report is not None:
                return self._finish(report, submitted=False, started=started)

            text_call = parse_text_tool_call(turn.content)
            if text_call:
                self._log("Interpreting text-protocol tool call")
                report = self._handle_calls([text_call])
                if report is not None:
                    return self._finish(report, submitted=True, started=started)
                continue

            if repairs < 2:
                repairs += 1
                prompt = NUDGE_PROMPT if repairs == 1 else REPAIR_PROMPT
                self._messages.append({"role": "user", "content": prompt})
                continue
            self._log("Model stopped calling tools; finalising with what is proven.")
            break

        report, submitted = self._finalise()
        return self._finish(report, submitted=submitted, started=started)

    # -- internals -------------------------------------------------------
    def _chat(self, force_tool: str | None = None) -> AssistantTurn | None:
        self.steps += 1
        self._log(f"Step {self.steps}/{self.max_steps}")
        try:
            turn = self.llm.chat(
                self._messages,
                tools=self._schemas,
                force_tool=force_tool,
            )
        except Exception as exc:  # LLMError or provider quirk
            if force_tool:
                self._log(f"Forced submission unsupported ({exc}); retrying as plain text.")
                return self._forced_text_fallback()
            raise
        if self.verbose and turn.content:
            self._log("Model: " + _preview(turn.content, 400))
        return turn

    def _forced_text_fallback(self) -> AssistantTurn | None:
        self.steps += 1
        self._messages.append({"role": "user", "content": FINALIZE_PROMPT})
        turn = self.llm.chat(self._messages, tools=None)
        self._messages.append(turn.raw_message)
        return turn

    def _handle_calls(self, calls: list[dict[str, Any]]) -> dict[str, Any] | None:
        if len(calls) > MAX_TOOL_CALLS_PER_STEP:
            self._log(f"Model requested {len(calls)} calls; executing the first {MAX_TOOL_CALLS_PER_STEP}.")
            calls = calls[:MAX_TOOL_CALLS_PER_STEP]

        for call in calls:
            name = str(call.get("name") or "").strip()
            arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}

            if is_submission_tool(name):
                missing = self._violations_missing_evidence(arguments)
                if missing and self.evidence_repairs < MAX_EVIDENCE_REPAIRS:
                    self.evidence_repairs += 1
                    self.trace.append(
                        {
                            "step": self.steps,
                            "tool": "submit_report",
                            "arguments": {"violations": "rejected (missing evidence)"},
                            "result_preview": "rejected: evidence missing for " + ", ".join(missing),
                            "error": True,
                        }
                    )
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or "submit",
                            "name": name,
                            "content": (
                                "ERROR: missing non-empty `evidence` for: "
                                + ", ".join(missing)
                                + ". Call submit_report again and quote, for each violation, the "
                                "tool output (path, line, listing, or command) that proves it. "
                                "Never invent evidence or a path you did not see."
                            ),
                        }
                    )
                    continue
                workflows = self._workflow_review_list()
                if workflows and self.workflow_nudges < MAX_WORKFLOW_NUDGES:
                    self.workflow_nudges += 1
                    self.trace.append(
                        {
                            "step": self.steps,
                            "tool": "submit_report",
                            "arguments": {"violations": "rejected (workflow review)"},
                            "result_preview": (
                                "rejected: workflow review checklist for "
                                f"{len(workflows)} file(s): triggers, permissions, "
                                "action pinning, untrusted input, remote scripts, "
                                "pinned installs, credentials, failing steps"
                            ),
                            "error": True,
                        }
                    )
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or "submit",
                            "name": name,
                            "content": WORKFLOW_REVIEW_PROMPT.format(
                                files=", ".join(workflows)
                            ),
                        }
                    )
                    continue
                candidates = self._unread_candidates()
                if candidates and self.coverage_nudges < MAX_COVERAGE_NUDGES:
                    self.coverage_nudges += 1
                    self.trace.append(
                        {
                            "step": self.steps,
                            "tool": "submit_report",
                            "arguments": {"violations": "rejected (coverage review)"},
                            "result_preview": (
                                "rejected: coverage review for "
                                f"{len(candidates)} unread candidate file(s)"
                            ),
                            "error": True,
                        }
                    )
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or "submit",
                            "name": name,
                            "content": COVERAGE_REVIEW_PROMPT.format(
                                files=", ".join(candidates)
                            ),
                        }
                    )
                    continue
                report = self._build_submitted_report(arguments)
                if report is not None:
                    self.trace.append(
                        {
                            "step": self.steps,
                            "tool": "submit_report",
                            "arguments": {"violations": _submitted_summary(report)},
                            "result_preview": f"accepted {len(report['violations'])} violation(s)",
                            "error": False,
                        }
                    )
                    self._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or "submit",
                            "name": name,
                            "content": f"Report accepted with {len(report['violations'])} violation(s).",
                        }
                    )
                    return report
                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or "submit",
                        "name": name,
                        "content": (
                            "ERROR: `violations` must be an array of objects. Call submit_report "
                            "again with the complete list (use [] if there are none)."
                        ),
                    }
                )
                continue

            if "__malformed_arguments__" in arguments:
                output = "ERROR: tool arguments were not valid JSON. Retry with a JSON object."
            else:
                if name == "read_file":
                    self._note_workflow_read(arguments.get("path"))
                output = self.toolbox.execute(name, arguments, step=self.steps)
                if name == "read_file" and not output.startswith("ERROR"):
                    self._files_read.add(_normalise_repo_path(arguments.get("path")))

            self.trace.append(
                {
                    "step": self.steps,
                    "tool": name,
                    "arguments": arguments,
                    "result_preview": _preview(redact_secrets(output), 400),
                    "error": output.startswith("ERROR:"),
                }
            )
            self._log(
                f"  -> {name}({_preview(json.dumps(arguments, ensure_ascii=False), 120)}) "
                f"{len(output)} chars"
            )
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call-{self.steps}",
                    "name": name,
                    "content": output[:MAX_TOOL_OUTPUT_CHARS],
                }
            )
        return None

    def _violations_missing_evidence(self, arguments: dict[str, Any]) -> list[str]:
        violations = self._raw_violation_items(arguments)
        if violations is None:
            return []
        missing = []
        for item in violations:
            if not isinstance(item, dict):
                continue
            if not str(item.get("evidence") or "").strip():
                missing.append(str(item.get("rule_id") or "unknown rule"))
        return missing

    def _raw_violation_items(self, arguments: dict[str, Any]) -> list[Any] | None:
        violations = arguments.get("violations")
        if violations is None and isinstance(arguments.get("report"), dict):
            violations = arguments["report"].get("violations")
        if isinstance(violations, dict):
            return [violations]
        return violations if isinstance(violations, list) else None

    def _note_workflow_read(self, path: Any) -> None:
        text = _normalise_repo_path(path)
        if _WORKFLOW_PATH.match(text):
            self._workflow_files_read.add(text)

    def _workflow_review_list(self) -> list[str]:
        """Return every workflow file read so far, for a single review nudge.

        This is a generic prompt-level nudge, not a finding: a healthy workflow
        produces no violation, so the model can resubmit the same report after
        confirming each file against the checklist.
        """
        if not self._workflow_files_read or self.workflow_nudges >= MAX_WORKFLOW_NUDGES:
            return []
        return sorted(self._workflow_files_read)[:MAX_WORKFLOW_FILES_LISTED]

    def _unread_candidates(self) -> list[str]:
        """Candidate files the agent has not opened yet, capped for the nudge.

        Candidates come from the file tree only; they are prompts to look, never
        findings, and the model may resubmit the same report unchanged.
        """
        if self.coverage_nudges >= MAX_COVERAGE_NUDGES:
            return []
        unread: set[str] = set()
        for paths in self.toolbox.candidate_files().values():
            unread.update(path for path in paths if path not in self._files_read)
        return sorted(unread)[:MAX_COVERAGE_FILES_LISTED]

    def _build_submitted_report(self, arguments: dict[str, Any]) -> dict[str, Any] | None:
        violations = self._raw_violation_items(arguments)
        if violations is None:
            return None
        return build_report(
            self.toolbox.repository.target.https_url.replace(".git", ""),
            violations,
            self._path_exists,
            self.notes,
        )

    def _report_from_text(self, content: str) -> dict[str, Any] | None:
        payload = extract_json_object(content)
        if not payload:
            return None
        if isinstance(payload.get("report"), dict):
            payload = payload["report"]
        violations = payload.get("violations")
        if not isinstance(violations, list):
            return None
        self._log("Accepted report from assistant text output")
        return build_report(
            self.toolbox.repository.target.https_url.replace(".git", ""),
            violations,
            self._path_exists,
            self.notes,
        )

    def _path_exists(self, relative: str) -> bool:
        try:
            return self.toolbox.repository.path_for(relative).is_file()
        except ValueError:
            return False

    def _finalise(self) -> tuple[dict[str, Any], bool]:
        self._log("Finalisation: requesting a report for the evidence gathered so far.")
        self._messages.append(
            {
                "role": "user",
                "content": FINALIZE_PROMPT,
            }
        )
        empty_report = build_report(
            self.toolbox.repository.target.https_url.replace(".git", ""),
            [],
            self._path_exists,
            self.notes,
        )
        try:
            turn = self.llm.chat(self._messages, tools=self._schemas, force_tool="submit_report")
        except Exception as exc:
            self._log(f"Forced finalisation failed ({exc}); retrying as plain JSON.")
            try:
                turn = self.llm.chat(self._messages, tools=None)
            except Exception as second:  # pragma: no cover - defensive
                self.notes.append(f"Finalisation failed: {second}")
                return empty_report, False
            self._messages.append(turn.raw_message)
            report = self._report_from_text(turn.content)
            return (report, False) if report is not None else (empty_report, False)

        self._messages.append(turn.raw_message)
        for call in turn.tool_calls or []:
            if is_submission_tool(call.get("name", "")):
                report = self._build_submitted_report(call.get("arguments") or {})
                if report is not None:
                    self.trace.append(
                        {
                            "step": self.steps,
                            "tool": "submit_report",
                            "arguments": {"violations": _submitted_summary(report)},
                            "result_preview": (
                                f"accepted {len(report['violations'])} violation(s) during finalisation"
                            ),
                            "error": False,
                        }
                    )
                    return report, True
        report = self._report_from_text(turn.content)
        if report is not None:
            return report, False
        self._log("Finalisation produced no usable report; returning an empty violation list.")
        return empty_report, False

    def _finish(self, report: dict[str, Any], submitted: bool, started: float) -> AuditResult:
        if self.notes:
            self._log("Normalisation notes: " + "; ".join(self.notes))
        self._log(
            f"Audit finished in {time.time() - started:.1f}s, {self.steps} step(s), "
            f"{len(report['violations'])} violation(s)."
        )
        return AuditResult(
            report=report,
            steps=self.steps,
            submitted=submitted,
            notes=list(self.notes),
            trace=list(self.trace),
        )


def _preview(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _submitted_summary(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Compact, redacted view of the submitted findings for the trace."""
    return [
        {
            "rule_id": violation["rule_id"],
            "file": violation["file"],
            "line": violation["line"],
            "description": redact_secrets(violation["description"]),
        }
        for violation in report["violations"]
    ]


def _normalise_repo_path(value: Any) -> str:
    """Return a repository-relative POSIX path without a leading ``./``."""
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")
