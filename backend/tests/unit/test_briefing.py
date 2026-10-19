"""The plain-English briefing: what it says, and that it never says anything untrue.

Each test builds a small, explicit scenario and checks the sentences that matter,
so a wording change that alters *meaning* fails here and one that only rephrases
does not.
"""

from __future__ import annotations

from typing import Any

import pytest

from faultline.analysis import briefing
from faultline.analysis.briefing import Briefing, build_briefing

FQN = "shop.services.billing.charge"


def symbol(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fqn": FQN,
        "name": "charge",
        "module": "shop.services.billing",
        "class_fqn": None,
        "line_start": 10,
        "line_end": 29,
        "is_method": False,
        "is_async": False,
        "is_test": False,
        "is_entrypoint": False,
        "is_private": False,
        "param_count": 2,
        "complexity": 4,
        "decorators": [],
        "doc": "Charge a customer for an order.",
        "fan_in": 0,
        "fan_out": 3,
    }
    base.update(over)
    return base


def reach(name: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fqn": f"shop.api.{name}",
        "name": name,
        "module": "shop.api",
        "class_fqn": None,
        "is_test": False,
        "is_entrypoint": False,
        "line": 5,
        "complexity": 3,
        "distance": 1,
        "path_count": 1,
        "confidence": 1.0,
    }
    base.update(over)
    return base


def test_row(name: str = "test_charge", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fqn": f"tests.test_billing.{name}",
        "name": name,
        "module": "tests.test_billing",
        "path": "tests/test_billing.py",
        "class_fqn": None,
        "distance": 1,
        "confidence": 1.0,
    }
    base.update(over)
    return base


