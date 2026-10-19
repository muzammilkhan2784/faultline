"""The fixture project has exactly-known answers. If any of these change, either
the resolver regressed or a deliberate improvement needs the expectation updated
*with a reason*."""

from __future__ import annotations

from collections import defaultdict

import pytest

from faultline.analysis.cycles import build_graph, find_cycles
from faultline.ingest.pipeline import Analysis

S = "shop"


def edges(a: Analysis) -> dict[tuple[str, str], tuple[float, str]]:
    return {(e.caller_fqn, e.callee_fqn): (e.confidence, e.resolution) for e in a.resolved.calls}


# -- discovery and parsing ----------------------------------------------------------


def test_every_module_is_found_with_the_right_fqn(fixture_analysis: Analysis) -> None:
    assert sorted(m.module_fqn for m in fixture_analysis.resolved.modules) == [
        "shop",
        "shop.broken",
        "shop.cycle_a",
        "shop.cycle_b",
        "shop.models",
        "shop.services",
        "shop.services.billing",
        "shop.services.notify",
        "shop.star",
        "shop.typed_a",
        "shop.typed_b",
        "shop.utils",
        "shop.web",
        "test_billing",
        "test_models",
        "test_web",
    ]


def test_the_syntax_error_is_recorded_and_ingestion_continues(fixture_analysis: Analysis) -> None:
    assert [f["path"] for f in fixture_analysis.parse_failures] == ["shop/broken.py"]
    assert fixture_analysis.parse_failures[0]["error"].startswith("SyntaxError")
    broken = next(m for m in fixture_analysis.resolved.modules if m.module_fqn == "shop.broken")
    assert broken.parse_error and broken.functions == []


def test_test_modules_are_flagged(fixture_analysis: Analysis) -> None:
    flagged = {m.module_fqn for m in fixture_analysis.resolved.modules if m.is_test}
    assert flagged == {"test_billing", "test_models", "test_web"}


def test_packages_are_flagged(fixture_analysis: Analysis) -> None:
    packages = {m.module_fqn for m in fixture_analysis.resolved.modules if m.is_package}
    assert packages == {"shop", "shop.services"}


# -- the resolved call graph ---------------------------------------------------------


def test_the_complete_call_graph(fixture_analysis: Analysis) -> None:
    b = f"{S}.services.billing"
    n = f"{S}.services.notify"
    expected = {
        (f"{S}.cycle_a.ping", f"{S}.cycle_b.pong"): (1.0, "exact"),
        (f"{S}.cycle_b.pong", f"{S}.cycle_a.ping"): (1.0, "exact"),
        (f"{S}.models.Admin.label", f"{S}.models.User.label"): (0.95, "exact"),
        (f"{S}.models.Admin.promote", f"{S}.models.Base.describe"): (0.95, "exact"),
        (f"{S}.models.Base.describe", f"{S}.models.Base.label"): (1.0, "exact"),
        (f"{S}.models.User.save", f"{S}.models.User.validate"): (1.0, "exact"),
        (f"{b}._apply_discount", f"{S}.utils.clamp"): (1.0, "exact"),
        (f"{b}.charge", f"{b}._apply_discount"): (1.0, "exact"),
        (f"{b}.charge", f"{S}.models.User.save"): (0.9, "inferred"),
        (f"{b}.charge", f"{n}.send"): (1.0, "exact"),
        (f"{b}.charge", f"{S}.utils.format_money"): (1.0, "exact"),
        (f"{n}.audit", f"{S}.utils.format_money"): (1.0, "exact"),
        (f"{n}.broadcast", f"{S}.models.Order.save"): (0.5, "ambiguous"),
        (f"{n}.broadcast", f"{S}.models.User.save"): (0.5, "ambiguous"),
        (f"{n}.send", f"{n}.audit"): (1.0, "exact"),
        (f"{S}.star.uses_star", f"{S}.utils.format_money"): (0.8, "inferred"),
        (f"{S}.typed_a.A.go", f"{S}.typed_b.make"): (1.0, "exact"),
        (f"{S}.web.checkout", f"{S}.web.App.route"): (0.7, "inferred"),
        (f"{S}.web.checkout", f"{S}.models.User.__init__"): (0.95, "exact"),
        (f"{S}.web.checkout", f"{b}.charge"): (1.0, "exact"),
        (f"{S}.web.index", f"{S}.web.App.route"): (0.7, "inferred"),
        (f"{S}.web.index", f"{S}.utils.format_money"): (1.0, "exact"),
        ("test_billing.test_charge_applies_discount", f"{S}.models.User.__init__"): (1.0, "exact"),
        ("test_billing.test_charge_applies_discount", f"{b}.charge"): (1.0, "exact"),
        ("test_billing.test_charge_without_discount", f"{S}.models.User.__init__"): (1.0, "exact"),
        ("test_billing.test_charge_without_discount", f"{b}.charge"): (1.0, "exact"),
        ("test_models.test_admin_promote_describes", f"{S}.models.User.__init__"): (0.95, "exact"),
        ("test_web.test_checkout_returns_receipt", f"{S}.web.checkout"): (1.0, "exact"),
        ("test_web.test_index_is_free", f"{S}.web.index"): (1.0, "exact"),
    }
    assert edges(fixture_analysis) == expected
    # `edges()` keys on (caller, callee), which collapses `charge` calling
    # `format_money` on two different lines. They are two distinct edges.
    assert len(fixture_analysis.resolved.calls) == 30
    money_lines = sorted(
        e.line
        for e in fixture_analysis.resolved.calls
        if e.caller_fqn == f"{b}.charge" and e.callee_fqn == f"{S}.utils.format_money"
    )
    assert money_lines == [15, 16]


