"""The check pipeline end to end: git, shadows, databases, rules, ``RunResult`` (task 8.3).

Everything below the pipeline is real except Understand itself. Each test drives a **real**
``git`` repository, a **real** :class:`ShadowSync` and a **real**
:class:`~scitools_hook.understand.database.DatabaseManager`, and stands in only for the two
things that need a licence: ``und`` (a fake that records its commands and creates the database
directory an operator's ``und create`` would) and the snapshot extractor (a stub that answers
from the committed fixture snapshots and records every :class:`SnapshotTarget` it was given).

The stub extractor is scripted with an exact number of answers per side, so a run that
extracts more or fewer times than the design prescribes fails loudly rather than reusing an
answer. That is deliberate: with this many doubles in play, "the fake was never reached" is
the failure mode that produces a confident green about nothing.

The properties pinned here are the ones the requirements turn on:

* **Requirement 4.9 decides whether ordinary commits are blocked.** Four *different* inputs
  reach "nothing was analyzed": an empty staged set, a staged set of files no configured
  language covers, a repository whose ``HEAD`` does not exist yet, and -- separately -- a
  deletions-only change, which is *not* an early exit but must still finish clean (req 4.10).
  Each is a different input, not a renamed test.
* **The before side exists only when ``HEAD`` does and the mode needs it.** An unborn branch
  must skip the ratchet, not crash on it, and ``--all`` must not build a before side at all.
* **Every finding carries a hint** (req 7.2), asserted over the whole set in every scenario
  that produces findings, because the evaluators leave ``hint`` empty on purpose.
* **The evaluator order is part of the contract** -- thresholds, ratchet, structure,
  codecheck, then classify -- because ``attach_before`` runs between the first two and nothing
  can be pre-existing without it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeCommandLog, FakeProgress, GitRepoBuilder, MakeGitRepo
from fakes import FakeUndCli
from fixtures.constants import BUILD, STARTED_AT

from scitools_hook.analysis.ratchet import within_limit
from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import (
    BaselineSettings,
    CodeCheckSettings,
    CouplingRule,
    IgnoreRules,
    LayerRule,
    Limit,
    Provenance,
    RatchetSettings,
    Settings,
)
from scitools_hook.config.validate import AvailabilityReport, validate_settings
from scitools_hook.errors import ConfigError
from scitools_hook.git.repo import GitRepo
from scitools_hook.git.shadow import ShadowSync
from scitools_hook.models.baseline import Baseline
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.findings import Finding, RunResult
from scitools_hook.models.snapshot import ParseError, ProjectSnapshot, Side
from scitools_hook.models.understand import AnalyzeResult, RawViolation
from scitools_hook.runner.baseline_store import BaselineStore
from scitools_hook.runner.check import CheckPipeline, Selection
from scitools_hook.runner.context import RunContext
from scitools_hook.understand.codecheck import CodeCheckRunner
from scitools_hook.understand.database import DatabaseManager
from scitools_hook.understand.fake import FixtureApiRunner, FixtureUndCli, fixture_env
from scitools_hook.understand.snapshot import SnapshotExtractor, SnapshotTarget

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
"""Where the committed before/after snapshots of the sample project live."""


DROPPED = "PercentLackOfCohesion"
"""A real C++/Java class metric Python lacks; task 2.4 drops the shipped default and reports it."""

SAMPLE_FILES = (
    "src/analysis/engine.py",
    "src/analysis/rules.py",
    "src/cli/app.py",
    "src/understand/adapter.py",
    "src/util/text.py",
)
"""The five files the fixture snapshots describe; the repository must hold the same paths."""


# --- stand-ins for the two things that need a licence ---------------------------


@dataclass
class UndStub(FakeUndCli):
    """``FakeUndCli`` that also creates the database directory, as ``und create`` does.

    Without it the manager cannot tell a first run from a second one, because it decides that
    by looking for the database exactly as an operator who cleared the cache would.
    """

    def create(self, db: Path, languages: list[str], local: bool = True) -> None:
        """Record the creation and make the ``.und`` directory the real command makes."""
        super().create(db, languages, local)
        db.mkdir(parents=True, exist_ok=True)


@dataclass
class StubExtractor(SnapshotExtractor):
    """Answers each extraction from a scripted queue and records the target it was given.

    The queue is exact. An extra extraction raises rather than repeating the last answer, so a
    pipeline that reads a side more often than the design says fails here instead of quietly
    costing a worker run per commit.
    """

    answers: dict[Side, list[ProjectSnapshot]] = field(default_factory=dict)
    targets: list[SnapshotTarget] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialise the base, so an unoverridden member answers rather than raises."""
        SnapshotExtractor.__init__(self, FixtureApiRunner(Path("unused")), default_settings())

    def extract(self, target: SnapshotTarget) -> ProjectSnapshot:
        """The next scripted snapshot for ``target``'s side."""
        self.targets.append(target)
        queue = self.answers.get(target.side, [])
        if not queue:
            raise AssertionError(f"the pipeline extracted the {target.side} side once too often")
        return queue.pop(0)

    def sides(self) -> list[Side]:
        """Which sides were extracted, in order."""
        return [target.side for target in self.targets]

    def requested(self, side: Side, pass_: int) -> set[str]:
        """The file set of one side's ``pass_``-th extraction (0-based)."""
        matching = [target for target in self.targets if target.side == side]
        return set(matching[pass_].files)

    def rings(self, side: Side, pass_: int) -> int:
        """How many dependency steps one side's ``pass_``-th extraction recorded past its files."""
        matching = [target for target in self.targets if target.side == side]
        return matching[pass_].rings


