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
* **Detection proposes; it never applies.** The settings are read *before* detection runs,
  so ``[project] include``/``exclude`` come back as the ``not-analysed`` regions: "what is
  this run not looking at?" has no other answer, and it is the half of the feature that keeps
  an exclusion honest. ``init --detect`` and ``config --detect`` read
  what the repository declares about its own directories (``config.detect``) and write it out
  *with the evidence beside each line*. ``init --detect --print`` sends the same document to
  standard output and touches no file, which is how a proposal is read before it is taken.
  ``config --why PATH`` answers the other half -- why is this path treated the way it is --
  from the same data, so the report and the generated file can never disagree.
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
from scitools_hook.config.detect import Detection, detect
from scitools_hook.config.loader import load_settings, repo_config_path, threshold_tables
from scitools_hook.config.models import Provenance, ScopeOverride, Settings
from scitools_hook.config.template import Proposal, propose, render_template, write_template
from scitools_hook.errors import ConfigError, NotAGitRepositoryError
from scitools_hook.git.repo import GitRepo
from scitools_hook.paths import classify_file
from scitools_hook.runner.context import find_repository

# `threshold_tables` regroups the flattened `ThresholdSpec` list back into the
# `{scope: {metric: {...}}}` shape the loader recorded provenance against. Importing the
# loader's own function rather than writing a second one is deliberate: a copy that drifted
# would print keys no provenance entry matches and quietly report every threshold as unset.
# It was `_threshold_tables` and this import was the last entry in
# `tests/test_import_direction.py`'s RECORDED_PRIVATE_IMPORTS; the other one --
# `understand/codecheck._unusable_name`, which used to be cited here as a precedent -- was
# given a public name in task 11.7, so the "precedent" was a problem that had been fixed
# everywhere but here. It now has a public name too and the table is empty.

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
    detect_scopes: Annotated[bool, typer.Option("--detect", help=DETECT_HELP)] = False,
    to_stdout: Annotated[bool, typer.Option("--print", help=PRINT_HELP)] = False,
) -> None:
    """Write a configuration file for this repository (req 3.9)."""
    options = common.global_options(ctx)
    repo = GitRepo.discover(options.cwd, options.command_log())
    proposal = _proposal(options, repo) if detect_scopes else None
    if to_stdout:
        common.emit_findings(render_template(proposal=proposal), None)
        return
    target = repo_config_path(repo.root)
    taken = _reject_unusable(target)
    write_template(target, force=force, proposal=proposal)
    common.emit_findings(f"{REPLACED if taken else WROTE} {target}", None)


def _proposal(options: common.GlobalOptions, repo: GitRepo) -> Proposal:
    """What a detection of ``repo`` suggests, built on the configuration already in force."""
    settings, _ = effective_configuration(options, repo)
    return propose(detect(repo.root, repo.tracked_files(), settings.project), settings)


def config(
    ctx: typer.Context,
    detect_scopes: Annotated[bool, typer.Option("--detect", help=DETECT_HELP)] = False,
    why: Annotated[str | None, typer.Option("--why", metavar="PATH", help=WHY_HELP)] = None,
) -> None:
    """Show the effective configuration with the source of every setting (req 3.10, 12.5)."""
    options = common.global_options(ctx)
    repo = find_repository(options.cwd, options.command_log())
    if detect_scopes or why is not None:
        common.emit_findings("\n".join(_explain(options, repo, why)), None)
        return
    settings, provenance = effective_configuration(options, repo)
    common.emit_findings("\n".join([CONFIG_HEADER, *render_settings(settings, provenance)]), None)


def _explain(options: common.GlobalOptions, repo: GitRepo | None, why: str | None) -> list[str]:
    """The detection report, or the explanation of one path; both need a working tree."""
    if repo is None:
        raise NotAGitRepositoryError(NEEDS_REPOSITORY, hint="run it inside a git repository")
    settings, _ = effective_configuration(options, repo)
    found = detect(repo.root, repo.tracked_files(), settings.project)
    if why is None:
        return [DETECT_LEGEND, *render_detection(found, settings)]
    return [WHY_LEGEND, *render_why(why, found, settings)]


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
    layer["thresholds"] = threshold_tables(settings.thresholds)
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


# --- detection: propose a configuration, and explain one path ----------------------

DETECT_HELP = "Classify the repository from what it declares about itself, with the evidence."
WHY_HELP = "Explain how one path is classified and which scopes apply to it."
PRINT_HELP = "Write the configuration to standard output instead of to the file."

NEEDS_REPOSITORY: Final = "detection reads the tracked file list, so it needs a working tree"

