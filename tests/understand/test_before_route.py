"""Which route builds the before side, decided from a setting and a measurement (3.3, 1.4).

Two functions and no processes. ``wanted`` reads the setting; ``offers`` reads the record
``doctor`` left beside the databases. Keeping them here, apart from the run that uses them,
is what lets the decision be exercised over every combination without building anything.

``auto`` is the value that matters. It asks for the commit route *if the build has it* and
falls back silently otherwise, which is what keeps a 6.5 install working with no
configuration change and no refusal -- and what makes ``shadow``, the shipped default, a
deliberate choice rather than an accident of the build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitools_hook.config.defaults import default_settings
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.understand import Availability, Feature, FeatureReport
from scitools_hook.understand.commit_before import offers, wanted
from scitools_hook.understand.features import FEATURES_FILE

BUILD = "(Build 1262)"
"""The build the record below was measured on; another string is another installation."""


def layout(tmp_path: Path) -> CachePaths:
    """The cache layout the database manager owns."""
    root = tmp_path / "cache"
    root.mkdir(parents=True, exist_ok=True)
    return CachePaths(
        root=root,
        before_tree=root / "before",
        after_tree=root / "after",
        before_db=root / "before.und",
        after_db=root / "after.und",
        state=root / "state.json",
        graphs=root / "graphs",
    )


def measured(tmp_path: Path, build: str = BUILD, **states: str) -> CachePaths:
    """Write the record ``doctor`` would have left, every feature available unless named."""
    paths = layout(tmp_path)
    report = FeatureReport(
        build=build,
        features={
            found: Availability(state=states.get(found.value, "available"), detail="measured")
            for found in Feature
        },
    )
    (paths.root / FEATURES_FILE).write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return paths


def asking(chosen: str):
    """The shipped settings with ``understand.before_side`` replaced."""
    settings = default_settings()
    settings.understand.before_side = chosen
    return settings


# --- the setting (requirement 3.3) --------------------------------------------------------


def test_the_shipped_default_is_the_route_that_has_always_worked() -> None:
    """Requirement 1.3: every feature of this specification ships off, this one included."""
    assert default_settings().understand.before_side == "shadow"
    assert wanted(default_settings(), True) is False


@pytest.mark.parametrize(
    ("chosen", "offered", "expected"),
    [
        ("auto", True, True),
        ("auto", False, False),
        ("commit", True, True),
        ("commit", False, True),
        ("shadow", True, False),
        ("shadow", False, False),
    ],
    ids=["auto-yes", "auto-no", "commit-yes", "commit-forced", "shadow-yes", "shadow-forced"],
)
def test_the_setting_and_the_measurement_decide_the_route(
    chosen: str, offered: bool, expected: bool
) -> None:
    """``auto`` follows the build; the other two are the operator overriding it.

    ``commit`` on a build that does not offer the route is still ``True``, and that is
    deliberate: the configuration check of requirement 1.2 refuses that combination before any
    run starts, so this function never has to second-guess it.
    """
    assert wanted(asking(chosen), offered) is expected


# --- the measurement (requirements 1.2, 1.4) -----------------------------------------------


def test_a_record_that_says_available_offers_it(tmp_path: Path) -> None:
    assert offers(measured(tmp_path), BUILD, Feature.COMMIT_BEFORE) is True


def test_no_record_at_all_offers_nothing(tmp_path: Path) -> None:
    """A repository nothing has diagnosed takes the shadow route, which always works."""
    assert offers(layout(tmp_path), BUILD, Feature.COMMIT_BEFORE) is False


def test_a_record_from_another_build_is_not_an_answer_about_this_one(tmp_path: Path) -> None:
    """Upgrading Understand must not leave yesterday's answers standing."""
    assert offers(measured(tmp_path, build="(Build 9999)"), BUILD, Feature.COMMIT_BEFORE) is False


def test_a_feature_the_record_says_is_missing_is_not_offered(tmp_path: Path) -> None:
    """Each feature is asked separately: a build may have one report and not the other."""
    paths = measured(tmp_path, commit_before="not on this build")

    assert offers(paths, BUILD, Feature.COMMIT_BEFORE) is False
    assert offers(paths, BUILD, Feature.ACCURACY) is True


def test_an_unverified_probe_is_not_permission(tmp_path: Path) -> None:
    """The test seam answers from fixtures and measures nothing; that is not a yes."""
    assert offers(measured(tmp_path, accuracy="unverified"), BUILD, Feature.ACCURACY) is False
