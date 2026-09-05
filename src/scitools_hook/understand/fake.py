"""Fixture-backed Understand adapters: the documented ``SCITOOLS_HOOK_FAKE_UNDERSTAND`` seam.

Setting ``SCITOOLS_HOOK_FAKE_UNDERSTAND=<dir>`` makes :func:`runner.context.build_context`
substitute the two adapters here for the real ones, so the whole Gate -- pipelines, rules,
renderers, exit codes, the hook shim -- runs against a directory of JSON files instead of
against an installed Understand. Two audiences need that: the end-to-end tests of task 10.2,
which point the variable at a "violating" fixture directory, watch a real ``git commit`` be
blocked, then re-point it at a "fixed" one and watch the commit succeed; and an agent
developing this tool on a machine with no license. ``tests/fakes`` therefore reuses these
classes rather than growing a second, divergent implementation of the same idea.

**The directory is the whole contract.** ``analyze.json`` is one ``AnalyzeResult``; every
worker operation is ``<op>.<side>.json`` (``snapshot.before.json``, ``snapshot.after.json``)
falling back to ``<op>.json`` for the operations that carry no side -- ``catalogue``,
``impact``, ``graphs`` -- and for a fixture whose two sides are identical. ``codecheck.csv``
is the violations export. Nothing else is read, and the files are handed on unvalidated:
these adapters stand in for the *transport*, so a fixture that is wrong must fail in the same
model validation a real answer would.

**A missing fixture is an error, never an empty answer.** This is the one decision in the
module that matters. An empty ``snapshot`` document validates, evaluates every rule against
nothing and reports a green run — a complete, confident, fictional answer. So every absent
file raises :class:`~scitools_hook.errors.AnalysisFailedError` naming the paths that were
tried. The two deliberate exceptions are stated where they are made: a missing
``analyze.json`` means "this fixture project parses cleanly", which is a real answer and the
common case, and ``ping`` answers from a constant because the fixture directory *is* the
installation being probed.

Both classes subclass the adapter they replace. That is not decoration: mypy compares every
override against the real signature, so a drift in ``UndCli`` or ``ApiRunner`` fails the type
check instead of failing an end-to-end test months later. Neither calls its base
``__init__`` -- there is no installation, no command log and no subprocess behind them.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final

from pydantic import ValidationError

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import Side
from scitools_hook.models.understand import AnalyzeResult, LicenseStatus, UnderstandEnv
from scitools_hook.paths import classify_directory, classify_file
from scitools_hook.understand.api_runner import ApiRunner, Operation
from scitools_hook.understand.und_cli import (
    UndCli,
)

FAKE_VAR: Final = "SCITOOLS_HOOK_FAKE_UNDERSTAND"
"""The environment variable that turns the seam on; its value is the fixture directory."""

ANALYZE_FILE: Final = "analyze.json"
"""What ``und analyze`` answers with: one ``AnalyzeResult`` document (req 2.6)."""

NEAR_MISS_STEM_EDITS: Final = 2
"""How far a filename's *stem* may stray from ``analyze`` before it is read as a typo.

The rule is deliberately two-part -- the stem must be a misspelling of the word **and** the
suffix must be exactly ``.json`` -- because a different extension is a different file *on
purpose*: ``analyze.jsonl``, ``analyze.json5``, ``analyze.json~``, ``analyze.csv`` and
``analyze.json.bak`` are all left alone, as are dotfiles and directories. An earlier version
compared whole filenames at distance 3 and refused every one of those.

