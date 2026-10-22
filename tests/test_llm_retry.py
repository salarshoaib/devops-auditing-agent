"""Retry/backoff must respect the wall-clock deadline."""

from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from agent_loop import AuditorAgent
from llm import DeadlineExceeded, LLMClient, LLMError
from tools import ToolBox


def _always_failing_client() -> LLMClient:
    """A real LLMClient whose provider always raises a retryable error.

    Built without invoking the OpenAI SDK constructor: it is unrelated to the
    retry/deadline behaviour under test and costs seconds per instance.
    """

    def create(**kwargs):
        raise APIConnectionError(
            message="provider down",
            request=httpx.Request("POST", "http://localhost/v1/chat/completions"),
        )

    client = LLMClient.__new__(LLMClient)
    client.model = "test-model"
    client.deadline = None
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client


def test_retry_sleep_is_skipped_when_deadline_is_nearer_than_the_wait():
    client = _always_failing_client()
    client.set_deadline(time.time() + 2)  # first backoff is 5s, longer than left

    started = time.time()
    with pytest.raises(DeadlineExceeded):
        client.chat([{"role": "user", "content": "hi"}])
    elapsed = time.time() - started

    # If it had slept, this call would have taken 5s+; it must return at once.
    assert elapsed < 1.0


def test_retry_sleep_still_happens_without_a_deadline(monkeypatch):
    client = _always_failing_client()
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))

    # chat() converts the exhausted provider error into a fatal LLMError.
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}])

    assert slept == [5.0, 15.0, 30.0]


def test_finalisation_retry_cap_limits_backoff(monkeypatch):
    client = _always_failing_client()
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))

    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "hi"}], retries=1)

    assert slept == [5.0]


def test_run_ends_within_limit_when_provider_always_fails(sample_repo, monkeypatch):
    client = _always_failing_client()
    slept: list[float] = []
    real_sleep = time.sleep

    def record_sleep(seconds: float) -> None:
        slept.append(seconds)
        real_sleep(seconds)

    monkeypatch.setattr(time, "sleep", record_sleep)

    agent = AuditorAgent(
        toolbox=ToolBox(repository=sample_repo),
        llm=client,
        max_steps=30,
        time_limit=5,
        log=lambda message: None,
    )
    started = time.time()
    result = agent.run()
    elapsed = time.time() - started

    # A run that slept through the backoff would take 50s+ in the investigation
    # alone; with the deadline check it stops at once and finalises.
    assert elapsed < 5 + 2  # limit plus a small margin
    assert slept == []  # every backoff was nearer than the deadline
    assert result.submitted is False
    assert result.report["violations"] == []
    assert any("wall-clock" in note for note in result.notes)
