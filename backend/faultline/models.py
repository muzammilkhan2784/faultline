"""Plain dataclasses describing everything pass 1 learns about a module.

These are deliberately dumb records. Nothing here touches Neo4j, the filesystem
or the AST — which is what makes the parser and the resolver testable as pure
functions over strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: How confident the resolver is about an edge.
#:   exact     — a single unambiguous target, found by scope/import/class lookup
#:   inferred  — found via a heuristic (star import, local-variable type)
#:   ambiguous — several plausible targets; every one gets a low-confidence edge
Resolution = Literal["exact", "inferred", "ambiguous"]

#: The syntactic shape that produced a call edge.
CallVia = Literal["name", "attribute", "self", "decorator"]

#: How the callee expression was written.
CallShape = Literal["name", "attr", "self", "super"]

#: Where a local variable's type came from.
TypeSource = Literal["assignment", "annotation", "attribute"]

#: The syntactic shape that produced an import edge.
ImportKind = Literal["absolute", "relative", "star"]

#: The marker Python itself uses in ``__qualname__`` for a nested definition.
LOCALS_MARKER = "<locals>"

# Confidence constants. Named rather than sprinkled as magic numbers so the
# README table and the code can never drift apart.
CONFIDENCE_EXACT = 1.0
CONFIDENCE_INHERITED = 0.95  # declaration-order base walk, not true C3 MRO
CONFIDENCE_ANNOTATED = 0.9  # `def f(u: User)` … `u.save()` — could be a subclass
CONFIDENCE_RETURN_TYPE = 0.85  # `u = get_user()` where `get_user() -> User`
CONFIDENCE_CLASS_ATTRIBUTE = (
    0.9  # `session_class = Foo` … `self.session_class()` — a subclass may override
)
CONFIDENCE_STAR_IMPORT = 0.8
CONFIDENCE_LOCAL_TYPE = 0.7  # `u = User()` … `u.save()`
CONFIDENCE_AMBIGUOUS_CAP = 0.5

#: Above this many same-named candidates an attribute call is pure noise.
MAX_AMBIGUOUS_FANOUT = 10


# ---------------------------------------------------------------------------
# Pass 1 — facts
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ImportRecord:
    """One name bound into a module's namespace by an import statement.

    Two FQNs are tracked because they genuinely differ. For ``import a.b.c``
    Python binds the *name* ``a`` while the *dependency* is ``a.b.c``; for
    ``from pkg import helpers`` the dependency is ``pkg`` but the bound name
    refers to ``pkg.helpers``. Collapsing the two is the classic way to get
    ``a.b.c.foo()`` resolving to ``a.b.c.b.c.foo``.
    """

    module_fqn: str
    """The module this import depends on — the ``from`` module, or the full
    dotted module for a plain ``import``. This is the ``:IMPORTS`` edge target."""

    local_name: str
    """The name actually bound in this module's namespace (the alias if given)."""

    target_fqn: str
    """What ``local_name`` refers to, dotted. Resolving ``local_name.rest()``
    means looking up ``f"{target_fqn}.{rest}"``."""

    imported_symbol: str | None
    """For ``from x import y`` this is ``y``; for ``import x`` it is ``None``."""

    line: int
    kind: ImportKind
    is_type_only: bool = False
    """True when the import sits under ``if TYPE_CHECKING:``. It does not exist
    at runtime, so it cannot take part in a runtime import cycle."""

    is_deferred: bool = False
    """True when the import sits inside a function body. Like ``TYPE_CHECKING``,
    a deferred import is a standard *fix* for a circular import rather than a
    cause of one, so cycle detection ignores it."""


@dataclass(slots=True)
class FunctionDef:
    fqn: str
    name: str
    module_fqn: str
    class_fqn: str | None
    line_start: int
    line_end: int
    is_method: bool
    is_async: bool
    is_dunder: bool
    is_private: bool
    is_test: bool
    is_entrypoint: bool
    param_count: int
    param_names: list[str] = field(default_factory=list)
    complexity: int = 1
    decorators: list[str] = field(default_factory=list)
    return_type: str | None = None
    """Raw return annotation, when one is present. Followed one hop so that
    ``u = get_user()`` types ``u`` when ``get_user`` is annotated."""
    doc: str | None = None
    """First sentence of the docstring: what the author says this is for."""


@dataclass(slots=True)
class ClassDef:
    fqn: str
    name: str
    module_fqn: str
    line_start: int
    line_end: int
    base_names: list[str] = field(default_factory=list)
    """Raw source text of each base expression, e.g. ``["models.Model"]``."""
    decorators: list[str] = field(default_factory=list)
    method_count: int = 0


@dataclass(slots=True)
class RawCall:
    """An unresolved call site. Resolution happens in pass 2.

    ``kind`` and ``parts`` together describe the callee expression:

    ==========  ======================  ==========================
    kind        parts                   source
    ==========  ======================  ==========================
    ``name``    ``["foo"]``             ``foo()``
    ``attr``    ``["a", "b", "foo"]``   ``a.b.foo()``
    ``self``    ``["foo"]``             ``self.foo()``
    ``self``    ``["db", "commit"]``    ``self.db.commit()``
    ``super``   ``["foo"]``             ``super().foo()``
    ==========  ======================  ==========================
    """

    caller_fqn: str
    module_fqn: str
    line: int
    kind: CallShape
    parts: list[str]
    via: CallVia


