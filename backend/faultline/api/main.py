"""FastAPI application factory.

``create_app`` builds the app; the module-level ``app`` is what uvicorn serves.
Keeping construction in a function lets tests build an app with a different
graph client without touching global state.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram

from faultline import __version__
from faultline.api.deps import SymbolNotFound
from faultline.api.routes import explain, health, impact, insights, repos, symbols
from faultline.config import Settings, get_settings
from faultline.graph.client import GraphClient, GraphUnavailable
from faultline.graph.schema import ensure_schema
from faultline.ingest.pipeline import IngestPipeline
from faultline.logging import configure_logging, get_logger

logger = get_logger(__name__)

API_PREFIX = "/api"

REQUESTS = Counter(
    "faultline_http_requests_total",
    "HTTP requests by method, route template and status.",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "faultline_http_request_seconds",
    "HTTP request latency by route template.",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

DESCRIPTION = """
**Faultline** maps a Python codebase into a call graph and answers the question
*"if I change this function, what breaks, and which tests should I run?"*

Call edges carry a **confidence** score. `1.0` is an exact lookup; lower values
mean the resolver had to infer or guess, and the impact endpoints let you filter
on it with `min_confidence`.
"""


def _route_label(request: Request) -> str:
    """The matched route *template* to use as a metric label.

    Never the raw path: a label per repo id or FQN would create unbounded metric
    cardinality. Some FastAPI versions report the route without its include
    prefix, so it is restored here to keep dashboards readable.
    """
    raw = getattr(request.scope.get("route"), "path", None)
    if not raw:
        return "unmatched"
    template = str(raw)
    if request.url.path.startswith(API_PREFIX) and not template.startswith(API_PREFIX):
        return API_PREFIX + template
    return template


def create_app(
    settings: Settings | None = None,
    *,
    graph: GraphClient | None = None,
    init_schema: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = graph or GraphClient(settings)
        app.state.graph = client
        app.state.pipeline = IngestPipeline(client, settings)
        if init_schema and client.verify():
            # Constraints before the first write, never after.
            ensure_schema(client)
        elif init_schema:
            logger.warning("neo4j not reachable at startup; schema not applied")
        yield
        client.close()

    app = FastAPI(
        title="Faultline",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def record_metrics(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        # The route *template* (`/repos/{repo_id}/impact`), not the raw path:
        # a label per repo id would create unbounded metric cardinality.
        route = _route_label(request)
        LATENCY.labels(route).observe(time.perf_counter() - started)
        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        return response

    @app.exception_handler(GraphUnavailable)
    async def graph_unavailable(_: Request, exc: GraphUnavailable) -> JSONResponse:
        logger.error("graph database unavailable", extra={"error": str(exc)})
        return JSONResponse(status_code=503, content={"detail": "graph database unavailable"})

    @app.exception_handler(SymbolNotFound)
    async def symbol_not_found(_: Request, exc: SymbolNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": "symbol not found", "did_you_mean": exc.did_you_mean},
        )

    for module in (health, repos, symbols, impact, insights, explain):
        app.include_router(module.router, prefix=API_PREFIX)

    return app


app = create_app()
