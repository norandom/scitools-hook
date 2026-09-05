"""Understand's SARIF prepared during a run and placed after it (requirements 2.1, 2.4).

Two halves, tested separately because they can fail separately. ``prepare`` turns whatever
documents this run produced into records on the result, re-rooted on the repository, and says
so when there is nothing to prepare. ``write_beside`` copies them where ``--sarif`` asked and
records where each one went, or why it did not go anywhere.

The property both halves share is that **nothing here can fail a run**: the Gate's own SARIF
is the deliverable and these are extra, so every failure is a sentence on the result rather
than an exception, and the exit code stays a function of the findings.
"""

from __future__ import annotations

import json
from pathlib import Path

from scitools_hook.models.cache import CachePaths
from scitools_hook.models.findings import RunResult, UnderstandSarif
from scitools_hook.runner.companions import (
    ANALYSIS,
    CODECHECK,
    DISCARDED,
    NO_DIAGNOSTICS,
    prepare,
    write_beside,
)

DOCUMENT: dict[str, object] = {
    "version": "2.1.0",
    "runs": [
        {
            "tool": {"driver": {"name": "Analysis", "version": "1262"}},
            "artifacts": [{"location": {"uri": "after/pkg/one.py", "uriBaseId": "UND_PROJECT"}}],
            "originalUriBaseIds": {"UND_PROJECT": {"uri": "file:///somewhere/cache/abc123/"}},
            "results": [{"ruleId": "UND_ERROR", "message": {"text": "unexpected indent"}}],
        }
    ],
}
"""The shape Build 1262 writes, with the shadow segment the Gate's own layout produces."""


def layout(tmp_path: Path) -> CachePaths:
    """A cache directory of the shape the database manager owns."""
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


def written(tmp_path: Path, name: str = "after.sarif", document: object = DOCUMENT) -> Path:
    """One document where Understand would have left it."""
    target = tmp_path / "cache" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def result(*companions: UnderstandSarif) -> RunResult:
    """A run result carrying nothing but the companions under test."""
    return RunResult(
        tool_version="0.1.0",
        understand_version="(Build 1262)",
        repo_root="/repo",
        selection="staged",
        started_at="2026-09-05T12:00:00Z",
        seconds=1.0,
        understand_sarif=list(companions),
    )


# --- preparing what the run produced ---------------------------------------------------


def test_a_document_the_run_produced_is_re_rooted_and_kept_in_the_cache(tmp_path: Path) -> None:
    """Requirement 2.1: the paths an upload needs are the repository's, not the cache's."""
    repo = tmp_path / "repo"
    repo.mkdir()

    found = prepare({ANALYSIS: written(tmp_path)}, layout(tmp_path), repo)

    assert [one.kind for one in found] == [ANALYSIS]
    assert found[0].problem == ""
    assert found[0].source is not None
    document = json.loads(Path(found[0].source).read_text(encoding="utf-8"))
    run = document["runs"][0]
    assert run["artifacts"][0]["location"]["uri"] == "pkg/one.py"
    assert run["originalUriBaseIds"]["UND_PROJECT"]["uri"] == f"{repo.resolve().as_uri()}/"


def test_nothing_is_written_beside_the_gates_file_yet(tmp_path: Path) -> None:
    """Preparing is not placing: where a document goes is ``--sarif``'s decision."""
    (tmp_path / "repo").mkdir()

    found = prepare({ANALYSIS: written(tmp_path)}, layout(tmp_path), tmp_path / "repo")

    assert found[0].written is None


def test_an_analysis_that_produced_no_document_says_why_rather_than_saying_nothing(
    tmp_path: Path,
) -> None:
    """The operator asked for it, so silence would read as "this build has none"."""
    (tmp_path / "repo").mkdir()

    found = prepare({ANALYSIS: None}, layout(tmp_path), tmp_path / "repo")

    assert [one.kind for one in found] == [ANALYSIS]
    assert found[0].source is None
    assert found[0].problem == NO_DIAGNOSTICS
    assert "cold cache" in found[0].problem, "the reason has to say what would produce one"


