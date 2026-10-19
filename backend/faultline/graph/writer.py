"""Batched ingestion into Neo4j.

Two rules govern this module.

**Never write node by node.** A 90k-line repository produces on the order of
150k edges. One statement per edge is tens of thousands of round trips;
``UNWIND`` over batches of a thousand is a few dozen. That difference is a
benchmark number, not a micro-optimisation.

**Write nodes before the relationships that reference them.** Relationship
statements use ``MATCH`` on both endpoints, so anything written out of order is
silently dropped instead of loudly failing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from faultline.graph import cypher
from faultline.graph.client import GraphClient
from faultline.logging import get_logger
from faultline.models import ModuleFacts, ResolvedRepo

logger = get_logger(__name__)

Row = dict[str, Any]


@dataclass(slots=True)
class WriteResult:
    seconds: float = 0.0
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def nodes(self) -> int:
        return sum(self.counts.get(k, 0) for k in ("modules", "classes", "functions"))

    @property
    def edges(self) -> int:
        return sum(self.counts.get(k, 0) for k in ("imports", "inherits", "instantiates", "calls"))


# ---------------------------------------------------------------------------
# row builders — pure functions, unit-testable without a database
# ---------------------------------------------------------------------------


def module_rows(repo_id: str, modules: list[ModuleFacts]) -> list[Row]:
    return [
        {
            "repo_id": repo_id,
            "fqn": m.module_fqn,
            "props": {
                "repo_id": repo_id,
                "fqn": m.module_fqn,
                "name": m.module_fqn.rsplit(".", 1)[-1],
                "path": m.path,
                "loc": m.loc,
                "is_package": m.is_package,
                "is_test": m.is_test,
                "parse_error": m.parse_error,
            },
        }
        for m in modules
    ]


def class_rows(repo_id: str, modules: list[ModuleFacts]) -> list[Row]:
    return [
        {
            "repo_id": repo_id,
            "fqn": c.fqn,
            "module_fqn": c.module_fqn,
            "props": {
                "repo_id": repo_id,
                "fqn": c.fqn,
                "name": c.name,
                "module_fqn": c.module_fqn,
                "line_start": c.line_start,
                "line_end": c.line_end,
                "base_names": c.base_names,
                "decorators": c.decorators,
                "method_count": c.method_count,
            },
        }
        for m in modules
        for c in m.classes
    ]


def function_rows(repo_id: str, modules: list[ModuleFacts]) -> list[Row]:
    return [
        {
            "repo_id": repo_id,
            "fqn": f.fqn,
            "module_fqn": f.module_fqn,
            "props": {
                "repo_id": repo_id,
                "fqn": f.fqn,
                "name": f.name,
                "module_fqn": f.module_fqn,
                "class_fqn": f.class_fqn,
                "line_start": f.line_start,
                "line_end": f.line_end,
                "is_method": f.is_method,
                "is_async": f.is_async,
                "is_private": f.is_private,
                "is_dunder": f.is_dunder,
                "is_test": f.is_test,
                "is_entrypoint": f.is_entrypoint,
                "param_count": f.param_count,
                "complexity": f.complexity,
                "decorators": f.decorators,
                "doc": f.doc,
            },
        }
        for m in modules
        for f in m.functions
    ]


def method_link_rows(repo_id: str, modules: list[ModuleFacts]) -> list[Row]:
    return [
        {"repo_id": repo_id, "fqn": f.fqn, "class_fqn": f.class_fqn}
        for m in modules
        for f in m.functions
        if f.class_fqn is not None
    ]


def call_rows(repo_id: str, resolved: ResolvedRepo) -> list[Row]:
    return [
        {
            "repo_id": repo_id,
            "caller": e.caller_fqn,
            "callee": e.callee_fqn,
            "line": e.line,
            "confidence": e.confidence,
            "resolution": e.resolution,
            "via": e.via,
        }
        for e in resolved.calls
    ]


def import_rows(repo_id: str, resolved: ResolvedRepo) -> list[Row]:
    return [
        {
            "repo_id": repo_id,
            "source": e.source_fqn,
            "target": e.target_fqn,
            "line": e.line,
            "kind": e.kind,
            "alias": e.alias,
            "is_type_only": e.is_type_only,
            "is_deferred": e.is_deferred,
        }
        for e in resolved.imports
    ]


def inherit_rows(repo_id: str, resolved: ResolvedRepo) -> list[Row]:
    return [
        {"repo_id": repo_id, "child": e.child_fqn, "parent": e.parent_fqn, "position": e.position}
        for e in resolved.inherits
    ]


def instantiation_rows(repo_id: str, resolved: ResolvedRepo) -> list[Row]:
    return [
        {
            "repo_id": repo_id,
            "caller": e.caller_fqn,
            "class_fqn": e.class_fqn,
            "line": e.line,
            "confidence": e.confidence,
        }
        for e in resolved.instantiations
    ]


# ---------------------------------------------------------------------------
# the writer
# ---------------------------------------------------------------------------


class GraphWriter:
    def __init__(self, client: GraphClient) -> None:
        self.client = client

    def upsert_repo(self, repo_id: str, props: dict[str, Any]) -> None:
        self.client.write(cypher.WRITE_REPO, id=repo_id, props={"id": repo_id, **props})

    def set_status(self, repo_id: str, status: str, **props: Any) -> None:
        self.client.write(cypher.UPDATE_REPO_STATUS, id=repo_id, status=status, props=props)

    def write_repo(self, repo_id: str, resolved: ResolvedRepo) -> WriteResult:
        """Write a fully resolved repository, nodes first, in dependency order."""
        started = time.perf_counter()
        counts: dict[str, int] = {}

        counts["modules"] = self.client.write_batches(
            cypher.WRITE_MODULES, module_rows(repo_id, resolved.modules)
        )
        counts["classes"] = self.client.write_batches(
            cypher.WRITE_CLASSES, class_rows(repo_id, resolved.modules)
        )
        counts["functions"] = self.client.write_batches(
            cypher.WRITE_FUNCTIONS, function_rows(repo_id, resolved.modules)
        )
        counts["method_links"] = self.client.write_batches(
            cypher.WRITE_METHOD_LINKS, method_link_rows(repo_id, resolved.modules)
        )
        counts["imports"] = self.client.write_batches(
            cypher.WRITE_IMPORTS, import_rows(repo_id, resolved)
        )
        counts["inherits"] = self.client.write_batches(
            cypher.WRITE_INHERITS, inherit_rows(repo_id, resolved)
        )
        counts["instantiates"] = self.client.write_batches(
            cypher.WRITE_INSTANTIATES, instantiation_rows(repo_id, resolved)
        )
        counts["calls"] = self.client.write_batches(
            cypher.WRITE_CALLS, call_rows(repo_id, resolved)
        )

        result = WriteResult(seconds=round(time.perf_counter() - started, 3), counts=counts)
        logger.info(
            "repo written",
            extra={"repo_id": repo_id, "seconds": result.seconds, **counts},
        )
        return result

    def delete_repo(self, repo_id: str, *, batch_size: int = 10_000) -> None:
        """Remove every node belonging to a repo, in bounded transactions."""
        self.client.run_autocommit(cypher.delete_repo_nodes_query(batch_size), repo_id=repo_id)
        self.client.write(cypher.DELETE_REPO_ROOT, repo_id=repo_id)
        logger.info("repo deleted", extra={"repo_id": repo_id})
