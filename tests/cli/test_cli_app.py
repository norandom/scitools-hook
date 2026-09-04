"""The assembled application: subcommands, help, prompts and stream discipline (task 9.1).

Requirement 12.1 asks for ten subcommands, each with a ``--help`` that documents every
option and the exit codes; 12.6 forbids prompting anywhere, because the Gate runs inside a
pre-commit hook where stdin is not a terminal. Both are checked over the *whole* command
tree discovered from the application rather than over a hand-written list, so the
subcommands tasks 9.2 and 9.3 fill in are covered the moment they are registered.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import pytest
import typer
from fakes.cli import StubAssembler
from fakes.cli import describe as describe_target
from typer.main import get_command
from typer.testing import CliRunner

from scitools_hook import __version__
from scitools_hook.cli import agent_rules as agent_rules_module
from scitools_hook.cli import app as app_module
from scitools_hook.cli import baseline as baseline_module
from scitools_hook.cli import check as check_module
from scitools_hook.cli import common, pipelines
from scitools_hook.cli import config_cmd as config_module
from scitools_hook.cli import db as db_module
from scitools_hook.cli import doctor as doctor_module
from scitools_hook.cli import explain as explain_module
from scitools_hook.cli import hooks as hooks_module
from scitools_hook.cli import recommend as recommend_module
from scitools_hook.cli import skills as skills_module
from scitools_hook.errors import ConfigError, NotAGitRepositoryError
from scitools_hook.exit_codes import ExitCode, describe

SUBCOMMANDS = (
    "check",
    "explain",
    "baseline",
    "recommend",
    "init",
    "config",
    "doctor",
    "db",
    "install-hook",
    "uninstall-hook",
    "agent-rules",
    "install-skills",
)
"""Requirement 12.1's ten names, in the order it lists them, plus two later additions.

``recommend`` is the eleventh and postdates the requirement list. It sits directly after
``baseline`` because the two are the repository-measuring pair -- "where you are" and "where
to aim" -- and ``--help`` lists commands in registration order, so an operator meets them
together and reads their contrasting one-line help side by side.

