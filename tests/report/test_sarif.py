"""The SARIF view of a run, checked against the official 2.1.0 schema.

``tests/fixtures/sarif-schema-2.1.0.json`` is the OASIS schema, downloaded verbatim from
``https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json``
(sha256 ``ad6db49878699b091f3eeb765b6e29e92a34bad4da88664d000c923b549c3a25``, CRLF endings
and all, so the copy can be diffed against the published file). Task 5.2's done criterion is
that every document this renderer produces validates against it, which is why the fixture is
a file rather than a hand-written subset: a schema written by the test would only ever assert
what the renderer already does.

Schema validity is necessary but not sufficient -- SARIF accepts almost any property bag --
so the rest of these tests pin the decisions the schema cannot see:

* **``%SRCROOT%``.** In-repo paths are repo-relative URIs against the base id declared in
  ``run.originalUriBaseIds``; a path *outside* the repository (task 4.7: Understand analyses
  its own ``builtins.py`` stub) has no relative form, so it becomes an absolute ``file://``
  URI with no ``uriBaseId`` at all.
* **``startLine`` >= 1.** ``line=None`` (CodeCheck's line 0, and every fan finding) means the
  region is omitted, never emitted as ``startLine: 0``, which the SARIF spec forbids.
* **Architecture nodes are not artifacts.** ``Finding.path`` on an arch-scope finding is an
  architecture node path (task 4.3); it is emitted as a ``logicalLocation``, never as an
  ``artifactLocation`` pointing at a file that does not exist.
* **Entities from ``details``.** A CodeCheck finding leaves ``Finding.entity`` empty and
  carries the qualified name in ``details["entity"]`` (task 4.7).
* **Levels.** ``error``/``warning`` map through, and a pre-existing finding is a ``note``
  whatever its severity, so a SARIF consumer sees the same triage the human output shows.
* **Rules.** One ``reportingDescriptor`` per distinct ``Finding.rule``, sorted by id, with
  every ``ruleIndex`` pointing at the descriptor whose id is the result's ``ruleId``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest

# jsonschema ships no type information and ``types-jsonschema`` is not a dev dependency.
from jsonschema.validators import validator_for  # type: ignore[import-untyped]

from scitools_hook.models.findings import Finding, RunResult
from scitools_hook.models.snapshot import EntityKey, EntityRef
from scitools_hook.report.sarif import (
    SARIF_SCHEMA_URI,
    SARIF_VERSION,
    SRCROOT,
    TOOL_NAME,
    render_sarif,
)

ENGINE: Final = "src/analysis/engine.py"
APP: Final = "src/cli/app.py"
SPACED: Final = "src/legacy code/old parser.cpp"
BUILTINS: Final = "/opt/scitools/conf/understand/python/python3/builtins.py"
REPO_ROOT: Final = "/home/dev/repo"
TOOL_VERSION: Final = "1.4.2"

SCHEMA_PATH: Final = Path(__file__).resolve().parent.parent / "fixtures" / "sarif-schema-2.1.0.json"

_REMOTE_RENDER: Final = """\
import json, sys
from scitools_hook.models.findings import RunResult
from scitools_hook.report.sarif import render_sarif