def test_call_sites_the_resolver_correctly_refuses(fixture_analysis: Analysis) -> None:
    """``checkout(1).startswith`` and ``Admin("root").promote`` hang off a call
    result. That is unresolvable statically, so there must be no edge for it."""
    callers = {c for c, _ in edges(fixture_analysis)}
    assert ("test_models.test_admin_promote_describes", f"{S}.models.Admin.promote") not in edges(
        fixture_analysis
    )
    assert "test_web.test_checkout_returns_receipt" in callers  # only the resolvable call


def test_instantiations(fixture_analysis: Analysis) -> None:
    got = {(i.caller_fqn, i.class_fqn) for i in fixture_analysis.resolved.instantiations}
    assert got == {
        (f"{S}.web.checkout", f"{S}.models.Admin"),
        ("test_billing.test_charge_applies_discount", f"{S}.models.User"),
        ("test_billing.test_charge_without_discount", f"{S}.models.User"),
        ("test_models.test_admin_promote_describes", f"{S}.models.Admin"),
    }


def test_inheritance_chain(fixture_analysis: Analysis) -> None:
    got = {(e.child_fqn, e.parent_fqn) for e in fixture_analysis.resolved.inherits}
    assert got == {
        (f"{S}.models.User", f"{S}.models.Base"),
        (f"{S}.models.Admin", f"{S}.models.User"),
    }


def test_statistics_partition_the_call_sites(fixture_analysis: Analysis) -> None:
    s = fixture_analysis.stats
    assert s.call_sites_total == 35
    assert (s.resolved_exact, s.resolved_inferred, s.resolved_ambiguous) == (24, 4, 1)
    assert s.dropped_builtin == 6  # bool, str, round, len, print, repr
    assert (s.dropped_external, s.dropped_too_ambiguous, s.unresolved) == (0, 0, 0)
    assert s.internal_resolution_rate == 1.0


# -- import graph and cycles ---------------------------------------------------------


def import_graph(a: Analysis, *, runtime_only: bool) -> dict[str, list[str]]:
    rows = [
        {"source": e.source_fqn, "target": e.target_fqn}
        for e in a.resolved.imports
        if not (runtime_only and (e.is_type_only or e.is_deferred))
    ]
    return build_graph(rows)


def test_the_runtime_import_cycle_is_found(fixture_analysis: Analysis) -> None:
    cycles = find_cycles(import_graph(fixture_analysis, runtime_only=True))
    assert [set(c) for c in cycles] == [{f"{S}.cycle_a", f"{S}.cycle_b"}]


def test_type_checking_import_does_not_create_a_cycle(fixture_analysis: Analysis) -> None:
    """typed_a -> typed_b is real; typed_b -> typed_a only exists for the type
    checker. Counting it would report a cycle that cannot happen at runtime."""
    with_type_only = find_cycles(import_graph(fixture_analysis, runtime_only=False))
    without = find_cycles(import_graph(fixture_analysis, runtime_only=True))
    assert {f"{S}.typed_a", f"{S}.typed_b"} in [set(c) for c in with_type_only]
    assert all(f"{S}.typed_a" not in c for c in without)


def test_the_star_import_edge_is_marked(fixture_analysis: Analysis) -> None:
    star = [e for e in fixture_analysis.resolved.imports if e.kind == "star"]
    assert [(e.source_fqn, e.target_fqn) for e in star] == [(f"{S}.star", f"{S}.utils")]


# -- graph-derived answers, computed independently of Neo4j --------------------------