``install-skills`` is the twelfth and sits last, beside ``agent-rules``: the two of them
hand something to an agent rather than measuring anything, and both are steps in enabling a
repository rather than in running the gate.
"""

DB_SUBCOMMANDS = ("path", "rebuild", "analyze", "export-arch", "project")

COMMAND_MODULES = {
    "check": check_module,
    "explain": explain_module,
    "baseline": baseline_module,
    "recommend": recommend_module,
    "init": config_module,
    "config": config_module,
    "doctor": doctor_module,
    "db": db_module,
    "install-hook": hooks_module,
    "uninstall-hook": hooks_module,
    "agent-rules": agent_rules_module,
    "install-skills": skills_module,
}
"""Which module holds each command."""


def is_stub(module: object) -> bool:
    """Whether ``module``'s commands still raise instead of doing the work.

    Read off the module rather than off a list, so that a task giving a command its body
    shrinks the stub tests below **without editing them** -- tasks 9.2 and 9.3 land
    separately and a hand-maintained list would make each one break the other's suite.
    """
    return any(name.endswith("NOT_IMPLEMENTED") for name in vars(module))


STUBS = tuple(name for name in SUBCOMMANDS if is_stub(COMMAND_MODULES[name]))
"""The commands that are still stubs; empty once every task in section 9 has landed."""

CLI_SOURCE_DIR = Path(common.__file__).resolve().parent

PROMPT_CALLS = re.compile(r"\b(typer\.prompt|typer\.confirm|click\.prompt|input|getpass)\s*\(")


def command_tree() -> list[tuple[tuple[str, ...], object]]:
    """Every command in the application, as ``(argv path, click command)`` pairs."""
    found: list[tuple[tuple[str, ...], object]] = []
    pending: list[tuple[tuple[str, ...], object]] = [((), get_command(app_module.app))]
    while pending:
        path, command = pending.pop()
        found.append((path, command))
        for name, child in sorted(getattr(command, "commands", {}).items()):
            pending.append((path + (name,), child))
    return found


TREE = command_tree()
HELP_IDS = ["root" if not path else " ".join(path) for path, _ in TREE]


# --- the ten subcommands ---------------------------------------------------------


def test_help_lists_every_subcommand() -> None:
    result = CliRunner().invoke(app_module.app, ["--help"])
    assert result.exit_code == 0
    for name in SUBCOMMANDS:
        assert re.search(rf"^\s+{re.escape(name)}\b", result.stdout, re.MULTILINE), name


def test_exactly_the_documented_subcommands_are_registered() -> None:
    root = get_command(app_module.app)
    assert set(getattr(root, "commands", {})) == set(SUBCOMMANDS)


def test_the_database_subcommand_offers_exactly_its_documented_operations() -> None:
    root = get_command(app_module.app)
    database = getattr(root, "commands", {})["db"]
    assert set(getattr(database, "commands", {})) == set(DB_SUBCOMMANDS)


@pytest.mark.parametrize(("path", "command"), TREE, ids=HELP_IDS)
def test_every_command_documents_the_exit_codes(path: tuple[str, ...], command: object) -> None:
    result = CliRunner().invoke(app_module.app, list(path) + ["--help"])
    assert result.exit_code == 0
    for code in ExitCode:
        assert str(int(code)) in result.stdout
        assert describe(code) in result.stdout


def test_the_exit_code_help_is_built_from_the_enum() -> None:
    """A hand-typed table would drift; the epilog must name every member and its meaning."""
    for code in ExitCode:
        assert describe(code) in common.HELP_EPILOG
        assert str(int(code)) in common.HELP_EPILOG


@pytest.mark.parametrize(("path", "command"), TREE, ids=HELP_IDS)
def test_help_goes_to_stdout_and_succeeds(path: tuple[str, ...], command: object) -> None:
    result = CliRunner().invoke(app_module.app, list(path) + ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert result.stderr == ""


# --- global options --------------------------------------------------------------


def test_the_root_help_documents_every_global_option() -> None:
    result = CliRunner().invoke(app_module.app, ["--help"])
    for option in (
        "--scitools-home",
        "--config",
        "--api-mode",
        "--verbose",
        "--color",
        "--no-color",
        "--quiet",
    ):
        assert option in result.stdout, option


def test_check_and_explain_offer_the_whole_selection_group() -> None:
    for name in ("check", "explain"):
        result = CliRunner().invoke(app_module.app, [name, "--help"])
        for option in ("--staged", "--worktree", "--all", "--files", "--format", "--output"):
            assert option in result.stdout, f"{name} {option}"


def test_version_prints_the_installed_version_to_stdout() -> None:
    result = CliRunner().invoke(app_module.app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
    assert result.stderr == ""


# --- no prompts anywhere ---------------------------------------------------------


@pytest.mark.parametrize(("path", "command"), TREE, ids=HELP_IDS)
def test_no_parameter_prompts(path: tuple[str, ...], command: object) -> None:
    """Requirement 12.6: stdin is not a terminal inside a hook, so nothing may ask."""
    for parameter in getattr(command, "params", []):
        assert getattr(parameter, "prompt", None) is None, parameter.name
        assert not getattr(parameter, "confirmation_prompt", False), parameter.name
        assert not getattr(parameter, "hide_input", False), parameter.name


def test_a_command_run_with_empty_stdin_never_waits_for_input() -> None:
    """Reaching the body with stdin closed proves nothing on the way asked for input.

    A prompt fed no input raises ``EOFError``, which the shared handler would report as an
    unexpected error naming ``EOFError`` -- so an answer on standard output is what says the
    body was reached. ``db path`` is the cheapest command that has one: it runs in this
    repository, touches no file and starts no subprocess but ``git`` (task 9.3).
    """
    result = CliRunner().invoke(app_module.app, ["db", "path"], input="")
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert result.stdout.strip().endswith("after.und")
    assert "EOFError" not in result.stderr
    assert "Aborted" not in result.stderr


# --- stream discipline and the bare invocation -----------------------------------


def test_a_bare_invocation_prints_usage_to_stderr() -> None:
    """Findings own stdout; a usage reminder is a diagnostic and exits with the input code."""
    result = CliRunner().invoke(app_module.app, [])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert "Usage:" in result.stderr


def test_conflicting_selection_flags_are_rejected_on_a_real_command() -> None:
    result = CliRunner().invoke(app_module.app, ["check", "--staged", "--all"])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert "--staged" in result.stderr
    assert "--all" in result.stderr


def test_conflicting_selection_flags_are_rejected_on_explain_too() -> None:
    result = CliRunner().invoke(app_module.app, ["explain", "--worktree", "--files", "a.py"])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert "--worktree" in result.stderr
    assert "--files" in result.stderr


def test_the_application_is_wired_to_the_shared_error_handler() -> None:
    assert isinstance(get_command(app_module.app), common.GateGroup)


def test_the_root_callback_publishes_the_global_options(tmp_path: Path) -> None:
    """Built fresh, so the shared application is never mutated to observe it."""
    seen: list[common.GlobalOptions] = []
    probe = app_module.build_app()

    @probe.command(name="spy")
    def spy(ctx: typer.Context) -> None:
        """Record what the root callback published."""
        seen.append(common.global_options(ctx))

    result = CliRunner().invoke(
        probe,
        [
            "--quiet",
            "--verbose",
            "--api-mode",
            "upython",
            "--config",
            str(tmp_path / "c.toml"),
            "--scitools-home",
            str(tmp_path),
            "spy",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert seen[0].quiet is True
    assert seen[0].verbose is True
    assert seen[0].api_mode is common.ApiMode.UPYTHON
    assert seen[0].config == tmp_path / "c.toml"
    assert seen[0].scitools_home == tmp_path


# --- the stubs tasks 9.2 and 9.3 replace -----------------------------------------


@pytest.mark.parametrize("name", STUBS)
def test_every_stub_command_is_reachable_and_reports_it_is_unimplemented(name: str) -> None:
    """Delete this test as 9.2 and 9.3 give each command a body; the help tests stay."""
    argv = [name, "path"] if name == "db" else [name]
    result = CliRunner().invoke(app_module.app, argv)
    assert result.exit_code == int(ExitCode.UNEXPECTED)
    assert result.stdout == ""
    assert "NotImplementedError" in result.stderr


# --- the selection each command actually resolves --------------------------------

NO_HOOK: dict[str, str | None] = {"GIT_INDEX_FILE": None}
IN_HOOK: dict[str, str | None] = {"GIT_INDEX_FILE": ".git/index"}

SELECTION_CASES = (
    ([], NO_HOOK, "all"),
    ([], IN_HOOK, "staged"),
    (["--staged"], NO_HOOK, "staged"),
    (["--worktree"], NO_HOOK, "worktree"),
    (["--all"], IN_HOOK, "all"),
    (["--files", "a.py", "--files", "b/c.py"], IN_HOOK, "files: a.py, b/c.py"),
)


@pytest.fixture
def assembler(monkeypatch: pytest.MonkeyPatch) -> StubAssembler:
    """Stand in for the whole run assembly, so a command reaches its pipeline and stops.

    Task 9.1 observed the resolved selection through the stub bodies' ``NotImplementedError``
    message. Those bodies are gone, so the same question is now asked one layer in: the
    double records what the pipeline was pointed at, and the cases below are unchanged.
    """
    stub = StubAssembler()
    monkeypatch.setattr(pipelines, "assemble", stub)
    return stub


@pytest.mark.parametrize(("flags", "env", "expected"), SELECTION_CASES)
@pytest.mark.parametrize("name", ("check", "explain"))
def test_the_selection_group_is_wired_flag_by_flag(
    assembler: StubAssembler,
    name: str,
    flags: list[str],
    env: dict[str, str | None],
    expected: str,
) -> None:
    """Each flag must reach its own argument, and the default must follow the hook env."""
    result = CliRunner().invoke(app_module.app, [name] + flags, env=env)
    assert result.exit_code in (int(ExitCode.OK), int(ExitCode.VIOLATIONS)), result.stderr
    assert [describe_target(target) for target in assembler.assembly.targets] == [expected]


@pytest.mark.parametrize("name", ("check", "explain"))
def test_a_format_is_accepted_whatever_its_case(assembler: StubAssembler, name: str) -> None:
    """A usage error would be exit 2; running to completion means the value parsed."""
    result = CliRunner().invoke(app_module.app, [name, "--staged", "--format", "JSON"])
    assert result.exit_code == int(ExitCode.OK)
    assert len(assembler.assembly.targets) == 1


def test_an_api_mode_is_accepted_whatever_its_case(assembler: StubAssembler) -> None:
    result = CliRunner().invoke(app_module.app, ["--api-mode", "UPYTHON", "check", "--staged"])
    assert result.exit_code == int(ExitCode.OK)


def test_an_unknown_format_is_refused_with_the_configuration_code() -> None:
    result = CliRunner().invoke(app_module.app, ["check", "--format", "yaml"])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""


# --- defaults of the global options ----------------------------------------------


def spy_app(seen: list[common.GlobalOptions]) -> typer.Typer:
    """A fresh application with one extra command that records what was published."""
    probe = app_module.build_app()

    @probe.command(name="spy")
    def spy(ctx: typer.Context) -> None:
        """Record what the root callback published."""
        seen.append(common.global_options(ctx))

    return probe


def test_the_global_options_default_to_off() -> None:
    seen: list[common.GlobalOptions] = []
    result = CliRunner().invoke(spy_app(seen), ["spy"])
    assert result.exit_code == 0, result.stderr
    assert seen[0].verbose is False
    assert seen[0].quiet is False
    assert seen[0].color is None
    assert seen[0].api_mode is None
    assert seen[0].config is None
    assert seen[0].scitools_home is None


def test_no_color_reaches_the_options_as_a_refusal() -> None:
    seen: list[common.GlobalOptions] = []
    result = CliRunner().invoke(spy_app(seen), ["--no-color", "spy"])
    assert result.exit_code == 0, result.stderr
    assert seen[0].color is False


def test_the_options_carry_the_real_process_environment() -> None:
    seen: list[common.GlobalOptions] = []
    result = CliRunner().invoke(spy_app(seen), ["spy"], env={"SCITOOLS_HOOK_PROBE": "1"})
    assert result.exit_code == 0, result.stderr
    assert seen[0].env["SCITOOLS_HOOK_PROBE"] == "1"


# --- help is complete, and the program names itself ------------------------------


def test_the_usage_line_names_the_installed_program() -> None:
    result = CliRunner().invoke(app_module.app, ["--help"])
    assert result.stdout.startswith("Usage: scitools-hook [OPTIONS] COMMAND")


def test_the_exit_code_block_is_headed_and_indented() -> None:
    result = CliRunner().invoke(app_module.app, ["--help"])
    assert "Exit codes:" in result.stdout
    assert "\n    0   no blocking violations\n" in result.stdout
    assert "\n    70  unexpected internal error\n" in result.stdout


def test_the_epilog_runs_table_then_blank_line_then_the_option_note() -> None:
    """One contiguous assertion, so nothing can be inserted between the two blocks."""
    result = CliRunner().invoke(app_module.app, ["--help"])
    assert (
        "    70  unexpected internal error\n"
        "\n"
        "  Global options come before the subcommand:\n"
        "    scitools-hook --verbose check --staged\n"
    ) in result.stdout


@pytest.mark.parametrize(("path", "command"), TREE, ids=HELP_IDS)
def test_every_command_and_option_carries_help_text(path: tuple[str, ...], command: object) -> None:
    """Requirement 12.1: ``--help`` documents every option, so every option has help."""
    assert getattr(command, "help", None), path
    for parameter in getattr(command, "params", []):
        assert getattr(parameter, "help", None), f"{path} {parameter.name}"


def test_shell_completion_is_not_offered() -> None:
    """Installing completion edits the user's shell profile; a gate never does that."""
    root = get_command(app_module.app)
    spellings = {name for parameter in root.params for name in parameter.opts}
    assert "--install-completion" not in spellings
    assert "--show-completion" not in spellings