run = RunResult.model_validate(json.loads(sys.stdin.read()))
sys.stdout.write(render_sarif(run, "1.4.2"))
"""
"""Rebuild the run from JSON in a fresh interpreter and render it again (determinism)."""


def schema() -> dict[str, Any]:
    """The official SARIF 2.1.0 schema, loaded from the fixture."""
    loaded = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def routine_ref(longname: str, path: str, line: int) -> EntityRef:
    """The entity reference a routine-scope finding carries."""
    key = EntityKey(scope="routine", path=path, longname=longname, parameters="")
    return EntityRef(key=key, kind="Python Function", name=longname.rpartition(".")[2], line=line)


def file_ref(path: str) -> EntityRef:
    """A file-scope reference, whose longname is the path itself."""
    key = EntityKey(scope="file", path=path, longname=path)
    return EntityRef(key=key, kind="File", name=path.rpartition("/")[2], line=1)


def cyclomatic() -> Finding:
    """A blocking metric threshold on a routine, inside the repository, with a hint."""
    return Finding(
        kind="threshold",
        rule="routine.CyclomaticStrict",
        metric="CyclomaticStrict",
        scope="routine",
        entity=routine_ref("engine.Engine.evaluate", ENGINE, 42),
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


def second_cyclomatic() -> Finding:
    """A second finding of the *same* rule: the rules array must still hold one descriptor."""
    return Finding(
        kind="threshold",
        rule="routine.CyclomaticStrict",
        metric="CyclomaticStrict",
        scope="routine",
        entity=routine_ref("app.build_parser", APP, 12),
        path=APP,
        line=12,
        value=12,
        limit=10,
        severity="error",
        blocking=True,
        message="routine app.build_parser CyclomaticStrict is 12, over the maximum of 10",
        hint="split the decision groups into named routines",
    )


def comment_ratio() -> Finding:
    """A warning whose entity longname merely repeats the file path."""
    return Finding(
        kind="threshold",
        rule="file.RatioCommentToCode",
        metric="RatioCommentToCode",
        scope="file",
        entity=file_ref(ENGINE),
        path=ENGINE,
        line=1,
        value=0.05,
        limit=0.1,
        severity="warning",
        message="file src/analysis/engine.py RatioCommentToCode is 0.05, under the minimum 0.1",
        hint="document why the module exists",
    )


def preexisting_lines() -> Finding:
    """An ``error`` that was already true before the change: SARIF sees a ``note``."""
    return Finding(
        kind="threshold",
        rule="file.CountLineCode",
        metric="CountLineCode",
        scope="file",
        entity=file_ref(APP),
        path=APP,
        line=1,
        value=700,
        before=700,
        limit=500,
        limit_source="baseline",
        severity="error",
        preexisting=True,
        message="file src/cli/app.py CountLineCode is 700, over the maximum of 500",
        hint="move a cohesive group of functions into a new module",
    )


def coupling() -> Finding:
    """An architecture-node finding: its path is a node, not a file (task 4.3)."""
    return Finding(
        kind="structural",
        rule="structure.coupling",
        scope="arch",
        path="Core/Analysis",
        value=30,
        limit=12,
        limit_source="rule",
        severity="error",
        blocking=True,
        message="Core/Analysis makes 30 references to Ui, over the maximum of 12",
        hint="narrow the traffic to a single interface",
    )


def codecheck_outside_repo() -> Finding:
    """A CodeCheck row on a file the repository does not own, with no line (task 4.7)."""
    return Finding(
        kind="codecheck",
        rule="codecheck.CPP_F016",
        scope="file",
        path=BUILTINS,
        line=None,
        limit_source="rule",
        severity="warning",
        message="Function 'trim' has no explicit return type",
        details={"check_name": "Explicit return type", "column": 12, "entity": "text::trim"},
    )


def codecheck_line_zero() -> Finding:
    """A row whose line survived as 0: SARIF must not be handed ``startLine: 0``."""
    return Finding(
        kind="codecheck",
        rule="codecheck.CPP_F016",
        scope="file",
        path=SPACED,
        line=0,
        limit_source="rule",
        severity="warning",
        message="file-level violation with no line",
        details={"check_name": "Explicit return type"},
    )


def codecheck_at(path: str) -> Finding:
    """A CodeCheck row on ``path``, to pin how one path shape becomes one URI."""
    return Finding(
        kind="codecheck",
        rule="codecheck.CPP_F016",
        scope="file",
        path=path,
        line=7,
        limit_source="rule",
        severity="warning",
        message="Function 'trim' has no explicit return type",
    )


def project_average() -> Finding:
    """A population threshold: no entity and no path at all, so no location either."""
    return Finding(
        kind="threshold",
        rule="project.AVG:CyclomaticStrict",
        metric="AVG:CyclomaticStrict",
        scope="project",
        path="",
        value=4.5,
        limit=3,
        severity="error",
        blocking=True,
        message="project AVG:CyclomaticStrict is 4.5, over the maximum of 3",
        hint="fix the worst routines first",
    )


def fan_out() -> Finding:
    """A structural finding with no line and no hint attached: its rule gets no ``help``."""
    return Finding(
        kind="structural",
        rule="structure.fan_out",
        scope="file",
        path=APP,
        line=None,
        value=9,
        limit=6,
        limit_source="rule",
        severity="warning",
        message="src/cli/app.py depends on 9 files, over the maximum of 6",
    )


def run(*findings: Finding) -> RunResult:
    """A ``RunResult`` around ``findings`` with the counts the pipeline would have filled in."""
    return RunResult(
        tool_version="0.1.0",
        understand_version="Understand 7.0",
        repo_root=REPO_ROOT,
        selection="staged",
        started_at="2026-01-01T09:00:00Z",
        seconds=1.5,
        findings=list(findings),
        analyzed_files=4,
        blocking_count=sum(1 for finding in findings if finding.blocking),
        warning_count=sum(1 for finding in findings if finding.severity == "warning"),
        preexisting_count=sum(1 for finding in findings if finding.preexisting),
    )


def mixed_run() -> RunResult:
    """Every shape the renderer has to decide about, in one run."""
    return run(
        cyclomatic(),
        second_cyclomatic(),
        comment_ratio(),
        preexisting_lines(),
        coupling(),
        codecheck_outside_repo(),
        codecheck_line_zero(),
        project_average(),
        fan_out(),
    )


def document(result: RunResult) -> dict[str, Any]:
    """The rendered SARIF document, parsed."""
    parsed = json.loads(render_sarif(result, TOOL_VERSION))
    assert isinstance(parsed, dict)
    return parsed


def sole_run(result: RunResult) -> dict[str, Any]:
    """The one ``run`` object of the rendered document."""
    runs = document(result)["runs"]
    assert len(runs) == 1
    return dict(runs[0])


def results_of(result: RunResult) -> list[dict[str, Any]]:
    """Every SARIF result of the rendered document."""
    return list(sole_run(result)["results"])


def rules_of(result: RunResult) -> list[dict[str, Any]]:
    """The rule descriptors of the rendered document, in the order they were written."""
    return list(sole_run(result)["tool"]["driver"]["rules"])


def one_result(result: RunResult, finding: Finding) -> dict[str, Any]:
    """The single SARIF result produced for a run holding only ``finding``."""
    results = results_of(result)
    assert len(results) == 1
    assert results[0]["ruleId"] == finding.rule
    return results[0]


def sole_location(finding: Finding) -> dict[str, Any]:
    """The one location of the one result of a run holding only ``finding``."""
    result = run(finding)
    locations = one_result(result, finding)["locations"]
    assert len(locations) == 1
    return dict(locations[0])


def test_schema_fixture_is_the_published_sarif_2_1_0_schema() -> None:
    """A corrupted or home-made fixture would make every validation below meaningless."""
    loaded = schema()
    validator = validator_for(loaded)

    validator.check_schema(loaded)
    assert loaded["$id"].endswith("sarif-schema-2.1.0.json")
    assert loaded["title"].startswith("Static Analysis Results Format (SARIF) Version 2.1.0")


@pytest.mark.parametrize(
    "result",
    [mixed_run(), run(), run(codecheck_line_zero()), run(coupling()), run(project_average())],
    ids=["mixed", "empty", "line-zero", "architecture", "project"],
)
def test_document_validates_against_the_sarif_schema(result: RunResult) -> None:
    """Task 5.2's done criterion, over every shape the renderer decides about."""
    loaded = schema()
    validator = validator_for(loaded)(loaded)

    errors = [
        f"{list(error.absolute_path)}: {error.message}"
        for error in validator.iter_errors(document(result))
    ]

    assert errors == []


