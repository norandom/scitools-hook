"""The shared option groups: selection, format/output, and the global options (task 9.1).

The selection group is the part with a real failure mode. Four flags name one choice, so
every *pair* of them must be refused -- and refused per pair, not "at least one pair",
because a check that only ever sees one combination is the shape that has bitten this
project repeatedly (implementation note on per-occurrence mutants). The default is
hook-aware: git sets ``GIT_INDEX_FILE`` for a pre-commit hook (measured on git 2.43), which
is the signal requirement 12.3 turns into ``--staged``.
"""

from __future__ import annotations

import errno
import itertools
import os
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scitools_hook.cli import common
from scitools_hook.config.loader import CLI_CONFIG_KEY
from scitools_hook.errors import ConfigError, ReportUndeliverableError
from scitools_hook.exit_codes import ExitCode
from scitools_hook.report.human import ColorMode, Verbosity
from scitools_hook.runner.context import ContextOptions

DEV_FULL = Path("/dev/full")
needs_dev_full = pytest.mark.skipif(not DEV_FULL.exists(), reason="no /dev/full on this system")

HOOK_ENV = {"GIT_INDEX_FILE": ".git/index"}
FLAGS = ("staged", "worktree", "all_", "files")


def resolve(**overrides: object) -> common.SelectionChoice:
    """Call ``resolve_selection`` with every flag off unless overridden."""
    keywords: dict[str, object] = {
        "staged": False,
        "worktree": False,
        "all_": False,
        "files": None,
        "paths": None,
        "env": {},
    }
    keywords.update(overrides)
    return common.resolve_selection(**keywords)  # type: ignore[arg-type]


def flag_value(name: str) -> object:
    """The value that turns flag ``name`` on."""
    return ["a.py"] if name in ("files", "paths") else True


# --- selection: the default ------------------------------------------------------


def test_no_flag_outside_a_hook_selects_everything() -> None:
    assert resolve().mode is common.SelectionMode.ALL


def test_no_flag_inside_a_hook_selects_the_index() -> None:
    assert resolve(env=HOOK_ENV).mode is common.SelectionMode.STAGED


def test_an_explicit_flag_beats_the_hook_default() -> None:
    assert resolve(all_=True, env=HOOK_ENV).mode is common.SelectionMode.ALL


def test_the_hook_signal_is_the_measured_one() -> None:
    """git 2.43 exports GIT_INDEX_FILE to a pre-commit hook; nothing else is assumed."""
    assert common.HOOK_ENV_VARS == ("GIT_INDEX_FILE",)
    assert common.in_hook(HOOK_ENV)
    assert not common.in_hook({"GIT_EXEC_PATH": "/usr/lib/git-core"})


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_hook_variable_counts_as_unset(blank: str) -> None:
    """``GIT_INDEX_FILE=`` is a caller saying "not this one", and is read that way.

    This is the convention the rest of the package already holds to
    (``config.loader._config_home``, ``understand.locator._env_home``,
    ``understand.fake.fake_directory``), and ``in_hook`` was the one reader still testing
    membership. Nothing real is lost: git sets the variable to the path of the index, never to
    nothing. What a membership test costs is a *different set of files* -- ``GIT_INDEX_FILE=
    scitools-hook check`` would default to ``--staged`` where the operator would read
    ``--all``.
    """
    assert not common.in_hook({"GIT_INDEX_FILE": blank})
    assert resolve(env={"GIT_INDEX_FILE": blank}).mode is common.SelectionMode.ALL


# --- selection: one flag at a time ----------------------------------------------


def test_staged_selects_the_index() -> None:
    assert resolve(staged=True).mode is common.SelectionMode.STAGED


def test_worktree_selects_the_working_tree() -> None:
    assert resolve(worktree=True).mode is common.SelectionMode.WORKTREE


def test_all_selects_the_whole_project() -> None:
    assert resolve(all_=True).mode is common.SelectionMode.ALL


def test_files_carries_the_list_it_was_given() -> None:
    choice = resolve(files=["a.py", "b/c.py"])
    assert choice.mode is common.SelectionMode.FILES
    assert choice.files == ("a.py", "b/c.py")


def test_an_empty_files_list_is_not_a_selection() -> None:
    """``--files`` absent arrives as ``None``; an empty list must not out-vote the default."""
    assert resolve(files=[], env=HOOK_ENV).mode is common.SelectionMode.STAGED


def test_a_non_files_mode_carries_no_files() -> None:
    assert resolve(staged=True).files == ()


# --- selection: every conflicting pair ------------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    list(itertools.combinations(FLAGS, 2)),
    ids=[f"{a}+{b}" for a, b in itertools.combinations(FLAGS, 2)],
)
def test_every_conflicting_pair_is_refused(first: str, second: str) -> None:
    with pytest.raises(ConfigError) as raised:
        resolve(**{first: flag_value(first), second: flag_value(second)})
    message = str(raised.value)
    assert common.SELECTION_FLAGS[first] in message
    assert common.SELECTION_FLAGS[second] in message
    assert raised.value.exit_code is ExitCode.CONFIG_ERROR


def test_three_conflicting_flags_are_refused_and_all_named() -> None:
    with pytest.raises(ConfigError) as raised:
        resolve(staged=True, worktree=True, all_=True)
    message = str(raised.value)
    for flag in ("--staged", "--worktree", "--all"):
        assert flag in message


def test_the_conflict_error_names_the_option_group_as_its_key() -> None:
    with pytest.raises(ConfigError) as raised:
        resolve(staged=True, all_=True)
    assert raised.value.key == common.SELECTION_KEY
    assert raised.value.hint


# --- global options --------------------------------------------------------------


def test_quiet_becomes_quiet_verbosity() -> None:
    assert common.GlobalOptions(cwd=Path("."), env={}, quiet=True).verbosity is Verbosity.QUIET
    assert common.GlobalOptions(cwd=Path("."), env={}).verbosity is Verbosity.NORMAL


def test_color_is_forced_on_even_when_the_stream_is_not_a_terminal() -> None:
    options = common.GlobalOptions(cwd=Path("."), env={"NO_COLOR": "1"}, color=True)
    assert options.color_mode(is_tty=False) is ColorMode.ON


def test_no_color_wins_over_a_terminal() -> None:
    options = common.GlobalOptions(cwd=Path("."), env={}, color=False)
    assert options.color_mode(is_tty=True) is ColorMode.OFF


def test_without_a_flag_a_pipe_gets_no_colour() -> None:
    options = common.GlobalOptions(cwd=Path("."), env={})
    assert options.color_mode(is_tty=False) is ColorMode.OFF
    assert options.color_mode(is_tty=True) is ColorMode.ON


def test_the_no_color_environment_variable_is_honoured() -> None:
    options = common.GlobalOptions(cwd=Path("."), env={"NO_COLOR": "1"})
    assert options.color_mode(is_tty=True) is ColorMode.OFF


