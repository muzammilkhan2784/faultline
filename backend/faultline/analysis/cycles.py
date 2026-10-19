"""Import cycle detection.

A circular import is a strongly connected component in the module import graph,
so Tarjan's algorithm finds every one of them in a single linear pass.

Two decisions worth knowing about:

**It runs in Python, not in Cypher.** Fetching the import edges is one cheap
query; the algorithm itself is clearer, faster and testable without a database.

**It is iterative.** The textbook Tarjan is recursive, and a recursive version
hits Python's recursion limit on a repository the size of Django — which is
exactly where you want cycle detection to work.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from faultline.graph import cypher
from faultline.graph.client import GraphClient

Graph = Mapping[str, Sequence[str]]


def tarjan_scc(graph: Graph) -> list[list[str]]:
    """Every strongly connected component, in reverse topological order.

    The explicit work stack replaces recursion: each entry is
    ``(node, next_neighbour_index)``, so a suspended node resumes exactly where
    it left off after its child finishes.
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    for root in graph:
        if root in index_of:
            continue

        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            node, next_index = work[-1]

            if next_index == 0:
                index_of[node] = counter
                lowlink[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)

            descended = False
            neighbours = graph.get(node, ())
            for position in range(next_index, len(neighbours)):
                neighbour = neighbours[position]
                if neighbour not in index_of:
                    work[-1] = (node, position + 1)
                    work.append((neighbour, 0))
                    descended = True
                    break
                if neighbour in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[neighbour])
            if descended:
                continue

            if lowlink[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])

    return components


def build_graph(edges: Iterable[Mapping[str, str]]) -> dict[str, list[str]]:
    """``[{"source": a, "target": b}, ...]`` → adjacency list."""
    graph: dict[str, list[str]] = {}
    for edge in edges:
        source, target = edge["source"], edge["target"]
        graph.setdefault(source, [])
        graph.setdefault(target, [])
        if target not in graph[source]:
            graph[source].append(target)
    return graph


def order_cycle(component: Sequence[str], graph: Graph) -> list[str]:
    """Order a component so it reads as a loop: ``a → b → c → a``.

    A component is just a set; walking one actual cycle through it turns the
    result into something a person can follow back to the offending import.
    """
    members = set(component)
    start = min(members)
    path: list[str] = [start]
    visited = {start}
    current = start
    while True:
        step = next(
            (n for n in sorted(graph.get(current, ())) if n in members and n not in visited),
            None,
        )
        if step is None:
            break
        path.append(step)
        visited.add(step)
        current = step
    return path


def find_cycles(graph: Graph) -> list[list[str]]:
    """Real cycles only: components of more than one module, plus self-imports."""
    cycles: list[list[str]] = []
    for component in tarjan_scc(graph):
        if len(component) > 1:
            cycles.append(order_cycle(component, graph))
        elif component and component[0] in graph.get(component[0], ()):
            cycles.append([component[0]])
    cycles.sort(key=lambda c: (-len(c), c[0]))
    return cycles


def import_cycles(
    client: GraphClient, repo_id: str, *, include_deferred: bool = False
) -> list[dict[str, Any]]:
    """Cycles in a repo's import graph.

    ``TYPE_CHECKING`` and function-local imports are excluded by default,
    because both are the standard *fix* for a circular import rather than a
    cause of one. Counting them would report cycles that do not exist at
    runtime — and would flag every codebase that has already done the right
    thing.
    """
    edges = client.read(cypher.IMPORT_EDGES, repo_id=repo_id, include_deferred=include_deferred)
    graph = build_graph(edges)
    return [
        {"modules": cycle, "size": len(cycle), "is_self_import": len(cycle) == 1}
        for cycle in find_cycles(graph)
    ]