def brief(
    sym: dict[str, Any] | None = None,
    impact: list[dict[str, Any]] | None = None,
    tests: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> Briefing:
    kwargs.setdefault("depth", 3)
    kwargs.setdefault("min_confidence", 0.5)
    kwargs.setdefault("repo_test_count", 10)
    return build_briefing(sym or symbol(), impact or [], tests or [], **kwargs)


def section(b: Briefing, section_id: str) -> str:
    return next(s.text for s in b.sections if s.id == section_id)


# -- structure ------------------------------------------------------------------------------


def test_a_briefing_has_the_four_sections_in_reading_order() -> None:
    b = brief()
    assert [s.id for s in b.sections] == ["what", "affected", "tests", "risk"]
    assert [s.title for s in b.sections] == [
        "What this function does",
        "What is affected if you change it",
        "Which tests to run",
        "Where the risk is concentrated",
    ]
    assert b.fqn == FQN


def test_the_same_graph_always_yields_the_same_words() -> None:
    impact = [reach("a"), reach("b", distance=2)]
    tests = [test_row()]
    assert brief(impact=impact, tests=tests) == brief(impact=impact, tests=tests)


SCENARIOS: list[dict[str, Any]] = [
    {},
    {"impact": [reach("a"), reach("b", is_entrypoint=True), reach("c", complexity=30)]},
    {"impact": [reach(f"f{i}", module=f"m{i % 2}") for i in range(30)]},
    {"sym": symbol(is_entrypoint=True)},
    {"sym": symbol(is_test=True)},
    {"sym": symbol(is_private=True, doc=None)},
    {"sym": symbol(fan_in=4)},
    {"sym": symbol(doc="Uses `backticks` in its summary."), "tests": [test_row()]},
    {"impact": [reach("t", is_test=True)]},
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_backticks_are_always_balanced_so_the_ui_can_style_names(
    scenario: dict[str, Any],
) -> None:
    """The UI splits on backticks. One stray backtick would turn a paragraph into code."""
    b = brief(**scenario)
    for s in b.sections:
        assert s.text.count("`") % 2 == 0, s.text
    assert b.headline.count("`") % 2 == 0


# -- what ------------------------------------------------------------------------------------


def test_what_names_the_function_module_and_line() -> None:
    text = section(brief(), "what")
    assert "`charge` is a function, found in `shop.services.billing` at line 10." in text


def test_what_quotes_the_docstring_as_the_authors_description() -> None:
    assert "Charge a customer for an order." in section(brief(), "what")


def test_what_admits_when_there_is_no_docstring() -> None:
    text = section(brief(symbol(doc=None)), "what")
    assert "no docstring" in text
    assert "Its author describes it" not in text


def test_stray_backticks_in_a_docstring_cannot_break_the_markup() -> None:
    text = section(brief(symbol(doc="Wraps `requests.get` safely.")), "what")
    assert "Wraps 'requests.get' safely." in text


def test_a_method_is_described_with_its_class_and_self_is_not_a_parameter() -> None:
    sym = symbol(
        name="run",
        fqn="app.Flask.run",
        class_fqn="app.Flask",
        is_method=True,
        param_count=3,
    )
    text = section(brief(sym), "what")
    assert "`Flask.run` is a method of the class `Flask`" in text
    assert "It takes 2 parameters" in text  # host, port -- not self


def test_a_static_method_keeps_all_its_parameters() -> None:
    sym = symbol(is_method=True, class_fqn="app.Util", decorators=["staticmethod"], param_count=3)
    assert "It takes 3 parameters" in section(brief(sym), "what")


@pytest.mark.parametrize(
    ("over", "phrase"),
    [
        ({"is_async": True}, "an async function"),
        ({"is_test": True}, "a test function"),
        ({"fqn": "shop.f.<locals>.inner", "name": "inner"}, "defined inside another function"),
    ],
)
def test_what_recognises_kinds_of_function(over: dict[str, Any], phrase: str) -> None:
    assert phrase in section(brief(symbol(**over)), "what")


def test_singular_counts_read_naturally() -> None:
    text = section(brief(symbol(param_count=1, complexity=1, line_end=10, fan_out=1)), "what")
    assert "It takes 1 parameter " in text
    assert "1 line long" in text
    assert "1 independent path" in text
    assert "calls 1 other function in" in text


def test_a_function_that_calls_nothing_says_so_without_claiming_it_is_empty() -> None:
    text = section(brief(symbol(fan_out=0)), "what")
    assert "calls no other function in this repository" in text
    assert "built-ins and libraries" in text


@pytest.mark.parametrize(
    ("score", "word"),
    [
        (1, "simple"),
        (5, "simple"),
        (6, "moderately complicated"),
        (10, "moderately complicated"),
        (11, "complicated"),
        (20, "complicated"),
        (21, "very complicated"),
    ],
)
def test_complexity_bands_sit_exactly_on_the_boundaries(score: int, word: str) -> None:
    assert briefing.complexity_word(score) == word
    assert f"which is {word}." in section(brief(symbol(complexity=score)), "what")


# -- affected --------------------------------------------------------------------------------


def test_affected_separates_direct_callers_from_indirect_ones() -> None:
    impact = [reach("a"), reach("b"), reach("c", distance=2), reach("d", distance=3)]
    text = section(brief(impact=impact), "affected")
    assert "4 other functions can be affected within 3 calls" in text
    assert "2 of them call it directly, so they are the first to notice" in text
    assert "the other 2 reach it through a chain of calls" in text


def test_a_single_direct_caller_uses_the_singular() -> None:
    text = section(brief(impact=[reach("a")]), "affected")
    assert "1 of them calls it directly, so it is the first to notice" in text


def test_affected_when_everything_is_direct_does_not_mention_chains() -> None:
    text = section(brief(impact=[reach("a"), reach("b")]), "affected")
    assert "2 of them call it directly" in text
    assert "chain of calls" not in text


def test_affected_when_nothing_is_direct() -> None:
    text = section(brief(impact=[reach("a", distance=2)]), "affected")
    assert "None call it directly" in text


def test_affected_counts_one_function_in_the_singular() -> None:
    text = section(brief(impact=[reach("a")], depth=1), "affected")
    assert "1 other function can be affected within 1 call of it" in text


def test_tests_are_not_counted_as_affected_application_code() -> None:
    impact = [reach("a"), reach("t1", is_test=True), reach("t2", is_test=True)]
    text = section(brief(impact=impact), "affected")
    assert "1 other function can be affected" in text


def test_affected_names_the_module_when_they_all_share_one() -> None:
    text = section(brief(impact=[reach("a"), reach("b")]), "affected")
    assert "All of them live in `shop.api`." in text


def test_affected_counts_modules_when_they_differ() -> None:
    impact = [reach("a"), reach("b", module="shop.cli"), reach("c", module="shop.jobs")]
    assert "spread across 3 modules" in section(brief(impact=impact), "affected")


def test_entry_points_are_called_out_because_users_can_reach_them() -> None:
    impact = [reach("view", is_entrypoint=True), reach("helper")]
    text = section(brief(impact=impact), "affected")
    assert "1 of them is an entry point (`view`)" in text
    assert "real users" in text


def test_several_entry_points_are_listed_then_truncated() -> None:
    impact = [reach(f"route{i}", is_entrypoint=True) for i in range(5)]
    text = section(brief(impact=impact), "affected")
    assert "5 of them are entry points" in text
    assert "`route0`, `route1`, `route2` and 2 more" in text


# -- affected: nothing depends on it -------------------------------------------------------


def test_a_test_with_no_callers_is_normal() -> None:
    text = section(brief(symbol(is_test=True)), "affected")
    assert "it is a test" in text


def test_an_entry_point_with_no_callers_is_expected_but_still_matters_outside() -> None:
    text = section(brief(symbol(is_entrypoint=True)), "affected")
    assert "which is expected" in text
    assert "entry point" in text
    assert "outside the repository" in text


def test_callers_hidden_by_the_confidence_filter_are_reported_not_denied() -> None:
    """Zero rows with a non-zero raw caller count means the filter hid them."""
    text = section(brief(symbol(fan_in=4), min_confidence=0.9), "affected")
    assert "0.90" in text
    assert "4 possible callers" in text
    assert "Lower the confidence slider" in text


def test_a_function_called_only_by_tests_says_so() -> None:
    text = section(brief(symbol(fan_in=2), impact=[reach("t", is_test=True)]), "affected")
    assert "No application code calls" in text
    assert "Only 1 test exercises it" in text
    assert "can break that test." in text


def test_a_function_called_only_by_several_tests_uses_the_plural() -> None:
    impact = [reach("t1", is_test=True), reach("t2", is_test=True)]
    text = section(brief(symbol(fan_in=2), impact=impact), "affected")
    assert "Only 2 tests exercise it" in text
    assert "can break those tests." in text


def test_a_private_function_with_no_callers_may_be_unused() -> None:
    text = section(brief(symbol(is_private=True)), "affected")
    assert "underscore" in text
    assert "may simply be unused" in text


def test_a_public_function_with_no_callers_lists_the_honest_possibilities() -> None:
    text = section(brief(), "affected")
    assert "public API" in text
    assert "static analysis cannot follow" in text
    assert "something invisible might" in text


# -- tests -----------------------------------------------------------------------------------


def test_a_test_function_is_told_to_run_itself() -> None:
    text = section(brief(symbol(is_test=True)), "tests")
    assert "is itself a test" in text


def test_no_tests_at_all_in_the_repo_explains_what_faultline_looks_for() -> None:
    text = section(brief(repo_test_count=0), "tests")
    assert "no tests that Faultline can recognise" in text
    assert "`test_*`" in text
    assert "`tests/`" in text


def test_untested_function_in_a_tested_repo_suggests_the_full_suite() -> None:
    text = section(brief(repo_test_count=25), "tests")
    assert "No test reaches" in text
    assert "full test suite" in text


def test_tests_are_counted_grouped_by_file_and_the_nearest_is_named() -> None:
    tests = [
        test_row("test_far", distance=4),
        test_row("test_near", distance=1),
        test_row("test_other", path="tests/test_other.py", distance=2),
    ]
    text = section(brief(tests=tests), "tests")
    assert "3 tests can reach" in text
    assert "2 files" in text
    assert "`test_near`, which is 1 call away" in text


def test_uncertain_tests_are_flagged_as_probably_not_certainly() -> None:
    tests = [test_row("test_a"), test_row("test_b", confidence=0.55)]
    text = section(brief(tests=tests), "tests")
    assert "1 of them reaches it only through links" in text
    assert "treat that one as" in text
    assert "under 0.7" in text


def test_several_uncertain_tests_use_the_plural() -> None:
    tests = [test_row("a", confidence=0.5), test_row("b", confidence=0.6)]
    text = section(brief(tests=tests), "tests")
    assert "2 of them reach it only through links" in text
    assert "treat those as" in text


def test_certain_tests_carry_no_uncertainty_warning() -> None:
    assert "less sure" not in section(brief(tests=[test_row()]), "tests")


def test_the_tests_paragraph_points_at_the_copyable_command() -> None:
    assert "Tests tab" in section(brief(tests=[test_row()]), "tests")


# -- risk level ------------------------------------------------------------------------------


def dependents(n: int, **over: Any) -> list[dict[str, Any]]:
    return [reach(f"f{i}", **over) for i in range(n)]


@pytest.mark.parametrize(
    ("n_dependents", "complexity", "tests", "entry", "expected"),
    [
        (0, 4, 1, False, "low"),
        (0, 99, 0, True, "low"),  # nothing depends on it: a change cannot spread
        (1, 3, 1, False, "low"),
        (4, 3, 1, False, "low"),  # 1 point
        (5, 3, 1, False, "moderate"),  # 2 points: the FEW_DEPENDENTS boundary
        (19, 3, 1, False, "moderate"),
        (20, 3, 1, False, "moderate"),  # 3 points
        (20, 3, 1, True, "high"),  # 3 + entry point = 4
        (3, 25, 0, False, "high"),  # 1 + 2 (very complex) + 1 (untested) = 4
        (3, 11, 1, False, "moderate"),  # 1 + 1
        (3, 10, 1, False, "low"),  # complexity 10 is not yet a point
        (3, 20, 1, False, "moderate"),  # 20 is the top of "complicated": 1 + 1
        (3, 21, 1, False, "moderate"),  # 1 + 2
        (3, 3, 0, False, "moderate"),  # 1 + 1 (untested)
    ],
)
def test_risk_level_thresholds(
    n_dependents: int, complexity: int, tests: int, entry: bool, expected: str
) -> None:
    impact = dependents(n_dependents)
    if entry and impact:
        impact[0] = reach("route", is_entrypoint=True)
    found = [test_row(f"test_{i}") for i in range(tests)]
    assert brief(symbol(complexity=complexity), impact, found).risk_level == expected


def test_a_repo_with_no_tests_at_all_does_not_penalise_every_function() -> None:
    """ "Untested" is only a risk where testing was possible."""
    b = brief(symbol(complexity=3), dependents(3), repo_test_count=0)
    assert b.risk_level == "low"


def test_headline_states_the_level_and_the_count() -> None:
    assert brief().headline == "Low risk: nothing else in this repository depends on it."
    assert brief(impact=[reach("a")], tests=[test_row()]).headline == (
        "Low risk: 1 function depends on it."
    )
    b = brief(impact=dependents(6), tests=[test_row()])
    assert b.headline == "Moderate risk: 6 functions depend on it."


def test_headline_says_when_missing_tests_are_part_of_the_reason() -> None:
    b = brief(impact=[reach("a")])
    assert b.risk_level == "moderate"
    assert b.headline == "Moderate risk: 1 function depends on it, and no test covers it."


def test_headline_does_not_blame_missing_tests_in_a_repo_that_has_none() -> None:
    b = brief(impact=[reach("a")], repo_test_count=0)
    assert b.headline == "Low risk: 1 function depends on it."


def test_high_risk_headline_names_the_entry_points() -> None:
    impact = dependents(20)
    impact[0] = reach("route", is_entrypoint=True)
    b = brief(impact=impact, tests=[test_row()])
    assert b.risk_level == "high"
    assert b.headline == "High risk: 20 functions depend on it, including 1 entry point."


# -- risk paragraph --------------------------------------------------------------------------


def test_risk_with_no_dependents_points_at_what_static_analysis_cannot_see() -> None:
    text = section(brief(symbol(complexity=8)), "risk")
    assert "Very little can go wrong for other code" in text
    assert "complexity 8" in text
    assert "cannot see" in text


def test_risk_names_the_three_ingredients() -> None:
    text = section(brief(impact=dependents(3), tests=[test_row()]), "risk")
    assert "3 functions" in text
    assert "complexity 4" in text
    assert "1 test" in text


def test_risk_lists_the_most_complicated_dependents_first_and_links_them() -> None:
    impact = [
        reach("calm", complexity=2),
        reach("tangled", complexity=31, distance=2),
        reach("busy", complexity=14),
        reach("knotty", complexity=22),
        reach("fussy", complexity=12),
    ]
    b = brief(impact=impact, tests=[test_row()])
    text = section(b, "risk")
    assert "`tangled`, `knotty`, `busy` and 1 more" in text
    links = next(s.links for s in b.sections if s.id == "risk")
    assert [link.label for link in links] == ["tangled", "knotty", "busy"]
    assert links[0].fqn == "shop.api.tangled"
    assert links[0].note == "complexity 31, 2 calls away"
    assert links[2].note == "complexity 14, 1 call away"


def test_risk_reassures_when_no_dependent_is_complicated() -> None:
    b = brief(impact=dependents(4, complexity=6), tests=[test_row()])
    text = section(b, "risk")
    assert "None of the 4 functions" in text
    assert "not concentrated in any one tangled spot" in text
    assert next(s.links for s in b.sections if s.id == "risk") == ()


def test_risk_calls_out_a_module_that_holds_most_of_the_exposure() -> None:
    impact = dependents(6, module="shop.api") + dependents(2, module="shop.cli")
    for i, row in enumerate(impact):
        row["fqn"], row["name"] = f"f{i}", f"f{i}"
    text = section(brief(impact=impact, tests=[test_row()]), "risk")
    assert "Most of the exposure (6 of 8) sits in `shop.api`." in text


def test_risk_does_not_call_out_a_module_when_exposure_is_even() -> None:
    impact = dependents(3, module="a") + dependents(3, module="b") + dependents(3, module="c")
    for i, row in enumerate(impact):
        row["fqn"], row["name"] = f"f{i}", f"f{i}"
    assert "Most of the exposure" not in section(brief(impact=impact, tests=[test_row()]), "risk")


def test_risk_counts_connections_that_are_inferred_rather_than_certain() -> None:
    impact = [reach("a"), reach("b", confidence=0.55), reach("c", confidence=0.65)]
    text = section(brief(impact=impact, tests=[test_row()]), "risk")
    assert "2 of those connections are inferred rather than certain" in text
    assert "check they are real before you rely on them." in text


def test_a_single_inferred_connection_uses_the_singular() -> None:
    impact = [reach("a"), reach("b", confidence=0.55)]
    text = section(brief(impact=impact, tests=[test_row()]), "risk")
    assert "1 of those connections is inferred rather than certain" in text
    assert "check it is real before you rely on it." in text


def test_risk_warns_when_nothing_would_catch_a_mistake() -> None:
    text = section(brief(impact=dependents(2), repo_test_count=9), "risk")
    assert "No test reaches it, so a mistake would not be caught automatically." in text


def test_risk_does_not_nag_about_tests_in_a_repo_that_has_none() -> None:
    text = section(brief(impact=dependents(2), repo_test_count=0), "risk")
    assert "No test reaches it" not in text


# -- helpers ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        ([], ""),
        (["a"], "`a`"),
        (["a", "b"], "`a` and `b`"),
        (["a", "b", "c"], "`a`, `b` and `c`"),
        (["a", "b", "c", "d", "e"], "`a`, `b`, `c` and 2 more"),
    ],
)
def test_name_list(labels: list[str], expected: str) -> None:
    assert briefing.name_list(labels) == expected


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, "0 functions"), (1, "1 function"), (2, "2 functions"), (1234, "1,234 functions")],
)
def test_plural(n: int, expected: str) -> None:
    assert briefing.plural(n, "function") == expected


