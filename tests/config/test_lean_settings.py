"""The ``[lean]`` section: nine rule switches, all of them off, and what they cost while off.

Requirement 9.4 is what most of these tests are really about. A repository that names none of
these keys must produce the settings it produced before the section existed, and must ask the
worker for nothing on the family's behalf -- which is what the two derived booleans decide, so
each is pinned per rule rather than in one aggregate assertion.

The rest is the shape requirements 1.5, 2.4, 3.4, 4.3, 5.5 and 7.5 ask for: a rule is a
severity or absent, every ignore list is validated where it is written rather than mid-run,
and each configurable number has a bound that says what the rule cannot mean (a duplicate of
two lines, a similarity of zero).
"""

from __future__ import annotations

import re
import tomllib

import pytest
from fixtures.constants import LEAN_REFERENCE_RULES, LEAN_RULE_SWITCHES, LEAN_TOKEN_RULES
from pydantic import ValidationError

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.metric_names import SYNTHETIC_METRICS
from scitools_hook.config.models import (
    DEFAULT_ACCURACY_FLOOR,
    DEFAULT_LEAN_CLASS_IGNORE,
    DEFAULT_LEAN_IMPLEMENTATION_IGNORE,
    DEFAULT_LEAN_OVER_EXPORT_IGNORE,
    DEFAULT_LEAN_PARAMETER_IGNORE,
    DEFAULT_LEAN_VARIABLE_IGNORE,
    DEFAULT_RESOLUTION_FLOOR,
    DEFAULT_UNUSED_IGNORE,
    REFERENCE_RULES,
    LeanRules,
    Settings,
    matching_pattern,
)

NAME_IGNORE_LISTS = (
    "unused_parameters_ignore",
    "unused_classes_ignore",
    "unused_variables_ignore",
    "pass_through_ignore",
    "single_implementation_ignore",
)
PATH_IGNORE_LISTS = ("over_export_ignore", "duplicates_ignore", "similar_ignore")

EVERY_KEY = """
[lean]
unused_parameters = "warning"
unused_parameters_ignore = ["^_"]
unused_classes = "warning"
unused_classes_ignore = ["Error$"]
unused_variables = "error"
unused_variables_ignore = ["^log$"]
resolution_floor = 0.5
accuracy_floor = 0.6
pass_through = "warning"
pass_through_max_statements = 3
pass_through_ignore = ["(^|\\\\.)main$"]
single_implementation = "warning"
single_implementation_ignore = ["Base$"]
over_export = "warning"
over_export_ignore = ["**/__init__.py"]
duplicates = "warning"
duplicates_min_lines = 10
duplicates_ignore = ["tests/**"]
similar_routines = "warning"
similar_min_statements = 8
similar_threshold = 0.95
similar_ignore = ["tests/**"]
verbosity_min_statements = 2
max_net_growth = 40
net_growth_severity = "error"
"""


# --- the defaults keep today's behaviour ------------------------------------------


@pytest.mark.parametrize("rule", LEAN_RULE_SWITCHES)
def test_every_lean_rule_ships_off(rule: str) -> None:
    """Requirement 9.1: none of the nine fires until an operator chooses a severity."""
    assert getattr(default_settings().lean, rule) is None


def test_the_net_growth_maximum_ships_unset_and_never_blocks_by_default() -> None:
    """Requirement 7.5: the delta is reported; refusing on it is an operator's decision."""
    lean = default_settings().lean

    assert lean.max_net_growth is None
    assert lean.net_growth_severity == "warning"


def test_a_configuration_without_the_lean_section_means_exactly_what_it_did_before() -> None:
    """The observable form of requirement 9.4: an existing file still describes today's run."""
    settings = Settings.model_validate(tomllib.loads('[project]\nlanguages = ["Python"]\n'))
    shipped = default_settings()

    assert settings.lean == shipped.lean == LeanRules()
    assert settings.lean.wants_references is False
    assert settings.lean.wants_tokens is False


def test_the_verbosity_floor_ships_at_the_number_the_metric_declares() -> None:
    """Requirement 6.3 asks for a *configurable* minimum, and the default is the declared one.

    Read off ``SYNTHETIC_METRICS`` rather than written as 5, because the whole point of the
    key is that the declaration owns the number and the operator may move it. A default that
    drifted from the declaration would give ``check`` and the metric's own documentation two
    different floors.
    """
    declared = SYNTHETIC_METRICS["LinesPerStatement"].floor

    assert declared is not None
    assert default_settings().lean.verbosity_min_statements == declared[1]


