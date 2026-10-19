from __future__ import annotations

from typing import Any

from helpers import parse

from faultline.graph import cypher
from faultline.graph.client import GraphClient, _chunks
from faultline.graph.writer import (
    GraphWriter,
    call_rows,
    class_rows,
    function_rows,
    import_rows,
    inherit_rows,
    instantiation_rows,
    method_link_rows,
    module_rows,
)
from faultline.ingest.pipeline import Analysis

REPO = "test-repo"


class RecordingClient:
    """Stands in for GraphClient and records what would have been sent."""

    def __init__(self) -> None:
        self.batches: list[tuple[str, int]] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.autocommit: list[tuple[str, dict[str, Any]]] = []

    def write_batches(self, query: str, rows: list[dict[str, Any]], **_: Any) -> int:
        self.batches.append((query, len(rows)))
        return len(rows)

    def write(self, query: str, **params: Any) -> list[dict[str, Any]]:
        self.writes.append((query, params))
        return []

    def run_autocommit(self, query: str, **params: Any) -> list[dict[str, Any]]:
        self.autocommit.append((query, params))
        return []


# -- row builders (pure functions) ---------------------------------------------------


def test_module_rows(fixture_analysis: Analysis) -> None:
    rows = module_rows(REPO, fixture_analysis.resolved.modules)
    assert len(rows) == 16
    billing = next(r for r in rows if r["fqn"] == "shop.services.billing")
    assert billing["props"]["path"] == "shop/services/billing.py"
    assert billing["props"]["is_test"] is False
    assert billing["props"]["parse_error"] is None
    broken = next(r for r in rows if r["fqn"] == "shop.broken")
    assert broken["props"]["parse_error"].startswith("SyntaxError")


def test_every_row_carries_the_repo_id(fixture_analysis: Analysis) -> None:
    resolved = fixture_analysis.resolved
    for rows in (
        module_rows(REPO, resolved.modules),
        class_rows(REPO, resolved.modules),
        function_rows(REPO, resolved.modules),
        method_link_rows(REPO, resolved.modules),
        call_rows(REPO, resolved),
        import_rows(REPO, resolved),
        inherit_rows(REPO, resolved),
        instantiation_rows(REPO, resolved),
    ):
        assert rows
        assert {r["repo_id"] for r in rows} == {REPO}


def test_function_rows_carry_every_spec_property(fixture_analysis: Analysis) -> None:
    (row,) = [
        r
        for r in function_rows(REPO, fixture_analysis.resolved.modules)
        if r["fqn"] == "shop.utils.clamp"
    ]
    assert set(row["props"]) >= {
        "repo_id",
        "fqn",
        "name",
        "module_fqn",
        "class_fqn",
        "line_start",
        "line_end",
        "is_method",
        "is_async",
        "is_private",
        "is_dunder",
        "is_test",
        "is_entrypoint",
        "param_count",
        "complexity",
        "decorators",
        "doc",
    }
    assert row["props"]["complexity"] == 3


def test_function_rows_carry_the_docstring_summary_or_null() -> None:
    facts = parse(
        'def documented():\n    """Do the thing. Then more detail."""\n\n\ndef bare():\n    pass\n'
    )
    rows = {r["fqn"]: r["props"] for r in function_rows(REPO, [facts])}
    assert rows["pkg.mod.documented"]["doc"] == "Do the thing."
    assert rows["pkg.mod.bare"]["doc"] is None


def test_method_links_only_for_methods(fixture_analysis: Analysis) -> None:
    rows = method_link_rows(REPO, fixture_analysis.resolved.modules)
    fqns = {r["fqn"] for r in rows}
    assert "shop.models.User.save" in fqns
    assert "shop.utils.clamp" not in fqns
    assert "shop.web.App.route.<locals>.register" not in fqns  # nested, not a method


