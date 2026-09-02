"""Everything every subcommand shares: global options, selection, output and exit codes.

The command modules in this package hold one command each and nothing else. What they have
in common lives here, so that a rule the requirements state once is implemented once:

* **One exit code per failure, decided by the error itself.** :func:`exit_code_for` reads
  ``type(error).exit_code``, the class attribute every ``GateError`` subclass carries. There
  is deliberately no table mapping classes to codes -- a table is a thing that can fall
  behind the hierarchy, and requirement 1.6 promises a distinct documented code for *every*
  failure kind. Anything that is not a ``GateError`` is the unexpected-error code 70, which
  requirement 12.7 requires to be distinct from the analysis-failure code 5.
* **The error handler is structural, not per command.** :class:`GateGroup` is the click
  group class the application is built with, so every subcommand -- including one added by a
  later task, and including one nested inside ``db`` -- is wrapped without having to
  remember a decorator.
* **Standard output carries findings and nothing else** (req 7.4, 7.7). :func:`emit_findings`
  is the only place ``cli/`` writes standard output -- ``--version`` routes its answer
  through it too, so there is no exception for a later task to forget. (Two writers outside
  this package are not ours to route: click prints ``--help`` itself, and
  ``understand/worker.py`` writes its answer envelope to stdout because stdout *is* its IPC
  channel to the parent process.) Diagnostics,
  progress and the command log go to stderr through :func:`echo_err`. Findings are written
  *raw*: the human renderer emits its own SGR escapes and a fixed-width layout, and passing
  that through a ``rich`` console would re-wrap it and parse ``[...]`` inside a message as
  markup. The write also survives a consumer that stops reading (``... | head``), which is an
  ordinary pipeline and not an internal error.
* **Nothing prompts** (req 12.6). The Gate runs inside a pre-commit hook where stdin is not
  a terminal, so every choice is an option or an environment variable; a refusal is an error
  with a hint, never a question.

The selection group is the one option group with a real failure mode: four flags name one
choice, so any two of them together is a refusal rather than a silent precedence rule. Its
default is hook-aware -- git exports ``GIT_INDEX_FILE`` to a pre-commit hook (measured on
git 2.43), which is the signal requirement 12.3 turns into ``--staged``. Trailing bare paths
are the same choice as ``--files``: the ``pre-commit`` framework appends the staged paths as
positional arguments after the entry line's ``--files`` (req 11.7, 11.8), so a grammar that
accepted only the repeated option would fail every commit touching more than one file.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shlex
import stat
import sys
import tempfile
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final

import typer
from typer.core import TyperGroup

from scitools_hook.config.loader import CLI_CONFIG_KEY
from scitools_hook.errors import ConfigError, GateError, ReportUndeliverableError
from scitools_hook.exit_codes import ExitCode, describe
from scitools_hook.models.progress import CommandLog, NullCommandLog, NullProgress, Progress
from scitools_hook.report.human import ColorMode, Verbosity, resolve_color
from scitools_hook.runner.context import ContextOptions

# --- vocabulary ------------------------------------------------------------------


class OutputFormat(StrEnum):
    """The renderings requirement 12.4 makes selectable with ``--format``."""

    HUMAN = "human"
    JSON = "json"
    SARIF = "sarif"
    MARKDOWN = "markdown"


class ApiMode(StrEnum):
    """How the Understand Python API is reached; ``auto`` lets the locator decide."""

    AUTO = "auto"
    INPROCESS = "inprocess"
    UPYTHON = "upython"


class SelectionMode(StrEnum):
    """Which files a run looks at (req 12.3)."""

    STAGED = "staged"
    WORKTREE = "worktree"
    ALL = "all"
    FILES = "files"


API_MODE_KEY: Final = "understand.api_mode"
"""Dotted settings key ``--api-mode`` overrides; the loader merges it above every file."""

NO_COLOR_VAR: Final = "NO_COLOR"

HOOK_ENV_VARS: Final[tuple[str, ...]] = ("GIT_INDEX_FILE",)
"""What tells us we are inside a git hook.

