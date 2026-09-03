"""Where a command writes, and what is already there.

Two commands merge into a file the operator owns rather than replacing one the Gate owns:
``agent-rules --write`` inserts its block between markers, and ``install-skills`` compares a
skill against the shipped one to tell an edit from a stale copy. Both have to read before
they write, and both have to decide *where* the default destination is.

Neither belongs in ``common``. That module is already 39 declared functions against a limit
of 25 -- its own gate says so -- and "the destination of this command" is a cohesive group
rather than one more utility. The two halves live here together because they are asked in
the same breath: resolve the path, then find out what is at it.

The guard on the read is the load-bearing part. It is as dangerous as the write: a FIFO at
the path blocks forever with no writer, and a dangling symlink would have the caller's
document written wherever the link points, possibly outside the repository (req 2.2). The
kind is therefore settled by ``classify_file`` before anything is opened, and the refusal
carries ``ReportUndeliverableError`` so that one physical cause gets one exit code -- the
same one ``common.emit_findings`` raises when the *write* is the half that meets it.
"""

from __future__ import annotations

from pathlib import Path

from scitools_hook.cli.common import GlobalOptions
from scitools_hook.errors import ReportUndeliverableError
from scitools_hook.paths import classify_file
from scitools_hook.runner.context import find_repository


def repository_root(options: GlobalOptions) -> Path:
    """The repository containing the working directory, or that directory when there is none.

    A default destination has to mean the same thing from any depth of the tree, so it is
    resolved against the root -- the asymmetry ``baseline --file`` draws, where a path the
    operator *typed* keeps meaning what it means where they typed it.

    A repository is not required. Refusing for want of git would only push an operator into
    copying files by hand, which is the work these commands exist to remove (req 12.5).
    """
    repo = find_repository(options.cwd, options.command_log())
    return options.cwd if repo is None else repo.root


def read_existing(target: Path, *, option: str, hint: str) -> str | None:
    """What is at ``target`` now, or ``None`` when nothing is there yet.

    ``option`` is the spelling that produced ``target`` and is what a failure names, for the
    reason ``emit_findings`` takes the same argument: reporting an option the operator never
    passed sends them looking in the wrong place.
    """
    verdict = classify_file(target)
    if verdict.absent:
        return None
    if not verdict.usable:
        raise ReportUndeliverableError(
            f"cannot write to {target}: it {verdict.reason}", key=option, hint=hint
        )
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, ValueError) as unreadable:
        # ValueError covers a file that is not UTF-8 text: merging into it would have to
        # choose an error policy for bytes the operator wrote, and every choice is a silent
        # edit of a file the Gate does not own.
        raise ReportUndeliverableError(
            f"cannot read {target}: {unreadable}", key=option, hint=hint
        ) from unreadable