def test_config_and_api_mode_become_configuration_overrides() -> None:
    options = common.GlobalOptions(
        cwd=Path("."),
        env={},
        config=Path("/repo/other.toml"),
        api_mode=common.ApiMode.UPYTHON,
    )
    assert options.cli_overrides == {
        CLI_CONFIG_KEY: Path("/repo/other.toml"),
        "understand.api_mode": "upython",
    }


def test_an_unset_global_option_produces_no_override() -> None:
    assert common.GlobalOptions(cwd=Path("."), env={}).cli_overrides == {}


def test_scitools_home_stays_out_of_the_overrides() -> None:
    """Requirement 1.1 ranks the option above the file, so it travels beside the settings."""
    options = common.GlobalOptions(cwd=Path("."), env={}, scitools_home=Path("/opt/scitools"))
    assert options.cli_overrides == {}
    assert options.context_options().scitools_home == Path("/opt/scitools")


def test_context_options_carry_the_whole_command_line() -> None:
    options = common.GlobalOptions(
        cwd=Path("/repo"),
        env={"SCITOOLS_HOME": "/opt"},
        config=Path("/repo/c.toml"),
        verbose=True,
    )
    context = options.context_options()
    assert isinstance(context, ContextOptions)
    assert context.cwd == Path("/repo")
    assert context.env == {"SCITOOLS_HOME": "/opt"}
    assert context.cli_overrides == {CLI_CONFIG_KEY: Path("/repo/c.toml")}


def test_verbose_installs_a_command_log_that_records() -> None:
    verbose = common.GlobalOptions(cwd=Path("."), env={}, verbose=True)
    quiet = common.GlobalOptions(cwd=Path("."), env={})
    assert isinstance(verbose.command_log(), common.ConsoleCommandLog)
    assert not isinstance(quiet.command_log(), common.ConsoleCommandLog)


def test_quiet_silences_progress_but_verbose_does_not() -> None:
    loud = common.GlobalOptions(cwd=Path("."), env={}, verbose=True)
    hushed = common.GlobalOptions(cwd=Path("."), env={}, quiet=True)
    assert isinstance(loud.progress(), common.ConsoleProgress)
    assert not isinstance(hushed.progress(), common.ConsoleProgress)


# --- diagnostics go to stderr, findings to stdout --------------------------------


def test_the_command_log_prints_argv_timing_and_status_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    common.ConsoleCommandLog().record(["und", "-db", "a b.und", "analyze"], 1.25, 0)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "'a b.und'" in captured.err
    assert "1.25" in captured.err
    assert "rc=0" in captured.err


def test_progress_notes_go_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    common.ConsoleProgress().note("syncing shadow")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "syncing shadow" in captured.err


def test_a_slow_phase_is_reported_and_a_quick_one_is_not(
    capsys: pytest.CaptureFixture[str],
) -> None:
    progress = common.ConsoleProgress()
    progress.finish("analyze", 0.2)
    assert capsys.readouterr().err == ""
    progress.finish("analyze", common.SLOW_PHASE_S + 0.1)
    captured = capsys.readouterr()
    assert "analyze" in captured.err
    assert captured.out == ""


def test_a_verbose_progress_reports_every_phase(capsys: pytest.CaptureFixture[str]) -> None:
    progress = common.ConsoleProgress(verbose=True)
    progress.start("extract")
    progress.finish("extract", 0.01)
    captured = capsys.readouterr()
    assert "extract" in captured.err
    assert captured.out == ""


# --- findings output -------------------------------------------------------------


def test_findings_go_to_stdout_verbatim(capsys: pytest.CaptureFixture[str]) -> None:
    common.emit_findings("routine.CyclomaticStrict \x1b[1;31m12\x1b[0m", None)
    captured = capsys.readouterr()
    assert captured.out == "routine.CyclomaticStrict \x1b[1;31m12\x1b[0m\n"
    assert captured.err == ""


def test_findings_already_ending_in_a_newline_gain_no_second_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    common.emit_findings("one line\n", None)
    assert capsys.readouterr().out == "one line\n"


def test_output_writes_the_file_and_leaves_stdout_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "findings.json"
    common.emit_findings('{"schema_version": 1}', target)
    captured = capsys.readouterr()
    assert target.read_text(encoding="utf-8") == '{"schema_version": 1}\n'
    assert captured.out == ""
    assert captured.err == ""


def missing_parent(tmp_path: Path) -> Path:
    """ENOENT: the directory above the target does not exist."""
    return tmp_path / "missing" / "findings.json"


def a_directory(tmp_path: Path) -> Path:
    """EISDIR: the target is a directory, which ``write_text`` cannot replace."""
    target = tmp_path / "findings.json"
    target.mkdir()
    return target


def full_device(tmp_path: Path) -> Path:
    """ENOSPC: the target exists and is writable, and the write still cannot land."""
    return DEV_FULL


OUTPUT_FAILURES = (
    pytest.param(missing_parent, errno.ENOENT, common.BAD_PATH_HINT, id="missing-parent"),
    pytest.param(a_directory, errno.EISDIR, common.BAD_PATH_HINT, id="target-is-a-directory"),
    pytest.param(
        full_device,
        errno.ENOSPC,
        common.NO_SPACE_HINT,
        id="device-full",
        marks=needs_dev_full,
    ),
)
"""Three *different* errnos through one branch.

Round 2's suite had three tests on this branch and every one of them passed the same
``missing/findings.json``, so ``except OSError`` could be narrowed to
``except FileNotFoundError`` with the whole suite still green -- and a full device or a
directory target would then have exited 70, "unexpected internal error", instead of 2.
"""


@pytest.mark.parametrize(("make_target", "expected_errno", "expected_hint"), OUTPUT_FAILURES)
def test_an_unwritable_output_is_a_located_delivery_error(
    tmp_path: Path,
    make_target: Callable[[Path], Path],
    expected_errno: int,
    expected_hint: str,
) -> None:
    """The message must be ours, naming the target -- not just the OS error's own filename."""
    target = make_target(tmp_path)
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("{}", target)
    assert str(raised.value).startswith(f"cannot write to {target}: ")
    assert f"[Errno {expected_errno}]" in str(raised.value)
    assert raised.value.hint == expected_hint
    assert raised.value.key == "--output"
    assert raised.value.exit_code is ExitCode.REPORT_UNDELIVERABLE


def test_a_full_device_is_not_told_to_find_a_writable_directory() -> None:
    """``/dev/full`` IS an existing writable path, so the other hint would be a false claim.

    The literals are asserted, not just compared to each other: every other test reads the
    hint back out of the same constant it is checking, which cannot notice the text changing
    into something that advises nothing. This is the operator-facing half of the fix, and it
    is the half that was factually wrong before it.
    """
    assert common.BAD_PATH_HINT == "name a path in an existing, writable directory"
    assert common.NO_SPACE_HINT == "free space on the device, or send the report somewhere else"
    assert common.NO_SPACE_HINT != common.BAD_PATH_HINT
    assert "writable directory" not in common.NO_SPACE_HINT
    assert "space" in common.NO_SPACE_HINT


def test_every_output_format_the_requirement_names_is_offered() -> None:
    assert {member.value for member in common.OutputFormat} == {
        "human",
        "json",
        "sarif",
        "markdown",
    }


