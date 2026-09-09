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
from scitools_hook.config.metric_names import PLUGIN_METRICS, SYNTHETIC_METRICS, Scope
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
