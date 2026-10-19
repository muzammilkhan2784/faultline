"""Dead code *candidates*.

The word candidates is doing real work there. Static analysis of a dynamic
language cannot prove a function is unused: dynamic dispatch, ``getattr``,
plugin registries, template lookups, entry points declared in packaging
metadata and callers outside the repository all produce false positives.

So the query is deliberately conservative. It excludes tests, dunders, anything
carrying a framework decorator, anything exported through ``__all__``, and --
the one that catches most people out -- any method that overrides a base class
method, since those are usually called through the base.
"""

from __future__ import annotations

from typing import Any

from faultline.graph import cypher
from faultline.graph.client import GraphClient

DEFAULT_LIMIT = 100

CAVEAT = (
    "Candidates only. Dynamic dispatch, getattr, monkey-patching, plugin "
    "registries and callers outside this repository all produce false "
    "positives. Read this as a list worth checking, not a list to delete."
)


def dead_code(
    client: GraphClient, repo_id: str, *, limit: int = DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Functions with no inbound call edge that are not entrypoints."""
    return client.read(cypher.DEAD_CODE, repo_id=repo_id, limit=limit)
