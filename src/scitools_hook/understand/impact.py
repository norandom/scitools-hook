"""The blast radius of a change: what references an entity, transitively (req 9.5).

``ImpactExpander`` asks the worker's ``impact`` operation about entities the caller already
knows by :class:`~scitools_hook.models.snapshot.EntityKey` and reads its answer back into
typed :class:`~scitools_hook.models.change.ImpactSet` values. Three details are the worker's,
not this module's invention, and each is measured:

* **The answer is keyed by ``EntityKey.token``.** JSON object keys must be strings, so the
  identity of every entity travels as its reversible token and is decoded here.
* **Resolving a key needs kind strings.** The API offers no lookup by this identity, so the
  worker walks the element scopes and indexes them — with the very ``SCOPE_KINDS`` the
  snapshot travelled with, because the worker may never invent one.
* **A key that resolves to nothing is ordinary.** A routine the change deleted is asked about
  against the after database, and one it added against the before database; each costs one
  warning and an empty set, never the change summary.

``depth`` is an inclusive count of reference hops and 0 is a legal answer ("report nothing").
That is deliberately *not* ``ExtractRequest.depth``, which is a count of architecture levels
and floors at 1.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.change import ImpactSet
from scitools_hook.models.snapshot import EntityKey
from scitools_hook.understand.api_runner import ApiRunner


class ImpactExpander:
    """Expands entity keys into the sets of entities that reference them.

    ``warnings`` collects every key the database had no record of, in the order the worker
    reported them and across every call, because the answer type has nowhere to put them and
    the reviewer's report needs them.
    """

    def __init__(self, runner: ApiRunner):
        self.runner = runner
        self.warnings: list[str] = []

    def expand(
        self, db: Path, root: Path, keys: Sequence[EntityKey], depth: int
    ) -> dict[EntityKey, ImpactSet]:
        """The impact set of every key, at most ``depth`` reference hops out (req 9.5)."""
        if not keys:
            return {}
        answer = self.runner.run("impact", self._request(db, root, keys, depth))
        self.warnings.extend(_warnings(answer))
        return _impact_sets(answer)

    def _request(
        self, db: Path, root: Path, keys: Sequence[EntityKey], depth: int
    ) -> dict[str, object]:
        """The self-describing ``impact`` request."""
        return {
            "db": str(db),
            "root": str(root),
            "kinds_by_scope": dict(SCOPE_KINDS),
            "keys": [key.model_dump(mode="json") for key in keys],
            "depth": depth,
        }


def _warnings(answer: dict[str, object]) -> list[str]:
    """The warnings an answer carries; an operation that warns about nothing carries none."""
    reported = answer.get("warnings")
    if not isinstance(reported, list):
        return []
    return [str(warning) for warning in reported]


def _impact_sets(answer: dict[str, object]) -> dict[EntityKey, ImpactSet]:
    """Read the ``{token: ImpactSet}`` object back into keys and validated sets."""
    sets = answer.get("impact")
    if not isinstance(sets, dict):
        raise _unusable("the answer carries no 'impact' object", str(sets)[:200])
    found: dict[EntityKey, ImpactSet] = {}
    for token, document in sets.items():
        found[_key_of(token)] = _impact_of(token, document)
    return found


def _key_of(token: object) -> EntityKey:
    """The entity a token names; anything else is a broken contract, not an empty answer."""
    try:
        return EntityKey.from_token(str(token))
    except ValueError as invalid:
        raise _unusable(f"{token!r} is not an entity key token", str(invalid)) from invalid


def _impact_of(token: object, document: object) -> ImpactSet:
    """One impact set, validated; ``by_depth``'s string levels become the integers they are."""
    try:
        return ImpactSet.model_validate(document)
    except ValidationError as invalid:
        raise _unusable(f"the impact set of {token!r} is not one", str(invalid)) from invalid


def _unusable(reason: str, detail: str) -> AnalysisFailedError:
    """The error an answer the models cannot read becomes."""
    return AnalysisFailedError(
        f"the impact operation answered with something this version cannot read: {reason}",
        stderr=detail,
        hint="The worker and the Gate are out of step; reinstall the Gate.",
    )
