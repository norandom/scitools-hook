"""Configuration validation with and without an Understand metric catalogue (req 3.6, 3.8)."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.metric_names import Scope
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
    spec = ThresholdSpec(scope="routine", metric="CountPath", limit=Limit(max=100))
    validate_settings(one(spec, languages=["Python", "C++"]), CATALOGUE)


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
    spec = ThresholdSpec(scope="routine", metric="CountParams", limit=Limit(max=5))
    validate_settings(one(spec, languages=["Python"]), FakeAvailability({}))


def test_project_population_metric_is_checked_against_the_element_scopes() -> None:
    spec = ThresholdSpec(scope="project", metric="AVG:CyclomaticStrict", limit=Limit(max=3))
    validate_settings(one(spec, languages=["Python"]), CATALOGUE)


def test_availability_is_skipped_when_no_language_is_configured() -> None:
    spec = ThresholdSpec(scope="routine", metric="CountNotAMetric", limit=Limit(max=1))
    validate_settings(one(spec), FakeAvailability({}))
