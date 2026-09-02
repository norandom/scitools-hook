"""The ``db`` subcommand group: where the analysis database is, and how to refresh it.

The nested application is BUILT PER REGISTRATION rather than held as a module-level
singleton: ``app.build_app()`` is called by tests and could be called again by an embedder,
and a shared instance would let one assembly mutate another's commands and epilogs.

Three decisions, each of which changes what an operator can do on a broken machine:

* **``path`` needs no Understand** (req 2.8). Where the cache lives is decided by the
  repository and the configuration alone, and the operator asking is usually the one whose
  installation is *not* working -- answering "no usable Understand installation found" to
  "where is my database?" would be both wrong and unhelpful. ``rebuild`` and ``analyze`` do
  need it, because they run it.
* **Not being in a repository is reported before Understand is looked for.** ``path``
  discovers the repository itself and ``rebuild``/``analyze`` get the same ordering from
  ``cli.pipelines.assemble``, which was written for ``check``, ``explain`` and ``baseline``
  and documents the trade: a run from the wrong directory says so (exit 6) instead of
  reporting a missing installation (exit 3) it never needed. Using that assembly rather than
  a second one is the point -- the cache derivation, the shadow synchroniser and the database
  manager are then built in exactly one place for every command that touches a database.
* **``rebuild`` discards and then analyses** (req 2.7: "discard the existing database and
  perform a full analysis"). ``DatabaseManager.rebuild`` deliberately only discards -- its
  docstring notes that this leaves the method usable without a license -- so the *command*
  composes the two halves and the requirement is met where it was asked for. What was
  removed is named on standard output rather than left to be inferred, because the operation
  is destructive and its blast radius (three fixed names under a cache root derived from the
  repository's own git common directory) is exactly what an operator wants to see.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

import typer

from scitools_hook.cli import common
from scitools_hook.cli.config_cmd import effective_configuration
from scitools_hook.cli.pipelines import assemble
from scitools_hook.config.models import Settings
from scitools_hook.git.repo import GitRepo
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.git import IndexTarget
from scitools_hook.models.understand import AnalyzeResult
from scitools_hook.paths import classify_file
from scitools_hook.runner.context import cache_dir
from scitools_hook.understand.database import DatabaseManager

HELP = "Inspect and maintain the Understand database for this repository."

PATH_HELP = "Print the path of this repository's analysis database."
REBUILD_HELP = "Discard the analysis databases and analyse the project again."
ANALYZE_HELP = "Bring the analysis database up to date with the index."

REMOVED: Final = "removed"
NOTHING_REMOVED: Final = "no analysis database was present under"
ANALYZED: Final = "analyzed the after database"


def build_db_app() -> typer.Typer:
    """A fresh ``db`` application carrying its three operations."""
    db_app = typer.Typer(name="db", help=HELP, no_args_is_help=True, rich_markup_mode=None)
    db_app.command(name="path", help=PATH_HELP)(path)
    db_app.command(name="rebuild", help=REBUILD_HELP)(rebuild)
    db_app.command(name="analyze", help=ANALYZE_HELP)(analyze)
    return db_app


def register(app: typer.Typer) -> None:
    """Add the ``db`` group to ``app``; it names and describes itself."""
    app.add_typer(build_db_app())


def path(ctx: typer.Context) -> None:
    """Print the path of this repository's analysis database (req 2.8).

    Exactly one line, and it is the *after* database -- the one that holds the project as it
    currently stands, and the one requirement 9.8's "open in the GUI" command names -- so the
    output can be substituted straight into ``understand $(scitools-hook db path)``.
    """
    options = common.global_options(ctx)
    repo = GitRepo.discover(options.cwd, options.command_log())
    settings, _ = effective_configuration(options, repo)
    common.emit_findings(str(_cache_of(repo, settings, options.env).after_db), None)


def rebuild(ctx: typer.Context) -> None:
    """Discard the analysis databases and build them again from scratch (req 2.7).

    What was removed is reported **before** the analysis starts, in its own write. The two
    halves can fail independently -- a repository holding nothing Understand can parse fails
    the second one (exit 5) -- and a destructive step whose record is lost because the step
    after it failed is the worst way to learn what happened to your cache.
    """
    manager, paths = _database(ctx)
    common.emit_findings("\n".join(_discarded(manager, paths)), None)
    common.emit_findings(_analysed(manager, paths), None)


def analyze(ctx: typer.Context) -> None:
    """Bring the analysis database up to date with the index (req 2.1, 2.3, 2.6).

    The index, not the working tree: this is the state a ``check --staged`` run and the
    pre-commit hook analyse, so warming any other target would leave the next commit paying
    for a full re-sync of the after shadow rather than saving it one.
    """
    manager, paths = _database(ctx)
    common.emit_findings(_analysed(manager, paths), None)


def _discarded(manager: DatabaseManager, paths: CachePaths) -> list[str]:
    """Remove the databases and the recorded state, naming everything that was really there."""
    doomed = [item for item in (paths.before_db, paths.after_db, paths.state) if _taken(item)]
    manager.rebuild()
    if not doomed:
        return [f"{NOTHING_REMOVED} {paths.root}"]
    return [f"{REMOVED} {item}" for item in doomed]


def _analysed(manager: DatabaseManager, paths: CachePaths) -> str:
    """Analyse the after side and say what it cost, parse errors included (req 2.6)."""
    result = manager.ensure_side("after", IndexTarget())
    lines = [f"{ANALYZED} {paths.after_db} in {result.seconds:.1f}s"]
    lines.extend(_parse_error_lines(result))
    return "\n".join(lines)


def _parse_error_lines(result: AnalyzeResult) -> list[str]:
    """One line per parse error: requirement 2.6 asks for the files *and* the errors."""
    return [
        f"parse error: {error.path}{'' if error.line is None else f':{error.line}'}: "
        f"{error.message}"
        for error in result.parse_errors
    ]


def _database(ctx: typer.Context) -> tuple[DatabaseManager, CachePaths]:
    """The database manager for this repository, and the paths it owns.

    ``cli.pipelines.assemble`` is the shared assembly the three pipeline commands use: it
    refuses a run outside a working tree before it looks for Understand, derives the cache
    from the repository it required, and builds the manager over it. The extractor it also
    builds is unused here and costs nothing -- it holds settings and an adapter and opens
    nothing -- which is a better price than a second assembly that could drift from it.
    """
    manager = assemble(common.global_options(ctx)).dbm
    return manager, manager.paths()


def _cache_of(repo: GitRepo, settings: Settings, env: Mapping[str, str]) -> CachePaths:
    """Where this repository's cache lives, for the one command that must not need Understand.

    ``cli.pipelines.assemble`` computes the same paths, but only after locating and verifying
    an installation, which is the whole thing ``db path`` exists to answer without. ``cache_dir``
    is not optional in either place, and for the reason recorded in ``RunContext.cache``:
    ``platformdirs`` expands ``~`` from the *ambient* environment, so without it the answer
    names the real user's cache directory whatever environment the caller supplied.
    """
    return CachePaths.for_repo(repo.common_dir, settings.understand.db_location, cache_dir(env))


def _taken(item: Path) -> bool:
    """Whether anything at all occupies this path -- a dangling symlink included.

    ``classify_file`` rather than ``Path.exists()``: a database is a *directory*, and this
    question is only about the name being occupied, which ``lstat`` answers for both kinds
    without swallowing the ``OSError`` that distinguishes "not there" from "cannot be read".
    """
    return not classify_file(item).absent