def test_every_api_mode_the_design_names_is_offered() -> None:
    assert {member.value for member in common.ApiMode} == {"auto", "inprocess", "upython"}


# --- the option grammar as a contract, not as a self-comparison ------------------


def test_the_selection_modes_carry_the_documented_names() -> None:
    """These strings become the runner's ``Selection.mode``, so they are a contract."""
    assert common.SelectionMode.STAGED.value == "staged"
    assert common.SelectionMode.WORKTREE.value == "worktree"
    assert common.SelectionMode.ALL.value == "all"
    assert common.SelectionMode.FILES.value == "files"


def test_the_selection_flags_are_spelled_as_the_requirement_spells_them() -> None:
    assert common.SELECTION_FLAGS == {
        "staged": "--staged",
        "worktree": "--worktree",
        "all_": "--all",
        "files": "--files",
    }


def test_the_flag_names_and_the_mode_names_describe_the_same_four_options() -> None:
    assert set(common.SELECTION_FLAGS) == set(FLAGS)


def test_the_conflict_key_names_all_four_options() -> None:
    with pytest.raises(ConfigError) as raised:
        resolve(staged=True, all_=True)
    assert raised.value.key == "--staged/--worktree/--all/--files"


def test_the_conflict_hint_states_the_default_rule() -> None:
    with pytest.raises(ConfigError) as raised:
        resolve(staged=True, all_=True)
    hint = raised.value.hint or ""
    assert "exactly one" in hint
    assert "--staged inside a git hook" in hint
    assert "--all otherwise" in hint


def test_a_three_way_conflict_reads_as_one_sentence() -> None:
    with pytest.raises(ConfigError) as raised:
        resolve(staged=True, worktree=True, files=["a.py"])
    assert str(raised.value) == (
        "--staged, --worktree and --files cannot be combined: they select different files"
    )


def test_a_files_conflict_names_the_files_option_by_its_spelling() -> None:
    with pytest.raises(ConfigError) as raised:
        resolve(all_=True, files=["a.py"])
    assert str(raised.value) == "--all and --files cannot be combined: they select different files"


def test_a_selection_describes_itself_for_a_message_or_a_log() -> None:
    assert common.describe_selection(resolve(all_=True)) == "all"
    assert common.describe_selection(resolve(staged=True)) == "staged"
    assert common.describe_selection(resolve(worktree=True)) == "worktree"
    assert common.describe_selection(resolve(files=["a.py", "b/c.py"])) == "files: a.py, b/c.py"


# --- values that other layers depend on ------------------------------------------


def test_the_slow_phase_threshold_is_the_five_seconds_the_requirement_names() -> None:
    assert common.SLOW_PHASE_S == 5.0


def test_a_phase_exactly_at_the_threshold_is_reported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The boundary itself: ``>`` instead of ``>=`` loses the phase that took five seconds."""
    common.ConsoleProgress().finish("analyze", 5.0)
    assert "analyze" in capsys.readouterr().err
    common.ConsoleProgress().finish("analyze", 4.999)
    assert capsys.readouterr().err == ""


def test_empty_findings_write_nothing_at_all(capsys: pytest.CaptureFixture[str]) -> None:
    common.emit_findings("", None)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_the_output_failure_names_the_option_it_came_from(tmp_path: Path) -> None:
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("{}", tmp_path / "missing" / "findings.json")
    assert raised.value.key == "--output"


# --- the option carriers are frozen ----------------------------------------------


def test_a_resolved_selection_cannot_be_rewritten() -> None:
    choice = resolve(all_=True)
    with pytest.raises(FrozenInstanceError):
        choice.mode = common.SelectionMode.STAGED  # type: ignore[misc]


def test_the_global_options_cannot_be_rewritten() -> None:
    options = common.GlobalOptions(cwd=Path("."), env={})
    with pytest.raises(FrozenInstanceError):
        options.verbose = True  # type: ignore[misc]


def test_the_progress_threshold_cannot_be_rewritten_mid_run() -> None:
    progress = common.ConsoleProgress()
    with pytest.raises(FrozenInstanceError):
        progress.threshold_s = 0.0  # type: ignore[misc]


# --- trailing bare paths are the same choice as --files --------------------------

SELECTORS = ("staged", "worktree", "all_")


def test_trailing_paths_alone_select_those_files() -> None:
    choice = resolve(paths=["a.py", "b/c.py"])
    assert choice.mode is common.SelectionMode.FILES
    assert choice.files == ("a.py", "b/c.py")


def test_the_option_and_the_trailing_paths_are_one_list() -> None:
    """``check --files a.py b.py c.py``: the option takes one, the rest arrive as arguments."""
    choice = resolve(files=["a.py"], paths=["b.py", "c.py"])
    assert choice.mode is common.SelectionMode.FILES
    assert choice.files == ("a.py", "b.py", "c.py")


def test_trailing_paths_do_not_conflict_with_the_option_they_extend() -> None:
    assert resolve(files=["a.py"], paths=["b.py"]).files == ("a.py", "b.py")


def test_empty_trailing_paths_leave_the_default_alone() -> None:
    assert resolve(paths=[], env=HOOK_ENV).mode is common.SelectionMode.STAGED
    assert resolve(paths=[]).mode is common.SelectionMode.ALL


@pytest.mark.parametrize("selector", SELECTORS)
def test_trailing_paths_still_conflict_with_every_other_selector(selector: str) -> None:
    """Folding paths into ``--files`` must not fold them past the mutual-exclusion check."""
    with pytest.raises(ConfigError) as raised:
        resolve(paths=["a.py"], **{selector: True})
    assert common.SELECTION_FLAGS["files"] in str(raised.value)
    assert common.SELECTION_FLAGS[selector] in str(raised.value)
    assert raised.value.exit_code is ExitCode.CONFIG_ERROR


def test_the_hint_says_trailing_paths_are_the_files_option() -> None:
    with pytest.raises(ConfigError) as raised:
        resolve(paths=["a.py"], staged=True)
    assert "trailing PATH arguments count as --files" in (raised.value.hint or "")


def test_a_selection_from_trailing_paths_describes_itself_the_same_way() -> None:
    assert common.describe_selection(resolve(paths=["a.py", "b.py"])) == "files: a.py, b.py"


# --- a consumer that stops reading is a pipeline, not an internal error ----------


class BrokenStdout:
    """A stdout whose pipe has been closed, as ``| head`` leaves it."""

    def __init__(self) -> None:
        self.attempts = 0

    def write(self, text: str) -> int:
        self.attempts += 1
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self) -> None:
        raise BrokenPipeError(32, "Broken pipe")

    def isatty(self) -> bool:
        return False


def test_a_broken_pipe_on_stdout_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = BrokenStdout()
    monkeypatch.setattr(sys, "stdout", broken)
    common.emit_findings('{"schema_version": 1}', None)
    monkeypatch.undo()
    assert broken.attempts == 1
    assert capsys.readouterr().err == ""


class FailingWriter:
    """A text writer that refuses, wrapping a real one so the descriptor still closes."""

    def __init__(self, wrapped: object, error: OSError) -> None:
        self._wrapped = wrapped
        self._error = error

    def __enter__(self) -> FailingWriter:
        return self

    def __exit__(self, *exc: object) -> bool:
        self._wrapped.close()  # type: ignore[attr-defined]
        return False

    def write(self, _text: str) -> int:
        raise self._error


def fail_the_file_write(monkeypatch: pytest.MonkeyPatch, error: OSError) -> None:
    """Make the ``--output`` branch's real write fail with ``error``.

    **The injection point is the branch's own writer, not ``Path.write_text``, and the reason
    is a measured near-miss.** Both tests below used to patch ``Path.write_text``; when the
    branch changed to write a scratch file and rename it on -- so a failed write stops
    destroying the previous report -- that patch intercepted nothing and both tests failed
    loudly with ``DID NOT RAISE``. Failing loudly was luck: a patch aimed at a call the code no
    longer makes is a test that has quietly stopped testing, and the same edit could as easily
    have left them green. Patch what the branch actually opens.
    """
    real_fdopen = os.fdopen

    def fdopen(handle: int, *args: object, **kwargs: object) -> FailingWriter:
        return FailingWriter(real_fdopen(handle, *args, **kwargs), error)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "fdopen", fdopen)


def test_a_file_destination_forgives_nothing_a_reader_could_have_caused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only standard output has a reader that can leave; a file never does.

    Pushed a real ``BrokenPipeError`` through the file branch rather than reusing the EISDIR
    case a third time: ``Path.write_text`` cannot produce one on its own, so the forgiveness
    that branch must NOT have is otherwise untestable.
    """
    target = tmp_path / "findings.json"

    fail_the_file_write(monkeypatch, BrokenPipeError(errno.EPIPE, "Broken pipe"))
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("{}", target)
    assert raised.value.exit_code is ExitCode.REPORT_UNDELIVERABLE


