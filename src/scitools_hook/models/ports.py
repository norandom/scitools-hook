"""Behavioural ports that let one adapter be *used* by code the other adapter owns.

``understand/`` and ``git/`` are siblings in the layer direction: neither may import the
other, and the architecture diagram has no edge between them. But
:class:`~scitools_hook.understand.database.DatabaseManager` cannot do its job without a
shadow synchroniser -- the tree it analyses is materialised by ``git/shadow.py`` -- so the
dependency is real and only its *direction* is negotiable. :class:`ShadowPort` is that
direction made explicit: ``models`` is below both adapters, so the manager depends on the
port and the git adapter satisfies it, and the sideways import disappears without anything
being pretended away.

**The port is exactly as wide as its one caller.** ``DatabaseManager`` reaches for two
members of the synchroniser and no others -- ``sync(side, target, state)``, which moves one
shadow and says what moved, and ``repo.root``, which names the repository in the refusal
raised when nothing under it is a language Understand can analyse. A port that mirrored the
whole class would be a second copy of ``ShadowSync``'s surface to keep in step, and would
re-create the coupling it exists to remove, so anything the manager does not call is left
out. ``tests/models/test_ports.py`` holds that width to the two members.

**Both members are read-only properties, and that is load-bearing rather than stylistic.**
A protocol member written as a plain annotation (``repo: RepositoryRoot``) is a mutable
attribute, and a mutable attribute is *invariant*: mypy then refuses ``ShadowSync``, whose
``repo`` is a ``GitRepo``, with ``repo: expected "RepositoryRoot", got "GitRepo"`` --
measured under ``mypy --strict`` on both spellings before this file was written. A read-only
property is covariant, so the concrete ``GitRepo`` satisfies ``RepositoryRoot`` and the
concrete ``ShadowSync`` satisfies ``ShadowPort``. Nothing assigns through either member, so
read-only is also the truth about how they are used.

There is no implementation here and there is deliberately no null object either, unlike
:mod:`scitools_hook.models.progress`: a manager with no way to materialise a shadow has
nothing to analyse, so "absent" is not a state this port has a sensible answer for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from scitools_hook.models.cache import SyncState
from scitools_hook.models.git import SyncDelta, SyncTarget
from scitools_hook.models.snapshot import Side


@runtime_checkable
class RepositoryRoot(Protocol):
    """The one thing a shadow synchroniser is asked about the repository it copies from.

    ``isinstance`` against this protocol tests that the attribute is *there*, not that it is
    a ``Path``; the signature is what ``mypy --strict`` checks at the composition root, where
    the real ``GitRepo`` is handed over.
    """

    @property
    def root(self) -> Path:
        """The working tree's top directory."""


@runtime_checkable
class ShadowPort(Protocol):
    """Materialise one side's shadow tree and say what moved (requirements 2.1, 2.3).

    Satisfied by :class:`~scitools_hook.git.shadow.ShadowSync`; nothing in ``models`` or
    ``understand`` names that class.
    """

    @property
    def repo(self) -> RepositoryRoot:
        """The repository the shadows are copies of."""

    def sync(self, side: Side, target: SyncTarget, state: SyncState) -> SyncDelta:
        """Bring ``side``'s shadow up to date with ``target``, updating ``state`` in place.

        The returned delta names every path that arrived, changed or left, which is what the
        caller turns into the smallest set of ``und`` commands that leaves its database equal
        to the shadow. ``state`` is mutated rather than returned: the caller owns writing it
        back, and writes it only once the analysis it describes has actually succeeded.
        """
