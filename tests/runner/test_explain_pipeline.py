"""The explain pipeline end to end: git, shadows, databases, summary, graphs, impact (8.4).

Everything below the pipeline is real except Understand itself. Each test drives a **real**
``git`` repository, a **real** :class:`~scitools_hook.git.shadow.ShadowSync` and a **real**
:class:`~scitools_hook.understand.database.DatabaseManager`, and stands in only for the three
things that need a licence: ``und``, the snapshot extractor, and the API worker that draws
graphs and expands impact.

The graph stand-in **writes real files**. That is the point rather than a nicety: the obvious
false green here is a summary that names SVG files nobody ever created, and a runner scripted
with a list of paths would pass that test forever. So the double behaves as the worker does --
it creates one file per target in the directory it was handed -- and the assertions read the
directory rather than the answer.

The properties pinned here are the ones requirement 9 turns on:

* **A commit range is a different input shape from a selection**, and it is where the
  resolved-hash rule bites. Four *different* inputs are exercised, not one test renamed: an
  ordinary two-commit range, a range whose ends are the same commit, a range against an unborn
  ``HEAD``, and a range naming symbolic revisions. A fifth covers the option-injection shape
  the implementation notes record as the most serious defect found on this project.
* **The operator-named graph directory is settled before any analysis starts** (the 9.1
  ``--output`` lesson), so a wrong path costs a second rather than a full Understand run.
* **Nothing analysable is an empty summary, not exit 5.** ``ensure_side`` raises for a
  repository with no analysable file, so a README-only change or a null range would report a
  broken tool.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeCommandLog, FakeProgress, GitRepoBuilder, MakeGitRepo
from fakes.api import FakeApiRunner, FakeRun
from fakes.runner import ScriptedExtractor, UndStub, scripted
from fixtures import CLI_NODE
from fixtures.constants import BUILD, STARTED_AT

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.models import OutputSettings, Provenance, Settings
from scitools_hook.config.validate import validate_settings
from scitools_hook.errors import ConfigError
from scitools_hook.git.repo import GitRepo
from scitools_hook.git.shadow import ShadowSync
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.change import ChangeSummary
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot, Side
from scitools_hook.report.markdown import Format, render_summary
from scitools_hook.runner.context import RunContext
from scitools_hook.runner.explain import ExplainOptions, ExplainPipeline
from scitools_hook.runner.pipeline import (
    OBJECT_ID,
    CommitRange,
    Selection,
    resolve_commit,
)
from scitools_hook.understand.api_runner import Operation
from scitools_hook.understand.database import DatabaseManager
from scitools_hook.understand.fake import FixtureUndCli, fixture_env

SVG = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"
"""What the graph double writes; the pipeline never reads it, the assertions only stat it."""

UTIL_NODE = "Directory Structure/src/util"
"""Two architecture nodes, so a dependency between them crosses a boundary (req 9.2)."""


# --- stand-ins for the three things that need a licence -------------------------


@dataclass
class WorkerStub(FakeApiRunner):
    """The API worker as ``explain`` uses it: it draws graphs and expands impact.

    ``graphs`` writes one file per target into the directory the request names, exactly as the
    real worker does, and answers with the paths it wrote. ``refuse`` names the graph kinds
    Understand will not render for a target -- measured live, a routine draws ``Butterfly``
    and refuses ``Depends On`` -- which the real worker reports as a warning and no file.

    ``impact`` answers for every key it was asked about and for no other, so a test cannot
    assert on an impact set the pipeline never requested.
    """

    refuse: frozenset[str] = frozenset()

    def run(self, op: Operation, request: Mapping[str, object]) -> dict[str, object]:
        """Answer ``graphs`` and ``impact`` from the request; anything else from the script."""
        if op not in {"graphs", "impact"}:
            return super().run(op, request)
        self.calls.append(FakeRun(op, dict(request)))
        return self._draw(request) if op == "graphs" else self._expand(request)

    def _draw(self, request: Mapping[str, object]) -> dict[str, object]:
        """Write one SVG per target the way the worker does, and name what it wrote."""
        out_dir = Path(str(request["out_dir"]))
        targets = list(_sequence(request["targets"]))
        written: list[dict[str, object]] = []
        warnings: list[str] = []
        for index, target in enumerate(targets):
            entry = dict(_mapping(target))
            graph = str(entry["graph"])
            if graph in self.refuse:
                warnings.append(f"Understand will not draw {graph} for target {index}")
                continue
            drawn = out_dir / f"{index:02d}-{graph.replace(' ', '-')}.svg"
            drawn.write_text(SVG, encoding="utf-8")
            written.append({"key": entry["key"], "graph": graph, "path": str(drawn)})
        return {"graphs": written, "warnings": warnings}

    def _expand(self, request: Mapping[str, object]) -> dict[str, object]:
        """One impact set per requested key, so the answer cannot outrun the question."""
        sets: dict[str, object] = {}
        for raw in _sequence(request["keys"]):
            key = EntityKey.model_validate(dict(_mapping(raw)))
            sets[key.token] = {
                "by_depth": {"1": [_ref("routine", "src/caller.py", "caller.calls")]},
                "total": 1,
            }
        return {"impact": sets, "warnings": []}

    def requested_keys(self, op: str) -> list[EntityKey]:
        """The entity keys one operation was asked about, in the order they were sent."""
        request = self.request_for(op)
        raw = request["targets"] if op == "graphs" else request["keys"]
        entries = [dict(_mapping(item)) for item in _sequence(raw)]
        keys = [entry["key"] if op == "graphs" else entry for entry in entries]
        return [EntityKey.model_validate(dict(_mapping(key))) for key in keys]

    def requested_graphs(self) -> list[str]:
        """The graph kinds that were asked for, in order."""
        request = self.request_for("graphs")
        return [str(dict(_mapping(item))["graph"]) for item in _sequence(request["targets"])]


def _sequence(value: object) -> Sequence[object]:
    """A request field the double knows to be a list, narrowed for the type checker."""
    assert isinstance(value, Sequence) and not isinstance(value, str), value
    return value


def _mapping(value: object) -> Mapping[str, object]:
    """A request field the double knows to be an object, narrowed for the type checker."""
    assert isinstance(value, Mapping), value
    return value


# --- snapshots ------------------------------------------------------------------


def _ref(scope: str, path: str, longname: str) -> dict[str, Any]:
    """One entity reference in the wire shape the models validate."""
    return {
        "key": {"scope": scope, "path": path, "longname": longname, "parameters": None},
        "kind": f"Python {scope.title()}",
        "name": longname.rsplit(".", 1)[-1],
        "line": 1,
    }


def _record(scope: str, path: str, longname: str, **metrics: float) -> dict[str, Any]:
    """One entity record in the wire shape ``ProjectSnapshot`` validates."""
    return {
        "ref": _ref(scope, path, longname),
        "language": "Python",
        "metrics": dict(metrics),
        "archs": [],
    }


def routine(path: str, longname: str, **metrics: float) -> dict[str, Any]:
    """One routine record."""
    return _record("routine", path, longname, **metrics)


def source_file(path: str, **metrics: float) -> dict[str, Any]:
    """One file record; a file's long name is its repo-relative path (live finding, 6.2)."""
    return _record("file", path, path, **metrics)


