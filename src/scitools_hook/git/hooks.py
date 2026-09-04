"""Install, chain and remove the pre-commit shim (requirements 11.1-11.6, 11.9).

This module and :mod:`scitools_hook.git.hook_template` are the only parts of the Gate that
write into somebody else's repository, and both halves fail badly when they fail quietly:
one can destroy a hook the operator wrote, the other decides whether a commit is blocked.
So the rules here are deliberately blunt.

**Nothing is overwritten without being kept.** A ``pre-commit`` that is not our shim is
refused; forced, it is *renamed* -- not copied -- to ``pre-commit.scitools-hook-chained``,
which is what makes "restore it exactly" true by construction rather than by care: the inode
keeps its bytes, its mode and its timestamps, and :meth:`HookInstaller.uninstall` renames it
straight back. A copy would have to reproduce the mode, and a restore that drops the
executable bit leaves a hook git silently never runs again.

**Our own shim is never chained to itself.** Forcing an install over a shim replaces it;
chaining would run the Gate twice per commit and, worse, leave ``uninstall`` "restoring" a
shim, so the operator ends up gated by a file with no visible origin.

**A directory inside the working tree is refused.** ``core.hooksPath`` may name one -- ``.``
and every relative value do -- and a shim written there turns up in ``git status``, against
requirement 2.2 and against requirement 11's own objective of switching the Gate on without
committing anything. The refusal is deliberate rather than an omission, and it has an
answer: ``.pre-commit-hooks.yaml`` is how a repository that wants its hooks in the tree
enables the Gate (requirement 11.7). ``.git/hooks`` is not "inside the working tree" for this
purpose, and that is not a special case: git never reports anything under the git directory,
which is exactly why hooks live there.

**The shim holds no configuration** (requirement 11.3). The only thing substituted into the
template is which command was found at install time, chosen from three fixed strings and
written into a comment -- so two repositories with different limits get byte-identical shims,
and no path from the environment ever reaches the script's text.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel

from scitools_hook import __version__
from scitools_hook.errors import ConfigError, GateError
from scitools_hook.git.repo import GitRepo
from scitools_hook.paths import classify_directory, classify_file

HOOK_NAME: Final = "pre-commit"
"""The commit-boundary hook; requirement 11.1 is about that boundary."""

PRE_PUSH_NAME: Final = "pre-push"
"""The push-boundary hook, installed on its own so it can be removed on its own.

A separate hook rather than a mode of the first one, because the two ask different
questions: ``pre-commit`` judges what is staged, and ``pre-push`` judges what the commits
being pushed did -- at push time nothing is staged and the working tree is beside the point.
"""

TEMPLATES: Final[dict[str, str]] = {
    HOOK_NAME: "hook_template.sh",
    PRE_PUSH_NAME: "pre_push_template.sh",
}
"""Which shipped template each hook is rendered from."""

CHAINED_SUFFIX: Final = ".scitools-hook-chained"
"""Appended to the hook's name to store the hook that was there first (requirement 11.2)."""

MARKER: Final = "# scitools-hook-shim v1"
"""The line that identifies a shim as ours, and the only thing ``uninstall`` acts on.

It has to match a **whole line**: a hook that merely quotes the marker in a message would
otherwise be deleted as if the Gate had written it.
"""

TEMPLATE_PATH: Final = Path(__file__).with_name(TEMPLATES[HOOK_NAME])
"""The shipped pre-commit shim, read at install time rather than embedded as a Python string."""


def template_path(hook: str) -> Path:
    """Where ``hook``'s shipped template lives."""
    return Path(__file__).with_name(TEMPLATES[hook])


RESOLVED_PLACEHOLDER: Final = "@SCITOOLS_HOOK_RESOLVED@"
# This tool is distributed as a GitHub release, never on PyPI, so a bare `uvx scitools-hook`
# resolves to nothing. The shim needs a `--from` source, and it is substituted at install time
# rather than written into the template so a fork installs its own.
SOURCE_PLACEHOLDER: Final = "@SCITOOLS_HOOK_SOURCE@"
REPOSITORY: Final = "git+https://github.com/norandom/scitools-hook"
"""Where the tool is fetched from when it is not on PATH."""

UNRELEASED: Final = "0+unknown"
"""The version a source checkout reports when no distribution metadata is installed."""


def default_source(version: str = __version__) -> str:
    """The ``uvx --from`` source a shim is written with: this release, by tag.

    **Pinned, and that is the whole point of this function.** The source used to be the bare
    repository URL, which ``uvx`` resolves to the *default branch* -- so a repository that
    fell through to the uvx path ran whatever had last been pushed to ``main``, including a
    commit made minutes earlier and not released. A gate is the wrong thing to have tracking
    a moving branch: it decides whether commits are allowed, so it has to be a version
    somebody chose.

    A checkout with no distribution metadata reports :data:`UNRELEASED` and has no tag to
    pin, so it falls back to the unpinned URL rather than writing a reference that resolves
    to nothing. That is the only case where a shim tracks a branch, and it is a case where
    the installer is already running from an unreleased tree.
    """
    return REPOSITORY if version == UNRELEASED else f"{REPOSITORY}@v{version}"


