"""The plain-English briefing for one function."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from faultline.analysis import briefing, test_selection
from faultline.analysis import impact as impact_analysis
from faultline.api.deps import ClientDep, fetch_symbol, require_ready_repo, require_symbol
from faultline.api.schemas import BriefingResponse
from faultline.graph import cypher
from faultline.graph.cypher import MAX_DEPTH, MIN_DEPTH

router = APIRouter(prefix="/repos/{repo_id}", tags=["explain"])

#: The same reach the Impact tab reports ("can reach this within 3 calls"). The
#: briefing quotes those numbers, so it has to count the same way or a first-time
#: user sees two figures for one thing and assumes one of them is a bug.
IMPACT_DEPTH = 3
IMPACT_LIMIT = 500
TEST_LIMIT = 200


@router.get("/briefing", response_model=BriefingResponse)
def get_briefing(
    repo_id: str,
    client: ClientDep,
    fqn: Annotated[str, Query(min_length=1, max_length=500, description="Fully-qualified name.")],
    depth: Annotated[int, Query(ge=MIN_DEPTH, le=MAX_DEPTH)] = IMPACT_DEPTH,
    min_confidence: Annotated[float, Query(ge=0.0, le=1.0)] = 0.5,
) -> BriefingResponse:
    """What this function is, what a change to it reaches, which tests cover it, and
    where the risk sits, written as four short paragraphs for someone new to the code.

    Every sentence is computed from the graph; none is generated free-form.
    """
    require_ready_repo(client, repo_id)
    require_symbol(client, repo_id, fqn)
    symbol = fetch_symbol(client, repo_id, fqn)
    if symbol is None:  # deleted between the two queries
        raise HTTPException(status_code=404, detail="symbol not found")

    reach = impact_analysis.impact(
        client, repo_id, fqn, depth=depth, min_confidence=min_confidence, limit=IMPACT_LIMIT
    )
    tests = test_selection.select_tests(
        client, repo_id, fqn, min_confidence=min_confidence, limit=TEST_LIMIT
    )
    counts = client.read(cypher.REPO_COUNTS, repo_id=repo_id)
    repo_test_count = int(counts[0]["test_functions"]) if counts else None

    result = briefing.build_briefing(
        symbol,
        reach,
        tests,
        depth=depth,
        min_confidence=min_confidence,
        repo_test_count=repo_test_count,
    )
    return BriefingResponse(depth=depth, min_confidence=min_confidence, **asdict(result))
