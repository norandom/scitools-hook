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
    is_default_threshold,
)
from scitools_hook.config.metric_names import SYNTHETIC_METRICS, Scope, parse_metric_name
from scitools_hook.config.models import (
    DECOMPOSITION_COUNTS,
    Limit,
    Settings,
    ThresholdSpec,
)
from scitools_hook.report.hints import DEFAULT_CATALOGUE

INHERITANCE_VOCABULARY = ("superclass", "subclass", "inherit", "base", "derive")
"""Every word a hint recommending another inheritance layer would have to use.

Deliberately wider than the phrases that would actually raise ``MaxInheritanceTree``: the
test enumerates what it catches and pins what each one says, so a false positive costs a
reader one glance while a miss would cost the ratchet decision silently.
"""

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
    assert warnings == {
        "file.RatioCommentToCode",
        "class.PercentLackOfCohesion",
        "routine.Essential",
        "class.MaxInheritanceTree",
    }


# --- two metrics that rank the language rather than the code (task 11.14) ------------


@pytest.mark.parametrize("rule", ["routine.Essential", "class.MaxInheritanceTree"])
def test_the_two_language_metrics_keep_their_limits_and_stop_blocking(rule: str) -> None:
    """Measured on Understand 6.5.1204; the limits are unchanged and the severity is not.

    ``routine.Essential`` 4 fires on the fourth guard clause: six guards score 7 and the same
    logic as one ``elif`` ladder scores 1, both at ``CyclomaticStrict`` 7. Its own hint says
    "returns early", so following the hint raises the metric.

    ``class.MaxInheritanceTree`` 4 measures where a base class lives. One unchanged line,
    ``class Model(BaseModel)``, scores 5 when pydantic is on the analysing interpreter's
    ``sys.path`` and 1 when it is not -- and since task 11.10 it deliberately is not, so a
    framework hierarchy four deep now reports 1 while ``class X(Protocol)``, which has no
    hierarchy at all, reports 5.

    The limits stay so the number is still reported honestly; the severity goes so an
    incomparable number cannot refuse a commit. Both are asserted here rather than only in
    the warning set, so raising a limit "to make the rule usable" fails this test too.
    """
    spec = _by_rule(default_settings())[rule]
    assert spec.severity == "warning"
    assert spec.limit == Limit(max=4)


def test_the_complexity_rules_that_carry_the_blocking_half_are_untouched() -> None:
    """Demoting ``Essential`` must not quietly stop the Gate refusing complex routines.

    These four are the reason the demotion is affordable: they are what actually blocks on a
    routine that is too complex, and none of them is decided by whether the routine returns
    early. Asserted as literals beside the demoted neighbour above.
    """
    by_rule = _by_rule(default_settings())
    for rule, limit in (
        ("routine.CyclomaticStrict", 10),
        ("routine.CyclomaticModified", 8),
        ("routine.MaxNesting", 3),
        ("routine.CountPath", 100),
    ):
        assert by_rule[rule].severity == "error", rule
        assert by_rule[rule].limit == Limit(max=limit), rule


def test_no_shipped_hint_asks_for_another_inheritance_layer() -> None:
    """Task 11.9's argument for leaving ``class.MaxInheritanceTree`` ratcheted, as a test.

    Eight shipped counts lost their ratchet because a hint in the catalogue asks for exactly
    the change that raises them. ``MaxInheritanceTree`` deliberately kept its ratchet on the
    argument that **no** hint asks for another inheritance layer -- its own asks for one
    fewer, and ``CountClassDerived``'s pushes to a strategy object rather than a subclass.
    That argument lived only in a docstring.

    "Extract a superclass" is a real refactoring and a plausible future hint -- it is what
    ``CountDeclMethod``'s advice to move methods into a class of their own is one step away
    from -- so this walks the whole catalogue and fails the day one is added, which is the
    day the ratchet decision has to be taken again rather than inherited.
    """
    mentions = {
        key: text
        for key, text in DEFAULT_CATALOGUE.items()
        if any(word in text.lower() for word in INHERITANCE_VOCABULARY)
    }

    assert set(mentions) == {"MaxInheritanceTree", "CountClassDerived"}, (
        "a hint that talks about inheritance has been added or removed; if it recommends "
        f"another layer, class.MaxInheritanceTree must lose its ratchet too: {mentions}"
    )
    assert "replace one layer with composition" in mentions["MaxInheritanceTree"]
    assert "hold the base as a field and delegate to it" in mentions["MaxInheritanceTree"]
    assert "replace the variation with a strategy object" in mentions["CountClassDerived"]
    assert "class.MaxInheritanceTree" not in DECOMPOSITION_COUNTS, (
        "the ratchet stays on precisely while no hint pushes into it"
    )