def test_version_is_eager_enough_to_beat_a_bad_option() -> None:
    """``--version`` must win even when it comes last: an eager option is processed first."""
    result = CliRunner().invoke(app_module.app, ["--api-mode", "bogus", "--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_typer_never_renders_its_own_traceback() -> None:
    """The shared handler is the only error renderer; typer's rich one would double-report."""
    assert app_module.app.pretty_exceptions_enable is False


def test_a_bare_invocation_shows_the_command_list_not_just_a_usage_line() -> None:
    result = CliRunner().invoke(app_module.app, [])
    assert "Commands:" in result.stderr
    assert "check" in result.stderr


def test_the_database_group_with_no_operation_shows_its_operations() -> None:
    result = CliRunner().invoke(app_module.app, ["db"])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    for operation in DB_SUBCOMMANDS:
        assert operation in result.stderr


def stub_invocations() -> list[tuple[list[str], str]]:
    """One invocation per command still without a body, ``db``'s three operations included.

    Built from :data:`STUBS` rather than written out, for the reason :func:`is_stub` gives:
    tasks 9.2 and 9.3 implement different commands and neither may break the other's suite.
    """
    cases: list[tuple[list[str], str]] = []
    for name in STUBS:
        if name == "db":
            cases.extend(
                ([name, operation], f"`{name} {operation}`") for operation in DB_SUBCOMMANDS
            )
        else:
            cases.append(([name], f"`{name}`"))
    return cases


@pytest.mark.parametrize(("argv", "named"), stub_invocations())
def test_each_stub_says_which_command_is_missing(argv: list[str], named: str) -> None:
    """Delete with the stubs; until then the message must name the command it stands for."""
    result = CliRunner().invoke(app_module.app, argv)
    assert named in result.stderr


# --- the pre-commit framework's own invocation (req 11.7, 11.8) ------------------

PRE_COMMIT_FILES = ["a.py", "b.py", "c.py"]


@pytest.mark.parametrize("name", ("check", "explain"))
def test_a_pre_commit_file_list_reaches_the_selection(assembler: StubAssembler, name: str) -> None:
    """``entry: scitools-hook check --files`` with ``pass_filenames`` appends bare paths."""
    argv = [name, "--files"] + PRE_COMMIT_FILES
    result = CliRunner().invoke(app_module.app, argv, env=NO_HOOK)
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert describe_target(assembler.assembly.targets[0]) == "files: a.py, b.py, c.py"
    assert "unexpected extra argument" not in result.stderr


@pytest.mark.parametrize("name", ("check", "explain"))
def test_bare_paths_without_the_option_select_them_too(assembler: StubAssembler, name: str) -> None:
    """So ``entry: scitools-hook check`` with ``pass_filenames`` also works (req 12.3)."""
    result = CliRunner().invoke(app_module.app, [name] + PRE_COMMIT_FILES, env=NO_HOOK)
    assert result.exit_code == int(ExitCode.OK), result.stderr
    assert describe_target(assembler.assembly.targets[0]) == "files: a.py, b.py, c.py"


@pytest.mark.parametrize("name", ("check", "explain"))
@pytest.mark.parametrize("selector", ("--staged", "--worktree", "--all"))
def test_bare_paths_still_conflict_with_a_mode_flag(name: str, selector: str) -> None:
    result = CliRunner().invoke(app_module.app, [name, selector, "a.py"], env=NO_HOOK)
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert selector in result.stderr
    assert "--files" in result.stderr


def test_the_paths_argument_is_documented_in_help() -> None:
    """``"PATH" in stdout`` was already satisfied by ``--files PATH``; name the argument."""
    for name in ("check", "explain"):
        result = CliRunner().invoke(app_module.app, [name, "--help"])
        assert "[PATH]..." in result.stdout
        assert "a pre-commit framework appends" in result.stdout


# --- where the global options go -------------------------------------------------


@pytest.mark.parametrize(("path", "command"), TREE, ids=HELP_IDS)
def test_every_help_says_where_the_global_options_go(
    path: tuple[str, ...], command: object
) -> None:
    """``check --verbose`` is a usage error, so the right spelling is on every help page."""
    result = CliRunner().invoke(app_module.app, list(path) + ["--help"])
    assert "scitools-hook --verbose check --staged" in result.stdout


# --- one assembly never mutates another ------------------------------------------


def nested_apps(app: typer.Typer) -> dict[str, typer.Typer]:
    """The ``typer.Typer`` instance behind each registered group, by name."""
    found: dict[str, typer.Typer] = {}
    for group in app.registered_groups:
        instance = group.typer_instance
        assert instance is not None
        found[group.name or instance.info.name or ""] = instance
    return found


def test_two_assemblies_do_not_share_their_nested_applications() -> None:
    """Assert on the Typer objects: ``get_command`` builds fresh click objects every call,
    so comparing its output is true even for two calls on the SAME application and would
    hold with a module-level singleton reinstated -- which is the thing being prevented.
    """
    first = nested_apps(app_module.build_app())
    second = nested_apps(app_module.build_app())
    assert set(first) == set(second) == {"db"}
    assert first["db"] is not second["db"]
    assert first["db"].registered_commands is not second["db"].registered_commands
    firsts = {id(info) for info in first["db"].registered_commands}
    seconds = {id(info) for info in second["db"].registered_commands}
    assert firsts.isdisjoint(seconds)


def test_a_second_assembly_cannot_inherit_the_first_ones_epilogs() -> None:
    """``document_help`` mutates what it is given; a shared instance would carry it across."""
    fresh = db_module.build_db_app()
    assert all(not info.epilog for info in fresh.registered_commands)
    assembled = nested_apps(app_module.build_app())["db"]
    assert all(info.epilog == common.HELP_EPILOG for info in assembled.registered_commands)


# --- click's own control flow is not mistaken for a failure ----------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (typer.BadParameter("bad"), int(ExitCode.CONFIG_ERROR)),
        (typer.Exit(code=int(ExitCode.VIOLATIONS)), int(ExitCode.VIOLATIONS)),
    ),
)
def test_click_control_flow_from_a_body_keeps_its_own_code(
    error: BaseException, expected: int
) -> None:
    """A command may fail its own arguments or exit with a verdict; neither is code 70."""
    probe = typer.Typer(
        cls=common.GateGroup,
        name="probe",
        rich_markup_mode=None,
        add_completion=False,
        pretty_exceptions_enable=False,
    )

    @probe.callback()
    def root(ctx: typer.Context) -> None:
        """Probe root."""
        ctx.obj = common.GlobalOptions(cwd=Path("."), env={})

    @probe.command()
    def boom() -> None:
        """Raise click's own control flow."""
        raise error

    result = CliRunner().invoke(probe, ["boom"])
    assert result.exit_code == expected
    assert result.stdout == ""


