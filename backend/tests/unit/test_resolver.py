"""Call resolution: every rung of the ladder, the confidence each earns, and the
invariants the statistics must satisfy."""

from __future__ import annotations

import pytest
from helpers import call_edges, edge, edges_from, parse, resolve

from faultline.models import MAX_AMBIGUOUS_FANOUT

M = "pkg.mod"

# ---------------------------------------------------------------------------
# bare names (spec 9.2)
# ---------------------------------------------------------------------------


def test_nested_function_in_the_caller_resolves_exactly() -> None:
    r = resolve({M: "def outer():\n    def helper(): ...\n    helper()\n"})
    e = edge(r, f"{M}.outer", f"{M}.outer.<locals>.helper")
    assert (e.confidence, e.resolution, e.via) == (1.0, "exact", "name")


def test_enclosing_function_scope_is_walked_outward() -> None:
    r = resolve(
        {
            M: """
            def outer():
                def helper(): ...
                def inner():
                    helper()
            """
        }
    )
    e = edge(r, f"{M}.outer.<locals>.inner", f"{M}.outer.<locals>.helper")
    assert e.resolution == "exact"


def test_nested_function_shadows_a_module_level_name() -> None:
    r = resolve(
        {
            M: """
            def helper(): ...
            def outer():
                def helper(): ...
                helper()
            """
        }
    )
    assert call_edges(r) == {(f"{M}.outer", f"{M}.outer.<locals>.helper")}


def test_module_global_function() -> None:
    r = resolve({M: "def helper(): ...\ndef go():\n    helper()\n"})
    assert edge(r, f"{M}.go", f"{M}.helper").confidence == 1.0


def test_imported_function() -> None:
    r = resolve(
        {
            "pkg.helpers": "def fmt(): ...\n",
            M: "from pkg.helpers import fmt\ndef go():\n    fmt()\n",
        }
    )
    assert edge(r, f"{M}.go", "pkg.helpers.fmt").resolution == "exact"


def test_import_alias_resolves_to_the_real_target() -> None:
    r = resolve(
        {
            "pkg.helpers": "def fmt(): ...\n",
            M: "from pkg.helpers import fmt as f\ndef go():\n    f()\n",
        }
    )
    assert call_edges(r) == {(f"{M}.go", "pkg.helpers.fmt")}


def test_relative_import_resolves() -> None:
    r = resolve(
        {
            "pkg.helpers": "def fmt(): ...\n",
            M: "from .helpers import fmt\ndef go():\n    fmt()\n",
        }
    )
    assert ((f"{M}.go"), "pkg.helpers.fmt") in call_edges(r)


def test_star_import_is_inferred_at_point_eight() -> None:
    r = resolve(
        {
            "pkg.helpers": "def fmt(): ...\n",
            M: "from pkg.helpers import *\ndef go():\n    fmt()\n",
        }
    )
    e = edge(r, f"{M}.go", "pkg.helpers.fmt")
    assert (e.confidence, e.resolution) == (0.8, "inferred")


def test_explicit_binding_beats_star_import() -> None:
    r = resolve(
        {
            "a": "def fmt(): ...\n",
            "b": "def fmt(): ...\n",
            M: "from a import *\nfrom b import fmt\ndef go():\n    fmt()\n",
        }
    )
    assert edge(r, f"{M}.go", "b.fmt").resolution == "exact"
    assert ("pkg.mod.go", "a.fmt") not in call_edges(r)


def test_builtin_call_makes_no_edge_and_is_counted() -> None:
    r = resolve({M: "def go(x):\n    return len(x)\n"})
    assert r.calls == []
    assert r.stats.dropped_builtin == 1
    assert r.stats.call_sites_total == 1


def test_a_module_level_definition_shadows_a_builtin() -> None:
    r = resolve({M: "def print(x): ...\ndef go():\n    print(1)\n"})
    assert call_edges(r) == {(f"{M}.go", f"{M}.print")}


def test_bare_name_falls_back_to_ambiguous_module_level_candidates() -> None:
    r = resolve(
        {
            "a": "def helper(): ...\n",
            "b": "def helper(): ...\n",
            M: "def go():\n    helper()\n",
        }
    )
    a, b = edge(r, f"{M}.go", "a.helper"), edge(r, f"{M}.go", "b.helper")
    assert a.resolution == b.resolution == "ambiguous"
    assert a.confidence == b.confidence == 0.5
    assert r.stats.resolved_ambiguous == 1  # one call site, two edges


def test_bare_name_can_never_reach_a_method() -> None:
    """A bare ``helper()`` inside a method is a NameError; it must not resolve
    to ``C.helper``, which would be an edge that cannot exist at runtime."""
    r = resolve(
        {
            M: """
            class C:
                def helper(self): ...
                def go(self):
                    helper()
            """
        }
    )
    assert r.calls == []


def test_bare_name_can_never_reach_a_nested_function_elsewhere() -> None:
    r = resolve(
        {
            "a": "def outer():\n    def hidden(): ...\n",
            M: "def go():\n    hidden()\n",
        }
    )
    assert r.calls == []


def test_name_defined_nowhere_is_counted_as_external() -> None:
    r = resolve({M: "def go():\n    mystery()\n"})
    assert r.calls == []
    assert r.stats.dropped_external == 1
    assert r.stats.unresolved == 0


