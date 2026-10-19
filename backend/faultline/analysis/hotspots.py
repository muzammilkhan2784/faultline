"""Hotspot ranking: what is risky to touch.

``risk_score = fan_in x complexity`` -- many callers *and* complicated
internals. It is a blunt heuristic and should be described as one, not dressed
up as science. What makes it useful is the fan-in half: that number comes from
resolved call edges, so it reflects the code as written rather than a guess.

Only high-confidence edges count towards fan-in. Letting ambiguous edges inflate
the number would put whatever method has the most common name at the top of the
list every time.
"""

from __future__ import annotations

from typing import Any

from faultline.graph import cypher
from faultline.graph.client import GraphClient

DEFAULT_MIN_CONFIDENCE = 0.8
DEFAULT_MIN_FAN_IN = 3
DEFAULT_LIMIT = 50


def hotspots(
    client: GraphClient,
    repo_id: str,
    *,
    min_fan_in: int = DEFAULT_MIN_FAN_IN,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Functions ranked by how much a change to them would cost."""
    return client.read(
        cypher.HOTSPOTS,
        repo_id=repo_id,
        min_fan_in=min_fan_in,
        min_confidence=min_confidence,
        limit=limit,
    )
