"""Understand's own SARIF, written beside the Gate's by a real ``check`` run (2.1, 2.2, 2.4).

The seam supplies the document ``und analyze -sarif`` would have written, so this runs on any
machine; what is exercised is everything above it -- the feature record ``doctor`` stores, the
refusal that would stop a run whose build cannot honour the key, the re-rooting of the paths
onto the repository, the copy beside ``--sarif PATH``, and the report that names both files.

Three properties are each a way the feature could be green and wrong:

* **The companion is a second file, not a merge.** The Gate's own SARIF has to be exactly what
  it was before the key was turned on (requirement 2.2), and the check for that is a byte
  comparison against a run with the key off.
* **The paths are the repository's.** The document Understand writes names files under the
  shadow tree, which is a directory in the user's cache; uploaded unchanged, every result
  lands on a file no repository has.
* **A missing companion does not fail the run.** The exit code is the findings' (requirement
  2.4), and the reason is printed rather than raised.
"""

from __future__ import annotations

import json
from pathlib import Path

from e2e.harness import DEEP, NESTED, PARSE_ERROR_PATH, Workspace, report
from scitools_hook.exit_codes import ExitCode
from scitools_hook.models.understand import Availability, Feature, FeatureReport
from scitools_hook.understand.fake import DIAGNOSTICS_FILE, FIXTURE_VERSION
from scitools_hook.understand.features import FEATURES_FILE

ASKS_FOR_IT = "[understand]\nsarif = true\n"
"""The one key this feature ships off; requirement 1.3 makes turning it on deliberate."""

COMPANION = "gate.understand-analysis.sarif"
"""What the analysis companion is called beside a Gate SARIF written to ``gate.sarif``."""


def enabled(workspace: Workspace) -> None:
    """Record what the build offers, then turn the key on -- the operator's own order.

    A record first is not incidental: the key is refused without one for *this* build
    (requirement 1.2), which is the configuration check failing closed, and a run that never
    measured is a run that never promises a document it cannot produce.

    It is written here rather than by ``scitools-hook doctor`` because the fixture seam
    **refuses to probe** -- it runs no Understand at all, and a probe answering from fixtures
    would be measuring the fixtures (task 2.1). On a real install ``doctor`` writes exactly
    this file, and nothing downstream cares which of the two wrote it.
    """
    cache = Path(workspace.cli("db", "path").stdout.strip()).parent
    cache.mkdir(parents=True, exist_ok=True)
    measured = FeatureReport(
        build=FIXTURE_VERSION,
        features={
            feature: Availability(state="available", detail="recorded by the test")
            for feature in Feature
        },
    )
    (cache / FEATURES_FILE).write_text(measured.model_dump_json(indent=2), encoding="utf-8")
    workspace.write("scitools-hook.toml", ASKS_FOR_IT)


def checked(workspace: Workspace, *extra: str):
    """One ``check --worktree`` over an edit that breaks a rule."""
    workspace.write(DEEP, NESTED)
    return workspace.cli("check", "--worktree", *extra)


# --- the companion is written and named ------------------------------------------------


def test_a_run_with_sarif_writes_understands_document_beside_the_gates(
    workspace: Workspace, tmp_path: Path
) -> None:
    """Requirement 2.1: one upload step, two files, told apart by their names."""
    enabled(workspace)
    gate = tmp_path / "out" / "gate.sarif"
    gate.parent.mkdir()

    done = checked(workspace, "--sarif", str(gate))

    assert done.returncode == int(ExitCode.VIOLATIONS), done.stderr
    assert gate.is_file()
    companion = gate.with_name(COMPANION)
    assert companion.is_file(), sorted(path.name for path in gate.parent.iterdir())
    assert str(companion) in done.stdout, "the run has to name what it wrote"


