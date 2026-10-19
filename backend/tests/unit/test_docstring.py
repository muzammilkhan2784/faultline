from __future__ import annotations

import textwrap

import pytest

from faultline.parsing.docstring import MAX_SUMMARY_CHARS, summarise
from faultline.parsing.module_parser import parse_source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   \n  ", None),
        ("Return the sum.", "Return the sum."),
        ("Return the sum. Then do more things.", "Return the sum."),
        ("First line\ncontinues here.\n\nSecond paragraph.", "First line continues here."),
        ("Is it done? Yes it is.", "Is it done?"),
        # no capital after the full stop, so it is not a sentence boundary
        (
            "Return the value, e.g. the first one, if any.",
            "Return the value, e.g. the first one, if any.",
        ),
        (":param x: the input", None),
        ("@param x: the input", None),
    ],
)
def test_summarise(raw: str | None, expected: str | None) -> None:
    assert summarise(raw) == expected


def test_a_very_long_sentence_is_capped_with_an_ellipsis() -> None:
    out = summarise("word " * 200)
    assert out is not None
    assert len(out) == MAX_SUMMARY_CHARS
    assert out.endswith("…")


def test_the_parser_records_the_first_sentence_of_a_docstring() -> None:
    facts = parse_source(
        textwrap.dedent(
            '''
            def charge(order):
                """Charge a customer. Retries twice on failure."""

            def bare(order):
                return order

            class Cart:
                def total(self):
                    """Sum the line items.

                    Longer explanation that should not be stored.
                    """
            '''
        ),
        module_fqn="shop.billing",
    )
    docs = {f.name: f.doc for f in facts.functions}
    assert docs == {"charge": "Charge a customer.", "bare": None, "total": "Sum the line items."}
