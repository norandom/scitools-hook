"""The net delta in the two machine formats (lean-code req 7.1, 7.4, 8.2).

SARIF carries it as a property of the run and JSON as a field of the document, and both say
"not measured" by leaving it out rather than by writing a zero. The human report of the same
figures is ``tests/report/test_lean_human.py``.

Two decisions are asserted here that neither format's own schema can state:

* **The delta is a run property, never a result.** It breaks no rule and blocks nothing, so a
  code-scanning platform must not be handed it as a finding to triage. The results and the
  rule descriptors are asserted to be byte-identical with and without it.
* **An absent delta declares nothing at all** (7.4). ``--all`` has no before side; SARIF omits
  the property and JSON writes ``null``, which is a field a consumer can tell apart from a
  measured zero.

The example travelling in ``details`` is asserted here as a *field* -- that whatever the
pipeline attached reaches the document -- so the value is a fixture rather than the
catalogue's own text. Whether the shipped example survives being rendered for a person is a
question about the human report, and ``test_lean_human.py`` asks it there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from fixtures import ENGINE
from jsonschema.validators import validator_for  # type: ignore[import-untyped]

from scitools_hook.models.change import NetDelta
from scitools_hook.models.findings import Finding, RunResult, structure_rule
from scitools_hook.report.json_out import render_json
from scitools_hook.report.sarif import render_sarif

SCHEMA_PATH: Final = Path(__file__).resolve().parent.parent / "fixtures" / "sarif-schema-2.1.0.json"

PASS_THROUGH: Final = structure_rule("pass_through")
SHRANK: Final = NetDelta(statements=-4, lines=-9, routines=3)
MOVED: Final = {"statements": -4, "lines": -9, "routines": 3}
"""The same delta as the wire sees it: three named integers, in the model's own field names."""

EXAMPLE: Final = "service.py:L20-22: yagni: one caller, one callee.\n\n  before  a()\n  after   b()"
"""A stand-in for whatever the pipeline attached, in the shape of a worked example."""


def run(
    *findings: Finding, net_delta: NetDelta | None = None, selection: str = "staged"
) -> RunResult:
    """One run carrying the findings and the delta a test is about, and nothing else."""
    return RunResult(
        tool_version="0.1.0",
        understand_version="Understand 8.0",
        repo_root="/repo",
        selection=selection,
        started_at="2026-01-01T09:00:00Z",
        seconds=1.5,
        findings=list(findings),
        net_delta=net_delta,
        blocking_count=sum(1 for finding in findings if finding.blocking),
    )


def lean_finding() -> Finding:
    """A pass-through finding as the pipeline hands it over: hint attached, example beside it."""
    return Finding(
        kind="structural",
        rule=PASS_THROUGH,
        scope="routine",
        path=ENGINE,
        line=20,
        severity="warning",
        message="engine.save_user forwards to repo.insert and has one caller",
        hint="yagni: delete save_user and call repo.insert from its one caller",
        details={"example": EXAMPLE},
    )


def sarif_run(result: RunResult) -> dict[str, Any]:
    """The document's single run, parsed back."""
    document = json.loads(render_sarif(result, "0.1.0"))
    assert isinstance(document, dict)
    one = document["runs"][0]
    assert isinstance(one, dict)
    return one


# --- SARIF: the delta as a run property, the results untouched (7.1) --------------


def test_the_sarif_run_carries_the_delta_as_a_run_property() -> None:
    assert sarif_run(run(net_delta=SHRANK))["properties"]["net_delta"] == MOVED


def test_a_run_without_a_delta_declares_no_run_property() -> None:
    assert "properties" not in sarif_run(run(selection="all"))


def test_the_delta_leaves_the_results_and_the_rules_alone() -> None:
    """A run-level addition: the same findings render to the same results either way."""
    without = sarif_run(run(lean_finding()))
    with_delta = sarif_run(run(lean_finding(), net_delta=SHRANK))

    assert with_delta["results"] == without["results"]
    assert with_delta["tool"] == without["tool"]


def test_a_document_carrying_the_delta_still_validates_against_the_schema() -> None:
    loaded = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    document = json.loads(render_sarif(run(lean_finding(), net_delta=SHRANK), "0.1.0"))

    validator_for(loaded)(loaded).validate(document)


# --- JSON: the delta as a field, the example beside the hint (7.1, 8.2) -----------


def test_the_json_document_carries_the_delta_as_a_field() -> None:
    assert json.loads(render_json(run(net_delta=SHRANK)))["net_delta"] == MOVED


def test_the_json_document_says_null_when_there_was_no_before_side() -> None:
    """The field is present and empty, so a consumer can tell "not measured" from zero."""
    assert json.loads(render_json(run(selection="all")))["net_delta"] is None


def test_the_json_document_carries_the_example_beside_the_hint() -> None:
    """Requirement 8.2 asks for the example in JSON at every verbosity, not only verbose."""
    finding = json.loads(render_json(run(lean_finding())))["findings"][0]

    assert finding["hint"].startswith("yagni:")
    assert finding["details"]["example"] == EXAMPLE
