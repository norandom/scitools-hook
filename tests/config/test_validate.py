"""Configuration validation with and without an Understand metric catalogue (req 3.6, 3.8)."""

from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

import pytest

from scitools_hook.config.defaults import DEFAULT_THRESHOLDS, default_settings
from scitools_hook.config.loader import attach_source, load_settings
from scitools_hook.config.metric_names import Scope, parse_metric_name
from scitools_hook.config.models import (
    CouplingRule,
    IgnoreRules,
    LayerRule,
    Limit,
    ProjectSettings,
    Settings,
    StructureRules,
    ThresholdSpec,
)
from scitools_hook.config.template import CONFIG_FILENAME, render_template
from scitools_hook.config.validate import MetricAvailability, validate_settings
from scitools_hook.errors import ConfigError
from scitools_hook.exit_codes import ExitCode


class FakeAvailability:
    """Stand-in for ``understand.catalogue.MetricCatalogue``: metric ids per language/scope."""

    def __init__(self, metrics: Mapping[tuple[str, str], set[str]]) -> None:
        self._metrics = dict(metrics)

    def available(self, language: str, scope: Scope) -> set[str]:
        return set(self._metrics.get((language, scope), set()))


CATALOGUE = FakeAvailability(
    {
        ("Python", "routine"): {"CyclomaticStrict", "CountLineCode"},
        ("C++", "routine"): {"CyclomaticStrict", "CountLineCode", "CountPath"},
        ("C++", "class"): {"PercentLackOfCohesion"},
    }
)


def one(spec: ThresholdSpec, languages: list[str] | None = None) -> Settings:
    """A settings object carrying exactly one threshold (and optional languages)."""
    return Settings(thresholds=[spec], project=ProjectSettings(languages=languages))


# --- checks that need no catalogue -----------------------------------------------


def test_default_settings_validate_without_a_catalogue() -> None:
    validate_settings(default_settings(), None)


def test_synthetic_metric_outside_its_scope_is_rejected() -> None:
    spec = ThresholdSpec(scope="class", metric="CountParams", limit=Limit(max=5))
    with pytest.raises(ConfigError) as caught:
        validate_settings(one(spec), None)
    assert caught.value.key == "thresholds.class.CountParams"
    assert caught.value.exit_code is ExitCode.CONFIG_ERROR
    assert caught.value.file is None


def test_synthetic_metric_at_its_own_scope_is_accepted() -> None:
    validate_settings(
        one(ThresholdSpec(scope="routine", metric="CountParams", limit=Limit(max=5))), None
    )


def test_population_prefix_on_a_synthetic_metric_is_allowed_at_project_scope() -> None:
    spec = ThresholdSpec(scope="project", metric="AVG:CountParams", limit=Limit(max=3))
    validate_settings(one(spec), None)


def test_population_threshold_is_rejected_for_the_architecture_scope() -> None:
    spec = ThresholdSpec(scope="arch", metric="AVG:CountLineCode", limit=Limit(max=3))
    with pytest.raises(ConfigError) as caught:
        validate_settings(one(spec), None)
    assert caught.value.key == "thresholds.arch.AVG:CountLineCode"


def test_metric_grammar_is_rechecked_on_a_hand_built_settings() -> None:
    spec = ThresholdSpec.model_construct(scope="routine", metric="A:B:C", limit=Limit(max=1))
    with pytest.raises(ConfigError) as caught:
        validate_settings(one(spec), None)
    assert caught.value.key == "thresholds.routine.A:B:C"


def test_unknown_scope_is_rejected() -> None:
    spec = ThresholdSpec.model_construct(scope="module", metric="CountLineCode", limit=Limit(max=1))
    with pytest.raises(ConfigError) as caught:
        validate_settings(one(spec), None)
    assert caught.value.key == "thresholds.module.CountLineCode"


def test_invalid_ignore_regex_is_rejected() -> None:
    settings = default_settings()
    settings.ignore = IgnoreRules.model_construct(files=[], classes=[], routines=["(unclosed"])
    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, None)
    assert caught.value.key == "ignore.routines"