Measured on git 2.43: a ``pre-commit`` hook is started with ``GIT_INDEX_FILE``,
``GIT_AUTHOR_*``, ``GIT_EDITOR``, ``GIT_EXEC_PATH`` and ``GIT_PREFIX`` exported. Only the
index variable names the thing the default selection is *about*, so it is the only one read;
the others are set in contexts that are not a hook at all.
"""

SELECTION_FLAGS: Final[dict[str, str]] = {
    "staged": "--staged",
    "worktree": "--worktree",
    "all_": "--all",
    "files": "--files",
}
"""Parameter name -> the option spelling to name in a message; also the reporting order."""

_FLAG_MODES: Final[dict[str, SelectionMode]] = {
    "staged": SelectionMode.STAGED,
    "worktree": SelectionMode.WORKTREE,
    "all_": SelectionMode.ALL,
    "files": SelectionMode.FILES,
}
"""Parameter name -> the mode it selects; the keys are exactly ``SELECTION_FLAGS``'."""

SELECTION_KEY: Final = "--staged/--worktree/--all/--files"
"""What a conflict is *about*, carried on the error so a caller can locate it."""

SELECTION_HINT: Final = (
    "pass exactly one of them, or none to take the default "
    "(--staged inside a git hook, --all otherwise); "
    "trailing PATH arguments count as --files"
)

BAD_PATH_HINT: Final = "name a path in an existing, writable directory"
"""Why a write to a path the operator NAMED failed: the path is wrong."""

NO_SPACE_HINT: Final = "free space on the device, or send the report somewhere else"
"""Why a write failed when the destination is right and there is no room for it."""

REDIRECTION_HINT: Final = (
    "check where standard output is redirected, or pass --output to name a file"
)
"""Why a write to standard output failed for anything other than running out of room.

Standard output was not named by an option, so advice about naming a better path is about a
decision the operator never made -- it points them at a mistake they cannot find.
"""

MAX_DESTINATION: Final = 120
"""How much of a destination one error message may quote before it stops being one line."""

NO_SPACE_ERRNOS: Final = frozenset({errno.ENOSPC, errno.EDQUOT})
"""Failures that mean "there is no room", whatever the destination.

``EDQUOT`` belongs here with ``ENOSPC``: a quota'd NFS home is the ordinary environment for a
licensed Understand install, and the directory is present and writable in exactly the way
that makes ``BAD_PATH_HINT`` a false statement.
"""

SLOW_PHASE_S: Final = 5.0
"""A phase that takes longer than this is reported even when not verbose (req 4.11)."""

MISSING_OPTIONS: Final = "the root callback did not publish the global options"

VERBOSE_FLAG: Final = "--verbose"
"""The spelling ``app.py`` declares and :class:`GateGroup` looks for while parsing.

One constant rather than two literals: the handler that runs *during* parsing has no context
to read ``--verbose`` from, so it reads the raw argument list, and a spelling that drifted
would silently stop producing tracebacks for the one class of failure that happens too early
for anything else to see.
"""


GLOBAL_OPTIONS_NOTE: Final = (
    "Global options come before the subcommand:\n  scitools-hook --verbose check --staged"
)
"""Where ``--verbose`` and friends go.

Click attaches group options to the group, so ``check --verbose`` is a usage error. The
natural retry after an unexpected error is exactly that spelling, so the placement is stated
on every command's help rather than left to be discovered by failing.
"""


def _help_epilog() -> str:
    """The block every ``--help`` ends with: the exit codes and where options go (req 12.1).

    The leading ``\\b`` is click's marker for a paragraph that must not be re-wrapped; it
    only takes effect when the paragraph is preceded by a blank line.
    """
    lines = ["Exit codes:", "", "\b"]
    for code in ExitCode:
        lines.append(f"  {int(code):<3} {describe(code)}")
    lines.append("")
    lines.append("\b")
    lines.append(GLOBAL_OPTIONS_NOTE)
    return "\n".join(lines)


HELP_EPILOG: Final = _help_epilog()


# --- the selection group ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SelectionChoice:
    """One resolved selection: the mode, plus the explicit list when the mode is ``files``.

    Deliberately not the runner's ``Selection`` model: the option grammar is settled before
    any pipeline exists, and the command that owns a pipeline builds its model from this.
    """

    mode: SelectionMode
    files: tuple[str, ...] = ()


def in_hook(env: Mapping[str, str]) -> bool:
    """Whether this process was started by a git hook (req 12.3)."""
    return any(name in env for name in HOOK_ENV_VARS)


