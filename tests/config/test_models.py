"""Configuration models: limits, threshold specs, table flattening, structure (req 3.3, 3.7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from scitools_hook.config.metric_names import MetricRef
from scitools_hook.config.models import (
    FAN_KEYS,
    BaselineSettings,
    CodeCheckSettings,
    CouplingRule,
    IgnoreRules,
    LayerRule,
    Limit,
    OutputSettings,
    ProjectSettings,
    Provenance,
    RatchetSettings,
    Settings,
    StructureRules,
    ThresholdSpec,
    UnderstandSettings,
    thresholds_from_tables,
)

ALL_MODELS: list[type[BaseModel]] = [
    Limit,
    ThresholdSpec,
    LayerRule,
    CouplingRule,
    StructureRules,
    CodeCheckSettings,
    BaselineSettings,
    IgnoreRules,
    ProjectSettings,
    UnderstandSettings,
    OutputSettings,
    RatchetSettings,
    Settings,
    Provenance,
]


# --- Limit -----------------------------------------------------------------------


def test_scalar_limit_means_max() -> None:
    assert Limit.model_validate(10) == Limit(max=10.0, min=None)
    assert Limit.model_validate(0.5) == Limit(max=0.5)


def test_table_limit_keeps_min_and_max() -> None:
    assert Limit.model_validate({"min": 0.1}) == Limit(min=0.1)
    assert Limit.model_validate({"min": 1, "max": 5}) == Limit(min=1.0, max=5.0)


def test_limit_without_max_or_min_is_rejected() -> None:
    with pytest.raises(ValidationError, match="max.*min|min.*max"):
        Limit()


def test_limit_with_max_below_min_is_rejected() -> None:
    with pytest.raises(ValidationError, match="max"):
        Limit(max=1, min=2)


@pytest.mark.parametrize("bad", [True, "10", [10], {"max": "10"}, {"max": True}, {"max": None}])
def test_limit_of_the_wrong_type_is_rejected(bad: object) -> None:
    with pytest.raises(ValidationError):
        Limit.model_validate(bad)


# --- ThresholdSpec ------------------------------------------------------------------


def test_threshold_spec_defaults_and_scalar_limit() -> None:
    spec = ThresholdSpec.model_validate(
        {"scope": "routine", "metric": "CyclomaticStrict", "limit": 10}
    )
    assert spec.limit == Limit(max=10.0)
    assert spec.severity == "error"
    assert spec.ratchet is True


def test_threshold_spec_keeps_the_raw_prefixed_name_and_parses_it() -> None:
    spec = ThresholdSpec(scope="project", metric="avg:CyclomaticStrict", limit=Limit(max=3))
    assert spec.metric == "avg:CyclomaticStrict"
    assert spec.ref == MetricRef("AVG", "CyclomaticStrict")
    assert spec.rule == "project.AVG:CyclomaticStrict"


def test_threshold_spec_rule_name_is_scope_dot_metric() -> None:
    spec = ThresholdSpec(scope="file", metric="RatioCommentToCode", limit=Limit(min=0.1))
    assert spec.rule == "file.RatioCommentToCode"


@pytest.mark.parametrize("raw", ["FOO:CyclomaticStrict", "A:B:C", "Cyclomatic-Strict", ""])
def test_threshold_spec_with_an_invalid_metric_name_is_a_validation_error(raw: str) -> None:
    with pytest.raises(ValidationError, match="metric"):
        ThresholdSpec(scope="routine", metric=raw, limit=Limit(max=1))


def test_threshold_spec_with_an_unknown_scope_is_rejected() -> None:
    with pytest.raises(ValidationError, match="scope"):
        ThresholdSpec.model_validate({"scope": "module", "metric": "CountLineCode", "limit": 1})


def test_threshold_spec_with_an_unknown_severity_is_rejected() -> None:
    with pytest.raises(ValidationError, match="severity"):
        ThresholdSpec(scope="routine", metric="CountLineCode", limit=1, severity="fatal")  # type: ignore[arg-type]


# --- the ratchet default, which depends on the metric (task 11.9) -------------------


def test_a_decomposition_count_defaults_its_ratchet_off() -> None:
    """``file.CountDeclFunction`` goes up precisely when a routine is extracted (11.9)."""
    spec = ThresholdSpec(scope="file", metric="CountDeclFunction", limit=Limit(max=25))

    assert spec.ratchet is False


def test_another_metric_at_the_same_scope_defaults_its_ratchet_on() -> None:
    """The sibling of the case above, differing only in the metric name.

    ``file.MaxCyclomaticStrict`` is a file-scope threshold too, and no decomposition raises
    it -- so a change that answered by scope rather than by rule would fail here.
    """
    spec = ThresholdSpec(scope="file", metric="MaxCyclomaticStrict", limit=Limit(max=10))

    assert spec.ratchet is True


def test_the_same_metric_name_at_the_routine_scope_defaults_its_ratchet_on() -> None:
    """``CountLineCode`` is one of the eight at file scope and none of them at routine scope.

    The routine that was split is the entity whose own numbers fall, so its length stays
    compared; the file it lives in has nothing to show but the lines the new signature added.
    """
    assert (
        ThresholdSpec(scope="file", metric="CountLineCode", limit=Limit(max=500)).ratchet is False
    )
    assert (
        ThresholdSpec(scope="routine", metric="CountLineCode", limit=Limit(max=60)).ratchet is True
    )


def test_an_operator_can_switch_a_decomposition_count_back_on() -> None:
    """``ratchet`` written out wins over the metric's default, in the direction that adds it."""
    (spec,) = thresholds_from_tables({"file": {"CountDeclFunction": {"max": 25, "ratchet": True}}})

    assert spec.rule == "file.CountDeclFunction"
    assert spec.ratchet is True


