from __future__ import annotations

import pytest

from faultline.graph import lucene


def test_escape_neutralises_every_metacharacter() -> None:
    hostile = '+-&|!(){}[]^"~*?:\\/'
    escaped = lucene.escape(hostile)
    assert escaped == "".join("\\" + c for c in hostile)


def test_escape_leaves_plain_names_alone() -> None:
    assert lucene.escape("verify_token") == "verify_token"


def test_fuzzy_query_matches_exact_prefix_and_near_misses() -> None:
    assert lucene.fuzzy("super_ln") == "(super_ln~ OR super_ln*)"


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_fuzzy_of_nothing_is_no_query(blank: str) -> None:
    assert lucene.fuzzy(blank) == ""


def test_fuzzy_escapes_user_text() -> None:
    assert lucene.fuzzy("a:b") == "(a\\:b~ OR a\\:b*)"
