"""The whole stack through HTTP: POST a repo, let the background pipeline run,
then query every endpoint. No fakes anywhere."""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from faultline.api.main import create_app
from faultline.config import Settings
from faultline.graph.client import GraphClient

warnings.filterwarnings("ignore", category=DeprecationWarning)

pytestmark = pytest.mark.integration

CLAMP = "shop.utils.clamp"


@pytest.fixture(scope="module")
def api(graph_client: GraphClient, fixture_root: Path) -> Iterator[tuple[TestClient, str]]:
    settings = graph_client.settings
    app = create_app(Settings(**settings.model_dump()), graph=GraphClient(settings))
    with TestClient(app) as client:
        response = client.post("/api/repos", json={"source": str(fixture_root), "name": "shop-e2e"})
        assert response.status_code == 202, response.text
        repo_id = response.json()["repo_id"]
        # TestClient runs BackgroundTasks before returning, so the run is finished.
        yield client, repo_id
        client.delete(f"/api/repos/{repo_id}")


def test_the_repo_reaches_ready(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    body = client.get(f"/api/repos/{repo_id}").json()
    assert body["status"] == "ready", body.get("error")
    assert body["name"] == "shop-e2e"
    assert body["file_count"] == 16
    assert body["stats"]["counts"]["functions"] == 33


def test_stats_report_the_resolution_breakdown_and_the_failure(
    api: tuple[TestClient, str],
) -> None:
    client, repo_id = api
    stats = client.get(f"/api/repos/{repo_id}/stats").json()
    assert stats["resolution"]["call_sites_total"] == 35
    assert stats["resolution"]["resolved_exact"] == 24
    assert stats["resolution"]["dropped_builtin"] == 6
    assert [f["path"] for f in stats["parse_failures"]] == ["shop/broken.py"]
    assert stats["summary"] == "Parsed 16 modules (1 failed: see parse_failures)"
    assert stats["timings"]["total_seconds"] > 0


def test_repo_listing_contains_it(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    assert repo_id in {r["id"] for r in client.get("/api/repos").json()}


def test_impact_over_http(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    body = client.get(f"/api/repos/{repo_id}/impact", params={"fqn": CLAMP, "depth": 3}).json()
    assert body["total"] == 5
    assert body["affected_tests"] == 2
    assert body["results"][0]["fqn"] == "shop.services.billing._apply_discount"


def test_test_selection_over_http(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    body = client.get(
        f"/api/repos/{repo_id}/impact/tests", params={"fqn": "shop.utils.format_money"}
    ).json()
    assert body["total"] == 4
    assert body["command"].startswith("pytest tests/test_")


def test_path_over_http(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    body = client.get(
        f"/api/repos/{repo_id}/path",
        params={"from_fqn": "test_web.test_index_is_free", "to_fqn": "shop.utils.format_money"},
    ).json()
    assert body["found"] is True
    assert [h["name"] for h in body["hops"]] == ["test_index_is_free", "index", "format_money"]


def test_graph_over_http(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    body = client.get(
        f"/api/repos/{repo_id}/graph", params={"fqn": "shop.services.billing.charge", "depth": 1}
    ).json()
    assert body["total"] == 8 and body["truncated"] is False
    assert len(body["nodes"]) == 8


def test_insights_over_http(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    cycles = client.get(f"/api/repos/{repo_id}/cycles").json()
    assert cycles["total"] == 1
    hotspots = client.get(f"/api/repos/{repo_id}/hotspots").json()
    assert hotspots["total"] == 3
    dead = client.get(f"/api/repos/{repo_id}/dead-code").json()
    assert dead["total"] == 7
    assert "false positives" in dead["caveat"]


def test_symbol_search_over_http(api: tuple[TestClient, str]) -> None:
    """Regression: a Cypher parameter named ``query`` once collided with the client
    method's own argument. Here it runs against the real fulltext index."""
    client, repo_id = api
    hits = client.get(f"/api/repos/{repo_id}/symbols", params={"q": "clamp"}).json()
    assert CLAMP in {h["fqn"] for h in hits}
    dotted = client.get(f"/api/repos/{repo_id}/symbols", params={"q": "billing.charge"}).json()
    assert "shop.services.billing.charge" in {h["fqn"] for h in dotted}
    substring = client.get(f"/api/repos/{repo_id}/symbols", params={"q": "format_mon"}).json()
    assert substring  # the fulltext index has no hit; the scan fallback does


def test_symbol_search_survives_lucene_metacharacters(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    for hostile in ["clamp(", "a:b", "x AND", "foo*bar", "(((", '"', "\\", "~~", "a/b", "-x", "!"]:
        response = client.get(f"/api/repos/{repo_id}/symbols", params={"q": hostile})
        assert response.status_code == 200, hostile


def test_symbol_detail_over_http(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    body = client.get(f"/api/repos/{repo_id}/symbols/{CLAMP}").json()
    assert body["complexity"] == 3
    assert body["fan_in"] == 1 and body["fan_out"] == 0


def test_unknown_symbol_suggests_alternatives(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    response = client.get(f"/api/repos/{repo_id}/impact", params={"fqn": "shop.utils.clam"})
    assert response.status_code == 404
    assert CLAMP in response.json()["did_you_mean"]


def test_out_of_range_depth_is_rejected(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    assert (
        client.get(f"/api/repos/{repo_id}/impact", params={"fqn": CLAMP, "depth": 11}).status_code
        == 422
    )


def test_an_injection_attempt_in_fqn_is_just_an_unknown_symbol(api: tuple[TestClient, str]) -> None:
    """Everything except the depth bound is a real query parameter."""
    client, repo_id = api
    evil = "x'}) MATCH (n) DETACH DELETE n //"
    assert client.get(f"/api/repos/{repo_id}/impact", params={"fqn": evil}).status_code == 404
    assert client.get(f"/api/repos/{repo_id}").json()["stats"]["counts"]["functions"] == 33


def test_health_reports_a_live_database(api: tuple[TestClient, str]) -> None:
    client, _ = api
    assert client.get("/api/health").json()["neo4j"] is True


def test_a_failed_ingest_is_reported_not_raised(api: tuple[TestClient, str]) -> None:
    client, _ = api
    response = client.post("/api/repos", json={"source": str(Path("/definitely/not/a/path"))})
    assert response.status_code == 400  # rejected synchronously, before anything is registered


def test_delete_removes_the_repo(api: tuple[TestClient, str], fixture_root: Path) -> None:
    client, _ = api
    created = client.post("/api/repos", json={"source": str(fixture_root), "name": "doomed"})
    repo_id = created.json()["repo_id"]
    assert client.get(f"/api/repos/{repo_id}").status_code == 200
    assert client.delete(f"/api/repos/{repo_id}").status_code == 204
    assert client.get(f"/api/repos/{repo_id}").status_code == 404


# -- the plain-English briefing, against a real database ----------------------------------


def test_briefing_agrees_with_the_impact_and_test_endpoints(api: tuple[TestClient, str]) -> None:
    """The briefing quotes the same numbers as the Impact and Tests tabs. If it counted
    differently, a first-time user would see two figures for one thing."""
    client, repo_id = api
    params = {"fqn": CLAMP}
    brief = client.get(f"/api/repos/{repo_id}/briefing", params=params).json()
    impact = client.get(f"/api/repos/{repo_id}/impact", params={**params, "depth": 3}).json()
    tests = client.get(f"/api/repos/{repo_id}/impact/tests", params=params).json()

    application = [r for r in impact["results"] if not r["is_test"]]
    affected = next(s for s in brief["sections"] if s["id"] == "affected")["text"]
    assert f"{len(application)} other function" in affected
    tests_text = next(s for s in brief["sections"] if s["id"] == "tests")["text"]
    assert f"{tests['total']} test" in tests_text
    assert brief["headline"].startswith(brief["risk_level"].capitalize() + " risk")


def test_briefing_has_paragraphs_for_a_function_with_and_without_callers(
    api: tuple[TestClient, str],
) -> None:
    client, repo_id = api
    for fqn in (CLAMP, "shop.services.billing.charge"):
        brief = client.get(f"/api/repos/{repo_id}/briefing", params={"fqn": fqn}).json()
        assert [s["id"] for s in brief["sections"]] == ["what", "affected", "tests", "risk"]
        assert all(len(s["text"]) > 40 for s in brief["sections"])
        assert all(s["text"].count("`") % 2 == 0 for s in brief["sections"])


def test_briefing_for_a_typo_is_a_404_with_suggestions(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    response = client.get(f"/api/repos/{repo_id}/briefing", params={"fqn": "shop.utils.clmap"})
    assert response.status_code == 404
    assert CLAMP in response.json()["did_you_mean"]


def test_the_repo_overview_describes_what_was_found(api: tuple[TestClient, str]) -> None:
    client, repo_id = api
    overview = client.get(f"/api/repos/{repo_id}").json()["overview"]
    assert "16 Python files" in overview["text"]
    assert "33 functions" in overview["text"]
    assert any("could not be parsed" in n["text"] for n in overview["notices"])  # shop/broken.py


# -- what a person sees when they point it at the wrong thing ---------------------------------


def _analyse(client: TestClient, folder: Path) -> dict[str, object]:
    response = client.post("/api/repos", json={"source": str(folder), "name": folder.name})
    assert response.status_code == 202, response.text
    repo_id = response.json()["repo_id"]
    body: dict[str, object] = client.get(f"/api/repos/{repo_id}").json()
    client.delete(f"/api/repos/{repo_id}")
    return body


def test_docstrings_survive_the_round_trip_into_neo4j_and_the_briefing(
    api: tuple[TestClient, str], tmp_path: Path
) -> None:
    client, _ = api
    (tmp_path / "shop.py").write_text(
        "def price(x):\n"
        '    """Work out the price of an item. Includes tax."""\n'
        "    return x * 2\n\n\n"
        "def checkout(x):\n"
        "    return price(x)\n",
        encoding="utf-8",
    )
    response = client.post("/api/repos", json={"source": str(tmp_path), "name": "docs-e2e"})
    repo_id = response.json()["repo_id"]
    try:
        symbol = client.get(f"/api/repos/{repo_id}/symbols/shop.price").json()
        assert symbol["doc"] == "Work out the price of an item."
        brief = client.get(f"/api/repos/{repo_id}/briefing", params={"fqn": "shop.price"}).json()
        assert "Work out the price of an item." in brief["sections"][0]["text"]
        assert "1 other function can be affected" in brief["sections"][1]["text"]
        bare = client.get(f"/api/repos/{repo_id}/symbols/shop.checkout").json()
        assert bare["doc"] is None
    finally:
        client.delete(f"/api/repos/{repo_id}")


def test_a_javascript_project_fails_with_an_explanation_instead_of_zeros(
    api: tuple[TestClient, str], tmp_path: Path
) -> None:
    """Regression for the '0 files, 0 nodes, 0 edges' report."""
    client, _ = api
    (tmp_path / "index.js").write_text("module.exports = {}\n", encoding="utf-8")
    body = _analyse(client, tmp_path)
    assert body["status"] == "failed"
    assert "No Python (.py) files were found" in str(body["error"])
    assert "JavaScript" in str(body["error"])


def test_python_that_calls_nothing_is_ready_but_carries_a_notice(
    api: tuple[TestClient, str], tmp_path: Path
) -> None:
    client, _ = api
    (tmp_path / "script.py").write_text("x = 1\nprint(x)\n", encoding="utf-8")
    response = client.post("/api/repos", json={"source": str(tmp_path), "name": "scripts-e2e"})
    repo_id = response.json()["repo_id"]
    try:
        body = client.get(f"/api/repos/{repo_id}").json()
        assert body["status"] == "ready"
        assert "No function calls found" in body["notice"]
        assert any("defines no functions" in n["text"] for n in body["overview"]["notices"])
        listed = next(r for r in client.get("/api/repos").json() if r["id"] == repo_id)
        assert listed["notice"] == body["notice"]
    finally:
        client.delete(f"/api/repos/{repo_id}")