def test_a_decomposition_count_written_as_a_bare_number_still_defaults_off() -> None:
    """The TOML shape an operator most often writes carries no ``ratchet`` key at all."""
    (spec,) = thresholds_from_tables({"class": {"CountDeclMethod": 30}})

    assert spec.rule == "class.CountDeclMethod"
    assert spec.limit == Limit(max=30)
    assert spec.ratchet is False


def test_a_spec_that_round_trips_through_json_keeps_the_ratchet_it_resolved() -> None:
    """The resolved value travels as data; nothing downstream re-derives it from the name."""
    original = ThresholdSpec(scope="class", metric="CountClassCoupled", limit=Limit(max=12))

    restored = ThresholdSpec.model_validate(original.model_dump())

    assert original.ratchet is False
    assert restored.ratchet is False


# --- thresholds_from_tables ------------------------------------------------------


def test_tables_flatten_scalar_table_and_prefixed_forms() -> None:
    specs = thresholds_from_tables(
        {
            "routine": {"CyclomaticStrict": 10, "CountLineCode": {"max": 60}},
            "file": {"RatioCommentToCode": {"min": 0.1}},
            "project": {"AVG:CyclomaticStrict": 3},
        }
    )
    assert [s.rule for s in specs] == [
        "routine.CyclomaticStrict",
        "routine.CountLineCode",
        "file.RatioCommentToCode",
        "project.AVG:CyclomaticStrict",
    ]
    assert specs[0].limit == Limit(max=10)
    assert specs[1].limit == Limit(max=60)
    assert specs[2].limit == Limit(min=0.1)
    assert specs[3].ref.is_population is True


def test_tables_accept_severity_and_ratchet_inside_a_threshold_table() -> None:
    (spec,) = thresholds_from_tables(
        {"class": {"PercentLackOfCohesion": {"max": 70, "severity": "warning", "ratchet": False}}}
    )
    assert spec.severity == "warning"
    assert spec.ratchet is False
    assert spec.limit == Limit(max=70)


def test_tables_with_an_unknown_scope_name_the_scope() -> None:
    with pytest.raises(ValueError, match="module"):
        thresholds_from_tables({"module": {"CountLineCode": 1}})


@pytest.mark.parametrize("bad", ["10", True, [10], None])
def test_tables_with_a_wrong_value_type_name_the_key(bad: object) -> None:
    with pytest.raises(ValueError, match="CountLineCode"):
        thresholds_from_tables({"file": {"CountLineCode": bad}})


def test_tables_with_an_unknown_threshold_key_are_rejected() -> None:
    with pytest.raises(ValueError, match="foo"):
        thresholds_from_tables({"file": {"CountLineCode": {"max": 1, "foo": 2}}})


def test_tables_that_are_not_tables_are_rejected() -> None:
    with pytest.raises(ValueError, match="routine"):
        thresholds_from_tables({"routine": 5})  # type: ignore[dict-item]


# --- Settings ---------------------------------------------------------------------


def test_settings_without_arguments_is_valid_and_empty() -> None:
    settings = Settings()
    assert settings.thresholds == []
    assert settings.ratchet.strict is False
    assert settings.understand.api_mode == "auto"
    assert settings.understand.db_location == "cache"
    assert settings.understand.home is None
    assert settings.baseline.file == Path("scitools-hook.baseline.json")
    assert settings.baseline.adaptive is False
    assert settings.hints == {}


def test_settings_flatten_toml_threshold_tables() -> None:
    settings = Settings.model_validate(
        {"thresholds": {"routine": {"MaxNesting": 3}, "project": {"MEDIAN:CountLineCode": 20}}}
    )
    assert [s.rule for s in settings.thresholds] == [
        "routine.MaxNesting",
        "project.MEDIAN:CountLineCode",
    ]


def test_settings_accept_an_explicit_threshold_list() -> None:
    spec = ThresholdSpec(scope="routine", metric="MaxNesting", limit=Limit(max=3))
    assert Settings(thresholds=[spec]).thresholds == [spec]
    as_dicts = Settings.model_validate({"thresholds": [spec.model_dump()]})
    assert as_dicts.thresholds == [spec]