# --- colour knows where the findings are actually going --------------------------


class TtyStdout:
    """A stdout that claims to be an interactive terminal."""

    def isatty(self) -> bool:
        return True


def test_findings_written_to_a_file_are_never_coloured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The discriminating case: an interactive session writing findings to a FILE."""
    monkeypatch.setattr(sys, "stdout", TtyStdout())
    options = common.GlobalOptions(cwd=Path("."), env={})
    assert options.color_for(Path("report.txt")) is ColorMode.OFF


def test_findings_going_to_a_terminal_are_coloured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other branch of the same decision, verified independently."""
    monkeypatch.setattr(sys, "stdout", TtyStdout())
    options = common.GlobalOptions(cwd=Path("."), env={})
    assert options.color_for(None) is ColorMode.ON


def test_forcing_colour_still_wins_for_a_file() -> None:
    options = common.GlobalOptions(cwd=Path("."), env={}, color=True)
    assert options.color_for(Path("report.txt")) is ColorMode.ON


def test_colour_for_stdout_defers_to_the_stream() -> None:
    options = common.GlobalOptions(cwd=Path("."), env={}, color=False)
    assert options.color_for(None) is ColorMode.OFF


# --- verbose beats quiet on the diagnostic stream --------------------------------


def test_verbose_overrides_quiet_for_progress() -> None:
    both = common.GlobalOptions(cwd=Path("."), env={}, quiet=True, verbose=True)
    progress = both.progress()
    assert isinstance(progress, common.ConsoleProgress)
    assert progress.verbose is True


def test_verbose_overrides_quiet_for_the_command_log() -> None:
    both = common.GlobalOptions(cwd=Path("."), env={}, quiet=True, verbose=True)
    assert isinstance(both.command_log(), common.ConsoleCommandLog)


def test_quiet_alone_still_silences_progress() -> None:
    assert not isinstance(
        common.GlobalOptions(cwd=Path("."), env={}, quiet=True).progress(),
        common.ConsoleProgress,
    )


# --- the same pipeline, in a real process ----------------------------------------

BIG_WRITER = "from scitools_hook.cli import common; common.emit_findings('x' * 8_000_000, None)"
"""Enough to overflow the pipe buffer, so the failure lands on ``write`` itself."""

SMALL_WRITER = (
    "import time; time.sleep(0.4)\n"
    "from scitools_hook.cli import common\n"
    "common.emit_findings('{\"schema_version\": 1}', None)\n"
)
"""Small enough that ``write`` buffers happily and only ``flush`` -- then SHUTDOWN -- fails.

Measured: with the shutdown flush left to itself the process exits **120** printing
``Exception ignored while flushing sys.stdout: BrokenPipeError`` on stderr, after its exit
code was already decided. That is a different and worse failure than the one on the big
payload, and only this shape reaches it -- which is why both are pinned.
"""

READER = "import sys; sys.stdin.buffer.read(16)"
DISK_FULL_LINE = (
    "error: cannot write to standard output: [Errno 28] No space left on device\n"
    "  hint: free space on the device, or send the report somewhere else\n"
)
"""The whole of stderr on a full device: the located error, its hint, and nothing else.

No traceback and no ``Exception ignored`` complaint after the status was decided.
"""

DEV_FULL_WRITER = """
import typer
from pathlib import Path
from scitools_hook.cli import common

app = typer.Typer(
    cls=common.GateGroup,
    name="probe",
    rich_markup_mode=None,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.callback()
def root(ctx: typer.Context) -> None:
    "Probe root."
    ctx.obj = common.GlobalOptions(cwd=Path("."), env={})


@app.command()
def report() -> None:
    "Emit findings the way task 9.2 will."
    common.emit_findings("finding " * 40, None)


app()
"""
"""A real application, because the documented code comes from the shared handler.

Calling ``emit_findings`` bare exits 1 with a traceback -- correctly, since nothing is there
to map it. What requirement 12.7 promises is what the CLI does, so the CLI is what runs here.
"""
IDLE_READER = "import sys; sys.exit(0)"

STDERR_WRITER = (
    "import sys\n"
    "from scitools_hook.cli import common\n"
    "common.echo_err('a diagnostic that cannot land')\n"
    "sys.stdout.write('still here\\n')\n"
)
"""Writes a diagnostic to a stderr that is always full, then proves the process carried on."""