def test_architecture_depth_below_one_is_rejected() -> None:
    settings = default_settings()
    settings.structure = StructureRules.model_construct(depth=0)
    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, None)
    assert caught.value.key == "structure.depth"


def test_empty_architecture_name_is_rejected() -> None:
    settings = default_settings()
    settings.structure = StructureRules.model_construct(architecture="  ", depth=2)
    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, None)
    assert caught.value.key == "structure.architecture"


def test_well_formed_layer_and_coupling_rules_are_accepted() -> None:
    settings = default_settings()
    settings.structure.layers = [LayerRule(name="cli", node="src/cli", may_depend_on=["runner"])]
    settings.structure.coupling = [CouplingRule(from_node="src", to_node="lib", max_refs=5)]
    validate_settings(settings, None)


def test_layer_rule_with_an_empty_node_is_rejected() -> None:
    settings = default_settings()
    settings.structure.layers = [
        LayerRule(name="report", node="src/report", may_depend_on=["analysis"]),
        LayerRule(name="cli", node="  ", may_depend_on=["runner"]),
    ]
    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, None)
    assert caught.value.key == "structure.layers"


def test_coupling_rule_with_an_empty_node_is_rejected() -> None:
    settings = default_settings()
    settings.structure.coupling = [
        CouplingRule(from_node="src", to_node="lib", max_refs=5),
        CouplingRule(from_node="src", to_node="", max_refs=5),
    ]
    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, None)
    assert caught.value.key == "structure.coupling"


def test_unknown_fan_key_is_rejected() -> None:
    settings = default_settings()
    settings.structure = StructureRules.model_construct(fan={"file_fanout": Limit(max=3)})
    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, None)
    assert caught.value.key == "structure.fan.file_fanout"


# --- checks against a metric catalogue -------------------------------------------


def test_fake_catalogue_satisfies_the_protocol() -> None:
    assert isinstance(CATALOGUE, MetricAvailability)


def test_metric_available_for_one_configured_language_is_accepted() -> None:
    # Only C++ has CountPath here. It is a shipped default, so "does not raise" would pass
    # even if the language loop stopped looking after Python — assert it is evaluated.
    spec = ThresholdSpec(scope="routine", metric="CountPath", limit=Limit(max=100))

    report = validate_settings(one(spec, languages=["Python", "C++"]), CATALOGUE)

    assert rules_of(report.thresholds) == {"routine.CountPath"}
    assert report.dropped == ()


def test_metric_unavailable_for_every_configured_language_is_rejected() -> None:
    spec = ThresholdSpec(scope="routine", metric="CountNotAMetric", limit=Limit(max=1))
    with pytest.raises(ConfigError) as caught:
        validate_settings(one(spec, languages=["Python", "C++"]), CATALOGUE)
    assert caught.value.key == "thresholds.routine.CountNotAMetric"
    assert "Python" in caught.value.message


def test_metric_available_only_for_another_scope_is_rejected() -> None:
    spec = ThresholdSpec(scope="class", metric="CountLineCode", limit=Limit(max=1))
    with pytest.raises(ConfigError) as caught:
        validate_settings(one(spec, languages=["Python"]), CATALOGUE)
    assert caught.value.key == "thresholds.class.CountLineCode"


def test_synthetic_metrics_bypass_the_catalogue() -> None:
    # The catalogue knows Python but not this metric: the Gate computes it itself (req 3.5).
    # Asserting on the report, not on "does not raise": `routine.CountParams` is a shipped
    # default, so losing the bypass would drop it silently instead of raising.
    spec = ThresholdSpec(scope="routine", metric="CountParams", limit=Limit(max=5))

    report = validate_settings(one(spec, languages=["Python"]), CATALOGUE)

    assert rules_of(report.thresholds) == {"routine.CountParams"}
    assert report.dropped == ()


def test_a_synthetic_metric_the_defaults_do_not_ship_also_bypasses_the_catalogue() -> None:
    # `AVG:CountParams` is deliberately not in DEFAULT_THRESHOLDS, so no drop path can
    # swallow a lost bypass here: it would have to raise.
    spec = ThresholdSpec(scope="project", metric="AVG:CountParams", limit=Limit(max=3))

    report = validate_settings(one(spec, languages=["Python"]), CATALOGUE)

    assert rules_of(report.thresholds) == {"project.AVG:CountParams"}


