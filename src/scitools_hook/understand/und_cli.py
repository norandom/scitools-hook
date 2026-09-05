"""Run the ``und`` command-line tool and turn what it prints into typed results.

Everything here is written against a **measured** ``und`` 6.5 (Build 1204), because this
command line is full of behaviour no manual page implies. Six measurements shape the whole
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
* **``und`` executes a bare ``python`` off ``PATH`` to decide the Python dialect, and
  analyses Python 2 when it finds none.** Same sources, same ``und``, only ``PATH`` differing:
  ``Errors:0`` and both routines with one present, ``Errors:8`` and the routine after the
  parse failure *gone from the database* without one. An absent entity has no metrics, so it
  breaks no threshold and the run reports success — which is why the ``PATH`` handed to
  ``und`` is decided here rather than inherited. :meth:`UndCli._execute` runs every
  invocation under :func:`~scitools_hook.understand.locator.pinned_python`.

**Architectures are the sixth, and they have their own set of measured traps.**
``und import -arch`` is what turns ``structure.layers`` from a rule about folders into a rule
about layers, and four things about it decide the shape of :meth:`UndCli.declare_architecture`:
it must run **after** ``analyze`` (an import into an added-but-unanalysed database produces
empty nodes and the analysis does not fill them in); it refuses a name the database already
holds, while an imported architecture *survives* every ``analyze``, so a warm database has to
be told to forget one before it is given a new one; it takes a document naming files the
project does not hold with status 0 and silently keeps none of them, so the only proof an
import worked is reading it back with ``export -arch``; and the paths it reads are resolved
relative to the directory holding the ``.und`` database, while a *repository*-relative path
resolves to nothing at all and a bare file name resolves by short name. All four are pinned
by ``tests/contract/test_architecture_contract.py`` against the installed build.

Failure mapping follows the design: a non-zero status becomes
:class:`~scitools_hook.errors.AnalysisFailedError` carrying the argv and stderr, licensing
text becomes :class:`~scitools_hook.errors.LicenseError` (requirement 1.4), and a command
that never returns becomes an ``AnalysisFailedError`` too — note that
``subprocess.TimeoutExpired`` is *not* an ``OSError``, so the two have to be caught
separately. Every attempt, including the ones that time out or never start, is recorded on
the injected :class:`~scitools_hook.models.progress.CommandLog` with its timing and status
(requirement 12.8). The two statuses it records for those two failures --
:data:`~scitools_hook.exit_codes.TIMEOUT_RC` and
:data:`~scitools_hook.exit_codes.MISSING_RC` -- come from the package leaf rather than being
defined here, because ``git``, the API worker and the installation probes record the same two
numbers into the same ``--verbose`` stream and one convention cannot be spelled four times.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

from scitools_hook.errors import AnalysisFailedError, LicenseError
from scitools_hook.exit_codes import MISSING_RC, TIMEOUT_RC
from scitools_hook.models.progress import CommandLog
from scitools_hook.models.snapshot import ParseError
from scitools_hook.models.understand import AnalyzeResult, LicenseStatus, UnderstandEnv
from scitools_hook.understand.locator import pinned_python

DEFAULT_TIMEOUT_S: Final = 900
"""Ceiling for one ``und`` call: a full analysis of a large repository still fits."""

LICENSE_DOCS_URL: Final = "https://docs.scitools.com/help/licensing/command-line-licensing.html"
"""Where licensing is done. From the command line, by the operator, never by this tool."""

LICENSE_HINT: Final = (
    f"Licensing is done from the command line -- see {LICENSE_DOCS_URL}. "
    "The licence must carry the 'API Access' option for the Gate to read metrics."
)
"""What every licensing refusal points at. The steps are the vendor's; the check is ours."""

