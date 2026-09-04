"""The ``explain`` command: range parsing, review aids, the three views (task 9.2).

``explain`` describes a change rather than judging it, so it has no verdict and no exit code
of its own: success is 0 and every failure is a typed error ``GateGroup`` maps. What is
tested here is the grammar around that -- which target the pipeline is pointed at, which aids
it is asked for, and which of requirement 9.6's three views reaches which destination.

The double answers *from* what it was asked (``fakes.cli``): the summary it returns quotes
the target and the aids, so a command that drops ``--impact`` changes the rendered document.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes.cli import StubAssembler, a_summary, describe
from typer.testing import CliRunner

from scitools_hook.cli import app as app_module
from scitools_hook.cli import common, pipelines
from scitools_hook.cli import explain as explain_module
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.exit_codes import ExitCode
from scitools_hook.report.markdown import render_summary
from scitools_hook.runner.explain import CommitRange, ExplainOptions
from scitools_hook.runner.pipeline import Selection


@pytest.fixture
def assembler(monkeypatch: pytest.MonkeyPatch) -> StubAssembler:
    """Replace the real assembly, so no git repository and no Understand are needed."""
    stub = StubAssembler()
    monkeypatch.setattr(pipelines, "assemble", stub)
    return stub


def run(*args: str) -> object:
    """Invoke the real application with ``args``."""
    return CliRunner().invoke(app_module.app, list(args))


# --- the help documents every option (req 12.1) -----------------------------------


EXPLAIN_OPTIONS = (
    "--staged",
    "--worktree",
    "--all",
    "--files",
    "--format",
    "--output",
    "--range",
    "--graphs",
    "--impact",
    "--out",
)


@pytest.mark.parametrize("option", EXPLAIN_OPTIONS)
def test_the_help_documents_every_option_explain_accepts(option: str) -> None:
    result = run("explain", "--help")
    assert result.exit_code == 0
    assert option in result.stdout


def test_explain_offers_exactly_the_views_the_requirement_names() -> None:
    """Requirement 9.6: text, Markdown and JSON. SARIF is a findings format, not a summary."""
    offered = {member.value for member in explain_module.ExplainFormat}
    assert offered == {"human", "json", "markdown"}
    assert offered < {member.value for member in common.OutputFormat}


def test_a_format_explain_cannot_render_is_refused(assembler: StubAssembler) -> None:
    result = run("explain", "--all", "--format", "sarif")
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert assembler.assembly.explain_pipeline.targets == []


# --- the target: a selection, or a commit range (req 9.1, 12.3) -------------------


SELECTIONS = (
    pytest.param(["--staged"], Selection(mode="staged"), id="staged"),
    pytest.param(["--worktree"], Selection(mode="worktree"), id="worktree"),
    pytest.param(["--all"], Selection(mode="all"), id="all"),
    pytest.param(["--files", "a.py"], Selection(mode="files", files=["a.py"]), id="files"),
)


@pytest.mark.parametrize(("argv", "expected"), SELECTIONS)
def test_the_selection_the_options_name_is_the_one_explained(
    assembler: StubAssembler, argv: list[str], expected: Selection
) -> None:
    result = run("explain", *argv)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert assembler.assembly.explain_pipeline.targets == [expected]


def test_a_range_is_parsed_into_the_two_commits_it_names(assembler: StubAssembler) -> None:
    result = run("explain", "--range", "v1.0..HEAD")
    assert result.exit_code == 0, result.stderr
    assert assembler.assembly.explain_pipeline.targets == [CommitRange(base="v1.0", head="HEAD")]


def test_a_three_dot_range_reaches_the_pipeline_asking_for_the_merge_base(
    assembler: StubAssembler,
) -> None:
    """``A...B`` is what reviewing a branch means, and it used to be refused by name.

    ``git diff A...B`` is ``merge-base(A, B)..B`` -- what this branch did, without the commits
    main gathered meanwhile. It is what a pull request shows and what this project's own
    documentation told people to type, so it was the one form a reviewer reaches for first
    and the one form that failed.
    """
    result = run("explain", "--range", "main...HEAD")

    assert result.exit_code == int(ExitCode.OK)
    (target,) = assembler.assembly.explain_pipeline.targets
    assert (target.base, target.head, target.from_merge_base) == ("main", "HEAD", True)


def test_a_range_that_is_not_a_range_is_refused_before_anything_is_assembled(
    assembler: StubAssembler,
) -> None:
    result = run("explain", "--range", "HEAD")
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert assembler.overrides == []


def test_a_range_and_a_selection_flag_name_two_different_changes(
    assembler: StubAssembler,
) -> None:
    result = run("explain", "--range", "a..b", "--staged")
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert "--range" in result.stderr
    assert "--staged" in result.stderr


CONFLICTING = (
    pytest.param(["--staged"], "--staged", id="staged"),
    pytest.param(["--worktree"], "--worktree", id="worktree"),
    pytest.param(["--all"], "--all", id="all"),
    pytest.param(["--files", "a.py"], "--files", id="files"),
)


@pytest.mark.parametrize(("argv", "named"), CONFLICTING)
def test_the_conflict_names_the_selection_flag_that_was_actually_given(
    assembler: StubAssembler, argv: list[str], named: str
) -> None:
    """One message for four flags is a message that is wrong three times out of four."""
    result = run("explain", "--range", "a..b", *argv)
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert named in result.stderr


def test_every_selection_mode_names_the_flag_the_shared_module_declares() -> None:
    """``flag_of`` derives the spelling; this is what makes the derivation a fact."""
    derived = {explain_module.flag_of(mode) for mode in common.SelectionMode}
    assert derived == set(common.SELECTION_FLAGS.values())


def test_a_range_and_trailing_paths_are_the_same_conflict(assembler: StubAssembler) -> None:
    """Trailing bare paths are ``--files`` by another spelling, so they conflict too."""
    result = run("explain", "--range", "a..b", "one.py")
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "--range" in result.stderr


# --- the review aids (req 9.4, 9.5) -----------------------------------------------


def test_no_aid_is_requested_unless_it_is_asked_for(assembler: StubAssembler) -> None:
    run("explain", "--all")
    assert assembler.assembly.explain_pipeline.options == [ExplainOptions()]


def test_graphs_and_impact_reach_the_pipeline(assembler: StubAssembler, tmp_path: Path) -> None:
    out = tmp_path / "graphs"
    run("explain", "--all", "--graphs", "--impact", "--out", str(out))
    assert assembler.assembly.explain_pipeline.options == [
        ExplainOptions(graphs=True, impact=True, out_dir=out)
    ]


def test_impact_alone_asks_for_no_graphs(assembler: StubAssembler) -> None:
    run("explain", "--all", "--impact")
    assert assembler.assembly.explain_pipeline.options == [ExplainOptions(impact=True)]


def test_graphs_without_a_directory_let_the_pipeline_choose_the_cache(
    assembler: StubAssembler,
) -> None:
    run("explain", "--all", "--graphs")
    assert assembler.assembly.explain_pipeline.options == [ExplainOptions(graphs=True)]


def test_a_graph_directory_without_graphs_is_refused_rather_than_ignored(
    assembler: StubAssembler,
) -> None:
    """Silently ignoring an explicit option is this project's recurring silent green."""
    result = run("explain", "--all", "--out", "/tmp/somewhere")
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert "--graphs" in result.stderr
    assert assembler.assembly.explain_pipeline.targets == []


