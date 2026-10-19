"""Getting a repository onto disk, safely.

``POST /repos`` accepts a local path or a git URL, which means accepting
user-controlled input that touches the filesystem and the network. Every
mitigation lives in this module so the whole attack surface can be read in one
sitting:

* only ``https://`` git URLs — no ``file://``, ``git://``, ``ssh://`` or ``git@``
* clones land in a fresh :func:`tempfile.mkdtemp`, removed in a ``finally``
* ``git clone --depth 1`` with a timeout — no history is needed
* ``subprocess.run`` with an argument list, never ``shell=True``
* a local path must sit inside ``ALLOWED_ROOT`` when that is configured
* size caps on both bytes and Python-file count, checked after the clone
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from faultline.config import Settings, get_settings
from faultline.logging import get_logger

logger = get_logger(__name__)

#: A git ref we are willing to hand to the CLI. Deliberately strict: no spaces,
#: no leading dash (which would be read as an option), no shell metacharacters.
REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")

#: A full or abbreviated commit SHA.
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")

BYTES_PER_MB = 1024 * 1024


class SourceError(ValueError):
    """The requested source was rejected, or could not be fetched."""


@dataclass(slots=True)
class RepoCheckout:
    root: Path
    name: str
    source: str
    commit_sha: str | None
    is_temporary: bool


def looks_like_url(source: str) -> bool:
    return "://" in source or source.startswith("git@")


def repo_name_from_url(url: str) -> str:
    tail = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return tail[:-4] if tail.endswith(".git") else tail or "repo"


def validate_url(url: str) -> str:
    """Accept only plain ``https://`` git URLs."""
    if url.startswith("git@"):
        raise SourceError("SSH git URLs are not accepted; use an https:// URL")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SourceError(f"unsupported scheme {parsed.scheme or '(none)'!r}; use https://")
    if not parsed.netloc:
        raise SourceError("git URL is missing a host")
    if "@" in parsed.netloc:
        raise SourceError("credentials in the URL are not accepted")
    return url


_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _visible_folders(allowed_root: str) -> str:
    """Name the folders the server *can* see, so a wrong path has an obvious fix."""
    try:
        root = Path(allowed_root).expanduser().resolve()
        names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return ""
    if not names:
        return f" The folder {root} is empty: copy a project into it first."
    shown = ", ".join(f"{root.as_posix()}/{n}" for n in names[:8])
    return f" Folders the server can see: {shown}" + (" ..." if len(names) > 8 else ".")


def _not_found(source: str, settings: Settings) -> SourceError:
    message = f"path not found: {source}"
    if not settings.allowed_root:
        return SourceError(message)
    root = Path(settings.allowed_root).as_posix()
    if _WINDOWS_PATH.match(source):
        message += (
            f". That is a Windows path, but the server runs in a container and cannot see your "
            f"drives: it only sees {root}. Copy the project into the .repos folder next to "
            f"docker-compose.yml, then enter {root}/<folder-name>."
        )
    else:
        message += f". The server can only read inside {root}."
    return SourceError(message + _visible_folders(settings.allowed_root))


def validate_local_path(source: str, settings: Settings) -> Path:
    """Resolve a local path and confine it to ``ALLOWED_ROOT`` when one is set."""
    path = Path(source).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _not_found(source, settings) from exc
    if not resolved.is_dir():
        raise SourceError(f"not a directory: {source}")

    allowed_root = settings.allowed_root
    if allowed_root:
        root = Path(allowed_root).expanduser().resolve()
        if resolved != root and root not in resolved.parents:
            raise SourceError(f"path is outside the allowed root {root}")
    return resolved


#: Extensions worth naming when a repository turns out not to be Python.
OTHER_LANGUAGES: dict[str, str] = {
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".swift": "Swift",
}
MAX_FILES_SCANNED = 20_000


def explain_no_python_files(root: Path, exclude_dirs: Iterable[str]) -> str:
    """Say *why* a repository produced an empty graph, instead of reporting zeros.

    An analysis with no Python files is not a success with nothing in it: it is
    almost always the wrong kind of project, and the useful answer is which kind.
    """
    excluded = set(exclude_dirs)
    languages: Counter[str] = Counter()
    notebooks = 0
    scanned = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excluded]
        for filename in filenames:
            scanned += 1
            suffix = Path(filename).suffix.lower()
            if suffix == ".ipynb":
                notebooks += 1
            elif suffix in OTHER_LANGUAGES:
                languages[OTHER_LANGUAGES[suffix]] += 1
        if scanned >= MAX_FILES_SCANNED:
            break

    message = "No Python (.py) files were found. Faultline analyses Python only."
    if languages:
        language, count = languages.most_common(1)[0]
        noun = "file" if count == 1 else "files"
        message += f" This looks like a {language} project ({count:,} {language} {noun})."
    if notebooks:
        noun = "notebook" if notebooks == 1 else "notebooks"
        message += (
            f" It has {notebooks:,} Jupyter {noun} (.ipynb), which are not read; export one "
            "to a script with `jupyter nbconvert --to script` and try again."
        )
    if not languages and not notebooks:
        message += (
            " The folder may be empty, or the code may sit in a directory Faultline skips "
            "(virtual environments, node_modules, build output)."
        )
    return message