LICENSE_TEXT: Final = re.compile(
    r"licensing error|no und license found|no valid und license found|"
    r"noapilicense|no api license|no server response|license is invalid|"
    r"no checks in this configuration are licensed",
    re.IGNORECASE,
)
"""The licensing sentences built into ``und``, and nothing looser (requirement 1.4).

``No Server Response`` is 8.0's: with no valid offline or node-locked code the build falls
back to its licence server, and on a machine kept off the network that is the whole of what
``und analyze`` says, at exit status 2. It is a licensing failure and is mapped to one (exit
4, requirement 1.4) rather than to a generic analysis failure, so the hook's message names
the licence and not the code. Measured on 8.0.1262, the day the install was replaced.
"""

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

CONFIG_HINT: Final = (
    "This is what und's CodeCheck wrote. Unset codecheck.config to stop running it — that "
    "is the lever that always works, and it is opt-in and unset by default. Naming a "
    "different configuration helps only where the report itself is the problem, not where "
    "a file name is."
)
"""The one lever an operator has: ``codecheck.config`` is opt-in and defaults to unset.

Worded identically to ``codecheck._CONFIG_HINT``: they name the same lever, and a reader who
meets both should not have to work out whether the difference means anything.
"""

EXPORT_PREFIX: Final = "CodeCheckResult"
"""Every CSV ``codecheck`` exports starts with this; anything else is not its output."""

VIOLATIONS_EXPORT: Final = "CodeCheckResultByViolation"
"""The CodeCheck export that lists one row per violation, chosen by name and never by luck.

``codecheck`` writes up to three CSVs — ``CodeCheckResultByFile``,
``CodeCheckResultByTable`` and ``CodeCheckResultByViolation`` (all three names are compiled
into the executable). Taking the alphabetically first of them picks ``…ByFile``, the
directory-*tree* export: it groups violations under file rows whose check id is empty, its
header repeats the ``CheckID`` column, and ``-flattentree`` exists precisely because its
files are "presented in a directory tree format". The per-violation export is the one whose
every row is a violation, so it is asked for by name.
"""

METRIC_LIST_HEADER: Final = "Metrics (+ if selected):"
"""``list -metrics settings`` prints a settings table first and the metric names after this."""

SELECTED_MARKER: Final = "+"
"""Marks a metric the project has enabled; it is a column, not part of the name."""

ARCH_LIST_HEADER: Final = "Architectures:"
"""``list arches`` prints this and then one indented architecture name per line."""

DIRECTORY_STRUCTURE: Final = "Directory Structure"
"""The architecture every database has whether or not anyone declared one.

Derived from the directory layout, so a rule written against it can only ever say what the
folder tree already says -- which is exactly why a repository that wants to gate on *layers*
has to declare one of its own. Measured: it is present in a database that has just been
created and holds no files, and ``und remove -arch "Directory Structure"`` exits 0 and resets
it to the folders rather than deleting it.
"""

ARCH_TAG: Final = "arch"
"""The only element ``und import -arch`` reads; an architecture is a tree of these."""

ARCH_NAME_ATTR: Final = "name"
"""The attribute naming an architecture or one of its nodes."""

ARCH_DOCTYPE: Final = "<!DOCTYPE arch>"
"""The first line ``und export -arch`` writes; reproduced so an emitted file matches it."""

ARCH_LONGNAME_PREFIX: Final = "@l"
"""What ``und export -arch`` puts in front of every member path.

Measured: the prefix is **optional on import** -- a member written without it resolves
identically -- so it is stripped on the way in and written on the way out, and a
hand-written file may leave it off.
"""

ARCH_INDENT: Final = "  "
"""One level of indentation in an emitted architecture file.

Measured: ``und import -arch`` strips leading and trailing whitespace from a member line, so
an indented, human-readable file resolves exactly as Understand's own single-line export does.
"""

ARCH_HINT: Final = (
    "Export a starting point with `scitools-hook db export-arch`, edit it, and commit it: "
    "the file `und import -arch` reads is one <arch> element per node, each holding one "
    "repository-relative file path per line."
)
"""The one thing to do about a file ``und`` would not take: start from a real export."""


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


