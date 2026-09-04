"""The ``recommend`` command end to end: a whole-project extraction, priced against config.

Everything below the command is real except Understand: a **real** git repository, a **real**
``ShadowSync``, a **real** ``DatabaseManager``. Three properties carry the feature and each is
asserted against something the command actually did rather than against what it returned:

* **The extraction is whole-project.** A percentile over the handful of files a change touched
  is not a statement about a repository, exactly as a maximum over them is not a baseline
  (``runner.baseline_cmd`` recorded that reason first). The test stages one file and then
  asserts the extractor was asked for the *other* one -- reading the request the double
  received, not the answer it gave.
* **No before side is built.** A recommendation compares nothing, so the second database must
  never be synced or analysed. Read off the ``und`` commands that ran.
* **The configured limits are what is priced.** A baseline sits in the repository holding a
  much lower value for the same rule; if the adaptive baseline ever reached this command, the
  report would price 4 instead of 10 and this test would fail.
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

from scitools_hook.analysis.recommend import Recommendation
from scitools_hook.config.models import (
    BaselineSettings,
    Limit,
    PathScope,
    Provenance,
    Settings,
    ThresholdSpec,
)
from scitools_hook.config.validate import validate_settings
from scitools_hook.git.repo import GitRepo
from scitools_hook.git.shadow import ShadowSync
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.snapshot import ProjectSnapshot, Side
from scitools_hook.runner.context import RunContext
from scitools_hook.runner.recommend import NOTHING_NOTE, RecommendCmd
from scitools_hook.understand.database import DatabaseManager
from scitools_hook.understand.fake import FixtureApiRunner, FixtureUndCli, fixture_env

BASELINE_FILE = "gate-baseline.json"


def spec(scope: str, metric: str, maximum: float) -> ThresholdSpec:
    """One configured ceiling."""
    return ThresholdSpec.model_validate(
        {"scope": scope, "metric": metric, "limit": Limit(max=maximum)}
    )


THRESHOLDS: Sequence[ThresholdSpec] = (spec("routine", "CyclomaticStrict", 10),)


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
            _record("routine", "src/app.py", "app.run", CyclomaticStrict=3),
            _record("routine", "src/util.py", "util.helper", CyclomaticStrict=9),
        ],
    }
)
"""The whole project. The worst routine lives in ``src/util.py``, which is never staged."""

STAGED_ONLY_SNAPSHOT = ProjectSnapshot.model_validate(
    {
        "side": "after",
        "languages": ["Python"],
        "entities": [_record("routine", "src/app.py", "app.run", CyclomaticStrict=3)],
    }
)
"""What a bounded extraction of the staged change would have seen: ``src/app.py`` alone."""


def settings_with(*thresholds: ThresholdSpec) -> Settings:
    """Settings carrying exactly ``thresholds`` and a repository-level baseline path."""
    return Settings(
        thresholds=list(thresholds), baseline=BaselineSettings(file=Path(BASELINE_FILE))
    )


@dataclass(frozen=True)
class Harness:
    """One repository, its cache, the two doubles and the command over them."""

    builder: GitRepoBuilder
    paths: CachePaths
    und: UndStub
    extractor: ScriptedExtractor
    progress: FakeProgress
    command: RecommendCmd

    def run(self, target: float = 0.95) -> Recommendation:
        """Measure the project."""
        return self.command.run(target)

    @property
    def analyzed_sides(self) -> list[str]:
        """Which databases were analysed, read from the ``und`` commands that actually ran."""
        return self.und.analyzed_sides(self.paths.before_db, self.paths.after_db)


def make_harness(
    builder: GitRepoBuilder,
    tmp_path: Path,
    *,
    answers: Mapping[Side, Sequence[ProjectSnapshot]] | None = None,
    thresholds: Sequence[ThresholdSpec] = THRESHOLDS,
    settings: Settings | None = None,
) -> Harness:
    """Bind a recommend command to ``builder``'s repository with its cache under ``tmp_path``."""
    effective = settings if settings is not None else settings_with(*thresholds)
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
        command=RecommendCmd(context, manager, extractor),
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
    """A committed two-file project with one configured ceiling."""
    return make_harness(project(git_repo()), tmp_path)


# --- the measurement --------------------------------------------------------------


