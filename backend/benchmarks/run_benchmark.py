"""Benchmark Faultline on real open-source repositories.

    python benchmarks/run_benchmark.py                       # all five repos
    python benchmarks/run_benchmark.py --repos flask requests
    python benchmarks/run_benchmark.py --repos-dir ~/src     # reuse local clones
    python benchmarks/run_benchmark.py --no-neo4j            # analysis only

Writes ``results/<date>.json`` (machine-readable, committed) and
``results/<date>.md`` (the table pasted into the README). Every number comes from
running this script; nothing in the README is typed in by hand.

What is measured, per repository:

* files, lines of code, parse / resolve / write / total seconds (median of 3 runs;
  the cold first-run parse time is kept in the JSON)
* node and edge counts, and the resolution breakdown (including what was dropped)
* latency of 100 random depth-3 impact queries (p50 / p95 / p99), after warm-up

And, once, two micro-benchmarks behind claims the README makes about ingestion:

* batched ``UNWIND`` writes versus one round trip per row
* ``MERGE`` with versus without the composite uniqueness constraint
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faultline.analysis import impact
from faultline.config import Settings
from faultline.graph import cypher
from faultline.graph.client import GraphClient
from faultline.graph.schema import ensure_schema
from faultline.graph.writer import GraphWriter
from faultline.ingest.pipeline import analyse_path
from faultline.ingest.repo_source import SourceError, clone
from faultline.logging import configure_logging

RESULTS_DIR = Path(__file__).parent / "results"

TARGETS: dict[str, str] = {
    "flask": "https://github.com/pallets/flask",
    "requests": "https://github.com/psf/requests",
    "httpx": "https://github.com/encode/httpx",
    "rich": "https://github.com/Textualize/rich",
    "django": "https://github.com/django/django",
}

QUERY_SAMPLES = 100
QUERY_DEPTH = 3
WARMUP = 5


@dataclass(slots=True)
class Latency:
    samples: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    median_result_size: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "samples": self.samples,
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "median_result_size": self.median_result_size,
        }


def percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted list."""
    if not sorted_values:
        return 0.0
    rank = max(1, min(len(sorted_values), math.ceil(pct / 100 * len(sorted_values))))
    return sorted_values[rank - 1]


def measure_impact_latency(client: GraphClient, repo_id: str, seed: int) -> Latency | None:
    rows = client.read(cypher.SAMPLE_FUNCTIONS, repo_id=repo_id, limit=QUERY_SAMPLES + WARMUP)
    fqns = [r["fqn"] for r in rows]
    if len(fqns) < WARMUP + 5:
        return None
    random.Random(seed).shuffle(fqns)
    for fqn in fqns[:WARMUP]:
        impact.impact(client, repo_id, fqn, depth=QUERY_DEPTH)

    timings: list[float] = []
    sizes: list[int] = []
    for fqn in fqns[WARMUP : WARMUP + QUERY_SAMPLES]:
        started = time.perf_counter()
        result = impact.impact(client, repo_id, fqn, depth=QUERY_DEPTH)
        timings.append((time.perf_counter() - started) * 1000)
        sizes.append(len(result))
    timings.sort()
    return Latency(
        samples=len(timings),
        p50_ms=percentile(timings, 50),
        p95_ms=percentile(timings, 95),
        p99_ms=percentile(timings, 99),
        max_ms=timings[-1],
        median_result_size=float(statistics.median(sizes)),
    )


def machine_info(client: GraphClient | None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }
    if client is not None:
        try:
            components = client.read("CALL dbms.components() YIELD name, versions, edition")
            info["neo4j"] = (
                f"{components[0]['name']} {components[0]['versions'][0]} {components[0]['edition']}"
            )
        except Exception:
            info["neo4j"] = "unknown"
    return info


# -- micro-benchmarks ---------------------------------------------------------------------