NODES: Sequence[Mapping[str, Any]] = (
    {"path": CLI_NODE, "members": ["src/app.py"]},
    {"path": UTIL_NODE, "members": ["src/util.py"]},
)
"""The architecture both sides report, so every entity has a path (req 9.7)."""


def built(
    side: Side,
    entities: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]] = (),
) -> ProjectSnapshot:
    """A purpose-built snapshot of the two-file sample project."""
    return ProjectSnapshot.model_validate(
        {
            "side": side,
            "languages": ["Python"],
            "entities": list(entities),
            "file_edges": [dict(edge) for edge in edges],
            "arch_nodes": [dict(node) for node in NODES],
            "arch_edges": [],
            "populations": {"project": {"CountLineCode": [50.0]}},
        }
    )


def edge(src: str, dst: str) -> Mapping[str, Any]:
    """One dependency edge in the wire shape."""
    return {"src": src, "dst": dst, "refs": 1, "crosses_arch": True}


BEFORE = built(
    "before",
    [
        source_file("src/app.py", CountLineCode=12),
        routine("src/app.py", "app.run", CyclomaticStrict=2, CountLineCode=8),
        source_file("src/util.py", CountLineCode=10),
        routine("src/util.py", "util.helper", CyclomaticStrict=1, CountLineCode=4),
    ],
)
"""The project before the change: ``app.py`` is small and depends on nothing."""

AFTER = built(
    "after",
    [
        source_file("src/app.py", CountLineCode=40),
        routine("src/app.py", "app.run", CyclomaticStrict=6, CountLineCode=28),
        source_file("src/util.py", CountLineCode=10),
        routine("src/util.py", "util.helper", CyclomaticStrict=1, CountLineCode=4),
    ],
    edges=[edge("src/app.py", "src/util.py")],
)
"""The project after it: ``app.run`` grew and ``app.py`` gained a dependency on ``util.py``."""

