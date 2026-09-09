"""The shrink metrics of requirement 6 and the duplicate-lines metrics of requirement 5.6.

Three declarations and two shipped numbers are pinned here. The verbosity ratio is a
synthetic the Gate computes and the only one carrying a floor; the per-routine comment-line
count is an ordinary Understand metric that needed no declaration at all, only a measured
default; the two duplicate-lines ids are plugin metrics, spellable in a configuration and
offered only where the installed build's own tags allow.

Requirement 9.1 is what most of these tests are really about: every number asserted below was
measured on this repository first, and the measurement lives beside the declaration.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping

import pytest

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.metric_names import (
    PLUGIN_METRICS,
    SYNTHETIC_METRICS,
    Scope,
    below_floor,
    declared_floor,
)
from scitools_hook.config.models import Limit, ProjectSettings, Settings, ThresholdSpec
from scitools_hook.config.validate import validate_settings
from scitools_hook.errors import ConfigError


class FakeCatalogue:
    """Stand-in for ``understand.catalogue.MetricCatalogue``: metric ids per language/scope."""

    def __init__(self, metrics: Mapping[tuple[str, str], set[str]]) -> None:
        self._metrics = dict(metrics)

    def available(self, language: str, scope: Scope) -> set[str]:
        return set(self._metrics.get((language, scope), set()))


OFFERS_DUPLICATES = FakeCatalogue(
    {("Python", "file"): {"CountLineCode", "DuplicateLinesOfCode", "DuplicateLinesOfCodePercent"}}
)
"""A build whose duplicates plugin is discovered and enabled."""

OFFERS_NOTHING_EXTRA = FakeCatalogue({("Python", "file"): {"CountLineCode"}})
"""The same build with the plugin absent or disabled in the Plugin Manager."""


def one(spec: ThresholdSpec) -> Settings:
    """Settings carrying exactly this threshold, for a project Understand reads as Python."""
    return Settings(thresholds=[spec], project=ProjectSettings(languages=["Python"]))


def by_rule(settings: Settings) -> dict[str, ThresholdSpec]:
    return {spec.rule: spec for spec in settings.thresholds}


# --- the verbosity ratio and its floor (6.3) ---------------------------------------


def test_lines_per_statement_is_a_routine_synthetic_over_lines_and_statements() -> None:
    entry = SYNTHETIC_METRICS["LinesPerStatement"]

    assert entry.scope == "routine"
    assert entry.requires == ("CountLineCode", "CountStmt")


def test_lines_per_statement_declares_a_statement_floor_of_five() -> None:
    """The floor is data, not a branch in the evaluator: a three-line routine cannot trip it."""
    assert SYNTHETIC_METRICS["LinesPerStatement"].floor == ("CountStmt", 5)


def test_a_floor_names_a_metric_the_synthetic_already_requires() -> None:
    for entry in SYNTHETIC_METRICS.values():
        if entry.floor is not None:
            assert entry.floor[0] in entry.requires, entry.id
            assert entry.floor[1] > 0, entry.id


def test_a_synthetic_without_a_floor_declares_none() -> None:
    assert SYNTHETIC_METRICS["CountParams"].floor is None
    assert SYNTHETIC_METRICS["CountDeclMethodNonStub"].floor is None


# --- the two shipped routine defaults (6.2, 6.4, 9.1) ------------------------------


@pytest.mark.parametrize(
    ("rule", "limit"),
    [("routine.LinesPerStatement", Limit(max=3.0)), ("routine.CountLineComment", Limit(max=20))],
)
def test_the_shrink_defaults_ship_at_their_measured_values(rule: str, limit: Limit) -> None:
    assert by_rule(default_settings())[rule].limit == limit


@pytest.mark.parametrize("rule", ["routine.LinesPerStatement", "routine.CountLineComment"])
def test_the_shrink_defaults_ship_as_warnings(rule: str) -> None:
    """Requirement 6.4: none of these blocks a commit until an operator chooses a severity."""
    assert by_rule(default_settings())[rule].severity == "warning"


def test_the_comment_line_count_needs_no_declaration_of_its_own() -> None:
    """6.2 asks only that it be offered: it is one of Understand's own routine metrics."""
    assert "CountLineComment" not in SYNTHETIC_METRICS
    assert "CountLineComment" not in PLUGIN_METRICS


# --- the comment ratio gains a maximum it does not ship (6.1) ----------------------


def test_the_shipped_comment_ratio_is_still_a_minimum_alone() -> None:
    limit = by_rule(default_settings())["file.RatioCommentToCode"].limit

    assert limit == Limit(min=0.1)
    assert limit.max is None