def test_document_declares_version_and_schema() -> None:
    """SARIF 2.1.0, named by the ``$schema`` URL the design fixes."""
    rendered = document(mixed_run())

    assert rendered["version"] == SARIF_VERSION == "2.1.0"
    assert rendered["$schema"] == SARIF_SCHEMA_URI
    assert SARIF_SCHEMA_URI == (
        "https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json"
    )


def test_driver_names_the_tool_and_the_version_it_was_given() -> None:
    """The driver version is the caller's, not a constant baked into the renderer."""
    driver = sole_run(mixed_run())["tool"]["driver"]

    assert driver["name"] == TOOL_NAME == "scitools-hook"
    assert driver["version"] == TOOL_VERSION


def test_srcroot_is_declared_as_a_directory_uri() -> None:
    """``%SRCROOT%`` resolves to the repository root as a ``file://`` URI ending in a slash."""
    base_ids = sole_run(mixed_run())["originalUriBaseIds"]

    assert base_ids == {SRCROOT: {"uri": f"file://{REPO_ROOT}/"}}


def test_a_windows_repository_root_declares_a_usable_base_uri() -> None:
    """``%SRCROOT%`` must use URI separators, whatever the platform wrote the root with.

    Every in-repo ``artifactLocation`` in the document is relative to this one base, so a
    root left as ``C:\\dev\\repo`` would percent-encode its backslashes and make every
    location in the file unresolvable (req 7.5).
    """
    result = run(cyclomatic()).model_copy(update={"repo_root": r"C:\dev\repo"})

    base_ids = sole_run(result)["originalUriBaseIds"]

    assert base_ids == {SRCROOT: {"uri": "file:///C:/dev/repo/"}}


