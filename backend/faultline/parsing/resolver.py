"""Pass 2: turn raw call sites into graph edges.

This is the heart of Faultline. Python has no compiler that can tell you
``obj.save()`` means ``models.User.save``, so the resolver applies a fixed
ladder of lookups and attaches a confidence score to whatever it finds. Where
the answer is genuinely unknowable it emits nothing and says so in the stats.

The ladder, for a bare name ``foo()`` called from function ``F`` in module ``M``:

===  ==========================================================  ==============
 #   Lookup                                                      Result
===  ==========================================================  ==============
 1   a function nested inside ``F`` (``F.<locals>.foo``)         exact   1.0
 2   the same, walking outward through enclosing functions       exact   1.0
 3   module globals of ``M`` (``M.foo``)                         exact   1.0
 4   the import table of ``M``                                   exact   1.0
 5   every ``from x import *`` target of ``M``                   inferred 0.8
 6   Python builtins                                             dropped
 7   anything in the repo with that short name                   ambiguous ≤0.5
 8   nothing                                                     unresolved
===  ==========================================================  ==============

Note what is **not** in that list: the enclosing class. A bare ``foo()`` inside
a method does not reach ``self.foo`` — that is a ``NameError`` in Python — so
resolving it there would invent edges that cannot exist at runtime.
"""

from __future__ import annotations

import builtins
from collections import defaultdict, deque
from dataclasses import dataclass, field

from faultline.models import (
    CONFIDENCE_AMBIGUOUS_CAP,
    CONFIDENCE_ANNOTATED,
    CONFIDENCE_CLASS_ATTRIBUTE,
    CONFIDENCE_EXACT,
    CONFIDENCE_INHERITED,
    CONFIDENCE_LOCAL_TYPE,
    CONFIDENCE_RETURN_TYPE,
    CONFIDENCE_STAR_IMPORT,
    LOCALS_MARKER,
    MAX_AMBIGUOUS_FANOUT,
    CallEdge,
    ClassDef,
    FunctionDef,
    ImportEdge,
    ImportRecord,
    InheritEdge,
    InstantiationEdge,
    ModuleFacts,
    RawCall,
    Resolution,
    ResolutionStats,
    ResolvedRepo,
)

BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins))

#: Method names that overwhelmingly belong to builtin and standard-library types
#: (dict, list, set, str, file objects). When the receiver is unknown, a repo class
#: that happens to define ``pop`` or ``get`` is a far less likely target than the
#: builtin, so the ambiguous edge's confidence is halved. It stays in the graph --
#: lower the threshold and it reappears -- but it no longer shows by default.
COMMON_BUILTIN_METHODS: frozenset[str] = frozenset(
    {
        "get", "pop", "popitem", "setdefault", "update", "items", "keys", "values", "clear",
        "copy", "append", "extend", "insert", "remove", "index", "count", "sort", "reverse",
        "add", "discard", "union", "intersection", "difference", "join", "split", "rsplit",
        "strip", "lstrip", "rstrip", "replace", "startswith", "endswith", "format", "encode",
        "decode", "lower", "upper", "find", "read", "readline", "readlines", "write",
        "writelines", "close", "flush", "seek", "tell", "loads", "dumps", "load", "dump",
    }
)  # fmt: skip

#: Builtin *type* names. A call like ``object.__repr__(self)`` or ``dict.fromkeys(x)``
#: is on the builtin unless the module rebinds the name, so it never targets repo code.
BUILTIN_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "object", "dict", "list", "set", "frozenset", "str", "bytes", "bytearray",
        "int", "float", "complex", "tuple", "type", "bool",
    }
)  # fmt: skip

#: Names that are always plumbing rather than a call worth an edge.
IGNORED_NAMES: frozenset[str] = frozenset({"super", "self", "cls"})

_DROP_BUILTIN = "builtin"
_DROP_EXTERNAL = "external"
_DROP_TOO_AMBIGUOUS = "too_ambiguous"
_DROP_UNRESOLVED = "unresolved"


