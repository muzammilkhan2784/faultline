from __future__ import annotations

import pytest
from helpers import parse


def cx(body: str, name: str = "f") -> int:
    facts = parse(body)
    return next(fn.complexity for fn in facts.functions if fn.name == name)


def test_flat_function_is_one() -> None:
    assert cx("def f():\n    return 1\n") == 1


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f(a):\n    if a:\n        return 1\n    return 2\n", 2),
        (
            "def f(a):\n    if a:\n        return 1\n    elif a > 1:\n        return 2\n    return 3\n",
            3,
        ),
        ("def f(xs):\n    for x in xs:\n        pass\n", 2),
        ("def f():\n    while True:\n        break\n", 2),
        ("def f():\n    try:\n        pass\n    except ValueError:\n        pass\n", 2),
        ("def f(a):\n    with a:\n        pass\n", 2),
        ("def f(a):\n    assert a\n", 2),
        ("def f(a):\n    return 1 if a else 2\n", 2),
        ("def f(xs):\n    return [x for x in xs]\n", 2),
        ("def f(xs):\n    return [x for x in xs if x]\n", 2),
    ],
)
def test_each_decision_point_adds_one(source: str, expected: int) -> None:
    assert cx(source) == expected


def test_boolean_operator_adds_one_per_extra_operand() -> None:
    assert cx("def f(a, b):\n    return a and b\n") == 2
    assert cx("def f(a, b, c):\n    return a and b and c\n") == 3
    assert cx("def f(a, b, c):\n    return a or (b and c)\n") == 3


def test_match_statement_and_guard() -> None:
    source = (
        "def f(x):\n"
        "    match x:\n"
        "        case 1:\n"
        "            return 1\n"
        "        case n if n > 5:\n"
        "            return 2\n"
    )
    try:
        assert cx(source) == 3  # match itself + the guard
    except SyntaxError:  # pragma: no cover - interpreter older than 3.10
        pytest.skip("match statement unsupported")


def test_nested_function_body_is_not_double_counted() -> None:
    """The nested function is its own FunctionDef with its own score."""
    facts = parse(
        """
        def outer(a):
            if a:
                pass

            def inner(b):
                if b:
                    pass
                if b > 1:
                    pass
        """
    )
    scores = {fn.name: fn.complexity for fn in facts.functions}
    assert scores == {"outer": 2, "inner": 3}


def test_nested_class_body_is_not_counted_in_enclosing_function() -> None:
    facts = parse(
        """
        def factory():
            class Local:
                def m(self, a):
                    if a:
                        pass
            return Local
        """
    )
    scores = {fn.name: fn.complexity for fn in facts.functions}
    assert scores["factory"] == 1
    assert scores["m"] == 2


def test_lambda_branches_belong_to_enclosing_function() -> None:
    """Lambdas never become their own FunctionDef, so they count where they sit."""
    assert cx("def f(a):\n    g = lambda x: 1 if x else 2\n    return g(a)\n") == 2