No margin is claimed. The previous justification cited a gap measured over a corpus this
author chose, which is a guess about the population dressed as a measurement; a filename
space cannot be sampled that way. What is stated instead is the rule's *known* cost: a
``.json`` file whose stem is within two edits of ``analyze`` is refused even when it was
meant -- ``analyzer.json``, ``analyzed.json`` and ``analyze2.json`` are the reachable
examples -- and they are examples, not a closed list: ``reanalyze.json``, ``analyzers.json``
and ``analyzee.json`` are the same distance away. That is accepted deliberately, because the
two failures are not symmetric. A false
refusal raises immediately, names the file and says what the fixture must be called; the
failure it prevents is a run reporting zero parse errors from a fixture that meant to supply
some. ``analysis.json`` is three edits away and is left alone: it is a different word, not a
misspelling of this one. The matching consequence on the other half of the rule is that an
*extension* typo (``analyze.jsonn``) is not caught either -- nothing can distinguish it from
``analyze.jsonl``, which must be allowed -- so the stem is where the discrimination is made
and 10.2 should still assert that its fixture produced the parse errors it meant to.
"""

ANALYZE_STEM: Final = "analyze"
"""The stem a near-miss is measured against; the suffix is checked separately."""

CODECHECK_FILE: Final = "codecheck.csv"
"""What ``und codecheck`` answers with: a violations export in the real CSV shape (req 6.9)."""

FIXTURE_VERSION: Final = "(Build 0000 fixture)"
"""What the seam reports as ``und version``; deliberately not a plausible real build."""

FIXTURE_API_VERSION: Final = "0.0.0-fixture"
"""What the ``ping`` probe answers; ``doctor`` prints it, so it must be unmistakable."""

SEAM_HINT: Final = (
    f"{FAKE_VAR} is set, so the Gate is reading fixtures instead of a real Understand; "
    "unset it to analyze this repository for real."
)
"""Every failure from the seam says why the Gate was not talking to Understand at all."""


def fake_directory(env: Mapping[str, str]) -> Path | None:
    """The fixture directory the seam is pointed at, or ``None`` when it is off.

    A blank value is off rather than "the current directory": an exported-but-empty variable
    is how a shell says nothing, and reading it as a path would silently replace a real
    installation with an empty fixture directory -- every operation missing, which the seam
    at least reports loudly, but for no reason the operator asked for.
    """
    value = env.get(FAKE_VAR, "")
    return Path(value) if value.strip() else None


def fixture_problem(directory: Path) -> str:
    """Why ``directory`` cannot back the seam, or ``""`` when it can.

    A variable pointing at a missing directory, at a plain file, at a link that leads nowhere,
    or at a directory this user cannot enter, is a typo -- and left
    unchecked it is a dangerous one: ``analyze`` answers "this project parsed cleanly" for a
    directory with no ``analyze.json``, and a directory that does not exist has no
    ``analyze.json`` either. The run would still fail at the first worker operation, but
    ``doctor`` would have reported a healthy installation, so the fault is named here instead.
    """
    verdict = classify_directory(directory)
    if verdict.absent:
        return f"{FAKE_VAR}={directory} names no such directory"
    if not verdict.usable:
        return f"{FAKE_VAR}={directory} {verdict.reason}"
    return ""


def fixture_env(directory: Path) -> UnderstandEnv:
    """The installation the seam presents: the fixture directory, honestly labelled.

    ``doctor`` prints this, so nothing here pretends to be real -- the source names the
    variable, the version names the fixture -- and the paths point inside the directory so
    that a report never shows an installation path that exists somewhere else on the machine.
    """
    return UnderstandEnv(
        home=directory,
        und=directory / "und",
        upython=None,
        python_api_dir=directory / "Python",
        version=FIXTURE_VERSION,
        source=f"fake:{FAKE_VAR}",
        api_mode="inprocess",
    )


@dataclass(frozen=True)
class FixtureRun:
    """One recorded operation: its name and the request it was given."""

    op: str
    request: Mapping[str, object]


@dataclass
class FixtureApiRunner(ApiRunner):
    """An ``ApiRunner`` answering every worker operation from a file in ``directory``."""

    directory: Path
    calls: list[FixtureRun] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialise the base as a real runner would, from the fixture installation.

        The dataclass ``__init__`` replaces ``ApiRunner.__init__``, so without this every
        attribute the base sets is missing, and any member these classes do not override
        fails with ``AttributeError`` instead of answering from fixtures. Every member of
        both bases happens to be overridden today, so nothing currently depends on it --
        which is precisely why it is worth establishing now: the next member added to
        ``UndCli`` or ``ApiRunner`` would otherwise break the fixtures silently, and one
        added during this task's own review did.
        """
        ApiRunner.__init__(self, fixture_env(self.directory), NullCommandLog())

    def run(self, op: Operation, request: Mapping[str, object]) -> dict[str, object]:
        """Answer ``op`` from ``<op>.<side>.json``, or ``<op>.json``, recording the request."""
        self.calls.append(FixtureRun(op, dict(request)))
        if op == "ping":
            return {"version": FIXTURE_API_VERSION, "python": _python_version()}
        with _fixture_contract(f"the {op!r} fixture"):
            return _read_document(self._candidates(op, _side_of(request)), op)

    def _candidates(self, op: str, side: Side | None) -> list[Path]:
        """The files that may answer this operation, most specific first."""
        names = [] if side is None else [f"{op}.{side}.json"]
        return [self.directory / name for name in [*names, f"{op}.json"]]


