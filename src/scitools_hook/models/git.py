"""Git records: staged changes, what a shadow tree is synced to, and what a sync moved.

The gate reads git through plumbing only (req 4.1, 4.3): the ``after`` side is materialized
from the index, the working tree (``--worktree``, req 10.5) or a commit (``explain --range``),
and the ``before`` side from ``HEAD``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from scitools_hook.models.snapshot import DataModel

SyncTargetKind = Literal["index", "worktree", "commit"]
"""Discriminator of :data:`SyncTarget`, also stored in ``SyncState.after_target``."""


class StagedChange(DataModel):
    """One entry of ``git diff --name-status -M``; ``old_path`` is set for renames."""

    status: Literal["A", "M", "D", "R"]
    path: str
    old_path: str | None = None


class IndexTarget(DataModel):
    """Sync the shadow from the index, ignoring unstaged edits (req 4.1)."""

    kind: Literal["index"] = "index"


class WorktreeTarget(DataModel):
    """Sync the shadow from the working tree, so an agent can check before staging (10.5)."""

    kind: Literal["worktree"] = "worktree"


class CommitTarget(DataModel):
    """Sync the shadow from one commit, for ``HEAD`` and for commit ranges (req 4.3)."""

    kind: Literal["commit"] = "commit"
    commit: str


SyncTarget = Annotated[IndexTarget | WorktreeTarget | CommitTarget, Field(discriminator="kind")]
"""What a shadow tree is synced from."""


class SyncDelta(DataModel):
    """What one shadow sync changed; ``full`` marks a from-scratch materialization."""

    added: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    full: bool = False