@dataclass(slots=True)
class Outcome:
    """What one lookup produced: some targets, or a reason there are none."""

    targets: list[str] = field(default_factory=list)
    confidence: float = CONFIDENCE_EXACT
    resolution: Resolution = "exact"
    dropped: str | None = None

    @classmethod
    def hit(cls, target: str, confidence: float, resolution: Resolution) -> Outcome:
        return cls(targets=[target], confidence=confidence, resolution=resolution)

    @classmethod
    def drop(cls, reason: str) -> Outcome:
        return cls(dropped=reason)


# ---------------------------------------------------------------------------
# symbol table
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SymbolTable:
    """Every fact about the repo that resolution needs, indexed for lookup."""

    functions: dict[str, FunctionDef] = field(default_factory=dict)
    classes: dict[str, ClassDef] = field(default_factory=dict)
    modules: dict[str, ModuleFacts] = field(default_factory=dict)

    by_name: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    """Short name → every FQN in the repo with that name. The last-resort lookup."""

    methods_by_name: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    """Same, restricted to methods — used for unresolvable ``obj.method()`` calls."""

    imports: dict[str, dict[str, ImportRecord]] = field(default_factory=dict)
    star_targets: dict[str, list[str]] = field(default_factory=dict)

    class_bases: dict[str, list[str]] = field(default_factory=dict)
    """Class FQN → resolved base class FQNs, in declaration order."""

    class_external_bases: dict[str, int] = field(default_factory=dict)
    """How many bases could not be resolved — i.e. live outside the repo."""

    local_types: dict[str, dict[str, tuple[str, float]]] = field(default_factory=dict)
    """Function FQN → variable name → (class FQN, confidence)."""

    attr_types: dict[str, dict[str, tuple[str, float]]] = field(default_factory=dict)
    """Class FQN → ``self.<attr>`` name → (class FQN, confidence)."""

    class_aliases: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)
    """Class FQN → attribute name → (defining module FQN, raw right-hand side)."""

    def is_callable_symbol(self, fqn: str) -> bool:
        return fqn in self.functions or fqn in self.classes

    def canonical(self, fqn: str, *, depth: int = 4) -> str | None:
        """Follow package re-exports to the definition site.

        ``from flask import Flask`` points at ``flask.Flask``, which is not
        defined there — ``flask/__init__.py`` does ``from .app import Flask``.
        Without this hop a large share of calls into any well-organised package
        resolve to nothing. The depth limit stops a circular re-export looping.
        """
        if self.is_callable_symbol(fqn):
            return fqn
        if depth <= 0 or "." not in fqn:
            return None
        parent, name = fqn.rsplit(".", 1)
        if parent not in self.modules:
            return None
        record = self.imports.get(parent, {}).get(name)
        if record is not None:
            if record.target_fqn == fqn:
                return None
            return self.canonical(record.target_fqn, depth=depth - 1)
        # No explicit binding: the package may re-export through `from x import *`,
        # which is how httpx and many other libraries publish their public API.
        for star_target in self.star_targets.get(parent, ()):
            found = self.canonical(f"{star_target}.{name}", depth=depth - 1)
            if found is not None:
                return found
        return None


def _enclosing_function_fqns(caller_fqn: str) -> list[str]:
    """Every enclosing function FQN, innermost first.

    ``pkg.mod.outer.<locals>.middle.<locals>.inner`` yields
    ``[..., pkg.mod.outer.<locals>.middle, pkg.mod.outer]``.
    """
    result: list[str] = []
    current = caller_fqn
    while LOCALS_MARKER in current:
        current = current.rsplit(f".{LOCALS_MARKER}.", 1)[0]
        result.append(current)
    return result