@dataclass
class FixtureUndCli(UndCli):
    """An ``UndCli`` that runs no process and reads what it needs from ``directory``."""

    directory: Path

    def __post_init__(self) -> None:
        """Initialise the base, so a member this class does not override answers, not raises."""
        UndCli.__init__(self, fixture_env(self.directory), NullCommandLog())

    def version(self) -> str:
        """The seam's own version string; ``doctor`` prints it verbatim."""
        return FIXTURE_VERSION

    def license_status(self) -> LicenseStatus:
        """Always licensed: there is no Understand behind the seam to refuse one."""
        return LicenseStatus(ok=True)

    def create(self, db: Path, languages: list[str], local: bool = True) -> None:
        """Create the database *directory*, because a ``.und`` is a directory (measured).

        The database manager decides between a first run and an incremental one by whether
        this path exists, so a seam that created nothing would rebuild on every commit and
        the end-to-end test of the warm path would silently test the cold one.
        """
        with _fixture_contract(f"creating the fixture database {db}"):
            db.mkdir(parents=True, exist_ok=True)

    def add(self, db: Path, root: Path, exclude: list[str]) -> None:
        """Nothing to add: the fixtures already describe the analysed project."""

    def remove_files(self, db: Path, files: list[Path]) -> None:
        """Nothing to remove, for the same reason."""

    def analyze(self, db: Path, files: list[Path] | None, all: bool = False) -> AnalyzeResult:
        """The analysis ``analyze.json`` describes; a directory without one parsed cleanly.

        The absent file is an answer here, unlike everywhere else in this module: "no parse
        errors, no warnings" is what a healthy fixture project reports, and requiring every
        fixture directory to spell that out would be noise. A file that is present but
        unreadable is still a broken fixture and says so.

        That exception is also the module's one trap, so it is closed rather than documented:
        a directory holding ``analyse.json`` or ``analyze.jsonn`` would report *no parse
        errors* instead of failing the way every other misspelt fixture does, which would turn
        requirement 2.6's end-to-end test green for the wrong reason. A near-miss filename is
        therefore refused by name.
        """
        with _fixture_contract("reading the analysis fixture"):
            return self._analysis()

    def _analysis(self) -> AnalyzeResult:
        """The analysis the fixture directory describes, or the reason it cannot be read."""
        path = self.directory / ANALYZE_FILE
        if not _name_is_taken(path):
            _reject_near_miss(self.directory)
            return AnalyzeResult(seconds=0.0)
        verdict = classify_file(path)
        if not verdict.usable:
            # The classifier's own reason, not a re-derived `is_file()` verdict: it tells a
            # dangling link, an unreachable target, a loop and a plain wrong kind apart, and
            # collapsing them into one message is exactly what `_link_or_kind` exists to stop.
            raise _unusable(
                f"the analysis fixture {path} {verdict.reason}",
                "a directory, FIFO, device or broken link is not an analysis result",
            )
        try:
            return AnalyzeResult.model_validate(_read_document([path], "analyze"))
        except ValidationError as invalid:
            raise _unusable(f"{path} is not an analysis result", str(invalid)) from invalid

    def codecheck(self, db: Path, config: str, files: list[Path], out_dir: Path) -> Path:
        """The fixture violations CSV; its absence is a failure, never "no violations".

        An empty CodeCheck result and a CodeCheck that never ran look identical downstream,
        and the second one must not be reported as a clean file (the same rule the real
        wrapper follows when ``und`` leaves the output directory empty).
        """
        path = self.directory / CODECHECK_FILE
        verdict = classify_file(path)
        if not verdict.usable:
            # Named by kind so the reason is not misleading: "holds no codecheck.csv" about a
            # FIFO or a directory sitting under exactly that name sends the operator looking
            # for a missing file that is right there. The reason comes from the classifier so
            # the four unusable shapes stay distinguishable.
            taken = "does not exist" if verdict.absent else verdict.reason
            raise _unusable(
                f"the fixture violations export {path} {taken}",
                f"looked for {path}",
            )
        return path


