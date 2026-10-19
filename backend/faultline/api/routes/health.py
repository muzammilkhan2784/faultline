"""Liveness and metrics."""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from faultline import __version__
from faultline.api.deps import ClientDep
from faultline.api.schemas import Health

router = APIRouter(tags=["ops"])


@router.get("/health", response_model=Health)
def health(client: ClientDep) -> Health:
    """Liveness plus a real Neo4j round trip.

    Returns 200 with ``status="degraded"`` rather than a 5xx when the database
    is down: the process is alive, and an orchestrator that restarts it will not
    bring Neo4j back.
    """
    reachable = client.verify()
    return Health(status="ok" if reachable else "degraded", neo4j=reachable, version=__version__)


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
