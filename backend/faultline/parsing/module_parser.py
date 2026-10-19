"""Pass 1: one Python file in, one :class:`ModuleFacts` out.

The visitor walks the AST **explicitly** rather than leaning on
``generic_visit`` everywhere, because scope matters: a decorator expression is
evaluated outside the function it decorates, a default argument is evaluated in
the enclosing scope, and a nested ``def`` must not leak its branches into the
complexity score of its parent. Getting those three right is most of the work.
"""

from __future__ import annotations

import ast
from pathlib import Path

from faultline.models import (
    CallShape,
    CallVia,
    ClassAlias,
    ClassDef,
    FunctionDef,
    ImportRecord,
    LocalAssignment,
    ModuleFacts,
    RawCall,
    TypeSource,
)
from faultline.parsing.complexity import cyclomatic_complexity
from faultline.parsing.discovery import DiscoveredFile, count_loc
from faultline.parsing.docstring import summarise
from faultline.parsing.imports import (
    is_type_checking_test,
    records_from_import,
    records_from_import_from,
)
from faultline.parsing.scope import ScopeStack

#: ``self`` by convention, ``cls`` in a classmethod — both name the owning class.
SELF_NAMES: frozenset[str] = frozenset({"self", "cls"})

#: A decorator whose trailing attribute is one of these registers the function
#: with a framework, so *something* calls it even though nothing in the repo does.
ENTRYPOINT_DECORATORS: frozenset[str] = frozenset(
    {
        "route",
        "websocket",
        "task",
        "shared_task",
        "command",
        "group",
        "fixture",
        "property",
        "setter",
        "getter",
        "deleter",
        "abstractmethod",
        "abstractproperty",
        "overload",
        "errorhandler",
        "before_request",
        "after_request",
        "teardown_request",
        "template_filter",
        "app_template_filter",
        "context_processor",
        "receiver",
        "hookimpl",
        "validator",
        "field_validator",
        "model_validator",
        "step",
        "cli",
        "main",
        "setup",
        "teardown",
    }
)

#: HTTP verbs only count as entrypoints when hung off something router-shaped,
#: so an ordinary ``@cache.get`` is not mistaken for a route.
HTTP_VERBS: frozenset[str] = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
ROUTER_PREFIXES: frozenset[str] = frozenset(
    {"app", "router", "api", "bp", "blueprint", "rt", "rest", "web", "server"}
)

#: Annotation wrappers that still describe the variable itself.
OPTIONAL_WRAPPERS: frozenset[str] = frozenset({"Optional", "typing.Optional"})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def flatten_callee(expr: ast.expr) -> tuple[CallShape, list[str]] | None:
    """Reduce a callee expression to a shape the resolver can work with.

    Returns ``None`` for anything whose base is not a plain name: ``f()()``,
    ``d["k"]()``, ``(a or b).c()``. Those are genuinely unresolvable statically,
    and Faultline would rather emit no edge than a wrong one.
    """
    if isinstance(expr, ast.Name):
        return "name", [expr.id]

    if not isinstance(expr, ast.Attribute):
        return None

    parts: list[str] = []
    node: ast.expr = expr
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    parts.reverse()

    if isinstance(node, ast.Name):
        if node.id in SELF_NAMES:
            return "self", parts
        return "attr", [node.id, *parts]

    # ``super().method()`` is resolvable by walking the base classes.
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super":
        return "super", parts

    return None


def annotation_to_type_expr(annotation: ast.expr) -> str | None:
    """Best-effort answer to "what class does this annotation describe?".

    Deliberately conservative. ``User``, ``Optional[User]`` and ``User | None``
    all describe a ``User``; ``list[User]`` describes a list, so it yields
    nothing. String annotations are unwrapped so that ``"User"`` works under
    ``from __future__ import annotations``.
    """
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None

    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return ast.unparse(annotation)

    # ``User | None``
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        for side in (annotation.left, annotation.right):
            if isinstance(side, ast.Constant) and side.value is None:
                continue
            return annotation_to_type_expr(side)
        return None

    # ``Optional[User]``
    if isinstance(annotation, ast.Subscript):
        base = ast.unparse(annotation.value)
        if base in OPTIONAL_WRAPPERS or base.endswith(".Optional"):
            return annotation_to_type_expr(annotation.slice)
    return None


