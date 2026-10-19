"""Import handling. The relative-import cases are the most bug-prone corner of
the whole project, so each is pinned with its own test."""

from __future__ import annotations

import pytest
from helpers import parse

from faultline.parsing.imports import build_import_table, resolve_relative

# -- resolve_relative -----------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "is_package", "level", "module", "expected"),
    [
        # inside a plain module, one dot is the containing package
        ("pkg.mod", False, 1, None, "pkg"),
        ("pkg.mod", False, 1, "helpers", "pkg.helpers"),
        ("pkg.sub.mod", False, 2, "other", "pkg.other"),
        ("pkg.sub.mod", False, 2, None, "pkg"),
        # inside an __init__.py, one dot is the package itself, NOT its parent
        ("pkg", True, 1, None, "pkg"),
        ("pkg", True, 1, "helpers", "pkg.helpers"),
        ("pkg.sub", True, 1, "x", "pkg.sub.x"),
        ("pkg.sub", True, 2, "x", "pkg.x"),
        # absolute import: level 0 passes the module through untouched
        ("pkg.mod", False, 0, "os.path", "os.path"),
    ],
)
def test_resolve_relative(
    current: str, is_package: bool, level: int, module: str | None, expected: str
) -> None:
    assert resolve_relative(current, is_package=is_package, level=level, module=module) == expected


@pytest.mark.parametrize(
    ("current", "is_package", "level"),
    [("mod", False, 1), ("pkg.mod", False, 3), ("pkg", True, 2)],
)
def test_relative_import_above_top_level_is_none(
    current: str, is_package: bool, level: int
) -> None:
    """Climbing above the top-level package is an ImportError at runtime."""
    assert resolve_relative(current, is_package=is_package, level=level, module="x") is None


# -- the two cases the spec calls out by name -----------------------------------


def test_relative_import_from_package_init() -> None:
    facts = parse("from . import helpers\n", module_fqn="pkg", is_package=True)
    assert facts.imports[0].module_fqn == "pkg"
    assert facts.imports[0].target_fqn == "pkg.helpers"


def test_relative_import_from_module() -> None:
    facts = parse("from . import helpers\n", module_fqn="pkg.mod", is_package=False)
    assert facts.imports[0].module_fqn == "pkg"
    assert facts.imports[0].target_fqn == "pkg.helpers"


# -- import forms ---------------------------------------------------------------


def test_plain_import_binds_top_package_only() -> None:
    """``import a.b.c`` binds the name ``a``; the dependency is ``a.b.c``."""
    record = parse("import a.b.c\n").imports[0]
    assert record.local_name == "a"
    assert record.target_fqn == "a"
    assert record.module_fqn == "a.b.c"
    assert record.kind == "absolute"


def test_import_alias_binds_leaf_module() -> None:
    record = parse("import a.b.c as abc\n").imports[0]
    assert record.local_name == "abc"
    assert record.target_fqn == "a.b.c"


def test_import_multiple_names_in_one_statement() -> None:
    records = parse("import os, sys\n").imports
    assert [r.local_name for r in records] == ["os", "sys"]


def test_from_import() -> None:
    record = parse("from x import y\n").imports[0]
    assert (record.module_fqn, record.local_name, record.target_fqn) == ("x", "y", "x.y")
    assert record.imported_symbol == "y"


def test_from_import_alias() -> None:
    record = parse("from x import y as z\n").imports[0]
    assert record.local_name == "z"
    assert record.target_fqn == "x.y"
    assert record.imported_symbol == "y"


def test_star_import() -> None:
    facts = parse("from x import *\n")
    record = facts.imports[0]
    assert record.kind == "star"
    assert record.module_fqn == "x"
    assert facts.star_import_targets == ["x"]
    # A star import binds unknown names, so it must not pollute module globals.
    assert "*" not in facts.module_globals


def test_relative_from_import_kinds() -> None:
    facts = parse(
        """
        from . import a
        from .. import b
        from .mod import c
        """,
        module_fqn="pkg.sub.mod2",
    )
    assert [(r.module_fqn, r.target_fqn, r.kind) for r in facts.imports] == [
        ("pkg.sub", "pkg.sub.a", "relative"),
        ("pkg", "pkg.b", "relative"),
        ("pkg.sub.mod", "pkg.sub.mod.c", "relative"),
    ]


def test_relative_import_above_top_level_is_skipped() -> None:
    assert parse("from ... import x\n", module_fqn="mod").imports == []


# -- conditional imports --------------------------------------------------------


def test_type_checking_import_is_type_only() -> None:
    facts = parse(
        """
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            from heavy import Thing
        """
    )
    by_name = {r.local_name: r for r in facts.imports}
    assert by_name["Thing"].is_type_only is True
    assert by_name["TYPE_CHECKING"].is_type_only is False


def test_attribute_style_type_checking_guard() -> None:
    facts = parse(
        """
        import typing

        if typing.TYPE_CHECKING:
            from heavy import Thing
        """
    )
    assert {r.local_name: r.is_type_only for r in facts.imports}["Thing"] is True


def test_else_branch_of_type_checking_is_runtime() -> None:
    facts = parse(
        """
        if TYPE_CHECKING:
            from a import X
        else:
            from b import X
        """
    )
    assert [(r.module_fqn, r.is_type_only) for r in facts.imports] == [("a", True), ("b", False)]


def test_try_except_import_error_records_both_branches() -> None:
    facts = parse(
        """
        try:
            import ujson as json
        except ImportError:
            import json
        """
    )
    assert [r.module_fqn for r in facts.imports] == ["ujson", "json"]
    assert not any(r.is_type_only for r in facts.imports)


def test_function_local_import_is_deferred() -> None:
    facts = parse(
        """
        def f():
            import heavy
            return heavy.go()
        """
    )
    record = facts.imports[0]
    assert record.is_deferred is True
    # A deferred import does not bind a module-level name.
    assert "heavy" not in facts.module_globals


def test_module_level_import_is_not_deferred() -> None:
    assert parse("import os\n").imports[0].is_deferred is False


# -- import table ---------------------------------------------------------------


def test_import_table_last_binding_wins_and_skips_star() -> None:
    facts = parse(
        """
        from a import thing
        from b import thing
        from c import *
        """
    )
    table = build_import_table({"pkg.mod": facts.imports})
    assert table["pkg.mod"]["thing"].module_fqn == "b"
    assert "*" not in table["pkg.mod"]
