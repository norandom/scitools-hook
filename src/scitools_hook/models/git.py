"""Git records: staged changes, what a shadow tree is synced to, and what a sync moved.

The gate reads git through plumbing only (req 4.1, 4.3): the ``after`` side is materialized
from the index, the working tree (``--worktree``, req 10.5) or a commit (``explain --range``),
and the ``before`` side from ``HEAD``.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import Field

from scitools_hook.errors import ConfigError
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


RANGE_SEPARATOR: Final = ".."
"""How requirement 9.1's ``--range A..B`` separates the two ends."""

MERGE_BASE_SEPARATOR: Final = "..."
"""``A...B``: the same question asked from the merge base, which is what reviewing a branch means.

Refused by name until a session driving this tool reported it. The refusal's reasoning called
``A...B`` git's *symmetric difference*, which is true of ``git log`` and **not** of ``git
diff``: ``git diff A...B`` is ``merge-base(A, B)..B``, and that is exactly what a code review
compares -- what this branch did, without the commits main gathered meanwhile. It is also
what a pull request shows, and what this project's own documentation and agent skill told
people to type, so the one form a reviewer reaches for first was the one form that failed.
"""

RANGE_KEY: Final = "range"
"""The option a refusal about a range names, so the operator knows which one to fix."""

RANGE_HINT: Final = (
    "Write the range as BASE..HEAD, or BASE...HEAD to compare from the merge base, "
    "where each end names one commit (a hash, a tag, a branch or a revision like HEAD~1)."
)
"""What an operator does about a range this module cannot read."""


class CommitRange(DataModel):
    """The two commits requirement 9.1's ``--range BASE..HEAD`` names.

    The ends are held as the operator typed them; :meth:`ExplainPipeline.run` resolves each to
    an object id. Keeping the typed form is what lets a refusal quote it back.
    """

    base: str
    head: str
    from_merge_base: bool = False

    @classmethod
    def parse(cls, text: str) -> CommitRange:
        """Read ``BASE..HEAD`` or ``BASE...HEAD``, or refuse with the forms expected (req 9.1).

        The three-dot form is tried first, because ``"A...B".partition("..")`` yields a head
        that begins with a dot and would otherwise be refused as malformed -- which is exactly
        how this form used to fail.
        """
        for separator, merge_base in ((MERGE_BASE_SEPARATOR, True), (RANGE_SEPARATOR, False)):
            base, found, head = text.partition(separator)
            if not found:
                continue
            if not base or not head or "." in head[:1] or RANGE_SEPARATOR in head:
                break
            return cls(base=base, head=head, from_merge_base=merge_base)
        raise ConfigError(f"{text!r} is not a commit range", key=RANGE_KEY, hint=RANGE_HINT)