def test_plural_with_an_irregular_form() -> None:
    assert briefing.plural(1, "class", "classes") == "1 class"
    assert briefing.plural(2, "class", "classes") == "2 classes"


def test_label_for_qualifies_methods_only() -> None:
    assert briefing.label_for("run", "pkg.mod.Flask") == "Flask.run"
    assert briefing.label_for("run", None) == "run"


# -- repository overview -----------------------------------------------------------------


COUNTS = {"functions": 1705, "classes": 40, "call_edges": 3459, "test_functions": 120}


def overview(**over: Any) -> briefing.RepoOverview:
    args: dict[str, Any] = {
        "file_count": 83,
        "loc": 13301,
        "counts": COUNTS,
        "internal_call_sites": 2000,
        "internal_resolution_rate": 0.966,
        "parse_failures": 0,
    }
    args.update(over)
    return briefing.describe_repo("flask", **args)


def test_overview_states_what_was_found_in_plain_words() -> None:
    text = overview().text
    assert "`flask` contains 83 Python files (13,301 lines)" in text
    assert "1,705 functions" in text
    assert "40 classes" in text
    assert "3,459 calls" in text
    assert "97% were traced to a definite target" in text


def test_a_healthy_repo_has_no_notices() -> None:
    assert overview().notices == ()