def test_name_imported_from_outside_the_repo_is_external() -> None:
    r = resolve({M: "from requests import get\ndef go():\n    get('x')\n"})
    assert r.calls == []
    assert r.stats.dropped_external == 1


def test_fanout_above_the_cap_is_dropped_entirely() -> None:
    modules = {f"m{i}": "def run(): ...\n" for i in range(MAX_AMBIGUOUS_FANOUT + 1)}
    modules[M] = "def go():\n    run()\n"
    r = resolve(modules)
    assert r.calls == []
    assert r.stats.dropped_too_ambiguous == 1


def test_fanout_at_the_cap_is_kept() -> None:
    modules = {f"m{i}": "def run(): ...\n" for i in range(MAX_AMBIGUOUS_FANOUT)}
    modules[M] = "def go():\n    run()\n"
    r = resolve(modules)
    assert len(edges_from(r, f"{M}.go")) == MAX_AMBIGUOUS_FANOUT


def test_direct_recursion_adds_no_edge() -> None:
    r = resolve({M: "def f(n):\n    return f(n - 1)\n"})
    assert r.calls == []


def test_same_target_on_two_lines_gives_two_edges() -> None:
    r = resolve({M: "def h(): ...\ndef go():\n    h()\n    h()\n"})
    assert sorted(e.line for e in edges_from(r, f"{M}.go")) == [3, 4]


def test_decorator_resolves_to_the_decorator_function() -> None:
    r = resolve({M: "def login_required(fn): ...\n@login_required\ndef view(): ...\n"})
    e = edge(r, f"{M}.view", f"{M}.login_required")
    assert (e.via, e.confidence) == ("decorator", 1.0)


# ---------------------------------------------------------------------------
# self calls and inheritance (spec 9.3)
# ---------------------------------------------------------------------------


def test_self_method_on_the_same_class() -> None:
    r = resolve({M: "class C:\n    def a(self): ...\n    def b(self):\n        self.a()\n"})
    e = edge(r, f"{M}.C.b", f"{M}.C.a")
    assert (e.confidence, e.resolution, e.via) == (1.0, "exact", "self")


def test_self_method_through_one_level_of_inheritance() -> None:
    r = resolve(
        {
            M: """
            class Base:
                def a(self): ...
            class Child(Base):
                def b(self):
                    self.a()
            """
        }
    )
    e = edge(r, f"{M}.Child.b", f"{M}.Base.a")
    assert (e.confidence, e.resolution) == (0.95, "exact")


def test_self_method_through_two_levels_of_inheritance() -> None:
    r = resolve(
        {
            M: """
            class A:
                def a(self): ...
            class B(A): ...
            class C(B):
                def go(self):
                    self.a()
            """
        }
    )
    assert edge(r, f"{M}.C.go", f"{M}.A.a").confidence == 0.95


def test_override_wins_over_the_base_method() -> None:
    r = resolve(
        {
            M: """
            class Base:
                def a(self): ...
            class Child(Base):
                def a(self): ...
                def go(self):
                    self.a()
            """
        }
    )
    assert call_edges(r) == {(f"{M}.Child.go", f"{M}.Child.a")}
    assert edge(r, f"{M}.Child.go", f"{M}.Child.a").confidence == 1.0


def test_bases_are_walked_in_declaration_order() -> None:
    r = resolve(
        {
            M: """
            class First:
                def a(self): ...
            class Second:
                def a(self): ...
            class Child(First, Second):
                def go(self):
                    self.a()
            """
        }
    )
    assert call_edges(r) == {(f"{M}.Child.go", f"{M}.First.a")}


def test_base_class_imported_from_another_module() -> None:
    r = resolve(
        {
            "pkg.base": "class Base:\n    def a(self): ...\n",
            M: "from pkg.base import Base\nclass C(Base):\n    def go(self):\n        self.a()\n",
        }
    )
    assert edge(r, f"{M}.C.go", "pkg.base.Base.a").confidence == 0.95


def test_missing_method_on_a_class_with_an_external_base_is_dropped_not_guessed() -> None:
    """The method probably lives on the library base class. Guessing another
    class that happens to share the name would be a wrong edge."""
    r = resolve(
        {
            "other": "class X:\n    def missing(self): ...\n",
            M: "from lib import LibBase\nclass C(LibBase):\n    def go(self):\n        self.missing()\n",
        }
    )
    assert r.calls == []
    assert r.stats.dropped_external == 1


def test_missing_method_on_a_closed_class_falls_back_to_ambiguous() -> None:
    r = resolve(
        {
            "other": "class X:\n    def run(self): ...\nclass Y:\n    def run(self): ...\n",
            M: "class C:\n    def go(self):\n        self.run()\n",
        }
    )
    assert {e.resolution for e in edges_from(r, f"{M}.C.go")} == {"ambiguous"}
    assert len(edges_from(r, f"{M}.C.go")) == 2


def test_object_and_abc_bases_do_not_count_as_external() -> None:
    r = resolve(
        {
            "other": "class X:\n    def run(self): ...\n",
            M: "from abc import ABC\nclass C(ABC, object):\n    def go(self):\n        self.run()\n",
        }
    )
    assert call_edges(r) == {(f"{M}.C.go", "other.X.run")}