@dataclass(slots=True)
class LocalAssignment:
    """A place where the type of a name is knowable without running the code.

    Three sources, in descending order of trust:

    * ``annotation`` — ``def f(u: User)`` or ``u: User = ...``. The annotation
      could name a base class or a Protocol, so this is strong but not certain.
    * ``assignment`` — ``u = User()`` in a function body. Certain, unless the
      name is rebound, in which case the resolver drops it entirely.
    * ``attribute`` — ``self.db = Session()`` in a method, which makes
      ``self.db.commit()`` resolvable from any other method of the class. Here
      ``scope_fqn`` is the *class* FQN and ``var_name`` is the attribute name.
    """

    scope_fqn: str
    var_name: str
    class_expr: str
    """Raw type text, e.g. ``"User"``, ``"models.User"`` or ``"User | None"``."""
    line: int
    source: TypeSource = "assignment"


@dataclass(slots=True)
class ClassAlias:
    """A class-body assignment binding a name to another symbol.

    ``session_class = SecureCookieSession`` in a class body makes
    ``self.session_class(data)`` an instantiation of ``SecureCookieSession``. It is
    the standard "pluggable class" idiom (Flask's ``url_rule_class``,
    ``response_class``, ``json_provider_class`` are all this), and a subclass may
    override the attribute, which is why the resolver scores it below an exact hit.
    """

    class_fqn: str
    name: str
    expr: str
    """Raw right-hand side, e.g. ``"Rule"`` or ``"werkzeug.routing.Rule"``."""
    line: int


@dataclass(slots=True)
class ModuleFacts:
    module_fqn: str
    path: str
    loc: int
    is_package: bool
    is_test: bool
    imports: list[ImportRecord] = field(default_factory=list)
    classes: list[ClassDef] = field(default_factory=list)
    functions: list[FunctionDef] = field(default_factory=list)
    calls: list[RawCall] = field(default_factory=list)
    assignments: list[LocalAssignment] = field(default_factory=list)
    class_aliases: list[ClassAlias] = field(default_factory=list)
    module_globals: set[str] = field(default_factory=set)
    star_import_targets: list[str] = field(default_factory=list)
    has_main_guard: bool = False
    dunder_all: list[str] = field(default_factory=list)
    parse_error: str | None = None


# ---------------------------------------------------------------------------
# Pass 2 — resolved edges
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class CallEdge:
    caller_fqn: str
    callee_fqn: str
    line: int
    confidence: float
    resolution: Resolution
    via: CallVia


@dataclass(slots=True, frozen=True)
class InstantiationEdge:
    caller_fqn: str
    class_fqn: str
    line: int
    confidence: float


@dataclass(slots=True, frozen=True)
class InheritEdge:
    child_fqn: str
    parent_fqn: str
    position: int


@dataclass(slots=True, frozen=True)
class ImportEdge:
    source_fqn: str
    target_fqn: str
    line: int
    kind: ImportKind
    alias: str
    is_type_only: bool
    is_deferred: bool


@dataclass(slots=True)
class ResolutionStats:
    """Counters the pipeline publishes verbatim in the README and at
    ``GET /api/repos/{id}/stats``. An honest breakdown is the point."""

    call_sites_total: int = 0
    resolved_exact: int = 0
    resolved_inferred: int = 0
    resolved_ambiguous: int = 0
    dropped_builtin: int = 0
    dropped_external: int = 0
    dropped_too_ambiguous: int = 0
    unresolved: int = 0

    @property
    def resolved_total(self) -> int:
        return self.resolved_exact + self.resolved_inferred + self.resolved_ambiguous

    @property
    def internal_call_sites(self) -> int:
        """Call sites that could possibly target code in this repository.

        Calls to ``len()`` or to ``requests.get()`` are not failures of the
        resolver — they have no in-repo target to find. Excluding them gives the
        number that actually measures how well resolution works.
        """
        return self.call_sites_total - self.dropped_builtin - self.dropped_external

    @property
    def exact_rate(self) -> float:
        return self.resolved_exact / self.call_sites_total if self.call_sites_total else 0.0

    @property
    def internal_exact_rate(self) -> float:
        internal = self.internal_call_sites
        return self.resolved_exact / internal if internal else 0.0

    @property
    def resolution_rate(self) -> float:
        return self.resolved_total / self.call_sites_total if self.call_sites_total else 0.0

    @property
    def internal_resolution_rate(self) -> float:
        internal = self.internal_call_sites
        return self.resolved_total / internal if internal else 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "call_sites_total": self.call_sites_total,
            "resolved_exact": self.resolved_exact,
            "resolved_inferred": self.resolved_inferred,
            "resolved_ambiguous": self.resolved_ambiguous,
            "dropped_builtin": self.dropped_builtin,
            "dropped_external": self.dropped_external,
            "dropped_too_ambiguous": self.dropped_too_ambiguous,
            "unresolved": self.unresolved,
            "internal_call_sites": self.internal_call_sites,
            "exact_rate": round(self.exact_rate, 4),
            "resolution_rate": round(self.resolution_rate, 4),
            "internal_exact_rate": round(self.internal_exact_rate, 4),
            "internal_resolution_rate": round(self.internal_resolution_rate, 4),
        }


@dataclass(slots=True)
class ResolvedRepo:
    """Everything the writer needs, and nothing it does not."""

    modules: list[ModuleFacts]
    calls: list[CallEdge]
    instantiations: list[InstantiationEdge]
    inherits: list[InheritEdge]
    imports: list[ImportEdge]
    stats: ResolutionStats
