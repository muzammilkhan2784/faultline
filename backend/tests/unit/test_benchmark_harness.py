"""The benchmark harness is a script, but its logic is tested: a broken harness
that only fails after a ten-minute Django run is the worst kind of bug."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "run_benchmark.py"


@pytest.fixture(scope="module")
def bench() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_benchmark", BENCH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_benchmark"] = module
    spec.loader.exec_module(module)
    return module


def test_every_function_main_uses_is_defined_before_the_entry_point(bench: ModuleType) -> None:
    """Regression: ``run_repo`` was once appended *after* the ``__main__`` guard."""
    source = BENCH.read_text(encoding="utf-8")
    guard = source.index('if __name__ == "__main__"')
    for name in ("run_repo", "run_repo_once", "markdown_table", "measure_impact_latency", "main"):
        assert hasattr(bench, name)
        assert source.index(f"def {name}(") < guard


def test_percentile_nearest_rank(bench: ModuleType) -> None:
    values = [float(i) for i in range(1, 101)]
    assert bench.percentile(values, 50) == 50.0
    assert bench.percentile(values, 95) == 95.0
    assert bench.percentile(values, 99) == 99.0
    assert bench.percentile(values, 100) == 100.0
    assert bench.percentile([], 50) == 0.0
    assert bench.percentile([7.0], 99) == 7.0


def test_run_repo_reports_medians_and_keeps_the_cold_run(
    bench: ModuleType, fixture_root: Path
) -> None:
    timings = iter([9.0, 1.0, 2.0])

    original = bench.run_repo_once

    def fake_once(name: str, root: Path, sha: str | None, client: object, seed: int) -> dict:
        record = original(name, root, sha, None, seed)
        record["parse_seconds"] = next(timings)
        return record

    bench.run_repo_once = fake_once
    try:
        result = bench.run_repo("fixture", fixture_root, "abc", None, 1, runs=3)
    finally:
        bench.run_repo_once = original

    assert result["runs"] == 3
    assert result["parse_seconds"] == 2.0  # the median of 9, 1, 2 -- not the cold 9.0
    assert result["first_run_parse_seconds"] == 9.0
    assert [r["parse_seconds"] for r in result["all_runs"]] == [9.0, 1.0, 2.0]
    assert result["files"] == 16 and result["nodes"] == 55


def test_run_repo_once_without_a_database(bench: ModuleType, fixture_root: Path) -> None:
    record = bench.run_repo_once("fixture", fixture_root, None, None, 1)
    assert record["files"] == 16
    assert record["call_edges"] == 30
    assert "write_seconds" not in record
    assert record["total_seconds"] == round(record["parse_seconds"] + record["resolve_seconds"], 3)


def test_markdown_table_renders_every_column(bench: ModuleType, fixture_root: Path) -> None:
    record = bench.run_repo_once("fixture", fixture_root, None, None, 1)
    record["write_seconds"] = 1.5
    record["total_seconds"] = 2.0
    record["impact_latency"] = {"p50_ms": 3.0, "p95_ms": 9.0, "p99_ms": 20.0}
    table = bench.markdown_table([record])
    assert table.startswith("| Repo | Files | LOC |")
    assert "| fixture | 16 |" in table
    assert "3 / 9 / 20 ms" in table
    assert "1.5s" in table


def test_markdown_table_without_a_database_says_n_a(bench: ModuleType, fixture_root: Path) -> None:
    record = bench.run_repo_once("fixture", fixture_root, None, None, 1)
    table = bench.markdown_table([record])
    assert table.count("n/a") == 2  # write time and latency


def test_targets_cover_the_five_spec_repositories(bench: ModuleType) -> None:
    assert set(bench.TARGETS) == {"flask", "requests", "httpx", "rich", "django"}
    assert all(url.startswith("https://") for url in bench.TARGETS.values())