def test_cls_in_a_classmethod_resolves_like_self() -> None:
    r = resolve(
        {
            M: """
            class C:
                @classmethod
                def make(cls):
                    return cls.build()
                @classmethod
                def build(cls): ...
            """
        }
    )
    assert ((f"{M}.C.make"), f"{M}.C.build") in call_edges(r)


def test_closure_inside_a_method_still_sees_self() -> None:
    r = resolve(
        {
            M: """
            class C:
                def a(self): ...
                def go(self):
                    def inner():
                        self.a()
                    inner()
            """
        }
    )
    assert ((f"{M}.C.go.<locals>.inner"), f"{M}.C.a") in call_edges(r)


def test_super_call_resolves_to_the_base_class() -> None:
    r = resolve(
        {
            M: """
            class Base:
                def label(self): ...
            class Child(Base):
                def label(self):
                    return super().label()
            """
        }
    )
    e = edge(r, f"{M}.Child.label", f"{M}.Base.label")
    assert (e.confidence, e.resolution) == (0.95, "exact")
    # It must NOT resolve to itself.
    assert (f"{M}.Child.label", f"{M}.Child.label") not in call_edges(r)


def test_super_call_with_an_external_base_is_dropped() -> None:
    r = resolve({M: "class C(LibBase):\n    def m(self):\n        super().m()\n"})
    assert r.calls == []
    assert r.stats.dropped_external == 1


def test_super_call_with_nothing_above_is_unresolved() -> None:
    r = resolve({M: "class C:\n    def m(self):\n        super().m()\n"})
    assert r.calls == []
    assert r.stats.unresolved == 1


def test_self_call_outside_any_class_is_treated_as_an_unknown_object() -> None:
    r = resolve(
        {
            "other": "class X:\n    def run(self): ...\n",
            M: "def go(self):\n    self.run()\n",
        }
    )
    assert edge(r, f"{M}.go", "other.X.run").resolution == "ambiguous"


# ---------------------------------------------------------------------------
# attribute chains and type heuristics (spec 9.4)
# ---------------------------------------------------------------------------


def test_call_through_an_imported_module() -> None:
    r = resolve(
        {
            "pkg.helpers": "def fmt(): ...\n",
            M: "from pkg import helpers\ndef go():\n    helpers.fmt()\n",
        }
    )
    e = edge(r, f"{M}.go", "pkg.helpers.fmt")
    assert (e.confidence, e.resolution, e.via) == (1.0, "exact", "attribute")


def test_call_through_a_module_alias() -> None:
    r = resolve(
        {
            "pkg.helpers": "def fmt(): ...\n",
            M: "import pkg.helpers as h\ndef go():\n    h.fmt()\n",
        }
    )
    assert ((f"{M}.go"), "pkg.helpers.fmt") in call_edges(r)


def test_dotted_call_through_a_plain_import() -> None:
    r = resolve(
        {
            "pkg.helpers": "def fmt(): ...\n",
            M: "import pkg.helpers\ndef go():\n    pkg.helpers.fmt()\n",
        }
    )
    assert ((f"{M}.go"), "pkg.helpers.fmt") in call_edges(r)


def test_classmethod_call_on_an_imported_class() -> None:
    r = resolve(
        {
            "pkg.models": "class User:\n    @classmethod\n    def from_row(cls): ...\n",
            M: "from pkg.models import User\ndef go():\n    User.from_row()\n",
        }
    )
    assert edge(r, f"{M}.go", "pkg.models.User.from_row").confidence == 1.0


def test_classmethod_call_on_a_class_in_the_same_module() -> None:
    r = resolve({M: "class U:\n    @staticmethod\n    def make(): ...\ndef go():\n    U.make()\n"})
    assert ((f"{M}.go"), f"{M}.U.make") in call_edges(r)


def test_method_on_a_nested_class_path() -> None:
    r = resolve(
        {
            M: "class Outer:\n    class Inner:\n        @staticmethod\n        def make(): ...\n"
            "def go():\n    Outer.Inner.make()\n"
        }
    )
    assert ((f"{M}.go"), f"{M}.Outer.Inner.make") in call_edges(r)


def test_locally_constructed_object_is_resolved_by_the_assignment_heuristic() -> None:
    r = resolve(
        {M: "class User:\n    def save(self): ...\ndef go():\n    u = User()\n    u.save()\n"}
    )
    e = edge(r, f"{M}.go", f"{M}.User.save")
    assert (e.confidence, e.resolution) == (0.7, "inferred")


def test_local_type_walks_the_base_classes() -> None:
    r = resolve(
        {
            M: """
            class Base:
                def save(self): ...
            class User(Base): ...
            def go():
                u = User()
                u.save()
            """
        }
    )
    e = edge(r, f"{M}.go", f"{M}.Base.save")
    assert e.confidence == 0.7  # min(local-type 0.7, inherited 0.95)


def test_variable_assigned_two_different_classes_is_dropped_from_the_heuristic() -> None:
    """One wrong high-confidence edge does more damage than ten missing ones,
    so a rebound name falls back to ambiguity rather than picking a winner."""
    r = resolve(
        {
            M: """
            class A:
                def save(self): ...
            class B:
                def save(self): ...
            def go(flag):
                x = A()
                if flag:
                    x = B()
                x.save()
            """
        }
    )
    resolutions = {e.resolution for e in edges_from(r, f"{M}.go") if e.callee_fqn.endswith("save")}
    assert resolutions == {"ambiguous"}


