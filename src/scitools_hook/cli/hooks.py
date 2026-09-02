"""The ``install-hook`` and ``uninstall-hook`` subcommands (req 11.1, 11.2, 11.6, 11.9).

``git.hooks.HookInstaller`` does the work and answers with an :class:`InstallReport`; this
module turns that report into a line and an exit code. Two things about that mapping:

* **A refusal is returned, not raised, and becomes exit 2 here.** The installer reports
  ``refused`` with a reason that already names ``--force``, because refusing is a normal
  outcome of a normal command rather than an exception. The CLI is where it becomes a
  failure, so it is re-raised as a ``ConfigError``: the operator then gets the same
  ``error: ...`` shape, the same stream (stderr, with standard output left empty) and the
  same exit code as every other refusal in the tool, without a second rendering path.
* **``absent`` is a success.** "Nothing was installed here" and "a hook I did not write is
  here" are different answers -- the installer keeps them apart precisely so that
  ``uninstall-hook`` on a clean repository is not a failure and does not claim to have
  removed anything.

``uninstall-hook`` takes ``--global`` although requirement 12.1 lists no options for it. The
alternative is worse than an extra flag: a shim installed with ``install-hook --global``
lives in the user's hooks path, and from a repository that sets its own ``core.hooksPath``
there would be no way to remove it again.

Requirement 11.7's committed-``.githooks`` layout is deliberately *not* served here. A hooks
directory inside the working tree is refused (requirement 2.2), and the refusal names
``.pre-commit-hooks.yaml``, which is the supported answer for a repository that wants its
hook configuration committed.
"""

from __future__ import annotations

from typing import Annotated, Final

import typer

from scitools_hook.cli import common
from scitools_hook.errors import ConfigError
from scitools_hook.git.hooks import HookInstaller, InstallReport
from scitools_hook.git.repo import GitRepo

INSTALL_HELP = "Install the pre-commit shim into this repository's hooks directory."
UNINSTALL_HELP = "Remove the pre-commit shim and restore whatever it replaced."

FORCE_HELP = "Replace an existing pre-commit hook, keeping it and chaining to it."
GLOBAL_HELP = "Use the user's global hooks path instead of this repository's."

DESCRIPTIONS: Final[dict[str, str]] = {
    "installed": "installed the pre-commit shim at",
    "uninstalled": "removed the pre-commit shim at",
    "restored": "removed the pre-commit shim at",
    "absent": "no pre-commit hook is installed at",
}
"""What each successful action says; ``refused`` is a failure and has no entry."""

CHAINED_NOTE: Final = "kept the hook that was there and chained to it:"
RESTORED_NOTE: Final = "restored the hook it had replaced, from:"
"""Named as the source, because by the time this is printed the stored copy is gone: the
restore is a rename back over the shim, so the path below no longer holds anything."""

FOREIGN_HINT: Final = "Remove or rename it yourself; the Gate only ever removes the shim it wrote."
"""What to do about a ``pre-commit`` the Gate did not write and will not touch."""


def register(app: typer.Typer) -> None:
    """Add ``install-hook`` and ``uninstall-hook`` to ``app``."""
    app.command(name="install-hook", help=INSTALL_HELP)(install_hook)
    app.command(name="uninstall-hook", help=UNINSTALL_HELP)(uninstall_hook)


def install_hook(
    ctx: typer.Context,
    force: Annotated[bool, typer.Option("--force", help=FORCE_HELP)] = False,
    global_: Annotated[bool, typer.Option("--global", help=GLOBAL_HELP)] = False,
) -> None:
    """Install the pre-commit shim (req 11.1, 11.2, 11.9)."""
    report = _installer(ctx).install(force=force, global_=global_)
    _report(report, hint=None)


def uninstall_hook(
    ctx: typer.Context,
    global_: Annotated[bool, typer.Option("--global", help=GLOBAL_HELP)] = False,
) -> None:
    """Remove the pre-commit shim and restore whatever it replaced (req 11.6)."""
    report = _installer(ctx).uninstall(global_=global_)
    _report(report, hint=FOREIGN_HINT)


def _installer(ctx: typer.Context) -> HookInstaller:
    """The installer for the repository the command was run in (exit 6 when there is none)."""
    options = common.global_options(ctx)
    return HookInstaller(GitRepo.discover(options.cwd, options.command_log()))


def _report(report: InstallReport, *, hint: str | None) -> None:
    """Print what happened, or raise the refusal as the configuration error it is."""
    if report.action == "refused":
        raise ConfigError(report.reason, file=report.path, hint=hint)
    common.emit_findings("\n".join(_lines(report)), None)


def _lines(report: InstallReport) -> list[str]:
    """The answer: what was done to which path, and what happened to a chained hook."""
    lines = [f"{DESCRIPTIONS[report.action]} {report.path}"]
    if report.chained is None:
        return lines
    note = RESTORED_NOTE if report.action == "restored" else CHAINED_NOTE
    lines.append(f"{note} {report.chained}")
    return lines