RESOLVED_DIRECT: Final = "scitools-hook, found on PATH"
RESOLVED_UVX: Final = "uvx --from the release source (scitools-hook was not on PATH)"
RESOLVED_MISSING: Final = "neither scitools-hook nor uvx was on PATH at install time"
"""The three things the header can say. Fixed strings, so nothing from the environment --
a directory name, a user name, a version -- can reach the script's text."""

SHIM_MODE: Final = 0o755
"""What git needs to run the file, and what git's own sample hooks are created with."""

MARKER_WINDOW: Final = 4096
"""How much of an existing hook is read to look for the marker.

Bounded on purpose: the file is whatever the operator put there, and a reader with no ceiling
meets this project's sixth fault class -- a regular file larger than memory raises
``MemoryError``, which walks past every type-named guard. The marker is the shim's second
line, so the bound only bites on a shim somebody has edited heavily -- and erring toward
"not ours" errs toward leaving the operator's file alone.
"""

GATE_EXECUTABLE: Final = "scitools-hook"
UVX_EXECUTABLE: Final = "uvx"

WORKTREE_HINT: Final = (
    "Point core.hooksPath outside the working tree, or unset it; a repository that wants its "
    "hooks committed should use the .pre-commit-hooks.yaml definition instead."
)

InstallAction = Literal["installed", "refused", "uninstalled", "restored", "absent"]
"""What one call did.

``absent`` is an addition to the four the design lists, and it earns its place: without it,
``uninstall`` on a repository that has no shim must either report ``uninstalled`` -- claiming
to have removed something that was never there -- or ``refused``, which is what a hook the
Gate did not write gets. Those two need different answers from the CLI (one is a success,
the other is a failure), so they need different values here.
"""


class InstallReport(BaseModel):
    """What an install or uninstall did, for the CLI to render (requirement 11.9)."""

    path: Path
    chained: Path | None = None
    action: InstallAction
    reason: str = ""