def test_variable_assigned_the_same_class_twice_keeps_its_type() -> None:
    r = resolve(
        {
            M: """
            class A:
                def save(self): ...
            def go():
                x = A()
                x = A()
                x.save()
            """
        }
    )
    assert edge(r, f"{M}.go", f"{M}.A.save").resolution == "inferred"


def test_parameter_annotation_types_the_variable() -> None:
    r = resolve(
        {
            M: """
            class User:
                def save(self): ...
            class Other:
                def save(self): ...
            def go(u: User):
                u.save()
            """
        }
    )
    assert call_edges(r) == {(f"{M}.go", f"{M}.User.save")}
    e = edge(r, f"{M}.go", f"{M}.User.save")
    assert (e.confidence, e.resolution) == (0.9, "inferred")


@pytest.mark.parametrize("annotation", ["Optional[User]", "User | None", "'User'"])
def test_wrapped_annotations_still_type_the_variable(annotation: str) -> None:
    r = resolve(
        {
            M: f"""
            from typing import Optional
            class User:
                def save(self): ...
            def go(u: {annotation}):
                u.save()
            """
        }
    )
    assert edge(r, f"{M}.go", f"{M}.User.save").confidence == 0.9


def test_annotation_naming_an_imported_class() -> None:
    r = resolve(
        {
            "pkg.models": "class User:\n    def save(self): ...\n",
            M: "from pkg.models import User\ndef go(u: User):\n    u.save()\n",
        }
    )
    assert ((f"{M}.go"), "pkg.models.User.save") in call_edges(r)


def test_return_annotation_types_the_assigned_variable() -> None:
    r = resolve(
        {
            M: """
            class User:
                def save(self): ...
            def get_user() -> User: ...
            def go():
                u = get_user()
                u.save()
            """
        }
    )
    e = edge(r, f"{M}.go", f"{M}.User.save")
    # 0.7 (assigned from a call) x 0.85 (trusting a return annotation): weaker
    # evidence than `u = User()`, which scores a flat 0.7.
    assert (e.confidence, e.resolution) == (0.595, "inferred")


def test_self_attribute_type_from_constructor_call() -> None:
    r = resolve(
        {
            "pkg.db": "class Session:\n    def commit(self): ...\n",
            M: """
            from pkg.db import Session
            class Repo:
                def __init__(self):
                    self.db = Session()
                def save(self):
                    self.db.commit()
            """,
        }
    )
    e = edge(r, f"{M}.Repo.save", "pkg.db.Session.commit")
    assert (e.confidence, e.resolution, e.via) == (0.7, "inferred", "self")


def test_self_attribute_type_from_constructor_injection() -> None:
    r = resolve(
        {
            "pkg.db": "class Client:\n    def read(self): ...\n",
            M: """
            from pkg.db import Client
            class Repo:
                def __init__(self, client: Client):
                    self.client = client
                def load(self):
                    self.client.read()
            """,
        }
    )
    assert ((f"{M}.Repo.load"), "pkg.db.Client.read") in call_edges(r)


def test_module_level_object_types_later_calls() -> None:
    r = resolve(
        {
            M: """
            class Router:
                def get(self): ...
            router = Router()
            def go():
                router.get()
            """
        }
    )
    assert ((f"{M}.go"), f"{M}.Router.get") in call_edges(r)


def test_local_variable_shadows_a_module_level_object() -> None:
    r = resolve(
        {
            M: """
            class A:
                def run(self): ...
            class B:
                def run(self): ...
            thing = A()
            def go():
                thing = B()
                thing.run()
            """
        }
    )
    assert call_edges(r) == {(f"{M}.go", f"{M}.B.run")}


def test_unknown_receiver_fans_out_with_capped_confidence() -> None:
    r = resolve(
        {
            "a": "class A:\n    def save(self): ...\n",
            "b": "class B:\n    def save(self): ...\n",
            "c": "class C:\n    def save(self): ...\n",
            M: "def go(thing):\n    thing.save()\n",
        }
    )
    edges = edges_from(r, f"{M}.go")
    assert len(edges) == 3
    assert {e.resolution for e in edges} == {"ambiguous"}
    assert {e.confidence for e in edges} == {round(1 / 3, 4)}


def test_two_candidates_are_capped_at_one_half() -> None:
    r = resolve(
        {
            "a": "class A:\n    def save(self): ...\n",
            "b": "class B:\n    def save(self): ...\n",
            M: "def go(thing):\n    thing.save()\n",
        }
    )
    assert {e.confidence for e in edges_from(r, f"{M}.go")} == {0.5}


def test_a_single_ambiguous_candidate_is_still_only_half_confident() -> None:
    r = resolve(
        {
            "a": "class A:\n    def save(self): ...\n",
            M: "def go(thing):\n    thing.save()\n",
        }
    )
    e = edge(r, f"{M}.go", "a.A.save")
    assert (e.confidence, e.resolution) == (0.5, "ambiguous")