def test_one_rule_per_distinct_finding_rule_sorted_by_id() -> None:
    """Requirement 7.5: one rule per metric or structural rule, however many findings share it."""
    rules = rules_of(mixed_run())

    assert [rule["id"] for rule in rules] == [
        "codecheck.CPP_F016",
        "file.CountLineCode",
        "file.RatioCommentToCode",
        "project.AVG:CyclomaticStrict",
        "routine.CyclomaticStrict",
        "structure.coupling",
        "structure.fan_out",
    ]


def test_every_rule_index_points_at_the_rule_of_its_result() -> None:
    """A wrong index silently relabels a finding in every SARIF viewer."""
    result = mixed_run()
    rules = rules_of(result)

    for sarif_result in results_of(result):
        index = sarif_result["ruleIndex"]
        assert 0 <= index < len(rules)
        assert rules[index]["id"] == sarif_result["ruleId"]


def test_findings_sharing_a_rule_share_its_index() -> None:
    """The two ``routine.CyclomaticStrict`` findings point at the same single descriptor."""
    result = mixed_run()
    indexes = {
        sarif_result["ruleIndex"]
        for sarif_result in results_of(result)
        if sarif_result["ruleId"] == "routine.CyclomaticStrict"
    }

    assert len(indexes) == 1
    assert sum(1 for rule in rules_of(result) if rule["id"] == "routine.CyclomaticStrict") == 1


def test_one_result_per_finding_in_the_order_the_run_produced() -> None:
    """Requirement 7.5: one result per finding; the renderer never re-sorts or merges them."""
    result = mixed_run()

    assert [sarif_result["ruleId"] for sarif_result in results_of(result)] == [
        finding.rule for finding in result.findings
    ]


def test_rule_help_carries_the_remediation_hint() -> None:
    """Requirement 7.2's text must reach a SARIF consumer, not only the human output."""
    rules = {rule["id"]: rule for rule in rules_of(mixed_run())}

    assert rules["routine.CyclomaticStrict"]["help"] == {
        "text": "split the decision groups into named routines"
    }


def test_rule_without_an_attached_hint_has_no_help() -> None:
    """An empty hint is not rendered as empty help text; the key is simply absent."""
    rules = {rule["id"]: rule for rule in rules_of(mixed_run())}

    assert "help" not in rules["structure.fan_out"]


def test_rule_descriptions_say_what_kind_of_rule_it_is() -> None:
    """The short description names the scope and metric, the structural rule, or the check."""
    rules = {rule["id"]: rule["shortDescription"]["text"] for rule in rules_of(mixed_run())}

    assert rules["routine.CyclomaticStrict"] == "routine metric CyclomaticStrict"
    assert rules["project.AVG:CyclomaticStrict"] == "project metric AVG:CyclomaticStrict"
    assert rules["structure.coupling"] == "structural rule coupling"
    assert rules["codecheck.CPP_F016"] == "CodeCheck check CPP_F016: Explicit return type"


