"""A git-derived architecture, generated where Understand can actually derive one (4.1, 4.3).

Understand ships 21 architectures it can generate, and three of them -- ``Git Stability``,
``Git Owner``, ``Git Author`` -- read a repository's history. Those three are the interesting
ones for a maintainability gate, because they group files by how the code has actually
behaved rather than by where someone filed it.

**They cannot be generated on the database the Gate analyses.** Measured on Build 1262: the
Gate's after database, built over an exported shadow tree with ``GitRepositoryDirectory`` set
to the repository, exports ``Git Stability`` with **zero** members while exporting
``Directory Structure`` with 260 from the same database, and ``arch -generate`` prints
``Git Stability: generated`` either way. The plugin runs ``git log`` and matches its output to
the database's file paths; a shadow tree's paths are not paths git has heard of. The same
repo-rooted database built with ``create -gitrepo <repo> -gitcommit <sha>`` exports 99
members, with or without the setting -- so ``-gitrepo`` at create time is what the plugin
reads.

So this module builds a **third** database, rooted at the repository and pinned to the after
side's commit, generates there, and answers the node with its members made repository-relative
so the existing declared-architecture step can place them in either shadow.

**That needs a commit, and two of the three selections do not have one.** A ``--staged`` or
``--worktree`` check judges code that is in no commit, so there is nothing to pin a database
to and nothing for ``git log`` to say about it. Those runs are told so
(:data:`NO_COMMIT`) rather than handed an empty architecture, which every node-level rule
would read as "nothing crosses a layer".
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from scitools_hook.config.models import Settings
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.progress import Progress
from scitools_hook.models.understand import Feature
from scitools_hook.understand.cache_files import discard
from scitools_hook.understand.features import load_features
from scitools_hook.understand.und_arch import (
    DIRECTORY_STRUCTURE,
    ArchNode,
    read_architecture,
    write_architecture,
)
from scitools_hook.understand.und_cli import (
    ALL,
    GitSource,
    UndCli,
    create_from_commit,
    generate_arch,
    set_git_repository,
    und_exclusions,
)

NO_COMMIT: Final = (
    "a git-derived architecture is generated from a commit, and this run judges code that is "
    "in none -- run `scitools-hook check --range A..B`, or name an architecture the "
    "repository declares"
)
"""Why ``--staged`` and ``--worktree`` cannot have one (requirement 4.3).

Said out loud rather than answered with an empty architecture. An architecture with no members
puts every file in no node, and every node-level rule reads that as "nothing crosses a layer"
-- a green run that measured nothing, which is the shape this tool exists to refuse.
"""

GIT_ARCHITECTURES: Final = frozenset({"Git Author", "Git Date", "Git Owner", "Git Stability"})
"""The generated architectures that read a repository's history (``und arch -list`` on 1262).

Named because they are the ones that need a commit-built database. The other seventeen derive
from the code alone and would generate on any database; nothing here treats them specially
yet, because the route that works for the history ones works for all of them.
"""


@dataclass(frozen=True, slots=True)
class Generation:
    """One request to generate an architecture on a database of the after side's commit."""

    db: Path
    """Where the generation database goes; it is discarded and rebuilt whenever it is used."""

    repo: Path
    commit: str
    languages: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    options: Mapping[str, str] | None = None


def generate_for_commit(cli: UndCli, request: Generation, name: str) -> ArchNode:
    """Build a repository-rooted database of ``request.commit`` and generate ``name`` in it.

    Four commands and then the generation, in this order and for these reasons:

    #. ``create`` with ``-gitrepo`` and ``-gitcommit``. This is the one that matters: the git
       plugins read the repository named here, and a database created without it generates an
       empty architecture whatever is set afterwards.
    #. ``add <repo>`` under the configured excludes, translated into the form ``und -exclude``
       honours. Without a reference database there is no file set to copy.
    #. ``settings -GitRepositoryDirectory``. Not needed by the plugin -- measured: the export
       is identical without it -- and recorded anyway, because it is what ``und``'s own
       documentation names and what a person reading the database would look for.
    #. ``analyze -all``. An architecture generated over a database that was added but not
       analysed holds empty nodes, and the analysis that follows does not fill them in.

    The members come back as absolute repository paths and are answered **repository-relative**,
    which is the form the declared-architecture step already places into a shadow tree. A
    member outside the repository is dropped rather than refused: the generation database holds
    what ``und add`` found, and an interpreter's own standard library is not this repository's
    architecture.
    """
    discard(request.db)
    create_from_commit(
        cli,
        request.db,
        list(request.languages),
        GitSource(repo=request.repo, commit=request.commit),
    )
    cli.add(request.db, request.repo, und_exclusions(request.exclude))
    set_git_repository(cli, request.db, request.repo)
    cli.analyze(request.db, ALL)
    generated = generate_arch(cli, request.db, name, options=request.options)
    return _repository_relative(generated, request.repo)


def _repository_relative(node: ArchNode, repo: Path) -> ArchNode:
    """The same tree with every member named relative to the repository, others dropped."""
    root = repo.resolve()
    rebased = node.rebase(lambda member: _under(member, root))
    if not any(True for _ in rebased.paths()):
        raise AnalysisFailedError(
            f"the generated architecture {node.name!r} holds no file of {repo}",
            hint=(
                "Every member Understand generated is outside the repository, which means the "
                "generation database was built over something else. This is a defect in the "
                "Gate, not in the configuration."
            ),
        )
    return rebased


