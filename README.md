# DevOps Repository Auditing Agent

An AI agent that clones a public GitHub repository, investigates it with its own
LLM-driven tool loop, and writes an evidence-backed JSON report of DevOps
best-practice violations.

Built for **Assignment 2 - AI Agent for DevOps Repository Auditing**. The agent
loop, tool dispatch, and control flow are implemented directly in Python; no
agent framework (LangChain, LangGraph, n8n, ...) is used, only a provider SDK.

---

## What it does

```bash
python agent.py --repo https://github.com/owner/project --output report.json
```

1. Validates the URL and makes a **shallow, read-only clone** in a temporary directory.
2. Starts from a cheap repository overview (file count, top-level tree, marker files, workflows).
3. Repeatedly asks the LLM what to inspect next and executes the tool it selects
   (`list_files`, `read_file`, `search_code`, `git_log`), feeding results back.
4. Interprets the evidence, drops duplicate/unsupported findings, redacts secrets,
   normalises severity, and validates every field.
5. Writes valid JSON - including `"violations": []` for a healthy repository - and
   cleans up the clone.

The model decides the investigation path. There is no fixed scanner pass and no
hard-coded repository knowledge; the rule catalog is vocabulary, not a checklist.

## Highlights

- **Genuine agent loop** - observe -> reason -> select tool -> execute -> interpret,
  with follow-up calls and a step budget.
- **Provider agnostic** - any OpenAI-compatible endpoint (OpenAI, Gemini, Groq,
  OpenRouter, Ollama) that supports tool calling.
- **Precision first** - equivalence rules (no `requirements.txt` is fine when
  `pyproject.toml` declares dependencies), dedupe by root cause, path validation
  against the clone, and no claims about remote branch protection.
- **Never leaks secrets** - tool output fed to the model, trace files, and reports
  all pass through a redactor. Evidence keeps the key name, never the value.
- **Safe by design** - no repository code is executed; the clone is thrown away.
- **80 automated tests** covering tools, the loop, normalisation, redaction, and the CLI.

## Architecture

```text
agent.py                 CLI: argument parsing, .env, orchestration, exit codes
  |
  +-- repo.py            URL parsing, shallow read-only clone, path-safety guards
  |
  +-- agent_loop.py      THE AGENT: message history, tool selection, dispatch,
  |                      step budget, forced finalisation, trace recording
  |     |
  |     +-- llm.py       OpenAI-compatible client, tool-call parsing,
  |     |                thought-signature passthrough, retry/backoff
  |     |
  |     +-- tools.py     list_files / read_file / search_code / git_log
  |     |                + submit_report schema, bounded outputs
  |     |
  |     +-- prompts.py   system prompt, nudges, finalisation prompt
  |
  +-- rules.py           rule catalog (ids, categories, severities, remediation)
  +-- reporting.py       schema normalisation, dedupe, severity policy, JSON write
  +-- redaction.py       credential redaction (never echoes a credential)
```

The loop in `agent_loop.AuditorAgent.run`:

```text
overview --> [ LLM turn ] --tool_call--> execute --> append result --> repeat
                 |                                   (max 8 calls per turn)
                 +--submit_report--> normalise --> report.json
                 +--text report----> normalise --> report.json
                 +--no tool call---> nudge / repair prompt (max 2)
                 +--budget reached--> forced submit_report with current evidence
```

Manifest signals in the overview only tell the model which files exist so it knows what to inspect. The overview also lists generic candidate files derived from the file tree — dependency manifests (including secondary dev/test ones), configuration-like files, and committed editor/IDE directories — as "files worth inspecting". None of these lists are findings: they point the model at files, and every violation still requires the model to read the evidence. A single pre-submit coverage nudge names up to 12 of those candidates that have not been read yet; it can be declined by resubmitting the same report.

## Requirements

- Python 3.10+
- `git` on `PATH`
- An API key for an OpenAI-compatible model with function calling
- Network access to GitHub and the LLM endpoint

## Installation

```bash
git clone https://github.com/salarshoaib/devops-auditing-agent.git
cd devops-auditing-agent

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Copy the template and fill in your credentials:

```bash
cp .env.example .env          # macOS / Linux
```

```powershell
Copy-Item .env.example .env   # Windows
```

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `LLM_API_KEY` | yes | - | API key for the OpenAI-compatible endpoint |
| `LLM_MODEL` | yes | `gpt-4o-mini` | Model name, must support tool calling |
| `LLM_BASE_URL` | no | OpenAI | Base URL of the compatible endpoint |
| `MAX_STEPS` | no | `30` | Upper bound on LLM steps |
| `LLM_TIMEOUT` | no | `120` | Per-request timeout in seconds |

Example for Google Gemini's OpenAI-compatible endpoint:

```dotenv
LLM_API_KEY=your-key
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-2.5-flash
```

Real environment variables take precedence over `.env`. `.env` is git-ignored;
only `.env.example` is committed.

## Usage

```bash
# required form
python agent.py --repo https://github.com/owner/project --output report.json