def resolve_selection(
    *,
    staged: bool,
    worktree: bool,
    all_: bool,
    files: Sequence[str] | None,
    paths: Sequence[str] | None,
    env: Mapping[str, str],
) -> SelectionChoice:
    """The one selection the options name, or the refusal that says they name several.

    ``files`` is what ``--files`` collected and ``paths`` is what arrived as trailing bare
    arguments; they are the SAME choice, joined here rather than by the caller, so a command
    cannot analyse one and forget the other. That join is what lets the ``pre-commit``
    framework's ``scitools-hook check --files a.py b.py c.py`` work at all (req 11.7, 11.8):
    the option takes one value and the rest land as arguments.

    Every parameter is a required keyword: a command that grows an option and forgets to pass
    it fails immediately and loudly rather than silently selecting the default.

    Raises ``ConfigError`` (exit code 2) when more than one is given -- they select different
    sets of files, so a precedence rule would quietly analyse something other than was asked.
    """
    selected = list(files or ()) + list(paths or ())
    chosen = _chosen_flags(staged=staged, worktree=worktree, all_=all_, files=selected)
    if len(chosen) > 1:
        raise ConfigError(_conflict_message(chosen), key=SELECTION_KEY, hint=SELECTION_HINT)
    if not chosen:
        return SelectionChoice(SelectionMode.STAGED if in_hook(env) else SelectionMode.ALL)
    mode = _FLAG_MODES[chosen[0]]
    return SelectionChoice(mode, tuple(selected) if mode is SelectionMode.FILES else ())


def describe_selection(selection: SelectionChoice) -> str:
    """One line naming what a resolved selection covers, for a message or a log line."""
    if selection.mode is SelectionMode.FILES:
        return f"{selection.mode.value}: {', '.join(selection.files)}"
    return selection.mode.value


def _chosen_flags(
    *, staged: bool, worktree: bool, all_: bool, files: Sequence[str] | None
) -> list[str]:
    """The names of the selection flags actually given, in ``SELECTION_FLAGS`` order."""
    given = {"staged": staged, "worktree": worktree, "all_": all_, "files": bool(files)}
    return [name for name in SELECTION_FLAGS if given[name]]


def _conflict_message(chosen: Sequence[str]) -> str:
    """Name every flag that was given, so the operator sees the whole conflict at once."""
    flags = [SELECTION_FLAGS[name] for name in chosen]
    listed = f"{', '.join(flags[:-1])} and {flags[-1]}"
    return f"{listed} cannot be combined: they select different files"


# --- the global options ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlobalOptions:
    """What the root callback parsed, in the shape the rest of a run needs it.

    ``env`` and ``cwd`` are values rather than ambient reads so that every decision derived
    from them -- the hook default, the colour decision, where configuration is looked for --
    is one a test can set and a caller can override.
    """

    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)
    scitools_home: Path | None = None
    config: Path | None = None
    api_mode: ApiMode | None = None
    verbose: bool = False
    color: bool | None = None
    quiet: bool = False

    @property
    def verbosity(self) -> Verbosity:
        """How much of a run to print (req 7.8)."""
        return Verbosity.QUIET if self.quiet else Verbosity.NORMAL

    @property
    def cli_overrides(self) -> dict[str, object]:
        """The settings this command line overrides, as the loader's dotted keys (req 3.1).

        ``--scitools-home`` is deliberately absent: requirement 1.1 ranks the option above
        the configuration file, and folding it in here would have the locator report the
        installation's source as ``config``. It travels on ``ContextOptions`` instead.
        """
        overrides: dict[str, object] = {}
        if self.config is not None:
            overrides[CLI_CONFIG_KEY] = self.config
        if self.api_mode is not None:
            overrides[API_MODE_KEY] = self.api_mode.value
        return overrides

    def color_mode(self, *, is_tty: bool | None = None) -> ColorMode:
        """Whether to colour findings (req 7.6); ``is_tty`` defaults to asking stdout."""
        terminal = sys.stdout.isatty() if is_tty is None else is_tty
        return resolve_color(self.color, is_tty=terminal, no_color=NO_COLOR_VAR in self.env)

    def color_for(self, output: Path | None) -> ColorMode:
        """The colour decision for findings going to ``output`` (``None`` means stdout).

        A file is never a terminal, so ``--output report.txt`` gets no escapes even from an
        interactive session -- asking ``sys.stdout`` there would colour a file because the
        *terminal* the findings are not going to happens to be one. An explicit ``--color``
        still wins, because requirement 7.6 makes forcing the operator's privilege.

        Two explicit branches rather than one conditional: they are separately observable,
        and this is the shape the project keeps getting wrong by verifying only one side.
        """
        if output is not None:
            return self.color_mode(is_tty=False)
        return self.color_mode()

    def command_log(self) -> CommandLog:
        """Every external command with its timing on stderr under ``--verbose`` (req 12.8)."""
        return ConsoleCommandLog() if self.verbose else NullCommandLog()

    def progress(self) -> Progress:
        """Phase reporting on stderr.

        ``--quiet`` is about the report on stdout (req 7.8), so it silences the incidental
        progress notes too -- but ``--verbose`` overrides it: an explicit request for detail
        beats an implicit request for less, and this is the same precedence ``command_log``
        applies, so ``--quiet --verbose`` is loud on stderr and terse on stdout in both.
        """
        if self.verbose:
            return ConsoleProgress(verbose=True)
        return NullProgress() if self.quiet else ConsoleProgress()

    def context_options(self) -> ContextOptions:
        """This command line, in the shape ``runner.context.build_context`` consumes.

        A fresh log and progress sink are built each call. Both are stateless writers to
        stderr, so two calls in one command behave as one; a sink that ever grows state must
        be hoisted out of here first.
        """
        return ContextOptions(
            cwd=self.cwd,
            env=self.env,
            cli_overrides=self.cli_overrides,
            scitools_home=self.scitools_home,
            log=self.command_log(),
            progress=self.progress(),
        )


