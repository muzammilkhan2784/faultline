"""Small helpers shared by the unit tests.

Most parser and resolver tests need no files at all: they hand a string of
source to :func:`parse` and assert on the facts that come back.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable

from faultline.models import CallEdge, ModuleFacts, ResolvedRepo
from faultline.parsing.module_parser import parse_source
from faultline.parsing.resolver import resolve_repository


def parse(source: str, module_fqn: str = "pkg.mod", **kwargs: object) -> ModuleFacts:
    """Parse dedented source as if it were the module ``module_fqn``."""
    return parse_source(textwrap.dedent(source), module_fqn=module_fqn, **kwargs)  # type: ignore[arg-type]


def resolve(modules: dict[str, str], *, packages: Iterable[str] = ()) -> ResolvedRepo:
    """Parse several in-memory modules and resolve calls between them.

    ``modules`` maps a module FQN to its source. Names listed in ``packages``
    are treated as ``__init__.py`` files, which changes relative-import maths.
    """
    package_set = set(packages)
    facts = [
        parse(source, fqn, path=fqn.replace(".", "/") + ".py", is_package=fqn in package_set)
        for fqn, source in modules.items()
    ]
    return resolve_repository(facts)


def call_edges(resolved: ResolvedRepo) -> set[tuple[str, str]]:
    return {(e.caller_fqn, e.callee_fqn) for e in resolved.calls}


def edge(resolved: ResolvedRepo, caller: str, callee: str) -> CallEdge:
    """The single edge from ``caller`` to ``callee``; fails loudly if absent."""
    matches = [e for e in resolved.calls if e.caller_fqn == caller and e.callee_fqn == callee]
    assert matches, f"no edge {caller} -> {callee}; have {sorted(call_edges(resolved))}"
    return matches[0]


def edges_from(resolved: ResolvedRepo, caller: str) -> list[CallEdge]:
    return [e for e in resolved.calls if e.caller_fqn == caller]