class Resolver:
    """Resolves every raw call site in a parsed repository."""

    def __init__(self, modules: list[ModuleFacts]) -> None:
        self.module_facts = modules
        self.table = SymbolTable()
        self.stats = ResolutionStats()
        self._build_symbols()
        self._resolve_class_bases()
        self._build_type_maps()

    # -- table construction --------------------------------------------------

    def _build_symbols(self) -> None:
        table = self.table
        for facts in self.module_facts:
            table.modules[facts.module_fqn] = facts
            table.imports[facts.module_fqn] = {
                record.local_name: record for record in facts.imports if record.kind != "star"
            }
            table.star_targets[facts.module_fqn] = list(facts.star_import_targets)
            for alias in facts.class_aliases:
                table.class_aliases.setdefault(alias.class_fqn, {})[alias.name] = (
                    facts.module_fqn,
                    alias.expr,
                )

            for klass in facts.classes:
                table.classes[klass.fqn] = klass
                table.by_name[klass.name].append(klass.fqn)
            for function in facts.functions:
                table.functions[function.fqn] = function
                table.by_name[function.name].append(function.fqn)
                if function.is_method:
                    table.methods_by_name[function.name].append(function.fqn)

    def _resolve_class_bases(self) -> None:
        """Turn raw base expressions such as ``models.Model`` into class FQNs."""
        for klass in self.table.classes.values():
            resolved: list[str] = []
            external = 0
            for base_expr in klass.base_names:
                base_fqn = self._class_from_expr(klass.module_fqn, base_expr)
                if base_fqn is not None and base_fqn != klass.fqn:
                    resolved.append(base_fqn)
                elif base_expr not in ("object", "Protocol", "ABC", "Enum"):
                    # A base we cannot see means inherited methods we cannot see.
                    external += 1
            self.table.class_bases[klass.fqn] = resolved
            self.table.class_external_bases[klass.fqn] = external

    def _build_type_maps(self) -> None:
        """Collapse the parser's type observations into one type per name.

        A name assigned two different classes in the same scope is dropped
        rather than guessed at — one wrong high-confidence edge does more damage
        to trust in the tool than ten missing ones.
        """
        local: dict[str, dict[str, list[tuple[str, float]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        attrs: dict[str, dict[str, list[tuple[str, float]]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for facts in self.module_facts:
            for assignment in facts.assignments:
                typed = self._type_of_expression(facts.module_fqn, assignment.class_expr)
                if typed is None:
                    continue
                class_fqn, base_confidence = typed
                confidence = {
                    "annotation": CONFIDENCE_ANNOTATED,
                    "assignment": CONFIDENCE_LOCAL_TYPE,
                    "attribute": CONFIDENCE_LOCAL_TYPE,
                }[assignment.source]
                # Evidence compounds: a type read off a return annotation
                # (base_confidence < 1) is weaker than one read off `User()`.
                confidence = round(confidence * base_confidence, 4)
                bucket = attrs if assignment.source == "attribute" else local
                bucket[assignment.scope_fqn][assignment.var_name].append((class_fqn, confidence))

        self.table.local_types = _collapse_types(local)
        self.table.attr_types = _collapse_types(attrs)

    def _type_of_expression(self, module_fqn: str, expr: str) -> tuple[str, float] | None:
        """``User`` → that class. ``get_user`` → its annotated return class."""
        class_fqn = self._class_from_expr(module_fqn, expr)
        if class_fqn is not None:
            return class_fqn, CONFIDENCE_EXACT

        function_fqn = self._symbol_from_expr(module_fqn, expr, self.table.functions)
        if function_fqn is not None:
            return_type = self.table.functions[function_fqn].return_type
            if return_type:
                returned = self._class_from_expr(
                    self.table.functions[function_fqn].module_fqn, return_type
                )
                if returned is not None:
                    return returned, CONFIDENCE_RETURN_TYPE
        return None

    def _class_from_expr(self, module_fqn: str, expr: str) -> str | None:
        return self._symbol_from_expr(module_fqn, expr, self.table.classes)

    def _symbol_from_expr(
        self, module_fqn: str, expr: str, index: dict[str, ClassDef] | dict[str, FunctionDef]
    ) -> str | None:
        """Resolve a dotted source expression against one symbol index."""
        if not expr:
            return None
        parts = expr.split(".")

        # Defined right here in this module (possibly nested: `Outer.Inner`).
        local_candidate = f"{module_fqn}.{expr}"
        if local_candidate in index:
            return local_candidate

        # Bound by an import in this module, possibly a package re-export.
        record = self.table.imports.get(module_fqn, {}).get(parts[0])
        if record is not None:
            candidate = self.table.canonical(".".join([record.target_fqn, *parts[1:]]))
            if candidate is not None and candidate in index:
                return candidate

        # Pulled in by `from x import *`.
        for star_target in self.table.star_targets.get(module_fqn, ()):
            candidate = ".".join([star_target, *parts])
            if candidate in index:
                return candidate

        # A single unambiguous match anywhere in the repo.
        if len(parts) == 1:
            matches = [fqn for fqn in self.table.by_name.get(parts[0], ()) if fqn in index]
            if len(matches) == 1:
                return matches[0]
        return None

    # -- class helpers -------------------------------------------------------

    def _owner_class(self, caller_fqn: str) -> str | None:
        """The class a ``self.`` call inside ``caller_fqn`` refers to.

        Walks up the FQN so a closure defined inside a method still finds the
        class — it sees the same ``self``.
        """
        current = caller_fqn
        while "." in current:
            current = current.rsplit(".", 1)[0]
            if current.endswith(f".{LOCALS_MARKER}"):
                continue
            if current in self.table.classes:
                return current
        return None

    def _lookup_on_class(
        self, class_fqn: str, name: str, *, include_self: bool = True
    ) -> tuple[str, float] | None:
        """Find ``name`` on a class or, failing that, on its bases.

        Bases are walked breadth-first in declaration order. That is not
        Python's true C3 linearisation, which is why an inherited hit scores
        0.95 rather than 1.0: for single inheritance and most diamonds the two
        agree, and where they disagree the score says so.
        """
        if include_self:
            direct = f"{class_fqn}.{name}"
            if self.table.is_callable_symbol(direct):
                return direct, CONFIDENCE_EXACT

        seen = {class_fqn}
        queue = deque(self.table.class_bases.get(class_fqn, ()))
        while queue:
            base = queue.popleft()
            if base in seen:
                continue
            seen.add(base)
            candidate = f"{base}.{name}"
            if self.table.is_callable_symbol(candidate):
                return candidate, CONFIDENCE_INHERITED
            queue.extend(self.table.class_bases.get(base, ()))
        return None

    def _has_external_bases(self, class_fqn: str) -> bool:
        """True when any class in the inheritance chain comes from outside the repo."""
        seen: set[str] = set()
        queue = deque([class_fqn])
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            if self.table.class_external_bases.get(current, 0) > 0:
                return True
            queue.extend(self.table.class_bases.get(current, ()))
        return False

    # -- the ladder ----------------------------------------------------------

    def _variable_type(
        self, caller_fqn: str, module_fqn: str, name: str
    ) -> tuple[str, float] | None:
        """The known type of variable ``name`` as seen from ``caller_fqn``.

        Follows Python's own scoping: the function itself, then each enclosing
        function outward (closures -- ``app = Flask(); @app.route`` inside an app
        factory is the canonical case), then the module. Innermost wins, so a
        local assignment shadows an outer one.
        """
        for scope in (caller_fqn, *_enclosing_function_fqns(caller_fqn), module_fqn):
            found = self.table.local_types.get(scope, {}).get(name)
            if found is not None:
                return found
        return None

    def _class_alias_target(self, class_fqn: str, name: str) -> str | None:
        """Where ``self.<name>`` points when ``<name>`` is a class-body alias.

        Walks the class and its bases, so an alias declared on a base class
        (``Scaffold.url_rule_class``) is found from a subclass.
        """
        seen: set[str] = set()
        queue = deque([class_fqn])
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            alias = self.table.class_aliases.get(current, {}).get(name)
            if alias is not None:
                module_fqn, expr = alias
                return self._class_from_expr(module_fqn, expr) or self._symbol_from_expr(
                    module_fqn, expr, self.table.functions
                )
            queue.extend(self.table.class_bases.get(current, ()))
        return None

    def _visible_from(self, candidate: str, call: RawCall) -> bool:
        """Could ``call`` plausibly reach ``candidate`` at all?

        Two structural impossibilities, removed before any guessing:

        * a class or function defined inside a function body is only visible
          within that function (a test's local ``Wrapper`` cannot be the receiver
          in library code);
        * production code does not call into test modules.
        """
        if LOCALS_MARKER in candidate:
            owner = candidate.rsplit(f".{LOCALS_MARKER}.", 1)[0]
            if call.caller_fqn != owner and not call.caller_fqn.startswith(owner + "."):
                return False
        caller_module = self.table.modules.get(call.module_fqn)
        if caller_module is not None and not caller_module.is_test:
            symbol = self.table.functions.get(candidate) or self.table.classes.get(candidate)
            target_module = self.table.modules.get(symbol.module_fqn) if symbol else None
            if target_module is not None and target_module.is_test:
                return False
        return True

    def _module_level_named(self, name: str) -> list[str]:
        """Definitions a bare ``name()`` could plausibly reach from elsewhere.

        Only names bound at module level qualify. A method is invisible to a
        bare call (``NameError``), and a nested function is invisible outside
        its parent, so admitting either would invent edges that cannot exist.
        """
        found: list[str] = []
        for fqn in self.table.by_name.get(name, ()):
            symbol = self.table.classes.get(fqn) or self.table.functions.get(fqn)
            if symbol is not None and fqn == f"{symbol.module_fqn}.{symbol.name}":
                found.append(fqn)
        return found

    def _resolve_bare_name(self, call: RawCall, name: str) -> Outcome:
        table = self.table

        # 1 & 2 — functions nested in this function, then in enclosing functions.
        for scope in (call.caller_fqn, *_enclosing_function_fqns(call.caller_fqn)):
            candidate = f"{scope}.{LOCALS_MARKER}.{name}"
            if table.is_callable_symbol(candidate):
                return Outcome.hit(candidate, CONFIDENCE_EXACT, "exact")

        # 3 — module globals.
        candidate = f"{call.module_fqn}.{name}"
        if table.is_callable_symbol(candidate):
            record = table.imports.get(call.module_fqn, {}).get(name)
            if record is None:
                return Outcome.hit(candidate, CONFIDENCE_EXACT, "exact")
            # The name is bound by an import *and* defined here: the classic
            #     try: from lib import Thing
            #     except ImportError: def Thing(...): ...
            # Which one runs depends on the environment, so neither is certain.
            targets = {candidate}
            imported = table.canonical(record.target_fqn)
            if imported is not None:
                targets.add(imported)
            ordered = sorted(targets)
            return Outcome(
                targets=ordered,
                confidence=round(min(CONFIDENCE_AMBIGUOUS_CAP, 1 / len(ordered)), 4),
                resolution="ambiguous",
            )

        # 4 — the import table, following package re-exports.
        record = table.imports.get(call.module_fqn, {}).get(name)
        if record is not None:
            target = table.canonical(record.target_fqn)
            if target is not None:
                return Outcome.hit(target, CONFIDENCE_EXACT, "exact")
            # Imported from outside the repo, or a module used as a callable.
            return Outcome.drop(_DROP_EXTERNAL)

        # 5 — star imports.
        for star_target in table.star_targets.get(call.module_fqn, ()):
            candidate = f"{star_target}.{name}"
            if table.is_callable_symbol(candidate):
                return Outcome.hit(candidate, CONFIDENCE_STAR_IMPORT, "inferred")

        # 6 — builtins never become nodes.
        if name in BUILTIN_NAMES:
            return Outcome.drop(_DROP_BUILTIN)

        # 7 — any module-level function or class in the repo with that name.
        return self._ambiguous(self._module_level_named(name), call)

    def _resolve_self_call(self, call: RawCall) -> Outcome:
        class_fqn = self._owner_class(call.caller_fqn)
        if class_fqn is None:
            # `self` outside a class body: a free function whose first parameter
            # happens to be called `self`. Treat it as an unknown object.
            return self._ambiguous(
                self.table.methods_by_name.get(call.parts[-1], []), call, method=call.parts[-1]
            )

        # `self.helper()`
        if len(call.parts) == 1:
            if call.parts[0] == "__class__":
                # `self.__class__(...)` builds another instance of this class -- or
                # of a subclass, hence "inferred" rather than "exact".
                return Outcome.hit(class_fqn, CONFIDENCE_CLASS_ATTRIBUTE, "inferred")
            found = self._lookup_on_class(class_fqn, call.parts[0])
            if found is not None:
                # An inherited hit is still a single definite target, so the
                # resolution stays "exact"; only the confidence drops.
                return Outcome.hit(found[0], found[1], "exact")
            aliased = self._class_alias_target(class_fqn, call.parts[0])
            if aliased is not None:
                # `session_class = Foo` in the class body: `self.session_class()`
                # builds a Foo, unless a subclass overrides the attribute.
                return Outcome.hit(aliased, CONFIDENCE_CLASS_ATTRIBUTE, "inferred")
            if self._has_external_bases(class_fqn):
                return Outcome.drop(_DROP_EXTERNAL)
            return self._ambiguous(
                self.table.methods_by_name.get(call.parts[0], []), call, method=call.parts[0]
            )

        # `self.db.commit()` — needs the type of the attribute.
        attribute, *rest = call.parts
        typed = self.table.attr_types.get(class_fqn, {}).get(attribute)
        if typed is not None:
            return self._resolve_chain_on_class(typed[0], rest, typed[1], call)
        if self._has_external_bases(class_fqn):
            return Outcome.drop(_DROP_EXTERNAL)
        return self._ambiguous(
            self.table.methods_by_name.get(call.parts[-1], []), call, method=call.parts[-1]
        )

    def _resolve_super_call(self, call: RawCall) -> Outcome:
        class_fqn = self._owner_class(call.caller_fqn)
        if class_fqn is None:
            return Outcome.drop(_DROP_UNRESOLVED)
        found = self._lookup_on_class(class_fqn, call.parts[-1], include_self=False)
        if found is not None:
            return Outcome.hit(found[0], CONFIDENCE_INHERITED, "exact")
        if self._has_external_bases(class_fqn):
            return Outcome.drop(_DROP_EXTERNAL)
        return Outcome.drop(_DROP_UNRESOLVED)

    def _resolve_attribute_call(self, call: RawCall) -> Outcome:
        """``a.b.foo()`` — worked left to right, four cases deep."""
        table = self.table
        base, *rest = call.parts
        attribute = rest[-1]

        if (
            base in BUILTIN_TYPE_NAMES
            and base not in table.imports.get(call.module_fqn, {})
            and f"{call.module_fqn}.{base}" not in table.classes
            and self._variable_type(call.caller_fqn, call.module_fqn, base) is None
        ):
            return Outcome.drop(_DROP_BUILTIN)

        record = table.imports.get(call.module_fqn, {}).get(base)
        if record is not None:
            # Case A — the base names an imported module: `helpers.format_date()`.
            candidate = table.canonical(".".join([record.target_fqn, *rest]))
            if candidate is not None:
                return Outcome.hit(candidate, CONFIDENCE_EXACT, "exact")

            # Case B — the base names an imported class: `User.from_row()`.
            if record.target_fqn in table.classes and len(rest) == 1:
                found = self._lookup_on_class(record.target_fqn, attribute)
                if found is not None:
                    return Outcome.hit(found[0], found[1], "exact")

            if record.target_fqn not in table.modules and record.target_fqn not in table.classes:
                # The base lives outside the repo — `os.path.join`, `np.array`.
                return Outcome.drop(_DROP_EXTERNAL)
            # The base is ours but the attribute is not a function or class we
            # know: a module-level object, a constant, a lazily built registry.
            # Guessing by name here would be low precision, so stop.
            return Outcome.drop(_DROP_UNRESOLVED)

        # Case A' — a dotted path into this module's own namespace, such as a
        # method on a nested class: `Outer.Inner.make()`.
        candidate = table.canonical(".".join([call.module_fqn, *call.parts]))
        if candidate is not None:
            return Outcome.hit(candidate, CONFIDENCE_EXACT, "exact")

        # Case B' — the base is a class defined in this module.
        class_fqn = self._class_from_expr(call.module_fqn, base)
        if class_fqn is not None and len(rest) == 1:
            found = self._lookup_on_class(class_fqn, attribute)
            if found is not None:
                return Outcome.hit(found[0], found[1], "exact")

        # Case C — the base is a variable whose type we know, either because it
        # was annotated or because it was built right here. Locals shadow
        # module-level names, so the function scope is consulted first.
        typed = self._variable_type(call.caller_fqn, call.module_fqn, base)
        if typed is not None:
            return self._resolve_chain_on_class(typed[0], rest, typed[1], call)

        # Case D — a parameter, or an expression we cannot follow. Fall back to
        # every method with that name, confidence spread across them.
        return self._ambiguous(table.methods_by_name.get(attribute, []), call, method=attribute)

    def _resolve_chain_on_class(
        self, class_fqn: str, rest: list[str], confidence: float, call: RawCall
    ) -> Outcome:
        """Resolve ``rest`` against a class whose identity we already know."""
        if len(rest) == 1:
            found = self._lookup_on_class(class_fqn, rest[0])
            if found is not None:
                return Outcome(
                    targets=[found[0]],
                    confidence=round(min(confidence, found[1]), 4),
                    resolution="inferred" if confidence < CONFIDENCE_EXACT else "exact",
                )
            if self._has_external_bases(class_fqn):
                return Outcome.drop(_DROP_EXTERNAL)
            return self._ambiguous(
                self.table.methods_by_name.get(rest[0], []), call, method=rest[0]
            )

        # `obj.inner.method()` — one more hop through the attribute types.
        attribute, *tail = rest
        typed = self.table.attr_types.get(class_fqn, {}).get(attribute)
        if typed is not None:
            return self._resolve_chain_on_class(
                typed[0], tail, round(min(confidence, typed[1]), 4), call
            )
        return self._ambiguous(self.table.methods_by_name.get(rest[-1], []), call, method=rest[-1])

    def _ambiguous(
        self, candidates: list[str], call: RawCall, *, method: str | None = None
    ) -> Outcome:
        """Spread confidence across every plausible target.

        With no score the impact query drowns: a name like ``run`` can have
        sixty methods, and sixty edges from one call site is noise, not data.
        Above :data:`MAX_AMBIGUOUS_FANOUT` candidates the call is dropped.
        """
        unique = sorted(
            {
                fqn
                for fqn in candidates
                if self.table.is_callable_symbol(fqn) and self._visible_from(fqn, call)
            }
        )
        if not unique:
            # Nothing reachable in the repository is called that. The target is
            # almost certainly standard-library or third-party code, which
            # Faultline does not model, so this counts as external, not a miss.
            return Outcome.drop(_DROP_EXTERNAL)
        if len(unique) > MAX_AMBIGUOUS_FANOUT:
            return Outcome.drop(_DROP_TOO_AMBIGUOUS)
        confidence = min(CONFIDENCE_AMBIGUOUS_CAP, 1 / len(unique))
        if method in COMMON_BUILTIN_METHODS:
            confidence *= 0.5
        return Outcome(targets=unique, confidence=round(confidence, 4), resolution="ambiguous")

    # -- driver --------------------------------------------------------------

    def resolve_call(self, call: RawCall) -> Outcome:
        if call.kind == "name":
            name = call.parts[0]
            if name in IGNORED_NAMES:
                return Outcome.drop(_DROP_BUILTIN)
            return self._resolve_bare_name(call, name)
        if call.kind == "self":
            return self._resolve_self_call(call)
        if call.kind == "super":
            return self._resolve_super_call(call)
        return self._resolve_attribute_call(call)

    def resolve(self) -> ResolvedRepo:
        """Resolve every call site and build the full edge set."""
        calls: dict[tuple[str, str, int], CallEdge] = {}
        instantiations: dict[tuple[str, str, int], InstantiationEdge] = {}

        for facts in self.module_facts:
            for call in facts.calls:
                self.stats.call_sites_total += 1
                outcome = self.resolve_call(call)

                if outcome.dropped is not None:
                    self._count_drop(outcome.dropped)
                    continue
                if not outcome.targets:
                    self.stats.unresolved += 1
                    continue

                self._count_resolution(outcome.resolution)
                for target in outcome.targets:
                    self._emit(call, target, outcome, calls, instantiations)

        return ResolvedRepo(
            modules=self.module_facts,
            calls=list(calls.values()),
            instantiations=list(instantiations.values()),
            inherits=self._inherit_edges(),
            imports=self._import_edges(),
            stats=self.stats,
        )

    def _emit(
        self,
        call: RawCall,
        target: str,
        outcome: Outcome,
        calls: dict[tuple[str, str, int], CallEdge],
        instantiations: dict[tuple[str, str, int], InstantiationEdge],
    ) -> None:
        """Record one resolved target.

        Constructing a class produces two edges: ``:INSTANTIATES`` to the class,
        and — when the constructor is visible — ``:CALLS`` to its ``__init__``,
        so that "what breaks if I change this constructor" has an answer.
        """
        confidence = outcome.confidence
        if target in self.table.classes:
            key = (call.caller_fqn, target, call.line)
            existing = instantiations.get(key)
            if existing is None or existing.confidence < outcome.confidence:
                instantiations[key] = InstantiationEdge(
                    caller_fqn=call.caller_fqn,
                    class_fqn=target,
                    line=call.line,
                    confidence=outcome.confidence,
                )
            constructor = self._lookup_on_class(target, "__init__")
            if constructor is None:
                return
            target = constructor[0]
            # An inherited constructor is only as certain as the base-class walk.
            confidence = min(confidence, constructor[1])

        if target == call.caller_fqn:  # direct recursion adds nothing to impact
            return

        key = (call.caller_fqn, target, call.line)
        existing_call = calls.get(key)
        if existing_call is None or existing_call.confidence < confidence:
            calls[key] = CallEdge(
                caller_fqn=call.caller_fqn,
                callee_fqn=target,
                line=call.line,
                confidence=round(confidence, 4),
                resolution=outcome.resolution,
                via=call.via,
            )

    def _count_drop(self, reason: str) -> None:
        if reason == _DROP_BUILTIN:
            self.stats.dropped_builtin += 1
        elif reason == _DROP_EXTERNAL:
            self.stats.dropped_external += 1
        elif reason == _DROP_TOO_AMBIGUOUS:
            self.stats.dropped_too_ambiguous += 1
        else:
            self.stats.unresolved += 1

    def _count_resolution(self, resolution: Resolution) -> None:
        if resolution == "exact":
            self.stats.resolved_exact += 1
        elif resolution == "inferred":
            self.stats.resolved_inferred += 1
        else:
            self.stats.resolved_ambiguous += 1

    def _inherit_edges(self) -> list[InheritEdge]:
        edges: list[InheritEdge] = []
        for class_fqn, bases in self.table.class_bases.items():
            for position, base in enumerate(bases):
                edges.append(InheritEdge(child_fqn=class_fqn, parent_fqn=base, position=position))
        return edges

    def _import_edges(self) -> list[ImportEdge]:
        """Only imports that stay inside the repo become edges.

        A dependency on ``os`` is real but uninteresting here, and creating
        ``:Module`` nodes for the standard library would swamp the graph.
        """
        seen: set[tuple[str, str, int]] = set()
        edges: list[ImportEdge] = []
        for facts in self.module_facts:
            for record in facts.imports:
                target = (
                    record.target_fqn
                    if record.target_fqn in self.table.modules
                    else record.module_fqn
                )
                if target not in self.table.modules or target == facts.module_fqn:
                    continue
                key = (facts.module_fqn, target, record.line)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    ImportEdge(
                        source_fqn=facts.module_fqn,
                        target_fqn=target,
                        line=record.line,
                        kind=record.kind,
                        alias=record.local_name,
                        is_type_only=record.is_type_only,
                        is_deferred=record.is_deferred,
                    )
                )
        return edges


def _collapse_types(
    observations: dict[str, dict[str, list[tuple[str, float]]]],
) -> dict[str, dict[str, tuple[str, float]]]:
    """One type per name, or none at all if the observations disagree."""
    collapsed: dict[str, dict[str, tuple[str, float]]] = {}
    for scope, names in observations.items():
        resolved: dict[str, tuple[str, float]] = {}
        for name, entries in names.items():
            distinct = {fqn for fqn, _ in entries}
            if len(distinct) != 1:
                continue  # rebound to a different class: refuse to guess
            fqn = next(iter(distinct))
            resolved[name] = (fqn, max(confidence for _, confidence in entries))
        if resolved:
            collapsed[scope] = resolved
    return collapsed


def resolve_repository(modules: list[ModuleFacts]) -> ResolvedRepo:
    """Convenience wrapper: parse facts in, graph edges out."""
    return Resolver(modules).resolve()
