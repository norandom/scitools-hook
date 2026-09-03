"""Metric-name grammar, reducer registry, synthetic metrics and scope kinds (req 3.4, 3.5)."""

from __future__ import annotations

import dataclasses
import statistics
from collections.abc import Callable, Sequence

import pytest

from scitools_hook.config.metric_names import (
    ELEMENT_SCOPES,
    SCOPE_KINDS,
    SCOPES,
    STATS_REDUCERS,
    SYNTHETIC_METRICS,
    MetricRef,
    SyntheticMetric,
    format_metric_name,
    is_valid_scope,
    parse_metric_name,
)
from scitools_hook.errors import ConfigError
from scitools_hook.exit_codes import ExitCode

# Prefix -> the exact ``statistics`` function it must resolve to (population variants).
EXPECTED_REDUCERS: list[tuple[str, Callable[[Sequence[float]], float]]] = [
    ("AVG", statistics.mean),
    ("MEDIAN", statistics.median),
    ("MEDIANHIGH", statistics.median_high),
    ("MEDIANLOW", statistics.median_low),
    ("MEDIANGROUPED", statistics.median_grouped),
    ("MODE", statistics.mode),
    ("STDEV", statistics.pstdev),
    ("VARIANCE", statistics.pvariance),
]

SAMPLE: list[float] = [1, 2, 2, 3, 10]

# Hand-computed for SAMPLE: mean 3.6, population variance 53.2 / 5 = 10.64.
EXPECTED_VALUES: list[tuple[str, float]] = [
    ("AVG", 3.6),
    ("MEDIAN", 2.0),
    ("MEDIANHIGH", 2.0),
    ("MEDIANLOW", 2.0),
    ("MEDIANGROUPED", 2.25),
    ("MODE", 2.0),
    ("STDEV", 10.64**0.5),
    ("VARIANCE", 10.64),
]


# --- reducer registry ----------------------------------------------------------


def test_reducer_registry_has_exactly_the_documented_prefixes() -> None:
    assert list(STATS_REDUCERS) == [prefix for prefix, _ in EXPECTED_REDUCERS]


# `ids=lambda x: str(x)` put the reducer's repr in the test id, which for a function is
# `<function mean at 0x704f2e9616f0>` -- a memory address. That made the id differ between any
# two processes, so `pytest -n` refused to run at all ("Different tests were collected between
# gw0 and gw1"), and it also meant no id could be reused to re-run a single case or matched
# against a previous CI log. The prefix alone names the case and is stable.
@pytest.mark.parametrize(
    ("prefix", "func"), EXPECTED_REDUCERS, ids=[p for p, _ in EXPECTED_REDUCERS]
)
def test_reducer_is_the_intended_statistics_function(
    prefix: str, func: Callable[[Sequence[float]], float]
) -> None:
    assert STATS_REDUCERS[prefix] is func


@pytest.mark.parametrize(("prefix", "expected"), EXPECTED_VALUES, ids=lambda x: str(x))
def test_reducer_produces_the_expected_value(prefix: str, expected: float) -> None:
    assert STATS_REDUCERS[prefix](SAMPLE) == pytest.approx(expected)


def test_stdev_and_variance_are_population_not_sample() -> None:
    # Sample variance of SAMPLE would be 53.2 / 4 = 13.3; srccheck used population statistics.
    assert STATS_REDUCERS["VARIANCE"](SAMPLE) == pytest.approx(10.64)
    assert STATS_REDUCERS["VARIANCE"](SAMPLE) != pytest.approx(statistics.variance(SAMPLE))
    assert STATS_REDUCERS["STDEV"](SAMPLE) == pytest.approx(statistics.pstdev(SAMPLE))


def test_reducer_keys_are_canonical_upper_case() -> None:
    assert all(key == key.upper() for key in STATS_REDUCERS)


# --- metric-name grammar ---------------------------------------------------------


def test_plain_metric_name_has_no_prefix() -> None:
    ref = parse_metric_name("CyclomaticStrict")
    assert ref == MetricRef(None, "CyclomaticStrict")
    assert ref.prefix is None
    assert ref.metric == "CyclomaticStrict"
    assert ref.is_population is False


def test_prefixed_metric_name_is_split_at_the_colon() -> None:
    ref = parse_metric_name("AVG:CyclomaticStrict")
    assert ref == MetricRef("AVG", "CyclomaticStrict")
    assert ref.is_population is True


@pytest.mark.parametrize("raw", ["avg:CountLineCode", "Avg:CountLineCode", "aVg:CountLineCode"])
def test_prefix_is_matched_case_insensitively_and_canonicalised(raw: str) -> None:
    assert parse_metric_name(raw) == MetricRef("AVG", "CountLineCode")


@pytest.mark.parametrize("prefix", list(STATS_REDUCERS))
def test_every_registered_prefix_parses(prefix: str) -> None:
    ref = parse_metric_name(f"{prefix}:CountParams")
    assert ref.prefix == prefix
    assert ref.prefix in STATS_REDUCERS


def test_metric_part_keeps_its_case() -> None:
    assert parse_metric_name("median:countLineCode").metric == "countLineCode"


@pytest.mark.parametrize(
    "raw",
    ["CyclomaticStrict", "AVG:CyclomaticStrict", "STDEV:CountParams", "MEDIANGROUPED:MaxNesting"],
)
def test_format_is_the_inverse_of_parse_for_canonical_names(raw: str) -> None:
    assert format_metric_name(parse_metric_name(raw)) == raw


