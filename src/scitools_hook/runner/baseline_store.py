"""The baseline file: the only place the Gate reads or writes it (req 8.1, 8.6).

``analysis.baseline`` decides what a baseline *document* means and never touches a disk;
this module owns the file that holds one. The split is what lets requirement 8.6 be
tolerant in the right place: every way a file can be unusable -- absent, a directory, held
open by no one, not JSON at all -- is answered with a :class:`BaselineIssue` and an empty
baseline, so the run continues on configured limits with the operator told why.

Reading reports, writing raises. A baseline that cannot be read is a degraded run the
operator can still trust: the limits fall back to configuration, which is the stricter of
the two by construction (``apply`` takes the *minimum*). A baseline that cannot be *written*
is the opposite -- the operator asked to record the project's current state and it was not
recorded -- and a silent failure there would be discovered a commit later, when a limit the
capture was supposed to establish does not hold. So :meth:`BaselineStore.save` raises a
``ConfigError`` naming the path, which is also the error class whose exit code says "fix
your configuration": the path came from ``baseline.file``.

The written document is sorted and newline-terminated because requirement 8.1 puts it in the
repository by default, where a re-capture that changed nothing must produce no diff.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Sequence
from pathlib import Path

from scitools_hook.analysis.baseline import parse_baseline
from scitools_hook.config.models import Settings, ThresholdSpec
from scitools_hook.errors import ConfigError
from scitools_hook.models.baseline import Baseline, BaselineIssue
from scitools_hook.paths import classify_file

ENCODING = "utf-8"
"""The baseline is JSON, and JSON is UTF-8; never the platform default."""

SAVE_HINT = "Check baseline.file: the directory must exist and be writable."
"""Requirement 8.1 lets the operator choose the location, so the fix is theirs to make."""

DEFAULT_MODE = 0o644
"""What a *new* baseline is created with; ``mkstemp`` would leave it 0600, and this file is
committed to a repository and read by everyone who clones it."""


def baseline_path(settings: Settings, repo_root: Path | None) -> Path:
    """Where ``baseline.file`` points, resolved the way requirement 8.1 describes it.

    The default is "a repository-level file", so a relative path is relative to the
    repository root rather than to whatever directory the operator happened to run from --
    a hook runs from the root, a CI job may not, and both must reach the same baseline. An
    absolute path is left exactly as configured, which is how a team shares one baseline
    across repositories. Outside a repository there is no root to resolve against and the
    path stays as written, relative to the process's own directory.

    There is no explicit "is it absolute?" test, and that is measured rather than assumed:
    ``base / candidate`` already answers ``candidate`` whenever ``candidate`` is absolute, on
    both path flavours -- checked over ``C:/x``, ``/x``, ``D:/x``, ``C:x``, a UNC root, a UNC
    target and a plain relative path, where the guarded and unguarded forms agree in all
    fourteen cases. (The doubling recorded elsewhere in this project is real but belongs to a
    different situation: a ``PurePosixPath`` handed drive-prefixed *text*, which is how
    ``understand/codecheck.py`` parses CSV paths, not how a configured ``Path`` arrives here.)
    """
    configured = settings.baseline.file
    if repo_root is None:
        return configured
    return repo_root / configured


class BaselineStore:
    """Reads and writes one baseline file; holds nothing but its path."""

    def __init__(self, path: Path):
        self.path = path

    def load(self, specs: Sequence[ThresholdSpec]) -> tuple[Baseline | None, list[BaselineIssue]]:
        """The stored baseline and every problem reading it produced (req 8.6).

        **This method does not raise.** That is the requirement, so it is guarded as an
        outcome rather than as a list of expected exception types -- an enumeration of types
        is one unlisted failure away from breaking the promise, and it broke three times here
        before it was written this way: ``RecursionError`` from ``json.loads`` is not a
        ``ValueError``, ``MemoryError`` from a file larger than memory is neither, and
        ``Path.exists()`` silently answered ``False`` for a file it could not reach at all.
        The specific handlers below survive only because they produce better messages.

        A file that does not exist is the one silence: it is the state of every repository
        that has never run ``baseline``, and reporting it would train operators to ignore the
        issue list. "Does not exist" is decided by ``os.lstat`` rather than ``Path.exists()``,
        which swallows every ``OSError`` -- an unsearchable parent, a symlink loop and a
        dangling symlink all answered ``False`` and were reported as *no baseline at all*.
        """
        try:
            return self._read(specs)
        except Exception as broken:  # noqa: BLE001 - the outcome is the contract; see above
            return None, [self._issue(f"could not be read ({type(broken).__name__}): {broken}")]

    def _read(self, specs: Sequence[ThresholdSpec]) -> tuple[Baseline | None, list[BaselineIssue]]:
        """Read the file, reporting each foreseeable problem in its own words."""
        absent, kind = self._kind()
        if absent is not None:
            return absent
        if kind is not None:
            return None, [kind]
        try:
            body = self.path.read_text(encoding=ENCODING)
        except OSError as unreadable:
            return None, [self._issue(f"cannot be read: {unreadable.strerror or unreadable}")]
        except ValueError as undecodable:
            return None, [self._issue(f"is not valid {ENCODING} text: {undecodable}")]
        try:
            document = json.loads(body)
        except ValueError as malformed:
            return None, [self._issue(f"is not valid JSON: {malformed}")]
        except RecursionError as too_deep:
            return None, [self._issue(f"nests values too deeply to be read: {too_deep}")]
        return parse_baseline(document, specs)

    def _kind(
        self,
    ) -> tuple[tuple[Baseline | None, list[BaselineIssue]] | None, BaselineIssue | None]:
        """Whether the path is absent (the answer), unusable (an issue), or a readable file.

        The classification lives in :mod:`scitools_hook.paths` rather than here: the
        same question is asked at four sites in this package, and keeping four copies of the
        answer is what let two of them go on using ``Path.exists()`` after the other two were
        fixed.
        """
        verdict = classify_file(self.path)
        if verdict.absent:
            return (None, []), None
        if not verdict.usable:
            return None, self._issue(verdict.reason)
        return None, None

    def save(self, baseline: Baseline) -> None:
        """Write ``baseline``, replacing whatever the file held (req 8.1, 8.3).

        The document is rewritten whole rather than merged, so a tightened baseline cannot
        leave a stale, higher entry behind -- requirement 8.4 forbids a limit rising, and a
        merge is exactly how one would.

        A writer needs the same kind check a reader does, and for a sharper reason: opening a
        FIFO for writing **blocks forever** with no reader (measured: still blocked at eight
        seconds), and writing through a symlink to ``/dev/null`` returns perfect success while
        storing nothing -- a capture that failed and looks exactly like one that worked. Both
        are refused by ``stat`` before anything is opened. Like :meth:`load`, the promise here
        is an outcome -- *raise ``ConfigError`` naming the path, or succeed* -- so it is
        guarded as one.
        """
        try:
            self._write(baseline)
        except ConfigError:
            raise
        except Exception as unwritable:  # noqa: BLE001 - the outcome is the contract
            raise ConfigError(
                f"the baseline file {self.path} could not be written "
                f"({type(unwritable).__name__}): {unwritable}",
                file=self.path,
                hint=SAVE_HINT,
            ) from unwritable

    def _write(self, baseline: Baseline) -> None:
        """Refuse a destination that should not be overwritten, then replace it atomically."""
        self._reject_unwritable_kind()
        # `ensure_ascii=False` writes real UTF-8 rather than \uXXXX escapes, which is the
        # right shape for a file committed to a repository. It also makes `encoding=` below
        # able to matter -- with the default the document is pure ASCII and the argument could
        # not change a byte -- but *able to matter* is not *pinned*: telling the two apart
        # needs a writer running under a non-UTF-8 ambient encoding, which is a subprocess.
        # Making a line load-bearing and pinning it are separate steps.
        body = json.dumps(
            baseline.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._replace_with(body + "\n")

    def _replace_with(self, body: str) -> None:
        """Write ``body`` beside the destination and rename it on, so a failure loses nothing.

        **The destination is the symlink's target, not the symlink.** Pointing
        ``baseline.file`` at a shared file is a working configuration -- one baseline, several
        repositories -- and the previous version broke it in two ways at once, both measured:
        ``os.replace`` replaced the *link* with a regular file, so the shared baseline was
        never updated and the link was gone; and the mode was read with ``os.lstat``, which on
        Linux reports a symlink as ``0777``, so the new file landed **world-writable inside a
        repository** -- any local user could then move the gate's limits.

        The scratch file comes from :func:`tempfile.mkstemp`, which opens with ``O_EXCL`` and
        an unpredictable name; a hand-built name is a *third* path this method opens without
        classifying, and all three ways of getting that wrong were measured on the version
        before it (a FIFO there blocked forever, a symlink was written through, a regular file
        was destroyed). It is created in the destination's own directory, which is what makes
        ``os.replace`` atomic: across a filesystem boundary it raises ``EXDEV``, and a
        ``/tmp`` on tmpfs is the common way to meet one.

        Accepted cost, recorded rather than fixed: a process killed with ``SIGKILL`` between
        ``mkstemp`` and the rename leaves an orphan ``<name>.<random>`` beside the baseline.
        The name is unpredictable, so nothing will tidy it up, and requirement 8.1 puts it in
        the repository. Closing that needs a fixed-name lock or a startup sweep, which is a
        larger change than the hazard warrants.
        """
        destination = self._destination()
        handle, name = tempfile.mkstemp(dir=destination.parent, prefix=f"{destination.name}.")
        scratch = Path(name)
        try:
            self._fill(handle, scratch, body)
            os.replace(scratch, destination)
        finally:
            scratch.unlink(missing_ok=True)

    def _destination(self) -> Path:
        """The file that actually receives the bytes: the target of a link, or the path itself.

        ``resolve()`` follows symlinked parents too, which is wanted for the same reason:
        the scratch file has to be created on the filesystem the rename will land on.
        """
        return self.path.resolve()

    def _fill(self, handle: int, scratch: Path, body: str) -> None:
        """Write the document through an already-open descriptor, closing it exactly once.

        The manual close covers only :func:`os.fdopen` itself failing, because after it
        succeeds the stream owns the descriptor and closes it on the way out of the ``with``
        -- even when the flush inside raises. Closing it a second time was measured to destroy
        the diagnosis: with ``RLIMIT_FSIZE`` set, the real ``OSError(27, 'File too large')``
        became ``[Errno 9] Bad file descriptor``, so every ``ENOSPC`` and ``EDQUOT`` -- the
        realistic ways writing a baseline fails -- reported as a bad descriptor instead.
        """
        try:
            stream = os.fdopen(handle, "w", encoding=ENCODING)
        except BaseException:
            os.close(handle)
            raise
        with stream:
            stream.write(body)
        os.chmod(scratch, self._mode())

    def _mode(self) -> int:
        """The permissions the replacement should carry: the destination's, or the default.

        ``os.stat``, which follows symlinks, and not ``os.lstat``: a symlink's own mode is
        ``0777`` on Linux, so reading it handed a shared baseline exactly those permissions.
        """
        try:
            return stat.S_IMODE(os.stat(self.path).st_mode)
        except OSError:
            return DEFAULT_MODE

    def _reject_unwritable_kind(self) -> None:
        """Refuse to overwrite a destination the operator put there on purpose.

        The reason is *not* the one two earlier versions gave. The first said the check
        prevented a write that "would either block forever or store nothing"; both halves were
        measured false once the write became a rename -- ``os.replace`` onto a FIFO and onto a
        symlink to ``/dev/null`` each succeed and leave a correct regular file. The second
        then said replacing "a symlink" was not the Gate's to make, which was false for the
        one symlink shape that is a working configuration: a link to a regular file is
        ``usable``, so it is not refused here at all -- it is *followed*, and the file it
        names is what gets replaced.

        What the check actually prevents is the Gate silently destroying something the
        operator put there on purpose and which cannot hold a baseline: a FIFO, a device, a
        directory, or a link to any of those. Naming the real reason is worth more than the
        reassurance it replaces, which was an inference offered as a fact -- twice.
        """
        verdict = classify_file(self.path)
        if verdict.absent or verdict.usable:
            return
        raise ConfigError(
            f"the baseline file {self.path} {verdict.reason}, and replacing it is not "
            f"something a capture should do silently",
            file=self.path,
            hint=SAVE_HINT,
        )

    def _issue(self, problem: str) -> BaselineIssue:
        """One file-level problem, naming the path so the operator can open it."""
        return BaselineIssue(message=f"the baseline file {self.path} {problem}")