def global_options(ctx: typer.Context) -> GlobalOptions:
    """The options the root callback published, from any depth of the command tree."""
    options = ctx.find_root().obj
    if isinstance(options, GlobalOptions):
        return options
    raise RuntimeError(MISSING_OPTIONS)


# --- streams ---------------------------------------------------------------------


def echo_err(message: str) -> None:
    """Write one diagnostic line to standard error (req 7.7).

    ``message`` is one line without its terminator; ``sys.stderr`` is looked up per call,
    never captured at import, so a caller that redirects the stream sees the redirection
    honoured. The ``flush`` is load-bearing: findings and diagnostics are two streams that a
    caller may be reading as one, and an unflushed line arrives after the run looks over.

    A stderr that cannot be written to is swallowed, because there is nowhere left to report
    a failure to report. It is also detached, so the interpreter's exit-time flush cannot
    raise the same failure again *after* the exit code was decided -- ``2> /dev/full`` would
    otherwise turn a correctly-reported failure into status 120 and a traceback.
    """
    stream = sys.stderr
    try:
        stream.write(f"{message}\n")
        stream.flush()
    except Exception:
        _detach(stream)


def emit_findings(text: str, output: Path | None, *, option: str = "--output") -> None:
    """Write rendered findings to ``output``, or to standard output when it is ``None``.

    The text is written verbatim apart from a single terminating newline: the human renderer
    produces its own escapes and column layout, and a JSON document must be the only thing on
    standard output (req 7.4).

    ``option`` is the spelling that produced ``output``, and it is what a failure will name.
    Task 9.2 has two more file destinations -- ``check --sarif PATH`` and ``explain --out
    DIR`` -- and reporting ``key: --output`` for an option the operator never passed is the
    same defect that was just fixed on the standard-output side. Pass the real spelling.
    """
    document = text if text.endswith("\n") or not text else f"{text}\n"
    if output is None:
        _write_stdout(document)
        return
    try:
        _deliver(output, document)
    except OSError as err:
        raise _cannot_write(str(output), option, err) from err


BLOCKING_DESTINATION: Final = (
    "is a named pipe or socket, which a report cannot be written to without a reader"
)