# SSH and owner/repo shorthand work too
python agent.py --repo git@github.com:owner/project.git --output report.json
python agent.py --repo owner/project --output report.json

# optional diagnostics
python agent.py --repo owner/project --output report.json --verbose --trace trace.json
```

| Flag | Description |
| --- | --- |
| `--repo` | Repository URL or `owner/repo` (required) |
| `--output` | Report path (default `report.json`) |
| `--max-steps` | Override `MAX_STEPS` |
| `--model`, `--base-url` | Override `LLM_MODEL` / `LLM_BASE_URL` |
| `--trace` | Write a JSON record of every tool call |
| `-v/--verbose`, `-q/--quiet` | Verbosity of progress output |

The command runs unattended - there are no prompts. Progress goes to stderr and a
one-line summary to stdout.

Exit codes: `0` report written, `1` fatal error (bad URL, auth failure, clone
failure, unwritable output), `130` interrupted.

## Output contract

Every run writes valid JSON with exactly two top-level keys:

```json
{
  "repository": "https://github.com/example/demo-project",
  "violations": [
    {
      "rule_id": "SEC-001",
      "category": "security",
      "severity": "critical",
      "file": "config.py",
      "line": 17,
      "description": "Hardcoded API credential",
      "evidence": "config.py assigns a literal API key on line 17.",
      "recommendation": "Load the key from an environment variable and rotate it."
    }
  ]
}
```

Rules enforced by `reporting.py`:

- all eight fields are always present; `file`/`line` are `null` when repository-wide;
- `severity` is one of `critical`, `high`, `medium`, `low`;
- `category` is one of the nine inspection areas;
- unknown rule ids become `GEN-001`; unknown categories fall back to the catalog;
- `file` is validated against the clone - a hallucinated path becomes `null` with a note;
- duplicate root causes and unsupported entries are dropped, output is capped at 40;
- secret-looking values are redacted everywhere.

A real generated report is committed as [`sample_report.json`](sample_report.json)
(produced by auditing <https://github.com/salarshoaib/devops-audit-fixture>).

## Rule catalog

| Rule | Category | Default severity | Finding |
| --- | --- | --- | --- |
| HYG-001 | repository_hygiene | high | No README or setup documentation |
| HYG-002 | repository_hygiene | medium | Missing or unsuitable `.gitignore` |
| HYG-003 | repository_hygiene | medium | Generated or temporary files tracked |
| HYG-004 | repository_hygiene | medium | Editor/OS/dependency directories committed |
| DEP-001 | dependency_management | high | Missing lockfile for a manifest |
| DEP-002 | dependency_management | medium | Unpinned or unbounded dependency constraint |
| DEP-003 | dependency_management | low | Inconsistent dependency declarations |
| DEP-004 | dependency_management | high | No manifest in a project with third-party imports |
| QUA-001 | code_quality | low | No linter or formatter configuration |
| QUA-002 | code_quality | medium | Debug or dead code left in source |
| TST-001 | testing | high | No automated tests |
| TST-002 | testing | high | CI does not execute the test suite |
| TST-003 | testing | medium | Tests exist but no automated runner |
| TST-004 | testing | medium | Tests skipped or disabled |
| CI-001 | ci_cd | high | No continuous integration workflow |
| CI-002 | ci_cd | medium | Workflow triggers missing or too narrow |
| CI-003 | ci_cd | high | CI step cannot fail the build |
| CI-004 | ci_cd | low | Runner/action reference follows a moving branch |
| CI-005 | ci_cd | medium | Packages installed in CI without version pins |
| SEC-001 | security | critical | Hardcoded credential in source/config |
| SEC-002 | security | critical | Environment/secret file committed |
| SEC-003 | security | high | Unsafe workflow permissions |
| SEC-004 | security | critical | Untrusted code executed with elevated context |
| SEC-005 | security | high | Secrets printed or passed to untrusted commands |
| SEC-006 | security | low | Action reference points at a moving branch |
| SEC-007 | security | high | Workflow trigger grants untrusted privileged context |
| SEC-008 | security | high | Untrusted event data interpolated into a run step |
| SEC-009 | security | high | Remote script downloaded and piped into a shell |
| SEC-010 | security | critical | Connection string with embedded credentials |
| CFG-001 | configuration | high | Machine-specific absolute path |
| CFG-002 | configuration | medium | Local IDE/environment settings committed |
| CFG-003 | configuration | medium | Configuration without environment separation |
| CFG-004 | configuration | medium | Debug mode enabled by default |
| COL-001 | collaboration | high | Unsafe pull-request or merge automation |
| COL-002 | collaboration | low | No contribution or review guidance |
| BLD-001 | build_release | medium | Build/packaging not validated in CI |
| BLD-002 | build_release | high | Risky deployment automation (force push, deploy without gate) |
| BLD-003 | build_release | low | No release or versioning process |

Escalation policy: `SEC-001`, `SEC-002`, and `SEC-004` cannot be downgraded below
`critical`. Style/documentation categories are capped at `medium` so cosmetic
issues never outrank a missing lockfile or broken CI gate.

## Tools

| Tool | Arguments | Behaviour |
| --- | --- | --- |
| `list_files` | `path`, `pattern`, `max_results` | Bounded recursive listing with sizes and glob filtering |
| `read_file` | `path`, `start_line`, `end_line` | Line-numbered text read, range-limited, binary detection |
| `search_code` | `pattern`, `path`, `glob`, `case_sensitive`, `max_results` | Regex search (literal fallback), `path:line: snippet` |
| `git_log` | `limit`, `path` | Recent commits, optionally filtered by path |
| `submit_report` | `violations[]` | Schema-terminated final answer |

All tools are read-only, path-safe (no `..`, absolute paths, or `.git` access),
bounded (large files skipped by search, huge reads truncated), and return
actionable errors instead of raising into the loop. Identical repeated calls
return a cached result so the model does not loop on the same question.

## Safety guarantees

- **Repository unchanged** - the only write is to the temporary clone, which is deleted.
- **No code execution** - no `pytest`/`npm`/build steps are run against the target.
- **No credential exposure** - URLs are sanitised in errors, tool output is redacted,
  and traces store only redacted previews.
- **No invented remote state** - branch protection, required reviewers, and repository
  secrets are not visible from files, so the prompt forbids claiming them.
- **Bounded resources** - clone depth 50, tool output caps, 30-step budget by default.

## Reliability

- HTTP 429/5xx/connection failures are retried with backoff (5s, 15s, 30s) and
  `Retry-After` support.
- Gemini 3 thought signatures are echoed back with tool calls, so multi-step tool
  use works on Google's OpenAI-compatible endpoint.
- A malformed `submit_report` gets a corrective tool message instead of aborting.
- A submission whose findings have no `evidence` is rejected once and re-requested;
  only a second incomplete submission falls back to a placeholder.
- If the model replies with prose, it is nudged twice; if the step budget runs out,
  a finalisation turn forces submission of what has been proven.
- Once a workflow file has been read, one submit attempt is answered with a review
  checklist covering every workflow file read (triggers, permissions, action
  pinning, untrusted input, remote scripts, pinned installs, credentials, failing
  steps). Once per run, a similar nudge names candidate dependency, configuration
  and IDE files that were never opened. Both are advisory: the model can resubmit
  the same report unchanged, and neither ever creates a finding.
- Any fatal error exits non-zero with a one-line message and never writes a
  half-finished report.

## Tests

```bash
python -m pytest -q
```

```text
80 passed
```

Coverage includes: URL parsing and rejection, clone path traversal/`.git` guards,
every tool (pagination, globs, invalid regex, redaction, missing paths, caching),
report normalisation (severity locks/caps, dotfile paths, hallucinated paths,
dedupe, cap), the agent loop (native and text-protocol tool calls, repair, nudges,
budget finalisation), and the CLI (invalid URL, missing key, report + trace writing).

## Sample run

```text
$ python agent.py --repo https://github.com/salarshoaib/devops-audit-fixture --output report.json
Repository: https://github.com/salarshoaib/devops-audit-fixture
Model: gemini-3.5-flash-lite
Cloning repository (shallow, read-only)...
Cloned salarshoaib/devops-audit-fixture @ a62eb4efba56 (main)
Observation: 11 overview lines
Step 1/30
  -> list_files({}) 212 chars