APP_FILE = EntityKey(scope="file", path="src/app.py", longname="src/app.py")
APP_RUN = EntityKey(scope="routine", path="src/app.py", longname="app.run")
"""The two entities of the changed file, which are the affected keys of every staged run."""


def both_sides() -> Mapping[Side, Sequence[ProjectSnapshot]]:
    """The four extractions a run with a before side makes, two per side."""
    return {"after": [AFTER, AFTER], "before": [BEFORE, BEFORE]}


# --- the harness ----------------------------------------------------------------


@dataclass(frozen=True)
class Harness:
    """One repository, its cache, the three doubles and the pipeline over them."""

    builder: GitRepoBuilder
    repo: GitRepo
    paths: CachePaths
    und: UndStub
    extractor: ScriptedExtractor
    api: WorkerStub
    progress: FakeProgress
    pipeline: ExplainPipeline

    def run(
        self, selection: Selection | CommitRange, options: ExplainOptions | None = None
    ) -> ChangeSummary:
        """Run the pipeline over one selection or range."""
        return self.pipeline.run(selection, options)

    def state(self) -> SyncState:
        """What ``state.json`` records after the run (req 2.3)."""
        return SyncState.model_validate_json(self.paths.state.read_text(encoding="utf-8"))

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
    api: WorkerStub | None = None,
) -> Harness:
    """Bind an explain pipeline to ``builder``'s repository with its cache under ``tmp_path``."""
    effective = settings if settings is not None else default_settings()
    repo = GitRepo.discover(builder.path, FakeCommandLog())
    paths = CachePaths.for_repo(repo.common_dir, effective.understand.db_location, tmp_path / "c")
    und = UndStub(version_text=BUILD)
    progress = FakeProgress()
    manager = DatabaseManager(
        paths, und, ShadowSync(repo, paths, effective.project), effective, progress
    )
    extractor = scripted(answers if answers is not None else both_sides())
    worker = api if api is not None else WorkerStub()
    context = RunContext(
        settings=effective,
        provenance=Provenance(),
        availability=validate_settings(effective, None),
        understand=fixture_env(tmp_path / "fixtures"),
        und=FixtureUndCli(tmp_path / "fixtures"),
        api=worker,
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
        api=worker,
        progress=progress,
        pipeline=ExplainPipeline(context, manager, extractor),
    )


def project(builder: GitRepoBuilder) -> GitRepoBuilder:
    """The two-file sample project, committed once."""
    builder.write("src/app.py", "def run():\n    return 1\n")
    builder.write("src/util.py", "def helper():\n    return 2\n")
    builder.write("README.md", "# sample\n")
    builder.stage()
    builder.commit("initial")
    return builder


def staged_edit(builder: GitRepoBuilder) -> GitRepoBuilder:
    """A staged change to ``src/app.py`` only."""
    builder.write("src/app.py", "import util\n\n\ndef run():\n    return util.helper()\n")
    builder.stage("src/app.py")
    return builder


@pytest.fixture
def staged(git_repo: MakeGitRepo, tmp_path: Path) -> Harness:
    """A repository with one committed project and one staged edit to ``src/app.py``."""
    return make_harness(staged_edit(project(git_repo())), tmp_path)


# --- the change summary (req 9.1, 9.2, 9.7, 9.8) --------------------------------


def test_a_staged_change_is_summarised_per_file_with_metrics_before_and_after(
    staged: Harness,
) -> None:
    """Requirement 9.1: per affected file, what moved, with both sides and the delta."""
    summary = staged.run(Selection(mode="staged"))

    assert staged.extractor.sides == ["after", "before"]
    assert set(summary.files) == {"src/app.py"}
    moved = {delta.ref.key.longname: delta for delta in summary.files["src/app.py"]}
    assert moved["app.run"].status == "modified"
    assert moved["app.run"].before["CyclomaticStrict"] == 2
    assert moved["app.run"].after["CyclomaticStrict"] == 6
    assert moved["app.run"].delta["CyclomaticStrict"] == 4


def test_one_extraction_per_side_records_the_neighbourhood_through_its_rings(
    staged: Harness,
) -> None:
    """Requirement 4.11's bound is unchanged; requirement 8.3 pays for it once instead of twice.

    The request still names the change. What used to be a second whole-project walk for the
    change plus its ring is now the ring count on the first one, and the document is narrowed
    in process to exactly the set that walk would have recorded.
    """
    staged.run(Selection(mode="staged"))

    assert staged.extractor.requested("after", 0) == {"src/app.py"}
    assert staged.extractor.rings("after", 0) == 2