def _deliver(output: Path, document: str) -> None:
    """Write ``document`` to ``output``, refusing a destination that would never return.

    **Opening a FIFO for writing blocks forever when nothing is reading it.** Measured on this
    branch before the guard existed: a real command with ``--output <fifo>`` was still blocked
    at ten seconds, having produced no report, no diagnostic and no exit code -- a gate that
    hangs a commit rather than failing it. ``os.stat`` answers the kind without opening
    anything, so the kind is settled first. This is the same defect ``baseline_store.save``
    was given a guard for, one destination over; the fault class was swept for readers and
    then not for this writer.

    **Which kinds are refused is a deliberate line, not a blanket.** Only FIFOs and sockets
    block. Character and block devices fail loudly with a real errno, and two of them are
    useful: ``--output /dev/full`` is how the disk-full path is exercised and must keep
    answering ``ENOSPC``, and ``--output /dev/null`` is a legitimate discard. So a device is
    written through rather than refused, and only the two blocking kinds are turned away.

    A symlink is followed to its target, because pointing the report at a shared path is a
    working configuration; ``realpath`` resolves without raising so a dangling link still
    reaches the write and yields the operating system's own errno.
    """
    destination = Path(os.path.realpath(output))
    try:
        mode: int | None = os.stat(destination).st_mode
    except OSError:
        mode = None  # absent or unreachable: let the write produce the real errno
    if mode is not None and (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
        raise ReportUndeliverableError(
            f"cannot write to {_short(str(output))}: it {BLOCKING_DESTINATION}",
            key="--output",
            hint=BAD_PATH_HINT,
        )
    if mode is not None and not stat.S_ISREG(mode):
        destination.write_text(document, encoding="utf-8")
        return
    _replace_atomically(destination, document, mode)


def _replace_atomically(destination: Path, document: str, mode: int | None) -> None:
    """Write beside the destination and rename on, so a failed write destroys nothing.

    ``Path.write_text`` opens ``'w'``, which **truncates before it encodes**. Measured: a
    destination holding a previous report was left at sixteen bytes of a partial write after
    the write failed under ``RLIMIT_FSIZE`` -- the old report gone and the new one never
    delivered, at exit 70. A report a hook overwrites on every run is exactly the file an
    operator still wants when the run that replaced it failed.

    The scratch file comes from ``mkstemp`` in the destination's own directory: ``O_EXCL``, an
    unpredictable name, and the same filesystem, which is what makes ``os.replace`` atomic
    (across a boundary it raises ``EXDEV``). An existing file's mode is carried over via
    ``os.stat`` -- **never ``os.lstat``**, which reports a symlink as ``0o777`` and would land
    the report world-writable; that exact mistake was measured in ``baseline_store``.

    Accepted and recorded rather than fixed: a ``SIGKILL`` between ``mkstemp`` and the rename
    leaves an orphan scratch file beside the report. The residual TOCTOU is likewise open -- a
    regular file swapped for a FIFO between the ``stat`` above and this write would still
    block, and closing that needs an ``O_NONBLOCK`` open rather than a better predicate.
    """
    # No `mkdir(parents=True)` here, deliberately, and this is where the difference between
    # this writer and `baseline_store.save` lives: that path is the tool's own and creating it
    # is a service, while `--output` is a path the operator typed. Building a tree for a
    # mistyped one hides the typo and leaves a report where nobody will look for it. A missing
    # directory keeps its old answer -- `ENOENT` through `_cannot_write`, naming the option.
    handle, scratch = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as writer:
            writer.write(document)
        os.chmod(scratch, stat.S_IMODE(mode) if mode is not None else 0o644)
        os.replace(scratch, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(scratch)
        raise


def _cannot_write(destination: str, key: str | None, err: OSError) -> ReportUndeliverableError:
    """The typed refusal an undeliverable report becomes, whichever destination it had.

    One physical cause must not get two answers: before this existed, ``--output /dev/full``
    exited 2 with a located message while the *same* condition on standard output exited 70,
    documented as "unexpected internal error", telling a hook author to file a bug about
    their full disk. Both destinations now raise this.

    It carries its **own** exit code (7) rather than borrowing "configuration error", because
    neither of the two it used to borrow was true: ``/dev/full`` is an existing, writable path,
    so nothing about the configuration is wrong, and nothing about a full disk is unexpected.
    Every delivery failure gets that one code whatever caused it -- a full device, an exceeded
    quota, a missing directory, a destination that would block -- because the operator's next
    move is the same in each case and CI can treat the whole class as an environment fault
    rather than a rejected commit.

    The hint is chosen from BOTH the errno and the destination, because each on its own
    produces a false statement: "name a path in an existing, writable directory" is untrue of
    a full device (it is both), and it is meaningless on standard output, which no option
    named. ``key`` is what tells the two destinations apart -- it is ``None`` exactly when the
    operator passed no option.
    """
    return ReportUndeliverableError(
        f"cannot write to {_short(destination)}: {err}", key=key, hint=_write_hint(key, err)
    )


def _write_hint(key: str | None, err: OSError) -> str:
    """Advice that is true of this errno on this destination, or none that is false."""
    if err.errno in NO_SPACE_ERRNOS:
        return NO_SPACE_HINT
    return BAD_PATH_HINT if key is not None else REDIRECTION_HINT


def _short(destination: str) -> str:
    """The destination, shortened so one error stays one readable line.

    ``ENAMETOOLONG`` arrives with the whole offending path, and the exception's own text
    repeats it, so an unshortened message is several kilobytes on a single line.
    """
    if len(destination) <= MAX_DESTINATION:
        return destination
    return f"{destination[:MAX_DESTINATION]}... ({len(destination)} characters)"


def _write_stdout(document: str) -> None:
    """Write findings to standard output; no way of failing may outrank the command's verdict.

    Two outcomes, and the difference between them is whether anything went wrong:

    * **A reader that stopped reading is not a fault.** ``check --format json | head`` closes
      the pipe once ``head`` has what it asked for. Reporting that would spend exit code 70 --
      which requirement 12.7 reserves for *unexpected* errors -- on the most routine thing a
      machine consumer does, so it is swallowed and the command's own verdict (req 7.9)
      stands. What is lost is delivery the reader had already stopped taking.
    * **The report could not be delivered**, which is what any other ``OSError`` means. That
      is the same condition ``--output`` reports, so it raises the same located error and
      exits with the same code -- one cause, one answer, whichever destination was used.
    * **Anything else is re-raised as itself**, so the structural handler renders requirement
      12.7's one-liner. Swallowing would be a silent green: findings never delivered, exit 0.

    What all three need is :func:`_detach`. Whatever the failure, the stream is detached
    *first*, because CPython flushes ``sys.stdout`` again at interpreter shutdown -- after
    the exit code has been decided -- and a second failure there replaces the documented
    status with 120 and prints a traceback nobody can act on. Measured on this build:
    ``scitools-hook --version > /dev/full`` exited **120** with 2872 bytes of traceback.

    The final catch is on ``Exception``, not on one errno, because guarding a TYPE rather
    than the outcome is how this defect survived a first fix that named only
    ``BrokenPipeError``: a closed stream raises ``ValueError``, a path decoded with
    ``surrogateescape`` (which is how ``git`` hands us a name that is not valid UTF-8) raises
    ``UnicodeEncodeError``, and a substituted stream can raise ``AttributeError``. Its
    breadth is insurance rather than a measured need -- narrowing it is *equivalent* for
    every shape we can construct, because a stream that raises those has no real descriptor
    for :func:`_detach` to redirect. What the tests prove for those shapes is the re-raise.
    ``KeyboardInterrupt`` and ``SystemExit`` are not ``Exception`` and still propagate.
    """
    try:
        sys.stdout.write(document)
        sys.stdout.flush()
    except BrokenPipeError:
        _detach(sys.stdout)
    except OSError as err:
        _detach(sys.stdout)
        raise _cannot_write("standard output", None, err) from err
    except Exception:
        _detach(sys.stdout)
        raise


def _detach(stream: object) -> None:
    """Point ``stream``'s descriptor at the null device so shutdown cannot raise again.

    Never raises, whatever ``stream`` turns out to be: raising here would put back the very
    interpreter-level failure the caller went to the trouble of avoiding. A capture buffer
    answers ``io.UnsupportedOperation`` (both an ``OSError`` and a ``ValueError``), a closed
    file answers ``ValueError``, and a substituted stream may not define ``fileno`` at all.

    The descriptor is looked up *before* the null device is opened, and the null device is
    closed in a ``finally``, so no failure path leaks a descriptor.
    """
    try:
        target = stream.fileno()  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return
    try:
        null = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(null, target)
    except OSError:
        return
    finally:
        os.close(null)


class ConsoleCommandLog:
    """Prints every external command, its duration and its status to stderr (req 12.8).

    Stateless, so it is a plain class rather than a dataclass: there is nothing to hold.
    """

    def record(self, argv: list[str], seconds: float, rc: int) -> None:
        """One command finished; quote it so a copied line runs as it ran here."""
        echo_err(f"$ {shlex.join(argv)}  [{seconds:.2f}s, rc={rc}]")


@dataclass(frozen=True, slots=True)
class ConsoleProgress:
    """Reports phases on stderr: the slow ones always, all of them under ``--verbose``."""

    verbose: bool = False
    threshold_s: float = SLOW_PHASE_S

    def start(self, phase: str) -> None:
        """A phase begins; announced only when the operator asked for detail."""
        if self.verbose:
            echo_err(f"... {phase}")

    def finish(self, phase: str, seconds: float) -> None:
        """A phase ended; reported when it was slow enough to have been noticed."""
        if self.verbose or seconds >= self.threshold_s:
            echo_err(f"... {phase} finished in {seconds:.1f}s")

    def note(self, message: str) -> None:
        """A one-line diagnostic; always shown, because something chose to say it."""
        echo_err(message)


# --- errors ----------------------------------------------------------------------

_SCALAR_FIELDS: Final[tuple[tuple[str, str], ...]] = (("file", "file"), ("key", "key"))
_LIST_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("tried", "tried"),
    ("available", "available"),
)
_BLOCK_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("stderr", "stderr"),
    ("und_output", "understand said"),
)
"""Context an error may carry, read by attribute so a new subclass renders without edits."""


