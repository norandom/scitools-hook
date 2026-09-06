"""The JSON view of a run: one document, everything requirement 7.4 lists, and nothing else.

The contract these tests pin is narrow on purpose. Requirement 7.4 asks for *a single JSON
document* with a versioned schema carrying the run metadata, the effective thresholds, all
findings, the ignored-entity counts, the unavailable metrics and the parse errors, and for
nothing else to reach standard output; requirement 7.7 keeps diagnostics off that stream.
A renderer that returns a string satisfies both only if the string is exactly one document
(no banner, no trailing note, no second object) and if rendering itself prints nothing.

The strongest statement of "carries everything" is the round trip: a document that validates
back into an equal :class:`RunResult` cannot have dropped, reordered or coerced a field. The
fixture run therefore fills every field of the model -- including the ones the smaller
renderers ignore (``tightened``, ``highest``, ``parse_errors``) and the shapes tasks 4.3 and
4.7 warned about (an architecture-node path, a CodeCheck row whose entity lives in
``details``, a finding with no line and one outside the repository root).

Determinism is checked across processes, not just across calls: a second interpreter with a
different ``PYTHONHASHSEED`` re-renders the same run and must produce the same bytes, so the
output can be diffed between runs and machines.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest
from fixtures import ENGINE

from scitools_hook.config.models import Limit, ThresholdSpec
from scitools_hook.models.findings import Finding, HighestValue, RunResult, TightenedLimit
from scitools_hook.models.snapshot import EntityKey, EntityRef, ParseError
from scitools_hook.report.json_out import render_json

BUILTINS: Final = "/opt/scitools/conf/understand/python/python3/builtins.py"

_REMOTE_RENDER: Final = """\
import json, sys
from scitools_hook.models.findings import RunResult
from scitools_hook.report.json_out import render_json

