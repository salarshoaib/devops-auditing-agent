"""Rule catalog for the audit.

The catalog gives the LLM a stable vocabulary (rule ids, categories, default
severities, remediation hints) so reports stay consistent and machine-checkable.
It is *not* a checklist that is evaluated blindly: every finding still has to be
proven by tool output.
"""

from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = (
    "repository_hygiene",
    "dependency_management",
    "code_quality",
    "testing",
    "ci_cd",
    "security",
    "configuration",
    "collaboration",
    "build_release",
)

SEVERITIES = ("critical", "high", "medium", "low")
SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITIES)}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    severity: str
    title: str
    recommendation: str


RULES: tuple[Rule, ...] = (
    # Repository hygiene -----------------------------------------------------
    Rule(
        "HYG-001",
        "repository_hygiene",
        "high",
        "No README or setup documentation",
        "Add a README covering purpose, install, configuration, and usage.",
    ),
    Rule(
        "HYG-002",
        "repository_hygiene",
        "medium",
        "Missing or unsuitable .gitignore",
        "Add a .gitignore matching the stack (build output, caches, local env files).",
    ),
    Rule(
        "HYG-003",
        "repository_hygiene",
        "medium",
        "Generated or temporary files are tracked",
        "Remove tracked artifacts from version control and ignore them instead.",
    ),
    Rule(
        "HYG-004",
        "repository_hygiene",
        "medium",
        "Editor, OS or dependency directories committed",
        "Untrack vendored/build directories (.venv, node_modules, .idea, __pycache__).",
    ),
    # Dependency management --------------------------------------------------
    Rule(
        "DEP-001",
        "dependency_management",
        "high",
        "Missing lockfile for a dependency manifest",
        "Commit a lockfile (poetry.lock, package-lock.json, uv.lock, ...) for reproducible installs.",
    ),
    Rule(
        "DEP-002",
        "dependency_management",
        "medium",
        "Unpinned or unbounded dependency constraint in any manifest",
        "Pin or bound versions (==, ~=, >=,<) so installs are reproducible.",
    ),
    Rule(
        "DEP-003",
        "dependency_management",
        "low",
        "Inconsistent dependency declarations",
        "Keep a single source of truth for runtime dependencies across manifest files.",
    ),
    Rule(
        "DEP-004",
        "dependency_management",
        "high",
        "No dependency manifest in a project that imports third-party packages",
        "Add a manifest (requirements.txt or pyproject.toml) listing runtime dependencies.",
    ),
    # Code quality -----------------------------------------------------------
    Rule(
        "QUA-001",
        "code_quality",
        "low",
        "No linter or formatter configuration",
        "Add ruff/flake8/eslint/prettier configuration and run it in CI.",
    ),
    Rule(
        "QUA-002",
        "code_quality",
        "medium",
        "Debug or dead code left in source",
        "Remove debugging statements and unreachable/dead code before release.",
    ),
    # Testing ----------------------------------------------------------------
    Rule(
        "TST-001",
        "testing",
        "high",
        "No automated tests",
        "Add a test suite under tests/ and document how to run it.",
    ),
    Rule(
        "TST-002",
        "testing",
        "high",
        "CI does not execute the test suite",
        "Add a CI step that runs the tests on pushes and pull requests.",
    ),
    Rule(
        "TST-003",
        "testing",
        "medium",
        "Tests exist but are not wired into an automated runner",
        "Configure the test runner and execute it in CI.",
    ),
    Rule(
        "TST-004",
        "testing",
        "medium",
        "Tests are skipped or disabled",
        "Re-enable skipped tests or document why they are skipped.",
    ),
    # CI / GitHub Actions ----------------------------------------------------
    Rule(
        "CI-001",
        "ci_cd",
        "high",
        "No continuous integration workflow",
        "Add a GitHub Actions workflow that validates pushes and pull requests.",
    ),
    Rule(
        "CI-002",
        "ci_cd",
        "medium",
        "Workflow triggers are missing or too narrow",
        "Trigger CI on push to the default branch and on pull_request.",
    ),
    Rule(
        "CI-003",
        "ci_cd",
        "high",
        "CI step cannot fail the build",
        "Remove continue-on-error and shell fallbacks such as `|| true` from validation steps.",
    ),
    Rule(
        "CI-004",
        "ci_cd",
        "low",
        "Deprecated or unmaintained runner/action versions",
        "Upgrade to supported runner images and current major action versions.",
    ),
    Rule(
        "CI-005",
        "ci_cd",
        "medium",
        "Packages installed in CI without version pins",
        "Pin the installed versions (or install from a lockfile) so CI runs are reproducible.",
    ),
    # Security ---------------------------------------------------------------
    Rule(
        "SEC-001",
        "security",
        "critical",
        "Hardcoded credential in source or configuration",
        "Remove the credential, rotate it, and load it from a secrets manager or environment variable.",
    ),
    Rule(
        "SEC-002",
        "security",
        "critical",
        "Environment/secret file committed",
        "Delete the file from history, rotate the exposed values, and ignore it going forward.",
    ),
    Rule(
        "SEC-003",
        "security",
        "high",
        "Unsafe workflow permissions",
        "Set least-privilege `permissions:` (default read-only) in the workflow.",
    ),
    Rule(
        "SEC-004",
        "security",
        "critical",
        "Untrusted code executed with elevated context",
        "Do not combine pull_request_target with checkout of the PR head; use pull_request.",
    ),
    Rule(
        "SEC-005",
        "security",
        "high",
        "Secrets printed or passed to untrusted commands",
        "Reference secrets only where required and never echo them.",
    ),
    Rule(
        "SEC-006",
        "security",
        "low",
        "Third-party actions pinned to a mutable tag or branch",
        "Pin third-party actions to a commit SHA.",
    ),
    Rule(
        "SEC-007",
        "security",
        "high",
        "Workflow trigger grants untrusted code a privileged context",
        "Trigger on `pull_request` instead of `pull_request_target`/`workflow_run`, "
        "or keep the privileged job limited to trusted events.",
    ),
    Rule(
        "SEC-008",
        "security",
        "high",
        "Untrusted event data interpolated into a workflow shell command",
        "Pass untrusted values through environment variables and quote them, or use a "
        "script with input sanitising instead of `${{ github.event... }}` inside `run:`.",
    ),
    Rule(
        "SEC-009",
        "security",
        "high",
        "Remote script downloaded and piped into a shell",
        "Download the script, verify its checksum, and review it before executing; "
        "prefer a pinned, trusted tool instead of `curl ... | bash`.",
    ),
    Rule(
        "SEC-010",
        "security",
        "critical",
        "Connection string or URL with embedded credentials",
        "Move credentials out of connection strings into environment variables or a "
        "secrets manager, and rotate the exposed values.",
    ),
    # Configuration ----------------------------------------------------------
    Rule(
        "CFG-001",
        "configuration",
        "high",
        "Machine-specific absolute path in project files",
        "Use relative paths or configuration values resolved at runtime.",
    ),
    Rule(
        "CFG-002",
        "configuration",
        "medium",
        "Committed IDE/editor settings with machine-specific paths",
        "Ignore editor-local settings, commit shared templates only, and use portable paths.",
    ),
    Rule(
        "CFG-003",
        "configuration",
        "medium",
        "Configuration hardcoded without environment separation",
        "Drive environment-specific values from environment variables or config profiles.",
    ),
    Rule(
        "CFG-004",
        "configuration",
        "medium",
        "Debug mode enabled in production or deployment configuration",
        "Default to debug off and enable it explicitly for local development only.",
    ),
    # Collaboration ----------------------------------------------------------
    Rule(
        "COL-001",
        "collaboration",
        "high",
        "Unsafe pull-request or merge automation (auto-approve, auto-merge, force-push to main)",
        "Require review and restrict auto-merge or auto-approval workflows.",
    ),
    Rule(
        "COL-002",
        "collaboration",
        "low",
        "No contribution or review guidance",
        "Add CONTRIBUTING.md and a pull-request template.",
    ),
    # Build and release ------------------------------------------------------
    Rule(
        "BLD-001",
        "build_release",
        "medium",
        "Build or packaging is not validated in CI",
        "Add a build/package step to CI so breakage is caught before merge.",
    ),
    Rule(
        "BLD-002",
        "build_release",
        "high",
        "Risky deployment automation (force push, deploy on every push, no environment gate)",
        "Gate deployments on review, tags, or protected environments.",
    ),
    Rule(
        "BLD-003",
        "build_release",
        "low",
        "No release or versioning process",
        "Document a versioning and release process (tags/changelog).",
    ),
)

RULES_BY_ID: dict[str, Rule] = {rule.rule_id: rule for rule in RULES}


def rule_catalog_text() -> str:
    """Compact catalog for the system prompt."""
    lines = []
    for rule in RULES:
        lines.append(
            f"- {rule.rule_id} | {rule.category} | default {rule.severity} | {rule.title}"
        )
    return "\n".join(lines)


def rule_recommendation(rule_id: str) -> str | None:
    rule = RULES_BY_ID.get(rule_id)
    return rule.recommendation if rule else None


def default_severity(rule_id: str) -> str | None:
    rule = RULES_BY_ID.get(rule_id)
    return rule.severity if rule else None
