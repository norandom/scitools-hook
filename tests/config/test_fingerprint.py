"""The analysis fingerprint: which settings changes make a cached analysis worthless.

Two caches in this specification are keyed on it -- the commit-built before database
(requirement 3.5) and the before snapshot (requirement 8.6) -- and they must agree, or one
would serve a stale answer the other had already discarded. So there is one function and
these tests, rather than a hash written twice.

The line it draws is the point. A setting that changes **what Understand analyses or how**
invalidates: the languages, the file selection, the architecture the structural rules read,
the acknowledged parse errors. A setting that changes only **how the result is judged** does
not: a threshold, a severity, a ratchet lever. Getting that backwards in the cheap direction
costs a rebuild nobody needed; getting it backwards in the expensive direction serves a
document that describes a different project, which is the failure this whole feature must
not introduce (requirement 8.7).
"""

from __future__ import annotations

import pytest
from fixtures.constants import LEAN_REFERENCE_RULES, LEAN_TOKEN_RULES

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.fingerprint import analysis_fingerprint
from scitools_hook.config.models import LeanRules, ParseAcknowledgement, Settings


def changed(**overrides: object) -> Settings:
    """The shipped settings with one branch replaced, as a configuration file would."""
    return default_settings().model_copy(update=overrides, deep=True)


def test_the_same_settings_always_hash_the_same() -> None:
    """A cache key that moved between two runs of one configuration would never hit."""
    assert analysis_fingerprint(default_settings()) == analysis_fingerprint(default_settings())


def test_the_fingerprint_is_a_short_stable_hex_string() -> None:
    """It goes in a file name and in `doctor`'s output, so it has to be both."""
    digest = analysis_fingerprint(default_settings())

    assert len(digest) == 16
    assert all(character in "0123456789abcdef" for character in digest)


def test_a_threshold_does_not_change_the_fingerprint() -> None:
    """The explicit criterion of task 1.3: judgement is not analysis.

    Two settings that differ only in a limit describe the same databases and the same
    snapshots; re-analysing on a threshold edit would make every experiment with a limit cost
    a full run, which is the opposite of what the ratchet is for.
    """
    loosened = default_settings()
    for spec in loosened.thresholds:
        if spec.limit is not None and spec.limit.max is not None:
            spec.limit.max += 1.0
            break
    else:  # pragma: no cover - the shipped defaults always carry a maximum
        pytest.fail("the shipped defaults carry no maximum to change")

    assert analysis_fingerprint(loosened) == analysis_fingerprint(default_settings())


def test_neither_do_the_severities_the_ratchet_or_the_baseline() -> None:
    """Everything downstream of the snapshot is outside the key, by the same argument."""
    base = default_settings()
    tightened = changed(
        ratchet=base.ratchet.model_copy(update={"strict": True}),
        baseline=base.baseline.model_copy(update={"adaptive": True}),
    )

    assert analysis_fingerprint(tightened) == analysis_fingerprint(base)


@pytest.mark.parametrize(
    "overrides",
    [
        {"languages": ["Python"]},
        {"include": ["src/**"]},
        {"exclude": ["vendor/**"]},
    ],
    ids=["languages", "include", "exclude"],
)
def test_what_enters_the_database_changes_the_fingerprint(overrides: dict[str, object]) -> None:
    """The file set and the language set decide what the database holds (requirement 2.4)."""
    base = default_settings()
    moved = changed(project=base.project.model_copy(update=overrides))

    assert analysis_fingerprint(moved) != analysis_fingerprint(base)


@pytest.mark.parametrize(
    "overrides",
    [{"architecture": "Git Stability"}, {"depth": 3}],
    ids=["architecture", "depth"],
)
def test_the_architecture_the_rules_read_changes_the_fingerprint(
    overrides: dict[str, object],
) -> None:
    """Nodes and their depth are recorded in the snapshot itself, so a cached one is stale."""
    base = default_settings()
    moved = changed(structure=base.structure.model_copy(update=overrides))

    assert analysis_fingerprint(moved) != analysis_fingerprint(base)


def test_an_acknowledged_parse_error_changes_the_fingerprint() -> None:
    """An acknowledgement changes which findings a snapshot can produce for a file."""
    base = default_settings()
    acknowledged = changed(
        parse=base.parse.model_copy(
            update={
                "acknowledged": [
                    ParseAcknowledgement(paths=["pkg/generic.py"], reason="measured elsewhere")
                ]
            }
        )
    )

    assert analysis_fingerprint(acknowledged) != analysis_fingerprint(base)


def test_the_ignore_rules_change_the_fingerprint() -> None:
    """The worker applies them while it records, so they are baked into the document."""
    base = default_settings()
    ignoring = changed(ignore=base.ignore.model_copy(update={"routines": ["^tests\\."]}))

    assert analysis_fingerprint(ignoring) != analysis_fingerprint(base)


# --- the lean-code family ----------------------------------------------------------