def bench_batching(client: GraphClient, sample: int = 3000) -> dict[str, Any]:
    """Row-at-a-time writes versus ``UNWIND`` batches, over identical data.

    Both write the same ``CALLS`` edges between the same nodes, so the only
    variable is the number of round trips.
    """
    repo_id = "bench-batching"
    writer = GraphWriter(client)
    writer.delete_repo(repo_id)
    rows = [
        {
            "repo_id": repo_id,
            "caller": f"m.f{i % 500}",
            "callee": f"m.f{(i * 7 + 1) % 500}",
            "line": i,
            "confidence": 1.0,
            "resolution": "exact",
            "via": "name",
        }
        for i in range(sample)
    ]
    node_rows = [
        {
            "repo_id": repo_id,
            "fqn": f"m.f{i}",
            "module_fqn": "m",
            "props": {"repo_id": repo_id, "fqn": f"m.f{i}", "name": f"f{i}", "module_fqn": "m"},
        }
        for i in range(500)
    ]
    client.write("MERGE (:Module {repo_id: $r, fqn: 'm'})", r=repo_id)
    client.write_batches(cypher.WRITE_FUNCTIONS, node_rows)

    started = time.perf_counter()
    for row in rows:
        client.write(cypher.WRITE_CALLS.replace("UNWIND $rows AS row", "WITH $row AS row"), row=row)
    unbatched = time.perf_counter() - started

    client.run_autocommit("MATCH (:Function {repo_id: $r})-[c:CALLS]->() DELETE c", r=repo_id)

    started = time.perf_counter()
    client.write_batches(cypher.WRITE_CALLS, rows, batch_size=1000)
    batched = time.perf_counter() - started

    writer.delete_repo(repo_id)
    return {
        "edges": sample,
        "one_by_one_seconds": round(unbatched, 3),
        "batched_seconds": round(batched, 3),
        "speedup": round(unbatched / batched, 1) if batched else None,
        "batch_size": 1000,
    }


def bench_constraints(client: GraphClient, n: int = 4000) -> dict[str, Any]:
    """``MERGE`` on ``(repo_id, fqn)`` with and without a uniqueness constraint.

    Uses a scratch label so it cannot touch real data. Without the constraint each
    MERGE is a label scan, so total time grows quadratically with the node count.
    """

    def run(label: str, constrained: bool) -> float:
        client.write(f"MATCH (n:{label}) DETACH DELETE n")
        if constrained:
            client.write(
                f"CREATE CONSTRAINT bench_{label.lower()} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE (n.repo_id, n.fqn) IS UNIQUE"
            )
        rows = [{"repo_id": "bench", "fqn": f"pkg.mod{i}"} for i in range(n)]
        query = f"UNWIND $rows AS row MERGE (n:{label} {{repo_id: row.repo_id, fqn: row.fqn}})"
        started = time.perf_counter()
        client.write_batches(query, rows, batch_size=500)
        elapsed = time.perf_counter() - started
        client.write(f"MATCH (n:{label}) DETACH DELETE n")
        if constrained:
            client.write(f"DROP CONSTRAINT bench_{label.lower()} IF EXISTS")
        return elapsed

    without = run("BenchPlain", constrained=False)
    with_constraint = run("BenchIndexed", constrained=True)
    return {
        "nodes": n,
        "without_constraint_seconds": round(without, 3),
        "with_constraint_seconds": round(with_constraint, 3),
        "speedup": round(without / with_constraint, 1) if with_constraint else None,
    }


# -- per-repo run ---------------------------------------------------------------------------