def test_the_verbosity_floor_asks_for_nothing_extra() -> None:
    """It judges statements the snapshot already carries, so no walk is paid for (req 9.4)."""
    lean = LeanRules.model_validate({"verbosity_min_statements": 2})

    assert lean.wants_references is False
    assert lean.wants_tokens is False


# --- requirement 1.8's two floors ---------------------------------------------------


def test_the_two_trust_floors_ship_at_the_placeholders_the_rules_document() -> None:
    """Requirement 1.8 asks for *configurable* floors, and 0.75 is where they start.

    Read off the constants rather than written as 0.75 twice, because the whole point of the
    keys is that one declaration owns each number and the operator may move it. Neither is
    calibrated -- requirement 1.10's two-repository count is what will calibrate them -- so
    nothing here claims either is the rate at which the rules become right.
    """
    lean = default_settings().lean

    assert lean.resolution_floor == DEFAULT_RESOLUTION_FLOOR
    assert lean.accuracy_floor == DEFAULT_ACCURACY_FLOOR


def test_a_floor_of_zero_and_a_floor_of_one_are_both_legal() -> None:
    """The ends of the range: trust nothing about the analysis, or demand a perfect one."""
    assert LeanRules.model_validate({"resolution_floor": 0.0}).resolution_floor == 0.0
    assert LeanRules.model_validate({"accuracy_floor": 1.0}).accuracy_floor == 1.0


def test_the_floors_cost_nothing_and_turn_nothing_on() -> None:
    """Requirement 9.4: they bound rules that are off, so on their own they buy no walk."""
    lean = LeanRules.model_validate({"resolution_floor": 0.9, "accuracy_floor": 0.9})

    assert lean.wants_references is False
    assert lean.wants_tokens is False


# --- the two accuracy floors are two decisions, not one written twice ----------------
#
# `analysis.accuracy_floor` and `lean.accuracy_floor` read the same measurement -- the share
# of files `und analyze -accuracy` parsed without an error -- to opposite ends. The first
# RAISES a non-blocking finding saying the run is less trustworthy, and ships unset. The
# second SUPPRESSES the rules that cannot be trusted below it, and ships at 0.75. Both
# docstrings say so; these tests are what fails if anyone ever wires one to the other, which
# is the binding two numbers that must DIFFER need exactly as two that must agree do.


def test_the_shipped_pair_disagrees_on_purpose() -> None:
    """One is unset because it reports; the other is set because it refuses."""
    shipped = default_settings()

    assert shipped.analysis.accuracy_floor is None
    assert shipped.lean.accuracy_floor == DEFAULT_ACCURACY_FLOOR


def test_setting_either_accuracy_floor_leaves_the_other_where_it_was() -> None:
    """A file that names one key must not move the number the other key owns."""
    reporting = Settings.model_validate(tomllib.loads("[analysis]\naccuracy_floor = 0.2\n"))
    refusing = Settings.model_validate(tomllib.loads("[lean]\naccuracy_floor = 0.2\n"))

    assert (reporting.analysis.accuracy_floor, reporting.lean.accuracy_floor) == (
        0.2,
        DEFAULT_ACCURACY_FLOOR,
    )
    assert (refusing.lean.accuracy_floor, refusing.analysis.accuracy_floor) == (0.2, None)


def test_a_configuration_naming_every_new_key_validates() -> None:
    """All twenty-five keys together, in the spellings the documentation will show."""
    settings = Settings.model_validate(tomllib.loads(EVERY_KEY))

    assert settings.lean.unused_variables == "error"
    assert settings.lean.resolution_floor == 0.5
    assert settings.lean.accuracy_floor == 0.6
    assert settings.lean.pass_through_max_statements == 3
    assert settings.lean.duplicates_min_lines == 10
    assert settings.lean.similar_threshold == 0.95
    assert settings.lean.similar_ignore == ["tests/**"]
    assert settings.lean.verbosity_min_statements == 2
    assert settings.lean.max_net_growth == 40
    assert settings.lean.net_growth_severity == "error"


