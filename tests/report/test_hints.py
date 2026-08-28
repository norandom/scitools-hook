"""The remediation-hint catalogue: rule -> metric -> generic lookup with operator overrides.

Requirement 7.2 asks for a hint that is specific to the metric or rule behind a finding and
that an operator can extend in configuration. Three levels answer both halves of that:

* the **rule** key (``routine.CountLineCode``, ``structure.file_cycle``,
  ``codecheck.CPP_F016``) carries wording that only makes sense for that scope,
* the **metric** key (``CountLineCode``) carries wording that fits every scope the metric
  appears in, and
* a **generic** key per finding kind (``generic.threshold``) catches metrics and checks the
  catalogue has never heard of, which is the normal case for CodeCheck.

The tests below pin the *order* of those levels, not only that a lookup finds something: a
catalogue that searched metric-first would still answer every query, just with the wrong
text, so each precedence test uses a rule whose two levels carry different hints.

They also pin the two properties an operator depends on: an override replaces the default
for the same key at any level, and a default hint exists for every threshold shipped in
``config/defaults.py`` and every entry of ``STRUCTURE_RULES`` -- the hint is the whole point
of the finding for an agent, so a metric the tool ships a limit for must never fall through
to the generic text.
"""

from __future__ import annotations

from typing import Final, get_args

import pytest

from scitools_hook.config.defaults import DEFAULT_THRESHOLDS
from scitools_hook.config.metric_names import Scope
from scitools_hook.models.findings import (
    STRUCTURE_RULES,
    Finding,
    FindingKind,
    StructureRuleName,
    build_rule_name,
    structure_rule,
)
from scitools_hook.report.hints import DEFAULT_CATALOGUE, GENERIC_KEYS, HintCatalogue

ROUTINE_LINES: Final = "routine.CountLineCode"
"""A rule that has both a rule-level and a metric-level default: the precedence case."""


def finding(kind: FindingKind = "threshold", rule: str = ROUTINE_LINES) -> Finding:
    """A finding carrying only the fields the catalogue reads: its kind and its rule."""
    return Finding(kind=kind, rule=rule, scope="routine", message="over the limit")


# --- lookup order ---------------------------------------------------------------


def test_rule_key_wins_over_the_metric_key() -> None:
    catalogue = HintCatalogue({})
    assert catalogue.hint(ROUTINE_LINES, finding()) == DEFAULT_CATALOGUE[ROUTINE_LINES]
    assert DEFAULT_CATALOGUE[ROUTINE_LINES] != DEFAULT_CATALOGUE["CountLineCode"]


def test_metric_key_answers_a_scope_without_its_own_rule_hint() -> None:
    catalogue = HintCatalogue({})
    assert (
        catalogue.hint("class.CountLineCode", finding(rule="class.CountLineCode"))
        == (DEFAULT_CATALOGUE["CountLineCode"])
    )


def test_stats_prefixed_rule_falls_back_to_the_bare_metric() -> None:
    catalogue = HintCatalogue({})
    rule = build_rule_name("project", "MEDIAN:CountStmt")
    assert rule not in DEFAULT_CATALOGUE
    assert catalogue.hint(rule, finding(rule=rule)) == DEFAULT_CATALOGUE["CountStmt"]


def test_generic_hint_answers_a_metric_the_catalogue_never_heard_of() -> None:
    catalogue = HintCatalogue({})
    rule = "routine.CountSemicolon"
    assert catalogue.hint(rule, finding(rule=rule)) == DEFAULT_CATALOGUE[GENERIC_KEYS["threshold"]]


def test_generic_hint_is_chosen_by_the_findings_kind() -> None:
    catalogue = HintCatalogue({})
    rule = "routine.CountSemicolon"
    ratchet = catalogue.hint(rule, finding(kind="ratchet", rule=rule))
    assert ratchet == DEFAULT_CATALOGUE[GENERIC_KEYS["ratchet"]]
    assert ratchet != DEFAULT_CATALOGUE[GENERIC_KEYS["threshold"]]


def test_unknown_codecheck_check_gets_the_generic_codecheck_hint() -> None:
    catalogue = HintCatalogue({})
    rule = "codecheck.CPP_F016"
    got = catalogue.hint(rule, finding(kind="codecheck", rule=rule))
    assert got == DEFAULT_CATALOGUE[GENERIC_KEYS["codecheck"]]


def test_the_rule_argument_decides_the_lookup_not_the_findings_rule() -> None:
    """``hint(rule, finding)`` takes the rule separately; the argument is what is looked up."""
    catalogue = HintCatalogue({})
    cycle = structure_rule("file_cycle")
    assert catalogue.hint(cycle, finding(kind="structural")) == DEFAULT_CATALOGUE[cycle]


def test_an_ungrammatical_rule_name_falls_back_instead_of_raising() -> None:
    catalogue = HintCatalogue({})
    assert (
        catalogue.hint("nonsense", finding(rule=ROUTINE_LINES))
        == (DEFAULT_CATALOGUE[GENERIC_KEYS["threshold"]])
    )


