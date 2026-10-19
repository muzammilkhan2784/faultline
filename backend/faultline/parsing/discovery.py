"""File discovery and module-FQN derivation.

Everything about "which files count" and "what is this file called from
Python's point of view" lives here, so the rules are in one place and can be
unit-tested without touching a real repository.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from faultline.config import DEFAULT_EXCLUDE_DIRS

#: Glob patterns for directory *names* that are also excluded.
EXCLUDE_DIR_GLOBS: tuple[str, ...] = ("*.egg-info",)

#: A module is a test module when its path matches any of these.
TEST_DIR_NAMES: frozenset[str] = frozenset({"tests", "test", "testing"})
TEST_FILE_GLOBS: tuple[str, ...] = ("test_*.py", "*_test.py", "conftest.py")


@dataclass(slots=True, frozen=True)
class DiscoveredFile:
    """A Python file that survived the exclude rules."""

    path: Path
    """Absolute path on disk."""

    rel_path: str
    """POSIX-style path relative to the repo root — this is what we store."""

    module_fqn: str
    is_package: bool
    is_test: bool


def is_excluded(rel_parts: tuple[str, ...], exclude_dirs: Iterable[str]) -> bool:
    """True when any path segment matches an excluded directory name or glob."""
    excluded = set(exclude_dirs)
    for part in rel_parts:
        if part in excluded:
            return True
        if any(fnmatch.fnmatch(part, pattern) for pattern in EXCLUDE_DIR_GLOBS):
            return True
    return False


def is_test_path(rel_path: str) -> bool:
    """Apply the single, shared definition of "this is test code".

    A file is test code when it lives under a ``tests/`` directory **or** its
    name matches ``test_*.py`` / ``*_test.py`` / ``conftest.py``.
    """
    parts = tuple(rel_path.split("/"))
    if any(part in TEST_DIR_NAMES for part in parts[:-1]):
        return True
    name = parts[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in TEST_FILE_GLOBS)


def iter_python_files(
    root: Path, exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS
) -> Iterator[Path]:
    """Yield every ``*.py`` file under ``root``, skipping excluded directories.

    Pruning happens at the directory level, so a 400 MB ``node_modules`` is
    never walked at all rather than walked and filtered.
    """
    excluded = set(exclude_dirs)
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, OSError):  # pragma: no cover - platform dependent
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                name = entry.name
                if name in excluded or any(
                    fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_DIR_GLOBS
                ):
                    continue
                stack.append(entry)
            elif entry.suffix == ".py":
                yield entry


def derive_module_fqn(root: Path, path: Path) -> tuple[str, bool]:
    """Return ``(module_fqn, is_package)`` for ``path`` inside ``root``.

    The rule, matching how Python itself would import the file:

    1. Find the *top package*: the **highest** ancestor directory (below the repo
       root) that contains an ``__init__.py``. Everything above it is just where
       the source tree happens to live (``src/``).
    2. The FQN is the path from the top package down to the file, dotted.
    3. ``__init__.py`` contributes no segment of its own — it *is* its package.

    ``src/pkg/mod.py`` where ``src/`` has no ``__init__.py`` becomes ``pkg.mod``,
    which is what ``import pkg.mod`` would give you after a ``pip install -e .``.

    A directory *inside* a package that lacks its own ``__init__.py`` is a PEP 420
    namespace subpackage, and still contributes its name. Flask's ``sansio/`` is
    exactly this: stopping at the first missing ``__init__.py`` would name
    ``flask/sansio/app.py`` plain ``app`` and silently break every import of it.
    """
    rel = path.relative_to(root)
    is_package = path.name == "__init__.py"

    top: Path | None = None
    directory = path.parent
    # Walk up only as far as the repo root; never escape it.
    while directory != root and directory != directory.parent:
        if (directory / "__init__.py").exists():
            top = directory
        directory = directory.parent

    parts: list[str] = []
    if top is not None:
        parts = list(path.parent.relative_to(top.parent).parts)
    if not is_package:
        parts.append(rel.stem)
    if not parts:  # a bare `__init__.py` at the repo root
        parts = [root.name]
    return ".".join(parts), is_package


def discover(
    root: Path, exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS
) -> list[DiscoveredFile]:
    """Discover every parseable file under ``root`` with a unique module FQN.

    Two files can legitimately derive the same FQN — ``scripts/build.py`` and
    ``tools/build.py`` are both just ``build`` when neither directory is a
    package. Rather than silently dropping one, collisions fall back to the full
    relative path, which is unique by construction.
    """
    root = root.resolve()
    exclude = tuple(exclude_dirs)

    candidates: list[tuple[Path, str, str, bool]] = []
    for path in iter_python_files(root, exclude):
        rel_path = path.relative_to(root).as_posix()
        if is_excluded(tuple(rel_path.split("/")[:-1]), exclude):
            continue
        fqn, is_package = derive_module_fqn(root, path)
        candidates.append((path, rel_path, fqn, is_package))

    seen: dict[str, int] = {}
    for _, _, fqn, _ in candidates:
        seen[fqn] = seen.get(fqn, 0) + 1

    files: list[DiscoveredFile] = []
    for path, rel_path, fqn, is_package in candidates:
        if seen[fqn] > 1:
            stem = rel_path[:-3] if rel_path.endswith(".py") else rel_path
            fqn = stem.replace("/", ".")
            if fqn.endswith(".__init__"):
                fqn = fqn[: -len(".__init__")]
        files.append(
            DiscoveredFile(
                path=path,
                rel_path=rel_path,
                module_fqn=fqn,
                is_package=is_package,
                is_test=is_test_path(rel_path),
            )
        )
    files.sort(key=lambda f: f.module_fqn)
    return files


def count_loc(source: str) -> int:
    """Non-blank, non-comment lines.

    Docstrings are counted as code — separating them would need a second parse
    for a number nobody makes decisions on.
    """
    total = 0
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            total += 1
    return total
