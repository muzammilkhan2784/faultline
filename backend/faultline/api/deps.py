"""Dependency injection: one shared graph client, plus the lookups every route needs.

The client lives on ``app.state`` and is created in the app lifespan, so tests
can swap in a client pointed at a test database (or a fake) with
``app.dependency_overrides`` and nothing else has to change.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from neo4j.exceptions import ClientError

from faultline.config import Settings, get_settings
from faultline.graph import cypher, lucene
from faultline.graph.client import GraphClient
from faultline.ingest.pipeline import IngestPipeline


class SymbolNotFound(Exception):
    """An FQN that does not exist. Rendered as a flat 404 body by the app."""

    def __init__(self, fqn: str, did_you_mean: list[str]) -> None:
        super().__init__(fqn)
        self.fqn = fqn
        self.did_you_mean = did_you_mean


def get_client(request: Request) -> GraphClient:
    client: GraphClient = request.app.state.graph
    return client


def get_pipeline(request: Request) -> IngestPipeline:
    pipeline: IngestPipeline = request.app.state.pipeline
    return pipeline


ClientDep = Annotated[GraphClient, Depends(get_client)]
PipelineDep = Annotated[IngestPipeline, Depends(get_pipeline)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def load_repo(client: GraphClient, repo_id: str) -> dict[str, Any]:
    """Fetch a repo node or raise the 404 every route shares."""
    rows = client.read(cypher.GET_REPO, repo_id=repo_id)
    if not rows:
        raise HTTPException(status_code=404, detail="repo not found")
    repo: dict[str, Any] = rows[0]["repo"]
    return repo


def require_ready_repo(client: GraphClient, repo_id: str) -> dict[str, Any]:
    """404 for an unknown repo, 409 for one that is not finished analysing.

    Querying a half-written graph returns confidently wrong answers, which is
    worse than returning none.
    """
    repo = load_repo(client, repo_id)
    status = repo.get("status")
    if status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"repo is not ready (status: {status})",
        )
    return repo


def fetch_symbol(client: GraphClient, repo_id: str, fqn: str) -> dict[str, Any] | None:
    """One function's properties plus its call counts, or ``None`` if it does not exist."""
    rows = client.read(cypher.GET_SYMBOL, repo_id=repo_id, fqn=fqn)
    if not rows:
        return None
    row = rows[0]
    node = dict(row["node"])
    node["module"] = node.get("module_fqn", "")
    node["fan_in"] = row["fan_in"]
    node["fan_out"] = row["fan_out"]
    return node


def require_symbol(client: GraphClient, repo_id: str, fqn: str) -> None:
    """404 with suggestions when an FQN does not exist.

    The suggestions cost one extra query and turn a dead end into a next step:
    most unknown-symbol requests are a typo or a missing module prefix.
    """
    rows = client.read(cypher.SYMBOL_EXISTS, repo_id=repo_id, fqn=fqn)
    if rows:
        return
    name = fqn.rsplit(".", 1)[-1]
    suggestions: list[str] = []
    query = lucene.fuzzy(name)
    if query:
        try:
            rows = client.read(cypher.DID_YOU_MEAN_FUZZY, repo_id=repo_id, search=query)
            suggestions = [row["fqn"] for row in rows]
        except ClientError:
            pass  # no fulltext index on this server: fall through to the substring scan
    if not suggestions:
        rows = client.read(cypher.DID_YOU_MEAN, repo_id=repo_id, needle=name.lower())
        suggestions = [row["fqn"] for row in rows]
    raise SymbolNotFound(fqn, suggestions)