def test_attribute_fanout_above_the_cap_is_dropped() -> None:
    modules = {
        f"m{i}": f"class C{i}:\n    def run(self): ...\n" for i in range(MAX_AMBIGUOUS_FANOUT + 1)
    }
    modules[M] = "def go(thing):\n    thing.run()\n"
    r = resolve(modules)
    assert r.calls == []
    assert r.stats.dropped_too_ambiguous == 1


def test_attribute_fallback_only_considers_methods() -> None:
    """``thing.run()`` cannot be a plain module-level function called ``run``."""
    r = resolve({"a": "def run(): ...\n", M: "def go(thing):\n    thing.run()\n"})
    assert r.calls == []


@pytest.mark.parametrize(
    "source",
    [
        "import os\ndef go():\n    os.path.join('a')\n",
        "import numpy as np\ndef go():\n    np.array([])\n",
        "from requests import Session\ndef go():\n    Session().get('x')\n",
    ],
)
def test_calls_into_third_party_code_are_external(source: str) -> None:
    r = resolve({M: source})
    assert r.calls == []
    assert r.stats.unresolved == 0
    assert r.stats.dropped_external + r.stats.dropped_builtin >= 1


def test_attribute_on_a_repo_module_that_is_not_a_callable_is_unresolved() -> None:
    """The module is ours but the attribute is a constant or registry, not a
    function. Guessing by name here would be low precision, so it stops."""
    r = resolve(
        {
            "pkg.registry": "HANDLERS = {}\n",
            M: "from pkg import registry\ndef go():\n    registry.HANDLERS.get('x')\n",
        }
    )
    assert r.calls == []
    assert r.stats.unresolved == 1


# ---------------------------------------------------------------------------
# package re-exports
# ---------------------------------------------------------------------------


def test_call_through_a_package_reexport() -> None:
    r = resolve(
        {
            "pkg": "from .app import Flask, run_app\n",
            "pkg.app": "class Flask: ...\ndef run_app(): ...\n",
            "client": "from pkg import run_app\ndef go():\n    run_app()\n",
        },
        packages=["pkg"],
    )
    assert edge(r, "client.go", "pkg.app.run_app").resolution == "exact"


def test_module_call_through_a_reexported_module_attribute() -> None:
    r = resolve(
        {
            "pkg": "from . import util\n",
            "pkg.util": "def tool(): ...\n",
            "client": "import pkg\ndef go():\n    pkg.util.tool()\n",
        },
        packages=["pkg"],
    )
    assert ("client.go", "pkg.util.tool") in call_edges(r)


def test_circular_reexport_terminates() -> None:
    r = resolve(
        {
            "a": "from b import f\n",
            "b": "from a import f\n",
            "client": "from a import f\ndef go():\n    f()\n",
        }
    )
    assert r.calls == []  # and, importantly, it returned


def test_reexport_of_a_class_is_followed_for_instantiation() -> None:
    r = resolve(
        {
            "pkg": "from .app import Flask\n",
            "pkg.app": "class Flask:\n    def __init__(self): ...\n",
            "client": "from pkg import Flask\ndef go():\n    Flask()\n",
        },
        packages=["pkg"],
    )
    assert ("client.go", "pkg.app.Flask.__init__") in call_edges(r)
    assert [(i.caller_fqn, i.class_fqn) for i in r.instantiations] == [
        ("client.go", "pkg.app.Flask")
    ]


# ---------------------------------------------------------------------------
# instantiation and inheritance edges
# ---------------------------------------------------------------------------


def test_instantiation_creates_both_an_instantiates_and_a_calls_edge() -> None:
    r = resolve({M: "class C:\n    def __init__(self): ...\ndef go():\n    C()\n"})
    assert [(i.caller_fqn, i.class_fqn) for i in r.instantiations] == [(f"{M}.go", f"{M}.C")]
    assert call_edges(r) == {(f"{M}.go", f"{M}.C.__init__")}


def test_class_without_a_constructor_only_gets_an_instantiates_edge() -> None:
    r = resolve({M: "class C: ...\ndef go():\n    C()\n"})
    assert len(r.instantiations) == 1
    assert r.calls == []


def test_inherited_constructor_scores_the_inheritance_confidence() -> None:
    r = resolve(
        {
            M: """
            class Base:
                def __init__(self): ...
            class Child(Base): ...
            def go():
                Child()
            """
        }
    )
    assert edge(r, f"{M}.go", f"{M}.Base.__init__").confidence == 0.95
    assert r.instantiations[0].confidence == 1.0


def test_inherits_edges_preserve_declaration_order() -> None:
    r = resolve({M: "class A: ...\nclass B: ...\nclass C(B, A): ...\n"})
    got = sorted((e.child_fqn, e.parent_fqn, e.position) for e in r.inherits)
    assert got == [(f"{M}.C", f"{M}.A", 1), (f"{M}.C", f"{M}.B", 0)]


def test_self_referential_base_is_ignored() -> None:
    r = resolve({M: "class A(A): ...\n"})
    assert r.inherits == []


# ---------------------------------------------------------------------------
# import edges
# ---------------------------------------------------------------------------