def test_call_rows_shape(fixture_analysis: Analysis) -> None:
    rows = call_rows(REPO, fixture_analysis.resolved)
    assert len(rows) == 30
    assert set(rows[0]) == {
        "repo_id",
        "caller",
        "callee",
        "line",
        "confidence",
        "resolution",
        "via",
    }


# -- ordering ----------------------------------------------------------------------


def test_nodes_are_written_before_the_relationships_that_reference_them(
    fixture_analysis: Analysis,
) -> None:
    client = RecordingClient()
    GraphWriter(client).write_repo(REPO, fixture_analysis.resolved)  # type: ignore[arg-type]
    order = [q for q, _ in client.batches]
    assert order == [
        cypher.WRITE_MODULES,
        cypher.WRITE_CLASSES,
        cypher.WRITE_FUNCTIONS,
        cypher.WRITE_METHOD_LINKS,
        cypher.WRITE_IMPORTS,
        cypher.WRITE_INHERITS,
        cypher.WRITE_INSTANTIATES,
        cypher.WRITE_CALLS,
    ]


def test_write_result_counts(fixture_analysis: Analysis) -> None:
    result = GraphWriter(RecordingClient()).write_repo(REPO, fixture_analysis.resolved)  # type: ignore[arg-type]
    assert result.counts["modules"] == 16
    assert result.counts["calls"] == 30
    assert (
        result.nodes
        == result.counts["modules"] + result.counts["classes"] + result.counts["functions"]
    )
    assert result.edges == sum(
        result.counts[k] for k in ("imports", "inherits", "instantiates", "calls")
    )
    assert result.seconds >= 0


def test_repo_lifecycle_statements() -> None:
    client = RecordingClient()
    writer = GraphWriter(client)  # type: ignore[arg-type]
    writer.upsert_repo("r1", {"name": "x"})
    writer.set_status("r1", "ready", loc=5)
    writer.delete_repo("r1", batch_size=500)

    assert client.writes[0][0] == cypher.WRITE_REPO
    assert client.writes[0][1]["props"] == {"id": "r1", "name": "x"}
    assert client.writes[1][1] == {"id": "r1", "status": "ready", "props": {"loc": 5}}
    # Batched delete runs outside a transaction; the Repo root is removed last.
    assert "IN TRANSACTIONS OF 500 ROWS" in client.autocommit[0][0]
    assert client.writes[2][0] == cypher.DELETE_REPO_ROOT


# -- chunking ------------------------------------------------------------------------


def test_chunks_split_evenly_and_keep_the_remainder() -> None:
    rows = [{"i": i} for i in range(2_500)]
    sizes = [len(c) for c in _chunks(rows, 1_000)]
    assert sizes == [1_000, 1_000, 500]


def test_chunks_of_nothing() -> None:
    assert list(_chunks([], 1_000)) == []


def test_write_batches_sends_one_transaction_per_chunk() -> None:
    """Batching is the whole write-throughput story: 2,500 rows must be 3 round
    trips, not 2,500."""
    sent: list[int] = []

    class FakeSession:
        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute_write(self, _fn: Any, _query: str, params: dict[str, Any]) -> list[Any]:
            sent.append(len(params["rows"]))
            return []

    class FakeDriver:
        def session(self, **_: Any) -> FakeSession:
            return FakeSession()

    client = GraphClient()
    client._driver = FakeDriver()  # type: ignore[assignment]
    written = client.write_batches(
        "UNWIND $rows AS row RETURN row", [{"i": i} for i in range(2_500)], batch_size=1_000
    )
    assert written == 2_500
    assert sent == [1_000, 1_000, 500]


def test_default_batch_size_is_the_measured_one() -> None:
    """5,000 was ~1.5x faster than 1,000 on Django (see benchmarks/results)."""
    from faultline.config import Settings

    assert Settings().write_batch_size == 5_000


def test_write_batches_with_no_rows_makes_no_round_trip() -> None:
    client = GraphClient()  # never touches the driver
    assert client.write_batches("UNWIND $rows AS row RETURN row", []) == 0