def test_a_kind_that_did_not_run_at_all_contributes_no_record(tmp_path: Path) -> None:
    """A repository configuring no CodeCheck is not a repository missing its results."""
    (tmp_path / "repo").mkdir()

    sources = {ANALYSIS: written(tmp_path), CODECHECK: None}
    found = prepare(sources, layout(tmp_path), tmp_path / "repo")

    assert [one.kind for one in found] == [ANALYSIS]


def test_both_documents_are_prepared_independently(tmp_path: Path) -> None:
    """A CodeCheck document that cannot be read must not stop the analysis one."""
    (tmp_path / "repo").mkdir()

    found = prepare(
        {ANALYSIS: written(tmp_path), CODECHECK: tmp_path / "cache" / "gone.sarif"},
        layout(tmp_path),
        tmp_path / "repo",
    )

    by_kind = {one.kind: one for one in found}
    assert by_kind[ANALYSIS].source is not None
    assert by_kind[CODECHECK].source is None
    assert "could not be read" in by_kind[CODECHECK].problem


def test_a_document_that_is_not_sarif_is_a_problem_and_not_an_exception(tmp_path: Path) -> None:
    """Requirement 2.4: a run must not fail over a file it was copying as a convenience."""
    (tmp_path / "repo").mkdir()

    found = prepare(
        {ANALYSIS: written(tmp_path, document={"version": "2.1.0"})},
        layout(tmp_path),
        tmp_path / "repo",
    )

    assert found[0].source is None
    assert "no SARIF runs" in found[0].problem


# --- placing them beside the Gate's file -------------------------------------------------


def test_each_prepared_document_is_copied_beside_the_gates_and_says_where(
    tmp_path: Path,
) -> None:
    source = written(tmp_path)
    gate = tmp_path / "out" / "gate.sarif"
    gate.parent.mkdir()

    placed = write_beside(result(UnderstandSarif(kind=ANALYSIS, source=str(source))), gate)

    target = gate.with_name("gate.understand-analysis.sarif")
    assert placed.understand_sarif[0].written == str(target)
    assert target.read_bytes() == source.read_bytes()


def test_a_run_that_prepared_nothing_is_returned_unchanged(tmp_path: Path) -> None:
    empty = result()

    assert write_beside(empty, tmp_path / "gate.sarif") is empty


def test_a_record_that_has_only_a_problem_is_left_as_it_is(tmp_path: Path) -> None:
    """There is no file to copy, and the reason it is absent is the useful thing."""
    placed = write_beside(
        result(UnderstandSarif(kind=ANALYSIS, problem=NO_DIAGNOSTICS)), tmp_path / "gate.sarif"
    )

    assert placed.understand_sarif[0].written is None
    assert placed.understand_sarif[0].problem == NO_DIAGNOSTICS


def test_a_destination_that_is_not_a_regular_file_is_a_discard_and_not_a_stray_file() -> None:
    """``--sarif /dev/null`` stores nothing; a companion beside it would land at the root."""
    placed = write_beside(
        result(UnderstandSarif(kind=ANALYSIS, source="/nowhere/after.sarif")), Path("/dev/null")
    )

    assert placed.understand_sarif[0].written is None
    assert placed.understand_sarif[0].problem == DISCARDED
    assert not Path("/null.understand-analysis.sarif").exists()


def test_a_copy_that_cannot_be_written_becomes_that_documents_problem(tmp_path: Path) -> None:
    """The directory ``--sarif`` named does not exist, so neither file can be written."""
    placed = write_beside(
        result(UnderstandSarif(kind=ANALYSIS, source=str(written(tmp_path)))),
        tmp_path / "missing" / "gate.sarif",
    )

    assert placed.understand_sarif[0].written is None
    assert "could not be written" in placed.understand_sarif[0].problem


def test_the_exit_code_carrying_fields_are_untouched_by_any_of_this(tmp_path: Path) -> None:
    """Requirement 2.4: the companions never move the verdict."""
    before = result(UnderstandSarif(kind=ANALYSIS, source=str(written(tmp_path))))

    after = write_beside(before, tmp_path / "gate.sarif")

    assert after.blocking_count == before.blocking_count
    assert after.findings == before.findings