def decorator_callee(decorator: ast.expr) -> ast.expr:
    """``@app.route("/")`` is a call; the thing being *used* is ``app.route``."""
    return decorator.func if isinstance(decorator, ast.Call) else decorator


def is_entrypoint_decorator(text: str) -> bool:
    """True when a decorator means "some framework will call this"."""
    head = text.split("(", 1)[0].strip()
    segments = head.split(".")
    last = segments[-1]
    if last in ENTRYPOINT_DECORATORS:
        return True
    if last in HTTP_VERBS and len(segments) >= 2:
        return segments[0].lower() in ROUTER_PREFIXES or segments[-2].lower() in ROUTER_PREFIXES
    return False


def param_names(args: ast.arguments) -> list[str]:
    names = [a.arg for a in (*args.posonlyargs, *args.args)]
    if args.vararg:
        names.append(args.vararg.arg)
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


def annotated_params(args: ast.arguments) -> list[tuple[str, ast.expr]]:
    return [
        (a.arg, a.annotation)
        for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        if a.annotation is not None
    ]


def is_main_guard(test: ast.expr) -> bool:
    """Recognise ``if __name__ == "__main__":``."""
    if not isinstance(test, ast.Compare) or not isinstance(test.left, ast.Name):
        return False
    if test.left.id != "__name__":
        return False
    return any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)


def _is_bare_super(node: ast.Call) -> bool:
    """``super()`` on its own is plumbing, not a call worth an edge.

    ``super().method()`` is already captured as a ``super`` shape, so counting
    the inner ``super()`` too would inflate the call-site total with a builtin
    that gets dropped a moment later.
    """
    return isinstance(node.func, ast.Name) and node.func.id == "super"


def _string_list(node: ast.expr) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return []
    return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]


# ---------------------------------------------------------------------------
# the visitor
# ---------------------------------------------------------------------------


