"""Helpers for building safe Lucene (fulltext index) queries from user text."""

from __future__ import annotations

import re

#: Lucene metacharacters. A user typing ``auth.verify_token(`` must get results,
#: not a query-parser error.
_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def escape(term: str) -> str:
    """Backslash-escape every Lucene metacharacter in ``term``."""
    return _SPECIAL.sub(lambda match: "\\" + match.group(1), term)


def fuzzy(name: str) -> str:
    """A query matching ``name`` exactly, as a prefix, or within two edits.

    ``super_ln`` finds ``super_len``; ``clmap`` finds ``clamp``. Empty input
    yields an empty string, which callers must treat as "no query".
    """
    term = escape(name.strip())
    return f"({term}~ OR {term}*)" if term else ""