def exit_code_for(error: BaseException) -> ExitCode:
    """The documented exit code ``error`` maps to (req 1.6).

    A ``GateError`` answers for itself through the class attribute every subclass carries, so
    this cannot fall behind the hierarchy; anything else is unexpected, which requirement
    12.7 requires to be a different code from an analysis failure.
    """
    if isinstance(error, GateError):
        return type(error).exit_code
    return ExitCode.UNEXPECTED


def report_error(error: BaseException, *, verbose: bool) -> ExitCode:
    """Render ``error`` on standard error and return the code the process should exit with.

    A ``GateError`` is already an explanation, so it prints its message, whatever context it
    carries and its hint -- and never a traceback. Anything else prints the one-line form
    requirement 12.7 asks for, with the full traceback only under ``--verbose``.

    The code itself is :func:`exit_code_for`'s answer and is not decided again here: two
    copies of one rule is how requirement 12.7's "distinct from the analysis-failure code"
    ends up enforced on one of them.
    """
    if isinstance(error, GateError):
        echo_err("\n".join(_gate_error_lines(error)))
    else:
        echo_err("\n".join(_unexpected_lines(error, verbose=verbose)))
    return exit_code_for(error)


def _gate_error_lines(error: GateError) -> list[str]:
    """The message, the context the error carries, and the hint -- in that order."""
    lines = [f"error: {error}"]
    lines.extend(_context_lines(error))
    if error.hint:
        lines.append(f"  hint: {error.hint}")
    return lines


