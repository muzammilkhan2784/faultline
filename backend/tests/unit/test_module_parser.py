from __future__ import annotations

from pathlib import Path

import pytest
from helpers import parse

from faultline.models import ModuleFacts
from faultline.parsing.discovery import DiscoveredFile
from faultline.parsing.module_parser import (
    annotation_to_type_expr,
    flatten_callee,
    is_entrypoint_decorator,
    parse_file,
)


def fn(facts: ModuleFacts, name: str):
    return next(f for f in facts.functions if f.name == name)


def calls(facts: ModuleFacts) -> list[tuple[str, str, list[str], str]]:
    return [(c.caller_fqn, c.kind, c.parts, c.via) for c in facts.calls]


# -- definitions and FQNs ---------------------------------------------------------


def test_method_fqn_includes_class() -> None:
    facts = parse("class User:\n    def save(self): ...\n")
    method = fn(facts, "save")
    assert method.fqn == "pkg.mod.User.save"
    assert method.class_fqn == "pkg.mod.User"
    assert method.is_method is True


def test_nested_function_fqn_uses_locals() -> None:
    facts = parse("def outer():\n    def inner(): ...\n")
    inner = fn(facts, "inner")
    assert inner.fqn == "pkg.mod.outer.<locals>.inner"
    assert inner.is_method is False
    assert inner.class_fqn is None


def test_function_nested_in_method_is_not_a_method() -> None:
    facts = parse("class C:\n    def m(self):\n        def helper(): ...\n")
    helper = fn(facts, "helper")
    assert helper.fqn == "pkg.mod.C.m.<locals>.helper"
    assert helper.is_method is False


def test_nested_class_method_fqn() -> None:
    facts = parse("class Outer:\n    class Inner:\n        def m(self): ...\n")
    assert fn(facts, "m").fqn == "pkg.mod.Outer.Inner.m"
    assert fn(facts, "m").class_fqn == "pkg.mod.Outer.Inner"
    assert {c.fqn for c in facts.classes} == {"pkg.mod.Outer", "pkg.mod.Outer.Inner"}


def test_class_defined_inside_function() -> None:
    facts = parse("def factory():\n    class Local:\n        def m(self): ...\n")
    assert fn(facts, "m").fqn == "pkg.mod.factory.<locals>.Local.m"


def test_async_function_and_flags() -> None:
    facts = parse(
        """
        async def fetch(): ...
        def _private(): ...
        def __dunder__(): ...
        """
    )
    assert fn(facts, "fetch").is_async is True
    assert fn(facts, "_private").is_private is True
    assert fn(facts, "_private").is_dunder is False
    assert fn(facts, "__dunder__").is_dunder is True
    assert fn(facts, "__dunder__").is_private is False


def test_parameter_names_and_count() -> None:
    facts = parse("def f(a, /, b, *args, c, **kw): ...\n")
    assert fn(facts, "f").param_names == ["a", "b", "args", "c", "kw"]
    assert fn(facts, "f").param_count == 5


def test_class_facts() -> None:
    facts = parse(
        """
        @dataclass
        class A(B, mod.C, metaclass=Meta):
            def one(self): ...
            async def two(self): ...
            x = 1
        """
    )
    klass = facts.classes[0]
    assert klass.base_names == ["B", "mod.C"]  # keywords are not bases
    assert klass.decorators == ["dataclass"]
    assert klass.method_count == 2


def test_return_type_captured() -> None:
    facts = parse("def a() -> User: ...\ndef b() -> None: ...\ndef c(): ...\n")
    assert fn(facts, "a").return_type == "User"
    assert fn(facts, "b").return_type is None
    assert fn(facts, "c").return_type is None


def test_line_numbers() -> None:
    facts = parse("x = 1\n\ndef f():\n    return 1\n")
    assert (fn(facts, "f").line_start, fn(facts, "f").line_end) == (3, 4)


def test_module_globals() -> None:
    facts = parse(
        """
        import os
        from a import b
        CONST = 1
        def f():
            local = 2
        class C: ...
        """
    )
    assert facts.module_globals == {"os", "b", "CONST", "f", "C"}


