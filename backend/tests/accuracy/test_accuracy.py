"""The accuracy harness: 142 hand-labelled call sites, and the CI gate on them.

This is the test that lets the README state a *measured* precision. If resolution
quality regresses -- a new heuristic that guesses too eagerly, say -- this fails
the build.

Where the repositories come from:

* ``fixture`` is in the repo and always runs.
* ``flask`` and ``requests`` are fetched at the exact commits the labels were
  written against (line numbers would otherwise drift). Set
  ``FAULTLINE_REPOS_DIR`` to a directory already holding ``flask/`` and
  ``requests/`` checkouts to skip the download. In CI, ``FAULTLINE_REQUIRE_REPOS=1``
  turns "could not fetch" into a hard failure instead of a skip, so the gate can
  never quietly stop gating.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from faultline.accuracy import Report, SiteResult, evaluate, load_labels
from faultline.ingest.pipeline import analyse_path
from faultline.ingest.repo_source import SourceError, fetch_commit

pytestmark = pytest.mark.accuracy

HERE = Path(__file__).parent
BACKEND = HERE.parents[1]
LABELS = HERE / "labelled_calls.json"

#: The gates. The spec's floor is 0.85 precision. Measured performance is higher
#: (see docs/ACCURACY.md), and a gate sitting 15 points below reality would wave
#: a real regression through -- a mutation test that made every ambiguous guess
#: fully confident still scored 0.94 -- so the default-view gate is set tight.
MIN_PRECISION = 0.85  # every emitted edge, however low its confidence
MIN_PRECISION_DEFAULT = 0.95  # what a person sees at the default 0.5 threshold
MIN_RECALL = 0.90

REPOS, ALL_LABELS = load_labels(LABELS)


def _require_repos() -> bool:
    return os.environ.get("FAULTLINE_REQUIRE_REPOS") == "1"


def _checkout_matches(path: Path, commit: str) -> bool:
    try:
        head = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return head.startswith(commit[:7]) or commit.startswith(head[:7])


def locate_repo(name: str) -> Path:
    meta = REPOS[name]
    if "path" in meta:
        return BACKEND / meta["path"]

    supplied = os.environ.get("FAULTLINE_REPOS_DIR")
    if supplied and (Path(supplied) / name).is_dir():
        found = Path(supplied) / name
        if not _checkout_matches(found, meta["commit"]):
            pytest.fail(
                f"{found} is not at commit {meta['commit'][:12]}; the labels' line numbers "
                "would not match"
            )
        return found

    cache = Path(os.environ.get("FAULTLINE_CACHE_DIR", BACKEND / ".cache" / "repos")) / name
    if (cache / ".git").is_dir() and _checkout_matches(cache, meta["commit"]):
        return cache
    try:
        fetch_commit(meta["url"], meta["commit"], cache)
    except SourceError as exc:
        message = f"could not fetch {name}@{meta['commit'][:12]}: {exc}"
        if _require_repos():
            pytest.fail(message)
        pytest.skip(message)
    return cache


_reports: dict[str, Report] = {}


def report_for(name: str) -> Report:
    if name not in _reports:
        analysis = analyse_path(locate_repo(name), progress_every=0)
        assert analysis.resolver is not None
        _reports[name] = evaluate(
            analysis.resolver, [lbl for lbl in ALL_LABELS if lbl.repo == name]
        )
    return _reports[name]


def combined(names: list[str]) -> Report:
    results: list[SiteResult] = []
    for name in names:
        results.extend(report_for(name).results)
    return Report(results)


# -- the labels themselves ----------------------------------------------------------------


def test_there_are_at_least_one_hundred_labels() -> None:
    assert len(ALL_LABELS) >= 100


def test_labels_are_unique_and_well_formed() -> None:
    keys = [(lbl.repo, *lbl.key) for lbl in ALL_LABELS]
    assert len(keys) == len(set(keys))
    assert {lbl.repo for lbl in ALL_LABELS} <= set(REPOS)
    for lbl in ALL_LABELS:
        assert lbl.line > 0 and lbl.callee


def test_every_exclusion_is_explained() -> None:
    import json

    excluded = json.loads(LABELS.read_text(encoding="utf-8"))["excluded_sites"]
    assert excluded
    assert all(item["why"].strip() for item in excluded)


def test_labels_are_not_a_single_kind_of_answer() -> None:
    """A set that is all "external" (or all "internal") would be trivially gameable."""
    targets = [lbl.expected_target for lbl in ALL_LABELS]
    internal = sum(1 for t in targets if t is not None)
    assert 0.25 < internal / len(targets) < 0.75


# -- the fixture: deterministic, offline, always on ------------------------------------------


def test_fixture_is_resolved_perfectly() -> None:
    report = report_for("fixture")
    assert report.precision == 1.0
    assert report.recall == 1.0
    assert report.false_positives == []


# -- the gate ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def everything() -> Report:
    return combined(sorted(REPOS))


def test_precision_gate_all_edges(everything: Report) -> None:
    """Every edge Faultline emits, however low its confidence."""
    assert everything.precision >= MIN_PRECISION, (
        f"precision {everything.precision:.3f} < {MIN_PRECISION}; "
        f"false positives: {[(r.label.file, r.label.line, r.label.callee) for r in everything.false_positives]}"
    )


def test_precision_gate_default_view(everything: Report) -> None:
    """What a person sees without touching the confidence slider (>= 0.5)."""
    assert everything.precision_default >= MIN_PRECISION_DEFAULT, everything.to_dict()


def test_recall_gate(everything: Report) -> None:
    missed = [
        (r.label.file, r.label.line, r.label.callee)
        for r in everything.results
        if r.outcome == "missed"
    ]
    assert everything.recall_default >= MIN_RECALL, f"missed: {missed}"


def test_no_wrong_target_is_ever_reported(everything: Report) -> None:
    """Resolving to the *wrong* in-repo symbol is worse than not resolving at all."""
    wrong = [
        (r.label.file, r.label.line, r.label.callee, r.targets)
        for r in everything.results
        if r.outcome == "wrong"
    ]
    assert wrong == []


def test_no_hallucinated_edge_is_visible_by_default(everything: Report) -> None:
    visible_false_positives = [
        (r.label.file, r.label.line, r.label.callee, r.confidence)
        for r in everything.visible
        if r.outcome == "false_positive"
    ]
    assert visible_false_positives == []


def test_confident_edges_are_essentially_always_right(everything: Report) -> None:
    assert everything.precision_confident >= 0.95


# -- the held-out repo: labelled after tuning, never used to tune -----------------------------


def test_held_out_repo_generalises() -> None:
    held_out = [name for name, meta in REPOS.items() if meta.get("held_out")]
    assert held_out, "the label file must keep at least one held-out repo"
    for name in held_out:
        report = report_for(name)
        assert report.precision_default >= MIN_PRECISION_DEFAULT, (name, report.to_dict())
        assert report.recall_default >= MIN_RECALL, (name, report.to_dict())
