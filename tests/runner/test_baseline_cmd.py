"""The ``baseline`` command end to end: whole-project capture, one entry per threshold (8.4).

Everything below the command is real except Understand: a **real** git repository, a **real**
:class:`~scitools_hook.git.shadow.ShadowSync`, a **real**
:class:`~scitools_hook.understand.database.DatabaseManager` and the **real**
:class:`~scitools_hook.runner.baseline_store.BaselineStore`, so the assertions read the file
that was actually written rather than the object that was going to be written.

Two properties carry requirement 8.1 and they are asserted separately:

* **One entry per configured threshold**, compared as a set in *both* directions -- a missing
  metric and a stray one are different defects and each has to fail.
* **The capture is a whole-project one.** Task 8.3 confined adaptive tightening to
  ``check --all`` for the same reason and handed this on: a capture taken from the staged
  change would record limits drawn from a handful of files, and requirement 8.4 forbids the
  operator raising them again. The test stages one file and then asserts the capture read the
  *other* one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeCommandLog, FakeProgress, GitRepoBuilder, MakeGitRepo
from fakes.runner import ScriptedExtractor, UndStub, scripted
from fixtures.constants import BUILD, STARTED_AT

from scitools_hook.config.models import (
    BaselineSettings,
    Limit,
    Provenance,
    Settings,
    ThresholdSpec,
)
from scitools_hook.config.validate import validate_settings
from scitools_hook.errors import ConfigError
from scitools_hook.git.repo import GitRepo
from scitools_hook.git.shadow import ShadowSync
from scitools_hook.models.baseline import Baseline
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.snapshot import ProjectSnapshot, Side
from scitools_hook.runner.baseline_cmd import BaselineCapture, BaselineCmd
from scitools_hook.runner.baseline_store import BaselineStore
from scitools_hook.runner.context import RunContext
from scitools_hook.understand.database import DatabaseManager
from scitools_hook.understand.fake import FixtureApiRunner, FixtureUndCli, fixture_env

BASELINE_FILE = "gate-baseline.json"
"""A repository-level baseline file, which is requirement 8.1's default location."""


def spec(scope: str, metric: str, maximum: float) -> ThresholdSpec:
    """One configured threshold."""
    return ThresholdSpec.model_validate(
        {"scope": scope, "metric": metric, "limit": Limit(max=maximum)}
    )


THRESHOLDS: Sequence[ThresholdSpec] = (
    spec("routine", "CyclomaticStrict", 10),
    spec("routine", "CountLineCode", 60),
    spec("file", "CountLineCode", 500),
    spec("project", "AVG:CountLineCode", 200),
)
"""Four thresholds across three scopes, one of them stats-prefixed (req 3.4)."""

UNMEASURABLE = spec("class", "PercentLackOfCohesion", 40)
"""A threshold this project's snapshot answers nothing for; requirement 8.1 records none."""


def settings_with(*thresholds: ThresholdSpec, baseline_file: str = BASELINE_FILE) -> Settings:
    """Settings carrying exactly ``thresholds`` and a repository-level baseline path."""
    return Settings(
        thresholds=list(thresholds), baseline=BaselineSettings(file=Path(baseline_file))
    )


# --- the project the capture reads ----------------------------------------------


def _record(scope: str, path: str, longname: str, **metrics: float) -> dict[str, Any]:
    """One entity record in the wire shape ``ProjectSnapshot`` validates."""
    return {
        "ref": {
            "key": {"scope": scope, "path": path, "longname": longname, "parameters": None},
            "kind": f"Python {scope.title()}",
            "name": longname.rsplit(".", 1)[-1],
            "line": 1,
        },
        "language": "Python",
        "metrics": dict(metrics),
        "archs": [],
    }


WHOLE_PROJECT_SNAPSHOT = ProjectSnapshot.model_validate(
    {
        "side": "after",
        "languages": ["Python"],
        "entities": [
            _record("file", "src/app.py", "src/app.py", CountLineCode=40),
            _record("routine", "src/app.py", "app.run", CyclomaticStrict=3, CountLineCode=18),
            _record("file", "src/util.py", "src/util.py", CountLineCode=120),
            _record("routine", "src/util.py", "util.helper", CyclomaticStrict=9, CountLineCode=55),
        ],
        "file_edges": [],
        "arch_nodes": [],
        "arch_edges": [],
        "populations": {"project": {"CountLineCode": [40.0, 120.0]}},
    }
)
"""The whole project: the worst routine and the longest file both live in ``src/util.py``."""