@dataclass(frozen=True)
class ArchNode:
    """One node of an architecture: a name, the files it holds and the nodes under it.

    The root node's :attr:`name` is the architecture's own name, which is what
    ``structure.architecture`` has to be set to for the layer and arch-cycle rules to read it.
    Members are file paths in whatever frame the holder is working in -- repository-relative
    in the checked-in file, absolute while ``und`` is being spoken to -- and :meth:`rebase`
    is how one frame becomes the other.
    """

    name: str
    members: tuple[str, ...] = ()
    children: tuple[ArchNode, ...] = ()

    def paths(self) -> Iterator[str]:
        """Every member of this node and of everything below it, in document order."""
        yield from self.members
        for child in self.children:
            yield from child.paths()

    def rebase(self, move: Callable[[str], str | None]) -> ArchNode:
        """The same tree with every member path put through ``move``.

        A member ``move`` answers ``None`` for is **dropped**, node and shape kept. That is
        the shape the before side needs: a file added by the change under review is not in
        the before shadow, so its declaration is not something to fail on -- it is simply not
        part of that side's architecture.
        """
        return ArchNode(
            name=self.name,
            members=tuple(moved for member in self.members if (moved := move(member)) is not None),
            children=tuple(child.rebase(move) for child in self.children),
        )


def read_architecture(text: str, source: str) -> ArchNode:
    """Parse one ``und`` architecture document, naming ``source`` in every refusal.

    Measured against build 1204's own export: the document is ``<!DOCTYPE arch>`` followed by
    a single ``<arch name="...">`` element, nested ``<arch>`` elements for the nodes, and the
    member paths as **text**, one per line, each prefixed ``@l``. A member written after a
    child element lands in that child's ``tail`` rather than in the parent's ``text``, so both
    are read.

    ``xml.etree`` is used rather than a regular expression because the failure this has to
    produce is a *typed* one: a malformed file must be refused here, in the operator's own
    words, rather than handed to ``und`` -- which answers ``Error: unable to import
    architecture - malformed XML.`` and exits 1 (measured), a fine outcome but one that names
    neither the line nor the file. It also declines external entities on its own (measured:
    ``ParseError: undefined entity``), so a checked-in file cannot reach outside itself.
    """
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as broken:
        raise AnalysisFailedError(
            f"{source} is not well-formed XML: {broken}", hint=ARCH_HINT
        ) from broken
    if root.tag != ARCH_TAG:
        raise AnalysisFailedError(
            f"{source} has <{root.tag}> at its root, and und import -arch reads <{ARCH_TAG}> there",
            hint=ARCH_HINT,
        )
    return _read_arch_node(root, source)


def write_architecture(node: ArchNode) -> str:
    """Serialise ``node`` into the document ``und import -arch`` reads.

    Indented and one member per line, which Understand's own export is not: measured, an
    import strips the whitespace around a member, so the readable form and the single-line
    form resolve to the same architecture and the readable one is what an operator has to
    keep in version control.
    """
    return "\n".join([ARCH_DOCTYPE, *_arch_lines(node, 0), ""])


def _read_arch_node(element: ElementTree.Element, source: str) -> ArchNode:
    """One ``<arch>`` element and everything under it."""
    name = element.get(ARCH_NAME_ATTR)
    if not name:
        raise AnalysisFailedError(
            f"{source} holds an <{ARCH_TAG}> element with no {ARCH_NAME_ATTR} attribute",
            hint=ARCH_HINT,
        )
    members = _arch_members(element.text)
    children: list[ArchNode] = []
    for child in element:
        if child.tag != ARCH_TAG:
            raise AnalysisFailedError(
                f"{source} holds a <{child.tag}> element inside {name!r}, and und "
                f"import -arch reads only <{ARCH_TAG}>",
                hint=ARCH_HINT,
            )
        children.append(_read_arch_node(child, source))
        members += _arch_members(child.tail)
    return ArchNode(name=name, members=tuple(members), children=tuple(children))