def check_size(root: Path, settings: Settings) -> tuple[int, int]:
    """Abort before parsing if the tree is implausibly large.

    Returns ``(python_files, total_bytes)``.
    """
    total_bytes = 0
    python_files = 0
    max_bytes = settings.max_repo_mb * BYTES_PER_MB
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            total_bytes += path.stat().st_size
        except OSError:  # pragma: no cover - races on a live filesystem
            continue
        if path.suffix == ".py":
            python_files += 1
        if total_bytes > max_bytes:
            raise SourceError(f"repository exceeds {settings.max_repo_mb} MB")
        if python_files > settings.max_repo_files:
            raise SourceError(f"repository exceeds {settings.max_repo_files} Python files")
    return python_files, total_bytes


def _git(args: list[str], *, cwd: Path | None = None, timeout: int = 60) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SourceError("git is not installed on this machine") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git timed out after {timeout}s") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise SourceError(f"git failed: {detail[-1] if detail else 'unknown error'}")
    return completed.stdout.strip()


def clone(url: str, destination: Path, *, ref: str | None, settings: Settings) -> str | None:
    """Shallow-clone ``url`` into ``destination`` and return the commit SHA."""
    args = ["clone", "--depth", "1", "--single-branch", "--no-tags", "--quiet"]
    if ref:
        if not REF_PATTERN.match(ref):
            raise SourceError(f"invalid git ref: {ref!r}")
        args += ["--branch", ref]
    args += ["--", url, str(destination)]

    logger.info("cloning", extra={"url": url, "ref": ref})
    _git(args, timeout=settings.clone_timeout_seconds)
    try:
        return _git(["rev-parse", "HEAD"], cwd=destination)
    except SourceError:  # pragma: no cover - a clone without a HEAD is pathological
        return None


def fetch_commit(url: str, commit: str, destination: Path, *, timeout: int = 300) -> str:
    """Materialise ``url`` at exactly ``commit`` in ``destination``; return the SHA.

    The accuracy labels are line numbers in a specific version of a specific
    repository, so "the latest flask" would silently invalidate them. Fetching by
    SHA (``git fetch --depth 1 origin <sha>``) keeps the download shallow while
    pinning the content. Same restrictions as :func:`clone`: https only, an
    argument list, never a shell.
    """
    validate_url(url)
    if not COMMIT_PATTERN.match(commit):
        raise SourceError(f"invalid commit: {commit!r}")
    destination.mkdir(parents=True, exist_ok=True)
    _git(["init", "--quiet", str(destination)], timeout=timeout)
    _git(["remote", "add", "origin", url], cwd=destination, timeout=timeout)
    _git(["fetch", "--quiet", "--depth", "1", "origin", commit], cwd=destination, timeout=timeout)
    _git(["checkout", "--quiet", "FETCH_HEAD"], cwd=destination, timeout=timeout)
    return _git(["rev-parse", "HEAD"], cwd=destination)


@contextmanager
def checkout(
    source: str, *, ref: str | None = None, settings: Settings | None = None
) -> Iterator[RepoCheckout]:
    """Yield a :class:`RepoCheckout` for ``source``, cleaning up afterwards.

    A local path is used in place and never deleted. A git URL is cloned into a
    temporary directory that is removed on the way out, whatever happens.
    """
    settings = settings or get_settings()

    if not looks_like_url(source):
        root = validate_local_path(source, settings)
        check_size(root, settings)
        yield RepoCheckout(
            root=root,
            name=root.name,
            source=str(root),
            commit_sha=None,
            is_temporary=False,
        )
        return

    url = validate_url(source)
    temp_dir = Path(tempfile.mkdtemp(prefix="faultline-"))
    try:
        destination = temp_dir / "repo"
        commit_sha = clone(url, destination, ref=ref, settings=settings)
        check_size(destination, settings)
        yield RepoCheckout(
            root=destination,
            name=repo_name_from_url(url),
            source=url,
            commit_sha=commit_sha,
            is_temporary=True,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