STAGED_ONLY_SNAPSHOT = ProjectSnapshot.model_validate(
    {
        "side": "after",
        "languages": ["Python"],
        "entities": [
            _record("file", "src/app.py", "src/app.py", CountLineCode=40),
            _record("routine", "src/app.py", "app.run", CyclomaticStrict=3, CountLineCode=18),
        ],
        "file_edges": [],
        "arch_nodes": [],
        "arch_edges": [],
        "populations": {"project": {"CountLineCode": [40.0, 120.0]}},
    }
)
"""What a *bounded* extraction of the staged change would have seen: ``src/app.py`` alone.

Only element maxima differ between the two; the project population vector is identical,
because a bounded extraction still reports project-wide populations. That is exactly why the
element maxima are the ones a partial capture gets wrong.
"""


# --- the harness ----------------------------------------------------------------


@dataclass(frozen=True)
class Harness:
    """One repository, its cache, the two doubles and the command over them."""

    builder: GitRepoBuilder
    paths: CachePaths
    und: UndStub
    extractor: ScriptedExtractor
    progress: FakeProgress
    command: BaselineCmd

    def run(self, path: Path | None = None) -> BaselineCapture:
        """Capture the baseline, optionally into a chosen file."""
        return self.command.run(path)

    def stored(self, path: Path) -> Baseline:
        """The document that actually reached the file, read back through the store."""
        stored, issues = BaselineStore(path).load(list(THRESHOLDS))
        assert issues == [], issues
        assert stored is not None, f"{path} holds no baseline"
        return stored

    @property
    def analyzed_sides(self) -> list[str]:
        """Which databases were analysed, read from the ``und`` commands that actually ran."""
        return self.und.analyzed_sides(self.paths.before_db, self.paths.after_db)

    @property
    def notes(self) -> list[str]:
        """Everything the run said on the diagnostics channel."""
        return list(self.progress.notes)


def make_harness(
    builder: GitRepoBuilder,
    tmp_path: Path,
    settings: Settings | None = None,
    *,
    answers: Mapping[Side, Sequence[ProjectSnapshot]] | None = None,
) -> Harness:
    """Bind a baseline command to ``builder``'s repository with its cache under ``tmp_path``."""
    effective = settings if settings is not None else settings_with(*THRESHOLDS)
    repo = GitRepo.discover(builder.path, FakeCommandLog())
    paths = CachePaths.for_repo(repo.common_dir, effective.understand.db_location, tmp_path / "c")
    und = UndStub(version_text=BUILD)
    progress = FakeProgress()
    manager = DatabaseManager(
        paths, und, ShadowSync(repo, paths, effective.project), effective, progress
    )
    extractor = scripted(answers if answers is not None else {"after": [WHOLE_PROJECT_SNAPSHOT]})
    context = RunContext(
        settings=effective,
        provenance=Provenance(),
        availability=validate_settings(effective, None),
        understand=fixture_env(tmp_path / "fixtures"),
        und=FixtureUndCli(tmp_path / "fixtures"),
        api=FixtureApiRunner(tmp_path / "fixtures"),
        repo=repo,
        env={},
        log=FakeCommandLog(),
        progress=progress,
        started_at=STARTED_AT,
    )
    return Harness(
        builder=builder,
        paths=paths,
        und=und,
        extractor=extractor,
        progress=progress,
        command=BaselineCmd(context, manager, extractor),
    )


def project(builder: GitRepoBuilder) -> GitRepoBuilder:
    """The two-file sample project, committed once."""
    builder.write("src/app.py", "def run():\n    return 1\n")
    builder.write("src/util.py", "def helper():\n    return 2\n")
    builder.write("README.md", "# sample\n")
    builder.stage()
    builder.commit("initial")
    return builder


@pytest.fixture
def repo(git_repo: MakeGitRepo, tmp_path: Path) -> Harness:
    """A committed two-file project with the four configured thresholds."""
    return make_harness(project(git_repo()), tmp_path)


# --- requirement 8.1: one entry per configured threshold ------------------------


def test_the_capture_records_one_entry_for_every_configured_threshold(repo: Harness) -> None:
    """Both directions: a threshold with no entry and an entry with no threshold both fail."""
    captured = repo.run()

    assert repo.extractor.sides == ["after"], "the capture must have reached the extractor"
    assert set(captured.baseline.values) == {threshold.rule for threshold in THRESHOLDS}
    assert captured.missing == ()


def test_each_entry_is_the_projects_current_worst_value(repo: Harness) -> None:
    """Requirement 8.1: the maximum for an element scope, the reduction for a population."""
    captured = repo.run()

    assert captured.baseline.values == {
        "routine.CyclomaticStrict": 9,
        "routine.CountLineCode": 55,
        "file.CountLineCode": 120,
        "project.AVG:CountLineCode": 80,
    }