def test_the_companion_names_the_repositorys_files_and_not_the_shadows(
    workspace: Workspace, tmp_path: Path
) -> None:
    """The document Understand writes is relative to the cache; an upload needs the repo."""
    enabled(workspace)
    gate = tmp_path / "gate.sarif"

    checked(workspace, "--sarif", str(gate))

    document = json.loads(gate.with_name(COMPANION).read_text(encoding="utf-8"))
    run = document["runs"][0]
    assert run["artifacts"][0]["location"]["uri"] == PARSE_ERROR_PATH
    base = run["originalUriBaseIds"]["UND_PROJECT"]["uri"]
    assert base == f"{workspace.root.resolve().as_uri()}/"


def test_understands_own_statement_is_carried_over_unchanged(
    workspace: Workspace, tmp_path: Path
) -> None:
    """The tool, its rules and its results are Understand's; only the paths are rewritten."""
    enabled(workspace)
    gate = tmp_path / "gate.sarif"

    checked(workspace, "--sarif", str(gate))

    run = json.loads(gate.with_name(COMPANION).read_text(encoding="utf-8"))["runs"][0]
    assert run["tool"]["driver"]["name"] == "Analysis"
    assert run["results"][0]["ruleId"] == "UND_ERROR"
    assert run["results"][0]["message"]["text"] == "unexpected indent"


def test_the_json_document_lists_the_companion_it_wrote(
    workspace: Workspace, tmp_path: Path
) -> None:
    """Requirement 7.4's document is what CI reads; the companion has to be in it."""
    enabled(workspace)
    gate = tmp_path / "gate.sarif"

    done = checked(workspace, "--sarif", str(gate), "--format", "json")

    document = report(done)
    assert document["schema_version"] == 2
    listed = document["understand_sarif"]
    assert isinstance(listed, list)
    analysis = [one for one in listed if one["kind"] == "analysis"]
    assert analysis and analysis[0]["written"] == str(gate.with_name(COMPANION))
    assert analysis[0]["problem"] == ""


# --- the Gate's own SARIF is untouched ---------------------------------------------------


def test_the_gates_own_sarif_is_byte_identical_with_the_key_on_and_off(
    workspace: Workspace, tmp_path: Path
) -> None:
    """Requirement 2.2: nothing moves between the two documents and nothing is duplicated."""
    off = tmp_path / "off.sarif"
    checked(workspace, "--sarif", str(off))
    without = off.read_bytes()

    enabled(workspace)
    on = tmp_path / "on.sarif"
    checked(workspace, "--sarif", str(on))

    assert on.read_bytes() == without
    assert not off.with_name("off.understand-analysis.sarif").exists(), (
        "a run with the key off writes no companion at all"
    )


# --- what goes wrong ----------------------------------------------------------------------


def test_a_companion_source_that_is_gone_is_reported_and_does_not_change_the_exit_code(
    workspace: Workspace, tmp_path: Path
) -> None:
    """Requirement 2.4: the Gate's own SARIF is the deliverable and these are extra.

    The fixture's document is removed, which is a build that was asked for one and wrote
    none. The run still reports its findings, still writes the Gate's SARIF, and exits on the
    findings alone.
    """
    enabled(workspace)
    fixture = Path(workspace.env["SCITOOLS_HOOK_FAKE_UNDERSTAND"]) / DIAGNOSTICS_FILE
    kept = fixture.read_bytes()
    fixture.unlink()
    try:
        gate = tmp_path / "gate.sarif"
        done = checked(workspace, "--sarif", str(gate), "--format", "json")
    finally:
        fixture.write_bytes(kept)

    assert done.returncode == int(ExitCode.VIOLATIONS), done.stderr
    assert gate.is_file()
    listed = report(done)["understand_sarif"]
    assert isinstance(listed, list)
    assert [one["kind"] for one in listed] == ["analysis"]
    assert listed[0]["written"] is None
    assert "no diagnostics" in listed[0]["problem"]


def test_a_run_without_sarif_prepares_the_companion_and_says_where_it_is(
    workspace: Workspace,
) -> None:
    """The document exists whether or not anything asked for it; the run says so rather than
    writing a file into a directory nobody named."""
    enabled(workspace)

    done = checked(workspace, "--format", "json")

    listed = report(done)["understand_sarif"]
    assert isinstance(listed, list)
    assert listed[0]["written"] is None
    assert listed[0]["source"] is not None
    assert listed[0]["problem"] == ""