def test_the_essential_hint_still_recommends_the_style_its_own_limit_ranks_worst() -> None:
    """The other half of 11.14: the contradiction that made ``Essential`` a warning.

    If this hint is ever rewritten to stop recommending an early return, the severity is
    worth revisiting -- so the demotion above is tied to the text that justifies it rather
    than standing alone.
    """
    assert "returns early" in DEFAULT_CATALOGUE["Essential"]


# --- the ratchet the shipped thresholds carry (task 11.9) ---------------------------


def test_the_counts_a_decomposition_raises_ship_with_the_ratchet_off() -> None:
    """Eight shipped thresholds keep their limit and lose their comparison against HEAD.

    Each counts what splitting the container *adds* to it, so "worse than before" is raised
    by exactly the refactoring the Gate's own hints ask for -- measured through the installed
    CLI: extracting two helpers out of one six-deep routine moved ``file.CountDeclFunction``
    1 -> 3 and ``file.CountLineCode`` 10 -> 18 while every metric of the routine that was
    split fell.
    """
    by_rule = _by_rule(default_settings())

    assert {rule for rule, spec in by_rule.items() if not spec.ratchet} == {
        "file.CountDeclFunction",
        "file.CountDeclClass",
        "file.CountLineCode",
        "class.CountDeclMethod",
        "class.CountDeclMethodNonStub",
        "class.CountDeclInstanceVariable",
        "class.CountClassCoupled",
        "class.CountClassDerived",
    }


@pytest.mark.parametrize(
    ("rule", "limit"),
    [
        ("file.CountDeclFunction", Limit(max=25)),
        ("file.CountDeclClass", Limit(max=3)),
        ("file.CountLineCode", Limit(max=500)),
        ("class.CountDeclMethod", Limit(max=20)),
        ("class.CountClassCoupled", Limit(max=12)),
    ],
)
def test_a_threshold_without_a_ratchet_keeps_its_limit_and_its_severity(
    rule: str, limit: Limit
) -> None:
    """Only the comparison stops: a file with 40 functions still fails at 25, as an error."""
    spec = _by_rule(default_settings())[rule]

    assert spec.ratchet is False
    assert spec.limit == limit
    assert spec.severity == "error"


@pytest.mark.parametrize(
    "rule",
    [
        "routine.CountLineCode",
        "routine.CountStmt",
        "routine.MaxNesting",
        "routine.CountParams",
        "file.MaxCyclomaticStrict",
        "class.MaxInheritanceTree",
    ],
)
def test_the_neighbours_of_that_list_still_ratchet(rule: str) -> None:
    """The four nearest misses, one per reason, so the list cannot quietly grow.

    ``routine.CountLineCode`` and ``routine.CountStmt`` are the same metric names as two of
    the eight at a different scope, and they stay on because the routine that was split is
    the entity that shows the improvement. ``file.MaxCyclomaticStrict`` sits in the same
    scope as three of the eight. ``class.MaxInheritanceTree`` does rise when a superclass is
    extracted (measured 0 -> 1), and stays on anyway: no hint in the catalogue asks for
    another inheritance layer, and its own asks for one fewer.
    """
    assert _by_rule(default_settings())[rule].ratchet is True


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


# --- which thresholds the Gate itself ships (req 3.1) ----------------------------


def test_a_shipped_threshold_is_known_as_a_default() -> None:
    """``config.validate`` asks this to tell a default from a metric an operator wrote."""
    assert is_default_threshold("class", "PercentLackOfCohesion")
    assert is_default_threshold("project", "AVG:CyclomaticStrict")


def test_a_metric_the_defaults_do_not_ship_is_not_a_default() -> None:
    assert not is_default_threshold("class", "CyclomaticStrickt")
    assert not is_default_threshold("routine", "PercentLackOfCohesion")


def test_a_shipped_threshold_is_recognised_however_its_prefix_is_written() -> None:
    """The prefix is case-insensitive in TOML, so the two spellings are one threshold."""
    assert is_default_threshold("project", "avg:CyclomaticStrict")
    assert not is_default_threshold("project", "MEDIAN:CyclomaticStrict")


def test_a_string_that_is_not_a_metric_name_is_nobody_s_default() -> None:
    assert not is_default_threshold("routine", "A:B:C")