def test_the_measurement_covers_the_whole_project_not_the_staged_change(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``src/app.py`` is staged and ``src/util.py`` is not; both must be measured.

    Asserted on the file set the extractor was **asked** for, so a command that requested the
    right files and then discarded them still fails, and one that requested only the staged
    file cannot pass by having a double that answers with everything.
    """
    builder = project(git_repo())
    builder.write("src/app.py", "def run():\n    return 3\n")
    builder.stage("src/app.py")
    harness = make_harness(
        builder, tmp_path, answers={"after": [WHOLE_PROJECT_SNAPSHOT, STAGED_ONLY_SNAPSHOT]}
    )

    result = harness.run()

    assert harness.extractor.requested("after", 0) == {"src/app.py", "src/util.py"}
    assert result.counts == {"routine": 2}
    (advice,) = result.advice
    assert advice.distribution.maximum == 9.0


def test_a_recommendation_builds_no_before_side(repo: Harness) -> None:
    """Nothing is compared, so the second database is never synced or analysed."""
    repo.run()

    assert repo.analyzed_sides == ["after"]
    assert repo.extractor.sides == ["after"]


def test_the_configured_ceilings_are_priced_and_not_the_recorded_baseline(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A baseline in the repository must not become the limit this report is about.

    ``baseline`` says where you are and ``recommend`` says where to aim; a recommendation
    computed against a recorded baseline would be a description of the first dressed as the
    second. The stored value is 4 and the configured limit is 10, so the two answers differ.
    """
    builder = project(git_repo())
    (builder.path / BASELINE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "captured_at": STARTED_AT,
                "values": {"routine.CyclomaticStrict": 4.0},
            }
        ),
        encoding="utf-8",
    )
    harness = make_harness(builder, tmp_path)

    (advice,) = harness.run().advice

    assert advice.configured == 10.0
    assert advice.verdict == "keep"


def test_the_target_reaches_the_measurement(git_repo: MakeGitRepo, tmp_path: Path) -> None:
    """The same project and the same limit, two targets, two verdicts.

    The routines score 3 and 9 against a configured limit of 5, so exactly half the project is
    inside it. A target of 0.5 calls that a fit and keeps 5; a target of 0.95 does not, and
    proposes the smallest readable rung that holds both, which is 10. Asserting one target
    alone would pass on a command that ignored the argument entirely.
    """
    builder = project(git_repo())
    ceiling = (spec("routine", "CyclomaticStrict", 5),)
    harness = make_harness(builder, tmp_path / "a", thresholds=ceiling)
    lenient = make_harness(builder, tmp_path / "b", thresholds=ceiling)

    (strict_advice,) = harness.run(0.95).advice
    (lenient_advice,) = lenient.run(0.5).advice

    assert strict_advice.share_inside == 0.5
    assert (strict_advice.verdict, strict_advice.proposed) == ("raise", 10.0)
    assert (lenient_advice.verdict, lenient_advice.proposed) == ("keep", None)


# --- nothing to measure -----------------------------------------------------------


def test_a_repository_with_nothing_analysable_answers_rather_than_failing(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A README-only repository is a question with an answer, not an analysis failure."""
    builder = git_repo()
    builder.write("README.md", "# nothing to analyse\n")
    builder.stage()
    builder.commit("initial")
    harness = make_harness(builder, tmp_path, answers={"after": []})

    result = harness.run()

    assert result == Recommendation(counts={}, advice=(), skipped=())
    assert NOTHING_NOTE in harness.progress.notes
    assert harness.extractor.targets == [], "nothing analysable must reach no extraction"
    assert harness.analyzed_sides == []


def test_the_configured_path_scopes_reach_the_recommendation(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The report cannot disclose a scope the command never passed on.

    Asserted through the command rather than through ``analysis.recommend`` directly: the
    wiring is the part that can be forgotten, and a report that silently stopped disclosing
    would look exactly like a repository with no scopes.
    """
    builder = project(git_repo())
    effective = settings_with(*THRESHOLDS)
    effective.scope["tests"] = PathScope.model_validate(
        {"paths": ["tests/**"], "thresholds": {"routine": {"CyclomaticStrict": 15}}}
    )
    harness = make_harness(builder, tmp_path, settings=effective)

    assert harness.run().scoped == ("tests",)


def test_a_repository_with_no_path_scope_reports_none(repo: Harness) -> None:
    """The paired negative, so the assertion above cannot pass by always answering ``tests``."""
    assert repo.run().scoped == ()
