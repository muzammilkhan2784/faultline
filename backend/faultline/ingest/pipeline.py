"""Orchestration: discover → parse → resolve → write.

The analysis half is deliberately separate from the writing half.
:func:`analyse_path` needs no database at all, which is what lets the benchmark
harness and the accuracy harness run against real repositories on a laptop with
nothing installed, and what makes "how long did parsing take, separately from
writing" a question with an answer.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faultline.config import DEFAULT_EXCLUDE_DIRS, Settings, get_settings
from faultline.graph.client import GraphClient
from faultline.graph.writer import GraphWriter, WriteResult
from faultline.ingest.repo_source import (
    RepoCheckout,
    SourceError,
    checkout,
    explain_no_python_files,
)
from faultline.logging import get_logger
from faultline.models import ModuleFacts, ResolutionStats, ResolvedRepo
from faultline.parsing.discovery import discover
from faultline.parsing.module_parser import parse_file
from faultline.parsing.resolver import Resolver

logger = get_logger(__name__)

STATUS_PENDING = "pending"
STATUS_PARSING = "parsing"
STATUS_WRITING = "writing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"


@dataclass(slots=True)
class Analysis:
    """The result of parsing and resolving a repository, with timings."""

    resolved: ResolvedRepo
    file_count: int
    loc: int
    parse_seconds: float
    resolve_seconds: float
    parse_failures: list[dict[str, str]] = field(default_factory=list)
    resolver: Resolver | None = None
    """Kept so callers (the accuracy harness) can resolve individual call sites."""

    @property
    def stats(self) -> ResolutionStats:
        return self.resolved.stats

    @property
    def node_count(self) -> int:
        modules = self.resolved.modules
        return (
            len(modules)
            + sum(len(m.classes) for m in modules)
            + sum(len(m.functions) for m in modules)
        )

    @property
    def edge_count(self) -> int:
        return (
            len(self.resolved.calls)
            + len(self.resolved.imports)
            + len(self.resolved.inherits)
            + len(self.resolved.instantiations)
        )


@dataclass(slots=True)
class IngestResult:
    repo_id: str
    name: str
    analysis: Analysis
    write: WriteResult
    total_seconds: float


def analyse_path(
    root: Path,
    *,
    exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS,
    progress_every: int = 500,
) -> Analysis:
    """Parse and resolve a repository on disk. Touches no database.

    Parse failures are recorded, never raised: every real repository contains a
    file that will not parse, and a tool that dies on the first one is useless.
    """
    started = time.perf_counter()
    files = discover(root, exclude_dirs)

    modules: list[ModuleFacts] = []
    failures: list[dict[str, str]] = []
    loc = 0
    for index, discovered in enumerate(files, start=1):
        facts = parse_file(discovered)
        modules.append(facts)
        loc += facts.loc
        if facts.parse_error:
            failures.append({"path": facts.path, "error": facts.parse_error})
        if progress_every and index % progress_every == 0:
            logger.info("parsing", extra={"parsed": index, "total": len(files)})

    parse_seconds = time.perf_counter() - started

    resolve_started = time.perf_counter()
    resolver = Resolver(modules)
    resolved = resolver.resolve()
    resolve_seconds = time.perf_counter() - resolve_started

    analysis = Analysis(
        resolved=resolved,
        file_count=len(files),
        loc=loc,
        parse_seconds=round(parse_seconds, 3),
        resolve_seconds=round(resolve_seconds, 3),
        parse_failures=failures,
        resolver=resolver,
    )
    logger.info(
        "analysis complete",
        extra={
            "files": analysis.file_count,
            "loc": analysis.loc,
            "parse_seconds": analysis.parse_seconds,
            "resolve_seconds": analysis.resolve_seconds,
            "parse_failures": len(failures),
            **analysis.stats.to_dict(),
        },
    )
    return analysis


class IngestPipeline:
    """Runs an ingestion and keeps the ``:Repo`` node's status current.

    Analysing a large repository takes tens of seconds — too long for a
    synchronous request to feel right, not long enough to justify a job queue.
    The status field is the compromise: the client polls ``GET /repos/{id}``
    and watches ``pending → parsing → writing → ready``.
    """

    def __init__(self, client: GraphClient, settings: Settings | None = None) -> None:
        self.client = client
        self.settings = settings or get_settings()
        self.writer = GraphWriter(client)

    def register(self, source: str, name: str | None = None, ref: str | None = None) -> str:
        """Create the ``:Repo`` node up front so the client has something to poll."""
        repo_id = str(uuid.uuid4())
        self.writer.upsert_repo(
            repo_id,
            {
                "name": name or _default_name(source),
                "source": source,
                "ref": ref,
                "status": STATUS_PENDING,
                "analyzed_at": _now(),
                "error": None,
            },
        )
        return repo_id

    def run(
        self, repo_id: str, source: str, *, ref: str | None = None, name: str | None = None
    ) -> IngestResult | None:
        """Fetch, analyse and write. Failures land on the node, not the caller.

        This runs in a background task, so there is nobody left to raise at: a
        failure is recorded as ``status="failed"`` with the reason attached.
        """
        started = time.perf_counter()
        try:
            with checkout(source, ref=ref, settings=self.settings) as repo:
                return self._run_checked(repo_id, repo, name=name, started=started)
        except SourceError as exc:
            logger.warning("ingest rejected", extra={"repo_id": repo_id, "error": str(exc)})
            self.writer.set_status(repo_id, STATUS_FAILED, error=str(exc))
            return None
        except Exception as exc:
            logger.exception("ingest failed", extra={"repo_id": repo_id})
            self.writer.set_status(repo_id, STATUS_FAILED, error=f"{type(exc).__name__}: {exc}")
            return None

    def _run_checked(
        self, repo_id: str, repo: RepoCheckout, *, name: str | None, started: float
    ) -> IngestResult:
        self.writer.set_status(repo_id, STATUS_PARSING, name=name or repo.name)
        analysis = analyse_path(repo.root, exclude_dirs=self.settings.exclude_dirs)
        if analysis.file_count == 0:
            # An empty graph is not a finished analysis: say what the folder is instead.
            raise SourceError(explain_no_python_files(repo.root, self.settings.exclude_dirs))

        self.writer.set_status(repo_id, STATUS_WRITING)
        write = self.writer.write_repo(repo_id, analysis.resolved)

        total = round(time.perf_counter() - started, 3)
        self.writer.set_status(
            repo_id,
            STATUS_READY,
            name=name or repo.name,
            source=repo.source,
            commit_sha=repo.commit_sha,
            analyzed_at=_now(),
            file_count=analysis.file_count,
            loc=analysis.loc,
            parse_seconds=analysis.parse_seconds,
            resolve_seconds=analysis.resolve_seconds,
            write_seconds=write.seconds,
            total_seconds=total,
            node_count=write.nodes,
            edge_count=write.edges,
            parse_failures=len(analysis.parse_failures),
            error=None,
            **_stat_properties(analysis.stats),
        )
        return IngestResult(
            repo_id=repo_id,
            name=name or repo.name,
            analysis=analysis,
            write=write,
            total_seconds=total,
        )

    def delete(self, repo_id: str) -> None:
        self.writer.delete_repo(repo_id)


def _stat_properties(stats: ResolutionStats) -> dict[str, Any]:
    """Resolution counters flattened onto the ``:Repo`` node.

    They are stored rather than recomputed so that ``/stats`` reports what the
    resolver actually decided, including the calls it deliberately dropped —
    which never became edges and therefore cannot be counted from the graph.
    """
    return {f"stat_{key}": value for key, value in stats.to_dict().items()}


def _default_name(source: str) -> str:
    cleaned = source.rstrip("/")
    tail = cleaned.rsplit("/", 1)[-1] or cleaned
    return tail[:-4] if tail.endswith(".git") else tail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
