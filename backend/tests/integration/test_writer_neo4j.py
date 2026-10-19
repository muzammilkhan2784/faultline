"""The writer against a real database: counts, idempotence, schema, deletion."""

from __future__ import annotations

import pytest

from faultline.graph.client import GraphClient
from faultline.graph.schema import describe_schema, ensure_schema
from faultline.graph.writer import GraphWriter
from faultline.ingest.pipeline import Analysis

pytestmark = pytest.mark.integration


def scalar(client: GraphClient, query: str, **params: object) -> int:
    (row,) = client.read(query, **params)
    return int(next(iter(row.values())))


def test_node_counts_match_what_was_parsed(
    graph_client: GraphClient, ingested_repo: str, fixture_analysis: Analysis
) -> None:
    def count(label: str) -> int:
        return scalar(
            graph_client, f"MATCH (n:{label} {{repo_id: $r}}) RETURN count(n)", r=ingested_repo
        )

    resolved = fixture_analysis.resolved
    assert count("Module") == len(resolved.modules) == 16
    assert count("Class") == sum(len(m.classes) for m in resolved.modules) == 6
    assert count("Function") == sum(len(m.functions) for m in resolved.modules) == 33


def test_relationship_counts_match_what_was_resolved(
    graph_client: GraphClient, ingested_repo: str, fixture_analysis: Analysis
) -> None:
    def count(rel: str) -> int:
        return scalar(
            graph_client,
            f"MATCH ({{repo_id: $r}})-[e:{rel}]->() RETURN count(e)",
            r=ingested_repo,
        )

    resolved = fixture_analysis.resolved
    assert count("CALLS") == len(resolved.calls) == 30
    assert count("IMPORTS") == len(resolved.imports)
    assert count("INHERITS") == len(resolved.inherits) == 2
    assert count("INSTANTIATES") == len(resolved.instantiations) == 4


def test_two_calls_on_different_lines_stay_two_edges(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    rows = graph_client.read(
        "MATCH (:Function {repo_id: $r, fqn: 'shop.services.billing.charge'})"
        "-[c:CALLS]->(:Function {fqn: 'shop.utils.format_money'}) RETURN c.line AS line "
        "ORDER BY line",
        r=ingested_repo,
    )
    assert [r["line"] for r in rows] == [15, 16]


def test_methods_belong_to_their_class_and_their_module(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    rows = graph_client.read(
        "MATCH (c:Class {repo_id: $r, fqn: 'shop.models.User'})-[:CONTAINS]->(f:Function) "
        "RETURN f.name AS name ORDER BY name",
        r=ingested_repo,
    )
    assert [r["name"] for r in rows] == ["__init__", "label", "save", "validate"]
    assert (
        scalar(
            graph_client,
            "MATCH (:Module {repo_id: $r, fqn: 'shop.models'})-[:CONTAINS]->"
            "(:Function {fqn: 'shop.models.User.save'}) RETURN count(*)",
            r=ingested_repo,
        )
        == 1
    )


def test_the_broken_module_is_stored_with_its_parse_error(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    (row,) = graph_client.read(
        "MATCH (m:Module {repo_id: $r, fqn: 'shop.broken'}) RETURN m.parse_error AS e",
        r=ingested_repo,
    )
    assert row["e"].startswith("SyntaxError")


def test_writing_the_same_repo_twice_is_idempotent(
    graph_client: GraphClient, ingested_repo: str, fixture_analysis: Analysis
) -> None:
    before = scalar(graph_client, "MATCH (n {repo_id: $r}) RETURN count(n)", r=ingested_repo)
    GraphWriter(graph_client).write_repo(ingested_repo, fixture_analysis.resolved)
    after = scalar(graph_client, "MATCH (n {repo_id: $r}) RETURN count(n)", r=ingested_repo)
    calls = scalar(
        graph_client, "MATCH ({repo_id: $r})-[e:CALLS]->() RETURN count(e)", r=ingested_repo
    )
    assert before == after
    assert calls == 30


def test_a_call_to_a_missing_callee_is_skipped_not_created(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    from faultline.graph import cypher

    graph_client.write_batches(
        cypher.WRITE_CALLS,
        [
            {
                "repo_id": ingested_repo,
                "caller": "shop.utils.clamp",
                "callee": "does.not.exist",
                "line": 1,
                "confidence": 1.0,
                "resolution": "exact",
                "via": "name",
            }
        ],
    )
    assert (
        scalar(
            graph_client,
            "MATCH (n:Function {repo_id: $r, fqn: 'does.not.exist'}) RETURN count(n)",
            r=ingested_repo,
        )
        == 0
    )


def test_schema_has_the_composite_constraints_and_fulltext_index(graph_client: GraphClient) -> None:
    ensure_schema(graph_client)  # idempotent: a second call must not fail
    schema = describe_schema(graph_client)
    constraint_names = {c["name"] for c in schema["constraints"]}
    assert {"repo_id", "module_key", "class_key", "function_key"} <= constraint_names
    index_types = {i["name"]: i["type"] for i in schema["indexes"]}
    assert index_types["symbol_search"] == "FULLTEXT"


def test_composite_constraint_rejects_a_duplicate_key(
    graph_client: GraphClient, ingested_repo: str
) -> None:
    from neo4j.exceptions import ClientError

    with pytest.raises(ClientError):
        graph_client.write(
            "CREATE (:Function {repo_id: $r, fqn: 'shop.utils.clamp'})", r=ingested_repo
        )


def test_the_same_fqn_can_exist_in_two_repos(graph_client: GraphClient) -> None:
    other = "test-other-repo"
    graph_client.write("CREATE (:Function {repo_id: $r, fqn: 'shop.utils.clamp'})", r=other)
    try:
        assert (
            scalar(
                graph_client,
                "MATCH (f:Function {fqn: 'shop.utils.clamp'}) RETURN count(f)",
            )
            >= 2
        )
    finally:
        graph_client.write("MATCH (n {repo_id: $r}) DETACH DELETE n", r=other)


def test_delete_removes_every_node_of_a_repo_and_nothing_else(
    graph_client: GraphClient, fixture_analysis: Analysis
) -> None:
    doomed, survivor = "test-doomed", "test-survivor"
    writer = GraphWriter(graph_client)
    for repo_id in (doomed, survivor):
        writer.upsert_repo(repo_id, {"name": repo_id, "status": "ready"})
        writer.write_repo(repo_id, fixture_analysis.resolved)

    writer.delete_repo(doomed, batch_size=100)  # tiny batches exercise the batching

    assert scalar(graph_client, "MATCH (n {repo_id: $r}) RETURN count(n)", r=doomed) == 0
    assert scalar(graph_client, "MATCH (r:Repo {id: $r}) RETURN count(r)", r=doomed) == 0
    assert scalar(graph_client, "MATCH (n {repo_id: $r}) RETURN count(n)", r=survivor) > 0
    writer.delete_repo(survivor)