sys.stdout.write(render_json(RunResult.model_validate(json.loads(sys.stdin.read()))))
"""
"""Rebuild the run from JSON in a fresh interpreter and render it again (determinism)."""


def evaluate_ref() -> EntityRef:
    """The routine every metric finding in the fixture run points at."""
    key = EntityKey(scope="routine", path=ENGINE, longname="engine.Engine.evaluate", parameters="")
    return EntityRef(key=key, kind="Python Function", name="evaluate", line=42)


def threshold_finding() -> Finding:
    """A metric threshold with a value, a before, a limit and a hint: the fullest shape."""
    return Finding(
        kind="threshold",
        rule="routine.CyclomaticStrict",
        metric="CyclomaticStrict",
        scope="routine",
        entity=evaluate_ref(),
        path=ENGINE,
        line=42,
        value=20,
        before=9,
        limit=10,
        severity="error",
        blocking=True,
        message="routine engine.Engine.evaluate CyclomaticStrict is 20, over the maximum of 10",
        hint="split the decision groups into named routines",
    )


def arch_finding() -> Finding:
    """A structural finding whose ``path`` is an architecture node, not a file (task 4.3)."""
    return Finding(
        kind="structural",
        rule="structure.coupling",
        scope="arch",
        path="Core",
        value=30,
        limit=12,
        limit_source="rule",
        severity="error",
        blocking=True,
        message="Core makes 30 references to Ui after the change, over the maximum of 12",
        hint="narrow the traffic to a single interface",
    )


def codecheck_finding() -> Finding:
    """A CodeCheck row: no ``entity``, no line, extra facts in ``details`` (task 4.7)."""
    return Finding(
        kind="codecheck",
        rule="codecheck.CPP_F016",
        scope="file",
        path=BUILTINS,
        line=None,
        limit_source="rule",
        severity="warning",
        preexisting=True,
        message="Function 'trim' has no explicit return type",
        details={"check_name": "Explicit return type", "column": 12, "entity": "text::trim"},
    )


def full_run(**extra: object) -> RunResult:
    """A run that fills every field of :class:`RunResult`, so the round trip proves coverage."""
    findings = [threshold_finding(), arch_finding(), codecheck_finding()]
    return RunResult(
        **extra,  # type: ignore[arg-type]
        tool_version="0.1.0",
        understand_version="Understand 7.0",
        repo_root="/repo",
        selection="staged",
        started_at="2026-01-01T09:00:00Z",
        seconds=1.5,
        effective_thresholds=[
            ThresholdSpec(scope="routine", metric="CyclomaticStrict", limit=Limit(max=10)),
            ThresholdSpec(
                scope="file",
                metric="RatioCommentToCode",
                limit=Limit(min=0.1),
                severity="warning",
                ratchet=False,
            ),
        ],
        findings=findings,
        ignored_counts={"routine": 2, "file": 1},
        unavailable_metrics={"Python": ["PercentLackOfCohesion"]},
        parse_errors=[ParseError(path=Path("src/util/text.cpp"), line=3, message="unknown token")],
        tightened=[TightenedLimit(rule="routine.CountLineCode", previous=80, current=72)],
        highest=[
            HighestValue(
                scope="routine", metric="CyclomaticStrict", value=20, entity=evaluate_ref()
            )
        ],
        analyzed_files=3,
        blocking_count=2,
        warning_count=1,
        preexisting_count=1,
    )


def empty_run() -> RunResult:
    """The run a change with nothing to report produces (req 4.9)."""
    return RunResult(
        tool_version="0.1.0",
        understand_version="Understand 7.0",
        repo_root="/repo",
        selection="staged",
        started_at="2026-01-01T09:00:00Z",
        seconds=0.2,
    )


def payload(result: RunResult) -> dict[str, object]:
    """The rendered document parsed back into a mapping."""
    parsed = json.loads(render_json(result))
    assert isinstance(parsed, dict)
    return parsed


def test_round_trips_into_an_equal_run_result() -> None:
    """The done criterion of task 5.2: nothing is dropped, coerced or reordered."""
    result = full_run()

    assert RunResult.model_validate(json.loads(render_json(result))) == result


def test_empty_run_round_trips_too() -> None:
    """A run with no findings is still a complete document, not an empty string."""
    result = empty_run()

    assert RunResult.model_validate(json.loads(render_json(result))) == result


def test_output_is_exactly_one_document() -> None:
    """Requirement 7.4: one JSON document and nothing else, to the last byte."""
    text = render_json(full_run())

    _, end = json.JSONDecoder().raw_decode(text)
    assert end == len(text)


def test_output_has_no_trailing_newline() -> None:
    """The renderer returns the document; the caller owns the line ending, as for human text."""
    text = render_json(full_run())

    assert not text.endswith("\n")
    assert text.startswith("{")


def test_output_is_indented_for_diffing() -> None:
    """Two-space indentation, so two runs of the gate diff line by line."""
    lines = render_json(full_run()).splitlines()

    assert lines[1].startswith('  "schema_version"')
    assert len(lines) > 20


def test_document_carries_everything_requirement_7_4_lists() -> None:
    """Run metadata, effective thresholds, findings, ignored counts, unavailable, parse errors."""
    document = payload(full_run())

    assert document["schema_version"] == 2
    assert document["tool_version"] == "0.1.0"
    assert document["understand_version"] == "Understand 7.0"
    assert document["repo_root"] == "/repo"
    assert document["selection"] == "staged"
    assert document["started_at"] == "2026-01-01T09:00:00Z"
    assert document["seconds"] == 1.5
    assert document["analyzed_files"] == 3
    assert document["effective_thresholds"] == [
        {
            "scope": "routine",
            "metric": "CyclomaticStrict",
            "limit": {"max": 10.0, "min": None},
            "severity": "error",
            "ratchet": True,
        },
        {
            "scope": "file",
            "metric": "RatioCommentToCode",
            "limit": {"max": None, "min": 0.1},
            "severity": "warning",
            "ratchet": False,
        },
    ]
    assert document["ignored_counts"] == {"routine": 2, "file": 1}
    assert document["unavailable_metrics"] == {"Python": ["PercentLackOfCohesion"]}
    assert document["parse_errors"] == [
        {"path": "src/util/text.cpp", "line": 3, "message": "unknown token"}
    ]
    assert document["tightened"] == [
        {"rule": "routine.CountLineCode", "previous": 80.0, "current": 72.0}
    ]
    assert document["blocking_count"] == 2
    assert document["warning_count"] == 1
    assert document["preexisting_count"] == 1


def test_findings_carry_every_field_of_requirement_7_1() -> None:
    """A finding in the document reads the same as the finding in the model."""
    document = payload(full_run())
    findings = document["findings"]
    assert isinstance(findings, list)

    assert findings[0] == {
        "kind": "threshold",
        "rule": "routine.CyclomaticStrict",
        "metric": "CyclomaticStrict",
        "scope": "routine",
        "entity": {
            "key": {
                "scope": "routine",
                "path": ENGINE,
                "longname": "engine.Engine.evaluate",
                "parameters": "",
            },
            "kind": "Python Function",
            "name": "evaluate",
            "line": 42,
        },
        "path": ENGINE,
        "line": 42,
        "value": 20.0,
        "before": 9.0,
        "limit": 10.0,
        "limit_source": "config",
        "severity": "error",
        "blocking": True,
        "preexisting": False,
        "message": "routine engine.Engine.evaluate CyclomaticStrict is 20, over the maximum of 10",
        "hint": "split the decision groups into named routines",
        "details": {},
    }


def test_codecheck_details_survive_the_round_trip() -> None:
    """The qualified name of a CodeCheck row lives in ``details`` and must not be lost."""
    document = payload(full_run())
    findings = document["findings"]
    assert isinstance(findings, list)

    assert findings[2]["details"] == {
        "check_name": "Explicit return type",
        "column": 12,
        "entity": "text::trim",
    }
    assert findings[2]["line"] is None
    assert findings[2]["path"] == BUILTINS


def test_findings_keep_the_order_the_run_produced() -> None:
    """The pipeline's order is the report's order; the renderer never re-sorts findings."""
    document = payload(full_run())
    findings = document["findings"]
    assert isinstance(findings, list)

    assert [finding["rule"] for finding in findings] == [
        "routine.CyclomaticStrict",
        "structure.coupling",
        "codecheck.CPP_F016",
    ]


def test_the_document_carries_each_sides_analysis_accuracy() -> None:
    """Requirement 7.1: the figure that says how much to trust everything else in here."""
    document = payload(full_run(accuracy={"after": 0.17, "before": 0.9}))

    assert document["accuracy"] == {"after": 0.17, "before": 0.9}


def test_a_run_with_no_figure_carries_an_empty_mapping_and_not_a_zero() -> None:
    """A 6.5 install reports none; a project that resolved nothing is a different claim."""
    assert payload(full_run())["accuracy"] == {}


def test_schema_version_is_the_first_key() -> None:
    """A consumer reading the head of the stream finds the version before anything else."""
    document = payload(full_run())

    assert next(iter(document)) == "schema_version"


def test_rendering_writes_nothing_to_the_streams(capsys: pytest.CaptureFixture[str]) -> None:
    """Requirement 7.7: the renderer returns text, it never prints (nor logs to stderr)."""
    render_json(full_run())

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_is_byte_identical_in_another_process() -> None:
    """Same run, fresh interpreter, different hash seed: the same bytes (diffable output)."""
    text = render_json(full_run())

    environment = {**os.environ, "PYTHONHASHSEED": "1"}
    completed = subprocess.run(
        [sys.executable, "-c", _REMOTE_RENDER],
        input=text,
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    )

    assert completed.stdout == text
