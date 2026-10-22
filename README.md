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
- **166 automated tests** covering tools, the loop, normalisation, redaction, and the CLI.

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
| `AGENT_TIME_LIMIT_SECONDS` | no | `600` | Total wall-clock limit in seconds; when reached the agent stops investigating and submits the findings it has already proven |
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
with its full tool trace in [`docs/sample_trace.json`](docs/sample_trace.json).
Both come from one run against the fixture repository
<https://github.com/salarshoaib/injected-test-3>, whose seeded defects were
injected by [`scripts/inject_round3.py`](scripts/inject_round3.py).

## Rule catalog

Mirrors `rules.py` exactly (40 rules; `GEN-001` is the fallback used when no
catalog rule fits):

| Rule | Category | Default severity | Finding |
| --- | --- | --- | --- |
| HYG-001 | repository_hygiene | high | No README or setup documentation |
| HYG-002 | repository_hygiene | medium | Missing or unsuitable `.gitignore` |
| HYG-003 | repository_hygiene | medium | Generated or temporary files are tracked |
| HYG-004 | repository_hygiene | medium | Editor, OS or dependency directories committed |
| DEP-001 | dependency_management | high | Missing lockfile for a dependency manifest |
| DEP-002 | dependency_management | medium | Unpinned or unbounded dependency constraint in any manifest |
| DEP-003 | dependency_management | low | Inconsistent dependency declarations |
| DEP-004 | dependency_management | high | No dependency manifest in a project that imports third-party packages |
| QUA-001 | code_quality | low | No linter or formatter configuration |
| QUA-002 | code_quality | medium | Debug or dead code left in source |
| TST-001 | testing | high | No automated tests |
| TST-002 | testing | high | CI does not execute the test suite |
| TST-003 | testing | medium | Tests exist but are not wired into an automated runner |
| TST-004 | testing | medium | Tests are skipped or disabled |
| CI-001 | ci_cd | high | No continuous integration workflow |
| CI-002 | ci_cd | medium | Workflow triggers are missing or too narrow |
| CI-003 | ci_cd | high | CI step cannot fail the build |
| CI-004 | ci_cd | low | Runner or action reference follows a moving branch instead of a release |
| CI-005 | ci_cd | medium | Packages installed in CI without version pins |
| SEC-001 | security | critical | Hardcoded credential in source or configuration |
| SEC-002 | security | critical | Environment/secret file committed |
| SEC-003 | security | high | Unsafe workflow permissions |
| SEC-004 | security | critical | Untrusted code executed with elevated context |
| SEC-005 | security | high | Secrets printed or passed to untrusted commands |
| SEC-006 | security | low | Action reference points at a moving branch instead of a version tag or SHA |
| SEC-007 | security | high | Workflow trigger grants untrusted code a privileged context |
| SEC-008 | security | high | Untrusted event data interpolated into a workflow shell command |
| SEC-009 | security | high | Remote script downloaded and piped into a shell |
| SEC-010 | security | critical | Connection string or URL with embedded credentials |
| SEC-011 | security | medium | Container image runs as root |
| CFG-001 | configuration | high | Machine-specific absolute path in project files |
| CFG-002 | configuration | medium | Committed IDE/editor settings with machine-specific paths |
| CFG-003 | configuration | medium | Configuration hardcoded without environment separation |
| CFG-004 | configuration | medium | Debug mode enabled in production or deployment configuration |
| COL-001 | collaboration | high | Unsafe pull-request or merge automation (auto-approve, auto-merge, force-push to main) |
| COL-002 | collaboration | low | No contribution or review guidance |
| BLD-001 | build_release | medium | Build or packaging is not validated in CI |
| BLD-002 | build_release | high | Risky deployment automation (force push, deploy on every push, no environment gate) |
| BLD-003 | build_release | low | No release or versioning process |
| BLD-004 | build_release | medium | Container base image uses a floating tag instead of a pinned version |

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
166 passed
```

Coverage includes: URL parsing and rejection, clone path traversal/`.git` guards,
every tool (pagination, globs, invalid regex, redaction, missing paths, caching),
report normalisation (severity locks/caps, dotfile paths, hallucinated paths,
dedupe, cap), the agent loop (native and text-protocol tool calls, repair, nudges,
budget finalisation), and the CLI (invalid URL, missing key, report + trace writing).

## Sample run

The committed [`sample_report.json`](sample_report.json) and
[`docs/sample_trace.json`](docs/sample_trace.json) come from **one run** against
the fixture repository <https://github.com/salarshoaib/injected-test-3>, which
was seeded with known defects by [`scripts/inject_round3.py`](scripts/inject_round3.py).
Recall varies between runs because the model is non-deterministic; this sample
is one such run.

```text
$ python agent.py --repo https://github.com/salarshoaib/injected-test-3 --output sample_report.json --trace docs/sample_trace.json
Repository: https://github.com/salarshoaib/injected-test-3
Model: gemini-3.5-flash-lite
Cloning repository (shallow, read-only)...
...
Audit finished in ..., 30 step(s), 8 violation(s).
Wrote sample_report.json (8 violation(s), 30 step(s)).
```

Scored against [`scripts/truth_round3.json`](scripts/truth_round3.json):

```text
$ python scripts/score.py sample_report.json scripts/truth_round3.json
Recall 8/15
Precision 8/8
```

That is 8 of 15 seeded defects found, and 0 false positives. The run followed
the operational search guidance (machine paths, credential-in-URL, secret
assignments, key prefixes, debug flags), then read the hits' files, then read
every workflow, the Dockerfile, `Pipfile`, `requirements-dev.txt`, and the
committed IDE settings before submitting; the late `git_log` call came from the
pre-submit history nudge.

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
| [salarshoaib/injected-test-3](https://github.com/salarshoaib/injected-test-3) | 8 findings, 0 false positives (recall 8/15 vs `scripts/truth_round3.json`; one run, see Sample run) |
| [salarshoaib/devops-auditing-agent](https://github.com/salarshoaib/devops-auditing-agent) | 0 findings (self-audit) |

The mature repositories all produce zero findings, which is the main guard against
false positives on healthy evaluation repos. The seeded fixture exercises the recall
side; its scored report is committed as `sample_report.json`.

## Evaluating recall

Three injector scripts build throw-away copies of public repositories with known,
seeded defects (FAKE data only). Each one shallow-clones the upstream repo, drops
its history, makes a baseline commit, writes the seeded files, and commits them.
**None of them push**; each prints the local path and the exact
`git remote add` / `git push` commands for you to publish the fixture under your
own account, because the agent only audits public GitHub URLs.

| Round | Injector | Truth set | Seeded defects |
| --- | --- | --- | --- |
| 1 | `scripts/inject_defects.py` | `scripts/truth.json` (11 entries) | privileged PR workflow, masked test gate, fake credentials, machine paths, committed `.env`, tracked build artifacts |
| 2 | `scripts/inject_round2.py` | `scripts/truth_round2.json` (10 entries) | remote script piped to shell, PR-title script injection, `write-all` permissions, unpinned CI installs, branch-pinned action, secret in workflow env, committed IDE settings, debug mode, credentialed DB URL, unpinned dev requirements |
| 3 | `scripts/inject_round3.py` | `scripts/truth_round3.json` (15 entries) | privileged `pull_request_target` with branch checkout, `eval` of comment body, dead branch trigger and masked pytest, Dockerfile floating tag/secret/root user, committed `.idea`, debug + password in `local.cfg`, wildcard `Pipfile`, committed `.env.production`, `.gitignore` gaps |

`scripts/score.py` scores a report against a truth set. The truth file is an
optional second argument and defaults to `scripts/truth.json`:

```powershell
python scripts/score.py report.json                          # rounds 1's truth set
python scripts/score.py report.json scripts/truth_round3.json # round 3's truth set
# Recall 8/15
# Precision 8/8
# MISSED ...
# EXTRA ...
```

A finding counts as a match when its file path contains (or is contained in) the
expected path and any keyword appears in its description or evidence. Scoring is
one-to-one: each violation can satisfy at most one truth entry. Recall is the
fraction of seeded defects that were found; precision is the fraction of reported
findings that matched a seeded defect.

Honest tuning history:

- **Round 1** was used for development: its misses drove rule and prompt changes,
  so it no longer measures generalisation.
- **Round 2** was tuned against after its first run; its score therefore also
  overstates current generalisation.
- **Round 3** was untuned on its first run - no rule, prompt, or code change was
  made in response to its misses - so unlike rounds 1 and 2 it measures
  generalisation. The committed `sample_report.json` is one round-3 run and
  scores **recall 8/15, precision 8/8** (8 findings, 0 false positives) against
  `scripts/truth_round3.json`. Recall varies between runs because the model is
  non-deterministic; a re-run may score differently.

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
| `gemini-3.5-flash-lite` | Google, OpenAI-compatible endpoint | Tested: 8 findings / 0 false positives on the round-3 seeded fixture (one run), 0 findings on mature repositories |
| other OpenAI-compatible models | Any compatible API | Not yet tested |

## Limitations

- Static inspection only: no build, test, or runtime execution by design.
- History analysis is limited to the last 50 commits of the default branch.
- Rule ids are curated for common DevOps stacks; genuinely novel findings fall back
  to `GEN-001`.
- Report quality depends on the chosen model's tool-calling ability; smaller models
  may need more steps (`--max-steps`).
- Cost/latency scale with repository size and step budget.
- Recall varies between runs because the model is non-deterministic, and the
  committed sample is one run: against `scripts/truth_round3.json` it found 8 of
  15 seeded defects with 0 false positives. In that sample the missed defects
  were an untrusted PR-head checkout in `ci.yml`, a dead branch trigger and a
  masked pytest failure in `docs.yml`, an unpinned base image and a root user in
  the `Dockerfile`, unpinned versions in `Pipfile`, and `.gitignore` gaps that
  left `.env.production` tracked.
- The default step budget is 30; large repositories with many workflows may
  exhaust it, in which case the agent submits what it has found.
- Only `gemini-3.5-flash-lite` has been tested; other models are not yet tested.

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
redaction.py        secret redaction
requirements.txt    runtime/test dependencies
.env.example        configuration template
tests/              166 automated tests
sample_report.json  authentic output from a real run
```