def test_a_dependency_across_an_architecture_boundary_is_reported_as_crossing(
    staged: Harness,
) -> None:
    """Requirement 9.2: dependency deltas carry both nodes and mark a crossed boundary."""
    summary = staged.run(Selection(mode="staged"))

    assert [(dep.src, dep.dst, dep.status) for dep in summary.dependencies] == [
        ("src/app.py", "src/util.py", "added")
    ]
    crossing = summary.dependencies[0]
    assert (crossing.src_node, crossing.dst_node) == (CLI_NODE, UTIL_NODE)
    assert crossing.crosses_arch is True


def test_every_ranked_entity_carries_the_architecture_path_of_its_file(staged: Harness) -> None:
    """Requirement 9.7: a reviewer can find the entity in the Understand GUI."""
    summary = staged.run(Selection(mode="staged"))

    assert summary.top_by_delta, "the change moved metrics, so the ranking cannot be empty"
    assert {delta.arch_path for delta in summary.top_by_delta} == {CLI_NODE}


def test_the_summary_names_the_database_and_the_command_that_opens_it(staged: Harness) -> None:
    """Requirement 9.8: the summary ends with the command that opens the database."""
    summary = staged.run(Selection(mode="staged"))

    assert summary.db_path == str(staged.paths.after_db)
    assert summary.open_command.startswith("understand ")
    assert str(staged.paths.after_db) in summary.open_command


