"""Repository acquisition and path safety helpers.

The inspected repository is cloned read-only into a throw-away directory. The
agent never writes to the clone, never checks out the target's hooks, and never
executes code from the target.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

CLONE_TIMEOUT_SECONDS = 240
CLONE_DEPTH = 50
MAX_PATH_DEPTH = 12


class RepoError(RuntimeError):
    """Raised when a repository cannot be validated or cloned."""


# Common git failure signatures mapped to a plain-language cause.
_GIT_CAUSES: tuple[tuple[str, str], ...] = (
    ("repository not found", "the repository does not exist, or it is private"),
    ("could not read from remote repository", "the repository is private or needs authentication"),
    ("could not read username", "the repository is private or needs authentication"),
    ("authentication failed", "the repository is private or needs authentication"),
    ("permission denied", "the repository is private or needs authentication"),
    ("empty repository", "the repository has no commits"),
    ("not a git repository", "the URL does not point at a git repository"),
    ("could not resolve host", "the host name could not be resolved"),
    ("connection refused", "the connection was refused"),
    ("network is unreachable", "the network is unreachable"),
    ("failed to connect", "the connection failed"),
    ("operation timed out", "the operation timed out"),
    ("early eof", "the remote closed the connection early"),
    ("remote end hung up unexpectedly", "the remote closed the connection unexpectedly"),
)


def first_git_error_line(stderr: str) -> str:
    """Return the first meaningful line of git's stderr.

    Usage output and blank lines are skipped so a failure is never reported as
    git's help text.
    """
    for line in (stderr or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("usage:"):
            continue
        if stripped:
            return stripped
    return ""


def git_failure_cause(stderr: str) -> str:
    """Return a plain-language cause for a git failure."""
    lowered = (stderr or "").lower()
    for needle, cause in _GIT_CAUSES:
        if needle in lowered:
            return cause
    return "git could not complete the operation"


def git_failure_message(command: str, stderr: str, url: str | None = None) -> str:
    """Build the message used for every git failure.

    The message always names the repository, the likely cause and the first
    line of git's own error output - never git's usage text.
    """
    detail = first_git_error_line(stderr)
    cause = git_failure_cause(stderr)
    where = f" for {url}" if url else ""
    parts = [f"git {command} failed{where}: {cause}"]
    if detail:
        parts.append(f"git said: {detail}")
    return ". ".join(parts)


_GITHUB_HTTPS = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_GITHUB_SSH = re.compile(
    r"^git@github\.com:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_SHORT_FORM = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$")


@dataclass(frozen=True)
class RepoTarget:
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def https_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"


def parse_repo_url(value: str) -> RepoTarget:
    """Accept a GitHub HTTPS URL, SSH URL, or ``owner/repo`` shorthand."""
    raw = (value or "").strip()
    if not raw:
        raise RepoError("Repository URL is empty.")
    for pattern in (_GITHUB_HTTPS, _GITHUB_SSH, _SHORT_FORM):
        match = pattern.match(raw)
        if match:
            owner, name = match.group(1), match.group(2).removesuffix(".git")
            if name.lower() in {"", ".", ".."}:
                break
            return RepoTarget(owner=owner, name=name)
    raise RepoError(
        f"Unsupported repository reference {raw!r}. "
        "Expected a public GitHub URL such as https://github.com/owner/project."
    )


def _run_git(args: list[str], cwd: Path | None = None, url: str | None = None) -> str:
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
    }
    base_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    base_env.update(env)
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLONE_TIMEOUT_SECONDS,
            env=base_env,
            check=False,
        )
    except FileNotFoundError as exc:  # git not installed
        raise RepoError("git executable not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepoError(
            f"git {args[0]} exceeded the {CLONE_TIMEOUT_SECONDS}s timeout."
        ) from exc
    if completed.returncode != 0:
        stderr = completed.stderr or completed.stdout or ""
        # Never echo credential-bearing URLs back to the caller.
        stderr = re.sub(r"https?://[^@\s]+@", "https://<redacted>@", stderr)
        raise RepoError(git_failure_message(args[0], stderr, url))
    return completed.stdout


@dataclass
class Repository:
    """A shallow, read-only clone of the audited repository."""

    target: RepoTarget
    root: Path
    head_sha: str
    branch: str
    commit_date: str
    _tmpdir: tempfile.TemporaryDirectory | None = None

    def path_for(self, relative: str) -> Path:
        """Resolve *relative* inside the clone, rejecting escapes and ``.git``."""
        raw = (relative or "").strip().replace("\\", "/")
        if raw in {"", ".", "./"}:
            return self.root
        if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
            raise ValueError(f"Absolute paths are not allowed: {relative!r}")
        candidate = (self.root / raw).resolve()
        root = self.root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"Path escapes the repository: {relative!r}")
        relative_parts = candidate.relative_to(root).parts
        if any(part == ".git" for part in relative_parts):
            raise ValueError("The .git directory is not readable through file tools.")
        return candidate

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def cleanup(self) -> None:
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
            self._tmpdir = None


def clone_repository(value: str, depth: int = CLONE_DEPTH) -> Repository:
    """Clone *value* into a temporary directory owned by the caller."""
    target = parse_repo_url(value)
    tmpdir = tempfile.TemporaryDirectory(prefix="repo-audit-")
    destination = Path(tmpdir.name) / "repo"
    try:
        _run_git(
            [
                "-c",
                "advice.detachedHead=false",
                "clone",
                "--depth",
                str(max(1, depth)),
                "--no-tags",
                "--single-branch",
                "--quiet",
                target.https_url,
                str(destination),
            ],
            url=target.https_url,
        )
        if not (destination / ".git").exists():
            raise RepoError(
                f"git clone failed for {target.https_url}: the clone produced no "
                "working tree. The repository may be empty or unreachable."
            )
        head_sha = _run_git(
            ["rev-parse", "HEAD"], cwd=destination, url=target.https_url
        ).strip()
        branch = _run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=destination, url=target.https_url
        ).strip()
        commit_date = _run_git(
            ["log", "-1", "--format=%cI"], cwd=destination, url=target.https_url
        ).strip()
    except Exception:
        tmpdir.cleanup()
        raise
    return Repository(
        target=target,
        root=destination.resolve(),
        head_sha=head_sha,
        branch=branch,
        commit_date=commit_date,
        _tmpdir=tmpdir,
    )


def is_probably_binary(path: Path, sample_size: int = 4096) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(sample_size)
    except OSError:
        return True
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    return False


def human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} GB"


def iter_repo_files(root: Path, max_files: int = 20000) -> list[Path]:
    """Return regular files in the clone, skipping ``.git`` and symlinks."""
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= max_files:
            break
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if ".git" in path.relative_to(root).parts:
                continue
            if len(path.relative_to(root).parts) > MAX_PATH_DEPTH:
                continue
        except OSError:
            continue
        files.append(path)
    files.sort(key=lambda item: item.as_posix())
    return files


def github_repo_url(value: str) -> str:
    """Canonical https URL used in the report."""
    target = parse_repo_url(value)
    return f"https://github.com/{target.slug}"