def test_the_graph_directory_is_not_classified_by_the_command(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    """8.4 classifies and creates ``--out`` itself; a second classification would differ."""
    out = tmp_path / "not-created-here"
    run("explain", "--all", "--graphs", "--out", str(out))
    assert not out.exists()
    assert assembler.assembly.explain_pipeline.options[0].out_dir == out


# --- the three views and their destinations (req 9.6, 12.4) -----------------------


def test_the_text_view_goes_to_standard_output(assembler: StubAssembler) -> None:
    result = run("explain", "--all")
    assert result.exit_code == 0
    assert result.stdout == render_summary(a_summary(Selection(mode="all")), "text") + "\n"


def test_the_markdown_view_is_the_one_a_merge_request_takes(
    assembler: StubAssembler,
) -> None:
    result = run("explain", "--all", "--format", "markdown")
    expected = render_summary(a_summary(Selection(mode="all")), "markdown")
    assert result.stdout == expected + "\n"
    assert result.stdout.startswith("# Change summary")


def test_the_json_view_is_the_only_thing_on_standard_output(
    assembler: StubAssembler,
) -> None:
    result = run("explain", "--all", "--format", "json")
    document = json.loads(result.stdout)
    assert document["db_path"] == "/cache/repo/after.und"


def test_the_summary_can_be_written_to_a_file_instead(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    target = tmp_path / "summary.md"
    result = run("explain", "--all", "--format", "markdown", "--output", str(target))
    assert result.stdout == ""
    expected = render_summary(a_summary(Selection(mode="all")), "markdown")
    assert target.read_text(encoding="utf-8") == expected + "\n"


def test_the_rendered_summary_names_the_target_it_was_built_for(
    assembler: StubAssembler,
) -> None:
    """The double answers from its input, so a dropped selection shows up in the document."""
    result = run("explain", "--files", "one.py", "--format", "json")
    quoted = json.loads(result.stdout)["open_command"]
    assert describe(Selection(mode="files", files=["one.py"])) in quoted


def test_the_rendered_summary_names_the_aids_that_were_requested(
    assembler: StubAssembler,
) -> None:
    result = run("explain", "--all", "--impact", "--format", "json")
    assert "[impact=True]" in json.loads(result.stdout)["open_command"]


def test_a_summary_that_cannot_be_delivered_names_the_output_option(
    assembler: StubAssembler, tmp_path: Path
) -> None:
    result = run("explain", "--all", "--output", str(tmp_path / "no-dir" / "s.md"))
    assert result.exit_code == int(ExitCode.REPORT_UNDELIVERABLE)
    assert "--output" in result.stderr


# --- exit codes (req 1.6, 12.5) ---------------------------------------------------


def test_a_successful_explanation_exits_zero(assembler: StubAssembler) -> None:
    assert run("explain", "--all").exit_code == int(ExitCode.OK)


def test_a_typed_error_from_the_pipeline_keeps_its_own_exit_code(
    assembler: StubAssembler,
) -> None:
    assembler.assembly.explain_pipeline.error = AnalysisFailedError("und failed")
    result = run("explain", "--all")
    assert result.exit_code == int(ExitCode.ANALYSIS_FAILED)
    assert result.stdout == ""


def test_explain_outside_a_repository_exits_with_the_not_a_git_repository_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCITOOLS_HOME", str(tmp_path / "no-understand-here"))
    result = run("explain", "--all")
    assert result.exit_code == int(ExitCode.NOT_A_GIT_REPO)
    assert result.stdout == ""
