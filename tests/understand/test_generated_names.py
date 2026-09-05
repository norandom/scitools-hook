"""What the stored measurement says this build can generate (requirements 1.4, 4.2).

Its own module because the file-level dependency rule leaves a test module room for about
five imports, and the record needs three of its own -- the cache layout it is written into,
the report model it is written as, and the reader that finds it.

Read rather than probed, which is the rule every availability question in this specification
follows: a check measures nothing about the installation, ``doctor`` does the measuring, and a
record from another build is not an answer about this one.
"""

from __future__ import annotations

from pathlib import Path

from scitools_hook.models.cache import CachePaths
from scitools_hook.models.understand import Availability, Feature, FeatureReport
from scitools_hook.understand.features import FEATURES_FILE
from scitools_hook.understand.generated_arch import generated_names

BUILD = "(Build 1262)"
"""The build the record below was measured on; another string is another installation."""

NAMES = ["Directory Structure", "Git Owner", "Git Stability"]
"""Three of the 21 ``und arch -list`` offers on 1262, which is all a test needs."""


def a_cache(tmp_path: Path) -> CachePaths:
    """The cache layout the record is written into, beside the databases it describes."""
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


def recorded(tmp_path: Path, build: str = BUILD, state: str = "available") -> CachePaths:
    """The record ``doctor`` would have left about the generated architectures."""
    paths = a_cache(tmp_path)
    report = FeatureReport(
        build=build,
        features={
            Feature.GENERATED_ARCHS: Availability(state=state, detail="measured", generated=NAMES)
        },
    )
    (paths.root / FEATURES_FILE).write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return paths


def test_the_recorded_names_are_what_the_build_offers(tmp_path: Path) -> None:
    assert generated_names(recorded(tmp_path), BUILD) == NAMES


def test_no_record_offers_nothing(tmp_path: Path) -> None:
    """A repository nothing has diagnosed generates nothing, and is refused at configuration."""
    assert generated_names(a_cache(tmp_path), BUILD) == []


def test_a_record_from_another_build_is_not_an_answer_about_this_one(tmp_path: Path) -> None:
    assert generated_names(recorded(tmp_path, build="(Build 9999)"), BUILD) == []


def test_a_probe_that_could_not_run_is_not_permission(tmp_path: Path) -> None:
    """The test seam answers from fixtures and measures nothing; that is not a yes."""
    assert generated_names(recorded(tmp_path, state="unverified"), BUILD) == []
