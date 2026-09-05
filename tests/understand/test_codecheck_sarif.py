"""CodeCheck's violations, read from the SARIF Understand 8.0 writes (2.3, 2.6).

**Every document here is synthetic and says so.** The licence on the machine this was
written on excludes CodeCheck, so no real inspection has ever been read: the documents below
follow the SARIF 2.1.0 schema and the shape ``und analyze -sarif`` writes, which *is*
measured, and the contract test that would settle it is an expected failure naming the
licence (``tests/contract/test_codecheck_sarif_contract.py``). That is the one part of this
specification standing on a document rather than on a run, and these tests are written to
make the assumption visible rather than to hide it.

What they pin down is the mapping onto :class:`~scitools_hook.models.understand.RawViolation`
-- the same record the CSV reader produces, so nothing downstream learns which build ran --
and the two refusals that keep an unfamiliar document from becoming a quiet green run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.understand import NO_LINE, RawViolation
from scitools_hook.understand.codecheck_sarif import (
    RESULTS_SARIF,
    find_results,
    read_sarif_violations,
)

FULL_RESULT: dict[str, Any] = {
    "ruleId": "CPP_F022",
    "level": "warning",
    "message": {"text": "Function main is too complex"},
    "locations": [
        {
            "physicalLocation": {
                "artifactLocation": {"index": 0, "uriBaseId": "UND_PROJECT"},
                "region": {"startLine": 42, "startColumn": 7},
            },
            "logicalLocations": [{"name": "main", "fullyQualifiedName": "app.main"}],
        }
    ],
}
"""One violation carrying everything a finding needs: rule, file, position, text, entity."""

RESULTS: dict[str, Any] = {
    "version": "2.1.0",
    "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
    "runs": [
        {
            "tool": {
                "driver": {
                    "name": "CodeCheck",
                    "organization": "SciTools",
                    "version": "1262",
                    "rules": [
                        {"id": "CPP_F022", "name": "Cyclomatic Complexity"},
                        {"id": "CPP_V001"},
                    ],
                }
            },
            "artifacts": [
                {"location": {"uri": "after/src/app.py", "uriBaseId": "UND_PROJECT"}},
                {"location": {"uri": "after/src/other.py", "uriBaseId": "UND_PROJECT"}},
            ],
            "originalUriBaseIds": {
                "UND_PROJECT": {"uri": "file:///home/someone/.cache/scitools-hook/abc123/"}
            },
            "results": [FULL_RESULT],
        }
    ],
}
"""``results.sarif`` as ``und codecheck`` is documented to write it (specified, not measured)."""


def document(tmp_path: Path, content: object = RESULTS) -> Path:
    """One inspection document where ``und codecheck`` would have left it."""
    out_dir = tmp_path / "cc"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = out_dir / RESULTS_SARIF
    written.write_text(json.dumps(content), encoding="utf-8")
    return written


def altered(**fields: object) -> dict[str, Any]:
    """The document with one result replacing the full one, everything else as it was."""
    changed = json.loads(json.dumps(RESULTS))
    changed["runs"][0]["results"] = [{**json.loads(json.dumps(FULL_RESULT)), **fields}]
    return changed


def only(found: list[RawViolation]) -> RawViolation:
    assert len(found) == 1, found
    return found[0]


# --- finding the document ------------------------------------------------------------


def test_an_output_directory_holding_the_inspections_sarif_answers_with_it(
    tmp_path: Path,
) -> None:
    written = document(tmp_path)

    assert find_results(written.parent) == written


def test_an_output_directory_without_one_answers_nothing_rather_than_raising(
    tmp_path: Path,
) -> None:
    """6.5 writes CSVs and no SARIF, and that is not a failure -- it is the other reader."""
    (tmp_path / "cc").mkdir()

    assert find_results(tmp_path / "cc") is None


def test_a_directory_named_like_the_document_is_not_the_document(tmp_path: Path) -> None:
    (tmp_path / "cc" / RESULTS_SARIF).mkdir(parents=True)

    assert find_results(tmp_path / "cc") is None


# --- one result becomes one violation --------------------------------------------------


def test_a_full_result_carries_the_rule_the_file_the_position_the_text_and_the_entity(
    tmp_path: Path,
) -> None:
    """The whole mapping in one assertion: the CSV reader produces exactly this record."""
    found = only(read_sarif_violations(document(tmp_path)))

    assert found == RawViolation(
        check_id="CPP_F022",
        check_name="Cyclomatic Complexity",
        path="after/src/app.py",
        line=42,
        column=7,
        message="Function main is too complex",
        entity="app.main",
    )


def test_a_rule_the_tool_names_only_by_id_falls_back_to_the_id(tmp_path: Path) -> None:
    """``CPP_V001`` is in the rules table with no ``name``, which SARIF allows.

    The operator would have read the same thing from the CSV, whose ``Check Name`` column the
    8.0 plugin leaves empty -- so an id is a worse name than a title and a better one than
    nothing at all.
    """
    found = only(read_sarif_violations(document(tmp_path, altered(ruleId="CPP_V001"))))

    assert found.check_id == "CPP_V001"
    assert found.check_name == "CPP_V001"


def test_a_rule_absent_from_the_table_entirely_still_names_itself(tmp_path: Path) -> None:
    found = only(read_sarif_violations(document(tmp_path, altered(ruleId="CPP_X999"))))

    assert found.check_name == "CPP_X999"


def test_a_result_naming_its_file_inline_is_read_as_well_as_one_indexing_the_table(
    tmp_path: Path,
) -> None:
    """Most results index ``artifacts``; SARIF also allows the path in the result itself."""
    inline = altered(
        locations=[
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": "after/src/inline.py"},
                    "region": {"startLine": 3},
                }
            }
        ]
    )

    found = only(read_sarif_violations(document(tmp_path, inline)))

    assert found.path == "after/src/inline.py"


def test_a_violation_against_a_whole_file_reports_no_line_rather_than_line_one(
    tmp_path: Path,
) -> None:
    """Understand's own value for a check that reports against a file is 0, not 1 (NO_LINE)."""
    whole_file = altered(
        locations=[{"physicalLocation": {"artifactLocation": {"index": 1}}}],
    )

    found = only(read_sarif_violations(document(tmp_path, whole_file)))

    assert found.path == "after/src/other.py"
    assert found.line == NO_LINE
    assert found.column is None