def test_the_resolution_sentence_is_dropped_when_there_are_no_internal_calls() -> None:
    assert "traced" not in overview(internal_call_sites=0).text


def test_no_python_files_is_a_warning() -> None:
    (notice,) = overview(file_count=0, counts={}).notices
    assert notice.level == "warn"
    assert "No Python files" in notice.text


def test_code_without_functions_is_explained() -> None:
    (notice,) = overview(counts={"functions": 0, "classes": 0, "call_edges": 0}).notices
    assert "defines no functions" in notice.text


def test_functions_that_never_call_each_other_are_explained() -> None:
    notices = overview(counts={**COUNTS, "call_edges": 0}).notices
    assert [n.level for n in notices] == ["warn"]
    assert "none of them call each other" in notices[0].text


@pytest.mark.parametrize(
    ("sites", "rate", "warns"),
    [
        (19, 0.1, False),  # too few calls to judge
        (20, 0.59, True),
        (20, 0.6, False),
        (5000, 0.3, True),
    ],
)
def test_low_resolution_is_flagged_only_with_enough_evidence(
    sites: int, rate: float, warns: bool
) -> None:
    notices = overview(internal_call_sites=sites, internal_resolution_rate=rate).notices
    assert any("could be traced" in n.text for n in notices) is warns


def test_missing_tests_get_an_informational_note() -> None:
    (notice,) = overview(counts={**COUNTS, "test_functions": 0}).notices
    assert notice.level == "info"
    assert "No tests were recognised" in notice.text


@pytest.mark.parametrize(("failures", "phrase"), [(1, "1 file could"), (3, "3 files could")])
def test_parse_failures_are_reported(failures: int, phrase: str) -> None:
    (notice,) = overview(parse_failures=failures).notices
    assert phrase in notice.text
    assert ("is missing" if failures == 1 else "are missing") in notice.text


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        ({"status": "pending"}, None),
        ({"status": "failed", "file_count": 0}, None),
        ({"status": "ready", "file_count": 0}, "No Python files found"),
        (
            {"status": "ready", "file_count": 4, "node_count": 5, "edge_count": 0},
            "No function calls",
        ),
        ({"status": "ready", "file_count": 4, "node_count": 50, "edge_count": 90}, None),
        ({"status": "ready", "file_count": 4, "node_count": None, "edge_count": None}, None),
    ],
)
def test_repo_card_notice(repo: dict[str, Any], expected: str | None) -> None:
    notice = briefing.repo_card_notice(repo)
    if expected is None:
        assert notice is None
    else:
        assert notice is not None and expected in notice