DETECT_LEGEND: Final = (
    "# what this repository declares about itself; nothing here is applied -- "
    "`init --detect` writes it for review"
)
"""Printed above the classification, because a report that looks like a verdict invites one."""

WHY_LEGEND: Final = "# classification, evidence, and the path scopes that change its thresholds"

NO_REGION: Final = "no region covers this path; it is product code by default"
NO_SCOPE: Final = "no path scope matches; the global thresholds apply unchanged"

MULTI_SCOPE: Final = (
    "two or more scopes match: they are applied in the order listed and the later one wins per rule"
)


def render_detection(found: Detection, settings: Settings) -> list[str]:
    """One line per region: role, pattern, how much it covers, and what said so."""
    lines = [
        f"{region.role:<12} {region.pattern:<36} {_covered(region.covered):<18} "
        f"{region.evidence.describe()}"
        for region in found.regions
    ]
    return [
        *(lines or ["(nothing declared)"]),
        *_render_limitations(found),
        *_render_stale(found, settings),
    ]


def _render_stale(found: Detection, settings: Settings) -> list[str]:
    """Acknowledgements that cover nothing here -- the entry an operator forgot to delete.

    An acknowledgement matching no file produces no output of its own anywhere else, so it
    goes on excusing a file that was fixed or deleted for as long as nobody looks. This is
    where somebody looks.
    """
    stale = settings.parse.unused(found.tracked)
    if not stale:
        return []
    return [
        "",
        "# [parse] entries that cover no tracked file; delete them or correct the paths",
        *(f"{'stale':<12} {', '.join(entry.paths):<36} {entry.reason}" for entry in stale),
    ]


def _render_limitations(found: Detection) -> list[str]:
    """The files Understand cannot read to the end, which are not a region of their own."""
    if not found.limitations:
        return []
    return [
        "",
        "# files the analyser could not read (see [parse] in the `init --detect` output)",
        *(f"{item.signal:<12} {item.source:<36} {item.detail}" for item in found.limitations),
    ]


def _covered(count: int) -> str:
    return "1 tracked file" if count == 1 else f"{count} tracked files"


def render_why(path: str, found: Detection, settings: Settings) -> list[str]:
    """Everything that decides how ``path`` is treated, in the order it decides it."""
    covering = found.covering(path)
    lines = [f"path: {path}", f"role: {found.role_of(path)}"]
    lines.extend(
        f"  {region.role:<12} {region.pattern:<28} {region.evidence.describe()}"
        for region in covering
    )
    if not covering:
        lines.append(f"  {NO_REGION}")
    lines.extend(_render_scopes(path, settings))
    lines.extend(_render_acknowledgement(path, settings))
    return lines


def _render_scopes(path: str, settings: Settings) -> list[str]:
    """The path scopes that match, in the order they are applied, and what each one says."""
    matching = [(name, scope) for name, scope in settings.scope.items() if scope.matched_by(path)]
    if not matching:
        return ["scopes: none", f"  {NO_SCOPE}"]
    lines = [f"scopes: {', '.join(name for name, _ in matching)}"]
    if len(matching) > 1:
        lines.append(f"  {MULTI_SCOPE}")
    for name, scope in matching:
        lines.append(f"  [scope.{name}] matched by {scope.matched_by(path)!r}")
        lines.extend(
            f"    {threshold_scope}.{metric} = {_override_text(override)}"
            for threshold_scope, table in scope.thresholds.items()
            for metric, override in table.items()
        )
    return lines


def _override_text(override: ScopeOverride) -> str:
    """One override as an operator wrote it, so the report and the file read the same."""
    if override.disabled:
        return "false (the rule does not apply here)"
    parts: list[str] = []
    if override.limit is not None:
        parts.extend(
            f"{bound}={value:g}"
            for bound, value in (("max", override.limit.max), ("min", override.limit.min))
            if value is not None
        )
    if override.severity is not None:
        parts.append(f"severity={override.severity}")
    if override.ratchet is not None:
        parts.append(f"ratchet={str(override.ratchet).lower()}")
    return ", ".join(parts)


def _render_acknowledgement(path: str, settings: Settings) -> list[str]:
    """Whether an unreadable file here has been acknowledged, and on what grounds."""
    entry = settings.parse.acknowledgement(path)
    if entry is None:
        return ["parse: not acknowledged; an unreadable file here blocks the commit"]
    return [
        f"parse: acknowledged -- {entry.reason}",
        "  it is still reported, and it is measured only up to the construct that stopped "
        "the parse",
    ]


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
