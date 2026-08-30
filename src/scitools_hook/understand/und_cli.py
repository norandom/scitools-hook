"""Run the ``und`` command-line tool and turn what it prints into typed results.

Everything here is written against a **measured** ``und`` 6.5 (Build 1204), because this
command line is full of behaviour no manual page implies. Four measurements shape the whole
module:

* **Global switches must precede the subcommand.** ``und create -db X -quiet`` answers
  ``Error: -quiet is not a recognized setting`` and exits 1, while ``und -quiet -db X create``
  works. Every argv built here therefore has the shape
  ``und [-quiet] [-db <database>] <subcommand> …``.
* **``-quiet`` silences the answer, not just the noise.** ``und -quiet version`` and
  ``und -quiet license`` print *nothing at all* and still exit 0, and ``und -quiet analyze``
  drops every parse error — the very output requirement 2.6 exists to report. So ``-quiet``
  is used only for the three commands whose output is genuinely discarded (``create``,
  ``add``, ``remove``); ``analyze`` uses ``-errors -warnings`` instead, which keeps the
  errors and the summary line and drops the per-file progress tree.
* **A zero exit status is not proof of success.** ``und -quiet version`` is the standing
  example: no output, status 0. For the two commands whose *stdout is parsed into an answer*
  — :meth:`UndCli.version` and :meth:`UndCli.list_metrics` — an empty answer or Understand's
  own ``Error: …`` shape is therefore rejected even at status 0. ``analyze`` and ``codecheck``
  are deliberately excluded from that check: ``Error:`` lines are their *normal* successful
  output (a parse error is data, not a failure).
* **Understand writes its answers to stdout and its failures to stderr**, and its licensing
  text is fixed English built into the executable (``Licensing Error: …``,
  ``No Und License Found``, ``NoApiLicense``). :data:`LICENSE_TEXT` matches those forms only,
  so a source path or a parse message cannot be mistaken for a licensing problem.

Failure mapping follows the design: a non-zero status becomes
:class:`~scitools_hook.errors.AnalysisFailedError` carrying the argv and stderr, licensing
text becomes :class:`~scitools_hook.errors.LicenseError` (requirement 1.4), and a command
that never returns becomes an ``AnalysisFailedError`` too — note that
``subprocess.TimeoutExpired`` is *not* an ``OSError``, so the two have to be caught
separately. Every attempt, including the ones that time out or never start, is recorded on
the injected :class:`~scitools_hook.models.progress.CommandLog` with its timing and status
(requirement 12.8).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.models.progress import CommandLog
from scitools_hook.models.snapshot import ParseError
from scitools_hook.models.understand import AnalyzeResult, LicenseStatus, UnderstandEnv

DEFAULT_TIMEOUT_S: Final = 900
"""Ceiling for one ``und`` call: a full analysis of a large repository still fits."""

TIMEOUT_RC: Final = 124
"""Status recorded for a command that had to be killed; GNU ``timeout``'s convention."""

MISSING_RC: Final = 127
"""Status recorded for a command that never started; the shell's "not found" convention."""

LICENSE_TEXT: Final = re.compile(
    r"licensing error|no und license found|no valid und license found|"
    r"noapilicense|no api license",
    re.IGNORECASE,
)
"""The licensing sentences built into ``und``, and nothing looser (requirement 1.4)."""

ERROR_LINE: Final = re.compile(r"^\s*Error:\s*(?P<message>.+?)\s*$")
"""``Error: <message>`` — a parse error from ``analyze``, or a refusal from anything else."""

WARNING_LINE: Final = re.compile(r"^\s*Warning:\s*.+$")
"""``Warning: <message>``; only counted, because ``AnalyzeResult`` keeps a count."""

LOCATION_LINE: Final = re.compile(
    r"^\s*File:\s*(?P<path>.+?)(?:\s+Line:\s*(?P<line>\d+))?(?:\s+Col:\s*\d+)?\s*$"
)
"""The line under an error: ``File: <path>`` plus an optional line and column."""

ANALYZE_SUMMARY: Final = re.compile(
    r"Analyze Completed \(Errors:(?P<errors>\d+) Warnings:(?P<warnings>\d+)\)"
)
"""``und``'s own closing tally; absent when the analysis had nothing to do."""

METRIC_LIST_HEADER: Final = "Metrics (+ if selected):"
"""``list -metrics settings`` prints a settings table first and the metric names after this."""

SELECTED_MARKER: Final = "+"
"""Marks a metric the project has enabled; it is a column, not part of the name."""


