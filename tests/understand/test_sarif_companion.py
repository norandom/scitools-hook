"""Understand's SARIF, re-rooted on the repository and put beside the Gate's (2.1, 2.4).

The document below is the shape Build 1262 writes, measured: a project base pointing at the
directory that holds the ``.und``, and artifact paths relative to it. Because the Gate
analyses a shadow tree, that base is a cache directory and every path begins with the
shadow's own segment -- so uploaded unchanged, every result would land on a file no
repository has. These tests are about that one rewrite, and about everything else staying
exactly as Understand wrote it.
"""

from __future__ import annotations

import json
from pathlib import Path

from scitools_hook.understand.sarif_companion import companion_path, companions

ANALYSIS_SARIF: dict[str, object] = {
    "version": "2.1.0",
    "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
    "runs": [
        {
            "artifacts": [
                {
                    "location": {"uri": "after/src/a.py", "uriBaseId": "UND_PROJECT"},
                    "roles": ["analysisTarget", "resultFile"],
                }
            ],
            "columnKind": "unicodeCodePoints",
            "invocations": [{"executionSuccessful": True, "properties": {"UndName": "after"}}],
            "logicalLocations": [],
            "originalUriBaseIds": {
                "UND_PROJECT": {
                    "description": {"text": "The directory containing the Understand project"},
                    "uri": "file:///home/someone/.cache/scitools-hook/abc123/",
                }
            },
            "results": [
                {
                    "fingerprints": {"understand/v1": "0c6d7153-4f846b05-6f0141bc"},
                    "locations": [
                        {
                            "id": 0,
                            "physicalLocation": {
                                "artifactLocation": {"index": 0},
                                "region": {"startLine": 2},
                            },
                        }
                    ],
                    "message": {"text": "expected identifier at token return"},
                    "ruleId": "UND_ERROR",
                    "ruleIndex": 1,
                }
            ],
            "tool": {
                "driver": {
                    "name": "Analysis",
                    "organization": "SciTools",
                    "rules": [{"id": "UND_WARNING"}, {"id": "UND_ERROR"}],
                    "version": "1262",
                }
            },
        }
    ],
}
"""``und analyze -sarif`` on Build 1262, with the shadow layout the Gate actually produces."""


def written(tmp_path: Path, document: object = ANALYSIS_SARIF) -> Path:
    """Understand's document, where Understand would have left it."""
    source = tmp_path / "cache" / "parselog.sarif"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(document), encoding="utf-8")
    return source


def companion(tmp_path: Path, document: object = ANALYSIS_SARIF):
    """Run the copy for one analysis document and answer the single companion."""
    found = companions(
        {"analysis": written(tmp_path, document)},
        tmp_path / "out" / "gate.sarif",
        tmp_path / "repo",
        tmp_path / "cache" / "after",
    )
    assert len(found) == 1
    return found[0]


# --- where it goes -------------------------------------------------------------------


def test_the_companion_is_named_so_it_cannot_be_confused_with_the_gates_own() -> None:
    """One upload carries three files; a reader has to be able to tell them apart."""
    assert companion_path(Path("/out/gate.sarif"), "analysis") == Path(
        "/out/gate.understand-analysis.sarif"
    )
    assert companion_path(Path("/out/gate.sarif"), "codecheck") == Path(
        "/out/gate.understand-codecheck.sarif"
    )


