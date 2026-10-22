#!/usr/bin/env python3
"""DevOps Repository Auditing Agent - CLI entry point.

Usage:
    python agent.py --repo https://github.com/owner/project --output report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from agent_loop import AuditorAgent
from llm import LLMClient, LLMError
from reporting import ReportError, write_report
from repo import RepoError, clone_repository, github_repo_url
from tools import ToolBox

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_MAX_STEPS = 30


def load_environment() -> None:
    """Load .env next to this script, then the current working directory."""
    load_dotenv(BASE_DIR / ".env", override=False)
    load_dotenv(override=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent.py",
        description="Audit a public GitHub repository for DevOps violations using an LLM-driven agent.",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Public GitHub repository URL (https://github.com/owner/project) or owner/project.",
    )
    parser.add_argument(
        "--output",
        default="report.json",
        help="Path of the JSON report to write (default: report.json).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Maximum LLM steps (default: MAX_STEPS from .env, or 30).",
    )
    parser.add_argument("--model", default=None, help="Override LLM_MODEL.")
    parser.add_argument("--base-url", default=None, help="Override LLM_BASE_URL.")
    parser.add_argument(
        "--time-limit",
        type=float,
        default=None,
        help=(
            "Total wall-clock limit in seconds before the agent submits what it "
            "has (default: AGENT_TIME_LIMIT_SECONDS from .env, or 600)."
        ),
    )
    parser.add_argument(
        "--trace",
        default=None,
        help="Optional path for a JSON trace of the tool-call sequence.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log model and tool activity.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only log errors.")
    return parser


def _emit(message: str, quiet: bool = False) -> None:
    if not quiet:
        print(message, file=sys.stderr, flush=True)


def _resolve_max_steps(cli_value: int | None) -> int:
    if cli_value is not None:
        return max(3, cli_value)
    raw = (os.getenv("MAX_STEPS") or "").strip()
    if not raw:
        return DEFAULT_MAX_STEPS
    try:
        return max(3, int(raw))
    except ValueError:
        return DEFAULT_MAX_STEPS


def _write_trace(path: str, payload: dict[str, Any]) -> None:
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_environment()

    api_key = (os.getenv("LLM_API_KEY") or "").strip()
    model = (args.model or os.getenv("LLM_MODEL") or DEFAULT_MODEL).strip()
    base_url = (args.base_url or os.getenv("LLM_BASE_URL") or "").strip() or None
    max_steps = _resolve_max_steps(args.max_steps)

    if not api_key:
        print(
            "error: LLM_API_KEY is not set. Copy .env.example to .env and add your provider key.",
            file=sys.stderr,
        )
        return 1

    try:
        report_url = github_repo_url(args.repo)
        _emit(f"Repository: {report_url}", args.quiet)
        _emit(f"Model: {model}", args.quiet)
        _emit("Cloning repository (shallow, read-only)...", args.quiet)

        repository = clone_repository(args.repo)
        try:
            _emit(
                f"Cloned {repository.target.slug} @ {repository.head_sha[:12]} "
                f"({repository.branch})",
                args.quiet,
            )
            llm = LLMClient(model=model, api_key=api_key, base_url=base_url)
            agent = AuditorAgent(
                toolbox=ToolBox(repository=repository),
                llm=llm,
                max_steps=max_steps,
                verbose=args.verbose,
                log=lambda message: _emit(message, args.quiet),
                time_limit=args.time_limit,
            )
            result = agent.run()
            destination = write_report(args.output, result.report)

            if args.trace:
                _write_trace(
                    args.trace,
                    {
                        "repository": result.report["repository"],
                        "model": model,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "steps": result.steps,
                        "submitted_via_tool": result.submitted,
                        "tool_calls": result.trace,
                        "notes": result.notes,
                    },
                )
                _emit(f"Trace written to {args.trace}", args.quiet)

            print(
                f"Wrote {destination} ({len(result.report['violations'])} violation(s), "
                f"{result.steps} step(s))."
            )
            return 0
        finally:
            repository.cleanup()
    except (RepoError, LLMError, ReportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # pragma: no cover - last-resort guard
        print(f"error: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