def test_project_population_metric_is_checked_against_the_element_scopes() -> None:
    # The catalogue has CyclomaticStrict for Python at the routine scope only, so a lost
    # fan-out would find nothing — and, this being a shipped default, would drop rather than
    # raise. The assertion is on the report for that reason.
    spec = ThresholdSpec(scope="project", metric="AVG:CyclomaticStrict", limit=Limit(max=3))

    report = validate_settings(one(spec, languages=["Python"]), CATALOGUE)

    assert rules_of(report.thresholds) == {"project.AVG:CyclomaticStrict"}
    assert report.dropped == ()


def test_a_population_metric_the_defaults_do_not_ship_reaches_the_element_scopes_too() -> None:
    # `project.AVG:CountPath` is not a shipped default, so a lost fan-out raises here.
    spec = ThresholdSpec(scope="project", metric="AVG:CountPath", limit=Limit(max=50))

    report = validate_settings(one(spec, languages=["C++"]), CATALOGUE)

    assert rules_of(report.thresholds) == {"project.AVG:CountPath"}


def test_availability_is_skipped_when_no_language_is_configured() -> None:
    # `languages` is unset by default and `init` writes it commented out, so this is the
    # shipped configuration: every threshold must come back evaluated, not silently zero.
    spec = ThresholdSpec(scope="routine", metric="CountNotAMetric", limit=Limit(max=1))
    settings = one(spec)

    report = validate_settings(settings, FakeAvailability({}))

    assert list(report.thresholds) == settings.thresholds
    assert report.dropped == ()
    assert report.unavailable == {}


# --- thresholds the built-in defaults ship (req 3.1) -----------------------------


DEFAULT_METRICS: Final[frozenset[str]] = frozenset(
    parse_metric_name(metric).metric for table in DEFAULT_THRESHOLDS.values() for metric in table
)
"""Every metric the built-in defaults name, with the stats prefixes stripped."""


class DefaultCatalogue:
    """A catalogue holding every metric the defaults name, minus the ones named here."""

    def __init__(self, *missing: str) -> None:
        self.missing = set(missing)

    def available(self, language: str, scope: Scope) -> set[str]:
        return set(DEFAULT_METRICS - self.missing)


def python_defaults() -> Settings:
    """The built-in defaults on a Python-only repository, exactly as req 3.1 ships them."""
    settings = default_settings()
    settings.project.languages = ["Python"]
    return settings


def write_config(repo: Path, body: str) -> Path:
    path = repo / CONFIG_FILENAME
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def rules_of(specs: Sequence[ThresholdSpec]) -> set[str]:
    return {spec.rule for spec in specs}


def test_defaults_on_a_python_repository_do_not_stop_the_gate() -> None:
    """No configuration file, Python only: the Gate runs on its own defaults (req 3.1)."""
    report = validate_settings(python_defaults(), DefaultCatalogue("PercentLackOfCohesion"))

    assert "class.CountDeclMethod" in rules_of(report.thresholds)


def test_a_default_metric_the_language_lacks_is_reported_as_not_evaluated() -> None:
    """The dropped threshold is visible per language, in ``unavailable_metrics`` shape (5.5)."""
    report = validate_settings(python_defaults(), DefaultCatalogue("PercentLackOfCohesion"))

    assert rules_of(report.dropped) == {"class.PercentLackOfCohesion"}
    assert "class.PercentLackOfCohesion" not in rules_of(report.thresholds)
    assert report.unavailable == {"Python": ("PercentLackOfCohesion",)}


def test_a_dropped_default_is_reported_without_its_stats_prefix() -> None:
    """A stats-prefixed default drops as well, and both report the bare metric id — which is
    what the catalogue, the snapshot and ``analysis.thresholds`` match on (req 5.5)."""
    report = validate_settings(python_defaults(), DefaultCatalogue("CyclomaticStrict"))

    assert rules_of(report.dropped) == {"routine.CyclomaticStrict", "project.AVG:CyclomaticStrict"}
    assert report.unavailable == {"Python": ("CyclomaticStrict",)}


