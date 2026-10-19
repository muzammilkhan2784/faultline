"""Cyclomatic complexity straight from the AST.

McCabe complexity is "one, plus one for every decision point". The only thing
that needs care is *scope*: a function that contains a nested function must not
inherit the nested function's branches, because the nested function is recorded
as a :class:`~faultline.models.FunctionDef` in its own right and would be
counted twice.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

#: Nodes that each add one branch. Kept as an explicit tuple so the README can
#: state the rule exactly rather than hand-waving at "McCabe".
DECISION_NODES: tuple[type[ast.AST], ...] = (
    ast.If,
    ast.IfExp,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.Assert,
    ast.comprehension,
    ast.Match,
)

#: Walking stops at these: they own their own complexity score.
SCOPE_BOUNDARIES: tuple[type[ast.AST], ...] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)


def iter_own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Yield every descendant of ``node`` that belongs to *its* scope.

    Nested ``def`` and ``class`` bodies are skipped. A ``lambda`` is **not** a
    boundary: lambdas never become their own :class:`FunctionDef`, so their
    branches belong to the enclosing function.
    """
    stack: list[ast.AST] = []
    # The node itself is the function being measured; start from its children.
    stack.extend(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        yield current
        if isinstance(current, SCOPE_BOUNDARIES):
            continue
        stack.extend(ast.iter_child_nodes(current))


def cyclomatic_complexity(node: ast.AST) -> int:
    """Complexity of one function, excluding any function nested inside it.

    A straight-line function scores 1. Each ``if``/``for``/``while``/``except``/
    ``with``/``assert``/comprehension/``match`` adds 1, and each extra operand of
    a boolean operator adds 1 (``a and b and c`` is two decisions, not one).
    """
    score = 1
    for child in iter_own_scope(node):
        if isinstance(child, SCOPE_BOUNDARIES):
            continue
        if isinstance(child, DECISION_NODES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.match_case) and child.guard is not None:
            score += 1
    return score