# --- the eager-callback hole (an option's callback runs before dispatch) ---------


EAGER_ERROR: list[BaseException] = []
"""What the eager callback should raise; module level because typer resolves names there."""


def raise_the_eager_error() -> None:
    """Named so the traceback test can look for this frame."""
    raise EAGER_ERROR[-1]


def eager_callback(value: bool) -> None:
    """An eager option's callback, which click runs during ``parse_args``."""
    if value:
        raise_the_eager_error()


def app_with_eager_option(error: BaseException) -> typer.Typer:
    """An application whose eager option fails, as ``--version`` could."""
    EAGER_ERROR.append(error)
    probe = typer.Typer(
        cls=common.GateGroup,
        name="probe",
        rich_markup_mode=None,
        add_completion=False,
        pretty_exceptions_enable=False,
    )

    @probe.callback()
    def root(
        ctx: typer.Context,
        boom: Annotated[
            bool, typer.Option("--boom", callback=eager_callback, is_eager=True)
        ] = False,
        verbose: Annotated[bool, typer.Option(common.VERBOSE_FLAG)] = False,
    ) -> None:
        """Probe root."""
        ctx.obj = common.GlobalOptions(cwd=Path("."), env={}, verbose=verbose)

    @probe.command()
    def noop() -> None:
        """Do nothing."""

    return probe


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (ConfigError("bad setting"), int(ExitCode.CONFIG_ERROR)),
        (NotAGitRepositoryError("outside"), int(ExitCode.NOT_A_GIT_REPO)),
        (RuntimeError("kaboom"), int(ExitCode.UNEXPECTED)),
    ),
)
def test_an_eager_option_callback_gets_the_documented_exit_code(
    error: BaseException, expected: int
) -> None:
    """It runs inside ``parse_args``, so only ``make_context`` can catch it."""
    result = CliRunner().invoke(app_with_eager_option(error), ["--boom", "noop"])
    assert result.exit_code == expected
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