# -- decorators -------------------------------------------------------------------


def test_decorator_produces_a_call_edge_from_the_decorated_function() -> None:
    facts = parse("@login_required\ndef view(): ...\n")
    assert calls(facts) == [("pkg.mod.view", "name", ["login_required"], "decorator")]


def test_decorator_with_arguments_uses_the_callee() -> None:
    facts = parse('@app.route("/x")\ndef view(): ...\n')
    assert calls(facts) == [("pkg.mod.view", "attr", ["app", "route"], "decorator")]


def test_decorator_arguments_are_evaluated_in_the_enclosing_scope() -> None:
    """``make_key()`` runs where the decorator is applied, not inside ``view``."""
    facts = parse(
        """
        def outer():
            @register(make_key())
            def view(): ...
        """
    )
    by_callee = {c.parts[0]: c.caller_fqn for c in facts.calls}
    assert by_callee["register"] == "pkg.mod.outer.<locals>.view"  # the decorator dependency
    assert by_callee["make_key"] == "pkg.mod.outer"  # evaluated in the enclosing function


def test_decorator_arguments_at_module_level_have_no_caller() -> None:
    facts = parse("@register(make_key())\ndef view(): ...\n")
    assert [c.parts[0] for c in facts.calls] == ["register"]


def test_stacked_decorators_each_produce_an_edge() -> None:
    facts = parse("@a\n@b.c\ndef view(): ...\n")
    assert [c.parts for c in facts.calls] == [["a"], ["b", "c"]]


# -- call shapes ------------------------------------------------------------------


def test_call_shapes_and_via() -> None:
    facts = parse(
        """
        class C:
            def m(self):
                foo()
                a.b.c()
                self.x()
                self.db.commit()
                cls.y()
                super().z()
        """
    )
    assert calls(facts) == [
        ("pkg.mod.C.m", "name", ["foo"], "name"),
        ("pkg.mod.C.m", "attr", ["a", "b", "c"], "attribute"),
        ("pkg.mod.C.m", "self", ["x"], "self"),
        ("pkg.mod.C.m", "self", ["db", "commit"], "self"),
        ("pkg.mod.C.m", "self", ["y"], "self"),
        ("pkg.mod.C.m", "super", ["z"], "self"),
    ]


def test_bare_super_call_is_not_recorded() -> None:
    facts = parse("class C:\n    def m(self):\n        return super().m()\n")
    assert [c.kind for c in facts.calls] == ["super"]


@pytest.mark.parametrize(
    "expr", ["f()()", "d['k']()", "(a or b).c()", "f().g()", "[1].append(2)", "'s'.join(x)"]
)
def test_unresolvable_callee_shapes_produce_no_edge(expr: str) -> None:
    """A base that is not a plain name cannot be resolved statically."""
    facts = parse(f"def f():\n    {expr}\n")
    outer = [c for c in facts.calls if c.parts and c.parts[-1] in {"c", "g", "append", "join", "k"}]
    assert outer == []


def test_nested_calls_are_both_recorded() -> None:
    facts = parse("def f():\n    outer(inner(x))\n")
    assert [c.parts for c in facts.calls] == [["outer"], ["inner"]]


def test_call_inside_a_call_result_chain_is_still_seen() -> None:
    facts = parse("def f():\n    make().go()\n")
    assert [c.parts for c in facts.calls] == [["make"]]


def test_module_level_and_class_body_calls_produce_no_edge() -> None:
    facts = parse("x = setup()\nclass C:\n    y = build()\n")
    assert facts.calls == []


def test_default_argument_call_belongs_to_the_enclosing_scope() -> None:
    facts = parse("def outer():\n    def inner(x=make()): ...\n")
    assert [(c.caller_fqn, c.parts) for c in facts.calls] == [("pkg.mod.outer", ["make"])]


def test_call_line_numbers() -> None:
    facts = parse("def f():\n    a()\n\n    b()\n")
    assert [c.line for c in facts.calls] == [2, 4]


def test_call_inside_nested_function_is_attributed_to_it() -> None:
    facts = parse("def outer():\n    def inner():\n        go()\n")
    assert facts.calls[0].caller_fqn == "pkg.mod.outer.<locals>.inner"