def _context_lines(error: GateError) -> list[str]:
    """Whatever locating information this particular error carries (req 1.3, 3.8)."""
    lines: list[str] = []
    for attribute, label in _SCALAR_FIELDS:
        value = getattr(error, attribute, None)
        if value:
            lines.append(f"  {label}: {value}")
    command = getattr(error, "command", None)
    if command:
        lines.append(f"  command: {shlex.join(command)}")
    for attribute, label in _LIST_FIELDS:
        lines.extend(_list_lines(getattr(error, attribute, None), label))
    for attribute, label in _BLOCK_FIELDS:
        lines.extend(_block_lines(getattr(error, attribute, None), label))
    return lines


def _list_lines(values: Sequence[str] | None, label: str) -> list[str]:
    """One bullet per entry, so a long list of tried locations stays readable."""
    if not values:
        return []
    lines = [f"  {label}:"]
    for value in values:
        lines.append(f"    - {value}")
    return lines


def _block_lines(text: str | None, label: str) -> list[str]:
    """Quoted output from another program, indented so it cannot be mistaken for ours."""
    if not text:
        return []
    lines = [f"  {label}:"]
    for line in text.splitlines():
        lines.append(f"    {line}")
    return lines


def _unexpected_lines(error: BaseException, *, verbose: bool) -> list[str]:
    """Requirement 12.7's one-liner, plus the traceback when the operator asked for it."""
    lines = [f"error: {type(error).__name__}: {error}"]
    if verbose:
        lines.extend("".join(traceback.format_exception(error)).splitlines())
    return lines


# --- the application shell -------------------------------------------------------

_CONTROL_FLOW: Final = (typer.Exit, typer.TyperException)
"""Not failures: how click and typer ask for an exit or render a usage message.

``typer.Abort`` is deliberately NOT here. Nothing in this package raises it -- it is what
``confirm(abort=True)`` and ``ctx.abort()` produce, and requirement 12.6 forbids prompting --
so reaching it would mean something unforeseen happened. Letting it through would give
click's exit status **1**, which this CLI has already spent on "blocking violations found":
a hook would block the commit reporting violations that were never measured. It is treated
as the unexpected error it would be.

A real Ctrl-C is unaffected, and the reason is worth stating correctly because the obvious
one is wrong for this stack. Vendored click's ``main()`` would turn ``KeyboardInterrupt``
into ``Abort`` and exit 1 -- which is the very collision described above. What actually runs
is ``typer.core._main``, measured at ``typer/core.py:198-199``: ``except KeyboardInterrupt as
e: raise Exit(130) from e``. A real SIGINT exits **130** with empty stderr. That is a typer
behaviour, not a click one, and it is one of the reasons ``pyproject.toml`` floors typer
rather than accepting whatever a resolver picks.
"""


