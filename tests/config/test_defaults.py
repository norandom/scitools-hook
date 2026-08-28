"""Built-in defaults: thresholds per scope, excludes, severities (req 2.5, 3.1, 5.1-5.4)."""

from __future__ import annotations

import pytest

from scitools_hook.config.defaults import (
    DEFAULT_EXCLUDES,
    DEFAULT_FAN,
    DEFAULT_HINTS,
    DEFAULT_INCLUDES,
    DEFAULT_SEVERITIES,
    DEFAULT_THRESHOLDS,
    default_settings,
)
from scitools_hook.config.metric_names import SYNTHETIC_METRICS, Scope, parse_metric_name
from scitools_hook.config.models import Limit, Settings, ThresholdSpec

# Metrics the requirements name for each scope (5.1-5.4); defaults must cover at least these.
REQUIRED_METRICS: dict[Scope, set[str]] = {
    "routine": {
        "CyclomaticStrict",
        "CyclomaticModified",
        "Essential",
        "MaxNesting",
        "CountLineCode",
        "CountStmt",
        "CountParams",
        "CountPath",
    },
    "class": {
        "CountDeclMethod",
        "CountDeclMethodNonStub",
        "CountDeclInstanceVariable",
        "MaxInheritanceTree",
        "CountClassDerived",
        "CountClassCoupled",
        "PercentLackOfCohesion",
    },
    "file": {
        "CountLineCode",
        "CountDeclFunction",
        "CountDeclClass",
        "MaxCyclomaticStrict",
        "RatioCommentToCode",
    },
    "project": {"AVG:CyclomaticStrict", "MaxCyclomaticStrict", "AVG:CountLineCode", "MaxNesting"},
}


def _by_rule(settings: Settings) -> dict[str, ThresholdSpec]:
    return {spec.rule: spec for spec in settings.thresholds}


# --- shape ---------------------------------------------------------------------------


def test_default_thresholds_cover_exactly_the_four_scopes() -> None:
    assert list(DEFAULT_THRESHOLDS) == ["routine", "class", "file", "project"]


@pytest.mark.parametrize("scope", list(REQUIRED_METRICS))
def test_defaults_cover_every_metric_named_in_the_requirements(scope: Scope) -> None:
    assert REQUIRED_METRICS[scope] <= set(DEFAULT_THRESHOLDS[scope])


def test_every_default_metric_name_parses() -> None:
    for table in DEFAULT_THRESHOLDS.values():
        for name in table:
            parse_metric_name(name)


def test_default_settings_validate_without_a_config_file() -> None:
    settings = default_settings()
    assert isinstance(settings, Settings)
    assert len(settings.thresholds) == sum(len(t) for t in DEFAULT_THRESHOLDS.values())


def test_default_settings_returns_independent_equal_instances() -> None:
    first, second = default_settings(), default_settings()
    assert first == second
    first.thresholds.clear()
    assert default_settings() == second


# --- values --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "limit"),
    [
        ("routine.CyclomaticStrict", Limit(max=10)),
        ("routine.CyclomaticModified", Limit(max=8)),
        ("routine.Essential", Limit(max=4)),
        ("routine.MaxNesting", Limit(max=3)),
        ("routine.CountLineCode", Limit(max=60)),
        ("routine.CountStmt", Limit(max=40)),
        ("routine.CountParams", Limit(max=5)),
        ("routine.CountPath", Limit(max=100)),
        ("class.CountDeclMethod", Limit(max=20)),
        ("class.CountDeclMethodNonStub", Limit(max=15)),
        ("class.CountDeclInstanceVariable", Limit(max=10)),
        ("class.MaxInheritanceTree", Limit(max=4)),
        ("class.CountClassDerived", Limit(max=8)),
        ("class.CountClassCoupled", Limit(max=12)),
        ("class.PercentLackOfCohesion", Limit(max=70)),
        ("file.CountLineCode", Limit(max=500)),
        ("file.CountDeclFunction", Limit(max=25)),
        ("file.CountDeclClass", Limit(max=3)),
        ("file.MaxCyclomaticStrict", Limit(max=10)),
        ("file.RatioCommentToCode", Limit(min=0.1)),
        ("project.AVG:CyclomaticStrict", Limit(max=3)),
        ("project.MaxCyclomaticStrict", Limit(max=15)),
        ("project.AVG:CountLineCode", Limit(max=30)),
        ("project.MaxNesting", Limit(max=5)),
    ],
)
def test_default_limit_values(rule: str, limit: Limit) -> None:
    assert _by_rule(default_settings())[rule].limit == limit