# --- overrides ------------------------------------------------------------------


def test_override_replaces_a_default_rule_hint() -> None:
    catalogue = HintCatalogue({ROUTINE_LINES: "our house rule: 40 lines"})
    assert catalogue.hint(ROUTINE_LINES, finding()) == "our house rule: 40 lines"


def test_override_at_the_metric_level_reaches_every_scope_without_a_rule_hint() -> None:
    catalogue = HintCatalogue({"CountStmt": "keep statements down"})
    for rule in ("routine.CountStmt", "class.CountStmt", "file.CountStmt"):
        assert catalogue.hint(rule, finding(rule=rule)) == "keep statements down"


def test_override_of_a_generic_hint_is_used_for_unknown_metrics() -> None:
    catalogue = HintCatalogue({GENERIC_KEYS["threshold"]: "ask the tech lead"})
    rule = "routine.CountSemicolon"
    assert catalogue.hint(rule, finding(rule=rule)) == "ask the tech lead"


def test_a_generic_hint_emptied_by_an_override_still_yields_advice() -> None:
    """An operator can silence a level, but a finding must never print an empty ``hint:``.

    Every level of the lookup can be overridden, including with an empty string; the last
    resort keeps requirement 7.2 true when someone does that.
    """
    catalogue = HintCatalogue({GENERIC_KEYS["threshold"]: ""})
    got = catalogue.hint("routine.CountSemicolon", finding(rule="routine.CountSemicolon"))
    assert got.strip()
    assert got != DEFAULT_CATALOGUE[GENERIC_KEYS["threshold"]]


def test_override_of_one_codecheck_check_leaves_the_others_generic() -> None:
    catalogue = HintCatalogue({"codecheck.CPP_F016": "add the return type"})
    assert catalogue.hint("codecheck.CPP_F016", finding(kind="codecheck")) == "add the return type"
    other = catalogue.hint("codecheck.CPP_F017", finding(kind="codecheck"))
    assert other == DEFAULT_CATALOGUE[GENERIC_KEYS["codecheck"]]


def test_a_bare_check_id_override_matches_the_codecheck_rule() -> None:
    catalogue = HintCatalogue({"CPP_F016": "add the return type"})
    assert catalogue.hint("codecheck.CPP_F016", finding(kind="codecheck")) == "add the return type"


def test_a_metric_override_does_not_beat_a_shipped_rule_hint() -> None:
    """Decision on record: the level order stands above the source of the text (7.2).

    An operator who wants to change ``routine.CountLineCode`` overrides that key; the finding
    prints the rule name, so the key to override is always visible in the output.
    """
    catalogue = HintCatalogue({"CountLineCode": "our own wording"})
    assert catalogue.hint(ROUTINE_LINES, finding()) == DEFAULT_CATALOGUE[ROUTINE_LINES]


def test_overrides_are_copied_so_later_edits_do_not_leak_in() -> None:
    overrides = {ROUTINE_LINES: "first"}
    catalogue = HintCatalogue(overrides)
    overrides[ROUTINE_LINES] = "second"
    assert catalogue.hint(ROUTINE_LINES, finding()) == "first"


def test_overrides_do_not_reach_another_catalogue() -> None:
    HintCatalogue({ROUTINE_LINES: "mine only"})
    assert HintCatalogue({}).hint(ROUTINE_LINES, finding()) == DEFAULT_CATALOGUE[ROUTINE_LINES]


# --- coverage of what the tool ships --------------------------------------------


@pytest.mark.parametrize(
    ("scope", "metric"),
    [(scope, metric) for scope, table in DEFAULT_THRESHOLDS.items() for metric in table],
)
def test_every_default_threshold_has_a_specific_hint(scope: Scope, metric: str) -> None:
    catalogue = HintCatalogue({})
    rule = build_rule_name(scope, metric)
    got = catalogue.hint(rule, finding(rule=rule))
    assert got
    assert got != DEFAULT_CATALOGUE[GENERIC_KEYS["threshold"]]


@pytest.mark.parametrize("name", STRUCTURE_RULES)
def test_every_structural_rule_has_a_specific_hint(name: StructureRuleName) -> None:
    catalogue = HintCatalogue({})
    rule = structure_rule(name)
    got = catalogue.hint(rule, finding(kind="structural", rule=rule))
    assert got
    assert got != DEFAULT_CATALOGUE[GENERIC_KEYS["structural"]]


@pytest.mark.parametrize("kind", get_args(FindingKind))
def test_every_finding_kind_has_a_generic_hint(kind: FindingKind) -> None:
    assert DEFAULT_CATALOGUE[GENERIC_KEYS[kind]].strip()


def test_no_default_hint_is_blank_or_a_placeholder() -> None:
    for key, text in DEFAULT_CATALOGUE.items():
        assert text.strip(), key
        assert text == text.strip(), key