@pytest.mark.parametrize("rule", LEAN_REFERENCE_RULES)
def test_switching_on_a_reference_rule_changes_the_fingerprint(rule: str) -> None:
    """A cached snapshot recorded while the rule was off has no lean facts in it.

    Requirement 1.6 says a rule that cannot be measured is reported unavailable, and that is
    the right answer for a build that cannot answer it. It would be the wrong answer for a
    cache: the run *can* measure the rule, it is holding a document from before the operator
    asked. So switching a rule on has to be a cache miss, not an "unavailable" line.
    """
    base = default_settings()
    switched = changed(lean=base.lean.model_copy(update={rule: "warning"}))

    assert analysis_fingerprint(switched) != analysis_fingerprint(base)


@pytest.mark.parametrize("rule", LEAN_TOKEN_RULES)
def test_switching_on_a_token_rule_changes_the_fingerprint(rule: str) -> None:
    """The token index is a second pass over every file; a document without it cannot serve."""
    base = default_settings()
    switched = changed(lean=base.lean.model_copy(update={rule: "warning"}))

    assert analysis_fingerprint(switched) != analysis_fingerprint(base)


def test_the_pass_through_rule_asks_for_a_metric_the_fingerprint_sees() -> None:
    """Follow-up 11 keyed the cache on the pass-through rule's own request for ``CountStmt``:
    a snapshot cached with the reference walk on and the rule **off** carried no statement
    count on any record, and served to a run that switched the rule on, the rule judged
    nothing without a word. Since follow-up 14 the extractor requests the count on every
    routine record of every run, for the net delta, so the two configurations below describe
    the same document and the key only invalidates in the safe direction; it is kept, and
    this test holds it in the payload. Both configurations share ``wants_references`` and a
    threshold list with no ``CountStmt`` in it, so only the rule's own key can tell them apart.
    """
    walking = changed(thresholds=[], lean=LeanRules(unused_parameters="warning", pass_through=None))
    asking = changed(
        thresholds=[], lean=LeanRules(unused_parameters="warning", pass_through="warning")
    )

    assert walking.lean.wants_references and asking.lean.wants_references
    assert analysis_fingerprint(asking) != analysis_fingerprint(walking)


def test_over_export_alone_turns_on_the_definitions_walk_and_not_the_reference_walk() -> None:
    """Both halves of the one rule in the family that asks for no reference call.

    ``over_export`` is answered from file metrics, ``file_edges`` and the definitions walk,
    so the settings that turn it on and the settings that turn ``duplicate_definitions`` on
    describe **the same document**: one definitions walk, the same metrics, no per-entity
    references. Equal fingerprints therefore say two things at once -- that ``over_export``
    flipped ``definitions`` (or it would not match a configuration that did) and that it left
    ``lean_references`` false (or it would not match one that has no lean rule at all). The
    inequality against the shipped settings is what proves the first half is not vacuous.

    Written as its own test because a draft of the design said six reference rules here and
    five in the extractor's ``ASKED_BY`` table, and the cost of getting it wrong is a
    reference call on every recorded entity whose answer nothing reads (req 9.4, 9.5).
    """
    base = default_settings()
    by_lean = changed(lean=base.lean.model_copy(update={"over_export": "warning"}))
    by_structure = changed(structure=base.structure.model_copy(update={"duplicate_definitions": 2}))

    assert analysis_fingerprint(by_lean) == analysis_fingerprint(by_structure)
    assert analysis_fingerprint(by_lean) != analysis_fingerprint(base)


def test_the_net_growth_maximum_does_not_change_the_fingerprint() -> None:
    """It judges statements the snapshot already carries, so it is a limit like any other."""
    base = default_settings()
    bounded = changed(
        lean=base.lean.model_copy(update={"max_net_growth": 40, "net_growth_severity": "error"})
    )

    assert analysis_fingerprint(bounded) == analysis_fingerprint(base)


def test_the_verbosity_floor_does_not_change_the_fingerprint() -> None:
    """It moves which routines are *judged*, not which numbers are extracted (req 6.3).

    The same distinction ``max_net_growth`` is pinned for above: the fingerprint covers the
    document a run produces, and ``CountStmt`` and ``LinesPerStatement`` are recorded for
    every routine whatever the floor is set to. A key that entered it would throw away a warm
    analysis cache for a limit, which is the cost requirement 9.4 exists to avoid.
    """
    base = default_settings()
    floored = changed(lean=base.lean.model_copy(update={"verbosity_min_statements": 2}))

    assert analysis_fingerprint(floored) == analysis_fingerprint(base)


def test_a_lean_ignore_list_alone_does_not_change_the_fingerprint() -> None:
    """A list belonging to a rule that is off changes neither the extraction nor the judgement."""
    base = default_settings()
    ignoring = changed(lean=base.lean.model_copy(update={"duplicates_ignore": ["vendor/**"]}))

    assert analysis_fingerprint(ignoring) == analysis_fingerprint(base)
