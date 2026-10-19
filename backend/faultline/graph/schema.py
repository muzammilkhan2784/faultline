"""Constraints and indexes.

Created **before** the first write, never after. The composite uniqueness
constraints on ``(repo_id, fqn)`` are what turn every ``MERGE`` in the writer
into an index seek; without them each one is a full label scan and ingesting a
large repository goes from seconds to minutes.
"""

from __future__ import annotations

from typing import Any

from neo4j.exceptions import ClientError

from faultline.graph.client import GraphClient
from faultline.graph.cypher import SCHEMA_STATEMENTS
from faultline.logging import get_logger

logger = get_logger(__name__)


def ensure_schema(client: GraphClient) -> list[str]:
    """Apply every constraint and index. Idempotent and safe to call at boot.

    A statement a particular Neo4j build refuses -- an older 5.x without
    composite uniqueness constraints, for instance -- is logged and skipped
    rather than fatal. The MERGEs still work without it, just more slowly.
    """
    applied: list[str] = []
    for statement in SCHEMA_STATEMENTS:
        try:
            client.write(statement)
        except ClientError as exc:
            logger.warning(
                "schema statement skipped",
                extra={"statement": statement.split("\n")[0], "error": exc.message},
            )
            continue
        applied.append(statement)
    logger.info("schema ensured", extra={"applied": len(applied), "total": len(SCHEMA_STATEMENTS)})
    return applied


def describe_schema(client: GraphClient) -> dict[str, list[dict[str, Any]]]:
    """What the database actually has -- handy in ``/health`` and when a
    benchmark run is unexpectedly slow."""
    try:
        constraints = client.read("SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties")
        indexes = client.read("SHOW INDEXES YIELD name, type, labelsOrTypes, properties")
    except ClientError as exc:  # pragma: no cover - depends on server edition
        logger.warning("could not describe schema", extra={"error": exc.message})
        return {"constraints": [], "indexes": []}
    return {"constraints": constraints, "indexes": indexes}
