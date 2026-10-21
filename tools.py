"""Inspection tools exposed to the LLM.

Every tool is read-only, bounded, and path-safe. Tool failures are reported as
plain text so the model can adapt instead of crashing the run.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repo import Repository, human_size, is_probably_binary, iter_repo_files
from secrets import redact_secrets

MAX_READ_BYTES = 1_500_000
MAX_READ_LINES = 600
MAX_SEARCH_FILE_BYTES = 2_000_000
MAX_PATTERN_LENGTH = 300
DEFAULT_LIST_LIMIT = 250


class ToolError(RuntimeError):
    """Recoverable tool failure surfaced to the model."""


@dataclass
class ToolCallRecord:
    step: int
    name: str
    arguments: dict
    output_chars: int
    error: bool = False


@dataclass
class ToolBox:
    """Bound to one cloned repository."""

    repository: Repository
    calls: list[ToolCallRecord] = field(default_factory=list)
    _cache: dict[tuple[str, str], str] = field(default_factory=dict)

    # -- helpers ---------------------------------------------------------
    def _resolve(self, path: str) -> Path:
        try:
            return self.repository.path_for(path)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    def _display(self, path: Path) -> str:
        try:
            return self.repository.relative(path)
        except ValueError:
            return path.name

    # -- list_files ------------------------------------------------------
    def list_files(
        self, path: str = "", pattern: str = "", max_results: int = DEFAULT_LIST_LIMIT
    ) -> str:
        base = self._resolve(path)
        if not base.exists():
            raise ToolError(f"Path does not exist: {path!r}")
        limit = max(1, min(int(max_results or DEFAULT_LIST_LIMIT), 1000))

        if base.is_file():
            return f"{self._display(base)} ({human_size(base.stat().st_size)})"

        entries: list[str] = []
        truncated = False
        total = 0
        for current_root, dirnames, filenames in _walk(base, skip_git=True):
            dirnames.sort()
            filenames.sort()
            for name in filenames:
                full = Path(current_root) / name
                try:
                    if full.is_symlink() or not full.is_file():
                        continue
                except OSError:
                    continue
                relative = self._display(full)
                if pattern and not _matches(relative, pattern):
                    continue
                total += 1
                if len(entries) >= limit:
                    truncated = True
                    continue
                entries.append(f"{relative} ({human_size(full.stat().st_size)})")

        if total == 0:
            scope = pattern or (path or "the repository")
            return f"No files matched {scope!r}."

        header = f"{total} file(s) matched"
        if truncated:
            header += f"; showing the first {len(entries)}"
        header += f" under {path or '.'}" + (f" (pattern: {pattern})" if pattern else "")
        body = "\n".join(entries)
        if truncated:
            body += "\n... truncated; narrow with `path` or `pattern`."
        return f"{header}\n{body}"

    # -- read_file -------------------------------------------------------
    def read_file(self, path: str, start_line: int = 1, end_line: int = 200) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise ToolError(f"File does not exist: {path!r}")
        if target.is_dir():
            listing = self.list_files(path=path, max_results=80)
            return f"{path!r} is a directory. Use list_files.\n{listing}"
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise ToolError(f"Cannot stat {path!r}: {exc}") from exc
        if size > MAX_READ_BYTES:
            return (
                f"{path} is {human_size(size)}, larger than the read limit "
                f"({human_size(MAX_READ_BYTES)}). Read a line range or search inside it."
            )
        if is_probably_binary(target):
            return f"{path} appears to be binary and cannot be read as text ({human_size(size)})."

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"Cannot read {path!r}: {exc}") from exc

        lines = text.splitlines()
        if not lines:
            return f"{path} is empty (0 lines)."
        total = len(lines)
        start = max(1, int(start_line or 1))
        end = int(end_line or (start + 199))
        if end < start:
            end = start
        end = min(end, total, start + MAX_READ_LINES - 1)

        excerpt = [
            f"{number:>5}: {line}" for number, line in enumerate(lines[start - 1 : end], start)
        ]
        header = f"{path} (lines {start}-{end} of {total})"
        footer = ""
        if end < total:
            footer = f"\n... {total - end} more line(s); call read_file again with a higher start_line."
        return redact_secrets("\n".join([header, *excerpt]) + footer)

    # -- search_code -----------------------------------------------------
    def search_code(
        self,
        pattern: str,
        path: str = "",
        glob: str = "",
        case_sensitive: bool = False,
        max_results: int = 60,
    ) -> str:
        raw = (pattern or "").strip()
        if not raw:
            raise ToolError("search_code requires a non-empty pattern.")
        if len(raw) > MAX_PATTERN_LENGTH:
            raise ToolError(f"Pattern is too long (max {MAX_PATTERN_LENGTH} characters).")
        try:
            expression = re.compile(raw, 0 if case_sensitive else re.IGNORECASE)
            used_regex = True
        except re.error:
            expression = re.compile(re.escape(raw), 0 if case_sensitive else re.IGNORECASE)
            used_regex = False

        base = self._resolve(path)
        if not base.exists():
            raise ToolError(f"Path does not exist: {path!r}")
        limit = max(1, min(int(max_results or 60), 200))

        matches: list[str] = []
        scanned = 0
        skipped = 0
        truncated = False
        for candidate in _iter_files(base):
            if len(matches) >= limit:
                truncated = True
                break
            relative = self._display(candidate)
            if glob and not _matches(relative, glob):
                continue
            try:
                if candidate.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    skipped += 1
                    continue
            except OSError:
                skipped += 1
                continue
            if is_probably_binary(candidate):
                skipped += 1
                continue
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                skipped += 1
                continue
            scanned += 1
            for number, line in enumerate(content.splitlines(), 1):
                if expression.search(line):
                    snippet = line.strip()
                    if len(snippet) > 240:
                        snippet = snippet[:240] + "..."
                    matches.append(f"{relative}:{number}: {redact_secrets(snippet)}")
                    if len(matches) >= limit:
                        truncated = True
                        break

        mode = "regex" if used_regex else "literal (invalid regex)"
        if not matches:
            return (
                f"No matches for {raw!r} ({mode}) in {scanned} file(s)"
                + (f"; {skipped} file(s) skipped as binary/large" if skipped else ".")
            )
        header = f"{len(matches)} match(es) for {raw!r} ({mode}) across {scanned} file(s)"
        if truncated:
            header += f" - truncated at {limit}"
        return "\n".join([header, *matches])

    # -- git_log ---------------------------------------------------------
    def git_log(self, limit: int = 20, path: str = "") -> str:
        count = max(1, min(int(limit or 20), 100))
        args = [
            "log",
            "--no-decorate",
            "--date=short",
            f"--pretty=format:%h %ad %an: %s",
            f"-n{count}",
        ]
        if path:
            target = self._resolve(path)
            if not target.exists():
                raise ToolError(f"Path does not exist: {path!r}")
            args.extend(["--", self._display(target)])
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(self.repository.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolError(f"git log failed: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            raise ToolError("git log failed: " + (detail[-1] if detail else "unknown error"))
        output = completed.stdout.strip()
        if not output:
            return "No commits found in the cloned history."
        return f"Last {count} commit(s) (newest first):\n{output}"

    # -- dispatch --------------------------------------------------------
    def execute(self, name: str, arguments: dict, step: int = 0) -> str:
        signature = (name, json.dumps(arguments, sort_keys=True, default=str))
        if signature in self._cache:
            cached = self._cache[signature]
            return f"[duplicate call - result already returned]\n{cached}"
        try:
            if name == "list_files":
                result = self.list_files(**arguments)
            elif name == "read_file":
                result = self.read_file(**arguments)
            elif name == "search_code":
                result = self.search_code(**arguments)
            elif name == "git_log":
                result = self.git_log(**arguments)
            else:
                raise ToolError(f"Unknown tool: {name}")
            error = False
        except ToolError as exc:
            result = f"ERROR: {exc}"
            error = True
        except TypeError as exc:
            result = f"ERROR: invalid arguments for {name}: {exc}"
            error = True
        except Exception as exc:  # never let a tool crash the audit
            result = f"ERROR: {name} failed: {type(exc).__name__}: {exc}"
            error = True
        self._cache[signature] = result
        self.calls.append(
            ToolCallRecord(
                step=step,
                name=name,
                arguments=arguments,
                output_chars=len(result),
                error=error,
            )
        )
        return result

    # -- initial observation --------------------------------------------
    def _manifest_signals(self) -> list[str]:
        """Cheap, factual hints that point the model at manifests worth reading."""
        signals: list[str] = []
        root = self.repository.root

        requirements = root / "requirements.txt"
        if requirements.is_file():
            try:
                text = requirements.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            entries = [
                line.split("#", 1)[0].strip()
                for line in text.splitlines()
                if line.split("#", 1)[0].strip()
                and not line.split("#", 1)[0].strip().startswith("-")
            ]
            unpinned = [entry for entry in entries if "==" not in entry.replace(" ", "")]
            signals.append(
                f"requirements.txt: {len(entries)} direct entr"
                f"{'y' if len(entries) == 1 else 'ies'}, "
                f"{len(unpinned)} without an exact == pin"
            )

        package = root / "package.json"
        if package.is_file():
            try:
                data = json.loads(package.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            dependencies = data.get("dependencies") if isinstance(data, dict) else None
            dev_dependencies = data.get("devDependencies") if isinstance(data, dict) else None
            if dependencies or dev_dependencies:
                signals.append(
                    f"package.json: {len(dependencies or {})} runtime, "
                    f"{len(dev_dependencies or {})} dev dependencies"
                )

        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            declares = bool(re.search(r"^\s*dependencies\s*=", text, re.M)) or (
                "[tool.poetry.dependencies]" in text
            )
            signals.append(
                "pyproject.toml: declares dependencies"
                if declares
                else "pyproject.toml: no dependency declaration detected"
            )
        return signals

    def overview(self) -> str:
        files = iter_repo_files(self.repository.root)
        total_size = sum(path.stat().st_size for path in files if path.exists())
        top_level = sorted(
            entry.name + ("/" if entry.is_dir() else "")
            for entry in self.repository.root.iterdir()
            if entry.name != ".git"
        )
        markers = {
            "README": ("README.md", "README.rst", "README.txt", "README"),
            "LICENSE": ("LICENSE", "LICENSE.md", "LICENSE.txt"),
            ".gitignore": (".gitignore",),
            "CI workflows": (".github/workflows",),
            "requirements.txt": ("requirements.txt",),
            "pyproject.toml": ("pyproject.toml",),
            "setup.py/setup.cfg": ("setup.py", "setup.cfg"),
            "package.json": ("package.json",),
            "lockfile": (
                "poetry.lock",
                "uv.lock",
                "Pipfile.lock",
                "package-lock.json",
                "yarn.lock",
                "pnpm-lock.yaml",
            ),
            "Dockerfile": ("Dockerfile", "dockerfile"),
            "docker-compose": ("docker-compose.yml", "docker-compose.yaml", "compose.yml"),
            "tests dir": ("tests", "test"),
            "Makefile": ("Makefile",),
            "pre-commit": (".pre-commit-config.yaml",),
            "CONTRIBUTING": ("CONTRIBUTING.md", "CONTRIBUTING.rst"),
            "CODEOWNERS": (".github/CODEOWNERS", "CODEOWNERS"),
            "env file": (".env", ".env.local", ".env.production"),
        }
        present: list[str] = []
        absent: list[str] = []
        found_paths: list[str] = []
        for label, candidates in markers.items():
            matches = [candidate for candidate in candidates if (self.repository.root / candidate).exists()]
            if matches:
                present.append(label)
                found_paths.extend(matches)
            else:
                absent.append(label)

        workflows = sorted(
            path.relative_to(self.repository.root).as_posix()
            for path in (self.repository.root / ".github" / "workflows").glob("*")
            if path.is_file()
        ) if (self.repository.root / ".github" / "workflows").is_dir() else []

        test_files = [
            self.repository.relative(path)
            for path in files
            if re.search(r"(^|/)(tests?|spec)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.(js|ts|tsx)$", self.repository.relative(path))
        ]

        parts = [
            f"Repository: {self.repository.target.slug}",
            f"Default branch (cloned): {self.repository.branch}",
            f"HEAD: {self.repository.head_sha[:12]} committed {self.repository.commit_date}",
            f"Tracked files in clone: {len(files)} (~{human_size(total_size)})",
            "Top level: " + (", ".join(top_level) if top_level else "(empty)"),
            "Present: " + (", ".join(present) if present else "none of the common markers"),
            "Not found at root: " + (", ".join(absent) if absent else "none"),
            f"Test-like files: {len(test_files)}"
            + (f" (e.g. {', '.join(test_files[:6])})" if test_files else ""),
        ]
        if workflows:
            parts.append(f"Workflow files: {', '.join(workflows)}")
            parts.append("Note: workflows were not read yet; inspect the ones relevant to your next step.")
        parts.extend(self._manifest_signals())
        parts.append(
            "Note: file presence alone is not evidence; confirm contents with read_file or search_code before reporting."
        )
        return redact_secrets("\n".join(parts))


# -- module helpers ------------------------------------------------------
def _walk(base: Path, skip_git: bool = True):
    import os

    for current_root, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not (skip_git and name == ".git") and not (Path(current_root) / name).is_symlink()
        )
        yield current_root, dirnames, filenames


def _iter_files(base: Path):
    if base.is_file():
        yield base
        return
    for current_root, _dirnames, filenames in _walk(base):
        for name in sorted(filenames):
            candidate = Path(current_root) / name
            if candidate.is_symlink():
                continue
            yield candidate


def _matches(relative: str, pattern: str) -> bool:
    pattern = pattern.strip().replace("\\", "/")
    if not pattern:
        return True
    if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(Path(relative).name, pattern):
        return True
    try:
        return Path(relative).match(pattern)
    except ValueError:
        return False


def tool_schemas() -> list[dict]:
    """OpenAI-style function definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in the repository, optionally under a directory and filtered by a glob pattern.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory or file, relative to the repository root. Empty means root."},
                        "pattern": {"type": "string", "description": "Optional glob such as '*.py', '*.yml' or '.github/**'."},
                        "max_results": {"type": "integer", "description": "Maximum entries to return (default 250, max 1000)."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file with line numbers. Use start_line/end_line for large files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to the repository root."},
                        "start_line": {"type": "integer", "description": "First line to return (1-based)."},
                        "end_line": {"type": "integer", "description": "Last line to return."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": "Regex search across repository text files. Returns path:line: snippet.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regular expression (falls back to literal if invalid)."},
                        "path": {"type": "string", "description": "Optional directory to search under."},
                        "glob": {"type": "string", "description": "Optional glob filter, e.g. '*.py'."},
                        "case_sensitive": {"type": "boolean", "description": "Case-sensitive match (default false)."},
                        "max_results": {"type": "integer", "description": "Maximum matches (default 60, max 200)."},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_log",
                "description": "Show recent commit history, optionally for one path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Number of commits (default 20, max 100)."},
                        "path": {"type": "string", "description": "Optional file or directory to filter by."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_report",
                "description": "Submit the final audit report. Call exactly once, when the investigation is complete.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "violations": {
                            "type": "array",
                            "description": "All proven violations; an empty array means the repository is clean.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "rule_id": {"type": "string", "description": "Catalog rule id, e.g. SEC-001."},
                                    "category": {"type": "string", "description": "One of the inspection categories."},
                                    "severity": {"type": "string", "description": "critical, high, medium or low."},
                                    "file": {"type": "string", "description": "Repository-relative path, or null when repository-wide."},
                                    "line": {"type": "integer", "description": "1-based line number, or null."},
                                    "description": {"type": "string", "description": "One-sentence statement of the defect."},
                                    "evidence": {"type": "string", "description": "Concrete quoted evidence with file/line references."},
                                    "recommendation": {"type": "string", "description": "Actionable remediation."},
                                },
                                "required": [
                                    "rule_id",
                                    "category",
                                    "severity",
                                    "description",
                                    "evidence",
                                    "recommendation",
                                ],
                            },
                        }
                    },
                    "required": ["violations"],
                },
            },
        },
    ]


def is_submission_tool(name: str) -> bool:
    return name == "submit_report"
