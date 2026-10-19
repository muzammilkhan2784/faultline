"""Whole-repo insights: import cycles, hotspots, dead-code candidates."""

from __future__ import annotations

from fastapi import APIRouter, Query

from faultline.analysis import cycles as cycle_analysis
from faultline.analysis import dead_code as dead_code_analysis
from faultline.analysis import hotspots as hotspot_analysis
from faultline.api.deps import ClientDep, require_ready_repo
from faultline.api.schemas import CyclesResponse, DeadCodeResponse, HotspotsResponse

router = APIRouter(prefix="/repos/{repo_id}", tags=["insights"])

CYCLE_NOTE = (
    "Imports under `if TYPE_CHECKING:` and imports inside function bodies are "
    "excluded: both are standard fixes for a circular import, not causes of one."
)
RISK_FORMULA = "risk_score = fan_in x complexity (high-confidence edges only)"


@router.get("/cycles", response_model=CyclesResponse)
def get_cycles(repo_id: str, client: ClientDep, include_deferred: bool = False) -> CyclesResponse:
    require_ready_repo(client, repo_id)
    found = cycle_analysis.import_cycles(client, repo_id, include_deferred=include_deferred)
    return CyclesResponse(total=len(found), cycles=found, note=CYCLE_NOTE)  # type: ignore[arg-type]


@router.get("/hotspots", response_model=HotspotsResponse)
def get_hotspots(
    repo_id: str,
    client: ClientDep,
    limit: int = Query(default=50, ge=1, le=500),
    min_fan_in: int = Query(default=3, ge=1, le=1000),
) -> HotspotsResponse:
    require_ready_repo(client, repo_id)
    rows = hotspot_analysis.hotspots(client, repo_id, min_fan_in=min_fan_in, limit=limit)
    return HotspotsResponse(total=len(rows), formula=RISK_FORMULA, hotspots=rows)  # type: ignore[arg-type]


@router.get("/dead-code", response_model=DeadCodeResponse)
def get_dead_code(
    repo_id: str, client: ClientDep, limit: int = Query(default=100, ge=1, le=1000)
) -> DeadCodeResponse:
    require_ready_repo(client, repo_id)
    rows = dead_code_analysis.dead_code(client, repo_id, limit=limit)
    return DeadCodeResponse(
        total=len(rows),
        caveat=dead_code_analysis.CAVEAT,
        candidates=rows,  # type: ignore[arg-type]
    )
