"""Which architecture a run's rules read, and when one is generated again (4.1, 4.4, 4.5).

Three sources and one node. A repository that **declares** the configured architecture
supplies it, whatever the build could generate: a declaration is a decision and a generation
is a derivation. ``Directory Structure`` is built into every database. Anything else the build
was measured to offer is generated from the after side's commit.

The two properties worth naming are both about *not* doing things:

* A generation is skipped while the commit it was generated from has not moved, and the kept
  export is handed over instead -- a skipped run still has to produce the architecture, not
  merely decline to rebuild it.
* A generation that fails is stepped over when the repository declares one of its own and
  raised when it does not. A run with no architecture evaluates every node-level rule against
  an empty node set and reports nothing, which is worse than stopping.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from und_stub import RecordingLog, UndStub, cli, write_stub

from scitools_hook.config.defaults import default_settings
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.understand.generated_arch import architecture_for, site_for
from scitools_hook.understand.und_arch import ArchNode

STABILITY = "Git Stability"
COMMIT = "3ca0a97"
OFFERED = ("Directory Structure", "Git Stability", "Git Owner")
"""What the stored measurement says this build can generate."""

EXPORT = (
    "<!DOCTYPE arch>\n"
    '<arch name="Git Stability"><arch name="Active">@l{repo}/pkg/core.py</arch></arch>\n'
)
"""What ``und export -arch`` writes, with the absolute members Build 1262 writes."""


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log."""
    return RecordingLog(entries=[])


class Recorder:
    """A progress port that keeps what it was told, so a fallback can be read back."""

    def __init__(self) -> None:
        self.notes: list[str] = []

    def start(self, name: str) -> None:
        """A phase began."""
        self.notes.append(name)

    def finish(self, name: str, seconds: float) -> None:
        """A phase ended."""

    def note(self, message: str) -> None:
        """Something the run wants the operator to read."""
        self.notes.append(message)


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


def a_repository(tmp_path: Path) -> Path:
    """A repository holding the one file the generated export names."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True, exist_ok=True)
    (repo / "pkg" / "core.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    return repo


def a_site(tmp_path: Path, stub: UndStub, log: RecordingLog, name: str = STABILITY):
    """The run's surroundings, with ``structure.architecture`` set to ``name``."""
    settings = default_settings()
    settings.structure.architecture = name
    settings.project.languages = ["Python"]
    progress = Recorder()
    return (
        site_for(cli(stub, log), layout(tmp_path), a_repository(tmp_path), settings, progress),
        progress,
    )


def at_commit() -> SyncState:
    """A state whose after side is a commit, which is what a generation needs."""
    return SyncState(after_target="commit", after_tree_id=COMMIT, languages=["Python"])


def exporting(stub: UndStub, tmp_path: Path) -> None:
    """Script the stub so ``export -arch`` writes the document the route reads back."""
    stub.plan({"export": {"write_argv": EXPORT.format(repo=a_repository(tmp_path))}})


# --- which of the three sources answers ---------------------------------------------------


def test_a_declaration_of_the_configured_name_supplies_it(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A declaration is a decision; generating over it would overrule the repository."""
    site, _ = a_site(tmp_path, stub, log, "Layers")
    declared = ArchNode(name="Layers", members=("pkg/core.py",), children=())

    assert architecture_for(site, declared, at_commit(), OFFERED) is declared
    assert stub.calls == []


def test_the_built_in_directory_structure_needs_no_generation(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Every database has it from the moment it exists, so nothing is built for it."""
    site, _ = a_site(tmp_path, stub, log, "Directory Structure")

    assert architecture_for(site, None, at_commit(), OFFERED) is None
    assert stub.calls == []


def test_a_name_the_build_does_not_offer_generates_nothing(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The configuration check refuses such a name before a run starts; this is the backstop."""
    site, _ = a_site(tmp_path, stub, log, "Git Stabilty")

    assert architecture_for(site, None, at_commit(), OFFERED) is None
    assert stub.calls == []


def test_an_offered_name_is_generated_from_the_after_sides_commit(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    site, progress = a_site(tmp_path, stub, log)
    exporting(stub, tmp_path)

    found = architecture_for(site, None, at_commit(), OFFERED)

    assert found is not None
    assert found.name == STABILITY
    assert sorted(found.paths()) == ["pkg/core.py"]
    assert any("generating" in note for note in progress.notes), "the phase is named and timed"


# --- generating once, and then not again (requirement 4.4) --------------------------------


def test_a_second_run_at_the_same_commit_generates_nothing(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Nothing else can change what ``git log`` says about a commit that has not moved."""
    site, _ = a_site(tmp_path, stub, log)
    exporting(stub, tmp_path)
    state = at_commit()
    first = architecture_for(site, None, state, OFFERED)
    generated = len(stub.calls)

    again = architecture_for(site, None, state, OFFERED)

    assert stub.calls[generated:] == [], "a skipped run builds no database and generates nothing"
    assert again is not None and first is not None
    assert sorted(again.paths()) == sorted(first.paths())
    assert again.name == first.name


def test_a_moved_commit_generates_again(stub: UndStub, log: RecordingLog, tmp_path: Path) -> None:
    site, _ = a_site(tmp_path, stub, log)
    exporting(stub, tmp_path)
    state = at_commit()
    architecture_for(site, None, state, OFFERED)
    generated = len(stub.calls)
    state.after_tree_id = "0000000"

    architecture_for(site, None, state, OFFERED)

    assert stub.calls[generated:], "a different commit is a different history"


def test_the_kept_export_is_what_a_skipped_run_hands_over(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The rules read the node, so a skip that produced nothing would produce no rules."""
    site, _ = a_site(tmp_path, stub, log)
    exporting(stub, tmp_path)
    state = at_commit()
    architecture_for(site, None, state, OFFERED)

    kept = layout(tmp_path).root / "generated" / f"{STABILITY}.xml"

    assert kept.is_file()
    assert STABILITY in kept.read_text(encoding="utf-8")


# --- what goes wrong (requirements 4.3, 4.5) -----------------------------------------------


# --- when a generation fails but a declaration stands (requirement 4.5) ---------------


def test_a_failed_generation_falls_back_to_a_declaration_and_says_so(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Requirement 4.5: reported and stepped over, because there is still an architecture."""
    site, progress = a_site(tmp_path, stub, log)
    stub.plan({"export": {"write_argv": '<!DOCTYPE arch>\n<arch name="Git Stability"></arch>\n'}})
    declared = ArchNode(name="Layers", members=("pkg/core.py",), children=())

    found = architecture_for(site, declared, at_commit(), OFFERED)

    assert found is declared
    said = " ".join(progress.notes)
    assert "could not be generated" in said
    assert "Layers" in said