def locate(
    name: str, repos_dir: Path | None, cache_dir: Path, settings: Settings
) -> tuple[Path, str | None]:
    if repos_dir and (repos_dir / name).is_dir():
        sha = None
        head = repos_dir / name / ".git" / "HEAD"
        if head.exists():
            import subprocess

            out = subprocess.run(
                ["git", "-C", str(repos_dir / name), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            sha = out.stdout.strip() or None
        return repos_dir / name, sha
    destination = cache_dir / name
    if (destination / ".git").is_dir():
        import subprocess

        out = subprocess.run(
            ["git", "-C", str(destination), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return destination, out.stdout.strip() or None
    print(f"  cloning {TARGETS[name]} ...", flush=True)
    sha = clone(TARGETS[name], destination, ref=None, settings=settings)
    return destination, sha


def run_repo_once(
    name: str, root: Path, sha: str | None, client: GraphClient | None, seed: int
) -> dict[str, Any]:
    print(f"  analysing {name} ...", flush=True)
    analysis = analyse_path(root, progress_every=0)
    stats = analysis.stats
    record: dict[str, Any] = {
        "name": name,
        "commit": sha,
        "files": analysis.file_count,
        "loc": analysis.loc,
        "parse_failures": len(analysis.parse_failures),
        "parse_seconds": analysis.parse_seconds,
        "resolve_seconds": analysis.resolve_seconds,
        "nodes": analysis.node_count,
        "edges": analysis.edge_count,
        "call_edges": len(analysis.resolved.calls),
        "resolution": stats.to_dict(),
    }

    if client is None:
        record["total_seconds"] = round(analysis.parse_seconds + analysis.resolve_seconds, 3)
        return record

    repo_id = f"bench-{name}"
    writer = GraphWriter(client)
    writer.delete_repo(repo_id)
    writer.upsert_repo(repo_id, {"name": name, "status": "ready", "analyzed_at": "bench"})
    print(f"  writing {name} to Neo4j ...", flush=True)
    write = writer.write_repo(repo_id, analysis.resolved)
    record["write_seconds"] = write.seconds
    record["total_seconds"] = round(
        analysis.parse_seconds + analysis.resolve_seconds + write.seconds, 3
    )
    record["stored_call_edges"] = write.counts["calls"]

    print(f"  timing {QUERY_SAMPLES} impact queries ...", flush=True)
    latency = measure_impact_latency(client, repo_id, seed)
    record["impact_latency"] = latency.to_dict() if latency else None
    writer.delete_repo(repo_id)
    return record


def markdown_table(results: list[dict[str, Any]]) -> str:
    header = (
        "| Repo | Files | LOC | Parse | Resolve | Write | **Total** | Nodes | Edges | "
        "In-repo resolved | Exact | Impact p50 / p95 / p99 |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    rows = []
    for r in results:
        res = r["resolution"]
        lat = r.get("impact_latency")
        latency = (
            f"{lat['p50_ms']:.0f} / {lat['p95_ms']:.0f} / {lat['p99_ms']:.0f} ms" if lat else "n/a"
        )
        write = f"{r['write_seconds']:.1f}s" if "write_seconds" in r else "n/a"
        rows.append(
            f"| {r['name']} | {r['files']:,} | {r['loc']:,} | {r['parse_seconds']:.1f}s | "
            f"{r['resolve_seconds']:.1f}s | {write} | **{r['total_seconds']:.1f}s** | "
            f"{r['nodes']:,} | {r['edges']:,} | {res['internal_resolution_rate']:.1%} | "
            f"{res['internal_exact_rate']:.1%} | {latency} |"
        )
    return header + "\n".join(rows) + "\n"


TIMING_KEYS = ("parse_seconds", "resolve_seconds", "write_seconds", "total_seconds")


def run_repo(
    name: str, root: Path, sha: str | None, client: GraphClient | None, seed: int, runs: int
) -> dict[str, Any]:
    """Run ``runs`` times and report the **median** of each timing.

    A single run is dominated by cold-cache effects: Django's first parse of a fresh
    clone took 52.6s here and ~9s once the files were cached. Reporting either
    extreme as "the" number would be misleading, so the median is the headline and
    the cold first run is recorded alongside it.
    """
    records = [run_repo_once(name, root, sha, client, seed) for _ in range(runs)]
    result = dict(records[-1])
    for key in TIMING_KEYS:
        values = [r[key] for r in records if key in r]
        if values:
            result[key] = round(statistics.median(values), 3)
    result["runs"] = runs
    result["first_run_parse_seconds"] = records[0]["parse_seconds"]
    result["all_runs"] = [{k: r[k] for k in TIMING_KEYS if k in r} for r in records]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repos", nargs="+", default=list(TARGETS), choices=list(TARGETS))
    parser.add_argument("--repos-dir", type=Path, help="reuse existing clones <dir>/<name>")
    parser.add_argument(
        "--cache-dir", type=Path, default=Path(__file__).parents[1] / ".cache" / "bench"
    )
    parser.add_argument("--no-neo4j", action="store_true")
    parser.add_argument("--skip-micro", action="store_true", help="skip the two micro-benchmarks")
    parser.add_argument("--seed", type=int, default=20260919)
    parser.add_argument("--runs", type=int, default=3, help="repeat each repo; report medians")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()

    configure_logging("WARNING", json_output=False)
    settings = Settings()
    client: GraphClient | None = None
    if not args.no_neo4j:
        candidate = GraphClient(settings)
        if candidate.verify():
            ensure_schema(candidate)
            client = candidate
        else:
            print("Neo4j is not reachable; running analysis-only (no write or query numbers).")

    results: list[dict[str, Any]] = []
    for name in args.repos:
        print(f"[{name}]")
        try:
            root, sha = locate(name, args.repos_dir, args.cache_dir, settings)
        except SourceError as exc:
            print(f"  skipped: {exc}")
            continue
        results.append(run_repo(name, root, sha, client, args.seed, args.runs))

    micro: dict[str, Any] = {}
    if client is not None and not args.skip_micro:
        print("[micro-benchmarks]")
        micro["batching"] = bench_batching(client)
        micro["constraints"] = bench_constraints(client)

    stamp = date.today().isoformat()
    payload = {
        "date": stamp,
        "machine": machine_info(client),
        "query": {
            "depth": QUERY_DEPTH,
            "samples": QUERY_SAMPLES,
            "warmup": WARMUP,
            "min_confidence": 0.5,
        },
        "repos": results,
        "micro": micro,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{stamp}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    table = markdown_table(results)
    (args.out / f"{stamp}.md").write_text(table, encoding="utf-8")
    print("\n" + table)
    if micro:
        print(json.dumps(micro, indent=2))
    if client is not None:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