@dataclass(frozen=True)
class HookInstaller:
    """Installs and removes the shim for one repository."""

    repo: GitRepo

    # --- installing -------------------------------------------------------------

    def install(
        self, force: bool = False, global_: bool = False, hook: str = HOOK_NAME
    ) -> InstallReport:
        """Write the shim into the hooks directory (requirements 11.1, 11.2, 11.9).

        Refuses when a ``pre-commit`` hook is already there, unless ``force`` is given, in
        which case that hook is kept and chained to. Forcing over a shim we wrote replaces it
        and chains nothing -- see the module docstring for why that asymmetry is deliberate.
        """
        directory = self._directory(global_, create=True)
        target = directory / hook
        chained = _chained_path(target)
        existing = classify_file(target)
        if not existing.absent and not existing.usable:
            raise _unusable(target, existing.reason)
        if existing.absent:
            self._write_shim(target)
            return self._installed(target, chained)
        if self._is_shim(target):
            if not force:
                return InstallReport(
                    path=target,
                    action="refused",
                    reason="the Gate's shim is already installed; pass --force to rewrite it",
                )
            self._write_shim(target)
            return self._installed(target, chained)
        if not force:
            return InstallReport(
                path=target,
                action="refused",
                reason=(
                    "a pre-commit hook is already installed and the Gate did not write it; "
                    "pass --force to keep it and run it after the Gate's own check"
                ),
            )
        self._store(target, chained)
        self._write_shim(target)
        return self._installed(target, chained)

    def _installed(self, target: Path, chained: Path) -> InstallReport:
        """Report a written shim, naming a stored hook only when one is really there."""
        stored = None if classify_file(chained).absent else chained
        return InstallReport(path=target, chained=stored, action="installed")

    def _store(self, target: Path, chained: Path) -> None:
        """Move the existing hook aside, refusing rather than overwriting a stored one.

        ``os.rename`` and not a copy: the file keeps its own inode, so its bytes and its mode
        survive without this code having to reproduce either. Both paths are in the same
        directory, so the rename cannot meet ``EXDEV``.
        """
        if not classify_file(chained).absent:
            raise ConfigError(
                f"{chained} already holds a hook the Gate stored earlier, and installing "
                f"over {target} would destroy it",
                file=chained,
                hint="Remove or rename it once you know which of the two hooks you want.",
            )
        try:
            os.rename(target, chained)
        except OSError as broken:
            raise _cannot(f"move {target} aside to {chained}", chained, broken) from broken

    # --- uninstalling -----------------------------------------------------------

    def uninstall(self, global_: bool = False, hook: str = HOOK_NAME) -> InstallReport:
        """Remove the shim and put back what it replaced (requirement 11.6).

        Only ever touches a file carrying :data:`MARKER`. Anything else at that path belongs
        to the operator and is reported rather than removed. The hooks directory is *not*
        created here, and the working-tree refusal that guards :meth:`install` is not applied
        either: taking a file away cannot violate requirement 2.2, and a shim that reached a
        directory this version would refuse must still be removable.
        """
        directory = self._directory(global_, create=False)
        target = directory / hook
        chained = _chained_path(target)
        verdict = classify_file(target)
        if verdict.absent:
            return InstallReport(
                path=target, action="absent", reason="no pre-commit hook is installed here"
            )
        if not verdict.usable:
            raise _unusable(target, verdict.reason)
        # A stored hook beside a foreign one is left alone as well: a refusal must not tidy
        # up around itself, and both files are the operator's.
        if not self._is_shim(target):
            return InstallReport(
                path=target,
                action="refused",
                reason="the pre-commit hook here was not written by the Gate",
            )
        if classify_file(chained).absent:
            self._remove(target)
            return InstallReport(path=target, action="uninstalled")
        self._restore(chained, target)
        return InstallReport(path=target, chained=chained, action="restored")

    def _remove(self, target: Path) -> None:
        """Delete the shim, reporting a failure as the operator's to fix."""
        try:
            target.unlink()
        except OSError as broken:
            raise _cannot(f"remove the shim {target}", target, broken) from broken

    def _restore(self, chained: Path, target: Path) -> None:
        """Rename the stored hook back over the shim, in one step that cannot half-happen."""
        try:
            os.replace(chained, target)
        except OSError as broken:
            raise _cannot(f"restore {chained} as {target}", chained, broken) from broken

    # --- where the shim goes ----------------------------------------------------

    def _directory(self, global_: bool, *, create: bool) -> Path:
        """The hooks directory for this call, checked and -- when installing -- created."""
        directory = self.repo.hooks_dir(global_=global_)
        if create:
            self._reject_inside_worktree(directory)
            self._make_directory(directory)
        return directory

    def _reject_inside_worktree(self, directory: Path) -> None:
        """Refuse a hooks directory that is part of the working tree (requirement 2.2).

        ``core.hooksPath = .`` resolves to the root and every relative value resolves under
        it, so this is reachable from configuration alone -- git 2.43 resolves such a value
        against the worktree root, measured. ``hooks_dir`` deliberately reports those values
        faithfully instead of refusing them, because reading a setting and writing a file are
        different decisions; this is where the writing decision is made.

        The git directory is the exception, and it is not a carve-out for the default case:
        nothing under it is part of the working tree as git sees it -- it never appears in
        ``git status`` and can never be committed -- which is the whole reason hooks live
        there. A linked worktree's hooks resolve into the *common* directory, so both are
        allowed.
        """
        root = self.repo.root.resolve()
        if not directory.is_relative_to(root):
            return
        for private in (self.repo.git_dir.resolve(), self.repo.common_dir.resolve()):
            if directory.is_relative_to(private):
                return
        raise ConfigError(
            f"the hooks directory {directory} is inside the working tree {root}, and the Gate "
            "does not write into a repository's working tree",
            key="core.hooksPath",
            hint=WORKTREE_HINT,
        )

    def _make_directory(self, directory: Path) -> None:
        """Create the hooks directory when it is missing, refusing anything that is not one.

        Creating it is right here and wrong for an operator-typed output path, and the
        difference is what the path is *for*: this is where git will look, the operator has
        just asked for a hook to be installed there, and the alternative is refusing to
        install because a directory the Gate could make does not exist yet. The global
        fallback (``~/.config/git/hooks``) usually does not.
        """
        verdict = classify_directory(directory)
        if verdict.usable:
            return
        if not verdict.absent:
            raise _unusable(directory, verdict.reason)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as broken:
            raise _cannot(f"create the hooks directory {directory}", directory, broken) from broken

    # --- the file itself --------------------------------------------------------

    def _write_shim(self, target: Path) -> None:
        """Write the rendered shim at ``target``, executable, and never half-written.

        The scratch file is created by :func:`tempfile.mkstemp` in the destination's own
        directory: ``O_EXCL`` and an unpredictable name close collision, symlink and FIFO in
        one step, and same-directory is what makes :func:`os.replace` atomic. A partly
        written hook is worth this much care because git would run it -- and a shim truncated
        in the middle of the soft-fail decision blocks every commit in the repository.

        ``mkstemp`` creates ``0600``, so the mode is set explicitly rather than inherited.
        """
        body = render(self._resolved(), target.name)
        handle, name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.")
        scratch = Path(name)
        try:
            self._fill(handle, scratch, body)
            os.replace(scratch, target)
        except OSError as broken:
            raise _cannot(f"write the hook {target}", target, broken) from broken
        finally:
            scratch.unlink(missing_ok=True)

    def _fill(self, handle: int, scratch: Path, body: str) -> None:
        """Write the shim through an already-open descriptor, closing it exactly once.

        The manual close covers only :func:`os.fdopen` itself failing; after it succeeds the
        stream owns the descriptor and closes it on the way out of the ``with``, even when the
        flush inside raises. Closing twice was measured on this project to replace the real
        error (``ENOSPC``, ``EDQUOT``) with ``EBADF``, which sends the operator hunting a Gate
        defect instead of a full disk.

        ``newline="\\n"`` is not decoration: the platform default would write CRLF on Windows,
        and ``#!/bin/sh\\r`` is not a shebang any kernel accepts. ``encoding="utf-8"`` is
        provably equivalent here rather than merely believed to be -- the template is pure
        ASCII, pinned by its own test, so every ASCII-compatible codec produces these bytes.
        """
        try:
            stream = os.fdopen(handle, "w", encoding="utf-8", newline="\n")
        except BaseException:
            os.close(handle)
            raise
        with stream:
            stream.write(body)
        os.chmod(scratch, SHIM_MODE)

    def _is_shim(self, path: Path) -> bool:
        """Whether the file at ``path`` is a shim the Gate wrote.

        Compared as bytes against whole lines: decoding would have to choose an error policy
        for a file the operator may have written in any encoding, and a lenient decode is a
        silent edit. The marker is ASCII, so a byte comparison answers the same question
        without one.
        """
        try:
            with path.open("rb") as stream:
                head = stream.read(MARKER_WINDOW)
        except OSError as broken:
            raise _cannot(f"read the existing hook {path}", path, broken) from broken
        return any(line.strip() == MARKER.encode("ascii") for line in head.splitlines())

    def _resolved(self) -> str:
        """Which command the shim will find, recorded in its header as a diagnostic.

        The shim resolves this again every time it runs -- it has to, since the operator may
        install the tool afterwards -- so this is a note about install time, never a decision
        the hook depends on.
        """
        if shutil.which(GATE_EXECUTABLE):
            return RESOLVED_DIRECT
        if shutil.which(UVX_EXECUTABLE):
            return RESOLVED_UVX
        return RESOLVED_MISSING