def test_the_shipped_ignore_lists_cover_the_shapes_a_reference_cannot_see() -> None:
    """Requirement 1.5: the receiver, the deliberate spare, the class raised but never read.

    Pinned as literals rather than as "not empty" because each entry is the answer to a
    finding the rule would otherwise produce on every language Understand parses.
    """
    lean = default_settings().lean

    assert lean.unused_parameters_ignore == list(DEFAULT_LEAN_PARAMETER_IGNORE)
    assert lean.unused_classes_ignore == list(DEFAULT_LEAN_CLASS_IGNORE)
    assert lean.unused_variables_ignore == list(DEFAULT_LEAN_VARIABLE_IGNORE)
    assert lean.pass_through_ignore == list(DEFAULT_UNUSED_IGNORE)
    assert lean.single_implementation_ignore == list(DEFAULT_LEAN_IMPLEMENTATION_IGNORE)
    assert lean.over_export_ignore == list(DEFAULT_LEAN_OVER_EXPORT_IGNORE)


@pytest.mark.parametrize(
    ("patterns", "name"),
    [
        (DEFAULT_LEAN_PARAMETER_IGNORE, "self"),
        (DEFAULT_LEAN_PARAMETER_IGNORE, "_unused"),
        (DEFAULT_LEAN_CLASS_IGNORE, "pkg.ConfigError"),
        (DEFAULT_LEAN_CLASS_IGNORE, "pkg.ParseException"),
        (DEFAULT_LEAN_VARIABLE_IGNORE, "__all__"),
        (DEFAULT_LEAN_VARIABLE_IGNORE, "logger"),
        (DEFAULT_LEAN_IMPLEMENTATION_IGNORE, "pkg.ConfigError"),
    ],
)
def test_a_shipped_name_pattern_matches_the_shape_it_is_there_for(
    patterns: tuple[str, ...], name: str
) -> None:
    """The lists are regexes applied the way ``structure.unused_ignore`` is (req 1.5)."""
    assert any(re.search(pattern, name) for pattern in patterns), name


def test_the_shipped_over_export_patterns_cover_the_initialiser_of_every_language() -> None:
    """Requirement 4.2: a package initialiser holds one definition for one importer by design."""
    shipped = list(DEFAULT_LEAN_OVER_EXPORT_IGNORE)

    assert matching_pattern(shipped, "src/pkg/__init__.py") is not None
    assert matching_pattern(shipped, "src/pkg/__main__.py") is not None
    assert matching_pattern(shipped, "web/routes/index.ts") is not None
    assert matching_pattern(shipped, "crate/src/mod.rs") is not None
    assert matching_pattern(shipped, "src/pkg/models.py") is None


# --- what the section refuses ------------------------------------------------------


@pytest.mark.parametrize("field", NAME_IGNORE_LISTS)
def test_a_name_ignore_pattern_that_is_not_a_regex_is_refused(field: str) -> None:
    """The same validation ``[ignore]`` and ``structure.unused_ignore`` get (req 3.8)."""
    with pytest.raises(ValidationError, match="invalid regular expression"):
        LeanRules.model_validate({field: ["(unclosed"]})


@pytest.mark.parametrize("field", PATH_IGNORE_LISTS)
def test_a_blank_path_ignore_pattern_is_refused(field: str) -> None:
    """A blank entry looks like an exclusion and excludes nothing; that is a typo, not a rule."""
    with pytest.raises(ValidationError, match="ignores nothing"):
        LeanRules.model_validate({field: ["  "]})


@pytest.mark.parametrize("field", PATH_IGNORE_LISTS)
def test_a_path_ignore_list_takes_globs_not_regexes(field: str) -> None:
    """``[project] include``'s language, so ``(unclosed`` is a literal rather than an error."""
    rules = LeanRules.model_validate({field: ["vendor/**", "(literal)/*.c"]})

    assert getattr(rules, field) == ["vendor/**", "(literal)/*.c"]