def test_codecheck_rule_description_falls_back_to_the_check_id() -> None:
    """A row that carried no check name still gets a description naming the check."""
    bare = Finding(
        kind="codecheck",
        rule="codecheck.CPP_F016",
        scope="file",
        path=APP,
        line=4,
        limit_source="rule",
        severity="warning",
        message="Function 'trim' has no explicit return type",
    )

    assert rules_of(run(bare))[0]["shortDescription"] == {"text": "CodeCheck check CPP_F016"}


@pytest.mark.parametrize(
    ("finding", "level"),
    [
        (cyclomatic(), "error"),
        (comment_ratio(), "warning"),
        (preexisting_lines(), "note"),
    ],
    ids=["error", "warning", "preexisting-error-is-a-note"],
)
def test_severity_maps_to_the_sarif_level(finding: Finding, level: str) -> None:
    """``error``/``warning`` pass through; anything pre-existing is reported as a note."""
    assert one_result(run(finding), finding)["level"] == level


def test_message_is_the_finding_message() -> None:
    """The text a human reads and the text a SARIF consumer reads are the same sentence."""
    finding = cyclomatic()

    assert one_result(run(finding), finding)["message"] == {"text": finding.message}


def test_in_repo_path_is_relative_to_srcroot() -> None:
    """Requirement 7.1's repo-relative path, expressed the way SARIF expresses it."""
    location = sole_location(cyclomatic())

    assert location["physicalLocation"]["artifactLocation"] == {
        "uri": ENGINE,
        "uriBaseId": SRCROOT,
    }
    assert location["physicalLocation"]["region"] == {"startLine": 42}


def test_path_outside_the_repository_becomes_an_absolute_file_uri() -> None:
    """Task 4.7: such a path has no ``%SRCROOT%`` form, so it must not claim one."""
    artifact = sole_location(codecheck_outside_repo())["physicalLocation"]["artifactLocation"]

    assert artifact == {"uri": f"file://{BUILTINS}"}
    assert "uriBaseId" not in artifact


def test_windows_path_outside_the_repository_keeps_its_drive_letter() -> None:
    """Task 4.7 keeps such a path absolute on Windows too; ``C:`` is not a relative segment."""
    finding = codecheck_at(r"C:\scitools\conf\understand\python\python3\builtins.py")

    artifact = sole_location(finding)["physicalLocation"]["artifactLocation"]

    assert artifact == {"uri": "file:///C:/scitools/conf/understand/python/python3/builtins.py"}


def test_native_separators_become_uri_separators() -> None:
    """A URI path is separated by slashes; a backslash in one is a character, not a separator."""
    finding = codecheck_at(r"src\cli\app.py")

    artifact = sole_location(finding)["physicalLocation"]["artifactLocation"]

    assert artifact == {"uri": "src/cli/app.py", "uriBaseId": SRCROOT}


def test_uris_are_percent_encoded() -> None:
    """A space in a path is not a valid URI character; SARIF asks for a URI, not a path."""
    artifact = sole_location(codecheck_line_zero())["physicalLocation"]["artifactLocation"]

    assert artifact == {"uri": "src/legacy%20code/old%20parser.cpp", "uriBaseId": SRCROOT}


def test_missing_line_omits_the_region() -> None:
    """``line=None`` means "somewhere in this file", which SARIF spells by leaving it out."""
    physical = sole_location(fan_out())["physicalLocation"]

    assert "region" not in physical


@pytest.mark.parametrize("line", [0, -1])
def test_a_line_below_one_never_becomes_a_region(line: int) -> None:
    """``startLine`` is 1-based in SARIF; anything below 1 drops the region instead."""
    physical = sole_location(codecheck_line_zero().model_copy(update={"line": line}))[
        "physicalLocation"
    ]

    assert "region" not in physical


def test_architecture_node_is_a_logical_location_not_a_file() -> None:
    """Task 4.3: an architecture node is not an artifact and must not be reported as one."""
    location = sole_location(coupling())

    assert "physicalLocation" not in location
    assert location["logicalLocations"] == [{"fullyQualifiedName": "Core/Analysis"}]