# -- local type facts -------------------------------------------------------------


def types(facts: ModuleFacts) -> list[tuple[str, str, str, str]]:
    return [(a.scope_fqn, a.var_name, a.class_expr, a.source) for a in facts.assignments]


def test_local_assignment_from_constructor_call() -> None:
    facts = parse("def f():\n    u = User()\n    m = models.Order()\n")
    assert types(facts) == [
        ("pkg.mod.f", "u", "User", "assignment"),
        ("pkg.mod.f", "m", "models.Order", "assignment"),
    ]


def test_non_call_assignments_carry_no_type() -> None:
    facts = parse("def f(p):\n    a = 1\n    b = p\n    c = [1]\n")
    assert facts.assignments == []


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        ("User", "User"),
        ("models.User", "models.User"),
        ("Optional[User]", "User"),
        ("typing.Optional[User]", "User"),
        ("User | None", "User"),
        ("None | User", "User"),
        ("'User'", "User"),
        ("Optional['User']", "User"),
        ("list[User]", None),
        ("dict[str, User]", None),
        ("int | str", "int"),
        ("'not valid python'", None),
    ],
)
def test_annotation_to_type_expr(annotation: str, expected: str | None) -> None:
    import ast

    node = ast.parse(f"x: {annotation}").body[0].annotation  # type: ignore[attr-defined]
    assert annotation_to_type_expr(node) == expected


def test_parameter_annotation_becomes_a_type_fact() -> None:
    facts = parse("def f(u: User, n: int = 0, self_like: 'Order' = None): ...\n")
    got = {(a.var_name, a.class_expr) for a in facts.assignments}
    assert ("u", "User") in got
    assert ("self_like", "Order") in got
    assert all(a.source == "annotation" for a in facts.assignments)


def test_self_parameter_is_never_typed() -> None:
    facts = parse("class C:\n    def m(self: 'C'): ...\n")
    assert facts.assignments == []


def test_annotated_local_variable() -> None:
    facts = parse("def f():\n    u: User = load()\n")
    assert ("pkg.mod.f", "u", "User", "annotation") in types(facts)


def test_self_attribute_assignment_is_typed_on_the_class() -> None:
    facts = parse("class Repo:\n    def __init__(self):\n        self.db = Session()\n")
    assert types(facts) == [("pkg.mod.Repo", "db", "Session", "attribute")]


def test_constructor_injection_inherits_the_parameter_annotation() -> None:
    facts = parse(
        """
        class Repo:
            def __init__(self, client: GraphClient):
                self.client = client
        """
    )
    assert ("pkg.mod.Repo", "client", "GraphClient", "attribute") in types(facts)


def test_module_level_assignment_is_typed_on_the_module() -> None:
    facts = parse("router = APIRouter()\n")
    assert types(facts) == [("pkg.mod", "router", "APIRouter", "assignment")]


def test_walrus_assignment_is_typed() -> None:
    facts = parse("def f():\n    if (u := User()):\n        pass\n")
    assert ("pkg.mod.f", "u", "User", "assignment") in types(facts)


# -- entrypoints ------------------------------------------------------------------


@pytest.mark.parametrize(
    "decorator",
    [
        "app.route('/x')",
        "router.get('/x')",
        "app.post",
        "task",
        "celery.shared_task",
        "click.command()",
        "pytest.fixture",
        "property",
        "name.setter",
        "abstractmethod",
        "bp.errorhandler(404)",
    ],
)
def test_entrypoint_decorators(decorator: str) -> None:
    assert is_entrypoint_decorator(decorator)


@pytest.mark.parametrize(
    "decorator", ["cache.get", "lru_cache", "staticmethod", "functools.wraps(f)"]
)
def test_non_entrypoint_decorators(decorator: str) -> None:
    assert not is_entrypoint_decorator(decorator)


def test_decorated_view_is_an_entrypoint() -> None:
    facts = parse("@app.route('/')\ndef index(): ...\ndef helper(): ...\n")
    assert fn(facts, "index").is_entrypoint is True
    assert fn(facts, "helper").is_entrypoint is False


