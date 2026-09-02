"""The ``init`` and ``config`` subcommands: write a configuration, or show the effective one.

Two commands and one shared question -- *what configuration is in force here?* -- which is why
:func:`effective_configuration` lives in this module and is imported by ``db`` and
``agent-rules`` rather than written out again in each of them.

Three decisions are worth stating:

* **Neither command needs Understand.** ``init`` writes the shipped template and ``config``
  prints what the loader merged; asking either to locate an installation first would make
  ``scitools-hook init`` -- the command an operator runs *before* anything else works -- fail
  with "no usable Understand installation found". Only ``init`` needs git, because requirement
  3.9 writes a *repository-level* file; requirement 12.5 names ``config`` as one of the two
  commands that must run without a working tree.
* **``init`` refuses a destination that is not a regular file, with or without ``--force``.**
  ``config.template.write_template`` guards the ordinary case (a file is there, so pass
  ``--force``), but its guard is ``Path.exists()``, and the force path walks straight past it
  into whatever is at that name: a directory answers ``IsADirectoryError`` at exit 70, a FIFO
  **blocks forever** with no report and no exit code, and a dangling symlink is followed to
  wherever it points -- which can be outside the repository, against requirement 2.2. The kind
  is therefore settled by :func:`~scitools_hook.paths.classify_file` before anything is opened.
* **The source is printed for every leaf, and the leaves are the loader's own** (req 3.10).
  ``Provenance`` holds one entry per merged leaf, so the report iterates *that* and looks each
  key up in the effective settings. The alternative -- walking the settings and asking for a
  source -- would silently omit a key the loader recorded and the settings no longer carry,
  which is exactly what an empty ``[thresholds.<scope>]`` table produces.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Final

import typer

from scitools_hook.cli import common
from scitools_hook.config.loader import _threshold_tables, load_settings, repo_config_path
from scitools_hook.config.models import Provenance, Settings
from scitools_hook.config.template import write_template
from scitools_hook.errors import ConfigError
from scitools_hook.git.repo import GitRepo
from scitools_hook.paths import classify_file
from scitools_hook.runner.context import find_repository

# `_threshold_tables` regroups the flattened `ThresholdSpec` list back into the
# `{scope: {metric: {...}}}` shape the loader recorded provenance against. Importing the
# loader's own function rather than writing a second one is deliberate: a copy that drifted
# would print keys no provenance entry matches and quietly report every threshold as unset.
# (Precedent: `understand/database.py` imports `codecheck._unusable_name` for the same
# reason.) It wants a public name; that is a change to `config/loader`, whose task is closed.

INIT_HELP = "Write a configuration file for this repository."
CONFIG_HELP = "Show the effective configuration and where each setting came from."

CONFIG_HEADER: Final = "# effective configuration; each line ends with the source of its value"
"""One header line, so a reader knows what the trailing ``# source`` on each line means."""

NO_VALUE: Final = "(none)"
"""A key the loader recorded a source for and the effective settings hold nothing under.

Reachable, and the case worth naming: ``[thresholds.arch]`` with no metrics under it records
``thresholds.arch`` as coming from that file while producing no threshold at all. Printing the
key with ``(none)`` says both true things -- the file set it, and it selected nothing.
"""

UNUSABLE_HINT: Final = "Remove what is at that path, or write the configuration somewhere else."

WROTE: Final = "wrote the configuration file"
REPLACED: Final = "replaced the configuration file"


def register(app: typer.Typer) -> None:
    """Add ``init`` and ``config`` to ``app``."""
    app.command(name="init", help=INIT_HELP)(init)
    app.command(name="config", help=CONFIG_HELP)(config)


def init(
    ctx: typer.Context,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing configuration file.")
    ] = False,
) -> None:
    """Write a configuration file for this repository (req 3.9)."""
    options = common.global_options(ctx)
    repo = GitRepo.discover(options.cwd, options.command_log())
    target = repo_config_path(repo.root)
    taken = _reject_unusable(target)
    write_template(target, force=force)
    common.emit_findings(f"{REPLACED if taken else WROTE} {target}", None)


def config(ctx: typer.Context) -> None:
    """Show the effective configuration with the source of every setting (req 3.10, 12.5)."""
    options = common.global_options(ctx)
    repo = find_repository(options.cwd, options.command_log())
    settings, provenance = effective_configuration(options, repo)
    common.emit_findings("\n".join([CONFIG_HEADER, *render_settings(settings, provenance)]), None)


def effective_configuration(
    options: common.GlobalOptions, repo: GitRepo | None
) -> tuple[Settings, Provenance]:
    """The settings this command line produces, and where each of their values came from.

    ``repo`` is passed in rather than looked for here because the commands that need one have
    already found it -- and because it is optional: a repository-level file is one layer of
    the merge, and its absence is what running outside a working tree means (req 12.5).
    ``db`` and ``agent-rules`` call this too, so the four commands that read configuration
    without touching Understand read it the same way.
    """
    root = None if repo is None else repo.root
    return load_settings(root, dict(options.cli_overrides), options.env)


def render_settings(settings: Settings, provenance: Provenance) -> list[str]:
    """One line per configured leaf: ``key = value  # source`` (req 1.5, 3.10).

    Values are rendered as JSON so a table, a list and a string are unambiguous and a run
    produces the same bytes twice; the keys are sorted for the same reason.
    """
    layer = _effective_layer(settings)
    return [
        f"{key} = {_value_of(layer, key)}  # {label}"
        for key, label in sorted(provenance.values.items())
    ]


def _effective_layer(settings: Settings) -> Mapping[str, object]:
    """``settings`` in the nested shape the loader merged and recorded provenance against."""
    layer = dict(settings.model_dump(mode="json"))
    layer["thresholds"] = _threshold_tables(settings.thresholds)
    return layer


def _value_of(layer: Mapping[str, object], key: str) -> str:
    """The effective value at dotted ``key``, as JSON, or :data:`NO_VALUE`.

    The walk tries the longest key segment first because a leaf name may itself contain dots:
    a hint is keyed by its rule (``hints.routine.MaxNesting``), so a walk that split on every
    dot would descend into a table that does not exist and report the hint as unset.
    """
    found = _walk(layer, tuple(key.split(".")))
    return NO_VALUE if found is _MISSING else json.dumps(found, sort_keys=True)


class _Missing:
    """Sentinel: a key with no value under it, which ``None`` cannot express (it is a value)."""


_MISSING: Final = _Missing()


def _walk(value: object, parts: tuple[str, ...]) -> object:
    """The value at ``parts`` inside ``value``, or :data:`_MISSING`."""
    if not parts:
        return value
    if not isinstance(value, Mapping):
        return _MISSING
    for take in range(len(parts), 0, -1):
        name = ".".join(parts[:take])
        if name in value:
            found = _walk(value[name], parts[take:])
            if found is not _MISSING:
                return found
    return _MISSING


def _reject_unusable(target: Path) -> bool:
    """Whether a configuration file is already there; refuse anything that is not one.

    Returns ``True`` for an existing regular file -- which ``write_template`` refuses without
    ``--force`` and replaces with it -- and raises for every other taken name. See the module
    docstring: this is the guard ``--force`` would otherwise walk past.
    """
    verdict = classify_file(target)
    if verdict.absent:
        return False
    if not verdict.usable:
        raise ConfigError(f"{target} {verdict.reason}", file=target, hint=UNUSABLE_HINT)
    return True