# --- reading the fixture files ---------------------------------------------------


def _name_is_taken(path: Path) -> bool:
    """Whether anything at all occupies ``path``, a dangling symlink included.

    ``exists()`` follows symlinks and answers ``False`` for a broken one, so a dangling
    ``analyze.json`` read as "there is no analysis fixture here" -- and a missing analysis
    fixture is the one absence this module treats as an answer.

    ``classify_file`` rather than ``os.path.lexists``: that also swallows ``OSError``, so a
    name inside an unsearchable directory read as untaken. It happened to be covered further
    down (``iterdir`` raises first), which is coverage by accident rather than by design.
    """
    return not classify_file(path).absent


def _reject_near_miss(directory: Path) -> None:
    """Refuse a directory whose analysis fixture is *almost* named ``analyze.json``.

    Every other fixture name fails loudly on its own -- a missing file raises naming the
    paths it tried -- so only this one needs the check, because only this one absence is read
    as an answer. **Kind is not consulted**, deliberately: a misspelt fixture is just as
    unread when it is a directory, a FIFO or a dangling symlink, so a *directory* named
    ``analyzer.json`` is refused too. ``analyzer/`` is left alone by the suffix half of
    :func:`_is_near_miss`, which is what that exclusion always actually rested on.
    """
    for name in _entries(directory):
        if _is_near_miss(name):
            raise _unusable(
                f"{directory} holds {name} but no {ANALYZE_FILE}; an analysis fixture must "
                f"be named exactly {ANALYZE_FILE}",
                f"a missing {ANALYZE_FILE} otherwise reports an analysis with no parse errors",
            )


def _is_near_miss(name: str) -> bool:
    """Whether ``name`` is a misspelling of ``analyze.json`` rather than a different file.

    A leading dot excludes it before anything else: a hidden file is an editor's backup or
    swap file, never the fixture someone meant to write. Nothing here inspects the entry's
    *kind* -- a misspelt fixture is just as unread when it is a directory, a FIFO or a
    dangling symlink, and inspecting the kind is precisely how the previous version let those
    three through.
    """
    if name == ANALYZE_FILE or name.startswith("."):
        return False
    spelled = PurePosixPath(name)
    if spelled.suffix.lower() != ".json":
        return False
    return _edits(spelled.stem.lower(), ANALYZE_STEM) <= NEAR_MISS_STEM_EDITS


def _entries(directory: Path) -> list[str]:
    """The names of everything in ``directory``, sorted; never an untyped raise.

    ``iterdir`` raises ``PermissionError`` on a directory that exists and cannot be read
    (measured), which would leave this module by a door its contract does not have: every
    fixture failure is an ``AnalysisFailedError`` carrying the seam hint. A directory that
    cannot be listed is a broken seam and says so.

    The names are sorted so the *first* near miss reported for a given directory is always the
    same one; without it the error an operator sees depends on the order the filesystem
    happened to hand the entries back.

    **Every entry is listed, whatever kind it is.** An earlier version filtered on
    ``is_file()``, justified by "a directory could never have been meant as the fixture, so
    ``analyzer/`` must not be refused" -- which was wrong twice over. ``analyzer`` is already
    excluded by the *suffix* half of :func:`_is_near_miss`, so the filter protected nothing;
    and ``is_file()`` is ``False`` for a directory, a FIFO **and a dangling symlink**, so
    ``analyse.json`` in any of those three shapes walked straight past the guard and produced
    the silent green the guard exists to prevent (measured: all three).
    """
    try:
        return sorted(entry.name for entry in directory.iterdir())
    except OSError as unreadable:
        raise _unusable(
            f"the fixture directory {directory} cannot be read: {unreadable.strerror}",
            str(unreadable),
        ) from unreadable