def test_a_whole_project_run_has_no_before_side_and_reports_every_entity_as_added(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 4.8: ``--all`` builds no before side, so the summary is an inventory."""
    harness = make_harness(project(git_repo()), tmp_path, answers={"after": [AFTER]})

    summary = harness.run(Selection(mode="all"))

    assert harness.extractor.sides == ["after"]
    assert harness.analyzed_sides == ["after"]
    assert set(summary.files) == {"src/app.py", "src/util.py"}
    assert {delta.status for group in summary.files.values() for delta in group} == {"added"}


def test_a_deleted_file_is_listed_with_its_entities_removed(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 9.1 asks the summary to list what a change removed, so the path must survive.

    A deletion is the input that proves the plan keeps paths git reports as gone: the file is
    not in the after snapshot at all, so if the planner dropped it there would be nothing to
    analyse, the run would short-circuit, and the reviewer would be told the change was empty.
    """
    builder = project(git_repo())
    builder.delete("src/util.py")
    survivor = built("after", [source_file("src/app.py", CountLineCode=12)])
    harness = make_harness(
        builder, tmp_path, answers={"after": [survivor] * 2, "before": [BEFORE] * 2}
    )

    summary = harness.run(Selection(mode="staged"))

    assert "src/util.py" in summary.files
    removed = {delta.ref.key.longname: delta for delta in summary.files["src/util.py"]}
    assert removed["util.helper"].status == "removed"
    assert removed["util.helper"].before["CyclomaticStrict"] == 1
    assert removed["util.helper"].after == {}


def test_a_staged_rename_reports_both_ends_of_the_move(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The old path of a rename is a deleted file and the new one an addition (req 9.1)."""
    builder = project(git_repo())
    builder.rename("src/util.py", "src/helpers.py")
    moved = built(
        "after",
        [
            source_file("src/app.py", CountLineCode=12),
            source_file("src/helpers.py", CountLineCode=10),
            routine("src/helpers.py", "helpers.helper", CyclomaticStrict=1, CountLineCode=4),
        ],
    )
    harness = make_harness(
        builder, tmp_path, answers={"after": [moved] * 2, "before": [BEFORE] * 2}
    )

    summary = harness.run(Selection(mode="staged"))

    assert harness.extractor.requested("before", 0) == {"src/util.py", "src/helpers.py"}, (
        "both ends of a rename must reach the plan; the old one only exists on the before side"
    )
    assert {"src/util.py", "src/helpers.py"} <= set(summary.files)
    assert [d.status for d in summary.files["src/util.py"]] == ["removed", "removed"]
    assert {d.status for d in summary.files["src/helpers.py"]} == {"added"}


# --- requirement 4.9: nothing analysable is an empty summary ---------------------


def test_a_readme_only_change_produces_an_empty_summary_rather_than_a_failure(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``ensure_side`` would raise exit 5 here; requirement 4.9 says the answer is nothing."""
    builder = project(git_repo())
    builder.write("README.md", "# sample, revised\n")
    builder.stage("README.md")
    harness = make_harness(builder, tmp_path, answers={})

    summary = harness.run(Selection(mode="staged"))

    assert summary.files == {}
    assert summary.dependencies == []
    assert harness.extractor.sides == [], "nothing may be extracted when nothing is analysable"
    assert harness.und.commands == [], "no shadow may be synced and no database touched"
    assert summary.open_command.startswith("understand ")
    assert any("nothing in this change can be analyzed" in note for note in harness.notes)


# --- requirement 9.1: a commit range, four distinct inputs -----------------------


def two_commits(builder: GitRepoBuilder) -> tuple[str, str]:
    """Commit the project, then commit an edit to ``src/app.py``; answer with both hashes."""
    project(builder)
    base = builder.run("rev-parse", "HEAD")
    staged_edit(builder)
    return base, builder.commit("use the helper")


def test_a_two_commit_range_syncs_both_shadows_to_the_named_commits(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 9.1: both ends are commits, and the state records the after side as one."""
    builder = git_repo()
    base, head = two_commits(builder)
    harness = make_harness(builder, tmp_path)

    summary = harness.run(CommitRange(base=base, head=head))

    assert harness.analyzed_sides == ["after", "before"]
    state = harness.state()
    assert state.after_target == "commit"
    assert (state.after_tree_id, state.before_commit) == (head, base)
    assert set(summary.files) == {"src/app.py"}
    moved = {delta.ref.key.longname: delta for delta in summary.files["src/app.py"]}
    assert moved["app.run"].status == "modified", (
        "a range compares two commits; reporting everything as added means the base was lost"
    )
    assert moved["app.run"].delta["CyclomaticStrict"] == 4


def test_a_range_whose_ends_are_the_same_commit_analyses_nothing(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A null range has no change, so it must not become an analysis failure."""
    builder = git_repo()
    _, head = two_commits(builder)
    harness = make_harness(builder, tmp_path, answers={})

    summary = harness.run(CommitRange(base=head, head=head))

    assert summary.files == {}
    assert harness.und.commands == []
    assert harness.extractor.sides == []


def test_a_range_against_an_unborn_head_is_refused_as_a_configuration_error(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A repository with no commit has no ``HEAD~1``; the operator asked for the impossible."""
    harness = make_harness(git_repo(), tmp_path, answers={})

    with pytest.raises(ConfigError) as refused:
        harness.run(CommitRange(base="HEAD~1", head="HEAD"))

    assert refused.value.key == "range"
    assert "HEAD~1" in str(refused.value)
    assert harness.und.commands == []


def test_a_symbolic_range_is_recorded_as_the_object_ids_it_resolved_to(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The recorded state must name commits, not the words that named them today (req 2.3)."""
    builder = git_repo()
    base, head = two_commits(builder)
    harness = make_harness(builder, tmp_path)

    harness.run(CommitRange(base="HEAD~1", head="HEAD"))

    state = harness.state()
    assert (state.after_tree_id, state.before_commit) == (head, base)
    assert OBJECT_ID.fullmatch(str(state.before_commit)), "a symbolic name is not a cache key"


def test_a_revision_that_names_an_option_is_refused_and_writes_nothing(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """An operator-supplied revision shaped like an option must refuse and write nothing.

    What this pins is the *outcome*, and the distinction is worth stating rather than
    implying: measured on git 2.43.0, ``rev-parse --verify --output=pwned.txt^{commit}``
    already exits 128 without the ``--end-of-options`` guard, because appending ``^{commit}``
    makes the token stop looking like an option. So this test would pass with the guard
    removed. The guard itself is pinned separately, on the argv
    (:func:`test_resolving_a_revision_is_recorded_for_the_verbose_log`), and it stays because
    the implementation notes make it the rule for *every* argv that interpolates a
    user-supplied ref -- ``git rev-parse --verify --git-path hooks^{commit}`` prints
    ``.git/hooks^{commit}`` when the two are separate arguments.
    """
    builder = git_repo()
    two_commits(builder)
    harness = make_harness(builder, tmp_path, answers={})

    with pytest.raises(ConfigError):
        harness.run(CommitRange(base="--output=pwned.txt", head="HEAD"))

    assert not (builder.path / "pwned.txt").exists()
    assert list(builder.path.glob("**/pwned.txt")) == []


def test_a_revision_that_names_a_tree_rather_than_a_commit_is_refused(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A shadow is exported from a commit; anything else is refused before the export."""
    builder = git_repo()
    two_commits(builder)
    tree = builder.run("rev-parse", "HEAD^{tree}")
    harness = make_harness(builder, tmp_path, answers={})

    with pytest.raises(ConfigError) as refused:
        harness.run(CommitRange(base=tree, head="HEAD"))

    assert tree in str(refused.value)


def test_resolve_commit_answers_a_full_object_id_for_a_revision_that_names_one(
    git_repo: MakeGitRepo,
) -> None:
    """The one green path of the resolver, so its refusals are not the only thing pinned."""
    builder = git_repo()
    _, head = two_commits(builder)
    repo = GitRepo.discover(builder.path, FakeCommandLog())

    assert resolve_commit(repo, "HEAD") == head
    assert resolve_commit(repo, head) == head


def test_an_annotated_tag_resolves_to_the_commit_it_points_at(git_repo: MakeGitRepo) -> None:
    """Measured: ``rev-parse v1`` answers the *tag object*, which is not what a shadow holds."""
    builder = git_repo()
    _, head = two_commits(builder)
    builder.run("tag", "-a", "v1", "-m", "release one")
    tag_object = builder.run("rev-parse", "v1")
    repo = GitRepo.discover(builder.path, FakeCommandLog())

    assert tag_object != head, "an annotated tag is its own object; that is the point here"
    assert resolve_commit(repo, "v1") == head


def test_a_git_that_answers_something_other_than_an_object_id_is_refused(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The answer is guarded, not only the status: only an object id is a usable cache key.

    Real ``git`` cannot produce this, which is exactly why it is driven by a stand-in: an
    unreachable guard is one nobody notices deleting, and ``ShadowSync`` silently falls back
    to a full re-sync for any id it cannot match, so a bad answer would cost correctness of
    the recorded state rather than a visible failure.
    """
    builder = project(git_repo())
    liar = tmp_path / "git-stub"
    liar.write_text("#!/bin/sh\necho refs/heads/main\n", encoding="utf-8")
    liar.chmod(0o755)
    repo = GitRepo.discover(builder.path, FakeCommandLog())

    with pytest.raises(ConfigError) as refused:
        resolve_commit(replace(repo, git=str(liar)), "HEAD")

    assert "refs/heads/main" in str(refused.value)


def test_resolving_a_revision_is_recorded_for_the_verbose_log(git_repo: MakeGitRepo) -> None:
    """Requirement 12.8: every external command the Gate runs is recorded, this one included."""
    builder = git_repo()
    two_commits(builder)
    log = FakeCommandLog()
    repo = GitRepo.discover(builder.path, log)
    log.calls.clear()

    resolve_commit(repo, "HEAD")

    assert [argv[3] for argv, _, _ in log.calls] == ["rev-parse"]
    assert "--end-of-options" in log.calls[0][0]


@pytest.mark.parametrize(
    "text",
    ["HEAD", "", "..", "a..", "..b", "a....b", "a..b..c"],
    ids=["no-separator", "empty", "bare", "no-head", "no-base", "four-dot", "three-ends"],
)
def test_the_range_grammar_refuses_everything_that_is_not_base_dot_dot_head(text: str) -> None:
    """Requirement 9.1 names one form; every other shape is refused rather than guessed at."""
    with pytest.raises(ConfigError) as refused:
        CommitRange.parse(text)

    assert refused.value.key == "range"


def test_the_range_grammar_reads_the_two_ends_it_was_given() -> None:
    """The one accepted form, including a revision that carries its own punctuation."""
    assert CommitRange.parse("HEAD~1..HEAD") == CommitRange(base="HEAD~1", head="HEAD")
    assert CommitRange.parse("v1.0..v2.0") == CommitRange(base="v1.0", head="v2.0")


# --- requirement 9.4: exported graphs -------------------------------------------


def test_requested_graphs_are_written_to_disk_and_referenced_by_the_summary(
    staged: Harness, tmp_path: Path
) -> None:
    """The summary must name files that exist: a reference to a file nobody wrote is a lie."""
    out_dir = tmp_path / "review"

    summary = staged.run(Selection(mode="staged"), ExplainOptions(graphs=True, out_dir=out_dir))

    assert "graphs" in staged.api.ops, "the exporter must actually have been reached"
    assert summary.graphs, "requirement 9.4 asks the summary to reference the exported files"
    for graph in summary.graphs:
        assert graph.path.is_file(), f"{graph.path} is referenced but was never written"
        assert graph.path.parent == out_dir
    assert sorted(graph.path for graph in summary.graphs) == sorted(out_dir.glob("*.svg"))


def test_a_butterfly_is_drawn_per_routine_and_a_depends_on_per_file(staged: Harness) -> None:
    """Requirement 9.4 names both pictures, and each has its own kind of target."""
    staged.run(Selection(mode="staged"), ExplainOptions(graphs=True))

    assert staged.api.requested_graphs() == ["Butterfly", "Depends On"]
    assert staged.api.requested_keys("graphs") == [APP_RUN, APP_FILE]


def test_graphs_default_to_the_directory_the_cache_owns(staged: Harness) -> None:
    """Requirement 2.2: without ``--out`` nothing is written inside the working tree."""
    summary = staged.run(Selection(mode="staged"), ExplainOptions(graphs=True))

    assert summary.graphs
    for graph in summary.graphs:
        assert graph.path.parent == staged.paths.graphs
        assert staged.builder.path not in graph.path.parents


def test_the_configured_maximum_caps_each_group_of_graph_targets(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Requirement 9.4's configurable count, applied so a whole-project run stays bounded."""
    entities = [
        source_file("src/app.py", CountLineCode=40),
        routine("src/app.py", "app.one", CyclomaticStrict=3),
        routine("src/app.py", "app.two", CyclomaticStrict=4),
        routine("src/app.py", "app.three", CyclomaticStrict=5),
    ]
    settings = default_settings().model_copy(update={"output": OutputSettings(graphs_max=2)})
    harness = make_harness(
        staged_edit(project(git_repo())),
        tmp_path,
        settings,
        answers={"after": [built("after", entities)] * 2, "before": [BEFORE] * 2},
    )

    harness.run(Selection(mode="staged"), ExplainOptions(graphs=True))

    assert harness.api.requested_graphs() == ["Butterfly", "Butterfly", "Depends On"]
    kept = [key.longname for key in harness.api.requested_keys("graphs")]
    assert kept == ["app.one", "app.three", "src/app.py"], "the cap must be deterministic"


def test_a_maximum_of_zero_asks_understand_for_no_graph_at_all(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Drawing nothing must cost no licence checkout and no entity walk."""
    settings = default_settings().model_copy(update={"output": OutputSettings(graphs_max=0)})
    harness = make_harness(staged_edit(project(git_repo())), tmp_path, settings)

    summary = harness.run(Selection(mode="staged"), ExplainOptions(graphs=True))

    assert summary.graphs == []
    assert "graphs" not in harness.api.ops


def test_a_graph_understand_refuses_is_reported_and_costs_no_other_graph(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Measured live: a routine draws ``Butterfly`` and refuses ``Depends On``."""
    harness = make_harness(
        staged_edit(project(git_repo())), tmp_path, api=WorkerStub(refuse=frozenset({"Depends On"}))
    )

    summary = harness.run(Selection(mode="staged"), ExplainOptions(graphs=True))

    assert [graph.graph for graph in summary.graphs] == ["Butterfly"]
    assert any("will not draw Depends On" in note for note in harness.notes)


def test_graphs_are_not_exported_when_they_were_not_asked_for(staged: Harness) -> None:
    """The default explain run pays for neither aid (design: graphs and impact only on ask)."""
    summary = staged.run(Selection(mode="staged"))

    assert summary.graphs == []
    assert staged.api.ops == []
    assert not staged.paths.graphs.exists()


# --- the operator-named graph directory ------------------------------------------


def test_a_graph_directory_that_is_a_regular_file_is_refused_before_any_analysis(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The 9.1 ``--output`` lesson: settle the kind of an operator-named destination first."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied", encoding="utf-8")
    harness = make_harness(staged_edit(project(git_repo())), tmp_path, answers={})

    with pytest.raises(ConfigError) as refused:
        harness.run(Selection(mode="staged"), ExplainOptions(graphs=True, out_dir=blocked))

    assert refused.value.key == "out"
    assert "is not a directory" in str(refused.value)
    assert harness.und.commands == [], "the refusal must come before any Understand work"
    assert blocked.read_text(encoding="utf-8") == "occupied"


def test_a_graph_directory_that_is_a_fifo_is_refused_rather_than_opened(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A FIFO does not raise on open, it blocks forever; the kind is settled by ``stat``."""
    fifo = tmp_path / "fifo"
    os_mkfifo(fifo)
    harness = make_harness(staged_edit(project(git_repo())), tmp_path, answers={})

    with pytest.raises(ConfigError) as refused:
        harness.run(Selection(mode="staged"), ExplainOptions(graphs=True, out_dir=fifo))

    assert "is not a directory" in str(refused.value)


def test_a_graph_directory_that_is_a_dangling_symlink_names_the_link(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """``Path.is_dir()`` says "no" for a link leading nowhere, which sends the operator away."""
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "missing", target_is_directory=True)
    harness = make_harness(staged_edit(project(git_repo())), tmp_path, answers={})

    with pytest.raises(ConfigError) as refused:
        harness.run(Selection(mode="staged"), ExplainOptions(graphs=True, out_dir=link))

    assert "symbolic link that leads nowhere" in str(refused.value)


def test_a_graph_directory_reached_through_a_symlink_is_accepted(
    staged: Harness, tmp_path: Path
) -> None:
    """A link to a real directory is a working configuration, not a hostile one.

    The same reading ``BaselineStore`` takes of a symlinked baseline file: pointing the review
    output at a share is an ordinary thing to do, and only links to something that cannot hold
    graphs are refused.
    """
    real = tmp_path / "share" / "review"
    real.mkdir(parents=True)
    link = tmp_path / "review-link"
    link.symlink_to(real, target_is_directory=True)

    summary = staged.run(Selection(mode="staged"), ExplainOptions(graphs=True, out_dir=link))

    assert summary.graphs
    assert sorted(path.name for path in real.iterdir()) == sorted(
        graph.path.name for graph in summary.graphs
    )


def test_a_graph_directory_is_created_when_it_does_not_exist_yet(
    staged: Harness, tmp_path: Path
) -> None:
    """``explain --out review/`` must not require the reviewer to make the directory first."""
    out_dir = tmp_path / "deep" / "review"

    staged.run(Selection(mode="staged"), ExplainOptions(graphs=True, out_dir=out_dir))

    assert out_dir.is_dir()


def os_mkfifo(path: Path) -> None:
    """Create a FIFO, skipping the test on a platform that has none."""
    import os

    if not hasattr(os, "mkfifo"):  # pragma: no cover - POSIX-only guard
        pytest.skip("this platform has no FIFOs")
    os.mkfifo(path)


# --- requirement 9.5: change impact ---------------------------------------------


def test_change_impact_is_expanded_for_every_affected_routine_and_reaches_the_summary(
    staged: Harness,
) -> None:
    """Requirement 9.5: the blast radius of each modified routine, with counts."""
    summary = staged.run(Selection(mode="staged"), ExplainOptions(impact=True))

    assert staged.api.requested_keys("impact") == [APP_RUN]
    assert set(summary.impact) == {APP_RUN}
    assert summary.impact[APP_RUN].total == 1


def test_impact_is_expanded_to_the_configured_depth(git_repo: MakeGitRepo, tmp_path: Path) -> None:
    """Requirement 9.5's configurable depth reaches the worker, not just the configuration."""
    settings = default_settings().model_copy(update={"output": OutputSettings(impact_depth=7)})
    harness = make_harness(staged_edit(project(git_repo())), tmp_path, settings)

    harness.run(Selection(mode="staged"), ExplainOptions(impact=True))

    assert harness.api.request_for("impact")["depth"] == 7


def test_impact_is_not_asked_for_when_it_was_not_requested(staged: Harness) -> None:
    """The blast radius is unbounded work; an explain run that did not ask must not pay."""
    summary = staged.run(Selection(mode="staged"), ExplainOptions(graphs=False, impact=False))

    assert summary.impact == {}
    assert "impact" not in staged.api.ops


def test_both_aids_can_be_asked_for_at_once(staged: Harness, tmp_path: Path) -> None:
    """The reviewer's documented invocation asks for graphs and impact together."""
    summary = staged.run(
        Selection(mode="staged"), ExplainOptions(graphs=True, impact=True, out_dir=tmp_path / "r")
    )

    assert sorted(staged.api.ops) == ["graphs", "impact"]
    assert summary.graphs and summary.impact


def test_the_progress_reporter_names_the_aid_phases(staged: Harness, tmp_path: Path) -> None:
    """Requirement 4.11: a slow phase has to be nameable, so each one announces itself."""
    staged.run(
        Selection(mode="staged"), ExplainOptions(graphs=True, impact=True, out_dir=tmp_path / "r")
    )

    started = staged.progress.started
    assert "exporting graphs" in started
    assert "expanding change impact" in started
    assert [name for name, _ in staged.progress.finished] == started


# --- the handoff to the CLI (task 9.2) -------------------------------------------


@pytest.mark.parametrize("fmt", ["text", "markdown", "json"])
def test_the_summary_a_run_produces_is_renderable_in_every_view(
    staged: Harness, tmp_path: Path, fmt: Format
) -> None:
    """Requirement 9.6, checked against a *pipeline-produced* summary rather than a fixture.

    The renderers are task 5.3's and are tested there on hand-built documents; what is pinned
    here is the seam task 9.2 will use -- that a summary this pipeline actually built, with
    both aids attached, goes through ``render_summary`` and carries the exported file names
    and the GUI command into the output.
    """
    summary = staged.run(
        Selection(mode="staged"), ExplainOptions(graphs=True, impact=True, out_dir=tmp_path / "r")
    )

    rendered = render_summary(summary, fmt)

    assert summary.open_command in rendered
    assert "app.run" in rendered
    assert summary.graphs[0].path.name in rendered
