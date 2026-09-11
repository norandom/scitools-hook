"""A configuration that needs what the build does not offer stops before anything runs (1.2).

The alternative is worse than a refusal. A key the build cannot honour would otherwise be
read, ignored, and the run would go green having measured something else -- which is the
silent-green shape the whole tool exists to refuse. So the check reads what ``doctor``
measured and stops at configuration time, naming the key, the feature and the build.

It **fails closed**: no record, or one measured on another build, is not permission. What
keeps that from being a nuisance is requirement 1.3 -- a configuration that asks for nothing
new needs no record at all, so an untouched repository never meets this code.

``understand.before_side`` is the interesting key. ``"commit"`` asks for the route and is
refused without it; ``"auto"`` asks for it *if the build has it* and falls back otherwise,
which is the whole point of the value and must never be refused (requirement 3.3).
"""

from __future__ import annotations

import pytest
from fixtures.constants import LEAN_REFERENCE_RULES, LEAN_TOKEN_RULES

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import Settings
from scitools_hook.errors import ConfigError
from scitools_hook.models.understand import Availability, Feature, FeatureReport
from scitools_hook.understand.features import ASKED_BY, asked_features, refuse_unavailable

BUILD = "(Build 1204)"
"""What the stubbed installation answers to ``und version``."""


def report(build: str = BUILD, **states: str) -> FeatureReport:
    """A measurement of one build, every feature available unless named otherwise."""
    return FeatureReport(
        build=build,
        features={
            feature: Availability(state=states.get(feature.value, "available"), detail="measured")
            for feature in Feature
        },
    )


def asking(**overrides: object):
    """The shipped settings with one branch replaced, as a configuration file would."""
    settings = default_settings()
    for dotted, value in overrides.items():
        section, _, key = dotted.partition("__")
        setattr(getattr(settings, section), key, value)
    return settings


# --- which keys ask for what --------------------------------------------------------


def test_a_configuration_that_asks_for_nothing_new_needs_no_record() -> None:
    """Requirement 1.3: an untouched repository never meets this code at all."""
    assert asked_features(default_settings()) == {}

    refuse_unavailable(default_settings(), None, BUILD, declared=False)


@pytest.mark.parametrize(
    ("overrides", "feature"),
    [
        ({"understand__sarif": True}, Feature.UNDERSTAND_SARIF),
        ({"understand__before_side": "commit"}, Feature.COMMIT_BEFORE),
        ({"analysis__accuracy_floor": 0.8}, Feature.ACCURACY),
        ({"structure__unused_routines": "warning"}, Feature.UNUSED_RULE),
        *(({f"lean__{rule}": "warning"}, Feature.LEAN_REFERENCES) for rule in LEAN_REFERENCE_RULES),
        *(({f"lean__{rule}": "warning"}, Feature.LEAN_TOKENS) for rule in LEAN_TOKEN_RULES),
    ],
    ids=[
        "sarif",
        "commit",
        "accuracy",
        "unused",
        *LEAN_REFERENCE_RULES,
        *LEAN_TOKEN_RULES,
    ],
)
def test_each_key_asks_for_the_feature_it_needs(
    overrides: dict[str, object], feature: Feature
) -> None:
    """One key, one feature; the mapping is what the refusal message is built from."""
    assert set(asked_features(asking(**overrides)).values()) == {feature}


def every_key_asking() -> Settings:
    """A configuration in which every key that can ask for a feature does ask."""
    settings = asking(
        understand__sarif=True,
        understand__before_side="commit",
        analysis__accuracy_floor=0.8,
        structure__unused_routines="warning",
    )
    for rule in (*LEAN_REFERENCE_RULES, *LEAN_TOKEN_RULES):
        setattr(settings.lean, rule, "warning")
    return settings


def test_the_two_tables_name_the_same_keys_from_both_ends() -> None:
    """The pair of tables, spanned in one assertion, because neither end can see the other.

    ``ASKED_BY`` says which feature a key needs and ``asked_features`` says whether that key
    is switched on, and a key in one and not the other is invisible from where it is written:
    a key ``ASKED_BY`` knows and the enabled table forgets is never asked about, on any
    build, and nothing about it fails; a key the enabled table knows and ``ASKED_BY`` forgets
    raises ``KeyError`` out of a configuration read. Both ends of both failures are here --
    the ``KeyError`` in the call and the equality in the assertion.
    """
    assert set(asked_features(every_key_asking())) == set(ASKED_BY)


def test_the_over_export_rule_asks_the_build_for_nothing_new() -> None:
    """It reads file metrics, the file edges and the definitions walk, all of which every
    build already answers -- so refusing it would refuse a rule the build can run (9.3)."""
    assert asked_features(asking(lean__over_export="warning")) == {}


def test_the_automatic_before_route_asks_for_nothing() -> None:
    """``auto`` falls back to the shadow tree, so refusing it would break requirement 3.3."""
    assert asked_features(asking(understand__before_side="auto")) == {}

    refuse_unavailable(asking(understand__before_side="auto"), None, BUILD, declared=False)


# --- what a refusal says --------------------------------------------------------------