def reverse(a: Analysis, min_conf: float = 0.5) -> dict[str, set[str]]:
    rev: dict[str, set[str]] = defaultdict(set)
    for e in a.resolved.calls:
        if e.confidence >= min_conf:
            rev[e.callee_fqn].add(e.caller_fqn)
    return rev


def reachable_callers(a: Analysis, target: str, depth: int) -> dict[str, int]:
    rev = reverse(a)
    seen: dict[str, int] = {}
    frontier = {target}
    for hop in range(1, depth + 1):
        frontier = {c for t in frontier for c in rev.get(t, ())} - set(seen) - {target}
        for caller in frontier:
            seen[caller] = hop
    return seen


def test_impact_of_clamp(fixture_analysis: Analysis) -> None:
    got = reachable_callers(fixture_analysis, f"{S}.utils.clamp", depth=3)
    b = f"{S}.services.billing"
    assert got == {
        f"{b}._apply_discount": 1,
        f"{b}.charge": 2,
        f"{S}.web.checkout": 3,
        "test_billing.test_charge_applies_discount": 3,
        "test_billing.test_charge_without_discount": 3,
    }


def test_depth_bounds_the_impact_set(fixture_analysis: Analysis) -> None:
    deeper = reachable_callers(fixture_analysis, f"{S}.utils.clamp", depth=4)
    assert deeper["test_web.test_checkout_returns_receipt"] == 4
    assert "test_web.test_checkout_returns_receipt" not in reachable_callers(
        fixture_analysis, f"{S}.utils.clamp", depth=3
    )


def test_test_selection_for_format_money(fixture_analysis: Analysis) -> None:
    tests = {
        fqn: hops
        for fqn, hops in reachable_callers(fixture_analysis, f"{S}.utils.format_money", 6).items()
        if fqn.startswith("test_")
    }
    assert tests == {
        "test_billing.test_charge_applies_discount": 2,
        "test_billing.test_charge_without_discount": 2,
        "test_web.test_index_is_free": 2,
        "test_web.test_checkout_returns_receipt": 3,
    }


def test_confidence_filter_removes_ambiguous_edges(fixture_analysis: Analysis) -> None:
    strict = reverse(fixture_analysis, min_conf=0.6)
    loose = reverse(fixture_analysis, min_conf=0.5)
    order_save = f"{S}.models.Order.save"
    assert f"{S}.services.notify.broadcast" in loose[order_save]
    assert order_save not in {t for t, callers in strict.items() if callers}


def test_dead_code_candidates(fixture_analysis: Analysis) -> None:
    """Zero inbound call edges, and not a test, entrypoint, dunder or override.

    Derived by hand from the fixture source. ``Admin.label`` has no callers but
    overrides ``User.label``, so it is excluded: overrides are normally reached
    through their base class. ``Admin.promote`` *is* listed even though a test
    calls it, because that call hangs off a constructor result and is
    unresolvable -- an honest false positive the README documents.
    """
    a = fixture_analysis.resolved
    called = {e.callee_fqn for e in a.calls}
    parents: dict[str, set[str]] = defaultdict(set)
    for inherit in a.inherits:
        parents[inherit.child_fqn].add(inherit.parent_fqn)

    def ancestors(class_fqn: str) -> set[str]:
        found: set[str] = set()
        stack = list(parents.get(class_fqn, ()))
        while stack:
            current = stack.pop()
            if current not in found:
                found.add(current)
                stack.extend(parents.get(current, ()))
        return found

    all_functions = {f.fqn for m in a.modules for f in m.functions}

    def overrides(f) -> bool:
        return f.class_fqn is not None and any(
            f"{base}.{f.name}" in all_functions for base in ancestors(f.class_fqn)
        )

    dead = {
        f.fqn
        for m in a.modules
        for f in m.functions
        if f.fqn not in called and not (f.is_test or f.is_entrypoint or f.is_dunder or overrides(f))
    }
    assert dead == {
        f"{S}.models.Admin.promote",
        f"{S}.utils.unused_helper",
        f"{S}.services.notify.broadcast",
        f"{S}.typed_a.A.go",
        f"{S}.typed_b.describe",
        f"{S}.star.uses_star",
        f"{S}.web.App.route.<locals>.register",
    }


@pytest.mark.parametrize(
    ("fqn", "expected"),
    [
        (f"{S}.cycle_a.ping", 2),  # 1 + the conditional expression
        (f"{S}.utils.clamp", 3),
        (f"{S}.utils.format_money", 1),
        (f"{S}.services.notify.broadcast", 2),  # 1 + the for loop
    ],
)
def test_complexity_scores(fixture_analysis: Analysis, fqn: str, expected: int) -> None:
    fn = next(f for m in fixture_analysis.resolved.modules for f in m.functions if f.fqn == fqn)
    assert fn.complexity == expected
