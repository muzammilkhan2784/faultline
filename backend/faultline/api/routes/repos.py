"""Repository lifecycle: create, list, inspect, delete, stats."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from faultline.analysis import briefing
from faultline.api.deps import ClientDep, PipelineDep, SettingsDep, load_repo
from faultline.api.schemas import (
    Counts,
    ParseFailure,
    RepoCreate,
    RepoCreated,
    RepoDetail,
    RepoStats,
    RepoSummary,
    ResolutionBreakdown,
    Timings,
)
from faultline.graph import cypher
from faultline.ingest.repo_source import (
    SourceError,
    looks_like_url,
    validate_local_path,
    validate_url,
)

router = APIRouter(prefix="/repos", tags=["repos"])

STAT_PREFIX = "stat_"


def build_stats(
    repo_id: str, repo: dict[str, Any], counts: dict[str, Any], failures: list[dict[str, Any]]
) -> RepoStats:
    """Assemble the stats payload from the repo node and live graph counts."""
    resolution = {
        key[len(STAT_PREFIX) :]: value for key, value in repo.items() if key.startswith(STAT_PREFIX)
    }
    breakdown = ResolutionBreakdown.model_validate(resolution)
    failure_count = int(repo.get("parse_failures") or len(failures))
    modules = counts.get("modules", 0)
    summary = f"Parsed {modules:,} modules"
    if failure_count:
        summary += f" ({failure_count} failed: see parse_failures)"
    return RepoStats(
        repo_id=repo_id,
        counts=Counts.model_validate(counts),
        timings=Timings.model_validate(repo),
        resolution=breakdown,
        parse_failures=[ParseFailure.model_validate(f) for f in failures],
        summary=summary,
    )


@router.post("", response_model=RepoCreated, status_code=status.HTTP_202_ACCEPTED)
def create_repo(
    body: RepoCreate,
    background: BackgroundTasks,
    pipeline: PipelineDep,
    settings: SettingsDep,
) -> RepoCreated:
    """Start analysing a repository.

    Cheap validation happens here, synchronously, so a bad URL or a path outside
    the allowed root is an immediate 400. The clone and the analysis run in a
    background task; their outcome shows up as ``status`` on ``GET /repos/{id}``.
    A real deployment would put a job queue here -- this is a deliberate
    simplification, not an oversight.
    """
    try:
        if looks_like_url(body.source):
            validate_url(body.source)
        else:
            validate_local_path(body.source, settings)
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    repo_id = pipeline.register(body.source, name=body.name, ref=body.ref)
    background.add_task(pipeline.run, repo_id, body.source, ref=body.ref, name=body.name)
    return RepoCreated(repo_id=repo_id, status="pending")


@router.get("", response_model=list[RepoSummary])
def list_repos(
    client: ClientDep, limit: int = Query(default=50, ge=1, le=200)
) -> list[dict[str, Any]]:
    rows = client.read(cypher.LIST_REPOS, limit=limit)
    return [{**row["repo"], "notice": briefing.repo_card_notice(row["repo"])} for row in rows]


@router.get("/{repo_id}", response_model=RepoDetail)
def get_repo(repo_id: str, client: ClientDep) -> dict[str, Any]:
    repo = load_repo(client, repo_id)
    detail = dict(repo)
    detail["notice"] = briefing.repo_card_notice(repo)
    if repo.get("status") == "ready":
        stats = _stats_for(client, repo_id, repo)
        detail["stats"] = stats.model_dump()
        detail["overview"] = asdict(
            briefing.describe_repo(
                str(repo.get("name") or "This repository"),
                file_count=int(repo.get("file_count") or 0),
                loc=int(repo.get("loc") or 0),
                counts=stats.counts.model_dump(),
                internal_call_sites=stats.resolution.internal_call_sites,
                internal_resolution_rate=stats.resolution.internal_resolution_rate,
                parse_failures=int(repo.get("parse_failures") or 0),
            )
        )
    return detail


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_repo(repo_id: str, client: ClientDep, pipeline: PipelineDep) -> None:
    load_repo(client, repo_id)
    pipeline.delete(repo_id)


@router.get("/{repo_id}/stats", response_model=RepoStats)
def repo_stats(repo_id: str, client: ClientDep) -> RepoStats:
    repo = load_repo(client, repo_id)
    return _stats_for(client, repo_id, repo)


def _stats_for(client: Any, repo_id: str, repo: dict[str, Any]) -> RepoStats:
    counts_rows = client.read(cypher.REPO_COUNTS, repo_id=repo_id)
    counts = counts_rows[0] if counts_rows else {}
    failures = client.read(cypher.PARSE_FAILURES, repo_id=repo_id, limit=100)
    return build_stats(repo_id, repo, counts, failures)