def test_an_eager_failure_still_honours_verbose_read_from_the_raw_arguments() -> None:
    """No context exists yet, so the flag is read from argv; the spelling is one constant."""
    result = CliRunner().invoke(
        app_with_eager_option(RuntimeError("kaboom")), [common.VERBOSE_FLAG, "--boom", "noop"]
    )
    assert result.exit_code == int(ExitCode.UNEXPECTED)
    assert "Traceback (most recent call last)" in result.stderr
    assert "raise_the_eager_error" in result.stderr


def test_the_verbose_spelling_is_the_one_the_application_declares() -> None:
    root = get_command(app_module.app)
    spellings = {name for parameter in root.params for name in parameter.opts}
    assert common.VERBOSE_FLAG in spellings


# --- an interrupt is the operator, not a fault -----------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    ((KeyboardInterrupt(), 130), (SystemExit(9), 9)),
    ids=("KeyboardInterrupt", "SystemExit"),
)
def test_base_exceptions_keep_their_own_status(error: BaseException, expected: int) -> None:
    """Measured: widening the handler to ``BaseException`` would make both of these 70."""
    probe = typer.Typer(
        cls=common.GateGroup,
        name="probe",
        rich_markup_mode=None,
        add_completion=False,
        pretty_exceptions_enable=False,
    )

    @probe.callback()
    def root(ctx: typer.Context) -> None:
        """Probe root."""
        ctx.obj = common.GlobalOptions(cwd=Path("."), env={})

    @probe.command()
    def boom() -> None:
        """Raise something that is not an ``Exception``."""
        raise error

    result = CliRunner().invoke(probe, ["boom"])
    assert result.exit_code == expected
    assert result.stdout == ""