def test_synthetic_metrics_are_defaulted_in_their_declared_scope() -> None:
    by_rule = _by_rule(default_settings())
    for metric in SYNTHETIC_METRICS.values():
        assert f"{metric.scope}.{metric.id}" in by_rule


def test_project_population_thresholds_use_stats_prefixes() -> None:
    project = [s for s in default_settings().thresholds if s.scope == "project"]
    assert {s.ref.prefix for s in project if s.ref.is_population} == {"AVG"}


# --- severities --------------------------------------------------------------------


def test_soft_metrics_default_to_warning_and_all_others_to_error() -> None:
    by_rule = _by_rule(default_settings())
    warnings = {rule for rule, spec in by_rule.items() if spec.severity == "warning"}
    assert warnings == {"file.RatioCommentToCode", "class.PercentLackOfCohesion"}
    assert all(spec.ratchet for spec in by_rule.values())


def test_default_severity_map_covers_every_threshold_and_structural_rule() -> None:
    by_rule = _by_rule(default_settings())
    threshold_keys = {
        k for k in DEFAULT_SEVERITIES if not k.startswith(("structure.", "codecheck"))
    }
    assert threshold_keys == set(by_rule)
    assert {
        "structure.file_cycle",
        "structure.arch_cycle",
        "structure.fan_in",
        "structure.fan_out",
        "structure.new_dependencies",
        "structure.layer",
        "structure.coupling",
        "codecheck",
    } <= set(DEFAULT_SEVERITIES)


def test_default_settings_apply_the_structural_and_codecheck_severities() -> None:
    settings = default_settings()
    assert settings.structure.file_cycles == DEFAULT_SEVERITIES["structure.file_cycle"] == "error"
    assert settings.structure.arch_cycles == DEFAULT_SEVERITIES["structure.arch_cycle"] == "error"
    assert settings.structure.fan_severity == DEFAULT_SEVERITIES["structure.fan_out"] == "warning"
    assert DEFAULT_SEVERITIES["structure.fan_in"] == "warning"
    assert settings.codecheck.severity == DEFAULT_SEVERITIES["codecheck"] == "warning"
    assert settings.structure.new_dependencies_severity == "error"


# --- project patterns, fan, hints -------------------------------------------------


def test_default_includes_everything() -> None:
    assert DEFAULT_INCLUDES == ["**"]
    assert default_settings().project.include == ["**"]


@pytest.mark.parametrize(
    "pattern",
    [".git/**", "node_modules/**", ".venv/**", "venv/**", "build/**", "dist/**", "target/**"],
)
def test_default_excludes_cover_vcs_dependency_and_build_directories(pattern: str) -> None:
    assert pattern in DEFAULT_EXCLUDES


@pytest.mark.parametrize(
    "pattern",
    ["__pycache__/**", "*.min.js", "*.generated.*", "*.lock", "uv.lock", "package-lock.json"],
)
def test_default_excludes_cover_generated_files(pattern: str) -> None:
    assert pattern in DEFAULT_EXCLUDES


def test_default_settings_use_the_default_excludes_and_no_languages() -> None:
    settings = default_settings()
    assert settings.project.exclude == list(DEFAULT_EXCLUDES)
    assert settings.project.languages is None


def test_default_fan_limits_are_installed_as_warnings() -> None:
    settings = default_settings()
    assert set(settings.structure.fan) == set(DEFAULT_FAN)
    assert settings.structure.fan["file_fan_out"] == Limit(max=20)
    assert settings.structure.fan_severity == "warning"


def test_default_hints_is_an_empty_override_map() -> None:
    assert DEFAULT_HINTS == {}
    assert default_settings().hints == {}
