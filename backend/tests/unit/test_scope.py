from __future__ import annotations

import pytest

from faultline.parsing.scope import ScopeStack


def test_module_level_fqn() -> None:
    assert ScopeStack("pkg.mod").current_fqn() == "pkg.mod"


def test_method_fqn_includes_class() -> None:
    stack = ScopeStack("pkg.mod")
    stack.push("class", "User")
    stack.push("function", "save")
    assert stack.current_fqn() == "pkg.mod.User.save"


def test_nested_function_uses_locals_marker() -> None:
    stack = ScopeStack("pkg.mod")
    stack.push("function", "outer")
    stack.push("function", "inner")
    assert stack.current_fqn() == "pkg.mod.outer.<locals>.inner"


def test_class_inside_function_uses_locals_marker() -> None:
    stack = ScopeStack("pkg.mod")
    stack.push("function", "factory")
    stack.push("class", "Local")
    assert stack.current_fqn() == "pkg.mod.factory.<locals>.Local"


def test_child_fqn_does_not_mutate_stack() -> None:
    stack = ScopeStack("pkg.mod")
    assert stack.child_fqn("function", "f") == "pkg.mod.f"
    assert stack.current_fqn() == "pkg.mod"
    assert len(stack.frames) == 1


def test_enclosing_function_fqn() -> None:
    stack = ScopeStack("m")
    assert stack.enclosing_function_fqn() is None
    stack.push("class", "C")
    assert stack.enclosing_function_fqn() is None  # class body has no caller
    stack.push("function", "meth")
    stack.push("function", "inner")
    assert stack.enclosing_function_fqn() == "m.C.meth.<locals>.inner"


def test_owning_class_only_for_direct_children() -> None:
    stack = ScopeStack("m")
    stack.push("class", "C")
    assert stack.owning_class_fqn() == "m.C"
    stack.push("function", "meth")
    # Inside the method body, a nested def is NOT a method.
    assert stack.owning_class_fqn() is None


def test_enclosing_class_sees_through_closures() -> None:
    stack = ScopeStack("m")
    stack.push("class", "C")
    stack.push("function", "meth")
    stack.push("function", "closure")
    assert stack.enclosing_class_fqn() == "m.C"


def test_enclosing_class_none_at_module_level() -> None:
    assert ScopeStack("m").enclosing_class_fqn() is None


def test_scope_predicates() -> None:
    stack = ScopeStack("m")
    assert stack.in_module_scope()
    stack.push("class", "C")
    assert stack.in_class_body()
    assert not stack.in_module_scope()


def test_cannot_pop_module_frame() -> None:
    with pytest.raises(RuntimeError):
        ScopeStack("m").pop()