# --- help that actually documents (req 12.1) -------------------------------------

MIN_HELP_WORDS = 4
"""Fewest words that can say what an option does. A PROXY, and only as good as its cases.

A character count was tried first and did not work: ``"Not implemented."`` and
``"TODO later."`` both cleared ten characters and a full stop while documenting nothing. Four
words is what separates them from the shortest real help this CLI has
(``"Analyse the whole project."``).

The threshold is pinned in BOTH directions by :data:`DOCUMENTATION_CASES` -- three-word
placeholders are refused and four-word help is accepted -- because a bar with no case on
either side of it can be moved without any test noticing, which is what happened to the
character count it replaced.

It remains a proxy and is described as one: it cannot tell prose from filler, so
``"The the the the."`` and ``"FIXME: fill this in later."`` both pass. What it buys is that
no option can be shipped with a stub for help, which is the failure requirement 12.1 is
about; judging whether real prose is *good* is a reviewer's job, not a predicate's.
"""


def documents(text: str | None) -> bool:
    """Whether ``text`` reads as a sentence about the thing, not a placeholder.

    Two conditions, both load-bearing and both pinned by :func:`test_the_documentation_bar`:
    it ends in a full stop, and it uses at least :data:`MIN_HELP_WORDS` words.
    """
    if not text:
        return False
    stripped = text.strip()
    return stripped.endswith(".") and len(stripped.split()) >= MIN_HELP_WORDS


