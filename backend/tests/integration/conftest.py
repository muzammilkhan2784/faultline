"""Integration fixtures: a real Neo4j, with the fixture project ingested once.

Every test here is skipped, not failed, when no database is reachable, so
``pytest`` on a laptop without Docker still goes green. CI provides Neo4j as a
service container and these run for real.

Isolation: every repo written by the tests has an id starting ``test-`` and is
deleted afterwards, so the suite never touches data it did not create.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from faultline.config import Settings
from faultline.graph.client import GraphClient
from faultline.graph.schema import ensure_schema
from faultline.graph.writer import GraphWriter
from faultline.ingest.pipeline import Analysis

TEST_REPO_ID = "test-fixture-shop"


def _settings() -> Settings:
    return Settings(
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("NEO4J_PASSWORD", "faultline-dev"),
    )


@pytest.fixture(scope="session")
def graph_client() -> Iterator[GraphClient]:
    client = GraphClient(_settings())
    if not client.verify():
        client.close()
        pytest.skip("Neo4j is not reachable; integration tests need a live database")
    ensure_schema(client)
    yield client
    client.run_autocommit(
        "MATCH (n) WHERE n.repo_id STARTS WITH 'test-' DETACH DELETE n",
    )
    client.run_autocommit("MATCH (r:Repo) WHERE r.id STARTS WITH 'test-' DETACH DELETE r")
    client.close()


@pytest.fixture(scope="session")
def ingested_repo(graph_client: GraphClient, fixture_analysis: Analysis) -> str:
    """The sample project written to Neo4j once for the whole session."""
    writer = GraphWriter(graph_client)
    writer.delete_repo(TEST_REPO_ID)  # a previous aborted run may have left it
    writer.upsert_repo(
        TEST_REPO_ID,
        {"name": "shop", "source": str(Path("fixture")), "status": "ready", "analyzed_at": "now"},
    )
    writer.write_repo(TEST_REPO_ID, fixture_analysis.resolved)
    return TEST_REPO_ID
