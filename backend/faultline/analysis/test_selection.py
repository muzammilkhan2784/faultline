"""Test selection: which tests actually exercise the code you changed.

This is the query that makes Faultline feel like a tool rather than a diagram.
The answer is rendered as a runnable ``pytest`` command, so the output is
something you paste into a terminal.

Test selection allows more hops than impact analysis does. A test rarely calls
the function under test directly — it goes through a fixture, a client, a
service object — so six hops is the useful default where three is right for
impact.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from faultline.graph import cypher
from faultline.graph.client import GraphClient

DEFAULT_DEPTH = 6
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_LIMIT = 200


def select_tests(
    client: GraphClient,
    repo_id: str,
    fqn: str,
    *,
    depth: int = DEFAULT_DEPTH,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Every test function that transitively reaches ``fqn``."""
    return client.read(
        cypher.test_selection_query(depth),
        repo_id=repo_id,
        fqn=fqn,
        min_confidence=min_confidence,
        limit=limit,
    )


def node_id(test: dict[str, Any]) -> str:
    """The pytest node id for one selected test.

    ``tests/test_auth.py::TestLogin::test_expired_token`` for a method,
    ``tests/test_auth.py::test_expired_token`` for a plain function.
    """
    path = test.get("path") or test.get("module", "").replace(".", "/") + ".py"
    class_fqn = test.get("class_fqn")
    if class_fqn:
        return f"{path}::{class_fqn.rsplit('.', 1)[-1]}::{test['name']}"
    return f"{path}::{test['name']}"


def pytest_command(tests: Iterable[dict[str, Any]], *, max_ids: int = 40) -> str:
    """Render the selection as a command you can paste into a terminal.

    Beyond ``max_ids`` tests the node-id list stops being something anyone
    would paste, so it collapses to the set of files instead.
    """
    ids = [node_id(test) for test in tests]
    if not ids:
        return ""
    if len(ids) > max_ids:
        files = sorted({node.split("::", 1)[0] for node in ids})
        return "pytest " + " ".join(files)
    return "pytest " + " ".join(ids)