def test_a_result_naming_no_entity_carries_none_rather_than_an_empty_name(
    tmp_path: Path,
) -> None:
    without = altered(
        locations=[
            {
                "physicalLocation": {
                    "artifactLocation": {"index": 0},
                    "region": {"startLine": 9},
                }
            }
        ]
    )

    assert only(read_sarif_violations(document(tmp_path, without))).entity is None


def test_an_entity_named_only_by_its_short_name_is_taken(tmp_path: Path) -> None:
    """``fullyQualifiedName`` is preferred and optional; ``name`` is the fallback."""
    short = altered(
        locations=[
            {
                "physicalLocation": {"artifactLocation": {"index": 0}},
                "logicalLocations": [{"name": "helper"}],
            }
        ]
    )

    assert only(read_sarif_violations(document(tmp_path, short))).entity == "helper"


def test_every_result_of_every_run_is_read_in_the_order_the_document_gives(
    tmp_path: Path,
) -> None:
    """A document may carry several runs; none of them is the one that counts."""
    several = json.loads(json.dumps(RESULTS))
    second = json.loads(json.dumps(FULL_RESULT))
    second["ruleId"] = "CPP_V001"
    third = json.loads(json.dumps(FULL_RESULT))
    third["ruleId"] = "CPP_X999"
    several["runs"][0]["results"] = [FULL_RESULT, second]
    several["runs"].append({**json.loads(json.dumps(several["runs"][0])), "results": [third]})

    found = read_sarif_violations(document(tmp_path, several))

    assert [violation.check_id for violation in found] == ["CPP_F022", "CPP_V001", "CPP_X999"]


def test_a_run_that_found_nothing_reads_as_no_violations(tmp_path: Path) -> None:
    """The one empty answer that is real: a document with runs and an empty result list."""
    quiet = json.loads(json.dumps(RESULTS))
    quiet["runs"][0]["results"] = []

    assert read_sarif_violations(document(tmp_path, quiet)) == []


# --- what is refused --------------------------------------------------------------------


def test_a_document_with_no_runs_is_refused_and_names_the_file(tmp_path: Path) -> None:
    """An empty read would report a clean inspection for a run that inspected nothing."""
    written = document(tmp_path, {"version": "2.1.0"})

    with pytest.raises(AnalysisFailedError) as caught:
        read_sarif_violations(written)

    assert str(written) in str(caught.value)
    assert "no SARIF runs" in str(caught.value)


def test_a_file_that_is_not_json_is_refused_and_names_the_file(tmp_path: Path) -> None:
    written = tmp_path / "cc" / RESULTS_SARIF
    written.parent.mkdir(parents=True)
    written.write_text("Licensing Error: No license for CodeCheck.\n", encoding="utf-8")

    with pytest.raises(AnalysisFailedError) as caught:
        read_sarif_violations(written)

    assert str(written) in str(caught.value)
    assert "not JSON" in str(caught.value)


def test_a_file_that_is_not_there_is_refused_and_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(AnalysisFailedError) as caught:
        read_sarif_violations(tmp_path / RESULTS_SARIF)

    assert "could not be read" in str(caught.value)


def test_a_result_with_no_rule_id_is_refused_rather_than_passed_on(tmp_path: Path) -> None:
    """It would raise ``ConfigError: a CodeCheck rule name needs a check id``, far from here."""
    with pytest.raises(AnalysisFailedError) as caught:
        read_sarif_violations(document(tmp_path, altered(ruleId="")))

    assert "no rule id" in str(caught.value)
    assert "too complex" in str(caught.value), "the refusal quotes what the result did say"


def test_a_result_naming_no_file_is_refused_rather_than_reported_against_the_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(AnalysisFailedError) as caught:
        read_sarif_violations(document(tmp_path, altered(locations=[])))

    assert "no file" in str(caught.value)


def test_a_result_indexing_an_artifact_the_table_does_not_have_names_no_file(
    tmp_path: Path,
) -> None:
    """An index past the end is a document this reader has not seen; it is not file zero."""
    with pytest.raises(AnalysisFailedError) as caught:
        read_sarif_violations(
            document(
                tmp_path,
                altered(locations=[{"physicalLocation": {"artifactLocation": {"index": 7}}}]),
            )
        )

    assert "no file" in str(caught.value)