def test_every_dropped_metric_is_reported_not_just_the_first() -> None:
    """Req 5.5 names *metrics*, plural: two unavailable defaults must both be reported."""
    report = validate_settings(
        python_defaults(), DefaultCatalogue("PercentLackOfCohesion", "CountClassCoupled")
    )

    assert rules_of(report.dropped) == {"class.PercentLackOfCohesion", "class.CountClassCoupled"}
    assert report.unavailable == {"Python": ("CountClassCoupled", "PercentLackOfCohesion")}


def test_a_drop_is_reported_for_every_configured_language() -> None:
    """Req 5.5 is "which metrics were unavailable for which language": every one of them."""
    settings = default_settings()
    settings.project.languages = ["Python", "Ada"]

    report = validate_settings(settings, DefaultCatalogue("PercentLackOfCohesion"))

    assert report.unavailable == {
        "Ada": ("PercentLackOfCohesion",),
        "Python": ("PercentLackOfCohesion",),
    }


def test_the_reported_drops_cannot_be_edited_by_a_caller() -> None:
    """A frozen report that handed out a live dict would be frozen in name only — including
    the empty report, which is what the shipped configuration (no ``languages``) produces."""
    with_drops = validate_settings(python_defaults(), DefaultCatalogue("PercentLackOfCohesion"))
    without_drops = validate_settings(python_defaults(), DefaultCatalogue())
    without_languages = validate_settings(default_settings(), DefaultCatalogue())

    for report in (with_drops, without_drops, without_languages):
        with pytest.raises(TypeError):
            report.unavailable["Python"] = ()  # type: ignore[index]


def test_a_default_metric_the_language_has_is_still_evaluated() -> None:
    """The same default is a real threshold on a C++ repository (req 5.2)."""
    settings = default_settings()
    settings.project.languages = ["C++"]

    report = validate_settings(settings, DefaultCatalogue())

    assert "class.PercentLackOfCohesion" in rules_of(report.thresholds)
    assert report.dropped == ()
    assert report.unavailable == {}


def test_every_threshold_is_reported_as_evaluated_without_a_catalogue() -> None:
    """The report is the caller's threshold list even when no catalogue was consulted."""
    settings = python_defaults()

    report = validate_settings(settings, None)

    assert list(report.thresholds) == settings.thresholds
    assert report.dropped == ()


def test_a_metric_the_gate_does_not_ship_is_still_rejected_at_a_shipped_metrics_scope() -> None:
    """Only the exact threshold the defaults ship is dropped; a metric moved to another scope
    is a name this Gate never configured, so 3.8 applies."""
    spec = ThresholdSpec(scope="routine", metric="PercentLackOfCohesion", limit=Limit(max=70))
    settings = one(spec, languages=["Python"])

    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, DefaultCatalogue("PercentLackOfCohesion"))

    assert caught.value.key == "thresholds.routine.PercentLackOfCohesion"
    assert caught.value.exit_code is ExitCode.CONFIG_ERROR


# --- a language Understand does not have (req 3.8) -------------------------------


class ArchOnlyCatalogue:
    """Answers only at the ``arch`` scope, as the real catalogue does for any language name.

    The architecture metric list carries no language (measured), so it answers for a typo
    exactly as it answers for Python — which is why the language check must not ask it.
    """

    def available(self, language: str, scope: Scope) -> set[str]:
        return {"CountEntities"} if scope == "arch" else set()


def test_a_language_the_catalogue_has_no_metric_for_is_rejected() -> None:
    """A misspelt language would otherwise drop every default and run green on no rules."""
    settings = default_settings()
    settings.project.languages = ["Pyhton"]

    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, FakeAvailability({}))

    assert caught.value.key == "project.languages"
    assert "Pyhton" in caught.value.message
    assert caught.value.exit_code is ExitCode.CONFIG_ERROR
    # The hint is the operator's only next step, so it must name a command that exists:
    # `und -languages` does not (it exits 1 with "No valid command found").
    assert "und list settings" in (caught.value.hint or "")
    assert "und -languages" not in (caught.value.hint or "")


