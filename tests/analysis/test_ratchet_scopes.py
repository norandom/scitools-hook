"""A path scope reaches the ratchet, not only the absolute check.

Without this, a scope was half-applied: the region got its own maximum, and an entity
comfortably inside that maximum still blocked the moment it grew -- because
``analysis.classify``'s below-limit ceiling reads the limit off the finding and the finding
carried the global one. Measured on this repository while adding ``check --range``: a command
body at 13 parameters, inside a scoped maximum of 13, reported ``rose from 12 to 13`` against
a limit of 5 and refused the commit.

The other half of the same defect is a rule a scope switched **off**: the limit stopped
applying and the growth check went on firing.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.analysis.ratchet import evaluate_ratchet
from scitools_hook.config.metric_names import parse_metric_name
from scitools_hook.config.models import Limit, PathScope, ThresholdSpec
from scitools_hook.models.findings import EffectiveThreshold
from scitools_hook.models.snapshot import (
    EntityKey,
    EntityRecord,
    EntityRef,
    ProjectSnapshot,
)

METRIC: Final = "CountParams"
RULE: Final = f"routine.{METRIC}"
PATH: Final = "src/cli/check.py"


def spec(maximum: float) -> EffectiveThreshold:
    """One routine-scope threshold with ``maximum`` as its ceiling."""
    parsed = ThresholdSpec(scope="routine", metric=METRIC, limit=Limit(max=maximum))
    return EffectiveThreshold(spec=parsed, metric=parse_metric_name(METRIC), limit=parsed.limit)


def key() -> EntityKey:
    return EntityKey(scope="routine", path=PATH, longname="cli.check.check", parameters="")


def side(value: float) -> ProjectSnapshot:
    """A snapshot holding the one routine at ``value``."""
    record = EntityRecord(
        ref=EntityRef(key=key(), kind="Function", name="check", line=1),
        language="Python",
        metrics={METRIC: value},
    )
    return ProjectSnapshot(side="after", entities={record.key: record})


def scope(**overrides: object) -> dict[str, PathScope]:
    return {
        "cli": PathScope.model_validate(
            {"paths": [f"{PATH.rsplit('/', 1)[0]}/*.py"], "thresholds": {"routine": overrides}}
        )
    }


def test_growth_inside_a_scoped_maximum_carries_the_scoped_limit() -> None:
    """The finding must say 13, because that is what decides whether it blocks."""
    (finding,) = evaluate_ratchet(
        side(13.0), side(12.0), [key()], [spec(5.0)], scope(CountParams=13)
    )

    assert finding.rule == RULE
    assert finding.limit == 13.0, "the global 5 would make an entity inside its own limit block"
    assert finding.before == 12.0
    assert finding.value == 13.0


def test_without_scopes_the_global_limit_is_carried() -> None:
    """The shipped case: no scopes configured, nothing overlaid."""
    (finding,) = evaluate_ratchet(side(13.0), side(12.0), [key()], [spec(5.0)])

    assert finding.limit == 5.0


def test_a_rule_a_scope_switched_off_is_not_ratcheted_either() -> None:
    """The limit stopping and the growth check continuing is the same defect, reversed."""
    findings = evaluate_ratchet(
        side(13.0), side(12.0), [key()], [spec(5.0)], scope(CountParams=False)
    )

    assert findings == []


def test_a_path_no_scope_matches_keeps_the_global_limit() -> None:
    """One region's numbers must not leak into another's."""
    elsewhere = EntityKey(scope="routine", path="src/runner/deep.py", longname="deep.walk")
    record = EntityRecord(
        ref=EntityRef(key=elsewhere, kind="Function", name="walk", line=1),
        language="Python",
        metrics={METRIC: 13.0},
    )
    after = ProjectSnapshot(side="after", entities={elsewhere: record})
    before = ProjectSnapshot(
        side="before",
        entities={elsewhere: record.model_copy(update={"metrics": {METRIC: 12.0}})},
    )

    (finding,) = evaluate_ratchet(after, before, [elsewhere], [spec(5.0)], scope(CountParams=13))

    assert finding.limit == 5.0
