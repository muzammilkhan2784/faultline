from __future__ import annotations

import sys

from faultline.analysis.cycles import (
    build_graph,
    find_cycles,
    import_cycles,
    order_cycle,
    tarjan_scc,
)


def normalise(components: list[list[str]]) -> set[frozenset[str]]:
    return {frozenset(c) for c in components}


def test_finds_a_three_module_cycle() -> None:
    (cycle,) = find_cycles({"a": ["b"], "b": ["c"], "c": ["a"]})
    assert set(cycle) == {"a", "b", "c"}


def test_ignores_a_dag() -> None:
    graph = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
    assert find_cycles(graph) == []


def test_two_independent_cycles_are_both_found() -> None:
    graph = {"a": ["b"], "b": ["a"], "x": ["y"], "y": ["z"], "z": ["x"], "solo": ["a"]}
    cycles = find_cycles(graph)
    assert normalise(cycles) == {frozenset("ab"), frozenset("xyz")}


def test_self_import_is_a_cycle_of_one() -> None:
    assert find_cycles({"a": ["a"], "b": []}) == [["a"]]


def test_a_module_that_merely_touches_a_cycle_is_not_in_it() -> None:
    graph = {"a": ["b"], "b": ["a"], "outside": ["a"], "downstream": []}
    (cycle,) = find_cycles(graph)
    assert set(cycle) == {"a", "b"}


def test_tarjan_returns_every_component_including_singletons() -> None:
    components = tarjan_scc({"a": ["b"], "b": []})
    assert normalise(components) == {frozenset("a"), frozenset("b")}


def test_neighbour_missing_from_the_graph_keys_is_tolerated() -> None:
    assert find_cycles({"a": ["ghost"]}) == []


def test_cycle_is_ordered_as_a_loop() -> None:
    graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
    assert order_cycle(["c", "a", "b"], graph) == ["a", "b", "c"]


def test_iterative_tarjan_survives_a_chain_deeper_than_the_recursion_limit() -> None:
    """The reason this is iterative: a recursive version dies on a big repo."""
    depth = sys.getrecursionlimit() * 5
    graph = {f"m{i}": [f"m{i + 1}"] for i in range(depth)}
    graph[f"m{depth}"] = ["m0"]  # close the loop: one giant cycle
    (cycle,) = find_cycles(graph)
    assert len(cycle) == depth + 1


def test_iterative_tarjan_on_a_deep_acyclic_chain() -> None:
    depth = sys.getrecursionlimit() * 5
    graph = {f"m{i}": [f"m{i + 1}"] for i in range(depth)}
    graph[f"m{depth}"] = []
    assert find_cycles(graph) == []


def test_cycles_sort_largest_first() -> None:
    graph = {"a": ["b"], "b": ["a"], "x": ["y"], "y": ["z"], "z": ["x"]}
    assert [len(c) for c in find_cycles(graph)] == [3, 2]


def test_build_graph_dedupes_and_registers_targets() -> None:
    graph = build_graph(
        [
            {"source": "a", "target": "b"},
            {"source": "a", "target": "b"},
            {"source": "b", "target": "c"},
        ]
    )
    assert graph == {"a": ["b"], "b": ["c"], "c": []}


def test_import_cycles_reads_edges_through_the_client() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.params: dict[str, object] = {}

        def read(self, query: str, **params: object) -> list[dict[str, str]]:
            self.params = params
            return [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}]

    client = FakeClient()
    result = import_cycles(client, "repo-1")  # type: ignore[arg-type]
    assert result == [{"modules": ["a", "b"], "size": 2, "is_self_import": False}]
    assert client.params == {"repo_id": "repo-1", "include_deferred": False}