def test_the_companion_is_written_beside_the_gates_file(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()

    found = companion(tmp_path)

    assert found.problem == ""
    assert found.target is not None
    assert found.target.parent == tmp_path / "out"
    assert found.target.exists()


# --- the one rewrite -------------------------------------------------------------------


def test_the_project_base_becomes_the_repository_root(tmp_path: Path) -> None:
    """Uploaded unchanged, the base is a cache directory and every result misses its file."""
    repo = tmp_path / "repo"
    repo.mkdir()

    found = companion(tmp_path)

    assert found.target is not None
    document = json.loads(found.target.read_text(encoding="utf-8"))
    base = document["runs"][0]["originalUriBaseIds"]["UND_PROJECT"]["uri"]
    assert base == f"{repo.resolve().as_uri()}/"


def test_the_shadow_segment_comes_off_the_front_of_every_path(tmp_path: Path) -> None:
    """``after/src/a.py`` is a file of the shadow tree; ``src/a.py`` is a file of the repository."""
    (tmp_path / "repo").mkdir()

    found = companion(tmp_path)

    assert found.target is not None
    document = json.loads(found.target.read_text(encoding="utf-8"))
    assert document["runs"][0]["artifacts"][0]["location"]["uri"] == "src/a.py"


def test_a_path_that_does_not_begin_with_the_shadow_is_left_alone(tmp_path: Path) -> None:
    """The interpreter's own standard library is named absolutely and is not the repository's."""
    (tmp_path / "repo").mkdir()
    elsewhere = json.loads(json.dumps(ANALYSIS_SARIF))
    elsewhere["runs"][0]["artifacts"][0]["location"]["uri"] = "/usr/lib/python3.14/typing.py"

    found = companion(tmp_path, elsewhere)

    assert found.target is not None
    document = json.loads(found.target.read_text(encoding="utf-8"))
    assert document["runs"][0]["artifacts"][0]["location"]["uri"] == "/usr/lib/python3.14/typing.py"


def test_everything_understand_said_survives_the_copy(tmp_path: Path) -> None:
    """The tool, the rules, the fingerprints and the results are Understand's statement."""
    (tmp_path / "repo").mkdir()

    found = companion(tmp_path)

    assert found.target is not None
    run = json.loads(found.target.read_text(encoding="utf-8"))["runs"][0]
    assert run["tool"]["driver"]["name"] == "Analysis"
    assert run["tool"]["driver"]["version"] == "1262"
    assert run["results"][0]["ruleId"] == "UND_ERROR"
    assert run["results"][0]["fingerprints"] == {"understand/v1": "0c6d7153-4f846b05-6f0141bc"}
    assert run["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 2


def test_a_result_naming_its_file_inline_is_rewritten_too(tmp_path: Path) -> None:
    """Most results index the artifacts table; a document may name the file in place instead."""
    (tmp_path / "repo").mkdir()
    inline = json.loads(json.dumps(ANALYSIS_SARIF))
    location = inline["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    location["artifactLocation"] = {"uri": "after/src/b.py", "uriBaseId": "UND_PROJECT"}

    found = companion(tmp_path, inline)

    assert found.target is not None
    document = json.loads(found.target.read_text(encoding="utf-8"))
    written_location = document["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert written_location["artifactLocation"]["uri"] == "src/b.py"


# --- what goes wrong ---------------------------------------------------------------------


def test_a_source_that_is_not_there_is_a_problem_and_not_an_exception(tmp_path: Path) -> None:
    """Requirement 2.4: the Gate's own SARIF is the deliverable; these are extra."""
    found = companions(
        {"analysis": tmp_path / "never-written.sarif"},
        tmp_path / "gate.sarif",
        tmp_path,
        tmp_path / "after",
    )

    assert found[0].target is None
    assert "could not be read" in found[0].problem


def test_a_source_that_is_not_json_says_so(tmp_path: Path) -> None:
    found = companion(tmp_path, "not a document at all")

    assert found.target is None
    assert "is not JSON" in found.problem or "carries no SARIF runs" in found.problem


def test_a_document_with_no_runs_is_refused_rather_than_copied(tmp_path: Path) -> None:
    """An empty file would upload as a tool that found nothing, which is not what happened."""
    found = companion(tmp_path, {"version": "2.1.0"})

    assert found.target is None
    assert "no SARIF runs" in found.problem


def test_two_documents_are_copied_independently(tmp_path: Path) -> None:
    """A CodeCheck document that is missing must not stop the analysis one being written."""
    (tmp_path / "repo").mkdir()

    found = companions(
        {"analysis": written(tmp_path), "codecheck": tmp_path / "gone.sarif"},
        tmp_path / "out" / "gate.sarif",
        tmp_path / "repo",
        tmp_path / "cache" / "after",
    )

    by_kind = {one.kind: one for one in found}
    assert by_kind["analysis"].target is not None
    assert by_kind["codecheck"].target is None
    assert by_kind["codecheck"].problem