@dataclass(frozen=True)
class CommandResult:
    """One finished ``und`` invocation, before any of it is interpreted."""

    argv: list[str]
    rc: int
    stdout: str
    stderr: str
    seconds: float

    @property
    def output(self) -> str:
        """Both streams together, for the checks that do not care which one spoke."""
        return f"{self.stdout}\n{self.stderr}"


class UndCli:
    """Every ``und`` subcommand the Gate needs, as a typed method (requirements 1.2, 2.x).

    The instance holds no state beyond its installation, its log and its timeout, so one
    wrapper serves both database sides and every command is independent of the last.
    """

    def __init__(self, env: UnderstandEnv, log: CommandLog, timeout_s: int = DEFAULT_TIMEOUT_S):
        self._env = env
        self._log = log
        self._timeout_s = timeout_s

    # --- environment ------------------------------------------------------------

    def version(self) -> str:
        """What ``und version`` prints, verbatim.

        On build 1204 that is ``(Build 1204)`` and nothing else — no product version — while
        the Python API reports ``6.5.1204``, so callers must not expect a ``6.5.x`` string
        here. ``und -version`` and ``und --version`` are not switches this build knows.
        """
        result = self._run(["version"])
        self._reject_failure(result)
        self._reject_error_shape(result)
        answer = result.stdout.strip()
        if not answer:
            raise AnalysisFailedError(
                "und version printed nothing, so the installation cannot be identified",
                command=result.argv,
                stderr=result.stderr,
                hint="Run the command by hand: a silent und usually means a broken install.",
            )
        return answer

    def license_status(self) -> LicenseStatus:
        """Whether a license is available, and what ``und`` said when it is not (req 1.4).

        This reports rather than raises: ``doctor`` prints the status even when it is bad
        (requirement 1.5), and it is the caller that decides to stop. ``und -isundlicensed``
        answers ``1`` or ``0`` and is preferred because it is unambiguous; ``und license``
        is the fallback for a build that does not know the switch.
        """
        probe = self._run(["-isundlicensed"])
        answer = probe.stdout.strip()
        if probe.rc == 0 and answer == "1":
            return LicenseStatus(ok=True)
        if probe.rc == 0 and answer == "0":
            return LicenseStatus(ok=False, text="und -isundlicensed printed 0: no valid license")
        return self._license_from_command()

    def _license_from_command(self) -> LicenseStatus:
        """Parse ``und license``; a licensed machine prints a reply code and no complaint."""
        told = self._run(["license"])
        text = told.output.strip()
        bad = told.rc != 0 or bool(LICENSE_TEXT.search(text)) or _has_error_line(text)
        return LicenseStatus(ok=not bad, text=text if bad else "")

    # --- database lifecycle -----------------------------------------------------

    def create(self, db: Path, languages: list[str], local: bool = True) -> None:
        """Create an empty database for ``languages`` (requirements 2.1, 2.4).

        ``-local`` keeps the analysis data inside the ``.und`` directory instead of the user
        profile, which is what lets the cache be deleted as one unit. Measured: ``create``
        makes any missing parent directory, and re-creating over an existing database
        rewrites its settings rather than failing.
        """
        argv = ["create", "-languages", *languages]
        if local:
            argv.append("-local")
        self._reject_failure(self._run(argv, db=db, quiet=True))

    def add(self, db: Path, root: Path, exclude: list[str]) -> None:
        """Add ``root`` to the database, honouring the exclude patterns (requirement 2.5).

        ``-exclude`` takes a single comma-separated argument of wildcards; measured, a bare
        directory name in it drops the whole tree.
        """
        argv = ["add"]
        if exclude:
            argv += ["-exclude", ",".join(exclude)]
        argv.append(str(root))
        self._reject_failure(self._run(argv, db=db, quiet=True))

    def remove_files(self, db: Path, files: list[Path]) -> None:
        """Remove files that no longer exist in the shadow tree.

        Measured: an unresolvable path makes ``remove`` exit 1 (``Error: … could not be
        resolved``), so the caller must only pass files the database still holds.
        """
        if not files:
            return
        with _list_file(files) as listing:
            argv = ["remove", "-file", f"@{listing}"]
            self._reject_failure(self._run(argv, db=db, quiet=True))

    def analyze(self, db: Path, files: list[Path] | None, all: bool = False) -> AnalyzeResult:
        """Analyze the whole project, only what changed, or only ``files`` (req 2.3, 2.6).

        ``files=None`` means ``-changed``; an explicit empty list means there is nothing to
        do, which ``und`` itself treats as a no-op exiting 0, so no process is started. The
        parse errors and the warning count come back as data: requirement 2.6 asks for them
        to be reported while every rule still runs.
        """
        if files is not None and not files:
            return AnalyzeResult(seconds=0.0)
        with _analysis_selection(files, all=all) as selection:
            result = self._run(["analyze", *selection, "-errors", "-warnings"], db=db)
        self._reject_failure(result)
        errors, warnings = _read_analysis(result.stdout)
        return AnalyzeResult(parse_errors=errors, warnings=warnings, seconds=result.seconds)

    # --- queries ----------------------------------------------------------------

    def list_metrics(self, db: Path) -> list[str]:
        """Every metric this build offers, from ``und -db <db> list -metrics settings``."""
        result = self._run(["list", "-metrics", "settings"], db=db)
        self._reject_failure(result)
        self._reject_error_shape(result)
        return _read_metric_names(result.stdout)

    def codecheck(self, db: Path, config: str, files: list[Path], out_dir: Path) -> Path:
        """Run CodeCheck over ``files`` and return the violations CSV it wrote (req 6.9).

        ``config`` is a configuration name held in the project or the path of an exported
        one, and the two positional arguments follow every switch. The CSV's name is not
        documented and could not be measured here — this machine's license excludes
        CodeCheck — so the file is *found* in the output directory rather than assumed, and
        an output directory with no CSV in it is a failure rather than "no violations".
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        with _list_file(files) as listing:
            result = self._run(["codecheck", "-files", str(listing), config, str(out_dir)], db=db)
        self._reject_failure(result)
        found = sorted(out_dir.glob("*.csv"))
        if not found:
            raise AnalysisFailedError(
                f"und codecheck wrote no csv file into {out_dir}",
                command=result.argv,
                stderr=result.stderr,
                hint=f"Check that {config!r} names a CodeCheck configuration in the project.",
            )
        return found[0]

    # --- running and mapping ----------------------------------------------------

    def _run(self, argv: list[str], db: Path | None = None, quiet: bool = False) -> CommandResult:
        """Run one ``und`` command with the global switches ahead of the subcommand."""
        head = [str(self._env.und)]
        if quiet:
            head.append("-quiet")
        if db is not None:
            head += ["-db", str(db)]
        return self._execute([*head, *argv])

    def _execute(self, argv: list[str]) -> CommandResult:
        """Run ``argv``, record it whatever happens, and turn a non-answer into an error."""
        started = time.monotonic()
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, timeout=self._timeout_s, check=False
            )
        except subprocess.TimeoutExpired as expired:
            self._log.record(argv, time.monotonic() - started, TIMEOUT_RC)
            raise _timed_out(argv, expired, self._timeout_s) from expired
        except OSError as broken:
            self._log.record(argv, time.monotonic() - started, MISSING_RC)
            raise _unrunnable(argv, broken) from broken
        seconds = time.monotonic() - started
        self._log.record(argv, seconds, done.returncode)
        return CommandResult(argv, done.returncode, done.stdout, done.stderr, seconds)

    def _reject_failure(self, result: CommandResult) -> None:
        """Map a non-zero status, and licensing text on either stream, to a typed error."""
        if LICENSE_TEXT.search(result.output):
            raise LicenseError(
                "SciTools Understand reports that no valid license is available",
                und_output=result.output.strip(),
                hint="Check the license with `und license`, or set one with `und -setlicensecode`.",
            )
        if result.rc != 0:
            raise AnalysisFailedError(
                f"{' '.join(result.argv)} failed with exit status {result.rc}",
                command=result.argv,
                stderr=result.stderr.strip(),
            )

    def _reject_error_shape(self, result: CommandResult) -> None:
        """Refuse Understand's own ``Error: …`` even at status 0, where stdout is the answer.

        Applied only to the commands whose stdout *is* the answer (``version``,
        ``list_metrics``). ``analyze`` prints ``Error:`` lines on a perfectly successful run,
        so it must never be checked this way.
        """
        reported = [line.strip() for line in result.output.splitlines() if ERROR_LINE.match(line)]
        if reported:
            first = reported[0]
            raise AnalysisFailedError(
                f"{' '.join(result.argv)} answered with an error: {first}",
                command=result.argv,
                stderr=result.stderr.strip(),
            )


# --- helpers ------------------------------------------------------------------------


def _timed_out(
    argv: list[str], expired: subprocess.TimeoutExpired, limit: int
) -> AnalysisFailedError:
    """The error a killed command becomes; ``TimeoutExpired`` is not an ``OSError``."""
    captured = expired.stderr
    text = captured.decode(errors="replace") if isinstance(captured, bytes) else (captured or "")
    return AnalysisFailedError(
        f"{' '.join(argv)} timed out after {limit}s",
        command=argv,
        stderr=text.strip(),
        hint="Raise understand.timeout_s, or rebuild the database if the analysis is stuck.",
    )


def _unrunnable(argv: list[str], broken: OSError) -> AnalysisFailedError:
    """The error an executable that never started becomes."""
    return AnalysisFailedError(
        f"{argv[0]} could not be run: {broken}",
        command=argv,
        stderr=str(broken),
        hint="Check the Understand installation directory with `scitools-hook doctor`.",
    )


def _has_error_line(text: str) -> bool:
    """True when any line carries Understand's ``Error: …`` shape."""
    return any(ERROR_LINE.match(line) for line in text.splitlines())


