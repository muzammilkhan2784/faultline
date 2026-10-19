"""The Cypher library: depth interpolation is the one place a number becomes
query text, so it is tested as a security boundary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from faultline.graph import cypher

DEPTH_BUILDERS = [
    cypher.impact_query,
    cypher.dependencies_query,
    cypher.test_selection_query,
    cypher.neighbourhood_query,
]


@pytest.mark.parametrize("builder", DEPTH_BUILDERS)
@pytest.mark.parametrize("depth", [1, 3, 6, 10])
def test_valid_depth_is_baked_into_the_pattern(builder, depth: int) -> None:
    assert f"*1..{depth}" in builder(depth) or f"*0..{depth}" in builder(depth)


@pytest.mark.parametrize("builder", DEPTH_BUILDERS)
@pytest.mark.parametrize("bad", [0, -1, 11, 999])
def test_out_of_range_depth_is_rejected(builder, bad: int) -> None:
    with pytest.raises(ValueError):
        builder(bad)


@pytest.mark.parametrize("builder", DEPTH_BUILDERS)
@pytest.mark.parametrize(
    "hostile",
    ["3; MATCH (n) DETACH DELETE n", "3", 3.0, None, True, [3]],
)
def test_non_int_depth_is_rejected_before_interpolation(builder, hostile: object) -> None:
    """Nothing but a real ``int`` may ever be formatted into a query string."""
    with pytest.raises(TypeError):
        builder(hostile)  # type: ignore[arg-type]


@pytest.mark.parametrize("builder", DEPTH_BUILDERS)
def test_depth_queries_use_real_parameters_for_everything_else(builder) -> None:
    query = builder(3)
    assert "$repo_id" in query
    assert "$depth" not in query  # that would be a syntax error in Neo4j
    assert "{{" not in query and "}}" not in query  # f-string escaping fully resolved
    assert query.count("{") == query.count("}")


def test_impact_query_shape() -> None:
    query = cypher.impact_query(3)
    for param in ("$fqn", "$min_confidence", "$limit"):
        assert param in query
    assert "reduce(c = 1.0" in query  # confidence is the product along the path
    assert "ORDER BY distance ASC" in query


def test_test_selection_only_matches_test_functions() -> None:
    assert "t.is_test = true" in cypher.test_selection_query(6)


def test_neighbourhood_is_capped_by_a_parameter() -> None:
    assert "$max_nodes" in cypher.neighbourhood_query(2)


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
def test_delete_batch_size_is_baked_in(size: int) -> None:
    query = cypher.delete_repo_nodes_query(size)
    assert f"IN TRANSACTIONS OF {size} ROWS" in query
    assert "$repo_id" in query


@pytest.mark.parametrize("bad", [0, 99, 100_001, -5])
def test_delete_batch_size_range(bad: int) -> None:
    with pytest.raises(ValueError):
        cypher.delete_repo_nodes_query(bad)


@pytest.mark.parametrize("bad", ["10", 1.5, True])
def test_delete_batch_size_type(bad: object) -> None:
    with pytest.raises(TypeError):
        cypher.delete_repo_nodes_query(bad)  # type: ignore[arg-type]


# -- schema -----------------------------------------------------------------------


def test_schema_statements_are_idempotent() -> None:
    assert all("IF NOT EXISTS" in s for s in cypher.SCHEMA_STATEMENTS)


def test_constraints_come_before_indexes() -> None:
    kinds = ["constraint" if "CONSTRAINT" in s else "index" for s in cypher.SCHEMA_STATEMENTS]
    assert kinds == sorted(kinds, key=lambda k: k != "constraint")


def test_every_node_type_has_a_composite_uniqueness_constraint() -> None:
    joined = " ".join(cypher.SCHEMA_STATEMENTS)
    for label in ("Module", "Class", "Function"):
        assert re.search(rf"\(\w:{label}\) REQUIRE \(\w\.repo_id, \w\.fqn\) IS UNIQUE", joined)
    assert "r.id IS UNIQUE" in joined


def test_fulltext_index_covers_the_searchable_labels() -> None:
    fulltext = next(s for s in cypher.SCHEMA_STATEMENTS if "FULLTEXT" in s)
    assert "Function|Class|Module" in fulltext
    assert "n.fqn" in fulltext and "n.name" in fulltext


# -- write statements -------------------------------------------------------------


def test_relationship_writes_match_endpoints_rather_than_merge_them() -> None:
    """A row whose callee is outside the repo must be skipped, not created."""
    for statement in (cypher.WRITE_CALLS, cypher.WRITE_IMPORTS, cypher.WRITE_INHERITS):
        assert "MATCH (a:" in statement and "MATCH (b:" in statement
        assert "MERGE (b" not in statement and "MERGE (a:" not in statement


def test_node_writes_merge_on_the_constrained_key() -> None:
    assert "MERGE (f:Function {repo_id: row.repo_id, fqn: row.fqn})" in cypher.WRITE_FUNCTIONS
    assert "MERGE (m:Module {repo_id: row.repo_id, fqn: row.fqn})" in cypher.WRITE_MODULES
    assert "MERGE (c:Class {repo_id: row.repo_id, fqn: row.fqn})" in cypher.WRITE_CLASSES


def test_bulk_writes_use_unwind() -> None:
    for statement in (
        cypher.WRITE_MODULES,
        cypher.WRITE_CLASSES,
        cypher.WRITE_FUNCTIONS,
        cypher.WRITE_METHOD_LINKS,
        cypher.WRITE_IMPORTS,
        cypher.WRITE_INHERITS,
        cypher.WRITE_INSTANTIATES,
        cypher.WRITE_CALLS,
    ):
        assert statement.strip().startswith("UNWIND $rows AS row")


def test_dead_code_excludes_the_documented_categories() -> None:
    for clause in ("is_test = false", "is_entrypoint = false", "is_dunder = false", "INHERITS"):
        assert clause in cypher.DEAD_CODE


# -- architecture rule: no inline Cypher outside graph/cypher.py ------------------


def test_no_query_strings_outside_the_cypher_module() -> None:
    root = Path(__file__).resolve().parents[2] / "faultline"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in {"cypher.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r'"""\s*\n?\s*(MATCH|UNWIND|MERGE|CALL) |"(MATCH|UNWIND|MERGE) \(', text):
            offenders.append(path.relative_to(root).as_posix())
    assert offenders == [], f"inline Cypher found in: {offenders}"