@dataclass
class StubCodeCheck(CodeCheckRunner):
    """A CodeCheck runner that returns scripted rows and records the list it was handed."""

    rows: list[RawViolation] = field(default_factory=list)
    calls: list[tuple[Path, str, tuple[str, ...]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialise the base; nothing behind this object runs a process."""
        CodeCheckRunner.__init__(self, FakeUndCli())

    def run(
        self, db_path: Path, config: str, files: list[str], out_dir: Path
    ) -> list[RawViolation]:
        """Record the request and answer with the scripted violations."""
        self.calls.append((db_path, config, tuple(files)))
        return list(self.rows)


# --- snapshots ------------------------------------------------------------------


def fixture_snapshot(side: Side) -> ProjectSnapshot:
    """One of the committed sample snapshots, freshly parsed so tests cannot share state."""
    raw = json.loads((FIXTURES / f"snapshot_{side}.json").read_text(encoding="utf-8"))
    return ProjectSnapshot.model_validate(raw)


SAFE_POPULATIONS: Mapping[str, Mapping[str, list[float]]] = {
    "project": {
        "CyclomaticStrict": [2.0],
        "CountLineCode": [10.0],
        "MaxCyclomaticStrict": [3.0],
        "MaxNesting": [1.0],
    }
}
"""Project vectors comfortably inside every shipped project threshold.

A snapshot with no project populations is not neutral: the shipped project thresholds would
report a reducer failure instead, so a purpose-built snapshot has to say what the project
looks like rather than stay silent about it.
"""


def routine(path: str, longname: str, **metrics: float) -> dict[str, Any]:
    """One routine record in the wire shape ``ProjectSnapshot`` validates."""
    name = longname.rsplit(".", 1)[-1]
    return _record("routine", path, longname, "Python Function", name, metrics)


def source_file(path: str, **metrics: float) -> dict[str, Any]:
    """One file record; a file's long name is its repo-relative path (live finding, 6.2)."""
    return _record("file", path, path, "Python File", path.rsplit("/", 1)[-1], metrics)


def _record(
    scope: str, path: str, longname: str, kind: str, name: str, metrics: Mapping[str, float]
) -> dict[str, Any]:
    """The wire form of one entity record."""
    return {
        "ref": {
            "key": {"scope": scope, "path": path, "longname": longname, "parameters": None},
            "kind": kind,
            "name": name,
            "line": 1,
        },
        "language": "Python",
        "metrics": dict(metrics),
        "archs": [],
    }


def edge(src: str, dst: str, refs: int = 1) -> dict[str, Any]:
    """One dependency edge in the wire shape."""
    return {"src": src, "dst": dst, "refs": refs, "crosses_arch": False}


def built(
    side: Side,
    entities: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]] = (),
    nodes: Sequence[Mapping[str, Any]] = (),
    arch_edges: Sequence[Mapping[str, Any]] = (),
) -> ProjectSnapshot:
    """A purpose-built snapshot whose project populations break no shipped threshold."""
    return ProjectSnapshot.model_validate(
        {
            "side": side,
            "languages": ["Python"],
            "entities": list(entities),
            "file_edges": list(edges),
            "arch_nodes": list(nodes) or [{"path": "Directory Structure/src", "members": []}],
            "arch_edges": list(arch_edges),
            "populations": {scope: dict(v) for scope, v in SAFE_POPULATIONS.items()},
        }
    )


# --- the harness ----------------------------------------------------------------


@dataclass(frozen=True)
class Harness:
    """One repository, its cache, a fake ``und``, a stub extractor and the pipeline over them."""

    builder: GitRepoBuilder
    repo: GitRepo
    paths: CachePaths
    und: UndStub
    extractor: StubExtractor
    codecheck: StubCodeCheck | None
    store: BaselineStore
    progress: FakeProgress
    pipeline: CheckPipeline

    def run(self, mode: str = "staged", files: Sequence[str] = ()) -> RunResult:
        """Run the pipeline over one selection."""
        return self.pipeline.run(Selection(mode=mode, files=list(files)))

    @property
    def analyzed_sides(self) -> list[str]:
        """Which databases were analysed, read from the ``und`` commands that actually ran."""
        seen: list[str] = []
        for call in self.und.calls:
            if call.command != "analyze":
                continue
            db = call.arguments["db"]
            seen.append("before" if db == self.paths.before_db else "after")
        return seen

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
    analyses: Sequence[AnalyzeResult] = (),
    codecheck: StubCodeCheck | None = None,
    availability: AvailabilityReport | None = None,
) -> Harness:
    """Bind a pipeline to ``builder``'s repository with its cache under ``tmp_path``."""
    effective = settings if settings is not None else default_settings()
    repo = GitRepo.discover(builder.path, FakeCommandLog())
    paths = CachePaths.for_repo(repo.common_dir, effective.understand.db_location, tmp_path / "c")
    und = UndStub(version_text=BUILD, analyze_results=list(analyses))
    progress = FakeProgress()
    manager = DatabaseManager(
        paths, und, ShadowSync(repo, paths, effective.project), effective, progress
    )
    extractor = StubExtractor(
        answers={side: list(queue) for side, queue in (answers or {}).items()}
    )
    store = BaselineStore(tmp_path / "baseline.json")
    context = RunContext(
        settings=effective,
        provenance=Provenance(),
        availability=availability
        if availability is not None
        else validate_settings(effective, None),
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
        repo=repo,
        paths=paths,
        und=und,
        extractor=extractor,
        codecheck=codecheck,
        store=store,
        progress=progress,
        pipeline=CheckPipeline(context, manager, extractor, codecheck, store),
    )


def sample_repository(git_repo: MakeGitRepo, name: str = "repo") -> GitRepoBuilder:
    """A repository holding the five paths the sample snapshots describe, plus a README."""
    builder = git_repo(name)
    for path in SAMPLE_FILES:
        builder.write(path, f"# {path}\n")
    builder.write("README.md", "# sample\n")
    builder.stage(*SAMPLE_FILES, "README.md")
    builder.commit("initial")
    return builder


def dropped_availability(settings: Settings) -> AvailabilityReport:
    """The availability report a Python repository really gets: one shipped default dropped.

    ``class.PercentLackOfCohesion`` is a real C++/Java metric Python has no value for, so task
    2.4 drops it from the evaluated set and reports it as unavailable instead. Reproducing that
    here is what makes the ``unavailable_metrics`` assertions test the trap note 2.4 records:
    the evaluator filters the catalogue down to the metrics its own specs name, so a drop that
    is only passed through ``catalogue_unavailable`` disappears from the report.
    """
    kept = tuple(spec for spec in settings.thresholds if spec.rule != f"class.{DROPPED}")
    dropped = tuple(spec for spec in settings.thresholds if spec.rule == f"class.{DROPPED}")
    return AvailabilityReport(thresholds=kept, dropped=dropped, unavailable={"Python": (DROPPED,)})


def subjects(findings: Sequence[Finding]) -> list[tuple[str, str, str]]:
    """Each finding as ``(kind, rule, subject)``, the subject being the entity or the path."""
    return [
        (
            finding.kind,
            finding.rule,
            finding.entity.key.longname if finding.entity is not None else finding.path,
        )
        for finding in findings
    ]


@pytest.fixture
def staged_harness(git_repo: MakeGitRepo, tmp_path: Path) -> Harness:
    """The sample repository with two files staged, the change the fixture snapshots describe."""
    builder = sample_repository(git_repo)
    builder.write("src/cli/app.py", "# changed\n")
    builder.write("src/analysis/rules.py", "# changed\n")
    builder.stage("src/cli/app.py", "src/analysis/rules.py")
    return make_harness(
        builder,
        tmp_path,
        answers={
            "after": [fixture_snapshot("after"), fixture_snapshot("after")],
            "before": [fixture_snapshot("before"), fixture_snapshot("before")],
        },
        analyses=[
            AnalyzeResult(
                parse_errors=[
                    ParseError(path=Path("src/analysis/rules.py"), line=41, message="bad token")
                ],
                seconds=0.0,
            ),
            AnalyzeResult(
                parse_errors=[ParseError(path=Path("src/util/text.py"), line=7, message="old")],
                seconds=0.0,
            ),
        ],
        availability=dropped_availability(default_settings()),
    )


# --- a staged run over the sample fixtures --------------------------------------