def test_a_key_the_build_cannot_honour_names_the_key_the_feature_and_the_build() -> None:
    """All three, because an operator reading this has to know which of them to change."""
    settings = asking(understand__before_side="commit")

    with pytest.raises(ConfigError) as caught:
        refuse_unavailable(settings, report(commit_before="not on this build"), BUILD, False)

    said = str(caught.value)
    assert "understand.before_side" in said
    assert "commit before" in said
    assert BUILD in said


def test_the_refusal_carries_the_builds_own_reason() -> None:
    """`not on this build` on its own sends the operator back to the terminal."""
    detailed = FeatureReport(
        build=BUILD,
        features={
            Feature.ACCURACY: Availability(
                state="not on this build", detail="Error: Unrecognized arguments."
            )
        },
    )

    with pytest.raises(ConfigError) as caught:
        refuse_unavailable(asking(analysis__accuracy_floor=0.8), detailed, BUILD, False)

    assert "Unrecognized arguments" in str(caught.value)


def test_an_unverified_feature_is_refused_and_sends_the_operator_to_doctor() -> None:
    """A probe that could not run is not permission, and the fix is to run the probe."""
    with pytest.raises(ConfigError) as caught:
        refuse_unavailable(
            asking(understand__sarif=True), report(understand_sarif="unverified"), BUILD, False
        )

    assert "doctor" in str(caught.value.hint or "")


# --- failing closed --------------------------------------------------------------------


def test_a_missing_record_with_a_feature_enabled_asks_for_doctor() -> None:
    """No measurement is not a measurement of yes."""
    with pytest.raises(ConfigError) as caught:
        refuse_unavailable(asking(understand__sarif=True), None, BUILD, declared=False)

    assert "doctor" in str(caught.value.hint or "")
    assert "understand.sarif" in str(caught.value)


def test_a_record_from_another_build_is_not_an_answer_about_this_one() -> None:
    """Upgrading Understand must not leave yesterday's answers standing."""
    with pytest.raises(ConfigError):
        refuse_unavailable(
            asking(understand__sarif=True), report(build="(Build 9999)"), BUILD, False
        )


# --- the architecture name (requirement 4.2) --------------------------------------------


def test_the_built_in_architecture_is_never_a_question_about_the_build() -> None:
    """Every database has ``Directory Structure`` from the moment it exists."""
    refuse_unavailable(default_settings(), None, BUILD, declared=False)


def test_a_declared_architecture_is_supplied_by_the_repository_not_the_build() -> None:
    """A repository with its own architecture file answers the name itself."""
    refuse_unavailable(asking(structure__architecture="Layers"), None, BUILD, declared=True)


def test_a_name_nothing_can_supply_is_refused_with_the_names_that_can() -> None:
    """Requirement 4.2, and it also catches a plain misspelling before two analyses run."""
    offered = FeatureReport(
        build=BUILD,
        features={
            Feature.GENERATED_ARCHS: Availability(
                state="available", generated=["Directory Structure", "Git Stability"]
            )
        },
    )

    with pytest.raises(ConfigError) as caught:
        refuse_unavailable(asking(structure__architecture="Git Stabilty"), offered, BUILD, False)

    assert "Git Stabilty" in str(caught.value)
    assert "Git Stability" in str(caught.value.hint or "")


def test_a_generated_name_the_build_offers_is_accepted() -> None:
    offered = FeatureReport(
        build=BUILD,
        features={
            Feature.GENERATED_ARCHS: Availability(
                state="available", generated=["Directory Structure", "Git Stability"]
            )
        },
    )

    refuse_unavailable(asking(structure__architecture="Git Stability"), offered, BUILD, False)


# --- the token rules, on a build whose lexer probe failed (task 5.5) -----------------


@pytest.mark.parametrize("rule", LEAN_TOKEN_RULES)
def test_a_token_rule_on_a_build_without_the_lexer_is_refused_by_name(rule: str) -> None:
    """Requirement 9.3: the key, the feature and the build, before anything is analysed."""
    settings = asking(**{f"lean__{rule}": "warning"})

    with pytest.raises(ConfigError) as caught:
        refuse_unavailable(settings, report(lean_tokens="not on this build"), BUILD, False)

    assert f"lean.{rule}" in str(caught.value)
    assert caught.value.key == f"lean.{rule}"
    assert "lean tokens" in str(caught.value)
    assert BUILD in str(caught.value)


@pytest.mark.parametrize("rule", LEAN_TOKEN_RULES)
def test_a_token_rule_is_allowed_on_a_build_whose_lexer_answered(rule: str) -> None:
    """The other half: a build that offers the feature runs the rule rather than refusing it.

    Without this, a refusal that fired on every build would pass the test above.
    """
    refuse_unavailable(asking(**{f"lean__{rule}": "warning"}), report(), BUILD, False)


def test_the_duplicate_lines_metric_is_no_key_of_this_family() -> None:
    """``DUPLICATE_METRIC`` is probed and printed, and nothing in ``[lean]`` asks for it.

    A threshold on ``DuplicateLinesOfCode`` goes through the plugin-metric refusal that
    exists, per language and scope; mapping a ``[lean]`` switch to it here would refuse a
    rule the Gate answers from its own lexer pass on every build.
    """
    assert Feature.DUPLICATE_METRIC not in set(ASKED_BY.values())
