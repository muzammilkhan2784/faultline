"""HTTP layer tests against a fake graph client, so they need no database.

The fake answers each Cypher constant with canned rows. That verifies routing,
validation, status codes, error bodies and response shaping -- everything the API
owns. The queries themselves are verified against real Neo4j in
tests/integration.
"""

from __future__ import annotations

import difflib
import re
import warnings
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ClientError

from faultline.api.main import create_app
from faultline.config import Settings
from faultline.graph import cypher
from faultline.graph.client import GraphUnavailable
from faultline.ingest.pipeline import IngestPipeline

warnings.filterwarnings("ignore", category=DeprecationWarning)

READY_REPO: dict[str, Any] = {
    "id": "r1",
    "name": "shop",
    "source": "/tmp/shop",
    "status": "ready",
    "file_count": 16,
    "loc": 200,
    "node_count": 60,
    "edge_count": 90,
    "total_seconds": 1.5,
    "parse_seconds": 0.5,
    "resolve_seconds": 0.2,
    "write_seconds": 0.8,
    "parse_failures": 1,
    "stat_call_sites_total": 35,
    "stat_resolved_exact": 24,
    "stat_resolved_inferred": 4,
    "stat_resolved_ambiguous": 1,
    "stat_dropped_builtin": 6,
    "stat_exact_rate": 0.6857,
}