EXPECTED_STAGED: tuple[tuple[str, str, str], ...] = (
    ("parse", "analysis.parse_error", "src/analysis/rules.py"),
    ("threshold", "routine.CyclomaticStrict", "app.build_parser"),
    ("threshold", "routine.CyclomaticModified", "app.build_parser"),
    ("threshold", "routine.MaxNesting", "app.build_parser"),
    ("threshold", "routine.CountLineCode", "app.build_parser"),
    ("threshold", "routine.CountStmt", "app.build_parser"),
    ("threshold", "file.MaxCyclomaticStrict", "src/cli/app.py"),
    ("threshold", "project.AVG:CyclomaticStrict", ""),
    ("threshold", "project.AVG:CountLineCode", ""),
    ("ratchet", "routine.CyclomaticStrict", "app.build_parser"),
    ("ratchet", "routine.CyclomaticModified", "app.build_parser"),
    ("ratchet", "routine.Essential", "app.build_parser"),
    ("ratchet", "routine.MaxNesting", "app.build_parser"),
    ("ratchet", "routine.CountLineCode", "app.build_parser"),
    ("ratchet", "routine.CountStmt", "app.build_parser"),
    ("ratchet", "routine.CountPath", "app.build_parser"),
    ("ratchet", "file.MaxCyclomaticStrict", "src/cli/app.py"),
    ("ratchet", "file.RatioCommentToCode", "src/analysis/rules.py"),
    ("ratchet", "file.RatioCommentToCode", "src/cli/app.py"),
    ("structural", "structure.file_cycle", "src/analysis/engine.py"),
    ("ratchet", "structure.fan_out", "src/analysis/rules.py"),
    ("ratchet", "structure.fan_out", "src/cli/app.py"),
)
"""Every finding the sample change produces under the shipped defaults, in evaluator order.

Derived from the two committed snapshots by hand and checked against the requirements: the
routine ``app.build_parser`` doubles in size and complexity, ``src/cli/app.py`` gains a
dependency on ``src/analysis/rules.py`` which closes a cycle with ``src/analysis/engine.py``,
and both changed files lose comment ratio. Nothing else moved, so nothing else is reported.

Both files also grew (``CountLineCode`` 90 -> 96 and 120 -> 160) and neither is reported for
it: ``file.CountLineCode`` ships without a ratchet, because that count is what goes up when a
file's contents are split (task 11.9). ``routine.CountLineCode`` on ``app.build_parser`` is
still here -- the routine grew *and* got more complex, which is a regression and not a
decomposition.

The ``analysis.parse_error`` entry leads the list because the fixture's after-side analysis
reports a parse error in ``src/analysis/rules.py``, which is one of the two staged files
(task 11.11): a file in the selection that Understand could not read is a finding of its own,
and it is produced before any rule because it says the rules below cover less than they
appear to. The before side's error, in ``src/util/text.py``, produces none -- it is a file
this change did not touch, and a before-side error is history rather than a verdict on the
commit.
"""


def test_a_staged_run_reports_exactly_the_findings_the_change_causes(
    staged_harness: Harness,
) -> None:
    """The whole finding set of a staged run, in the order the evaluators are specified in."""
    result = staged_harness.run()
    assert tuple(subjects(result.findings)) == EXPECTED_STAGED


def test_every_finding_of_a_staged_run_carries_a_hint(staged_harness: Harness) -> None:
    """Requirement 7.2 over the whole set: the evaluators leave ``hint`` empty on purpose."""
    result = staged_harness.run()
    assert result.findings, "the sample change must produce findings for this to test anything"
    assert [finding.rule for finding in result.findings if not finding.hint] == []


def test_a_staged_run_counts_blocking_warning_and_preexisting_findings(
    staged_harness: Harness,
) -> None:
    """The counts the exit code and both summaries are derived from (req 7.9).

    Two findings have moved from the first count to the second across two tasks, and both are
    named here because the moves are the point.

    Task 11.14 moved ``routine.Essential``: it rose on ``src/cli/app.py`` between the two
    fixture sides, so the *ratchet* still fires and the finding is still reported -- it no
    longer blocks. Demoting a threshold's severity must not switch its ratchet off, and a
    count that only went down would not have shown the difference.

    Task 11.15 moved ``routine.CountPath``, which is the defect this fixture had been
    recording without anyone reading it that way: 12 -> 40 against a maximum of **100**, a
    routine well inside its limit, counted as a blocking error. It is now a warning, and the
    six findings still blocking on this change are the ones that broke a limit.
    """
    result = staged_harness.run()
    warnings = [finding.rule for finding in result.findings if finding.severity == "warning"]
    assert result.blocking_count == 16
    assert result.warning_count == len(warnings) == 6
    assert "routine.Essential" in warnings
    assert "routine.CountPath" in warnings
    assert result.preexisting_count == 0


def test_no_finding_blocks_a_change_that_left_its_entity_inside_its_limit(
    staged_harness: Harness,
) -> None:
    """The property behind the counts above, asserted over every finding rather than one.

    Task 11.15's rule in one line: a value that has not broken its own limit does not refuse
    a commit. ``routine.CountPath`` at 40 of 100 is the finding this fixture used to fail on;
    the assertion is written over the whole result so that the next rule to grow a ratchet
    cannot reintroduce the freeze without failing here.
    """
    result = staged_harness.run()

    inside = [
        (finding.rule, finding.before, finding.value, finding.limit)
        for finding in result.findings
        if finding.blocking and within_limit(finding)
    ]

    assert inside == []
    assert ("routine.CountPath", 12.0, 40.0, 100.0) in [
        (finding.rule, finding.before, finding.value, finding.limit)
        for finding in result.findings
        if within_limit(finding)
    ]


def test_the_before_side_is_synced_from_the_resolved_head_hash_never_from_the_word_head(
    staged_harness: Harness,
) -> None:
    """A symbolic revision names a different commit tomorrow and full-syncs every run (8.1)."""
    staged_harness.run()
    state = json.loads(staged_harness.paths.state.read_text(encoding="utf-8"))
    assert state["before_commit"] == staged_harness.repo.head()
    assert state["before_commit"] != "HEAD"


def test_extraction_is_rooted_at_the_shadow_tree_object_the_database_was_built_from(
    staged_harness: Harness,
) -> None:
    """Handoff from 8.1: the root must be the path ``und add`` received, unresolved."""
    staged_harness.run()
    roots = {target.side: target.root for target in staged_harness.extractor.targets}
    assert roots["after"] == staged_harness.paths.after_tree
    assert roots["before"] == staged_harness.paths.before_tree
    added = [call.arguments["root"] for call in staged_harness.und.calls if call.command == "add"]
    assert added == [staged_harness.paths.after_tree, staged_harness.paths.before_tree]


def test_both_sides_parse_errors_reach_the_result_and_the_after_snapshot(
    staged_harness: Harness,
) -> None:
    """Requirement 2.6 over both databases: a before-side error is a coverage loss too."""
    result = staged_harness.run()
    assert [error.message for error in result.parse_errors] == ["bad token", "old"]
    first_after = staged_harness.extractor.targets[0]
    assert [error.message for error in first_after.parse_errors] == ["bad token"]


# --- a selected file that could not be read (req 2.6, task 11.11) ---------------

PEP695 = "def generic[T](x: T) -> T:\n    return x\n"
"""One declaration Understand 6.5.1204 cannot parse, measured: ``expected token '(' at token [``."""

UNREADABLE_MESSAGE = "expected token '(' at token ["
"""What ``und analyze`` really printed for :data:`PEP695`, quoted rather than invented."""


