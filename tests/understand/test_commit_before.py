"""The before database built from the base commit, and when that costs nothing (3.1, 3.5).

The saving requirement 3.5 asks for is not a faster analysis; it is **no analysis at all**.
A run whose key matches the recorded one reuses the database it finds and starts no process,
which is why the assertions below are as often about ``stub.calls == []`` as about argv.

The key is four things -- the commit, the languages, the analysis fingerprint and the
Understand build -- and each of them is varied on its own here, because a key that ignores one
of them reuses a database built under a different question. The route counts too: a
shadow-built ``before.und`` can sit at the same commit with the same languages and still hold
a different file set.

``und`` is the stubbed executable, so the argv is real and nothing licensed is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from und_stub import RecordingLog, UndStub, cli, write_stub

from scitools_hook.config.defaults import default_settings
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.git import CommitTarget, IndexTarget
from scitools_hook.understand.commit_before import (
    BeforeKey,
    CommitBuild,
    attempt_for,
    build,
    ensure,
    record,
    serve,
)

BUILD = "(Build 1262)"
COMMIT = "3ca0a97"
FINGERPRINT = "9f2c1d0e4b5a6c7d"

ACCURACY_OUTPUT = "25 of 92 parsed files had no errors or warnings (27%)\n"
"""What ``und analyze -accuracy`` prints; the wrapper reads ``25/92`` off it."""


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log."""
    return RecordingLog(entries=[])


def layout(tmp_path: Path) -> CachePaths:
    """The cache layout the database manager owns, with both databases in one directory."""
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


def a_key(**changed: object) -> BeforeKey:
    """The key of a run, with one component replaced."""
    fields: dict[str, object] = {
        "commit": COMMIT,
        "languages": ["Python"],
        "settings": FINGERPRINT,
        "build": BUILD,
    }
    fields.update(changed)
    return BeforeKey.of(**fields)  # type: ignore[arg-type]


def a_request(tmp_path: Path, key: BeforeKey | None = None, **extra: object) -> CommitBuild:
    """One request to make the before database be a commit."""
    return CommitBuild(paths=layout(tmp_path), repo=tmp_path / "repo", key=key or a_key(), **extra)


def recorded(tmp_path: Path, key: BeforeKey | None = None) -> SyncState:
    """A state describing a commit-built database of ``key``, with the database on disk."""
    (layout(tmp_path).before_db).mkdir(parents=True, exist_ok=True)
    settled = key or a_key()
    state = SyncState()
    record(state, settled, _nothing())
    return state


def _nothing():
    """An analysis that reported nothing, for seeding a state without running anything."""
    from scitools_hook.models.understand import AnalyzeResult

    return AnalyzeResult(seconds=0.0)


# --- what the build actually runs -------------------------------------------------------


