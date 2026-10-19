"""Scoring the resolver against hand-labelled call sites.

The labels live in ``tests/accuracy/labelled_calls.json``. Each one says: *at this
file and line, this call expression really targets that symbol* (or ``null`` when
the callee is outside the repo, such as ``os.path.join`` or ``click.option``).
They were written by reading source -- never copied from Faultline's output --
which is the only thing that makes the resulting numbers mean anything.

Definitions, chosen to be hard to flatter:

* A site is **resolved** when Faultline produces at least one target for it.
* A resolved site is **correct** when the labelled target is among them.
  A site labelled ``null`` that Faultline resolves anyway is a **false positive**
  and counts against precision: inventing an edge to code that is not there is
  the worst failure mode a call graph has.
* **Precision** = correct / resolved.  **Recall** = correct / labelled-with-a-target.
* ``precision_confident`` restricts to sites resolved as ``exact`` or ``inferred``,
  excluding ``ambiguous`` fan-out; ``precision`` includes them.
* ``precision_default`` / ``recall_default`` apply the API's default view: only
  targets with confidence >= 0.5, which is what a person sees without touching
  the confidence slider. Both are reported next to the all-edges numbers so
  neither can be quoted without the other.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from faultline.models import ModuleFacts, RawCall
from faultline.parsing.resolver import Resolver

#: The default ``min_confidence`` of the impact endpoints and the UI slider.
DEFAULT_MIN_CONFIDENCE = 0.5


def callee_text(call: RawCall) -> str:
    """The callee expression as it reads in source, e.g. ``self.save`` or ``a.b.c``.

    ``cls.x`` is rendered ``self.x``: both mean "a method of the owning class"
    and the resolver treats them identically.
    """
    if call.kind == "super":
        return "super()." + ".".join(call.parts)
    if call.kind == "self":
        return "self." + ".".join(call.parts)
    return ".".join(call.parts)


@dataclass(slots=True, frozen=True)
class Label:
    repo: str
    file: str
    line: int
    callee: str
    expected_target: str | None

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.file, self.line, self.callee)


@dataclass(slots=True)
class SiteResult:
    label: Label
    targets: list[str]
    resolution: str | None
    confidence: float | None
    outcome: str
    """One of ``correct``, ``wrong``, ``false_positive``, ``missed``, ``true_negative``."""


@dataclass(slots=True)
class Report:
    results: list[SiteResult] = field(default_factory=list)

    def _count(self, *outcomes: str) -> int:
        return sum(1 for r in self.results if r.outcome in outcomes)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def resolved(self) -> int:
        return self._count("correct", "wrong", "false_positive")

    @property
    def correct(self) -> int:
        return self._count("correct")

    @property
    def with_target(self) -> int:
        return sum(1 for r in self.results if r.label.expected_target is not None)

    @property
    def precision(self) -> float:
        return self.correct / self.resolved if self.resolved else 1.0

    @property
    def recall(self) -> float:
        return self.correct / self.with_target if self.with_target else 1.0

    @property
    def visible(self) -> list[SiteResult]:
        """Resolved sites whose confidence clears the default threshold."""
        return [
            r
            for r in self.results
            if r.outcome in {"correct", "wrong", "false_positive"}
            and r.confidence is not None
            and r.confidence >= DEFAULT_MIN_CONFIDENCE
        ]

    @property
    def precision_default(self) -> float:
        visible = self.visible
        if not visible:
            return 1.0
        return sum(1 for r in visible if r.outcome == "correct") / len(visible)

    @property
    def recall_default(self) -> float:
        if not self.with_target:
            return 1.0
        return sum(1 for r in self.visible if r.outcome == "correct") / self.with_target

    @property
    def confident(self) -> list[SiteResult]:
        return [
            r
            for r in self.results
            if r.outcome in {"correct", "wrong", "false_positive"} and r.resolution != "ambiguous"
        ]

    @property
    def precision_confident(self) -> float:
        confident = self.confident
        if not confident:
            return 1.0
        return sum(1 for r in confident if r.outcome == "correct") / len(confident)

    @property
    def exact_rate(self) -> float:
        with_target = [r for r in self.results if r.label.expected_target is not None]
        if not with_target:
            return 0.0
        return sum(
            1 for r in with_target if r.outcome == "correct" and r.resolution == "exact"
        ) / len(with_target)

    @property
    def false_positives(self) -> list[SiteResult]:
        return [r for r in self.results if r.outcome == "false_positive"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "labelled_sites": self.total,
            "with_in_repo_target": self.with_target,
            "resolved": self.resolved,
            "correct": self.correct,
            "precision": round(self.precision, 4),
            "precision_confident": round(self.precision_confident, 4),
            "precision_default": round(self.precision_default, 4),
            "recall": round(self.recall, 4),
            "recall_default": round(self.recall_default, 4),
            "exact_rate": round(self.exact_rate, 4),
            "wrong": self._count("wrong"),
            "false_positives": self._count("false_positive"),
            "missed": self._count("missed"),
            "true_negatives": self._count("true_negative"),
        }


class StaleLabelError(AssertionError):
    """A label points at a call site that does not exist: the labels are out of
    date with the source, or a line number was mistyped."""


def load_labels(path: Path) -> tuple[dict[str, dict[str, Any]], list[Label]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    repos: dict[str, dict[str, Any]] = data.get("repos", {})
    labels = [
        Label(
            repo=item["repo"],
            file=item["file"],
            line=int(item["line"]),
            callee=item["callee"],
            expected_target=item["expected_target"],
        )
        for item in data["labels"]
    ]
    return repos, labels


def _site_index(modules: list[ModuleFacts]) -> dict[tuple[str, int, str], list[RawCall]]:
    index: dict[tuple[str, int, str], list[RawCall]] = defaultdict(list)
    for facts in modules:
        for call in facts.calls:
            index[(facts.path, call.line, callee_text(call))].append(call)
    return index


def evaluate(resolver: Resolver, labels: list[Label]) -> Report:
    """Score ``labels`` for one repository against a built :class:`Resolver`."""
    index = _site_index(resolver.module_facts)
    report = Report()
    stale: list[Label] = []

    for label in labels:
        calls = index.get(label.key)
        if not calls:
            stale.append(label)
            continue

        targets: list[str] = []
        resolution: str | None = None
        confidence: float | None = None
        for call in calls:
            outcome = resolver.resolve_call(call)
            if outcome.dropped is None and outcome.targets:
                # The graph never contains an edge from a function to itself, so a
                # site whose only candidate is its own caller produces nothing.
                reachable = [t for t in outcome.targets if t != call.caller_fqn]
                if reachable:
                    targets.extend(reachable)
                    resolution = outcome.resolution
                    confidence = outcome.confidence

        expected = label.expected_target
        if not targets:
            verdict = "true_negative" if expected is None else "missed"
        elif expected is None:
            verdict = "false_positive"
        elif expected in targets:
            verdict = "correct"
        else:
            verdict = "wrong"
        report.results.append(SiteResult(label, targets, resolution, confidence, verdict))

    if stale:
        detail = ", ".join(f"{s.file}:{s.line} {s.callee}" for s in stale[:5])
        raise StaleLabelError(f"{len(stale)} label(s) match no call site, e.g. {detail}")
    return report