def unreadable_harness(
    git_repo: MakeGitRepo,
    tmp_path: Path,
    after: Sequence[str] = (),
    before: Sequence[str] = (),
    content: str = "# changed\n",
    name: str = "repo",
) -> Harness:
    """The staged sample change, with the named files failing to parse on the named side.

    ``after`` and ``before`` are repository-relative names, made absolute against the
    harness's **own** shadow trees once it exists -- which is what a real ``und`` reports
    (measured: ``<cache>/<repo id>/after/pkg/generic.py``) and what the relativisation in the
    database manager exists for. A name that is already absolute is passed through unchanged,
    which is how a standard-library error outside the repository is written.

    ``content`` is what the changed file holds in the shadow, because the pipeline reads that
    line to name the construct the parse stopped at.
    """
    builder = sample_repository(git_repo, name)
    builder.write("src/cli/app.py", "# changed\n")
    builder.write("src/analysis/rules.py", content)
    builder.stage("src/cli/app.py", "src/analysis/rules.py")
    harness = make_harness(
        builder,
        tmp_path,
        answers={
            "after": [fixture_snapshot("after"), fixture_snapshot("after")],
            "before": [fixture_snapshot("before"), fixture_snapshot("before")],
        },
        availability=dropped_availability(default_settings()),
    )
    harness.und.analyze_results.extend(
        [
            AnalyzeResult(
                parse_errors=[_unreadable(harness, "after", n) for n in after], seconds=0.0
            ),
            AnalyzeResult(
                parse_errors=[_unreadable(harness, "before", n) for n in before], seconds=0.0
            ),
        ]
    )
    return harness


def _unreadable(harness: Harness, side: Side, name: str) -> ParseError:
    """One parse error as ``und`` spells it: absolute, inside that side's shadow."""
    tree = harness.paths.after_tree if side == "after" else harness.paths.before_tree
    named = Path(name)
    return ParseError(
        path=named if named.is_absolute() else tree / name,
        line=1,
        message=UNREADABLE_MESSAGE,
    )


def parse_findings(result: RunResult) -> list[Finding]:
    """Every finding the run raised about a file it could not read."""
    return [finding for finding in result.findings if finding.rule == "analysis.parse_error"]


