"""Request and response models.

Every response is a Pydantic model, so the OpenAPI document at ``/docs`` is
generated rather than hand-maintained. Constraints on inputs live here too:
``depth`` is validated to 1..10 at the edge, and the Cypher layer checks it a
second time before interpolating it into a query.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RepoStatus = Literal["pending", "parsing", "writing", "ready", "failed"]
SymbolKind = Literal["Function", "Class", "Module"]


class ORMBase(BaseModel):
    model_config = {"extra": "ignore"}


# -- repos --------------------------------------------------------------------


class RepoCreate(BaseModel):
    source: str = Field(
        min_length=1,
        max_length=2048,
        description="An https:// git URL, or a local directory path.",
        examples=["https://github.com/pallets/flask"],
    )
    ref: str | None = Field(default=None, max_length=200, description="Branch or tag.")
    name: str | None = Field(default=None, max_length=200)


class RepoCreated(BaseModel):
    repo_id: str
    status: RepoStatus


class RepoSummary(ORMBase):
    id: str
    name: str
    source: str
    status: RepoStatus
    commit_sha: str | None = None
    analyzed_at: str | None = None
    file_count: int | None = None
    loc: int | None = None
    node_count: int | None = None
    edge_count: int | None = None
    total_seconds: float | None = None
    error: str | None = None
    notice: str | None = Field(
        default=None, description="A one-line warning when the analysis found little to show."
    )


class ResolutionBreakdown(BaseModel):
    call_sites_total: int = 0
    resolved_exact: int = 0
    resolved_inferred: int = 0
    resolved_ambiguous: int = 0
    dropped_builtin: int = 0
    dropped_external: int = 0
    dropped_too_ambiguous: int = 0
    unresolved: int = 0
    internal_call_sites: int = 0
    exact_rate: float = 0.0
    resolution_rate: float = 0.0
    internal_exact_rate: float = 0.0
    internal_resolution_rate: float = 0.0


class Timings(BaseModel):
    parse_seconds: float | None = None
    resolve_seconds: float | None = None
    write_seconds: float | None = None
    total_seconds: float | None = None


class Counts(BaseModel):
    modules: int = 0
    classes: int = 0
    functions: int = 0
    test_functions: int = 0
    call_edges: int = 0
    import_edges: int = 0


class ParseFailure(BaseModel):
    fqn: str
    path: str
    error: str


class RepoStats(BaseModel):
    repo_id: str
    counts: Counts
    timings: Timings
    resolution: ResolutionBreakdown
    parse_failures: list[ParseFailure]
    summary: str


class RepoNotice(BaseModel):
    level: Literal["info", "warn"]
    text: str


class RepoOverview(BaseModel):
    text: str = Field(description="A plain-English paragraph on what was found.")
    notices: list[RepoNotice]


class RepoDetail(RepoSummary):
    stats: RepoStats | None = None
    overview: RepoOverview | None = None


# -- symbols ------------------------------------------------------------------


class SymbolHit(BaseModel):
    fqn: str
    name: str
    kind: SymbolKind
    module: str
    line: int
    is_test: bool = False
    score: float = 0.0


class SymbolDetail(BaseModel):
    fqn: str
    name: str
    module: str
    class_fqn: str | None = None
    line_start: int
    line_end: int
    is_method: bool
    is_async: bool
    is_test: bool
    is_entrypoint: bool
    is_private: bool
    param_count: int
    complexity: int
    decorators: list[str]
    doc: str | None = None
    fan_in: int
    fan_out: int


# -- analysis -----------------------------------------------------------------


class Reach(BaseModel):
    fqn: str
    name: str
    module: str
    class_fqn: str | None = None
    is_test: bool = False
    is_entrypoint: bool = False
    line: int | None = None
    complexity: int | None = None
    distance: int
    path_count: int
    confidence: float


class ImpactResponse(BaseModel):
    fqn: str
    depth: int
    min_confidence: float
    total: int
    affected_tests: int
    results: list[Reach]


class DependenciesResponse(BaseModel):
    fqn: str
    depth: int
    min_confidence: float
    total: int
    results: list[Reach]


class SelectedTest(BaseModel):
    fqn: str
    name: str
    module: str
    path: str | None = None
    class_fqn: str | None = None
    distance: int
    confidence: float


class TestSelectionResponse(BaseModel):
    fqn: str
    total: int
    tests: list[SelectedTest]
    command: str = Field(description="A pytest invocation you can paste into a terminal.")


class PathHop(BaseModel):
    fqn: str
    name: str
    module: str
    line: int | None = None


class PathEdge(BaseModel):
    line: int | None = None
    confidence: float
    resolution: str | None = None


class PathResponse(BaseModel):
    found: bool
    from_fqn: str
    to_fqn: str
    hops: list[PathHop] = []
    edges: list[PathEdge] = []
    confidence: float = 0.0


class BriefingLink(BaseModel):
    fqn: str
    label: str
    note: str


class BriefingSection(BaseModel):
    id: Literal["what", "affected", "tests", "risk"]
    title: str
    text: str = Field(description="Prose. Names are wrapped in backticks.")
    links: list[BriefingLink] = []


class BriefingResponse(BaseModel):
    fqn: str
    depth: int
    min_confidence: float
    headline: str
    risk_level: Literal["low", "moderate", "high"]
    sections: list[BriefingSection]


class GraphNode(BaseModel):
    id: str
    name: str
    module: str | None = None
    class_fqn: str | None = None
    is_test: bool = False
    is_entrypoint: bool = False
    complexity: int | None = None
    line: int | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    confidence: float
    line: int | None = None
    resolution: str | None = None
    via: str | None = None


class GraphResponse(BaseModel):
    center: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    total: int
    truncated: bool


class CycleItem(BaseModel):
    modules: list[str]
    size: int
    is_self_import: bool


class CyclesResponse(BaseModel):
    total: int
    cycles: list[CycleItem]
    note: str


class Hotspot(BaseModel):
    fqn: str
    name: str
    module: str
    line: int | None = None
    fan_in: int
    complexity: int
    risk_score: int


class HotspotsResponse(BaseModel):
    total: int
    formula: str
    hotspots: list[Hotspot]


class DeadCodeItem(BaseModel):
    fqn: str
    name: str
    module: str
    line: int | None = None
    complexity: int
    is_private: bool = False


class DeadCodeResponse(BaseModel):
    total: int
    caveat: str
    candidates: list[DeadCodeItem]


# -- ops ----------------------------------------------------------------------


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    neo4j: bool
    version: str


class ErrorBody(BaseModel):
    detail: str
    did_you_mean: list[str] | None = None


def dump(model: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    """Validate a raw record against a schema and return only its declared fields."""
    return model.model_validate(data).model_dump()
