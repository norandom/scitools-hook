"""Stats reducers and ignore-regex filtering (task 4.1; req 3.4, 3.6).

The reducers are the only place a stats prefix becomes a number, and the ignore filter is
the only place a configured regular expression decides whether an entity is evaluated, so
both the success and the "cannot be computed" paths are pinned here.
"""

from __future__ import annotations

import re

import pytest
from fixtures import APP, BUILD_PARSER, snapshot_fixture

from scitools_hook.analysis.population import (
    IgnoreFilter,
    filter_keys,
    filter_snapshot_keys,
    reduce,
)
from scitools_hook.config.metric_names import STATS_REDUCERS
from scitools_hook.config.models import IgnoreRules
from scitools_hook.models.snapshot import EntityKey

VALUES = [1.0, 2.0, 2.0, 3.0, 6.0]
"""One vector every reducer accepts; ``2`` is its single mode."""

MAIN = EntityKey(scope="routine", path=APP, longname="app.main", parameters="argv")
ENGINE_CLASS = EntityKey(scope="class", path="src/analysis/engine.py", longname="engine.Engine")
APP_FILE = EntityKey(scope="file", path=APP, longname=APP)
PROJECT = EntityKey(scope="project", path="", longname="project")


@pytest.mark.parametrize("prefix", sorted(STATS_REDUCERS))
def test_every_prefix_reduces_its_population_to_a_float(prefix: str) -> None:
    """Each documented prefix (req 3.4) reduces a vector with the stdlib's own answer."""
    reduced = reduce(prefix, VALUES)

    assert reduced == pytest.approx(float(STATS_REDUCERS[prefix](VALUES)))
    assert isinstance(reduced, float)


@pytest.mark.parametrize("prefix", sorted(STATS_REDUCERS))
def test_an_empty_population_is_reported_as_none_rather_than_raising(prefix: str) -> None:
    """A vector no reducer can consume yields ``None`` so the caller can report it once."""
    assert reduce(prefix, []) is None


def test_a_prefix_is_matched_case_insensitively() -> None:
    """``parse_metric_name`` canonicalises prefixes, but ``reduce`` accepts either form."""
    assert reduce("avg", VALUES) == pytest.approx(2.8)


def test_a_multimodal_vector_still_reduces_under_the_current_mode_semantics() -> None:
    """``statistics.mode`` returns the first mode since 3.8; the failure path is empty data."""
    assert reduce("MODE", [1.0, 1.0, 2.0, 2.0]) == pytest.approx(1.0)


def test_an_unknown_prefix_is_a_programming_error() -> None:
    """Configuration rejects unknown prefixes, so reaching ``reduce`` with one is a bug."""
    with pytest.raises(ValueError, match="NOPE"):
        reduce("NOPE", VALUES)


def test_patterns_are_compiled_once_per_scope() -> None:
    """``from_rules`` compiles every regex up front instead of per entity (req 3.6)."""
    ignore = IgnoreFilter.from_rules(IgnoreRules(routines=[r"^app\."], files=[r"/util/"]))

    assert all(
        isinstance(pattern, re.Pattern)
        for patterns in ignore.patterns.values()
        for pattern in patterns
    )
    assert ignore.patterns["class"] == ()


def test_a_routine_pattern_matches_the_qualified_longname() -> None:
    """`srccheck` ignores on ``longname()``; the Gate keeps that behaviour."""
    ignore = IgnoreFilter.from_rules(IgnoreRules(routines=[r"^app\.build_"]))

    assert ignore.is_ignored(BUILD_PARSER)
    assert not ignore.is_ignored(MAIN)


def test_a_file_pattern_also_matches_the_repository_relative_path() -> None:
    """A file ignore rule is naturally written as a path fragment."""
    ignore = IgnoreFilter.from_rules(IgnoreRules(files=[r"^src/cli/"]))

    assert ignore.is_ignored(APP_FILE)
    assert not ignore.is_ignored(ENGINE_CLASS)


def test_a_pattern_never_leaks_into_another_scope() -> None:
    """Files, classes and routines have their own lists (req 3.6)."""
    ignore = IgnoreFilter.from_rules(IgnoreRules(classes=[r"Engine"]))

    assert ignore.is_ignored(ENGINE_CLASS)
    assert not ignore.is_ignored(BUILD_PARSER)
    assert not ignore.is_ignored(APP_FILE)
    assert not ignore.is_ignored(PROJECT)


def test_filtering_keeps_the_survivors_and_counts_the_ignored_per_scope() -> None:
    """Ignored entities leave the evaluation but are still counted (req 3.6)."""
    ignore = IgnoreFilter.from_rules(IgnoreRules(routines=[r"^app\."], files=[r"^src/cli/"]))

    filtered = filter_keys([BUILD_PARSER, MAIN, ENGINE_CLASS, APP_FILE], ignore)

    assert filtered.keys == {ENGINE_CLASS}
    assert filtered.ignored_counts == {"routine": 2, "file": 1}


def test_filtering_without_rules_keeps_every_key() -> None:
    """No ignore rules means no exclusions and no counts."""
    filtered = filter_keys([BUILD_PARSER, APP_FILE], None)

    assert filtered.keys == {BUILD_PARSER, APP_FILE}
    assert filtered.ignored_counts == {}


def test_a_snapshot_can_be_filtered_for_whole_project_mode() -> None:
    """Whole-project mode (req 4.8) evaluates every surviving entity of the snapshot."""
    snapshot = snapshot_fixture("after")
    ignore = IgnoreFilter.from_rules(IgnoreRules(files=[r"^src/util/"]))

    filtered = filter_snapshot_keys(snapshot, ignore)

    assert filtered.ignored_counts == {"file": 1}
    assert filtered.keys == set(snapshot.entities) - {
        EntityKey(scope="file", path="src/util/text.py", longname="src/util/text.py")
    }
