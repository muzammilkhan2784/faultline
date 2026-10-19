"""Command line entry point: analyse a repository without a database.

    faultline analyze PATH              # human-readable summary
    faultline analyze PATH --json       # machine-readable
    faultline analyze PATH --impact pkg.mod.func --depth 3

Everything here runs on :func:`faultline.ingest.pipeline.analyse_path`, which
needs no Neo4j, so this works on a laptop with nothing installed but Python.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from faultline.analysis.cycles import build_graph, find_cycles
from faultline.config import DEFAULT_EXCLUDE_DIRS
from faultline.ingest.pipeline import Analysis, analyse_path
from faultline.ingest.repo_source import explain_no_python_files
from faultline.logging import configure_logging


def reverse_reach(
    analysis: Analysis, target: str, depth: int, min_confidence: float
) -> list[tuple[str, int]]:
    """Callers of ``target`` within ``depth`` hops, nearest first (pure Python)."""
    reverse: dict[str, set[str]] = defaultdict(set)
    for edge in analysis.resolved.calls:
        if edge.confidence >= min_confidence:
            reverse[edge.callee_fqn].add(edge.caller_fqn)

    seen: dict[str, int] = {}
    frontier = {target}
    for hop in range(1, depth + 1):
        frontier = {c for t in frontier for c in reverse.get(t, ())} - set(seen) - {target}
        for caller in frontier:
            seen[caller] = hop
    return sorted(seen.items(), key=lambda item: (item[1], item[0]))


def summarise(analysis: Analysis) -> dict[str, object]:
    stats = analysis.stats
    runtime_imports = build_graph(
        {"source": e.source_fqn, "target": e.target_fqn}
        for e in analysis.resolved.imports
        if not (e.is_type_only or e.is_deferred)
    )
    return {
        "files": analysis.file_count,
        "loc": analysis.loc,
        "parse_failures": len(analysis.parse_failures),
        "nodes": analysis.node_count,
        "edges": analysis.edge_count,
        "parse_seconds": analysis.parse_seconds,
        "resolve_seconds": analysis.resolve_seconds,
        "resolution": stats.to_dict(),
        "import_cycles": [c for c in find_cycles(runtime_imports) if len(c) > 1],
    }


def render(summary: dict[str, object]) -> str:
    resolution = summary["resolution"]
    assert isinstance(resolution, dict)
    lines = [
        f"Parsed {summary['files']:,} files ({summary['loc']:,} lines) "
        f"in {summary['parse_seconds']}s; resolved in {summary['resolve_seconds']}s",
        f"Graph: {summary['nodes']:,} nodes, {summary['edges']:,} edges",
    ]
    if summary["parse_failures"]:
        lines.append(f"  ({summary['parse_failures']} file(s) failed to parse)")
    lines += [
        "",
        f"Call sites: {resolution['call_sites_total']:,}",
        f"  exact       {resolution['resolved_exact']:>7,}",
        f"  inferred    {resolution['resolved_inferred']:>7,}",
        f"  ambiguous   {resolution['resolved_ambiguous']:>7,}",
        f"  builtin     {resolution['dropped_builtin']:>7,}  (dropped)",
        f"  external    {resolution['dropped_external']:>7,}  (dropped)",
        f"  too vague   {resolution['dropped_too_ambiguous']:>7,}  (dropped)",
        f"  unresolved  {resolution['unresolved']:>7,}",
        f"In-repo resolution rate: {resolution['internal_resolution_rate']:.1%} "
        f"({resolution['internal_exact_rate']:.1%} exact)",
    ]
    cycles = summary["import_cycles"]
    assert isinstance(cycles, list)
    lines.append("")
    lines.append(f"Import cycles: {len(cycles)}")
    lines += [f"  {' -> '.join([*c, c[0]])}" for c in cycles[:5]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="faultline", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="analyse a repository on disk")
    analyze.add_argument("path", type=Path)
    analyze.add_argument("--json", action="store_true", help="emit JSON instead of text")
    analyze.add_argument("--impact", metavar="FQN", help="also list what would be affected")
    analyze.add_argument("--depth", type=int, default=3)
    analyze.add_argument("--min-confidence", type=float, default=0.5)
    args = parser.parse_args(argv)

    if not args.path.is_dir():
        print(f"error: {args.path} is not a directory", file=sys.stderr)
        return 2

    configure_logging("WARNING", json_output=False)
    analysis = analyse_path(args.path, progress_every=0)
    if analysis.file_count == 0:
        print(f"error: {explain_no_python_files(args.path, DEFAULT_EXCLUDE_DIRS)}", file=sys.stderr)
        return 1
    summary = summarise(analysis)

    impact = None
    if args.impact:
        known = {f.fqn for m in analysis.resolved.modules for f in m.functions}
        if args.impact not in known:
            print(f"error: no function named {args.impact!r}", file=sys.stderr)
            return 1
        impact = reverse_reach(analysis, args.impact, args.depth, args.min_confidence)

    if args.json:
        payload = dict(summary)
        if impact is not None:
            payload["impact"] = [{"fqn": fqn, "distance": d} for fqn, d in impact]
        print(json.dumps(payload, indent=2))
        return 0

    print(render(summary))
    if impact is not None:
        print(f"\nIf you change {args.impact}, {len(impact)} function(s) can be affected:")
        for fqn, distance in impact[:25]:
            print(f"  {distance}  {fqn}")
        if len(impact) > 25:
            print(f"  ... and {len(impact) - 25} more")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