@contextmanager
def _list_file(paths: Sequence[Path]) -> Iterator[Path]:
    """Write ``paths`` one per line into a throwaway list file and delete it afterwards.

    The file lives in the system temporary directory, never in the repository working tree
    (requirement 2.2), and only exists while ``und`` is reading it.
    """
    with tempfile.TemporaryDirectory(prefix="scitools-hook-") as scratch:
        listing = Path(scratch) / "files.txt"
        listing.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
        yield listing


@contextmanager
def _analysis_selection(files: Sequence[Path] | None, all: bool) -> Iterator[list[str]]:
    """The switches naming what to analyze, holding any list file open while ``und`` runs."""
    if all:
        yield ["-all"]
    elif files is None:
        yield ["-changed"]
    else:
        with _list_file(files) as listing:
            yield ["-files", f"@{listing}"]


def _read_analysis(text: str) -> tuple[list[ParseError], int]:
    """Read ``analyze``'s output into parse errors and a warning count (requirement 2.6).

    Understand's Python analyzer runs two passes and reports the same error in each, so the
    same file, line and message is kept once. The warning count comes from the closing
    ``Analyze Completed`` tally when there is one, and from the ``Warning:`` lines when the
    analysis had nothing to do and printed no summary.
    """
    errors: list[ParseError] = []
    seen: set[tuple[str, int | None, str]] = set()
    pending: str | None = None
    warnings = 0
    for line in text.splitlines():
        message = ERROR_LINE.match(line)
        if message:
            pending = message.group("message")
            continue
        if WARNING_LINE.match(line):
            pending, warnings = None, warnings + 1
            continue
        pending = _add_location(line, pending, errors, seen)
    return errors, _summary_warnings(text, warnings)


