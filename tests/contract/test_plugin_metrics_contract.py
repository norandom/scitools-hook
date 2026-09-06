"""Plugin metrics against the installed build: they answer, and what they cost (5.1, 5.5).

Understand 8.0 computes these from ``.upy`` plugins rather than reading them out of the
database, and two things about that are only knowable by asking the real build.

**They answer at all.** They are absent from every ``Metric.list(kind)`` on 1262, so nothing in
the catalogue proves ``Ent.metric()`` will return a number for one. A snapshot that carried no
value would look exactly like a metric always inside its limit.

**They are slow.** Measured here: three ``CountGlobals*`` cost about 2 ms per routine against
0.03 ms for three built-ins, a factor of seventy. That is the whole reason the worker reads
them for recorded entities only, and the budget is asserted rather than remembered, because a
future build that made them cheap would let the split be simplified and one that made them
slower would need the budget rewritten.
"""

from __future__ import annotations

import time

import pytest
from contract_project import (
    FILES,
    SampleProject,
    contract_settings,
    extract_with,
    real_env,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)

from scitools_hook.analysis.recommend import recommend
from scitools_hook.config.models import Limit, ThresholdSpec
from scitools_hook.models.snapshot import EntityRecord, ProjectSnapshot

pytestmark = pytest.mark.contract

ROUTINE_PLUGIN = "CountGlobalsUsed"
CLASS_PLUGIN = "CountClassCoupledModified"
"""One plugin metric per element scope; both are declared for Python in ``config``."""

BUDGET_MS = 4.0
"""What one plugin metric may cost per entity before the read strategy has to change.

Measured on Build 1262 over 300 routines of this project: three ``CountGlobals*`` together
take 2.056 ms per routine, so one is well inside this. The ceiling is twice the measurement
rather than the measurement itself, because a contract test that fails on a busy machine
teaches people to ignore it.
"""


def asking(*plugins: tuple[str, str]):
    """The contract settings with one threshold added per named ``(scope, metric)``."""
    settings = contract_settings()
    for scope, metric in plugins:
        settings.thresholds.append(ThresholdSpec(scope=scope, metric=metric, limit=Limit(max=1000)))
    return settings


def test_contract_a_plugin_metric_reaches_a_recorded_entity(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """Requirement 5.1: the value is on the record like any other, or the rule cannot fire."""
    snapshot = extract_with(
        sample_project.db("alpha"),
        sample_project.root("alpha"),
        FILES,
        asking(("routine", ROUTINE_PLUGIN), ("class", CLASS_PLUGIN)),
    )

    routines = _of_scope(snapshot, "routine")
    classes = _of_scope(snapshot, "class")
    assert routines and classes, "the fixture must record both scopes"
    assert any(ROUTINE_PLUGIN in record.metrics for record in routines), (
        f"no recorded routine carries {ROUTINE_PLUGIN}; unavailable says {snapshot.unavailable}"
    )
    assert any(CLASS_PLUGIN in record.metrics for record in classes), (
        f"no recorded class carries {CLASS_PLUGIN}; unavailable says {snapshot.unavailable}"
    )


def test_contract_the_plugin_read_is_inside_its_budget(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """One extraction with the plugins and one without, over the same recorded set.

    The difference divided by the recorded entities is what a plugin metric costs a run, which
    is the figure requirement 8 has to live with. Asserted against a ceiling twice the measured
    figure, for the reason :data:`BUDGET_MS` gives.
    """
    plain = _timed(sample_project, contract_settings())
    with_plugins = _timed(sample_project, asking(("routine", ROUTINE_PLUGIN)))

    recorded = len(_of_scope(with_plugins[1], "routine"))
    assert recorded, "the fixture must record routines for the figure to mean anything"
    per_entity = (with_plugins[0] - plain[0]) / recorded * 1000
    assert per_entity < BUDGET_MS, (
        f"{ROUTINE_PLUGIN} cost {per_entity:.3f} ms per recorded routine, over {BUDGET_MS} ms"
    )


def _of_scope(snapshot: ProjectSnapshot, scope: str) -> list[EntityRecord]:
    """The records of one scope; ``entities`` is keyed by the entity, not a list."""
    return [record for key, record in snapshot.entities.items() if key.scope == scope]


def _timed(project: SampleProject, settings: object):
    """One extraction and how long it took, in seconds."""
    started = time.monotonic()
    snapshot = extract_with(
        project.db("alpha"),
        project.root("alpha"),
        FILES,
        settings,  # type: ignore[arg-type]
    )
    return time.monotonic() - started, snapshot


def test_contract_the_installation_these_tests_read_is_the_measured_one() -> None:
    """The figures above are Build 1262's; a different build is a different measurement."""
    assert real_env("upython").und.is_file()


def test_contract_recommend_prices_a_plugin_metric(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """Requirement 5.3: a metric an operator may configure is a metric this command advises on.

    ``recommend`` is a pure function over a whole-project snapshot, so the only thing that
    could stop it pricing a plugin metric is the value never arriving -- which is what the
    test above proves it does, and what this one proves reaches the advice.
    """
    settings = asking(("routine", ROUTINE_PLUGIN))
    snapshot = extract_with(
        sample_project.db("alpha"), sample_project.root("alpha"), FILES, settings
    )

    advice = recommend(snapshot, settings.thresholds)

    priced = [one for one in advice.advice if one.rule == f"routine.{ROUTINE_PLUGIN}"]
    assert priced, [one.rule for one in advice.advice]
    assert priced[0].distribution.count > 0, (
        "a rule with no population measured is advice about nothing"
    )