class FakeGraph:
    """Answers queries by identity of the Cypher constant."""

    def __init__(self) -> None:
        self.repos: dict[str, dict[str, Any]] = {"r1": dict(READY_REPO)}
        self.symbols = {"shop.utils.clamp", "shop.services.billing.charge"}
        self.responses: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.down = False
        self.reachable = True
        self.fuzzy_unavailable = False

    def verify(self) -> bool:
        return self.reachable

    def close(self) -> None:
        return None

    def read(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        if self.down:
            raise GraphUnavailable("down")
        if query is cypher.GET_REPO:
            repo = self.repos.get(params["repo_id"])
            return [{"repo": repo}] if repo else []
        if query is cypher.SYMBOL_EXISTS:
            return [{"fqn": params["fqn"]}] if params["fqn"] in self.symbols else []
        if query is cypher.DID_YOU_MEAN_FUZZY:
            if self.fuzzy_unavailable:
                raise ClientError("no such index")
            term = re.match(r"\((.*)~ OR", params["search"]).group(1)  # type: ignore[union-attr]
            names = {s: s.rsplit(".", 1)[-1] for s in self.symbols}
            close = difflib.get_close_matches(term, list(names.values()), n=5, cutoff=0.6)
            return [{"fqn": s} for s in sorted(self.symbols) if names[s] in close]
        if query is cypher.DID_YOU_MEAN:
            return [{"fqn": s} for s in sorted(self.symbols) if params["needle"] in s]
        if query is cypher.REPO_COUNTS:
            return [
                {
                    "modules": 16,
                    "classes": 6,
                    "functions": 40,
                    "test_functions": 5,
                    "call_edges": 30,
                    "import_edges": 18,
                }
            ]
        if query is cypher.PARSE_FAILURES:
            return [{"fqn": "shop.broken", "path": "shop/broken.py", "error": "SyntaxError: x"}]
        if "IMPORTS" in query and "RETURN DISTINCT" in query:
            return self.responses.get("imports", [])
        for name in (
            "impact",
            "deps",
            "tests",
            "graph",
            "path",
            "hotspots",
            "dead",
            "symbols",
            "scan",
            "list",
            "symbol",
        ):
            if name in self.responses and self._matches(query, name):
                return self.responses[name]
        return []

    @staticmethod
    def _matches(query: str, name: str) -> bool:
        markers = {
            "impact": "caller.fqn AS fqn",
            "deps": "callee.fqn AS fqn",
            "tests": "t.is_test = true",
            "graph": "$max_nodes",
            "path": "shortestPath",
            "hotspots": "risk_score",
            "dead": "is_private",
            "symbols": "fulltext",
            "scan": "CONTAINS $needle",
            "list": "RETURN r AS repo\nORDER BY",
            "symbol": "fan_in",
        }
        return markers[name] in query

    def write(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        return []

    def run_autocommit(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((query, params))
        return []


class FakePipeline:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str | None, str | None]] = []
        self.ran: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def register(self, source: str, name: str | None = None, ref: str | None = None) -> str:
        self.registered.append((source, name, ref))
        return "new-repo"

    def run(self, repo_id: str, source: str, **_: Any) -> None:
        self.ran.append((repo_id, source))

    def delete(self, repo_id: str) -> None:
        self.deleted.append(repo_id)


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def pipeline() -> FakePipeline:
    return FakePipeline()


@pytest.fixture
def client(graph: FakeGraph, pipeline: FakePipeline) -> Iterator[TestClient]:
    app = create_app(Settings(), graph=graph, init_schema=False)  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        app.state.pipeline = pipeline  # type: ignore[assignment]
        yield test_client


# -- health and metrics -----------------------------------------------------------------


def test_health_ok(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "neo4j": True, "version": "0.1.0"}


def test_health_degrades_instead_of_failing_when_neo4j_is_down(
    client: TestClient, graph: FakeGraph
) -> None:
    graph.reachable = False
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["neo4j"] is False


def test_metrics_are_exposed_and_use_route_templates(client: TestClient) -> None:
    client.get("/api/repos/r1/stats")
    text = client.get("/api/metrics").text
    assert "faultline_http_requests_total" in text
    assert 'route="/api/repos/{repo_id}/stats"' in text  # template, not the raw path
    assert 'route="/api/repos/r1/stats"' not in text  # would be unbounded cardinality


def test_openapi_documents_every_endpoint(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/repos",
        "/api/repos/{repo_id}",
        "/api/repos/{repo_id}/stats",
        "/api/repos/{repo_id}/symbols",
        "/api/repos/{repo_id}/symbols/{fqn}",
        "/api/repos/{repo_id}/impact",
        "/api/repos/{repo_id}/impact/tests",
        "/api/repos/{repo_id}/dependencies",
        "/api/repos/{repo_id}/path",
        "/api/repos/{repo_id}/graph",
        "/api/repos/{repo_id}/cycles",
        "/api/repos/{repo_id}/hotspots",
        "/api/repos/{repo_id}/dead-code",
        "/api/health",
    ):
        assert path in paths


# -- repos --------------------------------------------------------------------------------


def test_create_repo_accepts_a_valid_url_and_starts_a_background_run(
    client: TestClient, pipeline: FakePipeline
) -> None:
    response = client.post("/api/repos", json={"source": "https://github.com/pallets/flask"})
    assert response.status_code == 202
    assert response.json() == {"repo_id": "new-repo", "status": "pending"}
    assert pipeline.registered == [("https://github.com/pallets/flask", None, None)]
    assert pipeline.ran == [("new-repo", "https://github.com/pallets/flask")]


@pytest.mark.parametrize(
    "source",
    ["file:///etc/passwd", "git@github.com:a/b.git", "http://github.com/a/b", "ssh://x/y"],
)
def test_create_repo_rejects_unsafe_sources_with_400(
    client: TestClient, pipeline: FakePipeline, source: str
) -> None:
    response = client.post("/api/repos", json={"source": source})
    assert response.status_code == 400
    assert pipeline.registered == []  # nothing was even registered


def test_create_repo_rejects_a_missing_local_path(client: TestClient) -> None:
    response = client.post("/api/repos", json={"source": "/definitely/not/a/real/path"})
    assert response.status_code == 400


def test_create_repo_validates_the_body(client: TestClient) -> None:
    assert client.post("/api/repos", json={}).status_code == 422
    assert client.post("/api/repos", json={"source": ""}).status_code == 422


def test_list_repos(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["list"] = [{"repo": READY_REPO}]
    body = client.get("/api/repos").json()
    assert [r["id"] for r in body] == ["r1"]
    assert body[0]["status"] == "ready"


def test_get_repo_includes_stats_when_ready(client: TestClient) -> None:
    body = client.get("/api/repos/r1").json()
    assert body["name"] == "shop"
    assert body["stats"]["counts"]["functions"] == 40
    assert body["stats"]["resolution"]["resolved_exact"] == 24


def test_get_repo_while_parsing_has_no_stats(client: TestClient, graph: FakeGraph) -> None:
    graph.repos["r1"]["status"] = "parsing"
    body = client.get("/api/repos/r1").json()
    assert body["status"] == "parsing"
    assert body["stats"] is None


def test_unknown_repo_is_404(client: TestClient) -> None:
    response = client.get("/api/repos/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "repo not found"}


def test_stats_report_failures_and_the_parsed_summary(client: TestClient) -> None:
    body = client.get("/api/repos/r1/stats").json()
    assert body["summary"] == "Parsed 16 modules (1 failed: see parse_failures)"
    assert body["parse_failures"][0]["path"] == "shop/broken.py"
    assert body["timings"]["write_seconds"] == 0.8
    assert body["resolution"]["dropped_builtin"] == 6


def test_delete_repo(client: TestClient, pipeline: FakePipeline) -> None:
    assert client.delete("/api/repos/r1").status_code == 204
    assert pipeline.deleted == ["r1"]


def test_delete_unknown_repo_is_404(client: TestClient, pipeline: FakePipeline) -> None:
    assert client.delete("/api/repos/nope").status_code == 404
    assert pipeline.deleted == []


# -- symbols --------------------------------------------------------------------------------


def test_symbol_search_uses_the_fulltext_index(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["symbols"] = [
        {
            "fqn": "shop.utils.clamp",
            "name": "clamp",
            "kind": "Function",
            "module": "shop.utils",
            "line": 8,
            "is_test": False,
            "score": 2.5,
        }
    ]
    body = client.get("/api/repos/r1/symbols", params={"q": "clamp"}).json()
    assert [s["fqn"] for s in body] == ["shop.utils.clamp"]
    _, params = next(c for c in graph.calls if "search" in c[1])
    assert params["search"] == "clamp*"


def test_symbol_search_falls_back_to_a_scan(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["scan"] = [
        {
            "fqn": "shop.utils.clamp",
            "name": "clamp",
            "kind": "Function",
            "module": "shop.utils",
            "line": 8,
            "is_test": False,
            "score": 1.0,
        }
    ]
    body = client.get("/api/repos/r1/symbols", params={"q": "CLAMP"}).json()
    assert body[0]["name"] == "clamp"


def test_symbol_search_requires_a_query(client: TestClient) -> None:
    assert client.get("/api/repos/r1/symbols").status_code == 422


def test_symbol_search_kind_filter_is_validated(client: TestClient) -> None:
    assert (
        client.get("/api/repos/r1/symbols", params={"q": "x", "kind": "Bogus"}).status_code == 422
    )


def test_symbol_detail(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["symbol"] = [
        {
            "node": {
                "fqn": "shop.utils.clamp",
                "name": "clamp",
                "module_fqn": "shop.utils",
                "class_fqn": None,
                "line_start": 8,
                "line_end": 14,
                "is_method": False,
                "is_async": False,
                "is_test": False,
                "is_entrypoint": False,
                "is_private": False,
                "param_count": 3,
                "complexity": 3,
                "decorators": [],
            },
            "fan_in": 2,
            "fan_out": 0,
        }
    ]
    body = client.get("/api/repos/r1/symbols/shop.utils.clamp").json()
    assert body["module"] == "shop.utils"
    assert (body["fan_in"], body["fan_out"], body["complexity"]) == (2, 0, 3)


def test_unknown_symbol_detail_is_404(client: TestClient) -> None:
    assert client.get("/api/repos/r1/symbols/no.such.thing").status_code == 404


# -- analysis -------------------------------------------------------------------------------

REACH = {
    "fqn": "shop.services.billing.charge",
    "name": "charge",
    "module": "shop.services.billing",
    "class_fqn": None,
    "is_test": False,
    "is_entrypoint": False,
    "line": 12,
    "complexity": 1,
    "distance": 1,
    "path_count": 1,
    "confidence": 1.0,
}


def test_impact(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["impact"] = [
        REACH,
        {**REACH, "fqn": "test_x.test_y", "is_test": True, "distance": 2},
    ]
    response = client.get(
        "/api/repos/r1/impact",
        params={"fqn": "shop.utils.clamp", "depth": 2, "min_confidence": 0.6},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["depth"], body["min_confidence"], body["total"], body["affected_tests"]) == (
        2,
        0.6,
        2,
        1,
    )
    _, params = next(c for c in graph.calls if "min_confidence" in c[1])
    assert params["fqn"] == "shop.utils.clamp"
    assert params["min_confidence"] == 0.6


def test_impact_defaults(client: TestClient, graph: FakeGraph) -> None:
    body = client.get("/api/repos/r1/impact", params={"fqn": "shop.utils.clamp"}).json()
    assert (body["depth"], body["min_confidence"]) == (3, 0.5)


@pytest.mark.parametrize("depth", [0, 11, -1, "abc"])
def test_impact_rejects_out_of_range_depth_at_the_edge(client: TestClient, depth: object) -> None:
    response = client.get(
        "/api/repos/r1/impact", params={"fqn": "shop.utils.clamp", "depth": depth}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("conf", [-0.1, 1.1])
def test_impact_rejects_out_of_range_confidence(client: TestClient, conf: float) -> None:
    response = client.get(
        "/api/repos/r1/impact", params={"fqn": "shop.utils.clamp", "min_confidence": conf}
    )
    assert response.status_code == 422


def test_a_typo_gets_a_did_you_mean_suggestion(client: TestClient) -> None:
    response = client.get("/api/repos/r1/impact", params={"fqn": "shop.utils.clam"})
    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "symbol not found"
    assert body["did_you_mean"] == ["shop.utils.clamp"]


def test_an_unrelated_name_gets_an_empty_suggestion_list(client: TestClient) -> None:
    body = client.get("/api/repos/r1/impact", params={"fqn": "zzz.nothing"}).json()
    assert body == {"detail": "symbol not found", "did_you_mean": []}


def test_did_you_mean_finds_near_misses(client: TestClient) -> None:
    body = client.get("/api/repos/r1/impact", params={"fqn": "billing.charge"}).json()
    assert body["did_you_mean"] == ["shop.services.billing.charge"]


def test_impact_requires_an_fqn(client: TestClient) -> None:
    assert client.get("/api/repos/r1/impact").status_code == 422


def test_impact_on_a_repo_still_parsing_is_409(client: TestClient, graph: FakeGraph) -> None:
    graph.repos["r1"]["status"] = "writing"
    response = client.get("/api/repos/r1/impact", params={"fqn": "shop.utils.clamp"})
    assert response.status_code == 409
    assert "writing" in response.json()["detail"]


def test_impact_on_an_unknown_repo_is_404(client: TestClient) -> None:
    assert client.get("/api/repos/nope/impact", params={"fqn": "x"}).status_code == 404


def test_test_selection_renders_a_pytest_command(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["tests"] = [
        {
            "fqn": "test_billing.test_a",
            "name": "test_a",
            "module": "test_billing",
            "path": "tests/test_billing.py",
            "class_fqn": None,
            "distance": 2,
            "confidence": 1.0,
        },
        {
            "fqn": "test_web.test_b",
            "name": "test_b",
            "module": "test_web",
            "path": "tests/test_web.py",
            "class_fqn": None,
            "distance": 3,
            "confidence": 0.9,
        },
    ]
    body = client.get("/api/repos/r1/impact/tests", params={"fqn": "shop.utils.clamp"}).json()
    assert body["total"] == 2
    assert body["command"] == "pytest tests/test_billing.py::test_a tests/test_web.py::test_b"


def test_test_selection_with_no_tests_has_an_empty_command(client: TestClient) -> None:
    body = client.get("/api/repos/r1/impact/tests", params={"fqn": "shop.utils.clamp"}).json()
    assert (body["total"], body["command"]) == (0, "")


def test_dependencies(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["deps"] = [{**REACH, "fqn": "shop.utils.clamp"}]
    body = client.get(
        "/api/repos/r1/dependencies", params={"fqn": "shop.services.billing.charge"}
    ).json()
    assert body["total"] == 1


def test_path_found(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["path"] = [
        {
            "hops": [
                {
                    "fqn": "shop.services.billing.charge",
                    "name": "charge",
                    "module": "shop.services.billing",
                    "line": 12,
                },
                {"fqn": "shop.utils.clamp", "name": "clamp", "module": "shop.utils", "line": 8},
            ],
            "edges": [{"line": 9, "confidence": 1.0, "resolution": "exact"}],
            "confidence": 1.0,
        }
    ]
    body = client.get(
        "/api/repos/r1/path",
        params={"from_fqn": "shop.services.billing.charge", "to_fqn": "shop.utils.clamp"},
    ).json()
    assert body["found"] is True
    assert [h["name"] for h in body["hops"]] == ["charge", "clamp"]


def test_path_not_found_is_a_200_with_found_false(client: TestClient) -> None:
    body = client.get(
        "/api/repos/r1/path",
        params={"from_fqn": "shop.utils.clamp", "to_fqn": "shop.services.billing.charge"},
    ).json()
    assert body["found"] is False
    assert body["hops"] == []


def test_graph_response_is_shaped_for_cytoscape(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["graph"] = [
        {
            "total": 350,
            "nodes": [
                {
                    "id": "a",
                    "name": "a",
                    "module": "m",
                    "is_test": False,
                    "is_entrypoint": False,
                    "complexity": 1,
                    "line": 1,
                },
                {
                    "id": "b",
                    "name": "b",
                    "module": "m",
                    "is_test": True,
                    "is_entrypoint": False,
                    "complexity": 2,
                    "line": 5,
                },
            ],
            "edges": [
                {
                    "source": "a",
                    "target": "b",
                    "confidence": 1.0,
                    "line": 3,
                    "resolution": "exact",
                    "via": "name",
                },
                None,
            ],
        }
    ]
    body = client.get("/api/repos/r1/graph", params={"fqn": "shop.utils.clamp"}).json()
    assert body["center"] == "shop.utils.clamp"
    assert body["total"] == 350 and body["truncated"] is True
    assert len(body["nodes"]) == 2
    assert len(body["edges"]) == 1  # the null from OPTIONAL MATCH is dropped


def test_graph_of_a_leaf_is_empty_rather_than_an_error(client: TestClient) -> None:
    body = client.get("/api/repos/r1/graph", params={"fqn": "shop.utils.clamp"}).json()
    assert body["nodes"] == [] and body["truncated"] is False


def test_graph_depth_is_capped_at_five(client: TestClient) -> None:
    assert (
        client.get(
            "/api/repos/r1/graph", params={"fqn": "shop.utils.clamp", "depth": 6}
        ).status_code
        == 422
    )


# -- insights --------------------------------------------------------------------------------


def test_cycles(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["imports"] = [
        {"source": "a", "target": "b"},
        {"source": "b", "target": "a"},
        {"source": "c", "target": "a"},
    ]
    body = client.get("/api/repos/r1/cycles").json()
    assert body["total"] == 1
    assert body["cycles"] == [{"modules": ["a", "b"], "size": 2, "is_self_import": False}]
    assert "TYPE_CHECKING" in body["note"]


def test_cycles_pass_the_deferred_flag_through(client: TestClient, graph: FakeGraph) -> None:
    client.get("/api/repos/r1/cycles", params={"include_deferred": "true"})
    _, params = next(c for c in graph.calls if "include_deferred" in c[1])
    assert params["include_deferred"] is True


def test_hotspots(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["hotspots"] = [
        {
            "fqn": "shop.utils.format_money",
            "name": "format_money",
            "module": "shop.utils",
            "line": 4,
            "fan_in": 5,
            "complexity": 2,
            "risk_score": 10,
        }
    ]
    body = client.get("/api/repos/r1/hotspots", params={"limit": 5, "min_fan_in": 2}).json()
    assert body["hotspots"][0]["risk_score"] == 10
    assert "fan_in x complexity" in body["formula"]


def test_dead_code_always_carries_the_caveat(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["dead"] = [
        {
            "fqn": "shop.utils.unused_helper",
            "name": "unused_helper",
            "module": "shop.utils",
            "line": 17,
            "complexity": 1,
            "is_private": False,
        }
    ]
    body = client.get("/api/repos/r1/dead-code").json()
    assert body["candidates"][0]["fqn"] == "shop.utils.unused_helper"
    assert "false positives" in body["caveat"]


# -- failure modes ----------------------------------------------------------------------------


def test_database_outage_is_a_503_with_a_clean_body(client: TestClient, graph: FakeGraph) -> None:
    graph.down = True
    response = client.get("/api/repos/r1/stats")
    assert response.status_code == 503
    assert response.json() == {"detail": "graph database unavailable"}


def test_cors_allows_the_dev_frontend(client: TestClient) -> None:
    response = client.options(
        "/api/repos",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_does_not_allow_arbitrary_origins(client: TestClient) -> None:
    response = client.options(
        "/api/repos",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_pipeline_type_is_the_real_one() -> None:
    """The fake must keep matching the real interface the routes call."""
    for name in ("register", "run", "delete"):
        assert hasattr(IngestPipeline, name)


# -- typo tolerance -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typo", "expected"),
    [
        ("shop.utils.clmap", "shop.utils.clamp"),  # transposed letters
        ("shop.utils.clam", "shop.utils.clamp"),  # dropped letter
        ("shop.services.billing.chrage", "shop.services.billing.charge"),
        ("shop.services.billing.charg", "shop.services.billing.charge"),
    ],
)
def test_typos_get_fuzzy_suggestions(client: TestClient, typo: str, expected: str) -> None:
    """Regression: suggestions were substring-only, so a real misspelling such as
    ``super_ln`` for ``super_len`` got nothing. Found by running the containers."""
    body = client.get("/api/repos/r1/impact", params={"fqn": typo}).json()
    assert body["detail"] == "symbol not found"
    assert expected in body["did_you_mean"]


def test_suggestions_fall_back_to_a_substring_scan_without_a_fulltext_index(
    client: TestClient, graph: FakeGraph
) -> None:
    graph.fuzzy_unavailable = True
    body = client.get("/api/repos/r1/impact", params={"fqn": "shop.utils.clam"}).json()
    assert body["did_you_mean"] == ["shop.utils.clamp"]


# -- briefing -------------------------------------------------------------------------------

TEST_ROW = {
    "fqn": "test_utils.test_clamp",
    "name": "test_clamp",
    "module": "test_utils",
    "path": "tests/test_utils.py",
    "class_fqn": None,
    "distance": 1,
    "confidence": 1.0,
}

SYMBOL_ROW = {
    "node": {
        "fqn": "shop.utils.clamp",
        "name": "clamp",
        "module_fqn": "shop.utils",
        "class_fqn": None,
        "line_start": 8,
        "line_end": 14,
        "is_method": False,
        "is_async": False,
        "is_test": False,
        "is_entrypoint": False,
        "is_private": False,
        "param_count": 3,
        "complexity": 3,
        "decorators": [],
        "doc": "Limit a value to a range.",
    },
    "fan_in": 1,
    "fan_out": 0,
}


def test_symbol_detail_includes_the_docstring_summary(client: TestClient, graph: FakeGraph) -> None:
    graph.responses["symbol"] = [SYMBOL_ROW]
    body = client.get("/api/repos/r1/symbols/shop.utils.clamp").json()
    assert body["doc"] == "Limit a value to a range."


def test_symbol_detail_tolerates_a_graph_written_before_docstrings_existed(
    client: TestClient, graph: FakeGraph
) -> None:
    old = {**SYMBOL_ROW, "node": {k: v for k, v in SYMBOL_ROW["node"].items() if k != "doc"}}
    graph.responses["symbol"] = [old]
    assert client.get("/api/repos/r1/symbols/shop.utils.clamp").json()["doc"] is None


def test_briefing_returns_four_sections_a_headline_and_a_risk_level(
    client: TestClient, graph: FakeGraph
) -> None:
    graph.responses["symbol"] = [SYMBOL_ROW]
    graph.responses["impact"] = [
        {**REACH, "fqn": "shop.web.checkout", "name": "checkout", "module": "shop.web"}
    ]
    graph.responses["tests"] = [TEST_ROW]
    body = client.get("/api/repos/r1/briefing", params={"fqn": "shop.utils.clamp"}).json()
    assert [s["id"] for s in body["sections"]] == ["what", "affected", "tests", "risk"]
    assert body["risk_level"] == "low"
    assert body["headline"] == "Low risk: 1 function depends on it."
    assert "1 test can reach" in body["sections"][2]["text"]
    assert "Limit a value to a range." in body["sections"][0]["text"]
    assert "1 other function can be affected within 3 calls" in body["sections"][1]["text"]
    assert (body["depth"], body["min_confidence"]) == (3, 0.5)


def test_briefing_counts_the_same_way_the_impact_tab_does(
    client: TestClient, graph: FakeGraph
) -> None:
    """The briefing quotes the Impact tab's numbers: same depth, same confidence filter."""
    graph.responses["symbol"] = [SYMBOL_ROW]
    client.get("/api/repos/r1/briefing", params={"fqn": "shop.utils.clamp", "min_confidence": 0.8})
    impact_calls = [(q, p) for q, p in graph.calls if "caller.fqn AS fqn" in q]
    assert impact_calls
    query, params = impact_calls[0]
    assert params["min_confidence"] == 0.8
    assert params["limit"] == 500
    assert "*1..3]" in query


def test_briefing_tells_a_repo_with_no_tests_there_is_nothing_to_select(
    client: TestClient, graph: FakeGraph
) -> None:
    graph.responses["symbol"] = [SYMBOL_ROW]
    original = graph.read

    def no_tests(query: str, /, **params: Any) -> list[dict[str, Any]]:
        if query is cypher.REPO_COUNTS:
            return [{"modules": 3, "classes": 0, "functions": 9, "test_functions": 0}]
        return original(query, **params)

    graph.read = no_tests  # type: ignore[method-assign]
    body = client.get("/api/repos/r1/briefing", params={"fqn": "shop.utils.clamp"}).json()
    assert "no tests that Faultline can recognise" in body["sections"][2]["text"]


def test_briefing_risk_section_links_are_clickable_fqns(
    client: TestClient, graph: FakeGraph
) -> None:
    graph.responses["symbol"] = [SYMBOL_ROW]
    graph.responses["impact"] = [
        {**REACH, "fqn": "shop.web.checkout", "name": "checkout", "complexity": 18}
    ]
    body = client.get("/api/repos/r1/briefing", params={"fqn": "shop.utils.clamp"}).json()
    (link,) = body["sections"][3]["links"]
    assert link == {
        "fqn": "shop.web.checkout",
        "label": "checkout",
        "note": "complexity 18, 1 call away",
    }


def test_briefing_for_an_unknown_symbol_suggests_alternatives(client: TestClient) -> None:
    response = client.get("/api/repos/r1/briefing", params={"fqn": "shop.utils.clmap"})
    assert response.status_code == 404
    assert "shop.utils.clamp" in response.json()["did_you_mean"]


def test_briefing_on_a_repo_still_parsing_is_409(client: TestClient, graph: FakeGraph) -> None:
    graph.repos["r1"]["status"] = "parsing"
    assert client.get("/api/repos/r1/briefing", params={"fqn": "x"}).status_code == 409


@pytest.mark.parametrize("bad", [{"depth": 0}, {"depth": 11}, {"min_confidence": 1.5}])
def test_briefing_validates_its_parameters(client: TestClient, bad: dict[str, Any]) -> None:
    response = client.get("/api/repos/r1/briefing", params={"fqn": "shop.utils.clamp", **bad})
    assert response.status_code == 422


# -- repository notices and overview ----------------------------------------------------------


def test_repo_detail_carries_a_plain_english_overview(client: TestClient) -> None:
    overview = client.get("/api/repos/r1").json()["overview"]
    assert "`shop` contains 16 Python files (200 lines)" in overview["text"]
    assert "40 functions" in overview["text"]
    assert all(n["level"] in {"info", "warn"} for n in overview["notices"])
    assert any("1 file could not be parsed" in n["text"] for n in overview["notices"])


def test_a_repo_still_parsing_has_no_overview(client: TestClient, graph: FakeGraph) -> None:
    graph.repos["r1"]["status"] = "parsing"
    assert client.get("/api/repos/r1").json()["overview"] is None


def test_the_repo_list_warns_about_an_empty_graph(client: TestClient, graph: FakeGraph) -> None:
    empty = {**READY_REPO, "id": "r2", "file_count": 0, "node_count": 0, "edge_count": 0}
    graph.responses["list"] = [{"repo": READY_REPO}, {"repo": empty}]
    healthy, no_python = client.get("/api/repos").json()
    assert healthy["notice"] is None
    assert "No Python files found" in no_python["notice"]


def test_a_repo_with_nodes_but_no_edges_gets_the_no_calls_notice(
    client: TestClient, graph: FakeGraph
) -> None:
    graph.repos["r1"].update(node_count=1, edge_count=0)
    assert "No function calls found" in client.get("/api/repos/r1").json()["notice"]