def _arch_members(text: str | None) -> list[str]:
    """The member paths in one run of element text: one per line, ``@l`` optional."""
    if not text:
        return []
    found: list[str] = []
    for line in text.splitlines():
        path = line.strip()
        if path.startswith(ARCH_LONGNAME_PREFIX):
            path = path[len(ARCH_LONGNAME_PREFIX) :].strip()
        if path:
            found.append(path)
    return found


def _arch_lines(node: ArchNode, depth: int) -> Iterator[str]:
    """One node as indented lines, members before children, exactly as ``und`` writes them."""
    pad = ARCH_INDENT * depth
    opening = f"{pad}<{ARCH_TAG} {ARCH_NAME_ATTR}={quoteattr(node.name)}>"
    if not node.members and not node.children:
        yield f"{opening}</{ARCH_TAG}>"
        return
    yield opening
    for member in node.members:
        yield f"{pad}{ARCH_INDENT}{ARCH_LONGNAME_PREFIX}{escape(member)}"
    for child in node.children:
        yield from _arch_lines(child, depth + 1)
    yield f"{pad}</{ARCH_TAG}>"


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
        """Whether a licence is there, from ``und -isundlicensed`` and nothing else (req 1.4).

        This reports rather than raises: ``doctor`` prints the status even when it is bad
        (requirement 1.5), and it is the caller that decides to stop. The switch answers
        ``1`` or ``0``, and the digit is the answer whatever the exit status: 6.5 exits 0
        beside the 0 and 8.0 exits 2 (its licensing reference says so). Measured on 8.0.1262
        before this read the digit: the probe fell through to ``und license`` and ``doctor``
        printed ``license: ok`` on a machine where every ``und analyze`` failed. Anything
        that is not a digit -- a build without the switch, an error -- is reported as not
        established, with und's words, and nothing else is run: this is the one licence
        switch the tool uses, on the user's instruction, because on 8.0 the "read-only"
        licence commands rewrote the licence file.
        """
        probe = self._run(["-isundlicensed"])
        answer = probe.stdout.strip()
        if answer == "1":
            return LicenseStatus(ok=True)
        if answer == "0":
            return LicenseStatus(ok=False, text="und -isundlicensed printed 0: no valid license")
        said = probe.output.strip() or f"exit status {probe.rc} and no output"
        return LicenseStatus(ok=False, text=f"und -isundlicensed did not answer 1 or 0: {said}")

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

    # --- architectures (und import -arch / export -arch) --------------------------

    def list_arches(self, db: Path) -> list[str]:
        """Every architecture ``db`` holds, from ``und -db <db> list arches``.

        Never under ``-quiet``: measured, ``und -quiet list arches`` prints *nothing at all*
        and still exits 0, which is the module's standing silent-answer trap. An empty answer
        is refused for the same reason -- a database always holds ``Directory Structure``,
        even one freshly created with no files in it (measured), so "no architectures" is not
        a state this command has.
        """
        result = self._run(["list", "arches"], db=db)
        self._reject_failure(result)
        self._reject_error_shape(result)
        names = _read_arch_names(result.stdout)
        if not names:
            raise AnalysisFailedError(
                f"und list arches named no architecture in {db}, and every database holds "
                f"at least {DIRECTORY_STRUCTURE!r}",
                command=result.argv,
                stderr=result.stderr,
                hint="Run the command by hand: a silent und usually means a broken install.",
            )
        return names

    def import_arch(self, db: Path, document: Path) -> None:
        """Run ``und import -arch``; the caller must check what actually resolved.

        **This command cannot tell you it worked, and that is measured rather than feared.**
        A document naming files the project does not hold imports with ``Architecture
        imported.`` and status 0, and the members it could not resolve are simply gone --
        a document whose every path is wrong produces an architecture of empty nodes,
        silently. So every caller here goes through :meth:`declare_architecture`, which reads
        the architecture back out and answers what survived.
        """
        result = self._run(["import", "-arch", str(document)], db=db)
        self._reject_failure(result)
        self._reject_error_shape(result)

    def remove_arch(self, db: Path, name: str) -> None:
        """Delete one architecture from ``db``.

        Measured: an architecture the database does not hold answers ``Error: <name> is not a
        valid architecture. Architecture skipped.`` and exits 1, ``-quiet`` or not, so this
        may only be called for a name :meth:`list_arches` has just reported. ``Directory
        Structure`` is built in: removing it exits 0 and leaves it in place.
        """
        self._reject_failure(self._run(["remove", "-arch", name], db=db, quiet=True))

    def export_arch(self, db: Path, name: str, out: Path) -> ArchNode:
        """Write ``name`` to ``out`` and answer it with every member path made absolute.

        Measured: the paths ``und export -arch`` writes are relative to the directory holding
        the ``.und`` database -- ``./after/pkg/core.py`` for a database at ``<root>/after.und``
        over a tree at ``<root>/after`` -- so they are resolved against ``db.parent`` before
        anyone outside this module sees them. An architecture the database does not hold, and
        a file that cannot be written, both exit 1 having written nothing (measured).
        """
        self._reject_failure(self._run(["export", "-arch", name, str(out)], db=db))
        try:
            document = out.read_text(encoding="utf-8")
        except OSError as unreadable:
            raise AnalysisFailedError(
                f"und export -arch wrote no readable file at {out}: {unreadable}",
                hint=ARCH_HINT,
            ) from unreadable
        node = read_architecture(document, f"the {name!r} architecture exported from {db}")
        return node.rebase(lambda member: os.path.realpath(db.parent / member))

    def declare_architecture(self, db: Path, root: ArchNode) -> frozenset[str]:
        """Put ``root`` into ``db``, replacing any architecture of the same name.

        Answers **the member paths that actually resolved**, absolute, so the caller can hold
        the import to what it claimed. Two measurements decide the shape:

        * ``und import -arch`` refuses a name the database already holds -- ``Error: unable to
          import architecture - duplicate name.``, status 1 -- so a warm database, which keeps
          the architecture across ``analyze -changed`` and ``analyze -all`` alike, has to have
          it removed first. Removing and re-importing every run is also what makes the file in
          the repository authoritative: an edited declaration takes effect on the next run
          rather than on the next rebuild.
        * The import must run **after** ``und analyze``. Importing into a database that has
          had ``und add`` but no analysis produces an architecture whose nodes are empty, and
          the analysis that follows does *not* fill them in (measured, both directions). That
          is the single worst failure this feature has: it exits 0, it lists the architecture,
          and every layer rule then evaluates an empty node set.
        """
        if root.name in self.list_arches(db):
            self.remove_arch(db, root.name)
        with tempfile.TemporaryDirectory(prefix="scitools-hook-arch-") as scratch:
            document = Path(scratch) / "architecture.xml"
            document.write_text(write_architecture(root), encoding="utf-8")
            self.import_arch(db, document)
            imported = self.export_arch(db, root.name, Path(scratch) / "read-back.xml")
            return frozenset(imported.paths())

    def codecheck(self, db: Path, config: str, files: list[Path], out_dir: Path) -> Path:
        """Run CodeCheck over ``files`` and return the violations CSV it wrote (req 6.9).

        ``config`` is a configuration name held in the project or the path of an exported
        one, and the two positional arguments follow every switch. Which CSV comes back is
        decided by name — :data:`VIOLATIONS_EXPORT` — because ``codecheck`` writes several
        and they are not interchangeable; ``-violations``, ``-coverage`` and ``-ignores``
        each add more. An output directory holding no CSV at all is a failure rather than
        "no violations".

        ``out_dir`` must be empty. ``codecheck`` can exit 0 having written nothing, so a
        directory reused between runs would hand back the previous run's export as this
        run's results — green on stale data, with nothing to see it by.
        """
        _require_empty(out_dir)
        with _list_file(files) as listing:
            result = self._run(["codecheck", "-files", str(listing), config, str(out_dir)], db=db)
        self._reject_failure(result)
        found = _csv_files(out_dir)
        if not found:
            raise AnalysisFailedError(
                f"und codecheck wrote no csv file into {out_dir}",
                command=result.argv,
                stderr=result.stderr,
                hint=f"Check that {config!r} names a CodeCheck configuration in the project.",
            )
        return _violations_export(found, result, out_dir)

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
        """Run ``argv`` with a pinned ``python``, record it, and turn a non-answer into an error.

        **Every** invocation runs under :func:`~scitools_hook.understand.locator.pinned_python`,
        not only the ones that analyse: the fallback is decided per process, nothing about it
        is remembered in the database, and a wrapper that pinned some calls and not others
        would be a wrapper whose answer depends on which call did the parsing.

        ``env`` is this process's own environment with every *Python* decision taken by
        :meth:`~scitools_hook.understand.locator.PinnedPython.environment` and nothing else
        touched. ``PATH`` alone was not enough: ``PYTHONHOME`` sent a pinned run back to the
        **Python 2** model, and ``PYTHONPATH`` put the analysed project back on the
        interpreter's ``sys.path`` and took 1272 file dependency edges down to 66 -- both
        measured, both recorded there. What is not Python's still arrives untouched, because
        ``und`` reads its licence from ``HOME`` and its Qt configuration from the rest, and a
        probe that handed it a clean environment would be measuring a different program.

        A pin that cannot be built raises before anything is started, so the command is not
        recorded -- it never ran. That is the same treatment ``_list_file`` gives a list file
        it cannot write, for the same reason.
        """
        started = time.monotonic()
        try:
            with pinned_python() as pinned:
                done = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_s,
                    check=False,
                    env=pinned.environment(os.environ),
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
                hint=LICENSE_HINT,
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


