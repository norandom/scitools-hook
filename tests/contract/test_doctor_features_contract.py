"""`doctor`'s feature probe against the installed build (requirements 1.1, 1.4).

The unit tests drive shell scripts that answer like each build does. This one asks the build
that is actually here, which is the only thing that can catch a probe that agrees with its own
stub and with nothing else.

Measured on Build 1262 while writing task 2.1: all six available, and 21 generated
architectures offered. Asserted as "every feature available" rather than as that list, because
the list is the build's and will grow.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import FakeCommandLog, MakeGitRepo

from scitools_hook.cli.doctor import render_report
from scitools_hook.models.understand import Feature
from scitools_hook.runner.context import ContextOptions
from scitools_hook.runner.doctor import run_doctor
from scitools_hook.understand.features import FEATURES_FILE, load_features

pytestmark = pytest.mark.contract


def diagnosis(repo: Path, cache: Path, log: FakeCommandLog):
    """`doctor` against the real installation, with its cache in a directory of our own."""
    env = {**os.environ, "XDG_CACHE_HOME": str(cache)}
    return run_doctor(ContextOptions(cwd=repo, env=env, log=log))


def test_this_build_offers_every_feature_of_this_specification(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Build 1262 answers every command the probe asks; a 6.5 install would not."""
    report = diagnosis(git_repo().path, tmp_path / "cache", command_log)

    assert report.understand.features is not None, f"no probe ran; problems: {report.problems}"
    unavailable = {
        feature: found
        for feature, found in report.understand.features.features.items()
        if found.state != "available"
    }
    assert unavailable == {}


def test_the_generated_architectures_this_build_offers_include_the_git_ones(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Requirement 4.3's input: the three architectures derived from a repository's history."""
    report = diagnosis(git_repo().path, tmp_path / "cache", command_log)

    assert report.understand.features is not None
    offered = report.understand.features.features[Feature.GENERATED_ARCHS].generated
    assert {"Git Stability", "Git Owner", "Git Author"} <= set(offered)
    assert "Directory Structure" in offered


def test_the_report_is_stored_and_reads_back(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """What task 2.3 reads to validate a configuration without paying for a probe."""
    cache = tmp_path / "cache"
    report = diagnosis(git_repo().path, cache, command_log)

    assert report.cache is not None
    assert (report.cache.root / FEATURES_FILE).exists()
    again = load_features(report.cache)
    assert again is not None
    assert again.build == report.understand.und_version
    assert again.offers(Feature.PLUGIN_METRICS) is True


def test_the_probe_leaves_nothing_behind_in_the_repository(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """It builds its own scratch project, and turns *that* into a repository, never yours."""
    repo = git_repo()
    diagnosis(repo.path, tmp_path / "cache", command_log)

    assert sorted(path.name for path in repo.path.iterdir()) == [".git"]


def test_the_rows_an_operator_reads_say_available_for_every_feature(
    git_repo: MakeGitRepo, tmp_path: Path, command_log: FakeCommandLog
) -> None:
    """Requirement 1.1 as the operator meets it: six rows in the Understand block."""
    text = render_report(diagnosis(git_repo().path, tmp_path / "cache", command_log))

    rows = [line.strip() for line in text.splitlines() if line.strip().startswith("feature ")]
    assert len(rows) == 6, rows
    answers = [row.split(":", 1)[1].strip() for row in rows]
    assert all(answer.startswith("available") for answer in answers), rows