def test_a_selected_file_that_could_not_be_read_blocks_the_commit(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Task 11.11: an entity that is not in the database breaks no rule, so the file must.

    Measured on this repository before the fix: one PEP 695 declaration took
    ``config/models.py`` from 15 classes to 3 in the database -- 12 findings hidden, 2
    fabricated -- and the staged run exited 0 with ``blocking_count`` 0. A gate that certifies
    a file it never read has the one failure mode a gate must not have.
    """
    harness = unreadable_harness(git_repo, tmp_path, after=["src/analysis/rules.py"])

    result = harness.run()

    found = parse_findings(result)
    assert [(finding.path, finding.blocking, finding.severity) for finding in found] == [
        ("src/analysis/rules.py", True, "error")
    ]
    assert UNREADABLE_MESSAGE in found[0].message
    assert found[0].hint, "requirement 7.2: every finding says what to do about it"


def test_a_repository_file_outside_the_selection_is_reported_and_does_not_block(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A repository file this change did not touch: news about coverage, not a verdict on it.

    The count is compared against the same run with nothing unparsed rather than asserted as a
    bare "no finding": an absent finding proves nothing when the search itself is broken, and
    the neighbouring test shows the same machinery producing one.
    """
    clean = unreadable_harness(git_repo, tmp_path / "a", name="clean").run()
    harness = unreadable_harness(
        git_repo, tmp_path / "b", after=["src/util/text.py"], name="unreadable"
    )

    result = harness.run()

    assert [error.path.as_posix() for error in result.parse_errors] == ["src/util/text.py"]
    assert parse_findings(result) == []
    assert result.blocking_count == clean.blocking_count


def test_a_parse_error_outside_the_repository_is_not_reported_at_all(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Task 10.4 measured four of these on a clean run, all in the interpreter's own stdlib.

    A different input from the test above rather than a different name for it: that one is a
    file of this repository that the change did not touch, this one is a file no commit of this
    repository can reach at all. The first must be reported and must not block; the second is
    **dropped**. Measured on one real run of a 770-file project: 63 parse errors under the
    interpreter's own ``typing.py``, ``pdb.py`` and ``_pyrepl``, none of which anyone can act
    on. Non-blocking was not enough, because they still printed.
    """
    stdlib = "/usr/lib/python3.12/inspect.py"
    harness = unreadable_harness(git_repo, tmp_path, after=[stdlib])

    result = harness.run()

    assert result.parse_errors == []
    assert parse_findings(result) == []


def test_dropping_the_stdlib_noise_keeps_a_selected_file_blocking(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The negative control: the signal must survive beside the noise it is filtered from.

    One error in the interpreter's standard library and one in a staged file of this
    repository, in the same analysis. Only the second reaches the report, and it still blocks
    -- which is requirement 2.6's whole point: the analysis stops where the parse stops, so a
    rule that reported success over that file would be reporting success over code nobody read.
    """
    harness = unreadable_harness(
        git_repo,
        tmp_path,
        after=["/usr/lib/python3.12/inspect.py", "src/analysis/rules.py"],
    )

    result = harness.run()

    assert [error.path.as_posix() for error in result.parse_errors] == ["src/analysis/rules.py"]
    blocking = [finding for finding in parse_findings(result) if finding.blocking]
    assert [finding.path for finding in blocking] == ["src/analysis/rules.py"]


def test_a_before_side_parse_error_does_not_block_the_commit_that_fixed_it(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The same staged file as the blocking case, failing on the other side: it must not block.

    A parse error on the before side is history: the likeliest reason the after side parses and
    the before side does not is that this very commit is the rewrite the hint asked for.
    Blocking it would make the Gate refuse its own remedy. It is still reported, because the
    comparison between the two sides is worth less than it looks (which is what the ratchet
    reads out of ``ProjectSnapshot.unparsed_files``).
    """
    harness = unreadable_harness(git_repo, tmp_path, before=["src/analysis/rules.py"])

    result = harness.run()

    assert [error.path.as_posix() for error in result.parse_errors] == ["src/analysis/rules.py"]
    assert parse_findings(result) == []


def test_the_hint_names_the_construct_the_parse_stopped_at(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 7.2 for a parse error: the remedy is a rewrite, and which one depends."""
    harness = unreadable_harness(
        git_repo, tmp_path, after=["src/analysis/rules.py"], content=PEP695
    )

    result = harness.run()

    finding = parse_findings(result)[0]
    assert finding.details["construct"] == "type_params"
    assert "TypeVar" in finding.hint


def test_an_unrecognised_construct_falls_back_to_the_rule_level_hint(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The same file, a line naming no construct: still actionable, just not specific."""
    harness = unreadable_harness(git_repo, tmp_path, after=["src/analysis/rules.py"])

    result = harness.run()

    finding = parse_findings(result)[0]
    assert finding.details["construct"] == ""
    assert "TypeVar" not in finding.hint
    assert finding.hint


def test_a_dropped_default_threshold_is_still_reported_as_unavailable(
    staged_harness: Harness,
) -> None:
    """Note 2.4's trap: the evaluator filters the catalogue down to the metrics it was given."""
    result = staged_harness.run()
    assert result.unavailable_metrics == {"Python": [DROPPED]}
    assert [spec.rule for spec in result.effective_thresholds if DROPPED in spec.metric] == []


def test_a_staged_run_records_the_highest_values_and_the_files_it_looked_at(
    staged_harness: Harness,
) -> None:
    """Requirement 5.6's highest values, and a file count the summary can quote."""
    result = staged_harness.run()
    highest = {(item.scope, item.metric): item.value for item in result.highest}
    assert highest[("file", "CountLineCode")] == 160
    assert highest[("routine", "CountLineCode")] == 75
    assert result.analyzed_files == len(SAMPLE_FILES)


def test_a_staged_run_stamps_the_run_metadata_from_the_context(staged_harness: Harness) -> None:
    """One clock, read once by the context, so every record of the run agrees (note 8.2)."""
    result = staged_harness.run()
    assert result.started_at == STARTED_AT
    assert result.selection == "staged"
    assert result.repo_root == str(staged_harness.repo.root)
    assert result.understand_version == staged_harness.pipeline.ctx.understand.version


# --- whole-project mode ---------------------------------------------------------

EXPECTED_ALL: tuple[tuple[str, str, str], ...] = (
    ("threshold", "routine.CyclomaticStrict", "app.build_parser"),
    ("threshold", "routine.CyclomaticModified", "app.build_parser"),
    ("threshold", "routine.MaxNesting", "app.build_parser"),
    ("threshold", "routine.CountLineCode", "app.build_parser"),
    ("threshold", "routine.CountStmt", "app.build_parser"),
    ("threshold", "file.MaxCyclomaticStrict", "src/cli/app.py"),
    ("threshold", "project.AVG:CyclomaticStrict", ""),
    ("threshold", "project.AVG:CountLineCode", ""),
    ("structural", "structure.file_cycle", "src/analysis/engine.py"),
)
"""Whole-project mode over the after snapshot: absolute limits and a cycle inventory (4.8)."""


@pytest.fixture
def all_harness(git_repo: MakeGitRepo, tmp_path: Path) -> Harness:
    """The sample repository with nothing staged, checked as a whole project."""
    builder = sample_repository(git_repo)
    return make_harness(
        builder,
        tmp_path,
        answers={"after": [fixture_snapshot("after")]},
        availability=dropped_availability(default_settings()),
    )


def test_a_whole_project_run_reports_no_ratchet_findings(all_harness: Harness) -> None:
    """Requirement 4.8: without a before side there is nothing to have got worse."""
    result = all_harness.run("all")
    assert tuple(subjects(result.findings)) == EXPECTED_ALL
    assert [f.rule for f in result.findings if f.kind == "ratchet"] == []


def test_a_whole_project_run_builds_no_before_side_at_all(all_harness: Harness) -> None:
    """The before database costs a sync and an analysis; ``--all`` never compares (4.8)."""
    all_harness.run("all")
    assert all_harness.analyzed_sides == ["after"]
    assert all_harness.extractor.sides() == ["after"]
    assert not all_harness.paths.before_db.exists()


def test_every_finding_of_a_whole_project_run_carries_a_hint(all_harness: Harness) -> None:
    """Requirement 7.2 does not depend on the selection mode."""
    result = all_harness.run("all")
    assert result.findings
    assert [finding.rule for finding in result.findings if not finding.hint] == []


# --- requirement 4.9: four different ways to analyse nothing --------------------


def test_an_empty_staged_set_exits_clean_without_touching_understand(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 4.9, and the 8.1 handoff: a repository with nothing to analyse raises."""
    harness = make_harness(sample_repository(git_repo), tmp_path)
    result = harness.run()
    assert result.findings == []
    assert result.blocking_count == 0
    assert result.analyzed_files == 0
    assert harness.und.calls == []
    assert harness.extractor.targets == []


def test_a_staged_set_of_files_no_language_covers_exits_clean(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Staging only a README is not a change Understand can parse (req 4.9)."""
    builder = sample_repository(git_repo)
    builder.write("README.md", "# edited\n")
    builder.stage("README.md")
    harness = make_harness(builder, tmp_path)
    result = harness.run()
    assert result.findings == []
    assert harness.und.calls == []


def test_a_staged_change_of_a_language_the_configuration_excludes_exits_clean(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A repository pinned to C++ must not analyse a Python-only commit (req 2.4, 4.9)."""
    builder = sample_repository(git_repo)
    builder.write("src/cli/app.py", "# changed\n")
    builder.stage("src/cli/app.py")
    settings = default_settings()
    settings.project.languages = ["C++"]
    harness = make_harness(builder, tmp_path, settings)
    assert harness.run().findings == []
    assert harness.und.calls == []


def test_a_repository_with_no_commit_yet_skips_the_before_side_instead_of_crashing(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """An unborn ``HEAD`` has no before side, so every entity is new and nothing ratchets (4.5)."""
    builder = git_repo()
    builder.write("src/cli/app.py", "# new\n")
    builder.stage("src/cli/app.py")
    harness = make_harness(
        builder,
        tmp_path,
        answers={"after": [fixture_snapshot("after"), fixture_snapshot("after")]},
    )
    result = harness.run()
    assert harness.analyzed_sides == ["after"]
    assert [finding.rule for finding in result.findings if finding.kind == "ratchet"] == []
    assert all(finding.before is None for finding in result.findings)


def test_a_whole_project_run_with_nothing_analysable_exits_clean(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The same short circuit protects ``--all``: no language means no database to create."""
    builder = git_repo()
    builder.write("README.md", "# docs\n")
    builder.stage("README.md")
    builder.commit("docs only")
    harness = make_harness(builder, tmp_path)
    assert harness.run("all").findings == []
    assert harness.und.calls == []


# --- requirement 4.10: a deletions-only change ----------------------------------

SURVIVORS = ("src/a.py", "src/b.py")
GONE = "src/c.py"


@pytest.fixture
def deletion_harness(git_repo: MakeGitRepo, tmp_path: Path) -> Harness:
    """Three files, a cycle between the two survivors, and the third staged for deletion.

    The cycle exists on both sides on purpose. A pipeline that forgot to hand the before edges
    to the cycle rule would report it as new, so this fixture makes that mistake visible
    instead of leaving the deletions-only case asserting the absence of everything.
    """
    builder = git_repo()
    for path in (*SURVIVORS, GONE):
        builder.write(path, f"# {path}\n")
    builder.stage(*SURVIVORS, GONE)
    builder.commit("initial")
    builder.delete(GONE)
    before = built(
        "before",
        [source_file(path, CountLineCode=10) for path in (*SURVIVORS, GONE)],
        [
            edge("src/a.py", "src/b.py"),
            edge("src/b.py", "src/a.py"),
            edge("src/a.py", GONE),
            edge("src/b.py", GONE),
        ],
    )
    after = built(
        "after",
        [source_file(path, CountLineCode=10) for path in SURVIVORS],
        [edge("src/a.py", "src/b.py"), edge("src/b.py", "src/a.py")],
    )
    return make_harness(
        builder,
        tmp_path,
        answers={"after": [after, after], "before": [before, before]},
    )


def test_a_deletions_only_change_reports_nothing_about_the_file_that_is_gone(
    deletion_harness: Harness,
) -> None:
    """Requirement 4.10: the structural rules run on the survivors and find nothing new."""
    result = deletion_harness.run()
    assert result.findings == []
    assert result.blocking_count == 0


def test_a_deletions_only_change_still_evaluates_the_files_that_remain(
    deletion_harness: Harness,
) -> None:
    """The former dependents are the neighbourhood, which the one extraction's rings record."""
    deletion_harness.run()
    assert deletion_harness.extractor.requested("before", 0) == {GONE}
    assert deletion_harness.extractor.rings("before", 0) == 2
    assert deletion_harness.extractor.sides() == ["after", "before"]


# --- evaluator order, classification and severities -----------------------------


@dataclass(frozen=True)
class OrderCase:
    """The two snapshots and the violation that make one run produce all four finding kinds."""

    before: ProjectSnapshot
    after: ProjectSnapshot
    violation: RawViolation


def order_case(tree: Path) -> OrderCase:
    """A change that breaks a limit, gets worse, closes a cycle and trips CodeCheck at once."""
    before = built(
        "before",
        [
            source_file("src/a.py"),
            source_file("src/b.py"),
            routine("src/a.py", "a.f", CyclomaticStrict=5),
        ],
        [edge("src/a.py", "src/b.py")],
    )
    after = built(
        "after",
        [
            source_file("src/a.py"),
            source_file("src/b.py"),
            routine("src/a.py", "a.f", CyclomaticStrict=12),
        ],
        [edge("src/a.py", "src/b.py"), edge("src/b.py", "src/a.py")],
    )
    violation = RawViolation(
        check_id="CPP_C001",
        check_name="Naming",
        path=str(tree / "src/a.py"),
        line=3,
        message="name it better",
    )
    return OrderCase(before=before, after=after, violation=violation)


def test_the_evaluators_run_in_the_specified_order(git_repo: MakeGitRepo, tmp_path: Path) -> None:
    """Thresholds, then ratchet, then structure, then CodeCheck -- classification depends on it."""
    builder = git_repo()
    for path in SURVIVORS:
        builder.write(path, "# x\n")
    builder.stage(*SURVIVORS)
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.stage("src/a.py")
    settings = default_settings()
    settings.codecheck = CodeCheckSettings(config="Sandbox", severity="warning")
    repo = GitRepo.discover(builder.path, FakeCommandLog())
    paths = CachePaths.for_repo(repo.common_dir, settings.understand.db_location, tmp_path / "c")
    case = order_case(paths.after_tree)
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={"after": [case.after, case.after], "before": [case.before, case.before]},
        codecheck=StubCodeCheck(rows=[case.violation]),
    )
    result = harness.run()
    assert [(finding.kind, finding.rule) for finding in result.findings] == [
        ("threshold", "routine.CyclomaticStrict"),
        ("ratchet", "routine.CyclomaticStrict"),
        ("structural", "structure.file_cycle"),
        ("ratchet", "structure.fan_out"),
        ("codecheck", "codecheck.CPP_C001"),
    ]
    assert all(finding.hint for finding in result.findings)


def test_codecheck_findings_name_the_repository_relative_path(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The CSV names shadow paths; a finding must name the file the operator edited (req 7.1)."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.stage("src/a.py")
    settings = default_settings()
    settings.codecheck = CodeCheckSettings(config="Sandbox", severity="error")
    repo = GitRepo.discover(builder.path, FakeCommandLog())
    paths = CachePaths.for_repo(repo.common_dir, settings.understand.db_location, tmp_path / "c")
    case = order_case(paths.after_tree)
    stub = StubCodeCheck(rows=[case.violation])
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={"after": [case.after, case.after], "before": [case.before, case.before]},
        codecheck=stub,
    )
    result = harness.run()
    found = [finding for finding in result.findings if finding.kind == "codecheck"]
    assert [finding.path for finding in found] == ["src/a.py"]
    assert found[0].severity == "error"
    # Requirement 6.9 says "the staged files". `src/b.py` gained a dependency, so it is
    # affected (req 4.2) and other rules do report it -- but it was not edited, so running
    # CodeCheck over it would report its standing violations as though this change caused them.
    assert stub.calls[0][2] == (str(paths.after_tree / "src/a.py"),)
    assert "src/b.py" in {finding.path for finding in result.findings}


def test_a_threshold_that_was_already_broken_and_did_not_get_worse_is_preexisting(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 4.6, and the proof that ``attach_before`` runs before ``classify``."""
    result = _preexisting_run(git_repo, tmp_path, strict=False)
    threshold = [f for f in result.findings if f.kind == "threshold"]
    assert [(f.preexisting, f.blocking, f.before) for f in threshold] == [(True, False, 12.0)]
    assert result.blocking_count == 0


def test_strict_mode_makes_a_preexisting_violation_block(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 4.7: the same finding, the same run, one setting apart."""
    result = _preexisting_run(git_repo, tmp_path, strict=True)
    threshold = [f for f in result.findings if f.kind == "threshold"]
    assert [(f.preexisting, f.blocking) for f in threshold] == [(True, True)]
    assert result.blocking_count == 1


def _preexisting_run(git_repo: MakeGitRepo, tmp_path: Path, strict: bool) -> RunResult:
    """One staged run over a routine that was over the limit before the change and still is."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.stage("src/a.py")
    settings = default_settings()
    settings.ratchet = RatchetSettings(strict=strict)
    entities = [routine("src/a.py", "a.f", CyclomaticStrict=12)]
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={
            "after": [built("after", entities), built("after", entities)],
            "before": [built("before", entities), built("before", entities)],
        },
    )
    return harness.run()


def one_new_edge(
    builder: GitRepoBuilder, tmp_path: Path, settings: Settings
) -> list[tuple[str, str, bool]]:
    """``src/a.py`` gaining its first dependency, and the fan findings that draws.

    One staged change, two graphs: the before side has no edges at all and the after side has
    ``src/a.py -> src/b.py``, so fan-out goes 0 -> 1 and every fan finding in the answer is
    about that one edge.
    """
    for path in SURVIVORS:
        builder.write(path, "# x\n")
    builder.stage(*SURVIVORS)
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.stage("src/a.py")
    before = built("before", [source_file("src/a.py"), source_file("src/b.py")], [])
    after = built(
        "after",
        [source_file("src/a.py"), source_file("src/b.py")],
        [edge("src/a.py", "src/b.py")],
    )
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={"after": [after, after], "before": [before, before]},
    )
    result = harness.run()
    return sorted(
        (finding.kind, finding.severity, finding.blocking)
        for finding in result.findings
        if finding.rule == "structure.fan_out"
    )


def test_the_fan_severity_setting_reaches_the_fan_findings(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Note 4.4: ``evaluate_fan`` carries no severity, so the pipeline must project it.

    The limit is pinned at zero so the single new edge breaks it, which is what makes both
    fan findings visible: the structural one for the absolute limit and the ratchet one for
    the growth. Since task 11.15 the ratchet finding is capped at
    ``ratchet.below_limit_severity`` while the entity is *inside* its limit, so a case under
    the shipped maximum of 20 would show a warning here whatever ``fan_severity`` said, and
    the projection this test exists for would be invisible.
    """
    settings = default_settings()
    settings.structure.fan_severity = "error"
    settings.structure.fan = {**settings.structure.fan, "file_fan_out": Limit(max=0)}

    fan = one_new_edge(git_repo(), tmp_path, settings)

    assert fan == [("ratchet", "error", True), ("structural", "error", True)]


def test_one_new_dependency_inside_the_fan_limit_does_not_block(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Task 11.15 reaches the fan ratchet too, because it is the same refusal (req 6.4).

    ``evaluate_fan`` builds a ``kind="ratchet"`` finding for fan-out growth, so "a file you
    touched may not gain a dependency" is the freeze this task is about, one rule along: with
    ``fan_severity = "error"`` a file importing its *first* module against a maximum of 20
    refused the commit. The severity is capped instead, and there is no structural finding at
    all -- one edge is not over the limit -- so this is a run with nothing blocking in it.
    """
    settings = default_settings()
    settings.structure.fan_severity = "error"

    fan = one_new_edge(git_repo(), tmp_path, settings)

    assert fan == [("ratchet", "warning", False)]


def test_the_fan_ratchet_refuses_again_under_below_limit_severity_error(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """One configuration key brings the refusal above back, which is what says it was real."""
    settings = default_settings()
    settings.structure.fan_severity = "error"
    settings.ratchet.below_limit_severity = "error"

    fan = one_new_edge(git_repo(), tmp_path, settings)

    assert fan == [("ratchet", "error", True)]


def test_the_fan_rules_see_the_neighbourhood_as_well_as_the_change(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Note 4.6's hazard: requirement 4.10's "remaining affected files" are the neighbourhood.

    ``src/b.py`` is nobody's staged file -- it is a dependency of the one that was staged -- and
    its fan-in is what breaks the limit. A pipeline that handed ``evaluate_fan`` only the
    affected files would report nothing here, and would evaluate nothing at all after a
    deletion, where the affected file set is empty by construction.
    """
    builder = git_repo()
    for path in ("src/a.py", "src/b.py", "src/c.py"):
        builder.write(path, "# x\n")
    builder.stage("src/a.py", "src/b.py", "src/c.py")
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.stage("src/a.py")
    settings = default_settings()
    settings.structure.fan = {"file_fan_in": Limit(max=1)}
    entities = [source_file(path) for path in ("src/a.py", "src/b.py", "src/c.py")]
    edges = [edge("src/a.py", "src/b.py"), edge("src/c.py", "src/b.py")]
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={
            "after": [built("after", entities, edges), built("after", entities, edges)],
            "before": [built("before", entities, edges), built("before", entities, edges)],
        },
    )
    result = harness.run()
    fan = [(finding.rule, finding.path) for finding in result.findings]
    assert fan == [("structure.fan_in", "src/b.py")]


LAYERS = ("Directory Structure/src/cli", "Directory Structure/src/core")
"""Two architecture nodes, so a layer rule and a coupling rule have something to name."""


def architecture(edges: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The nodes and node edges both node-level rules read (req 6.2, 6.3, 6.6, 6.7)."""
    return {
        "nodes": [
            {"path": LAYERS[0], "members": ["src/cli/app.py"]},
            {"path": LAYERS[1], "members": ["src/core/engine.py"]},
        ],
        "arch_edges": list(edges),
    }


def test_layer_and_coupling_rules_are_wired_into_the_run(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirements 6.3 and 6.6: both rules are configuration-only, so nothing else exercises them.

    Each carries its own severity, which is why the pipeline must not fold them into the
    severity map: one map entry per rule *name* would give every layer rule the same severity.
    """
    builder = git_repo()
    for path in ("src/cli/app.py", "src/core/engine.py"):
        builder.write(path, "# x\n")
    builder.stage("src/cli/app.py", "src/core/engine.py")
    builder.commit("initial")
    builder.write("src/cli/app.py", "# changed\n")
    builder.stage("src/cli/app.py")
    settings = default_settings()
    settings.structure.layers = [
        LayerRule(name="cli-may-not-reach-core", node=LAYERS[0], may_depend_on=[], severity="error")
    ]
    settings.structure.coupling = [
        CouplingRule(from_node=LAYERS[0], to_node=LAYERS[1], max_refs=0, severity="warning")
    ]
    entities = [source_file("src/cli/app.py"), source_file("src/core/engine.py")]
    arch = architecture([edge(LAYERS[0], LAYERS[1], refs=2)])
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={
            "after": [
                built("after", entities, [edge("src/cli/app.py", "src/core/engine.py")], **arch),
                built("after", entities, [edge("src/cli/app.py", "src/core/engine.py")], **arch),
            ],
            "before": [
                built("before", entities, [], **architecture()),
                built("before", entities, [], **architecture()),
            ],
        },
    )
    result = harness.run()
    assert [(f.rule, f.severity, f.blocking) for f in result.findings] == [
        ("structure.layer", "error", True),
        ("structure.fan_out", "warning", False),
        ("structure.coupling", "warning", False),
    ]
    assert all(finding.hint for finding in result.findings)


# --- the adaptive baseline ------------------------------------------------------


def test_a_baseline_narrows_the_limit_and_the_finding_says_so(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirements 8.2 and 8.5: the effective limit is the lower of the two, and it is named."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.stage("src/a.py")
    settings = default_settings()
    settings.baseline = BaselineSettings(file=Path("baseline.json"), adaptive=True)
    entities = [routine("src/a.py", "a.f", CyclomaticStrict=6)]
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={
            "after": [built("after", entities), built("after", entities)],
            "before": [built("before", entities), built("before", entities)],
        },
    )
    harness.store.save(Baseline(captured_at=STARTED_AT, values={"routine.CyclomaticStrict": 5}))
    result = harness.run()
    threshold = [f for f in result.findings if f.rule == "routine.CyclomaticStrict"]
    assert [(f.limit, f.limit_source) for f in threshold] == [(5.0, "baseline")]
    # `RunResult.effective_thresholds` is the limits that actually applied, not the configured
    # ones: the JSON document is what an agent reads the rules out of, and a report quoting 10
    # here while the run enforced 5 would send it to fix the wrong number.
    reported = {spec.rule: spec.limit.max for spec in result.effective_thresholds}
    assert reported["routine.CyclomaticStrict"] == 5.0


def test_a_whole_project_run_in_adaptive_mode_lowers_the_baseline_and_reports_it(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 8.3: the run beat the recorded maximum, so the recorded maximum moves down."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    settings = default_settings()
    settings.baseline = BaselineSettings(file=Path("baseline.json"), adaptive=True)
    entities = [routine("src/a.py", "a.f", CyclomaticStrict=4)]
    harness = make_harness(
        builder, tmp_path, settings, answers={"after": [built("after", entities)]}
    )
    harness.store.save(Baseline(captured_at=STARTED_AT, values={"routine.CyclomaticStrict": 9}))
    result = harness.run("all")
    assert [(item.rule, item.previous, item.current) for item in result.tightened] == [
        ("routine.CyclomaticStrict", 9.0, 4.0)
    ]
    stored, issues = harness.store.load(list(settings.thresholds))
    assert issues == [] and stored is not None
    assert stored.values["routine.CyclomaticStrict"] == 4.0


def test_a_staged_run_never_tightens_the_baseline_from_its_partial_view(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A change touching one simple file is not evidence about the project's maximum (4.5)."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.stage("src/a.py")
    settings = default_settings()
    settings.baseline = BaselineSettings(file=Path("baseline.json"), adaptive=True)
    entities = [routine("src/a.py", "a.f", CyclomaticStrict=1)]
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={
            "after": [built("after", entities), built("after", entities)],
            "before": [built("before", entities), built("before", entities)],
        },
    )
    harness.store.save(Baseline(captured_at=STARTED_AT, values={"routine.CyclomaticStrict": 9}))
    result = harness.run()
    assert result.tightened == []
    stored, _ = harness.store.load(list(settings.thresholds))
    assert stored is not None and stored.values["routine.CyclomaticStrict"] == 9.0
    assert any("whole project" in note for note in harness.notes)


# --- the remaining selection modes ----------------------------------------------


def test_worktree_mode_sees_an_unstaged_edit_and_still_compares_against_head(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 10.5: an agent checks its edits before staging them."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    builder.unstaged_edit("src/a.py", "# edited but not staged\n")
    entities = [routine("src/a.py", "a.f", CyclomaticStrict=12)]
    harness = make_harness(
        builder,
        tmp_path,
        answers={
            "after": [built("after", entities), built("after", entities)],
            "before": [built("before", entities), built("before", entities)],
        },
    )
    result = harness.run("worktree")
    assert harness.analyzed_sides == ["after", "before"]
    assert [f.rule for f in result.findings] == ["routine.CyclomaticStrict"]
    assert result.selection == "worktree"


def test_worktree_mode_syncs_the_after_shadow_from_the_working_tree(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The unstaged bytes must reach the shadow, or the mode checks the index after all."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    builder.unstaged_edit("src/a.py", "# edited but not staged\n")
    entities = [routine("src/a.py", "a.f", CyclomaticStrict=1)]
    harness = make_harness(
        builder,
        tmp_path,
        answers={
            "after": [built("after", entities), built("after", entities)],
            "before": [built("before", entities), built("before", entities)],
        },
    )
    harness.run("worktree")
    shadowed = (harness.paths.after_tree / "src/a.py").read_text(encoding="utf-8")
    assert shadowed == "# edited but not staged\n"


def test_files_mode_evaluates_only_the_given_files_and_still_resolves_head(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 11.8: the pre-commit framework hands over the staged list itself."""
    builder = git_repo()
    for path in SURVIVORS:
        builder.write(path, "# x\n")
    builder.stage(*SURVIVORS)
    builder.commit("initial")
    entities = [routine("src/a.py", "a.f", CyclomaticStrict=12)]
    harness = make_harness(
        builder,
        tmp_path,
        answers={
            "after": [built("after", entities), built("after", entities)],
            "before": [built("before", entities), built("before", entities)],
        },
    )
    result = harness.run("files", ["src/a.py"])
    assert harness.extractor.requested("after", 0) == {"src/a.py"}
    assert harness.analyzed_sides == ["after", "before"]
    assert result.selection == "files: src/a.py"


def test_files_mode_refuses_a_path_outside_the_repository(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A path that names nothing inside the tree would evaluate nothing and report success."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    harness = make_harness(builder, tmp_path)
    with pytest.raises(ConfigError) as refused:
        harness.run("files", [str(tmp_path / "elsewhere" / "other.py")])
    assert "outside the repository" in refused.value.message


def test_an_absolute_path_inside_the_repository_is_made_relative(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The same file named two ways must reach the same entity keys, not zero of them."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    entities = [routine("src/a.py", "a.f", CyclomaticStrict=12)]
    harness = make_harness(
        builder,
        tmp_path,
        answers={
            "after": [built("after", entities), built("after", entities)],
            "before": [built("before", entities), built("before", entities)],
        },
    )
    result = harness.run("files", [str(builder.path / "src" / "a.py")])
    assert harness.extractor.requested("after", 0) == {"src/a.py"}
    assert [f.rule for f in result.findings] == ["routine.CyclomaticStrict"]


# --- diagnostics ----------------------------------------------------------------


def test_a_population_that_cannot_be_reduced_is_reported_on_the_diagnostics_channel(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Note 4.1: ``reducer_failures`` has no ``RunResult`` field, so 8.3 surfaces it here."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.stage("src/a.py")
    empty = ProjectSnapshot.model_validate(
        {"side": "after", "languages": ["Python"], "entities": [], "populations": {}}
    )
    before = ProjectSnapshot.model_validate(
        {"side": "before", "languages": ["Python"], "entities": [], "populations": {}}
    )
    harness = make_harness(
        builder,
        tmp_path,
        answers={"after": [empty, empty], "before": [before, before]},
    )
    harness.run()
    assert any("project.AVG:CyclomaticStrict" in note for note in harness.notes)


def test_an_unusable_file_name_is_excluded_from_the_codecheck_list_and_reported(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Handoff from 6.7: ``und`` cannot be asked about such a name, so it must be left out."""
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.write("src/we#ird.py", "# x\n")
    builder.stage("src/a.py", "src/we#ird.py")
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.write("src/we#ird.py", "# changed\n")
    builder.stage("src/a.py", "src/we#ird.py")
    settings = default_settings()
    settings.codecheck = CodeCheckSettings(config="Sandbox", severity="warning")
    entities = [source_file("src/a.py"), source_file("src/we#ird.py")]
    stub = StubCodeCheck(rows=[])
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={
            "after": [built("after", entities), built("after", entities)],
            "before": [built("before", entities), built("before", entities)],
        },
        codecheck=stub,
    )
    harness.run()
    listed = stub.calls[0][2]
    assert listed == (str(harness.paths.after_tree / "src/a.py"),)
    assert any("we#ird.py" in note for note in harness.notes)


def test_ignored_entities_are_excluded_from_the_thresholds(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 3.6: the ignore list reaches the evaluator, not just the extractor.

    The snapshot here still holds the ignored routine, which the **real** worker would already
    have dropped (note 6.2 records that requirement 3.6's count is therefore unreachable in
    production and needs a spec decision). That is exactly what makes this test able to fail:
    it asks whether the pipeline forwards ``settings.ignore``, and a pipeline that did not
    would report the finding. It does not claim the count is reachable through a real ``und``.
    """
    builder = git_repo()
    builder.write("src/a.py", "# x\n")
    builder.stage("src/a.py")
    builder.commit("initial")
    builder.write("src/a.py", "# changed\n")
    builder.stage("src/a.py")
    settings = default_settings()
    settings.ignore = IgnoreRules(routines=[r"^a\.f$"])
    entities = [routine("src/a.py", "a.f", CyclomaticStrict=12)]
    harness = make_harness(
        builder,
        tmp_path,
        settings,
        answers={
            "after": [built("after", entities), built("after", entities)],
            "before": [built("before", entities), built("before", entities)],
        },
    )
    result = harness.run()
    assert [f.rule for f in result.findings] == []
    assert result.ignored_counts == {"routine": 1}


def test_progress_is_reported_for_the_extraction_phases(staged_harness: Harness) -> None:
    """Requirement 4.11: every phase is announced so a slow one can be named."""
    staged_harness.run()
    assert "reading the after snapshot" in staged_harness.progress.started
    assert "reading the before snapshot" in staged_harness.progress.started
