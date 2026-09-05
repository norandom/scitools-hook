"""The configuration this specification adds, and the defaults that keep today's behaviour.

Requirement 1.3 is the one every test here is really about: a repository that names none of
these keys must behave exactly as 0.1.0a8 did, on 6.5 and on 8.0 alike. So each new key is
checked for its default *and* for the effect of leaving it out, and the metric ids Understand
8.0 adds are declared without any of them entering the shipped limits (requirement 5.4).

The one deliberate exception is ``understand.snapshot_cache``, which ships **on**. It is a
performance key that changes no finding (requirement 8.7), and requirement 8.4's target --
a warm check under 15 s on this repository -- is stated unconditionally, so a cache the
operator has to discover would leave the shipped tool missing its own target. Requirement
1.3's "off by default" is read here as covering the features that change what a run reports,
which is every other key this specification adds.
"""

from __future__ import annotations

import tomllib

import pytest
from pydantic import ValidationError

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.metric_names import PLUGIN_METRICS, PluginMetric
from scitools_hook.config.models import Settings
from scitools_hook.config.template import render_template

# --- the defaults keep today's behaviour ------------------------------------------


def test_every_new_key_that_changes_a_run_is_off_by_default() -> None:
    """Requirement 1.3: nothing this specification adds fires until it is asked for."""
    settings = default_settings()

    assert settings.understand.sarif is False
    assert settings.understand.before_side == "shadow"
    assert settings.structure.unused_routines is None
    assert settings.structure.architecture_options == {}
    assert settings.analysis.accuracy_floor is None


def test_the_snapshot_cache_ships_on_because_it_changes_no_finding() -> None:
    """The documented exception to the rule above (requirements 8.4, 8.7).

    Pinned as its own test so that flipping it is a deliberate act with a failing test
    attached, rather than a quiet edit inside the model.
    """
    assert default_settings().understand.snapshot_cache is True


def test_a_configuration_naming_none_of_the_new_keys_is_the_shipped_default() -> None:
    """The observable form of requirement 1.3: an old configuration file still means today."""
    settings = Settings.model_validate(tomllib.loads('[project]\nlanguages = ["Python"]\n'))
    shipped = default_settings()

    assert settings.understand.sarif == shipped.understand.sarif
    assert settings.understand.before_side == shipped.understand.before_side
    assert settings.understand.snapshot_cache == shipped.understand.snapshot_cache
    assert settings.structure.unused_routines == shipped.structure.unused_routines
    assert settings.analysis.accuracy_floor == shipped.analysis.accuracy_floor


def test_a_configuration_naming_every_new_key_validates() -> None:
    """All six keys together, in the spellings the documentation will show."""
    text = """
[understand]
sarif = true
before_side = "commit"
snapshot_cache = false

[structure]
unused_routines = "warning"
unused_ignore = ["^tests\\\\."]
architecture_options = { "Date Relative to" = "Most Recent Commit" }

[analysis]
accuracy_floor = 0.8
"""
    settings = Settings.model_validate(tomllib.loads(text))

    assert settings.understand.sarif is True
    assert settings.understand.before_side == "commit"
    assert settings.understand.snapshot_cache is False
    assert settings.structure.unused_routines == "warning"
    assert settings.structure.unused_ignore == ["^tests\\."]
    assert settings.structure.architecture_options == {"Date Relative to": "Most Recent Commit"}
    assert settings.analysis.accuracy_floor == 0.8


# --- what each key refuses ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ('[understand]\nbefore_side = "sideways"\n', "before_side"),
        ('[structure]\nunused_routines = "fatal"\n', "unused_routines"),
        ("[analysis]\naccuracy_floor = 1.5\n", "accuracy_floor"),
        ("[analysis]\naccuracy_floor = -0.1\n", "accuracy_floor"),
        ("[analysis]\nnot_a_key = 1\n", "not_a_key"),
    ],
    ids=["route", "severity", "above-one", "below-zero", "unknown"],
)
def test_a_value_outside_the_grammar_is_refused_by_name(text: str, needle: str) -> None:
    """A misspelling stops at configuration time naming the key, as every other key does."""
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate(tomllib.loads(text))

    assert needle in str(caught.value)