@pytest.mark.parametrize(
    "payload",
    [
        {"pass_through_max_statements": 0},
        {"duplicates_min_lines": 2},
        {"similar_min_statements": 1},
        {"similar_threshold": 0.0},
        {"similar_threshold": 1.5},
        {"max_net_growth": -1},
        {"verbosity_min_statements": 0},
        {"resolution_floor": 1.5},
        {"resolution_floor": -0.01},
        {"accuracy_floor": 1.5},
        {"accuracy_floor": -0.01},
    ],
    ids=[
        "budget",
        "min_lines",
        "min_statements",
        "threshold_zero",
        "threshold_high",
        "growth",
        "verbosity_floor",
        "resolution_above",
        "resolution_below",
        "accuracy_above",
        "accuracy_below",
    ],
)
def test_a_number_outside_the_range_the_rule_can_mean_is_refused(
    payload: dict[str, object],
) -> None:
    """A window of two lines or a similarity of nothing is a rule that reports everything."""
    with pytest.raises(ValidationError):
        LeanRules.model_validate(payload)


def test_a_similarity_of_one_is_the_top_of_the_range() -> None:
    """Identical token sequences: the strictest the rule goes, and a legal setting."""
    assert LeanRules.model_validate({"similar_threshold": 1.0}).similar_threshold == 1.0


def test_an_unknown_lean_key_is_refused_where_it_is_written() -> None:
    """Requirement 3.8: a misspelt switch must fail, not sit in the file doing nothing."""
    with pytest.raises(ValidationError, match="unused_parameter"):
        Settings.model_validate({"lean": {"unused_parameter": "warning"}})


@pytest.mark.parametrize("rule", LEAN_RULE_SWITCHES)
def test_a_lean_rule_takes_only_a_known_severity(rule: str) -> None:
    """``Severity | None``, exactly as ``structure.unused_routines`` (req 1.5, 2.4, 3.4, 4.3)."""
    with pytest.raises(ValidationError):
        LeanRules.model_validate({rule: "fatal"})


# --- the two derived answers the extractor reads ------------------------------------


def test_the_suite_and_the_settings_name_the_same_reference_rules() -> None:
    """One list, two independent statements of it, and this is what keeps them equal.

    ``config.models.REFERENCE_RULES`` is what ``wants_references`` and
    ``understand.features.ASKED_BY`` both read; ``fixtures.constants.LEAN_REFERENCE_RULES`` is
    what the suite parametrises over, spelled out rather than imported so that a rule dropped
    from the production constant cannot silently drop the cases that would have caught it.
    Two artefacts that must agree need something binding them, and this is it.
    """
    assert LEAN_REFERENCE_RULES == REFERENCE_RULES


@pytest.mark.parametrize("rule", LEAN_REFERENCE_RULES)
def test_each_reference_rule_alone_asks_for_the_reference_walk(rule: str) -> None:
    """Requirement 9.4 the other way round: one rule on is enough to pay for the walk."""
    lean = LeanRules.model_validate({rule: "warning"})

    assert lean.wants_references is True
    assert lean.wants_tokens is False


def test_over_export_alone_asks_for_neither_walk() -> None:
    """The one rule in the family that needs no measurement of its own (req 9.4, 9.5).

    Over-export is decided from file metrics, ``file_edges`` and the definitions walk the
    snapshot already carries. If it flipped ``wants_references``, a configuration that
    enabled only this rule would pay for a reference call on every recorded entity and read
    none of the answers -- the cost requirement 9.4 forbids while the other rules are off.
    What it does turn on is the fingerprint's ``definitions`` key, which
    ``tests/config/test_fingerprint.py`` pins.
    """
    lean = LeanRules.model_validate({"over_export": "warning"})

    assert lean.wants_references is False
    assert lean.wants_tokens is False


@pytest.mark.parametrize("rule", LEAN_TOKEN_RULES)
def test_each_token_rule_alone_asks_for_the_token_index(rule: str) -> None:
    """The token pass is the expensive one, so it is asked for by exactly two rules."""
    lean = LeanRules.model_validate({rule: "warning"})

    assert lean.wants_tokens is True
    assert lean.wants_references is False


def test_a_net_growth_maximum_asks_for_nothing_extra() -> None:
    """The delta is summed from routine metrics the snapshot already carries (req 7.1)."""
    lean = LeanRules.model_validate({"max_net_growth": 0, "net_growth_severity": "error"})

    assert lean.wants_references is False
    assert lean.wants_tokens is False


def test_an_ignore_list_alone_asks_for_nothing() -> None:
    """A list without a severity is a rule that is off; configuring it must cost nothing."""
    lean = LeanRules.model_validate(
        {"unused_classes_ignore": ["^Legacy"], "duplicates_ignore": ["vendor/**"]}
    )

    assert lean.wants_references is False
    assert lean.wants_tokens is False