def run_pipeline(writer_source: str, reader_source: str) -> tuple[int, str]:
    """Run ``writer | reader`` as two real processes; answer the WRITER's status and stderr."""
    writer = subprocess.Popen(
        [sys.executable, "-c", writer_source],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert writer.stdout is not None
    reader = subprocess.Popen(
        [sys.executable, "-c", reader_source], stdin=writer.stdout, stdout=subprocess.DEVNULL
    )
    writer.stdout.close()
    reader.wait(timeout=60)
    stderr = writer.communicate(timeout=60)[1]
    return writer.returncode, stderr


def test_a_reader_that_stops_early_costs_no_exit_code_and_no_noise() -> None:
    """``scitools-hook check --format json | head`` must not become an internal error."""
    status, stderr = run_pipeline(BIG_WRITER, READER)
    assert status == 0, stderr
    assert stderr == ""


def test_a_reader_that_never_reads_leaves_no_shutdown_noise() -> None:
    """The other half: the interpreter's own exit-time flush must not raise after the fact.

    A capture buffer never breaks, so no in-process test can see this; measured without the
    guard the writer exits 120 and prints its complaint once the run is already over.
    """
    status, stderr = run_pipeline(SMALL_WRITER, IDLE_READER)
    assert "Exception ignored" not in stderr
    assert stderr == ""
    assert status == 0


# --- a stdout write may never outrank the command's verdict ----------------------


class FailingStdout:
    """A stdout whose every write fails in a chosen way."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.attempts = 0

    def write(self, text: str) -> int:
        self.attempts += 1
        raise self.error

    def flush(self) -> None:
        raise self.error

    def isatty(self) -> bool:
        return False


PASSED_THROUGH = (
    pytest.param(ValueError("I/O operation on closed file"), id="closed"),
    pytest.param(
        UnicodeEncodeError("utf-8", "\udce9", 0, 1, "surrogates not allowed"), id="undecodable-path"
    ),
    pytest.param(AttributeError("no write"), id="substituted-stream"),
)
"""Shapes that are neither a reader leaving nor a delivery failure with an errno.

``UnicodeEncodeError`` is not hypothetical -- ``git/repo.py`` decodes paths with
``surrogateescape``, so a file name that is not valid UTF-8 reaches the renderer as
surrogates and fails to encode on the way out.
"""


@pytest.mark.parametrize("error", PASSED_THROUGH)
def test_a_stdout_failure_without_an_errno_is_re_raised_as_itself(
    error: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swallowing these would be a silent green: findings never delivered, exit 0."""
    monkeypatch.setattr(sys, "stdout", FailingStdout(error))
    with pytest.raises(type(error)):
        common.emit_findings("findings", None)


def test_an_undeliverable_stdout_gets_the_same_error_as_an_undeliverable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One physical cause, one answer: ``/dev/full`` used to be exit 2 here and 70 there."""
    monkeypatch.setattr(sys, "stdout", FailingStdout(OSError(errno.ENOSPC, "No space left")))
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("findings", None)
    assert str(raised.value).startswith("cannot write to standard output: ")
    assert raised.value.hint == common.NO_SPACE_HINT
    assert raised.value.exit_code is ExitCode.REPORT_UNDELIVERABLE


def test_a_stdout_that_cannot_be_reached_is_not_blamed_on_the_output_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--output`` was never given, so the error must not point the operator at it."""
    monkeypatch.setattr(sys, "stdout", FailingStdout(OSError(errno.ENOSPC, "No space left")))
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("findings", None)
    assert raised.value.key is None


def test_only_a_reader_that_left_is_forgiven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", FailingStdout(BrokenPipeError(32, "Broken pipe")))
    common.emit_findings("findings", None)


def test_a_closed_stdout_fails_loudly_without_crashing_the_detach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real closed file: ``write`` and ``fileno`` both raise ``ValueError``.

    Pins the ``ValueError`` arm of the detach guard on a genuine stream rather than a stub.
    """
    handle = (tmp_path / "out.txt").open("w", encoding="utf-8")
    handle.close()
    monkeypatch.setattr(sys, "stdout", handle)
    with pytest.raises(ValueError):
        common.emit_findings("findings", None)


def test_keyboard_interrupt_through_a_write_is_not_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Exception``, not ``BaseException``: an interrupt is the operator, not a fault."""
    monkeypatch.setattr(sys, "stdout", FailingStdout(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        common.emit_findings("findings", None)


class DescriptorStdout:
    """A failing stdout that owns a REAL descriptor, so ``_detach`` runs its whole body.

    ``FailingStdout`` has no ``fileno`` at all, so ``_detach`` returns at its first guard and
    never reaches ``os.open(os.devnull)`` -- which makes it useless for testing what happens
    to that descriptor. A guard defeated by the filter in front of it.
    """

    def __init__(self, descriptor: int, error: BaseException) -> None:
        self.descriptor = descriptor
        self.error = error

    def fileno(self) -> int:
        return self.descriptor

    def write(self, text: str) -> int:
        raise self.error

    def flush(self) -> None:
        raise self.error

    def isatty(self) -> bool:
        return False


@pytest.mark.skipif(not Path("/proc/self/fd").exists(), reason="needs /proc to count descriptors")
def test_a_failing_stdout_leaks_no_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the null device before knowing where to point it leaked one fd per failure.

    Measured with a stream that has no ``fileno``: zero leaked, because the body never ran.
    With this one: 64 calls leaked exactly 64 descriptors before the ``finally`` was added.
    """
    scratch = os.open(tmp_path / "sink", os.O_WRONLY | os.O_CREAT)
    try:
        monkeypatch.setattr(
            sys, "stdout", DescriptorStdout(scratch, BrokenPipeError(32, "Broken pipe"))
        )
        before = len(os.listdir("/proc/self/fd"))
        for _ in range(64):
            common.emit_findings("findings", None)
        after = len(os.listdir("/proc/self/fd"))
    finally:
        os.close(scratch)
    assert after == before


def test_the_detach_actually_redirects_the_descriptor_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves the body ran at all: afterwards the descriptor is the null device, not the file."""
    sink = tmp_path / "sink"
    scratch = os.open(sink, os.O_WRONLY | os.O_CREAT)
    try:
        monkeypatch.setattr(
            sys, "stdout", DescriptorStdout(scratch, BrokenPipeError(32, "Broken pipe"))
        )
        common.emit_findings("findings", None)
        os.write(scratch, b"swallowed by the null device")
    finally:
        os.close(scratch)
    assert sink.read_bytes() == b""


def run_to_dev_full(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` with standard output pointed at a device that is always full."""
    with DEV_FULL.open("w", encoding="utf-8") as full:
        return subprocess.run(
            argv, stdout=full, stderr=subprocess.PIPE, text=True, timeout=60, check=False
        )


@needs_dev_full
def test_a_full_disk_on_the_findings_path_gives_the_documented_code() -> None:
    """Measured before the fix: the one-liner and code 70 were right, the process exited 120.

    This is the path task 9.2 writes findings on, so it is the one that has to hold.
    """
    done = run_to_dev_full([sys.executable, "-c", DEV_FULL_WRITER, "report"])
    assert done.stderr == DISK_FULL_LINE
    assert "Exception ignored" not in done.stderr
    assert "Traceback" not in done.stderr
    assert done.returncode == int(ExitCode.REPORT_UNDELIVERABLE)


@needs_dev_full
def test_a_full_disk_during_an_eager_option_gives_the_documented_code() -> None:
    """``--version`` runs inside ``parse_args``, before dispatch exists to catch anything.

    Measured before the fix: 2872 bytes of traceback and status 120, with no ``--verbose``.
    """
    done = run_to_dev_full([sys.executable, "-m", "scitools_hook.cli.app", "--version"])
    assert done.stderr == DISK_FULL_LINE
    assert "Exception ignored" not in done.stderr
    assert "Traceback" not in done.stderr
    assert done.returncode == int(ExitCode.REPORT_UNDELIVERABLE)


def test_an_absent_standard_output_is_a_delivery_failure_not_an_internal_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sys.stdout is None`` is what a descriptor closed at exec leaves behind.

    The pair divides the work. This one pins the *type* -- a delivery failure carrying no
    ``key``, since no option named this destination -- in process, where the exit code is
    reachable as an attribute. :func:`CLOSED_STDOUT_LINE`'s test below pins the *text* as a
    literal and proves CPython really produces this state from ``>&-``; the comparisons here
    read their expectations out of the module and so say nothing about the wording.
    """
    monkeypatch.setattr(sys, "stdout", None)
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("findings", None)
    assert str(raised.value) == common.CLOSED_STDOUT
    assert raised.value.hint == common.REDIRECTION_HINT
    assert raised.value.key is None, "no option named this destination"
    assert raised.value.exit_code is ExitCode.REPORT_UNDELIVERABLE


SHELL = Path("/bin/sh")
needs_shell = pytest.mark.skipif(not SHELL.exists(), reason="no /bin/sh to close a descriptor")

CLOSED_STDOUT_LINE = (
    "error: cannot write to standard output: it was closed before this process started\n"
    "  hint: check where standard output is redirected, or pass --output to name a file\n"
)
"""The whole of stderr when descriptor 1 was closed at exec: the condition, and its hint."""


@needs_shell
def test_a_standard_output_closed_at_exec_names_the_condition_not_the_symptom() -> None:
    """``>&-`` leaves ``sys.stdout`` as ``None``, which is a delivery failure like any other.

    Measured before the guard: ``error: AttributeError: 'NoneType' object has no attribute
    'write'`` at exit **70**. Both halves were wrong for the same reason exit 70 on a full
    disk was: the text named an implementation detail of ``_write_stdout`` instead of the
    thing the operator did, and 70 is documented as an *unexpected internal error*, which
    invites a bug report about a redirection the operator wrote on purpose.

    The descriptor is closed by a shell rather than by ``preexec_fn`` because ``>&-`` is what
    an operator actually types, and because CPython decides ``sys.stdout is None`` from the
    state of the descriptor at interpreter start -- which only a real exec can produce.
    """
    done = subprocess.run(
        [
            str(SHELL),
            "-c",
            'exec "$0" "$@" >&-',
            sys.executable,
            "-m",
            "scitools_hook.cli.app",
            "--version",
        ],
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.stderr == CLOSED_STDOUT_LINE
    assert "AttributeError" not in done.stderr, "the message must name the condition"
    assert "Traceback" not in done.stderr
    assert done.returncode == int(ExitCode.REPORT_UNDELIVERABLE)


# --- stderr is guarded too: there is nowhere to report a failure to report -------


class RecordingStderr:
    """A stderr that records the order of writes and flushes."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def write(self, text: str) -> int:
        self.events.append(f"write:{text}")
        return len(text)

    def flush(self) -> None:
        self.events.append("flush")


def test_a_diagnostic_is_flushed_as_it_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """Findings and diagnostics may be read as one stream; an unflushed line arrives late."""
    recorder = RecordingStderr()
    monkeypatch.setattr(sys, "stderr", recorder)
    common.echo_err("a diagnostic")
    assert recorder.events == ["write:a diagnostic\n", "flush"]


def test_a_stderr_that_cannot_be_written_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """``2> /dev/full`` must not turn a correctly reported failure into status 120."""
    monkeypatch.setattr(sys, "stderr", FailingStdout(OSError(28, "No space left on device")))
    common.echo_err("a diagnostic")


def test_a_closed_stderr_never_raises_out_of_the_error_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``echo_err`` is documented "never raises", and ``_detach`` is the part that can.

    A closed file raises ``ValueError`` from ``write`` AND from ``fileno``. The stdout test
    for the same stream cannot tell the two apart -- it expects a ``ValueError`` either way --
    so without this, dropping ``ValueError`` from ``_detach``'s guard is invisible. Here the
    consequence is direct: the handler itself would propagate it while reporting an error.
    """
    handle = (tmp_path / "err").open("w", encoding="utf-8")
    handle.close()
    monkeypatch.setattr(sys, "stderr", handle)
    common.echo_err("a diagnostic that cannot land")


def test_a_closed_stdout_reports_through_the_handler_without_it_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole round trip on a closed stream: the write fails, the report still lands."""
    closed = (tmp_path / "out").open("w", encoding="utf-8")
    closed.close()
    captured = (tmp_path / "err").open("w", encoding="utf-8")
    try:
        monkeypatch.setattr(sys, "stdout", closed)
        monkeypatch.setattr(sys, "stderr", captured)
        with pytest.raises(ValueError) as raised:
            common.emit_findings("findings", None)
        common.report_error(raised.value, verbose=False)
    finally:
        captured.close()
    assert "error: ValueError" in (tmp_path / "err").read_text(encoding="utf-8")


# --- the frozen carriers really are slotted --------------------------------------


@pytest.mark.parametrize(
    "instance",
    (
        common.SelectionChoice(common.SelectionMode.ALL),
        common.GlobalOptions(cwd=Path(".")),
        common.ConsoleProgress(),
    ),
    ids=("SelectionChoice", "GlobalOptions", "ConsoleProgress"),
)
def test_the_option_carriers_carry_no_instance_dictionary(instance: object) -> None:
    """``slots=True`` is a claim, so it gets an assertion rather than an equivalence note."""
    assert not hasattr(instance, "__dict__")


# --- findings written to a file are UTF-8 ----------------------------------------


def test_output_is_written_as_utf8(tmp_path: Path) -> None:
    """9.2's JSON and SARIF documents depend on this and nothing else pinned it."""
    target = tmp_path / "findings.json"
    common.emit_findings('{"entity": "café — Größe"}', target)
    assert target.read_bytes() == '{"entity": "café — Größe"}\n'.encode()
    assert target.read_text(encoding="utf-8").startswith('{"entity": "café')


# --- the encoder is strict, on the path where a surrogate provably arrives -------

SURROGATE_NAME = "caf\udce9.py"
"""A real filename that is not valid UTF-8, as ``git`` hands it to us.

``repo.py`` decodes git's bytes with ``surrogateescape``, so ``caf\\xe9.py`` on disk becomes
this string in memory and reaches the renderer, and then this function, unchanged.
"""

SURROGATE_DOCUMENT = f'{{"entity": "{SURROGATE_NAME}", "rule": "routine.MaxNesting"}}'


def test_a_report_carrying_a_surrogate_refuses_rather_than_substituting(tmp_path: Path) -> None:
    """A lenient encoder here is a silent edit of the operator's data.

    ``errors="replace"`` would write ``caf?.py`` -- a filename that exists nowhere -- into a
    machine-readable report, and every test using well-formed text would stay green. This
    project has already been burned by exactly that shape once, with U+FFFD accepted as a
    real filename character downstream.
    """
    target = tmp_path / "findings.json"
    with pytest.raises(UnicodeEncodeError):
        common.emit_findings(SURROGATE_DOCUMENT, target)
    if target.exists():
        assert "?" not in target.read_text(encoding="utf-8", errors="replace")
        assert "�" not in target.read_text(encoding="utf-8", errors="replace")


def test_the_substitution_a_lenient_encoder_would_have_made_is_a_different_filename() -> None:
    """States the stake, so the test above is not read as pedantry about an exception type."""
    lenient = SURROGATE_DOCUMENT.encode("utf-8", "replace").decode("utf-8")
    assert "caf?.py" in lenient
    assert SURROGATE_NAME not in lenient


def test_a_surrogate_on_the_stdout_path_also_refuses(tmp_path: Path) -> None:
    """The same input through the other destination, on a real strict stream."""
    handle = (tmp_path / "captured").open("w", encoding="utf-8")
    try:
        with pytest.raises(UnicodeEncodeError):
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(sys, "stdout", handle)
                common.emit_findings(SURROGATE_DOCUMENT, None)
    finally:
        handle.close()


# --- the remaining seams 9.2 inherits --------------------------------------------


def test_a_second_hook_variable_would_each_be_enough_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``any`` and ``all`` agree only while the tuple has one element, and would stop silently."""
    monkeypatch.setattr(common, "HOOK_ENV_VARS", ("GIT_INDEX_FILE", "SOME_OTHER_HOOK_MARKER"))
    assert common.in_hook({"GIT_INDEX_FILE": ".git/index"})
    assert common.in_hook({"SOME_OTHER_HOOK_MARKER": "1"})
    assert not common.in_hook({"GIT_EXEC_PATH": "/usr/lib/git-core"})


def test_the_context_carries_the_log_and_the_progress_in_the_right_fields() -> None:
    """Swapping the two keyword arguments is caught only by mypy, which is not a test."""
    context = common.GlobalOptions(cwd=Path("."), env={}, verbose=True).context_options()
    assert isinstance(context.log, common.ConsoleCommandLog)
    assert isinstance(context.progress, common.ConsoleProgress)
    assert hasattr(context.log, "record")
    assert hasattr(context.progress, "note")


@needs_dev_full
def test_a_full_device_on_stderr_leaves_no_shutdown_complaint() -> None:
    """The stub used elsewhere has no descriptor, so ``_detach`` never runs on that path."""
    with DEV_FULL.open("w", encoding="utf-8") as full:
        done = subprocess.run(
            [sys.executable, "-c", STDERR_WRITER],
            stdout=subprocess.PIPE,
            stderr=full,
            text=True,
            timeout=60,
            check=False,
        )
    assert done.returncode == 0
    assert done.stdout == "still here\n"


# --- the encoder names its encoding, and that half is load-bearing too -----------

ASCII_LOCALE = {
    "LC_ALL": "C",
    "LANG": "C",
    "PYTHONUTF8": "0",
    "PYTHONCOERCECLOCALE": "0",
    "PYTHONIOENCODING": "utf-8",
}
"""An environment whose preferred encoding is ANSI_X3.4-1968.

Measured on this interpreter: ``Path.write_text(text)`` without ``encoding=`` then raises
``UnicodeEncodeError('ascii')`` for ``café — Größe``, while the shipped call writes the
correct UTF-8 bytes. In-process there is nothing to see -- this machine's locale is UTF-8,
so the two spellings agree and dropping ``encoding="utf-8"`` looks equivalent.
``PYTHONIOENCODING`` keeps the child's own stdout usable so a failure is readable.
"""

UTF8_WRITER = (
    "import sys\n"
    "from pathlib import Path\n"
    "from scitools_hook.cli import common\n"
    'common.emit_findings(\'{"entity": "caf\\u00e9 \\u2014 Gr\\u00f6\\u00dfe"}\', '
    "Path(sys.argv[1]))\n"
)
"""Non-ASCII given as escapes: a C locale cannot decode the command line itself otherwise."""

EXPECTED_UTF8 = '{"entity": "café — Größe"}\n'.encode()


def test_the_report_is_utf8_whatever_the_operators_locale_is(tmp_path: Path) -> None:
    """Only ``errors=`` was pinned; ``encoding=`` was equivalent on a UTF-8 machine alone.

    A gate that ran in CI under ``LC_ALL=C`` would have failed to write any report at all,
    with a ``UnicodeEncodeError`` mapping to exit 70.
    """
    target = tmp_path / "findings.json"
    done = subprocess.run(
        [sys.executable, "-c", UTF8_WRITER, str(target)],
        env=dict(os.environ) | ASCII_LOCALE,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert target.read_bytes() == EXPECTED_UTF8


def test_the_ascii_locale_probe_is_really_hostile(tmp_path: Path) -> None:
    """Guards the guard: if the child's locale were UTF-8, the test above proves nothing."""
    done = subprocess.run(
        [sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
        env=dict(os.environ) | ASCII_LOCALE,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.stdout.strip() == "ANSI_X3.4-1968"


# --- the hint reads the destination, not just the errno -------------------------

STDOUT_ERRNOS = (errno.EIO, errno.EBADF, errno.ENXIO, errno.EROFS, errno.EACCES)


@pytest.mark.parametrize("code", STDOUT_ERRNOS)
def test_a_stdout_failure_is_never_advised_to_name_a_better_path(
    code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No option named standard output, so path advice is about a decision never made."""
    monkeypatch.setattr(sys, "stdout", FailingStdout(OSError(code, "broken")))
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("findings", None)
    assert raised.value.hint == common.REDIRECTION_HINT
    assert raised.value.hint != common.BAD_PATH_HINT


def test_an_errno_less_stdout_failure_still_gets_destination_advice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``OSError()`` carries ``errno=None``, which matches no branch by value."""
    monkeypatch.setattr(sys, "stdout", FailingStdout(OSError("no errno at all")))
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("findings", None)
    assert raised.value.hint == common.REDIRECTION_HINT


@pytest.mark.parametrize("destination", ("stdout", "file"))
def test_running_out_of_quota_is_the_same_cause_as_running_out_of_space(
    destination: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EDQUOT on a quota'd NFS home: the directory exists and is writable, as with ENOSPC."""
    quota = OSError(errno.EDQUOT, "Disk quota exceeded")
    if destination == "stdout":
        monkeypatch.setattr(sys, "stdout", FailingStdout(quota))
        target: Path | None = None
    else:
        fail_the_file_write(monkeypatch, quota)
        target = tmp_path / "findings.json"
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("findings", target)
    assert raised.value.hint == common.NO_SPACE_HINT


def test_the_three_hints_are_distinct_and_each_says_something_different() -> None:
    """Literals, because every other test reads them back out of the constant it checks."""
    assert common.BAD_PATH_HINT == "name a path in an existing, writable directory"
    assert common.NO_SPACE_HINT == "free space on the device, or send the report somewhere else"
    assert common.REDIRECTION_HINT == (
        "check where standard output is redirected, or pass --output to name a file"
    )
    assert len({common.BAD_PATH_HINT, common.NO_SPACE_HINT, common.REDIRECTION_HINT}) == 3


# --- the option spelling travels with the destination (for 9.2) -----------------


@pytest.mark.parametrize("option", ("--output", "--sarif", "--out"))
def test_a_write_failure_names_the_option_that_chose_the_path(option: str, tmp_path: Path) -> None:
    """9.2 has two more file destinations; reporting ``--output`` for them would be wrong."""
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("{}", tmp_path / "missing" / "findings.json", option=option)
    assert raised.value.key == option


def test_a_very_long_destination_does_not_become_a_kilobyte_of_one_line() -> None:
    """ENAMETOOLONG arrives with the whole path, and the exception repeats it."""
    long_path = "/" + "a" * 5000
    error = common._cannot_write(long_path, "--output", OSError(errno.ENAMETOOLONG, "too long"))
    assert f"({len(long_path)} characters)" in str(error)
    assert long_path not in str(error)
    assert len(str(error)) < 400


def short_error(destination: str) -> str:
    """The message ``_cannot_write`` builds for ``destination``, for boundary checks."""
    return str(common._cannot_write(destination, "--output", OSError(errno.ENOENT, "nope")))


def test_a_destination_exactly_at_the_limit_is_quoted_whole() -> None:
    """The boundary, from below: a 5000-character path says nothing about where it lies."""
    destination = "/" + "a" * (common.MAX_DESTINATION - 1)
    assert len(destination) == common.MAX_DESTINATION
    message = short_error(destination)
    assert destination in message
    assert "characters)" not in message


def test_a_destination_one_over_the_limit_is_shortened() -> None:
    """The boundary, from above; together these pin the constant and the comparison."""
    destination = "/" + "a" * common.MAX_DESTINATION
    assert len(destination) == common.MAX_DESTINATION + 1
    message = short_error(destination)
    assert destination not in message
    assert f"({len(destination)} characters)" in message


def test_the_quoting_limit_keeps_one_error_on_one_readable_line() -> None:
    """The literal, because the boundary tests move with the constant they read.

    Both neighbours of the limit are checked above, but each derives its input from
    ``MAX_DESTINATION`` itself, so changing the constant moves the input and the expectation
    together and neither notices. The value is a display contract -- how much of a path one
    error may quote -- so it is asserted, together with the property that motivates it.
    """
    assert common.MAX_DESTINATION == 120
    longest = short_error("/" + "a" * 5000)
    assert len(longest) <= common.MAX_DESTINATION + 80
    assert len(longest.splitlines()) == 1


# --- a destination that would never return, and one that must survive a failure ----


BLOCKING_KINDS = (
    pytest.param("fifo", id="named-pipe"),
    pytest.param("socket", id="unix-socket"),
)


@pytest.mark.parametrize("kind", BLOCKING_KINDS)
def test_a_destination_that_would_block_forever_is_refused_before_it_is_opened(
    tmp_path: Path, kind: str
) -> None:
    """Opening a FIFO for writing waits for a reader that a hook has no reason to have.

    Measured before the guard existed, under an external ``timeout``: a real command with
    ``--output <fifo>`` was still blocked at ten seconds having produced no report, no
    diagnostic and no exit code. A gate that hangs a commit is worse than one that fails it,
    because nothing tells the operator which it is doing. ``os.stat`` settles the kind without
    opening anything, so the refusal costs nothing on the ordinary path.

    Asserted with an external clock as well as an exception: the whole point is that this
    returns rather than waits, and a test that only checks the type would pass just as well
    after ten seconds as after none.
    """
    target = tmp_path / "destination"
    if kind == "fifo":
        os.mkfifo(target)
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(target))
        sock.close()

    started = time.monotonic()
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("{}", target)
    assert time.monotonic() - started < 1.0
    assert common.BLOCKING_DESTINATION in str(raised.value)
    assert raised.value.exit_code is ExitCode.REPORT_UNDELIVERABLE
    assert raised.value.key == "--output"


def test_a_device_destination_is_written_through_rather_than_refused(tmp_path: Path) -> None:
    """Only FIFOs and sockets block; refusing every non-regular file would cost two useful ones.

    ``/dev/null`` is a legitimate discard, and ``/dev/full`` is how the disk-full path is
    exercised at all -- it must keep answering ``ENOSPC`` with the space hint rather than
    becoming a kind refusal. This is the line the guard draws, asserted so that widening it
    later is a deliberate act.
    """
    common.emit_findings("{}", Path(os.devnull))

    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("{}", DEV_FULL)
    assert f"[Errno {errno.ENOSPC}]" in str(raised.value)
    assert raised.value.hint == common.NO_SPACE_HINT


def test_a_failed_write_leaves_the_previous_report_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``Path.write_text`` truncates before it encodes, so a failure destroyed both reports.

    Measured on the version before this: a destination holding a previous report was left at
    sixteen bytes of a partial write after the write failed under ``RLIMIT_FSIZE`` -- the old
    report gone and the new one never delivered, at exit 70. A hook overwrites its report on
    every run, so the file a failed run destroys is exactly the one still worth having.

    The bytes are asserted, not just the file's existence: a zero-length file exists too.
    """
    target = tmp_path / "findings.json"
    previous = '{"previous": "report"}\n'
    target.write_text(previous, encoding="utf-8")

    fail_the_file_write(monkeypatch, OSError(errno.ENOSPC, "No space left on device"))
    with pytest.raises(ReportUndeliverableError):
        common.emit_findings('{"new": true}', target)

    assert target.read_text(encoding="utf-8") == previous
    assert [entry.name for entry in tmp_path.iterdir()] == ["findings.json"]


def test_a_symlinked_report_path_is_followed_and_keeps_its_target_s_mode(
    tmp_path: Path,
) -> None:
    """Pointing ``--output`` at a shared file is a working configuration, not a fault.

    Renaming onto the *link* would replace it with a regular file, so the shared destination
    would silently stop being shared. The mode is read with ``os.stat`` rather than
    ``os.lstat`` for a sharper reason, measured one module over in ``baseline_store``: on
    Linux ``lstat`` reports a symlink as ``0o777``, so carrying that mode over lands the
    report **world-writable** inside a repository.
    """
    shared = tmp_path / "shared.json"
    shared.write_text("{}\n", encoding="utf-8")
    shared.chmod(0o600)
    link = tmp_path / "report.json"
    link.symlink_to(shared)

    common.emit_findings('{"new": true}', link)

    assert link.is_symlink()
    assert "new" in shared.read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(shared).st_mode) == 0o600


def test_a_missing_parent_directory_is_still_refused_rather_than_created(
    tmp_path: Path,
) -> None:
    """The scratch-and-rename write must not quietly acquire ``mkdir(parents=True)``.

    ``baseline_store.save`` does create its parents, and copying that here was a real mistake
    caught by measurement rather than by review: the baseline path is the tool's own, while
    ``--output`` is a path the operator typed. Building a tree for a mistyped one hides the
    typo and leaves the report where nobody will look for it.
    """
    target = tmp_path / "nope" / "deep" / "findings.json"
    with pytest.raises(ReportUndeliverableError) as raised:
        common.emit_findings("{}", target)
    assert f"[Errno {errno.ENOENT}]" in str(raised.value)
    assert raised.value.hint == common.BAD_PATH_HINT
    assert not target.parent.exists()