def test_settings_round_trip_through_model_dump() -> None:
    settings = Settings.model_validate(
        {
            "thresholds": {"routine": {"MaxNesting": 3}},
            "ignore": {"files": ["^tests/"]},
            "structure": {"layers": [{"name": "cli", "node": "a/cli", "may_depend_on": ["run"]}]},
        }
    )
    assert Settings.model_validate(settings.model_dump()) == settings
    assert Settings.model_validate(settings.model_dump(mode="json")) == settings


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_every_model_rejects_unknown_keys(model: type[BaseModel]) -> None:
    with pytest.raises(ValidationError, match="nonsense"):
        model.model_validate({"nonsense": 1})


def test_unknown_nested_key_reports_its_location() -> None:
    with pytest.raises(ValidationError) as caught:
        Settings.model_validate({"structure": {"depht": 3}})
    assert ("structure", "depht") == caught.value.errors()[0]["loc"]


# --- IgnoreRules --------------------------------------------------------------------


def test_ignore_rules_default_to_empty_lists() -> None:
    assert IgnoreRules() == IgnoreRules(files=[], classes=[], routines=[])


def test_ignore_rules_accept_valid_regexes() -> None:
    rules = IgnoreRules(files=[r"^tests/.*"], classes=[r"Test\w+"], routines=["^_"])
    assert rules.files == [r"^tests/.*"]


@pytest.mark.parametrize("field", ["files", "classes", "routines"])
def test_ignore_rules_reject_an_invalid_regex_naming_the_pattern(field: str) -> None:
    with pytest.raises(ValidationError, match=r"\(unclosed"):
        IgnoreRules.model_validate({field: ["ok", "(unclosed"]})


# --- StructureRules -----------------------------------------------------------------


def test_structure_rules_defaults() -> None:
    rules = StructureRules()
    assert rules.architecture == "Directory Structure"
    assert rules.depth == 2
    assert rules.file_cycles == "error"
    assert rules.arch_cycles == "error"
    assert rules.max_new_dependencies_per_file == 5
    assert rules.fan == {}
    assert rules.fan_severity == "warning"
    assert rules.new_dependencies_severity == "error"
    assert rules.layers == []
    assert rules.coupling == []


def test_structure_fan_accepts_scalar_and_table_limits_for_known_keys() -> None:
    rules = StructureRules.model_validate(
        {"fan": {"file_fan_out": 20, "class_fan_in": {"max": 30}}}
    )
    assert rules.fan == {"file_fan_out": Limit(max=20), "class_fan_in": Limit(max=30)}
    assert set(FAN_KEYS) == {"file_fan_in", "file_fan_out", "class_fan_in", "class_fan_out"}


def test_structure_fan_rejects_an_unknown_key() -> None:
    with pytest.raises(ValidationError, match="module_fan_out"):
        StructureRules.model_validate({"fan": {"module_fan_out": 1}})


@pytest.mark.parametrize("payload", [{"depth": 0}, {"max_new_dependencies_per_file": -1}])
def test_structure_rules_reject_out_of_range_values(payload: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        StructureRules.model_validate(payload)


def test_structure_rules_allow_disabling_new_dependency_limit() -> None:
    assert StructureRules(max_new_dependencies_per_file=None).max_new_dependencies_per_file is None


def test_layer_and_coupling_rules_carry_a_severity() -> None:
    layer = LayerRule(name="cli", node="Directory Structure/src/cli", may_depend_on=["runner"])
    coupling = CouplingRule(from_node="a", to_node="b", max_refs=10)
    assert layer.severity == "error"
    assert coupling.severity == "error"
    assert LayerRule(name="x", node="y", severity="warning").may_depend_on == []


def test_coupling_rule_rejects_negative_max_refs() -> None:
    with pytest.raises(ValidationError, match="max_refs"):
        CouplingRule(from_node="a", to_node="b", max_refs=-1)


# --- leaf settings ----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["auto", "inprocess", "upython"])
def test_understand_api_mode_accepts_documented_values(mode: str) -> None:
    assert UnderstandSettings.model_validate({"api_mode": mode}).api_mode == mode


@pytest.mark.parametrize("payload", [{"api_mode": "remote"}, {"db_location": "worktree"}])
def test_understand_settings_reject_unknown_enumerations(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        UnderstandSettings.model_validate(payload)


def test_understand_home_is_a_path() -> None:
    settings = UnderstandSettings.model_validate({"home": "/opt/scitools"})
    assert settings.home == Path("/opt/scitools")


def test_project_settings_defaults() -> None:
    project = ProjectSettings()
    assert project.include == ["**"]
    assert project.exclude == []
    assert project.languages is None


def test_codecheck_settings_defaults() -> None:
    assert CodeCheckSettings() == CodeCheckSettings(config=None, severity="warning")


def test_output_settings_defaults_and_bounds() -> None:
    assert OutputSettings() == OutputSettings(graphs_max=20, impact_depth=3, show_highest=False)
    with pytest.raises(ValidationError):
        OutputSettings(impact_depth=-1)


def test_provenance_maps_dotted_keys_to_sources() -> None:
    prov = Provenance(values={"thresholds.routine.MaxNesting": "default"})
    assert prov.values["thresholds.routine.MaxNesting"] == "default"
    assert Provenance().values == {}
