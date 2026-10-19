"""The analysis endpoints: impact, test selection, dependencies, path, graph."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from faultline.analysis import impact as impact_analysis
from faultline.analysis import test_selection
from faultline.api.deps import ClientDep, require_ready_repo, require_symbol
from faultline.api.schemas import (
    DependenciesResponse,
    GraphResponse,
    ImpactResponse,
    PathResponse,
    TestSelectionResponse,
)
from faultline.graph.cypher import MAX_DEPTH, MIN_DEPTH

router = APIRouter(prefix="/repos/{repo_id}", tags=["analysis"])

# Depth is validated here as an int in 1..10 and again in the Cypher layer
# before it is interpolated into a query: Neo4j cannot parameterise a
# variable-length bound, so this is the one place a number becomes query text.
Depth = Annotated[int, Query(ge=MIN_DEPTH, le=MAX_DEPTH, description="Maximum call hops.")]
MinConfidence = Annotated[
    float, Query(ge=0.0, le=1.0, description="Ignore edges below this confidence.")
]
Fqn = Annotated[str, Query(min_length=1, max_length=500, description="Fully-qualified name.")]


@router.get("/impact", response_model=ImpactResponse)
def get_impact(
    repo_id: str,
    client: ClientDep,
    fqn: Fqn,
    depth: Depth = 3,
    min_confidence: MinConfidence = 0.5,
    limit: int = Query(default=200, ge=1, le=1000),
) -> ImpactResponse:
    """If you change ``fqn``, what else can break?

    Every function that reaches it within ``depth`` calls, nearest first. The
    reported confidence is the product of edge confidences along the best path.
    """
    require_ready_repo(client, repo_id)
    require_symbol(client, repo_id, fqn)
    rows = impact_analysis.impact(
        client, repo_id, fqn, depth=depth, min_confidence=min_confidence, limit=limit
    )
    return ImpactResponse(
        fqn=fqn,
        depth=depth,
        min_confidence=min_confidence,
        total=len(rows),
        affected_tests=sum(1 for r in rows if r.get("is_test")),
        results=rows,  # type: ignore[arg-type]
    )


@router.get("/impact/tests", response_model=TestSelectionResponse)
def get_tests(
    repo_id: str,
    client: ClientDep,
    fqn: Fqn,
    depth: Depth = 6,
    min_confidence: MinConfidence = 0.5,
    limit: int = Query(default=200, ge=1, le=1000),
) -> TestSelectionResponse:
    """Which tests should run for a change to ``fqn``, as a paste-able command."""
    require_ready_repo(client, repo_id)
    require_symbol(client, repo_id, fqn)
    tests = test_selection.select_tests(
        client, repo_id, fqn, depth=depth, min_confidence=min_confidence, limit=limit
    )
    return TestSelectionResponse(
        fqn=fqn,
        total=len(tests),
        tests=tests,  # type: ignore[arg-type]
        command=test_selection.pytest_command(tests),
    )


@router.get("/dependencies", response_model=DependenciesResponse)
def get_dependencies(
    repo_id: str,
    client: ClientDep,
    fqn: Fqn,
    depth: Depth = 3,
    min_confidence: MinConfidence = 0.5,
    limit: int = Query(default=200, ge=1, le=1000),
) -> DependenciesResponse:
    """The forward direction: what ``fqn`` depends on."""
    require_ready_repo(client, repo_id)
    require_symbol(client, repo_id, fqn)
    rows = impact_analysis.dependencies(
        client, repo_id, fqn, depth=depth, min_confidence=min_confidence, limit=limit
    )
    return DependenciesResponse(
        fqn=fqn,
        depth=depth,
        min_confidence=min_confidence,
        total=len(rows),
        results=rows,  # type: ignore[arg-type]
    )


@router.get("/path", response_model=PathResponse)
def get_path(
    repo_id: str,
    client: ClientDep,
    from_fqn: Fqn,
    to_fqn: Fqn,
) -> PathResponse:
    """The shortest call path from one symbol to another, if there is one."""
    require_ready_repo(client, repo_id)
    require_symbol(client, repo_id, from_fqn)
    require_symbol(client, repo_id, to_fqn)
    found = impact_analysis.shortest_path(client, repo_id, from_fqn, to_fqn)
    if found is None:
        return PathResponse(found=False, from_fqn=from_fqn, to_fqn=to_fqn)
    return PathResponse(
        found=True,
        from_fqn=from_fqn,
        to_fqn=to_fqn,
        hops=found["hops"],
        edges=found["edges"],
        confidence=found["confidence"],
    )


@router.get("/graph", response_model=GraphResponse)
def get_graph(
    repo_id: str,
    client: ClientDep,
    fqn: Fqn,
    depth: Annotated[int, Query(ge=1, le=5)] = 2,
    min_confidence: MinConfidence = 0.5,
    max_nodes: Annotated[int, Query(ge=10, le=1000)] = 300,
) -> GraphResponse:
    """The neighbourhood of ``fqn`` shaped for Cytoscape.

    Capped at ``max_nodes`` server-side. ``total`` and ``truncated`` let the UI
    say "showing 300 of 1,412" instead of freezing the browser.
    """
    require_ready_repo(client, repo_id)
    require_symbol(client, repo_id, fqn)
    sub = impact_analysis.neighbourhood(
        client, repo_id, fqn, depth=depth, min_confidence=min_confidence, max_nodes=max_nodes
    )
    return GraphResponse(center=fqn, **sub)
