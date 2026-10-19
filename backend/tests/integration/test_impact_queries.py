"""Every analysis query against real Neo4j, checked against answers derived
independently from the fixture source (see tests/unit/test_fixture_project.py)."""

from __future__ import annotations

import pytest

from faultline.analysis import cycles, dead_code, hotspots, impact, test_selection
from faultline.graph import cypher
from faultline.graph.client import GraphClient

pytestmark = pytest.mark.integration

S = "shop"
B = "shop.services.billing"
CLAMP = f"{S}.utils.clamp"
FORMAT_MONEY = f"{S}.utils.format_money"
CHARGE = f"{B}.charge"


def by_fqn(rows: list[dict]) -> dict[str, dict]:
    return {r["fqn"]: r for r in rows}


# -- impact ------------------------------------------------------------------------------


def test_impact_of_clamp_at_depth_three(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = impact.impact(graph_client, ingested_repo, CLAMP, depth=3)
    got = {r["fqn"]: r["distance"] for r in rows}
    assert got == {
        f"{B}._apply_discount": 1,
        CHARGE: 2,
        f"{S}.web.checkout": 3,
        "test_billing.test_charge_applies_discount": 3,
        "test_billing.test_charge_without_discount": 3,
    }


def test_impact_is_ordered_nearest_first(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = impact.impact(graph_client, ingested_repo, CLAMP, depth=4)
    distances = [r["distance"] for r in rows]
    assert distances == sorted(distances)
    assert rows[0]["fqn"] == f"{B}._apply_discount"


def test_depth_bounds_the_result(graph_client: GraphClient, ingested_repo: str) -> None:
    shallow = {r["fqn"] for r in impact.impact(graph_client, ingested_repo, CLAMP, depth=3)}
    deep = {r["fqn"] for r in impact.impact(graph_client, ingested_repo, CLAMP, depth=4)}
    assert "test_web.test_checkout_returns_receipt" not in shallow
    assert "test_web.test_checkout_returns_receipt" in deep


def test_path_confidence_is_the_product_along_the_path(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    """charge -> User.save is 0.9 (annotation); checkout -> charge is 1.0."""
    rows = by_fqn(impact.impact(graph_client, ingested_repo, f"{S}.models.User.save", depth=2))
    assert rows[CHARGE]["confidence"] == 0.9
    assert rows[f"{S}.web.checkout"]["confidence"] == 0.9  # 1.0 * 0.9
    assert rows[CHARGE]["distance"] == 1
    assert rows[f"{S}.web.checkout"]["distance"] == 2


def test_min_confidence_filters_out_ambiguous_edges(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    target = f"{S}.models.User.save"
    loose = {
        r["fqn"]
        for r in impact.impact(graph_client, ingested_repo, target, depth=1, min_confidence=0.5)
    }
    strict = {
        r["fqn"]
        for r in impact.impact(graph_client, ingested_repo, target, depth=1, min_confidence=0.6)
    }
    assert f"{S}.services.notify.broadcast" in loose  # an ambiguous 0.5 edge
    assert f"{S}.services.notify.broadcast" not in strict
    assert CHARGE in strict


def test_impact_reports_test_and_entrypoint_flags(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    rows = by_fqn(impact.impact(graph_client, ingested_repo, CLAMP, depth=3))
    assert rows["test_billing.test_charge_applies_discount"]["is_test"] is True
    assert rows[f"{S}.web.checkout"]["is_entrypoint"] is True
    assert rows[CHARGE]["is_test"] is False


def test_impact_limit(graph_client: GraphClient, ingested_repo: str) -> None:
    assert len(impact.impact(graph_client, ingested_repo, CLAMP, depth=3, limit=2)) == 2


def test_a_function_nothing_calls_has_no_impact(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    assert impact.impact(graph_client, ingested_repo, f"{S}.utils.unused_helper") == []


def test_recursive_cycle_does_not_report_the_target_as_its_own_caller(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    """ping <-> pong is a call cycle; the target must not appear in its own impact."""
    rows = impact.impact(graph_client, ingested_repo, f"{S}.cycle_a.ping", depth=4)
    assert [r["fqn"] for r in rows] == [f"{S}.cycle_b.pong"]


def test_forward_dependencies(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = by_fqn(impact.dependencies(graph_client, ingested_repo, f"{S}.web.checkout", depth=2))
    assert rows[CHARGE]["distance"] == 1
    assert rows[f"{S}.models.User.__init__"]["distance"] == 1
    assert rows[FORMAT_MONEY]["distance"] == 2
    # clamp is checkout -> charge -> _apply_discount -> clamp: three hops, so
    # depth 2 must not reach it and depth 3 must.
    assert CLAMP not in rows
    deeper = by_fqn(impact.dependencies(graph_client, ingested_repo, f"{S}.web.checkout", depth=3))
    assert deeper[CLAMP]["distance"] == 3


# -- test selection -----------------------------------------------------------------------


def test_tests_that_reach_format_money(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = test_selection.select_tests(graph_client, ingested_repo, FORMAT_MONEY)
    assert {r["fqn"]: r["distance"] for r in rows} == {
        "test_billing.test_charge_applies_discount": 2,
        "test_billing.test_charge_without_discount": 2,
        "test_web.test_index_is_free": 2,
        "test_web.test_checkout_returns_receipt": 3,
    }


def test_selected_tests_carry_their_file_path(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    rows = test_selection.select_tests(graph_client, ingested_repo, FORMAT_MONEY)
    assert {r["path"] for r in rows} == {"tests/test_billing.py", "tests/test_web.py"}


def test_selection_renders_a_runnable_command(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    rows = test_selection.select_tests(graph_client, ingested_repo, FORMAT_MONEY)
    command = test_selection.pytest_command(rows)
    assert command.startswith("pytest ")
    assert "tests/test_billing.py::test_charge_applies_discount" in command
    assert "tests/test_web.py::test_checkout_returns_receipt" in command


def test_selection_depth_limits_which_tests_qualify(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    rows = test_selection.select_tests(graph_client, ingested_repo, FORMAT_MONEY, depth=2)
    assert "test_web.test_checkout_returns_receipt" not in {r["fqn"] for r in rows}


def test_a_function_no_test_reaches_selects_nothing(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    assert (
        test_selection.select_tests(graph_client, ingested_repo, f"{S}.utils.unused_helper") == []
    )


# -- shortest path --------------------------------------------------------------------------


def test_shortest_path_from_a_test_to_clamp(graph_client: GraphClient, ingested_repo: str) -> None:
    found = impact.shortest_path(
        graph_client, ingested_repo, "test_web.test_checkout_returns_receipt", CLAMP
    )
    assert found is not None
    assert [h["fqn"] for h in found["hops"]] == [
        "test_web.test_checkout_returns_receipt",
        f"{S}.web.checkout",
        CHARGE,
        f"{B}._apply_discount",
        CLAMP,
    ]
    assert len(found["edges"]) == 4
    assert found["confidence"] == 1.0


def test_no_path_in_the_reverse_direction(graph_client: GraphClient, ingested_repo: str) -> None:
    assert impact.shortest_path(graph_client, ingested_repo, CLAMP, CHARGE) is None


# -- graph neighbourhood -----------------------------------------------------------------------


def test_neighbourhood_of_charge(graph_client: GraphClient, ingested_repo: str) -> None:
    sub = impact.neighbourhood(graph_client, ingested_repo, CHARGE, depth=1)
    ids = {n["id"] for n in sub["nodes"]}
    assert ids == {
        CHARGE,
        f"{B}._apply_discount",
        f"{S}.models.User.save",
        f"{S}.services.notify.send",
        FORMAT_MONEY,
        f"{S}.web.checkout",
        "test_billing.test_charge_applies_discount",
        "test_billing.test_charge_without_discount",
    }
    assert sub["total"] == 8 and sub["truncated"] is False
    # charge calls format_money on two lines: two edges, both kept.
    money = [e for e in sub["edges"] if e["target"] == FORMAT_MONEY]
    assert len(money) == 2


def test_neighbourhood_edges_only_connect_returned_nodes(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    sub = impact.neighbourhood(graph_client, ingested_repo, CHARGE, depth=1)
    ids = {n["id"] for n in sub["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in sub["edges"])


def test_neighbourhood_is_capped_and_says_so(graph_client: GraphClient, ingested_repo: str) -> None:
    sub = impact.neighbourhood(graph_client, ingested_repo, CHARGE, depth=1, max_nodes=3)
    assert len(sub["nodes"]) == 3
    assert sub["total"] == 8
    assert sub["truncated"] is True
    assert CHARGE in {n["id"] for n in sub["nodes"]}  # the centre always survives the cap


def test_neighbourhood_of_a_leaf_is_just_itself(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    sub = impact.neighbourhood(graph_client, ingested_repo, f"{S}.utils.unused_helper", depth=2)
    assert [n["id"] for n in sub["nodes"]] == [f"{S}.utils.unused_helper"]
    assert sub["edges"] == []


# -- cycles -------------------------------------------------------------------------------------


def test_runtime_import_cycle(graph_client: GraphClient, ingested_repo: str) -> None:
    found = cycles.import_cycles(graph_client, ingested_repo)
    assert [set(c["modules"]) for c in found] == [{f"{S}.cycle_a", f"{S}.cycle_b"}]


def test_type_checking_cycle_appears_only_when_asked_for(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    everything = cycles.import_cycles(graph_client, ingested_repo, include_deferred=True)
    assert {frozenset(c["modules"]) for c in everything} == {
        frozenset({f"{S}.cycle_a", f"{S}.cycle_b"}),
        frozenset({f"{S}.typed_a", f"{S}.typed_b"}),
    }


# -- hotspots -----------------------------------------------------------------------------------


def test_hotspots_ranked_by_fan_in_times_complexity(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    rows = hotspots.hotspots(graph_client, ingested_repo, min_fan_in=3)
    assert [(r["fqn"], r["fan_in"], r["risk_score"]) for r in rows] == [
        (f"{S}.models.User.__init__", 4, 4),
        (FORMAT_MONEY, 4, 4),
        (CHARGE, 3, 3),
    ]


def test_hotspots_ignore_low_confidence_edges(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    """App.route is called by two decorators, but only at 0.7 -- below the 0.8 bar."""
    rows = hotspots.hotspots(graph_client, ingested_repo, min_fan_in=1)
    assert f"{S}.web.App.route" not in {r["fqn"] for r in rows}
    loose = hotspots.hotspots(graph_client, ingested_repo, min_fan_in=1, min_confidence=0.5)
    assert f"{S}.web.App.route" in {r["fqn"] for r in loose}


def test_hotspot_min_fan_in(graph_client: GraphClient, ingested_repo: str) -> None:
    assert hotspots.hotspots(graph_client, ingested_repo, min_fan_in=5) == []


# -- dead code ----------------------------------------------------------------------------------


def test_dead_code_candidates(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = dead_code.dead_code(graph_client, ingested_repo)
    assert {r["fqn"] for r in rows} == {
        f"{S}.models.Admin.promote",
        f"{S}.utils.unused_helper",
        f"{S}.services.notify.broadcast",
        f"{S}.typed_a.A.go",
        f"{S}.typed_b.describe",
        f"{S}.star.uses_star",
        f"{S}.web.App.route.<locals>.register",
    }


def test_dead_code_excludes_entrypoints_tests_dunders_and_overrides(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    got = {r["fqn"] for r in dead_code.dead_code(graph_client, ingested_repo)}
    assert f"{S}.web.checkout" not in got  # @app.route: an entrypoint
    assert f"{S}.web.index" not in got
    assert f"{S}.models.User.__init__" not in got  # a dunder
    assert f"{S}.models.Admin.label" not in got  # overrides User.label
    assert not any(fqn.startswith("test_") for fqn in got)


# -- symbols -------------------------------------------------------------------------------------


def test_fulltext_symbol_search(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = graph_client.read(
        cypher.SEARCH_SYMBOLS_FULLTEXT,
        repo_id=ingested_repo,
        search="clamp*",
        kinds=None,
        limit=10,
    )
    assert CLAMP in {r["fqn"] for r in rows}
    assert {r["kind"] for r in rows} <= {"Function", "Class", "Module"}


def test_fulltext_search_is_scoped_to_one_repo(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    rows = graph_client.read(
        cypher.SEARCH_SYMBOLS_FULLTEXT,
        repo_id="test-a-different-repo",
        search="clamp*",
        kinds=None,
        limit=10,
    )
    assert rows == []


def test_fulltext_search_can_filter_by_kind(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = graph_client.read(
        cypher.SEARCH_SYMBOLS_FULLTEXT,
        repo_id=ingested_repo,
        search="user*",
        kinds=["Class"],
        limit=10,
    )
    assert rows and {r["kind"] for r in rows} == {"Class"}


def test_scan_fallback_finds_substrings(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = graph_client.read(
        cypher.SEARCH_SYMBOLS_SCAN, repo_id=ingested_repo, needle="format_mon", kinds=None, limit=5
    )
    assert [r["fqn"] for r in rows] == [FORMAT_MONEY]


def test_symbol_detail_includes_fan_in_and_fan_out(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    (row,) = graph_client.read(cypher.GET_SYMBOL, repo_id=ingested_repo, fqn=CHARGE)
    assert row["fan_in"] == 3  # checkout + two tests
    assert row["fan_out"] == 4  # _apply_discount, User.save, send, format_money
    assert row["node"]["complexity"] == 1


def test_did_you_mean(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = graph_client.read(cypher.DID_YOU_MEAN, repo_id=ingested_repo, needle="clam")
    assert CLAMP in {r["fqn"] for r in rows}


def test_repo_counts_query(graph_client: GraphClient, ingested_repo: str) -> None:
    (row,) = graph_client.read(cypher.REPO_COUNTS, repo_id=ingested_repo)
    assert (row["modules"], row["classes"], row["functions"]) == (16, 6, 33)
    assert row["call_edges"] == 30
    assert row["test_functions"] == 5


def test_parse_failures_query(graph_client: GraphClient, ingested_repo: str) -> None:
    rows = graph_client.read(cypher.PARSE_FAILURES, repo_id=ingested_repo, limit=10)
    assert [r["path"] for r in rows] == ["shop/broken.py"]


@pytest.mark.parametrize(
    ("typo", "expected"),
    [
        ("clmap", CLAMP),
        ("clam", CLAMP),
        ("chrage", CHARGE),
        ("format_mony", FORMAT_MONEY),
    ],
)
def test_fuzzy_did_you_mean_on_the_real_fulltext_index(
    graph_client: GraphClient, ingested_repo: str, typo: str, expected: str
) -> None:
    from faultline.graph import lucene

    rows = graph_client.read(
        cypher.DID_YOU_MEAN_FUZZY, repo_id=ingested_repo, search=lucene.fuzzy(typo)
    )
    assert expected in {r["fqn"] for r in rows}


def test_fuzzy_did_you_mean_is_scoped_to_one_repo(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    from faultline.graph import lucene

    rows = graph_client.read(
        cypher.DID_YOU_MEAN_FUZZY, repo_id="test-elsewhere", search=lucene.fuzzy("clamp")
    )
    assert rows == []