def _require_empty(out_dir: Path) -> None:
    """Make ``out_dir`` exist and insist it is empty before ``codecheck`` writes into it.

    Listing the directory is inside the guard with the ``mkdir``: an existing directory the
    process cannot read raises ``PermissionError`` out of ``iterdir`` just as readily as a
    file in the way raises ``FileExistsError`` out of ``mkdir``, and both are ``OSError``s
    that no caught-error tuple in the package expects.

    *Every* entry counts, not only the CSVs: an HTML report or a compliance PDF left by an
    earlier run is the same evidence that this directory has been used before, and the run
    that follows might write nothing at all. The names are listed in sorted order so the
    same directory always produces the same message.
    """
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(path.name for path in out_dir.iterdir())
    except OSError as unusable:
        raise AnalysisFailedError(
            f"the CodeCheck output directory {out_dir} could not be used: {unusable}",
            hint=CONFIG_HINT,
        ) from unusable
    if existing:
        raise AnalysisFailedError(
            f"the CodeCheck output directory {out_dir} already holds {', '.join(existing)}",
            hint="Give every codecheck run its own empty directory: a run that writes "
            "nothing would otherwise return the file an earlier run left behind.",
        )


def _csv_files(out_dir: Path) -> list[Path]:
    """Every CSV in ``out_dir``, sorted, matching the extension without regard to case.

    ``glob("*.csv")`` is case-sensitive on Linux, so an export written as ``.CSV`` would not
    merely be ranked wrongly — it would be invisible, and the directory would read as "no
    results" when it holds them. Directories are skipped because one named ``x.csv`` is not
    a report, and dotfiles because ``und`` writes none — a hidden file is something else's.
    The result is sorted, so the message that lists it is the same every run.
    """
    return sorted(
        path
        for path in out_dir.iterdir()
        if not path.name.startswith(".")
        and path.suffix.casefold() == ".csv"
        and _is_regular_file(path)
    )


