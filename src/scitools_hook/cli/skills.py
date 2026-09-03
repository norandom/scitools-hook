"""The ``install-skills`` subcommand: put the shipped agent skills into a repository.

Enabling a repository was three steps that left the fourth to be done by hand -- write a
configuration, install the hook, write the rules into ``AGENTS.md``, and then somehow obtain
the skills, which existed only in this project's own checkout. An operator installing the
tool with ``uvx`` never sees that checkout, so the skills reached nobody. They ship inside
the package now (:mod:`scitools_hook.skills`) and this command copies them out.

Three decisions:

* **The default directory is ``.agents/skills``, not ``.claude/skills``.** The layout is the
  vendor-neutral one, so a repository does not acquire one assistant's directory as a side
  effect of installing a maintainability gate. ``--dir`` targets a host that reads somewhere
  else, and the documentation names ``.claude/skills`` for Claude Code.
* **The directory is created, unlike ``check --output``'s.** The distinction is ownership:
  ``--output`` names a path the operator typed, where a missing parent means a typo and
  creating it silently writes the report somewhere nobody will look, whereas this is a
  directory whose whole layout the skill format dictates and the tool owns.
* **A file that differs is refused, not overwritten.** A skill is a document an operator may
  have edited for their repository, and replacing an edited copy on an unrelated run of
  "install the tool" would lose that work with no record. ``--force`` is the way to take the
  shipped version back, and the refusal names it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer

from scitools_hook import skills as packaged
from scitools_hook.cli import common, targets
from scitools_hook.errors import ConfigError

HELP = "Install the agent skills that drive this tool into a repository."

LONG_HELP = f"""{HELP}

Writes three SKILL.md documents an agent host can load: `scitools-gate` (check a change),
`scitools-improve` (lower the complexity of a project commit by commit) and `scitools-adapt`
(change the rules themselves, with the measurement behind each decision).

The default location is `.agents/skills`, which is vendor-neutral. Use `--dir .claude/skills`
for Claude Code, or any other path your assistant reads.
"""

DEFAULT_DIR: Final = Path(".agents/skills")
"""Where the skills go when ``--dir`` is not given, relative to the repository root."""

DIR_HELP = "Write the skills here instead of .agents/skills."
FORCE_HELP = "Replace a SKILL.md that differs from the shipped one."

DIR_OPTION: Final = "--dir"
FORCE_OPTION: Final = "--force"

INSTALLED: Final = "installed"
REPLACED: Final = "replaced"
UNCHANGED: Final = "already up to date"
"""The three outcomes per skill. ``UNCHANGED`` is a success and says nothing was written."""

DIFFERS: Final = "holds a different version of the {name} skill"
FORCE_HINT: Final = f"Pass {FORCE_OPTION} to replace it with the version this release ships."

UNUSABLE_HINT: Final = "Name a directory the skills can be written into."

NEXT_STEPS: Final = (
    "Your agent can now run /scitools-gate to check a change, /scitools-improve to lower this "
    "project's complexity one commit at a time, and /scitools-adapt to change the rules with "
    "the measurement behind each decision."
)
"""What the skills are for, said once, where an operator has just installed them."""


def register(app: typer.Typer) -> None:
    """Add ``install-skills`` to ``app``."""
    app.command(name="install-skills", help=LONG_HELP)(install_skills)


def install_skills(
    ctx: typer.Context,
    directory: Annotated[
        Path | None, typer.Option(DIR_OPTION, metavar="DIR", help=DIR_HELP)
    ] = None,
    force: Annotated[bool, typer.Option(FORCE_OPTION, help=FORCE_HELP)] = False,
) -> None:
    """Copy the packaged skills into this repository."""
    options = common.global_options(ctx)
    root = targets.repository_root(options)
    target = (root / DEFAULT_DIR) if directory is None else Path(options.cwd) / directory
    lines = [_install(skill, target, force=force) for skill in packaged.shipped()]
    common.emit_findings("\n".join([*lines, "", NEXT_STEPS]), None)


def _install(skill: packaged.PackagedSkill, directory: Path, *, force: bool) -> str:
    """Write one skill beneath ``directory`` and say what happened to it."""
    target = directory / skill.relative_path
    action = _decide(skill, target, force=force)
    if action is not UNCHANGED:
        target.parent.mkdir(parents=True, exist_ok=True)
        common.emit_findings(skill.text, target, option=DIR_OPTION)
    return f"{action}: {skill.name} at {target}"


def _decide(skill: packaged.PackagedSkill, target: Path, *, force: bool) -> str:
    """Whether ``target`` gets written, and what to call the outcome.

    The comparison is on text rather than on bytes so that a copy checked out with a
    different line ending is not reported as an edit an operator made.
    """
    existing = targets.read_existing(target, option=DIR_OPTION, hint=UNUSABLE_HINT)
    if existing is None:
        return INSTALLED
    if existing.splitlines() == skill.text.splitlines():
        return UNCHANGED
    if force:
        return REPLACED
    raise ConfigError(
        f"{target} {DIFFERS.format(name=skill.name)}",
        file=target,
        key=FORCE_OPTION,
        hint=FORCE_HINT,
    )
