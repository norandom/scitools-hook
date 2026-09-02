"""Deciding whether a path is absent, unusable, or ready to be read.

``Path.exists()``, ``Path.is_file()`` and ``Path.is_dir()`` answer ``False`` for a file that
cannot be reached at all, which is indistinguishable from one that was never created. Every
consumer in this package treats absence as an *answer* -- no baseline was captured, this
repository has never been analysed -- so the two must be told apart or a broken installation
reports as a healthy one that simply has nothing yet.

**Those three predicates are not used here, and the reason is a version range rather than a
preference.** On CPython 3.14 they swallow every ``OSError``; on 3.12 and 3.13 ``pathlib``
filters through ``_ignore_error`` with ``_IGNORED_ERRNOS = (ENOENT, ENOTDIR, EBADF, ELOOP)``
and **re-raises everything else, ``EACCES`` included** -- which is exactly the fault
:func:`_link_or_kind` exists to report. Measured, one probe on three interpreters, a symlink
whose target sits under a ``chmod 000`` directory: 3.12.14 and 3.13.13 both raise
``PermissionError``; 3.14.4 returns ``False``. ``pyproject.toml`` declares
``requires-python = ">=3.12"``, so on two of the three supported interpreters those predicates
would let an untyped ``PermissionError`` escape a function whose whole contract is to return a
verdict. This module therefore asks ``os.stat``/``os.lstat`` directly and classifies the
result. ``os.path.isfile`` would also be version-stable, but it swallows unconditionally and
so destroys the very distinction this module exists to make.

The general shape, which outlives this instance: **a behaviour read off code you did not write
is a claim about a version range, and a test suite that runs one version cannot check a
range.** This project learned it once already with ``typer>=0.27.2``; the standard library is
the harder case only because nobody files it under "dependency".

That is not a hypothetical. The same mistake was made and fixed three times in one task, at
three sites, then made again at a fourth after the class was supposedly swept for, and a
*fifth* copy appeared in another module while that sweep was being reviewed. This module
exists so there is one implementation to fix rather than a shape to remember, and it answers
with ``os.lstat``, which is the only call that distinguishes them: ``FileNotFoundError`` is
genuine absence, and every other ``OSError`` -- ``EACCES`` from an unsearchable parent,
``ELOOP`` from a symlink loop, ``ENAMETOOLONG``, ``ENOTDIR`` from a file used as a directory --
is a path that exists as far as the operator is concerned and cannot be used.

**It lives at the package root, beside** :mod:`scitools_hook.exit_codes`, **not in any layer.**
It imports nothing from the package, and its callers are spread across ``config``,
``understand`` and ``runner``; placing it at the layer of the first two consumers is exactly
what let a third and a fourth copy be written. A leaf every layer may import is the only
placement under which "one implementation" is true rather than locally true.

``lstat`` also does not follow symlinks, which is the other half: a *dangling* symlink answers
``exists()`` ``False`` while its name is plainly taken, and reporting it as "nothing here" hides
the very thing the operator needs to see.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PathVerdict:
    """What a path is: absent, unusable for a stated reason, or ready to use."""

    absent: bool = False
    reason: str = ""

    @property
    def usable(self) -> bool:
        """True only for a path that is there and of the kind that was asked for.

        Both terms are load-bearing. An earlier version of this docstring claimed the
        ``absent`` term was "provably redundant -- every read in ``src/`` is dominated by an
        ``absent`` check", which was wrong twice over: the enumeration behind it missed a
        consumer, and ``understand.fake._read_document`` reads ``usable`` *before* asking
        about absence, because absence there means "try the next candidate". Dropping the term
        fails three tests -- it breaks the documented ``<op>.<side>.json`` -> ``<op>.json``
        fallback, so a fixture directory answers for the wrong side.

        The general point outlives the specific error: an equivalence argued by enumerating
        call sites expires the moment a call site is added. Re-run that mutant whenever a new
        consumer appears rather than trusting the count.
        """
        return not self.absent and not self.reason


def classify_file(path: Path) -> PathVerdict:
    """Whether ``path`` is a readable regular file, absent, or unusable and why.

    A non-regular file is refused by kind rather than discovered by opening it, because a FIFO
    does not fail -- ``read_text`` blocks forever on one with no writer, and ``open`` for
    writing blocks with no reader (both measured). ``stat`` does not block, so the kind is
    settled first. The residual TOCTOU is recorded rather than closed: a regular file swapped
    for a FIFO between this call and the open would still hang, and closing that needs an
    ``O_NONBLOCK`` open rather than a better predicate.
    """
    verdict = _stat(path)
    if verdict is not None:
        return verdict
    if not _is_kind(path, stat.S_ISREG, follow=True):
        return PathVerdict(reason=_link_or_kind(path, "a regular file"))
    return PathVerdict()


def classify_directory(path: Path) -> PathVerdict:
    """Whether ``path`` is a directory this process can read and enter, absent, or unusable.

    Both permission bits are asked for, because they are separate answers: measured, ``0o444``
    grants read but not search and ``0o111`` search but not read, and either alone makes the
    directory's contents unreachable while ``exists()`` and ``is_dir()`` both still say yes.
    """
    verdict = _stat(path)
    if verdict is not None:
        return verdict
    if not _is_kind(path, stat.S_ISDIR, follow=True):
        return PathVerdict(reason=_link_or_kind(path, "a directory"))
    if not os.access(path, os.R_OK | os.X_OK):
        return PathVerdict(reason="cannot be read by this user")
    return PathVerdict()


def _stat(path: Path) -> PathVerdict | None:
    """The verdict ``lstat`` alone settles, or ``None`` when the kind still has to be checked."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return PathVerdict(absent=True)
    except (OSError, ValueError) as unreachable:
        # ValueError, not OSError, is what a NUL byte in the path raises (measured).
        return PathVerdict(reason=f"cannot be reached: {_why(unreachable)}")
    return None