def test_main_in_a_module_with_main_guard_is_an_entrypoint() -> None:
    facts = parse("def main(): ...\ndef helper(): ...\nif __name__ == '__main__':\n    main()\n")
    assert facts.has_main_guard is True
    assert fn(facts, "main").is_entrypoint is True
    assert fn(facts, "helper").is_entrypoint is False


def test_main_without_a_guard_is_not_an_entrypoint() -> None:
    assert fn(parse("def main(): ...\n"), "main").is_entrypoint is False


def test_names_in_dunder_all_are_entrypoints() -> None:
    facts = parse("__all__ = ['public']\ndef public(): ...\ndef hidden(): ...\n")
    assert facts.dunder_all == ["public"]
    assert fn(facts, "public").is_entrypoint is True
    assert fn(facts, "hidden").is_entrypoint is False


def test_dunder_all_as_tuple() -> None:
    assert parse("__all__ = ('a', 'b')\n").dunder_all == ["a", "b"]


def test_dunder_methods_are_entrypoints() -> None:
    facts = parse("class C:\n    def __init__(self): ...\n")
    assert fn(facts, "__init__").is_entrypoint is True


# -- test functions ---------------------------------------------------------------


def test_is_test_needs_both_a_test_module_and_a_test_name() -> None:
    src = "def test_a(): ...\ndef helper(): ...\n"
    in_test_module = parse(src, is_test=True)
    assert fn(in_test_module, "test_a").is_test is True
    assert fn(in_test_module, "helper").is_test is False
    assert fn(parse(src, is_test=False), "test_a").is_test is False


# -- robustness -------------------------------------------------------------------


def test_syntax_error_is_recorded_not_raised() -> None:
    facts = parse("def broken(:\n    pass\n")
    assert facts.parse_error is not None
    assert facts.parse_error.startswith("SyntaxError")
    assert facts.functions == []


def test_null_bytes_are_recorded_not_raised() -> None:
    facts = parse("x = 1\x00\n")
    assert facts.parse_error is not None


def test_loc_is_counted_even_when_parsing_fails() -> None:
    assert parse("def broken(:\n    pass\n").loc == 2


def test_empty_module() -> None:
    facts = parse("")
    assert facts.parse_error is None
    assert (facts.functions, facts.classes, facts.calls) == ([], [], [])


def test_flatten_callee_direct() -> None:
    import ast

    def flat(src: str):
        return flatten_callee(ast.parse(src, mode="eval").body.func)  # type: ignore[attr-defined]

    assert flat("f()") == ("name", ["f"])
    assert flat("a.b()") == ("attr", ["a", "b"])
    assert flat("self.a.b()") == ("self", ["a", "b"])
    assert flat("super().a()") == ("super", ["a"])
    assert flat("f().a()") is None


def test_parse_file_reads_and_tolerates_bad_bytes(tmp_path: Path) -> None:
    path = tmp_path / "mod.py"
    path.write_bytes(b'def f():\n    return "\xff\xfe"\n')
    facts = parse_file(
        DiscoveredFile(
            path=path, rel_path="mod.py", module_fqn="mod", is_package=False, is_test=False
        )
    )
    assert facts.parse_error is None
    assert [f.name for f in facts.functions] == ["f"]


# -- class-body aliases (the pluggable-class idiom) ---------------------------------


def test_class_body_alias_is_recorded() -> None:
    facts = parse(
        """
        class App:
            rule_class = Rule
            session_class: type = werkzeug.Session
            x = 1
            y = make()
            z = a.b
        """
    )
    got = [(a.class_fqn, a.name, a.expr) for a in facts.class_aliases]
    assert got == [
        ("pkg.mod.App", "rule_class", "Rule"),
        ("pkg.mod.App", "session_class", "werkzeug.Session"),
        ("pkg.mod.App", "z", "a.b"),
    ]


def test_aliases_are_only_recorded_directly_in_a_class_body() -> None:
    facts = parse(
        """
        top = Foo
        def f():
            local = Bar
        class C:
            def m(self):
                inner = Baz
        """
    )
    assert facts.class_aliases == []