def test_format_canonicalises_a_lower_case_prefix() -> None:
    assert format_metric_name(parse_metric_name("mode:MaxNesting")) == "MODE:MaxNesting"


def test_format_of_a_plain_ref_is_the_metric_alone() -> None:
    assert format_metric_name(MetricRef(None, "Essential")) == "Essential"


@pytest.mark.parametrize(
    "raw",
    [
        "A:B:C",
        ":X",
        "AVG:",
        ":",
        "",
        "FOO:CyclomaticStrict",
        "AVERAGE:CyclomaticStrict",
        "AVG: CyclomaticStrict",
        "AVG:Cyclomatic Strict",
        "Cyclomatic-Strict",
    ],
)
def test_invalid_names_raise_config_error_naming_the_key(raw: str) -> None:
    with pytest.raises(ConfigError) as caught:
        parse_metric_name(raw)
    err = caught.value
    assert err.key == raw
    assert err.exit_code is ExitCode.CONFIG_ERROR
    assert err.hint is not None
    for prefix in STATS_REDUCERS:
        assert prefix in err.hint
    assert str(err)


def test_unknown_prefix_message_names_the_prefix() -> None:
    with pytest.raises(ConfigError, match="FOO"):
        parse_metric_name("FOO:CyclomaticStrict")


def test_too_many_colons_message_mentions_the_colon_rule() -> None:
    with pytest.raises(ConfigError, match="':'"):
        parse_metric_name("A:B:C")


# --- synthetic metrics ----------------------------------------------------------


def test_synthetic_registry_contains_exactly_the_documented_metrics() -> None:
    assert set(SYNTHETIC_METRICS) == {"CountParams", "CountDeclMethodNonStub"}


@pytest.mark.parametrize(("name", "entry"), list(SYNTHETIC_METRICS.items()), ids=lambda x: str(x))
def test_synthetic_entry_is_consistent(name: str, entry: SyntheticMetric) -> None:
    assert entry.id == name
    assert is_valid_scope(entry.scope)
    assert entry.scope in ELEMENT_SCOPES
    assert entry.description.strip()
    assert isinstance(entry.requires, tuple)
    assert name not in entry.requires
    assert not set(entry.requires) & set(SYNTHETIC_METRICS)


def test_count_params_is_bound_to_routines_and_needs_no_native_metric() -> None:
    entry = SYNTHETIC_METRICS["CountParams"]
    assert entry.scope == "routine"
    assert entry.requires == ()


def test_count_decl_method_non_stub_is_bound_to_classes_and_names_its_inputs() -> None:
    entry = SYNTHETIC_METRICS["CountDeclMethodNonStub"]
    assert entry.scope == "class"
    assert entry.requires == ("CountDeclMethod", "CountDeclPropertyAuto")


def test_synthetic_metric_is_immutable() -> None:
    entry = SYNTHETIC_METRICS["CountParams"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.scope = "class"  # type: ignore[misc]


def test_synthetic_names_parse_as_plain_metrics() -> None:
    for name in SYNTHETIC_METRICS:
        assert parse_metric_name(name) == MetricRef(None, name)


# --- scopes ----------------------------------------------------------------------


def test_scopes_are_the_five_documented_ones_in_order() -> None:
    assert SCOPES == ("routine", "class", "file", "project", "arch")


def test_element_scopes_are_the_entity_bearing_subset() -> None:
    assert ELEMENT_SCOPES == ("routine", "class", "file")
    assert set(ELEMENT_SCOPES) < set(SCOPES)


@pytest.mark.parametrize("value", list(SCOPES))
def test_is_valid_scope_accepts_every_scope(value: str) -> None:
    assert is_valid_scope(value)


@pytest.mark.parametrize("value", ["Routine", "", "method", "function", "ROUTINE", "architecture"])
def test_is_valid_scope_rejects_anything_else(value: str) -> None:
    assert not is_valid_scope(value)


# --- Understand kind strings -----------------------------------------------------


def test_scope_kinds_cover_exactly_the_element_scopes() -> None:
    assert set(SCOPE_KINDS) == set(ELEMENT_SCOPES)
    assert "project" not in SCOPE_KINDS
    assert "arch" not in SCOPE_KINDS


@pytest.mark.parametrize("scope", list(ELEMENT_SCOPES))
def test_every_kind_clause_excludes_unknown_and_unresolved(scope: str) -> None:
    assert is_valid_scope(scope)
    clauses = [clause.strip() for clause in SCOPE_KINDS[scope].split(",")]
    assert clauses
    for clause in clauses:
        assert clause.endswith(" ~unknown ~unresolved"), clause
        assert clause.split()[0].isalpha()


def test_routine_kinds_name_every_callable_kind() -> None:
    heads = [clause.split()[0] for clause in SCOPE_KINDS["routine"].split(",")]
    assert heads == ["function", "method", "procedure", "routine", "classmethod"]


def test_class_kinds_name_class_interface_and_struct() -> None:
    heads = [clause.split()[0] for clause in SCOPE_KINDS["class"].split(",")]
    assert heads == ["class", "interface", "struct"]


def test_file_kind_is_a_single_clause() -> None:
    assert SCOPE_KINDS["file"] == "file ~unknown ~unresolved"