def test_import_edges_only_cover_modules_inside_the_repo() -> None:
    r = resolve({"pkg.a": "import os\nimport pkg.b\n", "pkg.b": "x = 1\n"})
    assert [(e.source_fqn, e.target_fqn) for e in r.imports] == [("pkg.a", "pkg.b")]


def test_from_import_of_a_module_targets_the_module_not_the_package() -> None:
    r = resolve(
        {"pkg": "x = 1\n", "pkg.helpers": "x = 1\n", M: "from pkg import helpers\n"},
        packages=["pkg"],
    )
    assert [(e.source_fqn, e.target_fqn) for e in r.imports] == [(M, "pkg.helpers")]


def test_from_import_of_a_symbol_targets_its_module() -> None:
    r = resolve({"pkg.models": "class User: ...\n", M: "from pkg.models import User\n"})
    assert [(e.source_fqn, e.target_fqn) for e in r.imports] == [(M, "pkg.models")]


def test_type_only_and_deferred_flags_reach_the_edge() -> None:
    r = resolve(
        {
            "a": "x = 1\n",
            "b": "y = 1\n",
            M: """
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                from a import x
            def f():
                import b
            """,
        }
    )
    flags = {e.target_fqn: (e.is_type_only, e.is_deferred) for e in r.imports}
    assert flags == {"a": (True, False), "b": (False, True)}


def test_module_never_imports_itself() -> None:
    r = resolve({"pkg.a": "import pkg.a\n"})
    assert r.imports == []


def test_one_statement_importing_two_names_makes_one_edge() -> None:
    r = resolve({"pkg.m": "class A: ...\nclass B: ...\n", M: "from pkg.m import A, B\n"})
    assert len(r.imports) == 1


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


def test_every_call_site_is_counted_exactly_once() -> None:
    """The invariant the README table depends on: the categories partition the
    call sites, whatever mix of resolutions a repo produces."""
    r = resolve(
        {
            "a": "class A:\n    def run(self): ...\n",
            "b": "class B:\n    def run(self): ...\n",
            "helpers": "def fmt(): ...\n",
            M: """
            import os
            from helpers import fmt
            from helpers import *
            class C(LibBase):
                def m(self):
                    self.nope()
                    super().m()
            def go(thing, u: A):
                fmt()
                len([])
                thing.run()
                u.run()
                os.getcwd()
                mystery()
                x = A()
                x.run()
            """,
        }
    )
    s = r.stats
    accounted = (
        s.resolved_exact
        + s.resolved_inferred
        + s.resolved_ambiguous
        + s.dropped_builtin
        + s.dropped_external
        + s.dropped_too_ambiguous
        + s.unresolved
    )
    assert accounted == s.call_sites_total
    assert s.call_sites_total > 0


def test_stats_rates() -> None:
    r = resolve({M: "def a(): ...\ndef go():\n    a()\n    len([])\n    mystery()\n"})
    s = r.stats
    assert s.call_sites_total == 3
    assert s.internal_call_sites == 1  # builtin and external excluded
    assert s.exact_rate == pytest.approx(1 / 3)
    assert s.internal_exact_rate == 1.0
    assert s.internal_resolution_rate == 1.0
    assert s.resolved_total == 1


def test_stats_on_an_empty_repo_do_not_divide_by_zero() -> None:
    d = resolve({M: "x = 1\n"}).stats.to_dict()
    assert d["exact_rate"] == d["resolution_rate"] == 0.0
    assert d["internal_exact_rate"] == d["internal_resolution_rate"] == 0.0


def test_stats_to_dict_has_every_published_key() -> None:
    keys = set(resolve({M: "x = 1\n"}).stats.to_dict())
    assert {
        "call_sites_total",
        "resolved_exact",
        "resolved_inferred",
        "resolved_ambiguous",
        "dropped_builtin",
        "dropped_external",
        "dropped_too_ambiguous",
        "unresolved",
        "exact_rate",
    } <= keys


# ---------------------------------------------------------------------------
# closures and scoping (found by running on Flask)
# ---------------------------------------------------------------------------


def test_closure_sees_a_variable_typed_in_the_enclosing_function() -> None:
    """The Flask app-factory pattern: ``app`` is a local of ``create_app`` and is
    used from a nested view function."""
    r = resolve(
        {
            M: """
            class App:
                def route(self): ...
                def run(self): ...
            def create_app():
                app = App()
                @app.route()
                def index():
                    app.run()
            """
        }
    )
    body = edge(r, f"{M}.create_app.<locals>.index", f"{M}.App.run")
    assert (body.confidence, body.resolution) == (0.7, "inferred")
    decorator = edge(r, f"{M}.create_app.<locals>.index", f"{M}.App.route")
    assert decorator.via == "decorator"


def test_variable_lookup_walks_outward_through_several_closures() -> None:
    r = resolve(
        {
            M: """
            class C:
                def go(self): ...
            def outer():
                c = C()
                def middle():
                    def inner():
                        c.go()
            """
        }
    )
    assert ((f"{M}.outer.<locals>.middle.<locals>.inner"), f"{M}.C.go") in call_edges(r)


def test_an_inner_assignment_shadows_the_outer_variable() -> None:
    r = resolve(
        {
            M: """
            class A:
                def run(self): ...
            class B:
                def run(self): ...
            def outer():
                thing = A()
                def inner():
                    thing = B()
                    thing.run()
            """
        }
    )
    assert call_edges(r) == {(f"{M}.outer.<locals>.inner", f"{M}.B.run")}


