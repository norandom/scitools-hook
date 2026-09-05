"""A sync state from an older cache layout is discarded once, and said out loud (8.6).

The state file records what the two databases hold. This specification adds fields to it --
the before route, the analysis fingerprint, each side's accuracy, the generated-architecture
stamps -- and a state written before them cannot answer any of those questions. Reading it
anyway would leave a run believing the before database was built from a commit it was not,
which is the shape of a stale-cache bug that reports the wrong findings and exits 0.

So the layout carries a number, absent reads as zero, and a state that is not this layout is
discarded exactly as an unreadable one already is: the operator is told, and the next run
analyses from scratch. Once, because the run that discards it writes the current number.

The manager's own test module is at its file limit, so this one test lives beside it and
borrows its harness rather than growing it.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import MakeGitRepo
from test_database import Harness, make_harness

from scitools_hook.models.cache import CACHE_SCHEMA, SyncState


def built_repository(git_repo: MakeGitRepo, cache: Path) -> Harness:
    """A one-file repository whose databases have been built once, so a state exists."""
    builder = git_repo()
    builder.write("src/a.py", "def a():\n    return 1\n")
    builder.stage()
    builder.commit("first")
    harness = make_harness(builder, cache)
    harness.ensure()
    return harness


def test_the_run_that_writes_the_state_stamps_the_current_layout(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """Without this the next run would discard a state this run had just written."""
    harness = built_repository(git_repo, tmp_path / "cache")

    written = SyncState.model_validate_json(harness.paths.state.read_text(encoding="utf-8"))

    assert written.schema_version == CACHE_SCHEMA


def test_a_state_from_an_older_layout_is_discarded_with_a_note(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """The observable behaviour: the operator is told, and the run does not trust the file."""
    harness = built_repository(git_repo, tmp_path / "cache")
    stale = json.loads(harness.paths.state.read_text(encoding="utf-8"))
    stale.pop("schema_version", None)
    harness.paths.state.write_text(json.dumps(stale), encoding="utf-8")
    harness.progress.notes.clear()

    harness.ensure()

    said = [note for note in harness.progress.notes if "sync state" in note]
    assert said, f"nothing said about the discarded state; notes were {harness.progress.notes}"
    assert "layout" in said[0]
    assert "analysing from scratch" in said[0]


def test_the_discard_happens_once_and_the_next_run_trusts_the_state(
    git_repo: MakeGitRepo, tmp_path: Path
) -> None:
    """A state discarded on every run would be a full analysis on every run, for ever."""
    harness = built_repository(git_repo, tmp_path / "cache")
    stale = json.loads(harness.paths.state.read_text(encoding="utf-8"))
    stale.pop("schema_version", None)
    harness.paths.state.write_text(json.dumps(stale), encoding="utf-8")

    harness.ensure()
    harness.progress.notes.clear()
    harness.ensure()

    assert not [note for note in harness.progress.notes if "sync state" in note]
