"""Thin wrapper around an OpenAI-compatible chat completions endpoint.

Only the provider SDK is used; the agent loop, tool dispatch and control flow are
implemented in :mod:`agent_loop`.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

DEFAULT_TIMEOUT = 120.0
RETRY_DELAYS = (5.0, 15.0, 30.0)


class LLMError(RuntimeError):
    """Fatal LLM failure (auth, quota, unreachable endpoint)."""


class DeadlineExceeded(LLMError):
    """The wall-clock deadline is closer than the next retry sleep.

    Raised instead of sleeping, so callers stop retrying immediately and
    finalise with whatever evidence they already have.
    """


@dataclass
class AssistantTurn:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"__malformed_arguments__": raw}
    return parsed if isinstance(parsed, dict) else {"__value__": parsed}


class LLMClient:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 1,
    ) -> None:
        if not model:
            raise LLMError("No model configured. Set LLM_MODEL in .env.")
        if not api_key:
            raise LLMError("No API key configured. Set LLM_API_KEY in .env.")
        self.model = model
        # Absolute wall-clock timestamp; None disables the deadline check.
        self.deadline: float | None = None
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
            timeout=timeout,
            max_retries=max_retries,
        )

    def set_deadline(self, deadline: float | None) -> None:
        """Refuse retry sleeps that would run past *deadline* (see DeadlineExceeded)."""
        self.deadline = deadline

    def _create_with_retries(self, kwargs: dict[str, Any], retries: int | None = None):
        delays = RETRY_DELAYS if retries is None else tuple(RETRY_DELAYS[: max(0, retries)])
        last_error: Exception | None = None
        for attempt in range(len(delays) + 1):
            if attempt:
                wait = _retry_after(last_error, delays[attempt - 1])
                if self.deadline is not None:
                    remaining = self.deadline - time.time()
                    if remaining < wait:
                        raise DeadlineExceeded(
                            f"only {max(remaining, 0.0):.0f}s left before the wall-clock "
                            f"limit; skipping a {wait:.0f}s retry sleep and finalising now"
                        )
                print(
                    f"  (provider unavailable - retrying in {wait:.0f}s, "
                    f"attempt {attempt + 1}/{len(delays) + 1})",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait)
            try:
                return self._client.chat.completions.create(**kwargs)
            except (RateLimitError, InternalServerError, APIConnectionError) as exc:
                last_error = exc
        raise last_error  # type: ignore[misc]

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        force_tool: str | None = None,
        temperature: float = 0.0,
        retries: int | None = None,
    ) -> AssistantTurn:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            if force_tool:
                kwargs["tool_choice"] = {
                    "type": "function",
                    "function": {"name": force_tool},
                }
            else:
                kwargs["tool_choice"] = "auto"

        try:
            response = self._create_with_retries(kwargs, retries)
        except DeadlineExceeded:
            raise  # deadline reached: caller finalises with proven evidence
        except APITimeoutError as exc:
            raise LLMError(f"LLM request timed out after {DEFAULT_TIMEOUT:.0f}s.") from exc
        except APIConnectionError as exc:
            raise LLMError("Could not reach the LLM endpoint. Check LLM_BASE_URL and network.") from exc
        except APIStatusError as exc:
            detail = _error_detail(exc)
            raise LLMError(f"LLM request failed with HTTP {exc.status_code}: {detail}") from exc
        except Exception as exc:  # unexpected SDK/provider error
            raise LLMError(f"LLM request failed: {type(exc).__name__}: {exc}") from exc

        if not response.choices:
            raise LLMError("LLM returned no choices.")

        message = response.choices[0].message
        content = (message.content or "").strip()
        tool_calls: list[dict[str, Any]] = []
        for call in message.tool_calls or []:
            dumped = call.model_dump()
            extra_content = dumped.get("extra_content")
            tool_calls.append(
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": _parse_arguments(call.function.arguments),
                    "raw_arguments": call.function.arguments or "{}",
                    # Gemini 3 requires thought signatures to be echoed back with
                    # the assistant tool call; other providers do not send this.
                    "extra_content": extra_content,
                }
            )

        raw_message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            serialised_calls = []
            for call in tool_calls:
                entry: dict[str, Any] = {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call["raw_arguments"],
                    },
                }
                if call.get("extra_content"):
                    entry["extra_content"] = call["extra_content"]
                serialised_calls.append(entry)
            raw_message["tool_calls"] = serialised_calls
        return AssistantTurn(content=content, tool_calls=tool_calls, raw_message=raw_message)


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of *text* (fenced or bare)."""
    if not text:
        return None
    candidates: list[str] = [match.group(1) for match in _FENCE.finditer(text)]
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            parsed, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    # Last resort: scan for balanced objects.
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_text_tool_call(text: str) -> dict[str, Any] | None:
    """Fallback for providers without native tool calling.

    Accepts ``{"tool": "read_file", "arguments": {...}}`` or
    ``{"name": ..., "parameters": ...}`` shapes.
    """
    payload = extract_json_object(text)
    if not payload:
        return None
    name = payload.get("tool") or payload.get("name") or payload.get("function")
    if isinstance(name, dict):
        arguments = name.get("arguments")
        name = name.get("name")
    else:
        arguments = (
            payload.get("arguments")
            or payload.get("args")
            or payload.get("parameters")
            or payload.get("input")
        )
    if not isinstance(name, str):
        return None
    if isinstance(arguments, str):
        arguments = _parse_arguments(arguments)
    if not isinstance(arguments, dict):
        arguments = {}
    return {"id": f"text-call-{abs(hash((name, json.dumps(arguments, sort_keys=True)))) % 10**8}",
            "name": name,
            "arguments": arguments,
            "raw_arguments": json.dumps(arguments)}


def _retry_after(exc: Exception | None, default: float) -> float:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            value = headers.get("retry-after")
        except Exception:  # pragma: no cover - exotic header objects
            value = None
        if value:
            try:
                return max(1.0, min(float(value), 60.0))
            except (TypeError, ValueError):
                pass
    return default


def _error_detail(exc: APIStatusError) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:300]
        if body.get("message"):
            return str(body["message"])[:300]
    text = str(exc)
    # Never leak headers/keys that a provider may echo.
    text = re.sub(r"\b(sk|ghp|AIza)[A-Za-z0-9_\-]{8,}\b", "[REDACTED]", text)
    return text[:300]
