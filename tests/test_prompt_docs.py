"""Keeps prompt.md in sync with the prompts that actually ship."""

from __future__ import annotations

from pathlib import Path

import prompts

DOC = (Path(__file__).resolve().parents[1] / "prompt.md").read_text(encoding="utf-8")


def test_full_prompts_appear_verbatim():
    for prompt in (prompts.FINALIZE_PROMPT, prompts.REPAIR_PROMPT, prompts.NUDGE_PROMPT):
        assert prompt in DOC


def test_system_prompt_policy_lines_are_documented():
    for line in (
        "Findings about an absence that concerns the whole repository",
        "Read workflow files in full.",
        "Rule ids must be copied exactly from the catalog",
        "either HYG-004 or CFG-002",
        "A healthy repository must produce an empty violations list.",
        "Coverage check before submitting",
    ):
        assert line in DOC
        assert line in prompts.SYSTEM_PROMPT