def test_the_capture_is_taken_from_the_whole_project_not_from_the_staged_change(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A bounded capture would write limits the rest of the project does not meet (8.4).

    ``src/app.py`` is staged and ``src/util.py`` is not, and every recorded maximum belongs to
    ``src/util.py`` -- so a capture that had honoured the staged selection would record 3, 18
    and 40 instead and the next ordinary commit would fail against limits nobody agreed to.
    """
    builder = project(git_repo())
    builder.write("src/app.py", "def run():\n    return 3\n")
    builder.stage("src/app.py")
    harness = make_harness(
        builder, tmp_path, answers={"after": [WHOLE_PROJECT_SNAPSHOT, STAGED_ONLY_SNAPSHOT]}
    )

    captured = harness.run()

    assert harness.extractor.requested("after", 0) == {"src/app.py", "src/util.py"}
    assert captured.baseline.values["routine.CyclomaticStrict"] == 9
    assert captured.baseline.values["file.CountLineCode"] == 120


def test_a_capture_builds_no_before_side(repo: Harness) -> None:
    """A capture compares nothing, so the second database is never synced or analysed."""
    repo.run()

    assert repo.analyzed_sides == ["after"]


def test_the_capture_is_stamped_with_the_run_instant_rather_than_the_clock(repo: Harness) -> None:
    """``RunContext`` reads the clock once, so every record of a run agrees on when it was."""
    captured = repo.run()

    assert captured.baseline.captured_at == STARTED_AT
    assert repo.stored(repo.builder.path / BASELINE_FILE).captured_at == STARTED_AT


# --- where the file goes --------------------------------------------------------


def test_the_baseline_is_written_to_the_configured_repository_level_file(repo: Harness) -> None:
    """Requirement 8.1's default: a repository-level file, resolved against the root."""
    captured = repo.run()

    destination = repo.builder.path / BASELINE_FILE
    assert captured.path == destination
    assert captured.written is True
    assert repo.stored(destination).values == captured.baseline.values


def test_the_written_document_is_the_json_the_design_specifies(repo: Harness) -> None:
    """The file is a contract with the operator's editor as much as with the next run."""
    repo.run()

    document = json.loads((repo.builder.path / BASELINE_FILE).read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert document["captured_at"] == STARTED_AT
    assert document["values"]["routine.CyclomaticStrict"] == 9


def test_a_chosen_path_overrides_the_configured_one(repo: Harness, tmp_path: Path) -> None:
    """Requirement 8.1: "a location the operator chooses", defaulting to the repository file."""
    chosen = tmp_path / "elsewhere" / "baseline.json"
    chosen.parent.mkdir()

    captured = repo.run(chosen)

    assert captured.path == chosen
    assert repo.stored(chosen).values["routine.CyclomaticStrict"] == 9
    assert not (repo.builder.path / BASELINE_FILE).exists(), "the configured file is untouched"


def test_a_destination_that_cannot_hold_a_baseline_is_refused(
    repo: Harness, tmp_path: Path
) -> None:
    """The store already refuses these; the command must let the refusal through, not eat it."""
    occupied = tmp_path / "a-directory"
    occupied.mkdir()

    with pytest.raises(ConfigError) as refused:
        repo.run(occupied)

    assert str(occupied) in str(refused.value)


# --- what the project cannot answer for -----------------------------------------


def test_a_threshold_the_project_reports_no_value_for_is_named_and_omitted(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A baseline must never claim a value it did not observe -- nor hide that it did not."""
    settings = settings_with(*THRESHOLDS, UNMEASURABLE)
    harness = make_harness(project(git_repo()), tmp_path, settings)

    captured = harness.run()

    assert captured.missing == (UNMEASURABLE.rule,)
    assert UNMEASURABLE.rule not in captured.baseline.values
    assert any(UNMEASURABLE.rule in note for note in harness.notes)


def test_nothing_analysable_captures_nothing_and_writes_no_file(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``ensure_side`` would raise exit 5 here; and an empty file would look like a finished job."""
    builder = git_repo()
    builder.write("README.md", "# nothing to analyse\n")
    builder.stage()
    builder.commit("docs only")
    harness = make_harness(builder, tmp_path, answers={})

    captured = harness.run()

    assert captured.written is False
    assert captured.baseline.values == {}
    assert not (builder.path / BASELINE_FILE).exists()
    assert harness.und.commands == [], "no database may be created for a capture of nothing"
    assert captured.missing == tuple(sorted(t.rule for t in THRESHOLDS))
    assert any("no baseline was captured" in note for note in harness.notes)