def _is_regular_file(path: Path) -> bool:
    """Whether ``path`` is a regular file, letting the reason it cannot be told through.

    ``Path.is_file`` swallows every ``OSError`` and answers ``False``, which turns "I could
    not find out" into "it is not there". Measured: a symlink loop named
    ``CodeCheckResultByViolation.csv`` makes ``is_file()`` answer ``False`` while ``stat``
    raises ``ELOOP`` — so the per-violation export vanishes from the listing, the lone-export
    fallback hands back the by-table schema instead, and nothing says a word. This is the
    same reason :func:`_require_empty` wraps its ``iterdir`` two functions above.

    One shape it diagnoses imprecisely: a *dangling* symlink makes ``stat`` raise
    ``FileNotFoundError``, so the entry is reported as unexaminable rather than as a link to
    nothing. Loud and typed either way, and deliberately not fixed here — settling absence
    with ``lstat`` before asking about kind belongs in the shared classifier this is to be
    replaced by, not in a fourth private copy of it.
    """
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except OSError as unreadable:
        raise AnalysisFailedError(
            f"the CodeCheck output directory holds {path.name}, which could not be "
            f"examined: {unreadable}",
            hint=CONFIG_HINT,
        ) from unreadable


def _violations_export(found: list[Path], result: CommandResult, out_dir: Path) -> Path:
    """The per-violation CSV among the exports ``codecheck`` wrote.

    The per-violation export is taken by name. A *single* CSV is accepted only when its name
    still marks it as one of CodeCheck's own exports (:data:`EXPORT_PREFIX`): with one file
    there is no choice to get wrong, but there is still exactly one wrong outcome, and
    handing back some unrelated CSV that happened to be there would take it silently.
    Anything else fails, naming the directory and every file in it, because that is the case
    where picking one hands the caller a schema it did not ask for.
    """
    for candidate in found:
        if candidate.stem.casefold() == VIOLATIONS_EXPORT.casefold():
            return candidate
    lone = found[0] if len(found) == 1 else None
    if lone is not None and lone.stem.casefold().startswith(EXPORT_PREFIX.casefold()):
        return lone
    raise AnalysisFailedError(
        f"und codecheck wrote no {VIOLATIONS_EXPORT}.csv into {out_dir}; it wrote "
        f"{', '.join(path.name for path in found)}",
        command=result.argv,
        stderr=result.stderr,
        hint=CONFIG_HINT,
    )