class GateGroup(TyperGroup):
    """The click group that gives every subcommand the same exit codes (req 1.6, 12.7).

    Wrapping the dispatch rather than each command means a command added later is covered
    the moment it is registered, and a command nested inside a sub-application is covered
    through its parent -- there is no decorator anyone can forget.

    Dispatch is not the only place a command's code runs, which is why BOTH halves are
    wrapped. An **eager option's callback** -- ``--version``, and anything 9.2 or 9.3 adds --
    executes inside ``parse_args``, which click calls from ``make_context``, *before*
    :meth:`invoke` exists to catch anything. Measured before this was closed:
    ``scitools-hook --version > /dev/full`` escaped to the interpreter with 2872 bytes of
    traceback and status 120. A subcommand's own ``make_context`` is called from inside
    ``Group.invoke`` and so is covered by that half.
    """

    def make_context(self, info_name: Any, args: Any, parent: Any = None, **extra: Any) -> Any:
        """Parse, turning a failure raised while parsing into its documented exit code.

        ``--verbose`` is read from the raw arguments because no context exists yet to hold
        the parsed options; that is the whole reason this hole was invisible. The list is
        **copied first**: click's parser consumes ``args`` in place, so by the time an
        exception reaches this handler the flag it is looking for has already been removed.
        """
        given = list(args)
        try:
            return super().make_context(info_name, args, parent, **extra)
        except _CONTROL_FLOW:
            raise
        except Exception as error:
            code = report_error(error, verbose=VERBOSE_FLAG in given)
            raise typer.Exit(code=int(code)) from error

    def invoke(self, ctx: Any) -> Any:
        """Dispatch, turning any failure into its documented exit code.

        ``ctx`` is a click ``Context``. It is annotated ``Any`` rather than named, because
        typer vendors click under a private module whose path is not stable across the
        versions this package accepts, and naming ``typer.Context`` here would narrow the
        supertype's parameter -- the object typer actually passes is the click class.
        """
        try:
            return super().invoke(ctx)
        except _CONTROL_FLOW:
            raise
        except Exception as error:
            code = report_error(error, verbose=_verbose(ctx))
            raise typer.Exit(code=int(code)) from error


def _verbose(ctx: typer.Context) -> bool:
    """Whether ``--verbose`` was given; ``False`` when the options never got published."""
    options = ctx.find_root().obj
    return options.verbose if isinstance(options, GlobalOptions) else False


def document_help(app: typer.Typer) -> None:
    """Give every command and sub-application the shared help epilog (req 12.1).

    Applied once to the assembled application rather than written on each command, so a
    subcommand added by a later task documents the exit codes without being told to.
    """
    for command in app.registered_commands:
        if not command.epilog:
            command.epilog = HELP_EPILOG
    for group in app.registered_groups:
        if not group.epilog:
            group.epilog = HELP_EPILOG
        if group.typer_instance is not None:
            document_help(group.typer_instance)


# --- reusable option declarations ------------------------------------------------

StagedOption = Annotated[bool, typer.Option("--staged", help="Analyse the staged changes.")]
WorktreeOption = Annotated[
    bool, typer.Option("--worktree", help="Analyse the working tree, staged or not.")
]
AllOption = Annotated[bool, typer.Option("--all", help="Analyse the whole project.")]
FilesOption = Annotated[
    list[str] | None,
    typer.Option("--files", metavar="PATH", help="Analyse exactly these files; repeatable."),
]
PathsArgument = Annotated[
    list[str] | None,
    typer.Argument(
        metavar="[PATH]...",
        help="Same as --files: the paths a pre-commit framework appends to the entry line.",
    ),
]
FormatOption = Annotated[
    OutputFormat,
    typer.Option("--format", case_sensitive=False, help="How to render the findings."),
]
OutputOption = Annotated[
    Path | None,
    typer.Option("--output", metavar="PATH", help="Write the findings here instead of stdout."),
]