def _add_location(
    line: str,
    pending: str | None,
    errors: list[ParseError],
    seen: set[tuple[str, int | None, str]],
) -> str | None:
    """Attach a ``File: …`` line to the error above it; answer the still-pending message."""
    location = LOCATION_LINE.match(line)
    if location is None or pending is None:
        return pending
    raw = location.group("line")
    number = int(raw) if raw else None
    key = (location.group("path"), number, pending)
    if key not in seen:
        seen.add(key)
        errors.append(ParseError(path=Path(location.group("path")), line=number, message=pending))
    return None


def _summary_warnings(text: str, counted: int) -> int:
    """Understand's own warning tally, or the lines counted when it printed no summary."""
    summary = ANALYZE_SUMMARY.search(text)
    return int(summary.group("warnings")) if summary else counted


def _read_metric_names(text: str) -> list[str]:
    """The metric names under ``Metrics (+ if selected):``, dropping the selection marker.

    The names arrive two to a line, each optionally preceded by a ``+`` column marking a
    metric the project has enabled; the marker is a column, not part of any name.
    """
    names: list[str] = []
    for line in _metric_lines(text):
        names += [word for word in line.split() if word != SELECTED_MARKER]
    return names


def _metric_lines(text: str) -> Iterator[str]:
    """The indented rows under the metric header, and nothing above or after them.

    The settings table printed above the header holds identifier-shaped option names too
    (``WriteColumnTitles``), so nothing is read before the header, and the list ends at the
    first line that is not part of the indented block.
    """
    listing = False
    for line in text.splitlines():
        if not listing:
            listing = line.startswith(METRIC_LIST_HEADER)
            continue
        if line.strip() and not line.startswith(" "):
            return
        yield line