def _has_error_line(text: str) -> bool:
    """True when any line carries Understand's ``Error: …`` shape."""
    return any(ERROR_LINE.match(line) for line in text.splitlines())


@contextmanager
def _list_file(paths: Sequence[Path]) -> Iterator[Path]:
    """Write ``paths`` one per line into a throwaway list file and delete it afterwards.

    The file lives in the system temporary directory, never in the repository working tree
    (requirement 2.2), and only exists while ``und`` is reading it.

    Writing it is the one step that can fail on the *name* rather than on the filesystem:
    ``git`` decodes paths with ``surrogateescape``, so a latin-1 file name arrives holding
    surrogates and ``write_text`` raises ``UnicodeEncodeError`` — a ``ValueError``, which is
    neither a ``GateError`` nor an ``OSError`` and so is caught nowhere. Every caller of
    this helper is given a typed error instead, not just the CodeCheck one.
    """
    with tempfile.TemporaryDirectory(prefix="scitools-hook-") as scratch:
        listing = Path(scratch) / "files.txt"
        try:
            listing.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
        except UnicodeEncodeError as unwritable:
            raise AnalysisFailedError(
                f"a file name could not be written to und's list file: {unwritable}",
                hint="git decodes names with surrogateescape, so a name that is not valid "
                "UTF-8 arrives holding surrogates that no encoder will take.",
            ) from unwritable
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


def _read_arch_names(text: str) -> list[str]:
    """The architecture names under ``Architectures:``, one per indented line.

    Measured: the listing ends with a line of two spaces and no newline, so blank lines are
    skipped rather than trusted to be absent, and a name is taken stripped -- ``Directory
    Structure`` has a space in it, so splitting on whitespace would produce two names.
    """
    names: list[str] = []
    listing = False
    for line in text.splitlines():
        if not listing:
            listing = line.strip() == ARCH_LIST_HEADER
            continue
        name = line.strip()
        if name:
            names.append(name)
    return names


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
