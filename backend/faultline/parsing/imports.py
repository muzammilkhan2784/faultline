"""Import statements: every form Python allows, reduced to :class:`ImportRecord`.

The relative-import rules are the single most bug-prone corner of this project,
so they live in one small pure function with tests either side of it.
"""

from __future__ import annotations

import ast

from faultline.models import ImportKind, ImportRecord

#: Names that, as the test of an ``if``, mean "this block does not run".
TYPE_CHECKING_NAMES: frozenset[str] = frozenset({"TYPE_CHECKING", "typing.TYPE_CHECKING"})


def is_type_checking_test(test: ast.expr) -> bool:
    """True for ``if TYPE_CHECKING:``, ``if typing.TYPE_CHECKING:``, ``if t.TYPE_CHECKING:``."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def resolve_relative(
    current_module_fqn: str,
    *,
    is_package: bool,
    level: int,
    module: str | None,
) -> str | None:
    """Turn a relative import into an absolute module FQN.

    ``level`` is the number of leading dots. The subtlety that catches everyone:
    inside ``pkg/__init__.py`` (FQN ``pkg``) a single dot means ``pkg`` itself,
    whereas inside ``pkg/mod.py`` (FQN ``pkg.mod``) a single dot means ``pkg``.
    A package is already "at" its own level; a module has to drop its own name
    first.

    Returns ``None`` when the import climbs above the top-level package, which
    is an ``ImportError`` at runtime and nothing we can resolve.
    """
    if level == 0:
        return module

    parts = current_module_fqn.split(".")
    # A package is already "at" its own level; a module must drop its own name first.
    climb = level - 1 if is_package else level
    remaining = len(parts) - climb
    if remaining <= 0:
        return None  # climbs above the top-level package: an ImportError at runtime

    base = parts[:remaining]
    if module:
        base = [*base, *module.split(".")]
    return ".".join(base)


def records_from_import(
    node: ast.Import,
    *,
    is_type_only: bool = False,
    is_deferred: bool = False,
) -> list[ImportRecord]:
    """``import a.b.c`` / ``import a.b.c as abc`` / ``import x, y``."""
    records: list[ImportRecord] = []
    for alias in node.names:
        if alias.asname:
            # `import a.b.c as abc` binds `abc` directly to the leaf module.
            local_name, target = alias.asname, alias.name
        else:
            # `import a.b.c` binds only the top package name `a`.
            local_name = alias.name.split(".")[0]
            target = local_name
        records.append(
            ImportRecord(
                module_fqn=alias.name,
                local_name=local_name,
                target_fqn=target,
                imported_symbol=None,
                line=node.lineno,
                kind="absolute",
                is_type_only=is_type_only,
                is_deferred=is_deferred,
            )
        )
    return records


def records_from_import_from(
    node: ast.ImportFrom,
    *,
    current_module_fqn: str,
    is_package: bool,
    is_type_only: bool = False,
    is_deferred: bool = False,
) -> list[ImportRecord]:
    """``from x import y``, ``from . import y``, ``from ..pkg.mod import y as z``,
    and ``from x import *``."""
    base = resolve_relative(
        current_module_fqn,
        is_package=is_package,
        level=node.level,
        module=node.module,
    )
    if base is None:
        return []

    kind: ImportKind = "relative" if node.level else "absolute"
    records: list[ImportRecord] = []
    for alias in node.names:
        if alias.name == "*":
            records.append(
                ImportRecord(
                    module_fqn=base,
                    local_name="*",
                    target_fqn=base,
                    imported_symbol=None,
                    line=node.lineno,
                    kind="star",
                    is_type_only=is_type_only,
                    is_deferred=is_deferred,
                )
            )
            continue
        records.append(
            ImportRecord(
                module_fqn=base,
                local_name=alias.asname or alias.name,
                target_fqn=f"{base}.{alias.name}",
                imported_symbol=alias.name,
                line=node.lineno,
                kind=kind,
                is_type_only=is_type_only,
                is_deferred=is_deferred,
            )
        )
    return records


def build_import_table(
    records_by_module: dict[str, list[ImportRecord]],
) -> dict[str, dict[str, ImportRecord]]:
    """module FQN → local name → the record that bound it.

    Later imports win, matching Python: the last binding of a name is the one in
    effect for code that runs after it. Star imports are kept out of the table
    (they bind unknown names) and handled separately by the resolver.
    """
    table: dict[str, dict[str, ImportRecord]] = {}
    for module_fqn, records in records_by_module.items():
        bindings: dict[str, ImportRecord] = {}
        for record in records:
            if record.kind == "star":
                continue
            bindings[record.local_name] = record
        table[module_fqn] = bindings
    return table
