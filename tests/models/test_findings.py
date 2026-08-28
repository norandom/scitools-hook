"""Findings vocabulary: the rule-name grammar, Finding, EffectiveThreshold, RunResult (7.1)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from scitools_hook.config.metric_names import MetricRef
from scitools_hook.config.models import Limit, ThresholdSpec
from scitools_hook.errors import ConfigError
from scitools_hook.exit_codes import ExitCode
from scitools_hook.models.findings import (
    STRUCTURE_RULES,
    EffectiveThreshold,
    Finding,
    HighestValue,
    ParsedRule,
    RunResult,
    TightenedLimit,
    build_rule_name,
    codecheck_rule,
    is_valid_rule_name,
    parse_rule_name,
    structure_rule,
)
from scitools_hook.models.snapshot import EntityKey, EntityRef

KEY = EntityKey(scope="routine", path="src/cli/app.py", longname="app.build_parser", parameters="")
REF = EntityRef(key=KEY, kind="Python Function", name="build_parser", line=34)


def _finding(**overrides: object) -> Finding:
    fields: dict[str, object] = {
        "kind": "threshold",
        "rule": "routine.CyclomaticStrict",
        "metric": "CyclomaticStrict",
        "scope": "routine",
        "entity": REF,
        "path": "src/cli/app.py",
        "line": 34,
        "value": 12.0,
        "limit": 10.0,
        "message": "CyclomaticStrict 12 exceeds the limit of 10",
    }
    fields.update(overrides)
    return Finding.model_validate(fields)


# --- rule-name grammar ---------------------------------------------------------


def test_build_rule_name_matches_threshold_spec_rule() -> None:
    spec = ThresholdSpec(scope="routine", metric="CyclomaticStrict", limit=Limit(max=10))
    assert build_rule_name("routine", "CyclomaticStrict") == spec.rule == "routine.CyclomaticStrict"


def test_build_rule_name_canonicalises_the_stats_prefix() -> None:
    assert build_rule_name("project", "avg:CyclomaticStrict") == "project.AVG:CyclomaticStrict"


def test_build_rule_name_rejects_an_invalid_metric_name() -> None:
    with pytest.raises(ConfigError) as err:
        build_rule_name("routine", "AVG:MEDIAN:Cyclomatic")
    assert err.value.exit_code is ExitCode.CONFIG_ERROR


def test_structure_rule_covers_every_documented_structural_rule() -> None:
    assert set(STRUCTURE_RULES) == {
        "file_cycle",
        "arch_cycle",
        "layer",
        "fan_in",
        "fan_out",
        "new_dependencies",
        "coupling",
    }
    assert structure_rule("file_cycle") == "structure.file_cycle"


def test_codecheck_rule_uses_the_check_id() -> None:
    assert codecheck_rule("CPP_V001") == "codecheck.CPP_V001"


def test_codecheck_rule_rejects_an_empty_id() -> None:
    with pytest.raises(ConfigError):
        codecheck_rule("")


def test_parse_rule_name_reads_a_threshold_rule() -> None:
    assert parse_rule_name("project.AVG:CyclomaticStrict") == ParsedRule(
        category="threshold",
        scope="project",
        metric=MetricRef("AVG", "CyclomaticStrict"),
        name=None,
    )


def test_parse_rule_name_reads_a_structural_rule() -> None:
    assert parse_rule_name("structure.arch_cycle") == ParsedRule(
        category="structure", scope=None, metric=None, name="arch_cycle"
    )


def test_parse_rule_name_reads_a_codecheck_rule() -> None:
    assert parse_rule_name("codecheck.CPP_V001") == ParsedRule(
        category="codecheck", scope=None, metric=None, name="CPP_V001"
    )


@pytest.mark.parametrize(
    "raw", ["routine", "", "module.CyclomaticStrict", "structure.unknown_rule", "codecheck."]
)
def test_parse_rule_name_rejects_names_outside_the_grammar(raw: str) -> None:
    with pytest.raises(ConfigError):
        parse_rule_name(raw)
    assert is_valid_rule_name(raw) is False


def test_is_valid_rule_name_accepts_every_generated_name() -> None:
    generated = [
        build_rule_name("file", "CountLineCode"),
        structure_rule("layer"),
        codecheck_rule("PY_A001"),
    ]
    assert all(is_valid_rule_name(name) for name in generated)


# --- Finding -------------------------------------------------------------------


def test_finding_carries_every_field_required_by_7_1() -> None:
    finding = _finding()
    assert finding.kind == "threshold"
    assert finding.rule == "routine.CyclomaticStrict"
    assert finding.metric == "CyclomaticStrict"
    assert finding.scope == "routine"
    assert finding.entity is not None and finding.entity.key == KEY
    assert finding.path == "src/cli/app.py"
    assert finding.line == 34
    assert finding.value == 12.0
    assert finding.limit == 10.0
    assert finding.severity == "error"
    assert finding.message


def test_finding_hint_defaults_to_empty_for_the_pipeline_to_fill() -> None:
    assert _finding().hint == ""


def test_finding_before_defaults_to_none_for_the_ratchet_step_to_fill() -> None:
    assert _finding().before is None


def test_finding_defaults_to_a_configured_non_blocking_finding() -> None:
    finding = _finding()
    assert finding.limit_source == "config"
    assert finding.blocking is False
    assert finding.preexisting is False
    assert finding.details == {}


def test_finding_accepts_structural_details() -> None:
    finding = _finding(
        kind="structural",
        rule="structure.file_cycle",
        metric=None,
        scope="file",
        entity=None,
        details={
            "members": ["src/analysis/engine.py", "src/analysis/rules.py"],
            "closing_refs": [{"src": "src/analysis/rules.py", "dst": "src/analysis/engine.py"}],
        },
    )
    assert finding.details["members"] == ["src/analysis/engine.py", "src/analysis/rules.py"]


def test_finding_rejects_a_rule_name_outside_the_grammar() -> None:
    with pytest.raises(ValidationError):
        _finding(rule="cyclomatic-too-high")


def test_a_warning_may_not_be_blocking() -> None:
    with pytest.raises(ValidationError):
        _finding(severity="warning", blocking=True)


def test_a_preexisting_error_may_still_block_under_strict_mode() -> None:
    finding = _finding(severity="error", preexisting=True, blocking=True)
    assert finding.blocking is True


def test_finding_round_trips_through_json() -> None:
    finding = _finding(before=6.0, preexisting=True, hint="extract the inner block")
    assert Finding.model_validate(json.loads(finding.model_dump_json())) == finding


# --- effective thresholds, tightening, highest values --------------------------


def test_effective_threshold_reports_its_rule_and_source() -> None:
    spec = ThresholdSpec(scope="routine", metric="CyclomaticStrict", limit=Limit(max=10))
    effective = EffectiveThreshold(
        spec=spec, metric=spec.ref, limit=Limit(max=8), source="baseline"
    )
    assert effective.rule == "routine.CyclomaticStrict"
    assert effective.limit.max == 8
    assert effective.source == "baseline"


def test_effective_threshold_defaults_to_the_configured_source() -> None:
    spec = ThresholdSpec(scope="file", metric="CountLineCode", limit=Limit(max=500))
    assert EffectiveThreshold(spec=spec, metric=spec.ref, limit=spec.limit).source == "config"


def test_tightened_limit_records_both_values() -> None:
    tightened = TightenedLimit(rule="routine.CyclomaticStrict", previous=10.0, current=8.0)
    assert (tightened.previous, tightened.current) == (10.0, 8.0)


def test_highest_value_names_the_entity_that_holds_it() -> None:
    highest = HighestValue(scope="routine", metric="CyclomaticStrict", value=12.0, entity=REF)
    assert highest.entity is not None and highest.entity.name == "build_parser"


# --- RunResult -----------------------------------------------------------------


def _run_result(**overrides: object) -> RunResult:
    fields: dict[str, object] = {
        "tool_version": "0.1.0",
        "understand_version": "6.5.1204",
        "repo_root": "/home/dev/project",
        "selection": "staged",
        "started_at": "2026-08-28T10:00:00+00:00",
        "seconds": 4.5,
    }
    fields.update(overrides)
    return RunResult.model_validate(fields)


def test_run_result_pins_schema_version_one() -> None:
    assert _run_result().schema_version == 1
    with pytest.raises(ValidationError):
        _run_result(schema_version=2)


def test_run_result_defaults_are_empty() -> None:
    result = _run_result()
    assert result.findings == []
    assert result.effective_thresholds == []
    assert result.ignored_counts == {}
    assert result.unavailable_metrics == {}
    assert result.parse_errors == []
    assert result.tightened == []
    assert result.highest == []
    assert (result.analyzed_files, result.blocking_count) == (0, 0)
    assert (result.warning_count, result.preexisting_count) == (0, 0)


def test_run_result_blocking_count_must_match_its_findings() -> None:
    blocking = _finding(blocking=True)
    assert _run_result(findings=[blocking], blocking_count=1).blocking_count == 1
    with pytest.raises(ValidationError):
        _run_result(findings=[blocking], blocking_count=0)


def test_run_result_round_trips_through_json() -> None:
    result = _run_result(
        findings=[_finding()],
        effective_thresholds=[
            ThresholdSpec(scope="routine", metric="CyclomaticStrict", limit=Limit(max=10))
        ],
        ignored_counts={"routine": 2},
        unavailable_metrics={"Python": ["PercentLackOfCohesion"]},
        tightened=[TightenedLimit(rule="routine.CyclomaticStrict", previous=10.0, current=8.0)],
        highest=[HighestValue(scope="routine", metric="CyclomaticStrict", value=12.0, entity=REF)],
        analyzed_files=5,
        warning_count=1,
    )
    assert RunResult.model_validate(json.loads(result.model_dump_json())) == result