def test_a_configured_comment_ratio_maximum_validates() -> None:
    document = """
    [thresholds.file]
    RatioCommentToCode = { min = 0.1, max = 2.0 }
    """
    settings = Settings.model_validate(tomllib.loads(document))

    report = validate_settings(settings, None)

    accepted = {spec.rule: spec.limit for spec in report.thresholds}
    assert accepted["file.RatioCommentToCode"] == Limit(min=0.1, max=2.0)


# --- the duplicate-lines plugin metrics (5.6) --------------------------------------


@pytest.mark.parametrize("metric", ["DuplicateLinesOfCode", "DuplicateLinesOfCodePercent"])
def test_a_duplicate_lines_metric_is_declared_for_file_arch_and_project(metric: str) -> None:
    declared = PLUGIN_METRICS[metric]

    assert declared.id == metric
    assert declared.scopes == ("file", "arch", "project")
    assert declared.languages == ("Any",)


@pytest.mark.parametrize("metric", ["DuplicateLinesOfCode", "DuplicateLinesOfCodePercent"])
def test_a_duplicate_lines_threshold_is_accepted_when_the_build_offers_it(metric: str) -> None:
    spec = ThresholdSpec(scope="file", metric=metric, limit=Limit(max=50))

    report = validate_settings(one(spec), OFFERS_DUPLICATES)

    assert [threshold.rule for threshold in report.thresholds] == [f"file.{metric}"]
    assert report.dropped == ()


@pytest.mark.parametrize("metric", ["DuplicateLinesOfCode", "DuplicateLinesOfCodePercent"])
def test_a_duplicate_lines_threshold_is_refused_when_the_build_does_not_offer_it(
    metric: str,
) -> None:
    """Not dropped with a note: it is not a shipped default, so an operator asked for it."""
    spec = ThresholdSpec(scope="file", metric=metric, limit=Limit(max=50))

    with pytest.raises(ConfigError) as caught:
        validate_settings(one(spec), OFFERS_NOTHING_EXTRA)

    assert caught.value.key == f"thresholds.file.{metric}"
    assert metric in caught.value.message


def test_no_duplicate_lines_metric_ships_as_a_threshold() -> None:
    """Requirement 5.4 again: a metric this build may not even carry cannot be a default."""
    shipped = {spec.metric for spec in default_settings().thresholds}

    assert shipped.isdisjoint({"DuplicateLinesOfCode", "DuplicateLinesOfCodePercent"})


# --- the guard that applies a declared floor (task 1.4; req 6.3) -------------------
#
# The declaration was pinned above; what follows is the one predicate every consumer of it
# asks. It is stated here rather than in `tests/analysis` because the answer belongs to the
# metric, and because both evaluators and `recommend` have to agree on it.

BELOW = {"CountStmt": 4.0, "LinesPerStatement": 8.0}
"""A routine of four statements over 32 lines: a ratio of 8, and a statement about nothing."""

AT_THE_FLOOR = {"CountStmt": 5.0, "LinesPerStatement": 8.0}
"""The same ratio one statement higher, which is where the distribution starts to mean it."""


def test_a_routine_under_the_declared_minimum_is_below_the_floor() -> None:
    """Four statements is under the declared five, so the ratio judges nothing (req 6.3)."""
    assert below_floor(BELOW, "LinesPerStatement") is True


def test_a_routine_at_the_declared_minimum_is_not_below_the_floor() -> None:
    """The floor is a minimum, not a threshold to exceed: five statements is judged."""
    assert below_floor(AT_THE_FLOOR, "LinesPerStatement") is False


def test_a_configured_minimum_replaces_the_declared_one() -> None:
    """Requirement 6.3 asks for a configurable minimum, so two makes the small routine judged."""
    assert below_floor(BELOW, "LinesPerStatement", 2) is False
    assert below_floor(AT_THE_FLOOR, "LinesPerStatement", 6) is True


def test_a_metric_that_declares_no_floor_is_never_below_one() -> None:
    """The floor is per metric: CountLineCode over the same routine is judged as it always was."""
    assert below_floor(BELOW, "CountLineCode") is False
    assert below_floor(BELOW, "CountLineCode", 99) is False


def test_an_entity_without_the_counted_metric_is_not_below_the_floor() -> None:
    """Absent is not below: a metric Understand did not provide is the unavailable report's."""
    assert below_floor({"LinesPerStatement": 8.0}, "LinesPerStatement") is False


def test_declared_floor_answers_the_pair_for_the_one_metric_that_has_one() -> None:
    """The guard reads the declaration rather than a constant of its own."""
    assert declared_floor("LinesPerStatement") == ("CountStmt", 5)
    assert declared_floor("CountParams") is None
    assert declared_floor("CyclomaticStrict") is None