def _edits(name: str, target: str) -> int:
    """Levenshtein distance between two filenames; small means "meant to be the other"."""
    previous = list(range(len(target) + 1))
    for index, letter in enumerate(name, start=1):
        current = [index]
        for position, wanted in enumerate(target, start=1):
            current.append(
                min(
                    previous[position] + 1,
                    current[position - 1] + 1,
                    previous[position - 1] + (letter != wanted),
                )
            )
        previous = current
    return previous[-1]


def _side_of(request: Mapping[str, object]) -> Side | None:
    """The side a request names, when it names one; only ``snapshot`` does."""
    side = request.get("side")
    return side if side in ("before", "after") else None


def _read_document(candidates: list[Path], op: str) -> dict[str, object]:
    """The first candidate whose name is free of faults, read as a JSON object.

    A candidate whose name is *taken* but unusable -- a directory, a FIFO, a dangling symlink
    -- is refused by kind rather than skipped, for two reasons. Reporting "no fixture answers"
    about a name that is plainly occupied is an absence claim that is not true; and skipping it
    silently would let a broken ``snapshot.before.json`` fall through to the generic
    ``snapshot.json``, so a fixture directory would answer for the wrong side without saying
    so. This now matches what ``_analysis`` does for ``analyze.json`` -- one policy in the
    module rather than three.
    """
    for path in candidates:
        verdict = classify_file(path)
        if verdict.usable:
            return _parsed(path, op)
        if not verdict.absent:
            raise _unusable(f"the {op!r} fixture {path} {verdict.reason}", f"looked for {path}")
    tried = ", ".join(str(path) for path in candidates)
    raise _unusable(f"no fixture answers the {op!r} operation: looked for {tried}", tried)


def _parsed(path: Path, op: str) -> dict[str, object]:
    """One fixture file as the object the worker would have answered with."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        raise _unusable(f"the fixture {path} cannot be read", str(unreadable)) from unreadable
    except RecursionError as too_deep:
        # Same fault class as the two already mapped in config.loader: `json.loads` answers a
        # deeply nested document with RecursionError, which is not a ValueError, so it left
        # this module untyped and reached `doctor` as an internal defect (exit 70) rather than
        # as the broken fixture it is.
        raise _unusable(f"the fixture {path} nests values too deeply", str(too_deep)) from too_deep
    if not isinstance(document, dict):
        raise _unusable(
            f"the fixture {path} is not a JSON object",
            f"the {op!r} operation answers an object, this one is a {type(document).__name__}",
        )
    return document


def _python_version() -> str:
    """This interpreter's version, as the worker's ``ping`` reports it."""
    return ".".join(str(part) for part in sys.version_info[:3])


@contextmanager
def _fixture_contract(what: str) -> Iterator[None]:
    """Make this module's promise structural: nothing leaves except ``AnalysisFailedError``.

    The promise is an outcome, so it is guarded as one. Enumerating the types a fixture read
    can raise has failed twice here -- ``RecursionError`` from ``json.loads`` is not a
    ``ValueError``, and ``MemoryError`` from a file larger than available memory is neither
    (both measured) -- and each escape reached the CLI as an internal defect, exit 70, for
    what was plainly a broken fixture.
    """
    try:
        yield
    except AnalysisFailedError:
        raise
    except Exception as broken:  # noqa: BLE001 - the outcome is the contract; see above
        raise _unusable(
            f"{what} failed ({type(broken).__name__}): {broken}", str(broken)
        ) from broken


def _unusable(reason: str, detail: str) -> AnalysisFailedError:
    """The error a broken or missing fixture becomes, with the seam named in the hint."""
    return AnalysisFailedError(reason, stderr=detail, hint=SEAM_HINT)