Step 2/30
  -> read_file({"path": ".env"}) 106 chars
...
Audit finished in 16.3s, 12 step(s), 15 violation(s).
Wrote report.json (15 violation(s), 12 step(s)).
```

The audit found the committed `.env`, hardcoded credentials, `pull_request_target`
checkout of PR head code, `write-all` permissions, `continue-on-error` test gates,
missing tests/lockfile, a tracked build log, debug mode, absolute paths, and
force-push deployment automation.

## Validation

Beyond the unit tests, the agent was run against public repositories to check both
sides of the rubric: precision (mature projects should produce few or no findings)
and recall (neglected or purpose-built projects should expose their real defects).

| Repository | Result |
| --- | --- |
| [encode/httpx](https://github.com/encode/httpx) | 0 findings |
| [pallets/click](https://github.com/pallets/click) | 0 findings |
| [psf/requests](https://github.com/psf/requests) | 0 findings |
| [expressjs/express](https://github.com/expressjs/express) | 0 findings |
| [gin-gonic/gin](https://github.com/gin-gonic/gin) | 0 findings |
| [LondheShubham153/node-todo-cicd](https://github.com/LondheShubham153/node-todo-cicd) | 0 findings (Jenkins/Azure pipelines count as CI) |
| [microsoft/python-sample-vscode-flask-tutorial](https://github.com/microsoft/python-sample-vscode-flask-tutorial) | CI-001, TST-001 (stub test), DEP-002, CFG-001 |
| [missating/nodejs-todo](https://github.com/missating/nodejs-todo) | CI-001, TST-001, DEP-001, DEP-002, HYG-002 |
| [salarshoaib/devops-audit-fixture](https://github.com/salarshoaib/devops-audit-fixture) | 15 findings across 7 categories, each with file/line evidence |
| [salarshoaib/devops-auditing-agent](https://github.com/salarshoaib/devops-auditing-agent) | 0 findings (self-audit) |

The mature repositories all produce zero findings, which is the main guard against
false positives on healthy evaluation repos. The seeded fixture exercises the recall
side; its report is committed as `sample_report.json`.

## Evaluating recall

`scripts/inject_defects.py` builds a throw-away copy of a public repository
(`encode/httpx`) with a fresh two-commit history and a known set of seeded
defects: a `pull_request_target` workflow that checks out the PR head with
`write-all` permissions, a masked test gate, fake credentials in
`app/settings_local.py`, hardcoded machine paths, a committed `.env`, and tracked
build artifacts. It never pushes; it prints the exact commands to publish the
fixture under your own account.

`scripts/truth.json` lists the seeded defects (`file` plus matching `keywords`),
and `scripts/score.py` scores a report against it:

```powershell
python scripts/score.py report.json
# Recall 8/8
# Precision 8/9
# MISSED ...
# EXTRA ...
```

A finding counts as a match when its file path contains (or is contained in) the
expected path and any keyword appears in its description or evidence. Recall is
the fraction of seeded defects that were found; precision is the fraction of
reported findings that matched a seeded defect. The agent only audits public
GitHub URLs, so publishing the prepared clone is a manual step.

## Design decisions

- **Clone instead of the GitHub API** - `git_log()` and file reads work on any public
  repository without tokens, rate limits, or per-file API calls; the clone is shallow
  (depth 50) to stay fast.
- **Deterministic overview, LLM-driven investigation** - the overview only saves the
  model a few discovery calls; it never decides findings. Everything reported must
  come from a tool result.
- **Evidence before output** - normalisation refuses to silently pass through
  hallucinated paths or duplicate root causes, which protects precision on the
  healthy-repository evaluation.
- **Prompts define policy, code defines guardrails** - the model decides *what* is a
  violation; the code enforces schema, severity floors, redaction, and safety.

## Tested models

| Model | Provider | Result |
| --- | --- | --- |
| `gemini-3.5-flash-lite` | Google, OpenAI-compatible endpoint | Tested: 15 findings on the seeded fixture, 0 on mature repositories |
| other OpenAI-compatible models | Any compatible API | Not yet tested |

## Limitations

- Static inspection only: no build, test, or runtime execution by design.
- History analysis is limited to the last 50 commits of the default branch.
- Rule ids are curated for common DevOps stacks; genuinely novel findings fall back
  to `GEN-001`.
- Report quality depends on the chosen model's tool-calling ability; smaller models
  may need more steps (`--max-steps`).
- Cost/latency scale with repository size and step budget.

## Project layout

```text
agent.py            CLI entry point
agent_loop.py       agent loop, tool dispatch, finalisation
llm.py              OpenAI-compatible client + retries
prompts.py          system/nudge/repair/finalise prompts (see prompt.md)
prompt.md           human-readable prompt documentation
tools.py            inspection tools + submit_report schema
repo.py             clone + path safety
rules.py            rule catalog
reporting.py        validation, dedupe, severity policy, JSON writer
redaction.py          secret redaction
requirements.txt    runtime/test dependencies
.env.example        configuration template
tests/              80 automated tests
sample_report.json  authentic output from a real run
```