def _under(member: str, root: Path) -> str | None:
    """``member`` relative to ``root``, or ``None`` when it is not under it at all."""
    try:
        return PurePosixPath(Path(member).resolve().relative_to(root)).as_posix()
    except ValueError:
        return None


EXPORTS: Final = "generated"
"""The cache directory the generated architecture is kept in between runs.

Kept because a skipped run still has to *hand over* the architecture, not merely decline to
rebuild it: the rules, ``explain`` and the review aids all read the node, and regenerating it
to answer a question already answered is what the skip exists to avoid.
"""


@dataclass(frozen=True, slots=True)
class Site:
    """One run's surroundings, as the architecture step needs them.

    A value rather than five parameters for the reason the notes of tasks 4.2 and 5.2 record:
    ``DatabaseManager`` is eight methods and fifteen coupled classes past its own limits, so
    the work lives beside it and takes what it needs in one argument.
    """

    cli: UndCli
    paths: CachePaths
    repo: Path
    settings: Settings
    progress: Progress


def site_for(
    cli: UndCli, paths: CachePaths, repo: Path, settings: Settings, progress: Progress
) -> Site:
    """Gather a run's surroundings, so the caller need not name :class:`Site`."""
    return Site(cli=cli, paths=paths, repo=repo, settings=settings, progress=progress)


def architecture_for(
    site: Site, declared: ArchNode | None, state: SyncState, offered: Sequence[str]
) -> ArchNode | None:
    """The architecture this run's rules read: generated, declared, or none (req 4.1, 4.5).

    The order is the one an operator would expect. A repository that *declares* the configured
    architecture supplies it, whatever the build can generate -- a declaration is a decision
    and generation is a derivation. ``Directory Structure`` is built into every database and
    needs neither. Anything else is a name the configuration check already established the
    build can generate (requirement 4.2), so it is generated here.

    A generation that fails is **reported and then stepped over** when the repository declares
    an architecture of its own, and raised when it does not: a run with no architecture at all
    evaluates every node-level rule against an empty node set and reports nothing, which is
    worse than stopping.
    """
    name = site.settings.structure.architecture
    if declared is not None and declared.name == name:
        return declared
    if name == DIRECTORY_STRUCTURE or name not in offered:
        return declared
    try:
        return _generated(site, name, state)
    except AnalysisFailedError as refused:
        if declared is None:
            raise
        site.progress.note(
            f"the {name!r} architecture could not be generated, so this run used the declared "
            f"{declared.name!r} instead: {refused}"
        )
        return declared


def generated_names(paths: CachePaths, build: str) -> list[str]:
    """The architectures the stored measurement says this build can generate (req 1.4).

    Read from what ``doctor`` recorded rather than probed, for the reason every other
    availability question is: a check measures nothing about the installation, and a record
    from another build is not an answer about this one.
    """
    report = load_features(paths)
    if report is None or report.build != build:
        return []
    found = report.features.get(Feature.GENERATED_ARCHS)
    return list(found.generated) if found is not None and found.state == "available" else []


def _generated(site: Site, name: str, state: SyncState) -> ArchNode:
    """The generated architecture, from this run or from the last one that produced it (4.4).

    The skip is keyed on the commit the after side is at, which for a commit target is both
    "the repository head" and "the after tree id" -- they are the same string. Nothing else
    can change what ``git log`` says about that commit, so a run whose key matches reads the
    kept export instead of building a database and generating in it.
    """
    kept = site.paths.root / EXPORTS / f"{name}.xml"
    stamp = _stamp(state)
    if state.generated_archs.get(name) == stamp and kept.is_file():
        return read_architecture(kept.read_text(encoding="utf-8"), str(kept))
    commit = _commit_of(state, name)
    site.progress.start(f"generating the {name!r} architecture")
    started = time.monotonic()
    node = generate_for_commit(site.cli, _request(site, commit, state), name)
    site.progress.finish(f"generating the {name!r} architecture", time.monotonic() - started)
    _keep(kept, node)
    state.generated_archs[name] = stamp
    return node


def _stamp(state: SyncState) -> str:
    """What a generated architecture is a function of: the commit the after side is at."""
    return f"{state.after_target or 'none'}:{state.after_tree_id or 'none'}"


def _commit_of(state: SyncState, name: str) -> str:
    """The commit to generate from, or a refusal saying why this run has none (req 4.3)."""
    if state.after_target != "commit" or not state.after_tree_id:
        raise AnalysisFailedError(f"{name} cannot be generated for this run", hint=NO_COMMIT)
    return state.after_tree_id


def _request(site: Site, commit: str, state: SyncState) -> Generation:
    """The generation database goes in the cache, beside the two the run compares.

    The languages come from configuration when it names any and from the record otherwise --
    the same rule both database routes follow, and it is safe here because the after side has
    already been analysed and written the set it detected.
    """
    return Generation(
        db=site.paths.root / "generate.und",
        repo=site.repo,
        commit=commit,
        languages=tuple(site.settings.project.languages or state.languages),
        exclude=tuple(site.settings.project.exclude),
        options=site.settings.structure.architecture_options or None,
    )


def _keep(target: Path, node: ArchNode) -> None:
    """Write the export where the next run can read it instead of generating again."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(write_architecture(node), encoding="utf-8")
    except OSError:
        # Losing it costs the next run one generation and nothing else, so it is not worth
        # failing a run that has already produced the architecture it was asked for.
        return