def test_the_architecture_scope_does_not_vouch_for_a_language() -> None:
    settings = default_settings()
    settings.project.languages = ["Pyhton"]

    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, ArchOnlyCatalogue())

    assert caught.value.key == "project.languages"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_language_name_is_rejected(blank: str) -> None:
    """A kind string with no language matches every language at once, so a blank name would
    quietly widen every threshold to the union instead of narrowing it."""
    settings = default_settings()
    settings.project.languages = [blank]

    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, DefaultCatalogue())

    assert caught.value.key == "project.languages"
    assert caught.value.exit_code is ExitCode.CONFIG_ERROR


def test_a_real_language_with_metrics_at_one_scope_only_is_accepted() -> None:
    """An unusual language may have few metrics; one anywhere is proof it exists."""
    spec = ThresholdSpec(scope="project", metric="CountLineCode", limit=Limit(max=10))
    settings = one(spec, languages=["Jovial"])

    report = validate_settings(
        settings, FakeAvailability({("Jovial", "project"): {"CountLineCode"}})
    )

    assert rules_of(report.thresholds) == {"project.CountLineCode"}


class OneLanguageCatalogue:
    """Has every default metric, but only under one language name — as an install does."""

    def __init__(self, language: str) -> None:
        self.language = language

    def available(self, language: str, scope: Scope) -> set[str]:
        return set(DEFAULT_METRICS) if language == self.language else set()


def test_every_configured_language_is_checked_not_just_the_first() -> None:
    settings = default_settings()
    settings.project.languages = ["Python", "Pyhton"]

    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, OneLanguageCatalogue("Python"))

    assert "Pyhton" in caught.value.message


# --- metrics an actual configuration file names (req 3.8) ------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """An isolated XDG_CONFIG_HOME so the real user configuration is never read."""
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    return {"XDG_CONFIG_HOME": str(xdg)}


def test_a_misspelled_metric_in_a_configuration_file_stops_the_gate(
    repo: Path, env: dict[str, str]
) -> None:
    """3.8 as written: the file, the key and the problem, at the configuration exit code."""
    path = write_config(
        repo,
        """
        [project]
        languages = ["Python"]

        [thresholds.routine]
        CyclomaticStrickt = 10
        """,
    )
    settings, provenance = load_settings(repo, {}, env)

    with pytest.raises(ConfigError) as caught:
        validate_settings(settings, DefaultCatalogue("PercentLackOfCohesion"))

    located = attach_source(caught.value, provenance)
    assert located.key == "thresholds.routine.CyclomaticStrickt"
    assert located.file == path
    assert located.exit_code is ExitCode.CONFIG_ERROR


def test_a_shipped_default_a_configuration_file_repeats_is_dropped_not_rejected(
    repo: Path, env: dict[str, str]
) -> None:
    """Who wrote the value cannot decide this: ``init`` writes every default into the file, so
    a rule keyed on the file would stop the Gate on the very configuration it generates."""
    write_config(
        repo,
        """
        [project]
        languages = ["Python"]

        [thresholds.class]
        PercentLackOfCohesion = 80
        """,
    )
    settings, _ = load_settings(repo, {}, env)

    report = validate_settings(settings, DefaultCatalogue("PercentLackOfCohesion"))

    assert rules_of(report.dropped) == {"class.PercentLackOfCohesion"}
    assert report.unavailable == {"Python": ("PercentLackOfCohesion",)}


def test_the_configuration_init_writes_runs_on_a_python_repository(
    repo: Path, env: dict[str, str]
) -> None:
    """``scitools-hook init`` then ``languages = ["Python"]`` must still start (req 3.9, 3.1)."""
    settings = python_defaults()
    (repo / CONFIG_FILENAME).write_text(render_template(settings), encoding="utf-8")
    loaded, _ = load_settings(repo, {}, env)

    report = validate_settings(loaded, DefaultCatalogue("PercentLackOfCohesion"))

    assert loaded.project.languages == ["Python"]
    assert rules_of(report.dropped) == {"class.PercentLackOfCohesion"}
    assert "class.CountDeclMethod" in rules_of(report.thresholds)