def test_the_accuracy_floor_accepts_both_ends_of_the_fraction() -> None:
    """Zero and one are meaningful floors; the bound is inclusive at both ends."""
    for value in (0.0, 1.0):
        assert Settings.model_validate({"analysis": {"accuracy_floor": value}}).analysis is not None


def test_an_unused_ignore_pattern_that_is_not_a_regex_is_refused() -> None:
    """The ignore list is matched as regular expressions, so it compiles at load time."""
    with pytest.raises(ValidationError):
        Settings.model_validate({"structure": {"unused_ignore": ["(unclosed"]}})


def test_the_unused_ignore_defaults_cover_the_routines_nothing_calls_on_purpose() -> None:
    """Dunder methods, tests, entry points and fixture hooks are uncalled by design."""
    patterns = default_settings().structure.unused_ignore

    assert patterns, "an empty default would report every dunder method in the repository"
    for longname in ("pkg.Thing.__init__", "tests.test_it.test_works", "app.main"):
        assert any(__import__("re").search(pattern, longname) for pattern in patterns), longname


# --- the 8.0 metric ids ------------------------------------------------------------


def test_the_plugin_metrics_are_declared_with_the_tags_measured_on_the_build() -> None:
    """Requirement 5.1's ids, with the scopes and languages `Metric.lookup` reported."""
    globals_modified = PLUGIN_METRICS["CountGlobalsModified"]
    assert globals_modified.scopes == ("routine",)
    assert "Python" in globals_modified.languages

    assert PLUGIN_METRICS["CountClassCoupledModified"].scopes == ("class",)
    assert PLUGIN_METRICS["CorePercentage"].scopes == ("arch", "project")
    assert PLUGIN_METRICS["BidirectionalDepsPercent"].scopes == ("file", "class")
    assert PLUGIN_METRICS["CognitiveComplexity"].languages == ("C", "C++")
    assert "Python" not in PLUGIN_METRICS["CognitiveComplexity"].languages


def test_a_plugin_metric_declares_the_languages_it_answers_for() -> None:
    """``Any`` is Understand's own word for a metric with no language restriction."""
    assert PLUGIN_METRICS["CorePercentage"].languages == ("Any",)
    assert all(isinstance(metric, PluginMetric) for metric in PLUGIN_METRICS.values())


def test_no_plugin_metric_ships_as_a_blocking_default() -> None:
    """Requirement 5.4: a metric nobody measured here may not refuse somebody's commit."""
    blocking = {
        spec.rule
        for spec in default_settings().thresholds
        if spec.severity == "error" and spec.rule.split(".", 1)[-1] in PLUGIN_METRICS
    }

    assert blocking == set()


def test_no_plugin_metric_is_a_shipped_threshold_at_all_yet() -> None:
    """Stronger than the rule above, and true until a task records a measurement for one."""
    shipped = {spec.rule.split(".", 1)[-1] for spec in default_settings().thresholds}

    assert shipped.isdisjoint(PLUGIN_METRICS)


# --- the template the `init` command writes ----------------------------------------


def test_the_template_carries_every_new_key_commented() -> None:
    """Requirement 3.9's shape: the key is discoverable in the file without being enabled."""
    text = render_template()

    for key in (
        "sarif",
        "before_side",
        "snapshot_cache",
        "unused_routines",
        "unused_ignore",
        "architecture_options",
        "accuracy_floor",
    ):
        assert key in text, key


def test_the_template_still_round_trips_into_the_shipped_defaults() -> None:
    """The new lines must not change what the rendered file means (requirement 3.9)."""
    assert Settings.model_validate(tomllib.loads(render_template())) == default_settings()