def render(resolved: str, hook: str = HOOK_NAME) -> str:
    """``hook``'s shim text, with the install-time note filled in."""
    template = _template(hook)
    if RESOLVED_PLACEHOLDER not in template:
        raise GateError(
            f"the shipped hook template at {template_path(hook)} has no {RESOLVED_PLACEHOLDER} "
            "placeholder, so this installation of scitools-hook is incomplete",
            hint="Reinstall scitools-hook.",
        )
    return template.replace(RESOLVED_PLACEHOLDER, resolved).replace(
        SOURCE_PLACEHOLDER, default_source()
    )


def _template(hook: str = HOOK_NAME) -> str:
    """Read ``hook``'s shipped template, failing with a sentence rather than a traceback.

    A missing template means the package was built or installed wrongly -- it is not the
    operator's configuration and not an analysis failure -- so it carries the unexpected-error
    code with a message that says what to do about it.
    """
    path = template_path(hook)
    verdict = classify_file(path)
    if not verdict.usable:
        raise GateError(
            f"the shipped hook template is missing from {path}: "
            f"{verdict.reason or 'it is not there'}",
            hint="Reinstall scitools-hook; the template ships inside the package.",
        )
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError) as broken:
        raise GateError(
            f"the shipped hook template at {path} could not be read: {broken}",
            hint="Reinstall scitools-hook; the template ships inside the package.",
        ) from broken


def _chained_path(target: Path) -> Path:
    """Where the hook a shim replaced is kept, beside the shim itself.

    The shim derives the same name from ``$0`` rather than from a path written in at install
    time, so a repository that is moved or cloned to a different directory keeps working.
    """
    return target.with_name(target.name + CHAINED_SUFFIX)


def _unusable(path: Path, reason: str) -> ConfigError:
    """The path is taken by something that cannot serve as a hook, or cannot be reached."""
    return ConfigError(
        f"{path} {reason or 'cannot be used'}",
        file=path,
        hint="Remove it, or point core.hooksPath somewhere the Gate can write.",
    )


def _cannot(what: str, path: Path, broken: OSError) -> ConfigError:
    """A filesystem operation the operator has to resolve, named with what failed."""
    return ConfigError(
        f"cannot {what}: {broken.strerror or broken}",
        file=path,
        hint="Check that the hooks directory exists and is writable.",
    )
