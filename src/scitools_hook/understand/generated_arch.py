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

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.understand.cache_files import discard
from scitools_hook.understand.und_arch import ArchNode
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
