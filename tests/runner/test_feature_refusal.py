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

from scitools_hook.config.defaults import default_settings
from scitools_hook.errors import ConfigError
from scitools_hook.models.understand import Availability, Feature, FeatureReport
from scitools_hook.understand.features import asked_features, refuse_unavailable

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
    ],
    ids=["sarif", "commit", "accuracy", "unused"],
)
def test_each_key_asks_for_the_feature_it_needs(
    overrides: dict[str, object], feature: Feature
) -> None:
    """One key, one feature; the mapping is what the refusal message is built from."""
    assert set(asked_features(asking(**overrides)).values()) == {feature}


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
