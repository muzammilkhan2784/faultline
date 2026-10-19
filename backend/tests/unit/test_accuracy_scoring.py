"""The accuracy scorer itself, on tiny hand-built repos where the right answer
for every outcome category is obvious."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from helpers import parse

from faultline.accuracy import (
    Label,
    StaleLabelError,
    callee_text,
    evaluate,
    load_labels,
)
from faultline.parsing.resolver import Resolver

SOURCE = """
import os

def target(): ...

class A:
    def save(self): ...
    def delegate(self):
        self.inner.delegate()

def caller(x):
    target()
    os.getcwd()
    x.save()
    missing_thing()
"""


def resolver() -> Resolver:
    return Resolver([parse(SOURCE, "m", path="m.py")])


def label(line: int, callee: str, target: str | None) -> Label:
    return Label("t", "m.py", line, callee, target)


def outcome(lbl: Label) -> str:
    (result,) = evaluate(resolver(), [lbl]).results
    return result.outcome


def test_correct_when_the_labelled_target_is_resolved() -> None:
    assert outcome(label(12, "target", "m.target")) == "correct"


def test_true_negative_when_an_external_call_stays_unresolved() -> None:
    assert outcome(label(13, "os.getcwd", None)) == "true_negative"


def test_false_positive_when_an_external_call_is_resolved_into_the_repo() -> None:
    assert outcome(label(14, "x.save", None)) == "false_positive"


def test_wrong_when_resolved_to_something_else() -> None:
    assert outcome(label(14, "x.save", "m.target")) == "wrong"


def test_missed_when_a_real_target_goes_unresolved() -> None:
    assert outcome(label(15, "missing_thing", "m.target")) == "missed"


def test_a_site_whose_only_candidate_is_its_own_caller_produces_no_edge() -> None:
    """``delegate`` calling ``self.inner.delegate()`` matches only itself. The
    graph never contains that edge, so the scorer must not count it either."""
    assert outcome(label(9, "self.inner.delegate", None)) == "true_negative"


def test_stale_labels_fail_loudly() -> None:
    with pytest.raises(StaleLabelError, match="match no call site"):
        evaluate(resolver(), [label(999, "nothing", None)])


def test_report_arithmetic() -> None:
    report = evaluate(
        resolver(),
        [
            label(12, "target", "m.target"),  # correct
            label(14, "x.save", None),  # false positive
            label(13, "os.getcwd", None),  # true negative
            label(15, "missing_thing", "m.target"),  # missed
        ],
    )
    assert report.total == 4
    assert report.resolved == 2
    assert report.correct == 1
    assert report.with_target == 2
    assert report.precision == 0.5
    assert report.recall == 0.5
    d = report.to_dict()
    assert (d["false_positives"], d["missed"], d["true_negatives"]) == (1, 1, 1)
    assert len(report.false_positives) == 1


def test_default_view_excludes_sub_threshold_edges() -> None:
    """A common builtin method name is halved below the 0.5 threshold, so a wrong
    guess there counts against overall precision but never appears by default."""
    facts = parse("class A:\n    def pop(self): ...\ndef f(x):\n    x.pop()\n", "m", path="m.py")
    report = evaluate(Resolver([facts]), [Label("t", "m.py", 4, "x.pop", None)])
    assert report.precision == 0.0
    assert report.precision_default == 1.0
    assert report.visible == []


def test_confident_precision_ignores_ambiguous_sites() -> None:
    report = evaluate(resolver(), [label(12, "target", "m.target"), label(14, "x.save", None)])
    assert report.precision == 0.5
    assert report.precision_confident == 1.0


def test_empty_report_is_perfect_not_a_division_error() -> None:
    report = evaluate(resolver(), [])
    assert (report.precision, report.recall, report.precision_default) == (1.0, 1.0, 1.0)
    assert report.exact_rate == 0.0


def test_callee_text_normalises_self_and_super() -> None:
    source = (
        "class C:\n"
        "    def m(self):\n"
        "        self.a()\n"
        "        cls.b()\n"
        "        super().c()\n"
        "        d.e.f()\n"
        "        g()\n"
    )
    facts = parse(source, "m")
    assert [callee_text(c) for c in facts.calls] == ["self.a", "self.b", "super().c", "d.e.f", "g"]


def test_load_labels_reads_repos_and_labels(tmp_path: Path) -> None:
    path = tmp_path / "labels.json"
    path.write_text(
        json.dumps(
            {
                "repos": {"r": {"url": "u"}},
                "labels": [
                    {
                        "repo": "r",
                        "file": "a.py",
                        "line": 3,
                        "callee": "f",
                        "expected_target": "a.f",
                    },
                    {
                        "repo": "r",
                        "file": "a.py",
                        "line": 4,
                        "callee": "g",
                        "expected_target": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    repos, labels = load_labels(path)
    assert repos == {"r": {"url": "u"}}
    assert [(item.line, item.expected_target) for item in labels] == [(3, "a.f"), (4, None)]
    assert labels[0].key == ("a.py", 3, "f")
