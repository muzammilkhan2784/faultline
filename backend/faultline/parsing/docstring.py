"""Reduce a docstring to the one sentence a person reads first.

The graph stores this, not the whole docstring: a briefing wants "what is this
for", and a 40-line docstring per function would bloat the database for no gain.
"""

from __future__ import annotations

import re

MAX_SUMMARY_CHARS = 220

#: A sentence ends at ``.``, ``!`` or ``?`` followed by a capital (or an opening
#: quote/bracket). Requiring the capital keeps ``e.g. the value`` in one piece.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'`(])")


def summarise(docstring: str | None) -> str | None:
    """First sentence of the first paragraph, on one line, capped in length.

    Returns ``None`` when there is nothing readable to show: no docstring, a blank
    one, or one that opens with a Sphinx field (``:param x:``) rather than prose.
    """
    if not docstring:
        return None
    paragraph = docstring.strip().split("\n\n", 1)[0]
    flat = " ".join(paragraph.split())
    if not flat or flat[0] in ":@":
        return None
    first = _SENTENCE_END.split(flat, maxsplit=1)[0]
    if len(first) > MAX_SUMMARY_CHARS:
        first = first[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"
    return first