def test_codecheck_entity_comes_from_details() -> None:
    """Task 4.7: ``Finding.entity`` is empty for a CodeCheck row; the name is in ``details``."""
    location = sole_location(codecheck_outside_repo())

    assert location["logicalLocations"] == [{"fullyQualifiedName": "text::trim"}]


def test_routine_entity_becomes_a_logical_location() -> None:
    """The qualified name requirement 7.1 asks for has a home of its own in SARIF."""
    location = sole_location(cyclomatic())

    assert location["logicalLocations"] == [{"fullyQualifiedName": "engine.Engine.evaluate"}]


def test_entity_that_only_repeats_the_path_is_not_a_logical_location() -> None:
    """A file-scope longname *is* the path; repeating it as a logical location says nothing."""
    location = sole_location(comment_ratio())

    assert "logicalLocations" not in location
    assert location["physicalLocation"]["artifactLocation"]["uri"] == ENGINE


def test_finding_without_a_path_has_no_location() -> None:
    """A project-wide finding is about no artifact at all, which SARIF spells as an empty list."""
    finding = project_average()

    assert one_result(run(finding), finding)["locations"] == []


def test_properties_keep_the_fields_sarif_has_no_home_for() -> None:
    """Nothing a finding carries is lost: the JSON output and SARIF agree on every number."""
    finding = preexisting_lines()

    assert one_result(run(finding), finding)["properties"] == {
        "kind": "threshold",
        "scope": "file",
        "metric": "CountLineCode",
        "value": 700.0,
        "before": 700.0,
        "limit": 500.0,
        "limit_source": "baseline",
        "blocking": False,
        "preexisting": True,
    }


def test_properties_omit_the_measurements_a_finding_does_not_have() -> None:
    """A structural rule measures nothing against a limit it can name; no null noise for it."""
    finding = coupling()

    properties = one_result(run(finding), finding)["properties"]

    assert properties == {
        "kind": "structural",
        "scope": "arch",
        "value": 30.0,
        "limit": 12.0,
        "limit_source": "rule",
        "blocking": True,
        "preexisting": False,
    }
    assert "metric" not in properties
    assert "before" not in properties


def test_properties_carry_the_details_bag_verbatim() -> None:
    """``details`` holds the column and the check name; SARIF must not drop them."""
    finding = codecheck_outside_repo()

    properties = one_result(run(finding), finding)["properties"]

    assert properties["details"] == {
        "check_name": "Explicit return type",
        "column": 12,
        "entity": "text::trim",
    }


def test_properties_omit_an_empty_details_bag() -> None:
    """Most findings carry no details; an empty object in every result would be noise."""
    finding = cyclomatic()

    assert "details" not in one_result(run(finding), finding)["properties"]


def test_empty_run_is_a_valid_empty_sarif_run() -> None:
    """A clean run still produces the document a CI upload step expects, with no results."""
    rendered = sole_run(run())

    assert rendered["results"] == []
    assert rendered["tool"]["driver"]["rules"] == []
    assert rendered["originalUriBaseIds"] == {SRCROOT: {"uri": f"file://{REPO_ROOT}/"}}


def test_output_is_exactly_one_document_without_a_trailing_newline() -> None:
    """As for JSON: the renderer returns one document and the caller owns the line ending."""
    text = render_sarif(mixed_run(), TOOL_VERSION)

    _, end = json.JSONDecoder().raw_decode(text)
    assert end == len(text)
    assert not text.endswith("\n")


def test_rendering_writes_nothing_to_the_streams(capsys: pytest.CaptureFixture[str]) -> None:
    """Requirement 7.7: the renderer returns text, it never prints (nor logs to stderr)."""
    render_sarif(mixed_run(), TOOL_VERSION)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_is_byte_identical_in_another_process() -> None:
    """Same run, fresh interpreter, different hash seed: the same bytes."""
    result = mixed_run()
    text = render_sarif(result, TOOL_VERSION)

    completed = subprocess.run(
        [sys.executable, "-c", _REMOTE_RENDER],
        input=result.model_dump_json(),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": "1"},
        check=True,
    )

    assert completed.stdout == text