DOCUMENTATION_CASES = (
    ("Analyse the whole project.", True),
    ("Write the findings here instead of stdout.", True),
    ("Show this message and exit.", True),
    ("Maintainability gate backed by SciTools Understand.\n\nMore prose.\n", True),
    ("x", False),
    ("MUTANT", False),
    ("Not implemented.", False),
    ("TODO later.", False),
    ("Aaaaaaaaaa.", False),
    ("Not implemented yet.", False),
    ("To be documented.", False),
    ("No help available.", False),
    ("Analyse the whole project", False),
    ("", False),
    (None, False),
)
"""What the bar must and must not accept, on BOTH sides of the threshold.

``"Not implemented."`` is refused deliberately: it describes the state of the code, not the
option, and a help string that survives into a release saying it has documented nothing.

The three-word rows exist because the threshold was previously unpinned downward -- every
case was one or two words or four-plus, so lowering ``MIN_HELP_WORDS`` to 3 changed nothing
any test could see, and 3 is exactly the value that admits this trio of real placeholders.
"""


@pytest.mark.parametrize(("text", "expected"), DOCUMENTATION_CASES)
def test_the_documentation_bar(text: str | None, expected: bool) -> None:
    """The bar every other help test leans on; without this it can be silently weakened.

    Round 2 replaced a truthiness check with this predicate, and the predicate itself had no
    test -- so ``return bool(text)``, the very check that was rejected, could be restored
    with every test still green.
    """
    assert documents(text) is expected