def test_self_attribute_never_borrows_an_unrelated_local_of_the_same_name() -> None:
    """``self.db`` and a local ``db`` are different variables. The local's type
    must not be used to resolve ``self.db.commit()``."""
    r = resolve(
        {
            "a": "class Session:\n    def commit(self): ...\n",
            "b": "class Other:\n    def commit(self): ...\n",
            M: """
            from a import Session
            class C:
                def go(self):
                    db = Session()
                    self.db.commit()
            """,
        }
    )
    on_self = [e for e in edges_from(r, f"{M}.C.go") if e.via == "self"]
    assert {e.resolution for e in on_self} == {"ambiguous"}  # not "inferred" via the local


# ---------------------------------------------------------------------------
# star-import re-exports (found by running on httpx)
# ---------------------------------------------------------------------------


def test_call_through_a_star_import_reexport() -> None:
    """``httpx/__init__.py`` publishes its API with ``from ._models import *``."""
    r = resolve(
        {
            "pkg": "from ._models import *\n",
            "pkg._models": "class Response: ...\ndef helper(): ...\n",
            "client": "import pkg\ndef go():\n    pkg.Response()\n    pkg.helper()\n",
        },
        packages=["pkg"],
    )
    assert ("client.go", "pkg._models.helper") in call_edges(r)
    assert [(i.caller_fqn, i.class_fqn) for i in r.instantiations] == [
        ("client.go", "pkg._models.Response")
    ]


def test_star_reexport_chains_through_intermediate_modules() -> None:
    r = resolve(
        {
            "pkg": "from ._api import *\n",
            "pkg._api": "from ._models import Response\n",
            "pkg._models": "class Response:\n    def __init__(self): ...\n",
            "client": "from pkg import Response\ndef go():\n    Response()\n",
        },
        packages=["pkg"],
    )
    assert ("client.go", "pkg._models.Response.__init__") in call_edges(r)


def test_explicit_binding_wins_over_a_star_reexport() -> None:
    r = resolve(
        {
            "pkg": "from ._a import *\nfrom ._b import thing\n",
            "pkg._a": "def thing(): ...\n",
            "pkg._b": "def thing(): ...\n",
            "client": "import pkg\ndef go():\n    pkg.thing()\n",
        },
        packages=["pkg"],
    )
    assert call_edges(r) == {("client.go", "pkg._b.thing")}


# ---------------------------------------------------------------------------
# precision work driven by the accuracy harness (Flask sample)
# ---------------------------------------------------------------------------


def test_class_body_alias_resolves_a_pluggable_class() -> None:
    """Flask's ``session_class = SecureCookieSession`` / ``url_rule_class = Rule``."""
    r = resolve(
        {
            M: """
            class Session:
                def __init__(self, data): ...
            class Interface:
                session_class = Session
                def load(self, data):
                    return self.session_class(data)
            """
        }
    )
    assert [(i.caller_fqn, i.class_fqn) for i in r.instantiations] == [
        (f"{M}.Interface.load", f"{M}.Session")
    ]
    e = edge(r, f"{M}.Interface.load", f"{M}.Session.__init__")
    assert (e.confidence, e.resolution) == (0.9, "inferred")  # a subclass may override it


def test_alias_declared_on_a_base_class_is_found_from_a_subclass() -> None:
    r = resolve(
        {
            M: """
            class Plugin: ...
            class Base:
                plugin_class = Plugin
            class Child(Base):
                def make(self):
                    return self.plugin_class()
            """
        }
    )
    assert [i.class_fqn for i in r.instantiations] == [f"{M}.Plugin"]


def test_alias_can_name_a_class_in_another_module() -> None:
    r = resolve(
        {
            "pkg.plug": "class Plugin: ...\n",
            M: "from pkg.plug import Plugin\nclass Host:\n    kind = Plugin\n    def go(self):\n        self.kind()\n",
        }
    )
    assert [i.class_fqn for i in r.instantiations] == ["pkg.plug.Plugin"]


def test_alias_to_a_function_resolves_to_the_function() -> None:
    r = resolve(
        {M: "def impl(): ...\nclass C:\n    hook = impl\n    def go(self):\n        self.hook()\n"}
    )
    assert edge(r, f"{M}.C.go", f"{M}.impl").resolution == "inferred"


def test_alias_to_third_party_code_stays_unresolved() -> None:
    r = resolve(
        {
            M: "from werkzeug.routing import Rule\nclass App:\n    rule_class = Rule\n    def go(self):\n        self.rule_class('/')\n"
        }
    )
    assert r.calls == [] and r.instantiations == []
    assert r.stats.dropped_external == 1


def test_a_real_method_beats_an_alias_of_the_same_name() -> None:
    r = resolve(
        {M: "class C:\n    x = 1\n    def a(self): ...\n    def go(self):\n        self.a()\n"}
    )
    assert call_edges(r) == {(f"{M}.C.go", f"{M}.C.a")}


def test_self_dunder_class_instantiates_the_owning_class() -> None:
    r = resolve({M: "class C:\n    def copy(self):\n        return self.__class__()\n"})
    assert [(i.caller_fqn, i.class_fqn) for i in r.instantiations] == [(f"{M}.C.copy", f"{M}.C")]