class ModuleVisitor(ast.NodeVisitor):
    """Collects every fact pass 2 will need from a single module."""

    def __init__(self, facts: ModuleFacts) -> None:
        self.facts = facts
        self.stack = ScopeStack(facts.module_fqn)
        self._type_checking_depth = 0
        self._function_depth = 0
        # Annotated parameter types of the function currently being walked, so
        # that `self.client = client` can inherit the annotation on `client`.
        self._param_types: list[dict[str, str]] = [{}]

    # -- imports -------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        self._add_imports(
            records_from_import(
                node,
                is_type_only=self._type_checking_depth > 0,
                is_deferred=self._function_depth > 0,
            )
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._add_imports(
            records_from_import_from(
                node,
                current_module_fqn=self.facts.module_fqn,
                is_package=self.facts.is_package,
                is_type_only=self._type_checking_depth > 0,
                is_deferred=self._function_depth > 0,
            )
        )

    def _add_imports(self, records: list[ImportRecord]) -> None:
        for record in records:
            self.facts.imports.append(record)
            if record.kind == "star":
                self.facts.star_import_targets.append(record.module_fqn)
            elif self._function_depth == 0:
                self.facts.module_globals.add(record.local_name)

    # -- branching -----------------------------------------------------------

    def visit_If(self, node: ast.If) -> None:
        if is_main_guard(node.test):
            self.facts.has_main_guard = True

        if is_type_checking_test(node.test):
            # Imports in here do not exist at runtime; everything else does.
            self._type_checking_depth += 1
            for statement in node.body:
                self.visit(statement)
            self._type_checking_depth -= 1
            for statement in node.orelse:
                self.visit(statement)
            return

        self.generic_visit(node)

    # -- definitions ---------------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        fqn = self.stack.child_fqn("class", node.name)
        method_count = sum(
            1 for s in node.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        self.facts.classes.append(
            ClassDef(
                fqn=fqn,
                name=node.name,
                module_fqn=self.facts.module_fqn,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                base_names=[ast.unparse(base) for base in node.bases],
                decorators=[ast.unparse(d) for d in node.decorator_list],
                method_count=method_count,
            )
        )
        if self.stack.in_module_scope():
            self.facts.module_globals.add(node.name)

        # Bases and decorators are evaluated in the *enclosing* scope.
        for base in node.bases:
            self.visit(base)
        for decorator in node.decorator_list:
            self.visit(decorator)

        self.stack.push("class", node.name)
        for statement in node.body:
            self.visit(statement)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node, is_async=True)

    def _handle_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool
    ) -> None:
        fqn = self.stack.child_fqn("function", node.name)
        class_fqn = self.stack.owning_class_fqn()
        names = param_names(node.args)
        decorators = [ast.unparse(d) for d in node.decorator_list]
        is_dunder = node.name.startswith("__") and node.name.endswith("__")

        self.facts.functions.append(
            FunctionDef(
                fqn=fqn,
                name=node.name,
                module_fqn=self.facts.module_fqn,
                class_fqn=class_fqn,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                is_method=class_fqn is not None,
                is_async=is_async,
                is_dunder=is_dunder,
                is_private=node.name.startswith("_") and not is_dunder,
                is_test=self.facts.is_test and node.name.startswith("test_"),
                # ``__all__`` and the main guard are module-wide facts, so the
                # final value is filled in once the whole file has been parsed.
                is_entrypoint=any(is_entrypoint_decorator(d) for d in decorators),
                param_count=len(names),
                param_names=names,
                complexity=cyclomatic_complexity(node),
                decorators=decorators,
                return_type=(
                    annotation_to_type_expr(node.returns) if node.returns is not None else None
                ),
                doc=summarise(ast.get_docstring(node)),
            )
        )
        if self.stack.in_module_scope():
            self.facts.module_globals.add(node.name)

        # A decorator is a real dependency of the function it decorates: change
        # ``login_required`` and every view wearing it is affected. The edge is
        # attributed to the decorated function so reverse reachability finds it.
        for decorator in node.decorator_list:
            self._record_call(decorator_callee(decorator), fqn, decorator.lineno, via="decorator")
            if isinstance(decorator, ast.Call):
                for argument in (*decorator.args, *decorator.keywords):
                    self.visit(argument)

        # Defaults are evaluated in the enclosing scope, at definition time.
        defaults: list[ast.expr] = [*node.args.defaults]
        defaults.extend(d for d in node.args.kw_defaults if d is not None)
        for default in defaults:
            self.visit(default)

        # Parameter annotations are free, accurate type information.
        parameter_types: dict[str, str] = {}
        for name, annotation in annotated_params(node.args):
            type_expr = annotation_to_type_expr(annotation)
            if type_expr and name not in SELF_NAMES:
                parameter_types[name] = type_expr
                self.facts.assignments.append(
                    LocalAssignment(
                        scope_fqn=fqn,
                        var_name=name,
                        class_expr=type_expr,
                        line=node.lineno,
                        source="annotation",
                    )
                )

        self.stack.push("function", node.name)
        self._function_depth += 1
        self._param_types.append(parameter_types)
        for statement in node.body:
            self.visit(statement)
        self._param_types.pop()
        self._function_depth -= 1
        self.stack.pop()

    # -- calls ---------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        caller = self.stack.enclosing_function_fqn()
        if caller is not None and not _is_bare_super(node):
            shape = flatten_callee(node.func)
            if shape is not None:
                via: CallVia = "name" if shape[0] == "name" else "attribute"
                if shape[0] in ("self", "super"):
                    via = "self"
                self._record_call(node.func, caller, node.lineno, via=via)
        self.generic_visit(node)

    def _record_call(self, callee: ast.expr, caller_fqn: str, line: int, *, via: CallVia) -> None:
        shape = flatten_callee(callee)
        if shape is None:
            return
        kind, parts = shape
        self.facts.calls.append(
            RawCall(
                caller_fqn=caller_fqn,
                module_fqn=self.facts.module_fqn,
                line=line,
                kind=kind,
                parts=parts,
                via=via,
            )
        )

    # -- assignments ---------------------------------------------------------

    def _record_class_alias(self, target: ast.expr, value: ast.expr | None, line: int) -> None:
        """``name = Something`` directly in a class body: the pluggable-class idiom."""
        if value is None or not self.stack.in_class_body() or not isinstance(target, ast.Name):
            return
        if not isinstance(value, (ast.Name, ast.Attribute)):
            return
        class_fqn = self.stack.current_fqn()
        self.facts.class_aliases.append(
            ClassAlias(class_fqn=class_fqn, name=target.id, expr=ast.unparse(value), line=line)
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_class_alias(target, node.value, node.lineno)
            self._record_typed_binding(target, node.value, node.lineno)
            if self.stack.in_module_scope() and isinstance(target, ast.Name):
                self.facts.module_globals.add(target.id)
                if target.id == "__all__":
                    self.facts.dunder_all = _string_list(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_class_alias(node.target, node.value, node.lineno)
        type_expr = annotation_to_type_expr(node.annotation)
        if type_expr and isinstance(node.target, ast.Name):
            self._add_local_type(node.target.id, type_expr, node.lineno, source="annotation")
        elif node.value is not None:
            self._record_typed_binding(node.target, node.value, node.lineno)
        if self.stack.in_module_scope() and isinstance(node.target, ast.Name):
            self.facts.module_globals.add(node.target.id)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._record_typed_binding(node.target, node.value, node.lineno)
        self.generic_visit(node)

    def _type_expr_of_value(self, value: ast.expr) -> str | None:
        """The type text a right-hand side implies, if any.

        ``SomeClass()`` gives the class directly. A bare name gives whatever
        that name was annotated as, which is what makes constructor injection —
        ``def __init__(self, client: GraphClient): self.client = client`` —
        resolve every later ``self.client.read()`` exactly.
        """
        if isinstance(value, ast.Call):
            callee = flatten_callee(value.func)
            if callee is None or callee[0] not in ("name", "attr"):
                return None
            return ".".join(callee[1])
        if isinstance(value, ast.Name):
            return self._param_types[-1].get(value.id)
        return None

    def _record_typed_binding(self, target: ast.expr, value: ast.expr, line: int) -> None:
        """Capture ``x = SomeClass()`` and ``self.x = SomeClass()``."""
        class_expr = self._type_expr_of_value(value)
        if class_expr is None:
            return

        if isinstance(target, ast.Name):
            self._add_local_type(target.id, class_expr, line, source="assignment")
            return

        # ``self.db = Session()`` makes ``self.db.commit()`` resolvable from
        # every other method of the class.
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id in SELF_NAMES
        ):
            class_fqn = self.stack.enclosing_class_fqn()
            if class_fqn is not None:
                self.facts.assignments.append(
                    LocalAssignment(
                        scope_fqn=class_fqn,
                        var_name=target.attr,
                        class_expr=class_expr,
                        line=line,
                        source="attribute",
                    )
                )

    def _add_local_type(self, name: str, class_expr: str, line: int, *, source: TypeSource) -> None:
        # Module-level bindings are typed too: `router = APIRouter()` at the top
        # of a file is what makes `router.get(...)` resolvable further down.
        scope = self.stack.enclosing_function_fqn() or self.facts.module_fqn
        self.facts.assignments.append(
            LocalAssignment(
                scope_fqn=scope,
                var_name=name,
                class_expr=class_expr,
                line=line,
                source=source,
            )
        )


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


def parse_source(
    source: str,
    *,
    module_fqn: str,
    path: str = "<string>",
    is_package: bool = False,
    is_test: bool = False,
) -> ModuleFacts:
    """Parse one module. Never raises on bad syntax.

    A real repository always contains a file that will not parse: Python 2
    leftovers, a template with a ``.py`` extension, a fixture broken on purpose.
    Those are recorded on the module and the run continues.
    """
    facts = ModuleFacts(
        module_fqn=module_fqn,
        path=path,
        loc=count_loc(source),
        is_package=is_package,
        is_test=is_test,
    )
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        facts.parse_error = f"{type(exc).__name__}: {exc}"
        return facts

    ModuleVisitor(facts).visit(tree)
    _finalise_entrypoints(facts)
    return facts


def parse_file(discovered: DiscoveredFile) -> ModuleFacts:
    """Read and parse a discovered file, tolerating undecodable bytes."""
    try:
        source = Path(discovered.path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = Path(discovered.path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - filesystem dependent
        facts = ModuleFacts(
            module_fqn=discovered.module_fqn,
            path=discovered.rel_path,
            loc=0,
            is_package=discovered.is_package,
            is_test=discovered.is_test,
        )
        facts.parse_error = f"{type(exc).__name__}: {exc}"
        return facts

    return parse_source(
        source,
        module_fqn=discovered.module_fqn,
        path=discovered.rel_path,
        is_package=discovered.is_package,
        is_test=discovered.is_test,
    )


def _finalise_entrypoints(facts: ModuleFacts) -> None:
    """Apply the module-wide entrypoint rules once the whole file is parsed."""
    exported = set(facts.dunder_all)
    for function in facts.functions:
        if function.is_entrypoint:
            continue
        if (
            function.is_dunder
            or function.name in exported
            or (facts.has_main_guard and function.name == "main")
        ):
            function.is_entrypoint = True
