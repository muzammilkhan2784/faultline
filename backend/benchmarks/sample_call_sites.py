"""Draw a reproducible random sample of call sites for hand-labelling.

    python benchmarks/sample_call_sites.py PATH_TO_REPO --under src/flask --n 70

Methodology, because the accuracy number is only as honest as this step:

* The sample is a **seeded uniform draw** over every call site in the chosen part
  of the repo (builtins excluded, since they are dropped by design). It is not
  cherry-picked, so it includes the hard cases.
* The tool prints **only source context**. It never shows what Faultline resolved
  a site to. Labels are written by reading the code; if they were copied from the
  tool's output, measured precision would be circular and worthless.
"""

from __future__ import annotations

import argparse
import builtins
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faultline.accuracy import callee_text
from faultline.parsing.discovery import discover
from faultline.parsing.module_parser import parse_file

BUILTINS = frozenset(dir(builtins))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path)
    ap.add_argument("--under", default="", help="only sample files under this relative path")
    ap.add_argument("--n", type=int, default=70)
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--context", type=int, default=3, help="lines of context either side")
    ap.add_argument("--skip", type=int, default=0, help="skip the first N of the sample")
    ap.add_argument("--take", type=int, default=0, help="show only this many (0 = all)")
    args = ap.parse_args()

    root = args.repo.resolve()
    sites = []
    for discovered in discover(root):
        if args.under and not discovered.rel_path.startswith(args.under):
            continue
        facts = parse_file(discovered)
        for call in facts.calls:
            if call.kind == "name" and call.parts[0] in BUILTINS:
                continue
            sites.append((discovered.rel_path, call.line, callee_text(call)))

    sites = sorted(set(sites))
    rng = random.Random(args.seed)
    sample = rng.sample(sites, min(args.n, len(sites)))
    sample.sort()

    print(f"# {len(sites)} candidate call sites; sampling {len(sample)} (seed {args.seed})\n")
    chosen = sample[args.skip :]
    if args.take:
        chosen = chosen[: args.take]
    for index, (rel, line, text) in enumerate(chosen, start=args.skip + 1):
        lines = (root / rel).read_text(encoding="utf-8").splitlines()
        lo, hi = max(0, line - 1 - args.context), min(len(lines), line + args.context)
        print(f"=== [{index}] {rel}:{line}   callee: {text}")
        for number in range(lo, hi):
            marker = ">>" if number == line - 1 else "  "
            print(f"{marker}{number + 1:5d} {lines[number]}")
        print()


if __name__ == "__main__":
    main()