def test_a_class_defined_in_a_function_is_invisible_elsewhere() -> None:
    """A test's local ``Wrapper`` cannot be the receiver in library code."""
    r = resolve(
        {
            "test_mod": "def test_x():\n    class Wrapper:\n        def close(self): ...\n",
            M: "def go(thing):\n    thing.close()\n",
        }
    )
    assert r.calls == []


def test_a_local_class_is_visible_inside_its_own_function() -> None:
    r = resolve(
        {
            M: """
            def outer(thing):
                class Local:
                    def run(self): ...
                thing.run()
            """
        }
    )
    assert ((f"{M}.outer"), f"{M}.outer.<locals>.Local.run") in call_edges(r)


def test_production_code_never_resolves_into_test_modules() -> None:
    facts_test = parse(
        "class Fake:\n    def frobnicate(self): ...\n", "tests.helpers", is_test=True
    )
    facts_lib = parse("def go(thing):\n    thing.frobnicate()\n", "pkg.lib")
    from faultline.parsing.resolver import resolve_repository

    assert resolve_repository([facts_test, facts_lib]).calls == []


def test_test_code_may_still_call_test_helpers() -> None:
    facts_helper = parse(
        "class Fake:\n    def frobnicate(self): ...\n", "tests.helpers", is_test=True
    )
    facts_test = parse("def test_x(thing):\n    thing.frobnicate()\n", "tests.test_a", is_test=True)
    from faultline.parsing.resolver import resolve_repository

    assert len(resolve_repository([facts_helper, facts_test]).calls) == 1


@pytest.mark.parametrize("method", ["get", "pop", "setdefault", "loads", "close"])
def test_common_builtin_method_names_are_discounted_when_the_receiver_is_unknown(
    method: str,
) -> None:
    """``options.pop()`` is far more likely a dict than a repo class that happens
    to define ``pop``. The edge is kept, but below the default 0.5 threshold."""
    r = resolve(
        {
            "a": f"class A:\n    def {method}(self): ...\n",
            M: f"def go(thing):\n    thing.{method}()\n",
        }
    )
    e = edge(r, f"{M}.go", f"a.A.{method}")
    assert e.resolution == "ambiguous"
    assert e.confidence == 0.5 * 0.5  # a single candidate: 0.5, halved


def test_an_uncommon_method_name_keeps_the_full_ambiguous_confidence() -> None:
    r = resolve(
        {"a": "class A:\n    def frobnicate(self): ...\n", M: "def go(t):\n    t.frobnicate()\n"}
    )
    assert edge(r, f"{M}.go", "a.A.frobnicate").confidence == 0.5


def test_the_discount_never_touches_a_typed_receiver() -> None:
    r = resolve(
        {
            M: """
            class A:
                def get(self): ...
            def go(a: A):
                a.get()
            """
        }
    )
    assert edge(r, f"{M}.go", f"{M}.A.get").confidence == 0.9


@pytest.mark.parametrize("base", ["object", "dict", "str", "list", "type"])
def test_attribute_call_on_a_builtin_type_never_targets_the_repo(base: str) -> None:
    r = resolve(
        {
            "a": "class A:\n    def __repr__(self): ...\n    def fromkeys(self): ...\n",
            M: f"def go(x):\n    {base}.__repr__(x)\n    {base}.fromkeys(x)\n",
        }
    )
    assert r.calls == []
    assert r.stats.dropped_builtin == 2


def test_a_module_that_rebinds_a_builtin_type_name_is_respected() -> None:
    r = resolve(
        {M: "class dict:\n    @staticmethod\n    def make(): ...\ndef go():\n    dict.make()\n"}
    )
    assert ((f"{M}.go"), f"{M}.dict.make") in call_edges(r)


def test_a_name_both_imported_and_defined_is_a_fallback_not_an_exact_hit() -> None:
    """``try: from lib import Thing`` / ``except ImportError: def Thing(...)``.

    Found on requests (``SOCKSProxyManager``): which binding runs depends on the
    environment, so the in-repo stub must not be reported as an exact target."""
    r = resolve(
        {
            M: """
            try:
                from lib import Thing
            except ImportError:
                def Thing():
                    raise RuntimeError
            def go():
                Thing()
            """
        }
    )
    e = edge(r, f"{M}.go", f"{M}.Thing")
    assert (e.confidence, e.resolution) == (0.5, "ambiguous")


def test_a_fallback_over_an_in_repo_import_offers_both_targets() -> None:
    r = resolve(
        {
            "fast": "def impl(): ...\n",
            M: """
            try:
                from fast import impl
            except ImportError:
                def impl(): ...
            def go():
                impl()
            """,
        }
    )
    assert {e.callee_fqn for e in edges_from(r, f"{M}.go")} == {"fast.impl", f"{M}.impl"}
    assert {e.confidence for e in edges_from(r, f"{M}.go")} == {0.5}


def test_a_plain_definition_without_an_import_is_still_exact() -> None:
    r = resolve({M: "def helper(): ...\ndef go():\n    helper()\n"})
    assert edge(r, f"{M}.go", f"{M}.helper").resolution == "exact"