@pytest.mark.parametrize(("path", "command"), TREE, ids=HELP_IDS)
def test_every_command_and_option_is_documented_in_a_sentence(
    path: tuple[str, ...], command: object
) -> None:
    """Truthiness is not documentation: ``help="x"`` would satisfy it and tell nobody."""
    assert documents(getattr(command, "help", None)), path
    for parameter in getattr(command, "params", []):
        assert documents(getattr(parameter, "help", None)), f"{path} {parameter.name}"


EXPECTED_METAVARS = {
    "--scitools-home": "DIR",
    "--config": "PATH",
    "--files": "PATH",
    "--output": "PATH",
}
"""The placeholders 9.1 declares; 9.2 and 9.3 add their own rows rather than editing these."""


def test_path_options_show_a_placeholder_that_says_what_to_pass() -> None:
    for path in ((), ("check",)):
        result = CliRunner().invoke(app_module.app, list(path) + ["--help"])
        for option, metavar in EXPECTED_METAVARS.items():
            if option in result.stdout:
                assert f"{option} {metavar}" in result.stdout, f"{path} {option}"


def test_the_trailing_paths_argument_is_shown_in_the_usage_line() -> None:
    for name in ("check", "explain"):
        result = CliRunner().invoke(app_module.app, [name, "--help"])
        assert f"Usage: scitools-hook {name} [OPTIONS] [PATH]..." in result.stdout


# --- an abort is not control flow: exit 1 already means "violations found" -------


def test_an_abort_from_a_command_body_is_not_reported_as_violations() -> None:
    """Click renders ``Abort`` as exit 1, which this CLI has spent on blocking violations.

    Nothing here raises it -- ``confirm(abort=True)`` and ``ctx.abort()`` are the producers,
    and requirement 12.6 forbids prompting -- so reaching it means something unforeseen
    happened, and saying "violations found" would block a commit over findings never
    measured. It is deliberately absent from the control-flow tuple.
    """
    probe = typer.Typer(
        cls=common.GateGroup,
        name="probe",
        rich_markup_mode=None,
        add_completion=False,
        pretty_exceptions_enable=False,
    )

    @probe.callback()
    def root(ctx: typer.Context) -> None:
        """Probe root."""
        ctx.obj = common.GlobalOptions(cwd=Path("."), env={})

    @probe.command()
    def boom() -> None:
        """Abort from inside a command body."""
        raise typer.Abort()

    result = CliRunner().invoke(probe, ["boom"])
    assert result.exit_code == int(ExitCode.UNEXPECTED)
    assert result.stdout == ""


def test_the_control_flow_tuple_holds_only_what_carries_its_own_code() -> None:
    assert typer.Abort not in common._CONTROL_FLOW
    assert typer.Exit in common._CONTROL_FLOW
    assert typer.TyperException in common._CONTROL_FLOW


# --- an eager option on a SUBCOMMAND, which 9.2 and 9.3 will add ----------------


def test_a_subcommand_eager_option_also_gets_the_documented_code() -> None:
    """Only the root callback was covered; a subcommand parses in its own ``make_context``."""
    EAGER_ERROR.append(ConfigError("bad setting"))
    probe = typer.Typer(
        cls=common.GateGroup,
        name="probe",
        rich_markup_mode=None,
        add_completion=False,
        pretty_exceptions_enable=False,
    )

    @probe.callback()
    def root(ctx: typer.Context) -> None:
        """Probe root."""
        ctx.obj = common.GlobalOptions(cwd=Path("."), env={})

    @probe.command()
    def go(
        boom: Annotated[
            bool, typer.Option("--boom", callback=eager_callback, is_eager=True)
        ] = False,
    ) -> None:
        """Carry an eager option of its own."""

    result = CliRunner().invoke(probe, ["go", "--boom"])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert result.stdout == ""
    assert "bad setting" in result.stderr


# --- the current working directory really is carried ----------------------------


def test_the_options_carry_the_directory_the_command_was_run_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cwd == Path.cwd()`` cannot fail; run from somewhere else and name it."""
    seen: list[common.GlobalOptions] = []
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(spy_app(seen), ["spy"])
    assert result.exit_code == 0, result.stderr
    assert seen[0].cwd == Path.cwd()
    assert seen[0].cwd.resolve() == tmp_path.resolve()