def _is_kind(path: Path, wanted: Callable[[int], bool], *, follow: bool) -> bool:
    """Whether ``path`` is of the wanted kind, answering ``False`` when it cannot be asked.

    This is deliberately the **3.14** ``pathlib`` behaviour -- swallow and answer ``False`` --
    written out so it is the behaviour on 3.12 and 3.13 too, where the predicates re-raise
    ``EACCES`` instead (see the module docstring). Swallowing is safe *here* and nowhere else
    in this module: every caller sends a ``False`` on to :func:`_link_or_kind`, which asks
    again and turns the fault into the operator's reason. The distinction is deferred, not
    discarded -- which is the difference between this and ``os.path.isfile``.
    """
    try:
        info = os.stat(path) if follow else os.lstat(path)
    except (OSError, ValueError):
        return False
    return wanted(info.st_mode)


def _link_or_kind(path: Path, wanted: str) -> str:
    """Why a path whose name is taken is not what was wanted.

    Three different faults, and an operator fixes each of them differently: a link that leads
    nowhere, a link that leads to the wrong kind of thing, and a plain wrong kind. Reporting
    all three as "does not exist" -- which is what ``Path.exists()`` invites -- sends them
    looking for a file that is right there.
    """
    kind = f"is not {wanted}"
    if not _is_kind(path, stat.S_ISLNK, follow=False):
        return kind
    try:
        os.stat(path)
    except FileNotFoundError:
        return "is a symbolic link that leads nowhere"
    except (OSError, ValueError) as unreachable:
        # The one `Path.exists()` this module used to contain, and it was wrong for the case
        # the module docstring names: a target inside an unsearchable directory answers False
        # there, so a link that resolves perfectly well was reported as leading nowhere.
        return f"is a symbolic link whose target cannot be reached: {_why(unreachable)}"
    return f"is a symbolic link that {kind}"


def _why(unreachable: OSError | ValueError) -> str:
    """The operating system's own words, when it has any."""
    reason = getattr(unreachable, "strerror", None)
    return str(reason or unreachable)
