"""Impact analysis — the flagship query.

"If I change this function, what else breaks?" is reverse reachability over the
call graph: every function from which the changed symbol is reachable, within a
bounded number of hops, along edges the resolver was confident enough about.

The bound matters. Without it, on a real codebase almost everything reaches
almost everything through a logging helper, and the answer degenerates to "the
whole repo". Three hops is the useful default.
"""

from __future__ import annotations

from typing import Any

from faultline.graph import cypher
from faultline.graph.client import GraphClient

DEFAULT_DEPTH = 3
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_LIMIT = 200


def impact(
    client: GraphClient,
    repo_id: str,
    fqn: str,
    *,
    depth: int = DEFAULT_DEPTH,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Everything that can reach ``fqn``, nearest first."""
    return client.read(
        cypher.impact_query(depth),
        repo_id=repo_id,
        fqn=fqn,
        min_confidence=min_confidence,
        limit=limit,
    )


def dependencies(
    client: GraphClient,
    repo_id: str,
    fqn: str,
    *,
    depth: int = DEFAULT_DEPTH,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """The other direction: everything ``fqn`` reaches."""
    return client.read(
        cypher.dependencies_query(depth),
        repo_id=repo_id,
        fqn=fqn,
        min_confidence=min_confidence,
        limit=limit,
    )


def shortest_path(
    client: GraphClient, repo_id: str, from_fqn: str, to_fqn: str
) -> dict[str, Any] | None:
    """How does A reach B? Returns the hops and the per-edge confidences."""
    rows = client.read(cypher.SHORTEST_PATH, repo_id=repo_id, from_fqn=from_fqn, to_fqn=to_fqn)
    if not rows:
        return None
    row = rows[0]
    row["confidence"] = round(float(row.get("confidence") or 0.0), 4)
    return row


def neighbourhood(
    client: GraphClient,
    repo_id: str,
    fqn: str,
    *,
    depth: int = 2,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_nodes: int = 300,
) -> dict[str, Any]:
    """The subgraph the UI draws around one symbol.

    Capped server-side: Cytoscape with a hierarchical layout becomes unusable
    somewhere north of 500 nodes, and a browser that has frozen is worse than a
    view that admits it is showing you a slice. ``total`` lets the UI say
    "showing 300 of 1,412" instead.
    """
    rows = client.read(
        cypher.neighbourhood_query(depth),
        repo_id=repo_id,
        fqn=fqn,
        min_confidence=min_confidence,
        max_nodes=max_nodes,
    )
    if not rows:
        return {"nodes": [], "edges": [], "total": 0, "truncated": False}

    row = rows[0]
    nodes = [n for n in row.get("nodes") or [] if n and n.get("id")]
    # The OPTIONAL MATCH yields a null entry for any node with no outgoing edge.
    edges = [e for e in row.get("edges") or [] if e and e.get("source")]
    total = int(row.get("total") or len(nodes))
    return {
        "nodes": nodes,
        "edges": edges,
        "total": total,
        "truncated": total > len(nodes),
    }