def test_the_database_is_created_from_the_commit_with_the_after_database_as_reference(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-refdb`` is what copies the file set, which is what makes the two comparable (3.1)."""
    request = a_request(tmp_path)

    build(cli(stub, log), request)

    created = stub.calls[0]
    assert created[created.index("-db") + 1] == str(request.paths.before_db)
    assert created[created.index("-gitrepo") + 1] == str(request.repo)
    assert created[created.index("-gitcommit") + 1] == COMMIT
    assert created[created.index("-refdb") + 1] == str(request.paths.after_db)


def test_the_repository_directory_is_recorded_on_the_database(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-gitrepo`` says where contents come from; this is what `git log` is run in (4.3)."""
    request = a_request(tmp_path)

    build(cli(stub, log), request)

    settings = [call for call in stub.calls if "settings" in call]
    assert settings, stub.calls
    assert settings[0][settings[0].index("-GitRepositoryDirectory") + 1] == str(request.repo)


def test_the_new_database_is_analysed_once_and_wholly(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Everything in it is new, so there is nothing selective to do."""
    build(cli(stub, log), a_request(tmp_path))

    analyses = [call for call in stub.calls if "analyze" in call]
    assert len(analyses) == 1
    assert "-all" in analyses[0]


def test_the_accuracy_switch_is_asked_for_only_when_the_request_asks(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 1.3: a 6.5 install and an 8.0 one with the key off send the same argv."""
    stub.plan({"analyze": {"stdout": ACCURACY_OUTPUT}})

    asked = build(cli(stub, log), a_request(tmp_path, accuracy=True))

    assert "-accuracy" in [token for call in stub.calls for token in call]
    assert asked.accuracy == pytest.approx(25 / 92)


def test_without_the_switch_no_figure_comes_back_and_none_is_recorded(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``None`` is not zero: a run that did not ask has not measured a bad resolution."""
    state = SyncState()

    result = build(cli(stub, log), a_request(tmp_path))
    record(state, a_key(), result)

    assert "-accuracy" not in [token for call in stub.calls for token in call]
    assert result.accuracy is None
    assert state.accuracy == {}


def test_a_previous_database_is_removed_before_the_new_one_is_created(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``und create`` over an existing database keeps its file list, so a rebuild must clear."""
    request = a_request(tmp_path)
    stale = request.paths.before_db
    stale.mkdir(parents=True)
    (stale / "leftover").write_text("from the previous key", encoding="utf-8")

    build(cli(stub, log), request)

    assert not (stale / "leftover").exists()


def test_a_failure_carries_unds_own_words(stub: UndStub, log: RecordingLog, tmp_path: Path) -> None:
    """Requirement 3.1's failure mode: a typed error naming what the tool said."""
    stub.plan({"create": {"rc": 1, "stderr": "Error: unknown revision 3ca0a97\n"}})

    with pytest.raises(AnalysisFailedError) as caught:
        build(cli(stub, log), a_request(tmp_path))

    assert "unknown revision 3ca0a97" in caught.value.stderr
    assert "-gitcommit" in caught.value.command, "the command that failed is named too"


# --- what the state records --------------------------------------------------------------


def test_a_build_records_the_route_the_commit_and_the_figure(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirements 3.5, 3.6 and 7.2: what a later run reads instead of measuring again."""
    stub.plan({"analyze": {"stdout": ACCURACY_OUTPUT}})
    state = SyncState()

    ensure(cli(stub, log), a_request(tmp_path, accuracy=True), state)

    assert state.before_route == "commit"
    assert state.before_commit == COMMIT
    assert state.languages == ["Python"]
    assert state.analysis_settings == FINGERPRINT
    assert state.created_with == BUILD
    assert state.accuracy["before"] == pytest.approx(25 / 92)


# --- reuse, and every way it must not happen ----------------------------------------------


def test_an_identical_key_reuses_the_database_and_starts_no_process(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The whole of requirement 3.5: not a cheaper analysis, no analysis."""
    state = recorded(tmp_path)

    assert ensure(cli(stub, log), a_request(tmp_path), state) is None
    assert stub.calls == []


@pytest.mark.parametrize(
    "changed",
    [
        {"commit": "0000000"},
        {"languages": ["Python", "C++"]},
        {"settings": "0123456789abcdef"},
        {"build": "(Build 1204)"},
    ],
    ids=["commit", "languages", "settings", "build"],
)
def test_a_key_that_differs_in_any_component_rebuilds(
    stub: UndStub, log: RecordingLog, tmp_path: Path, changed: dict[str, object]
) -> None:
    """Each of the four decides what the database holds; ignoring one reuses a wrong one."""
    state = recorded(tmp_path)

    assert ensure(cli(stub, log), a_request(tmp_path, key=a_key(**changed)), state) is not None
    assert stub.calls, "a differing key has to rebuild"


def test_the_language_order_is_not_part_of_the_key(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``["C++", "Python"]`` and ``["Python", "C++"]`` are the same database."""
    state = recorded(tmp_path, a_key(languages=["Python", "C++"]))

    reused = ensure(
        cli(stub, log), a_request(tmp_path, key=a_key(languages=["C++", "Python"])), state
    )

    assert reused is None
    assert stub.calls == []


def test_a_shadow_built_database_at_the_same_commit_is_not_this_one(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The routes disagree about the file set, so the route is part of the key (req 3.3)."""
    state = recorded(tmp_path)
    state.before_route = "shadow"

    assert ensure(cli(stub, log), a_request(tmp_path), state) is not None
    assert stub.calls


def test_a_matching_key_whose_database_is_gone_rebuilds(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """An operator who cleared the cache gets a database, not a reuse of nothing."""
    state = recorded(tmp_path)
    (layout(tmp_path).before_db).rmdir()

    assert ensure(cli(stub, log), a_request(tmp_path), state) is not None


def test_a_path_that_is_taken_by_something_unusable_is_refused_rather_than_reused(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``exists()`` cannot tell "no database yet" from "a database this user cannot read"."""
    state = recorded(tmp_path)
    (layout(tmp_path).before_db).rmdir()
    (layout(tmp_path).before_db).write_text("not a database", encoding="utf-8")

    with pytest.raises(AnalysisFailedError) as caught:
        ensure(cli(stub, log), a_request(tmp_path), state)

    assert str(layout(tmp_path).before_db) in str(caught.value)


# --- serving one run: what the route does, and what it declines to do (3.1, 3.4, 3.5) -----


class Recorder:
    """A progress port that keeps what it was told, so a fallback can be read back.

    Local rather than imported: the file-level dependency rule leaves a test module room for
    about five imports, and this needs three characters of behaviour.
    """

    def __init__(self) -> None:
        self.notes: list[str] = []

    def start(self, name: str) -> None:
        """A phase began; the name is enough for these assertions."""
        self.notes.append(name)

    def finish(self, name: str, seconds: float) -> None:
        """A phase ended."""

    def note(self, message: str) -> None:
        """Something the run wants the operator to read."""
        self.notes.append(message)


def forced(tmp_path: Path, stub: UndStub, log: RecordingLog) -> tuple[object, Recorder]:
    """The route turned on by configuration, which needs no measurement of the build.

    ``commit`` rather than ``auto`` on purpose: ``auto`` consults the record ``doctor`` wrote,
    and that decision is ``test_before_route`` s subject. Here the question is what the route
    *does* once it has been chosen.
    """
    settings = default_settings()
    settings.understand.before_side = "commit"
    progress = Recorder()
    return (
        attempt_for(cli(stub, log), layout(tmp_path), tmp_path / "repo", settings, progress),
        progress,
    )


def a_state() -> SyncState:
    """A state as the after side would have left it, before the before side is asked for."""
    return SyncState(languages=["Python"], created_with=BUILD)


def test_the_after_side_is_never_commit_built(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 3.1 is about the before side; the after side is the change under test."""
    attempt, _ = forced(tmp_path, stub, log)

    assert serve(attempt, "after", CommitTarget(commit=COMMIT), a_state(), BUILD) is None
    assert stub.calls == []


def test_a_before_side_that_is_not_a_commit_falls_through(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``check --all`` has no before side to build, and an index target is not a commit."""
    attempt, _ = forced(tmp_path, stub, log)

    assert serve(attempt, "before", IndexTarget(), a_state(), BUILD) is None
    assert stub.calls == []


def test_the_shipped_configuration_asks_the_build_nothing_at_all(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 1.3: the key ships off, so an untouched repository never meets this."""
    progress = Recorder()
    attempt = attempt_for(
        cli(stub, log), layout(tmp_path), tmp_path / "repo", default_settings(), progress
    )

    assert serve(attempt, "before", CommitTarget(commit=COMMIT), a_state(), BUILD) is None
    assert stub.calls == []


def test_the_route_builds_the_database_and_answers_the_run(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    stub.plan({"analyze": {"stdout": ACCURACY_OUTPUT}})
    attempt, progress = forced(tmp_path, stub, log)
    state = a_state()

    answer = serve(attempt, "before", CommitTarget(commit=COMMIT), state, BUILD)

    assert answer is not None
    assert state.before_route == "commit"
    assert state.before_commit == COMMIT
    assert any("commit" in note for note in progress.notes), "a build that ran announces itself"


def test_a_run_this_route_serves_exports_no_shadow_tree(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 3.1, and where the saving is: the tree is the expensive half."""
    attempt, _ = forced(tmp_path, stub, log)

    serve(attempt, "before", CommitTarget(commit=COMMIT), a_state(), BUILD)

    assert not layout(tmp_path).before_tree.exists()


def test_a_recorded_database_is_reused_and_nothing_runs(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 3.5: not a cheaper analysis, no analysis.

    Measured on Build 1262: ``analyze -changed`` with nothing to do prints ``0 of 0 parsed
    files had no errors or warnings (100%)``. Recomputing here would report a perfect
    resolution for a database nothing looked at, so the recorded figure is what comes back.
    """
    attempt, _ = forced(tmp_path, stub, log)
    state = a_state()
    serve(attempt, "before", CommitTarget(commit=COMMIT), state, BUILD)
    layout(tmp_path).before_db.mkdir(parents=True, exist_ok=True)  # what `und create` leaves
    state.accuracy["before"] = 0.42
    built = len(stub.calls)

    again = serve(attempt, "before", CommitTarget(commit=COMMIT), state, BUILD)

    assert stub.calls[built:] == [], "a warm before side runs nothing at all"
    assert again is not None
    assert again.accuracy == pytest.approx(0.42)
    assert again.seconds == 0.0


def test_a_failed_build_is_reported_and_the_shadow_route_serves_the_run(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 3.4: the run continues, and it says which route actually ran."""
    stub.plan({"create": {"rc": 1, "stderr": "Error: unknown revision 3ca0a97\n"}})
    attempt, progress = forced(tmp_path, stub, log)

    answer = serve(attempt, "before", CommitTarget(commit=COMMIT), a_state(), BUILD)

    assert answer is None, "falling through is what sends the run to the shadow route"
    said = " ".join(progress.notes)
    assert "shadow tree" in said
    assert "unknown revision 3ca0a97" in said, "und's own words, not the wrapper's summary"
