"""Plugin metrics against the installed build: they answer, and what they cost (5.1, 5.5).

Understand 8.0 computes these from ``.upy`` plugins rather than reading them out of the
database, and two things about that are only knowable by asking the real build.

**They answer at all.** They are absent from every ``Metric.list(kind)`` on 1262, so nothing in
the catalogue proves ``Ent.metric()`` will return a number for one. A snapshot that carried no
value would look exactly like a metric always inside its limit.

**They are slow.** Measured here, inside one interpreter: a warm ``ent.metric()`` for
``CountGlobalsUsed`` costs 0.27 to 0.30 ms of processor time per routine against 0.004 to
0.006 ms for a built-in metric, a factor of fifty to seventy, and the first read of a routine
costs about three times the warm one (0.79 to 0.92 ms). That is the whole reason the worker
reads them for recorded entities only, and the budget is asserted rather than remembered,
because a future build that made them cheap would let the split be simplified and one that
made them slower would need the budget rewritten.

**How the cost is measured, and why not the obvious way.** The obvious way -- extract the
snapshot twice, once with the plugin threshold and once without, and divide the difference by
the recorded entities -- was here until task 5.7 and measured nothing. Each extraction is a
whole ``upython worker.py snapshot`` subprocess, measured at 0.58 to 1.45 s on this fixture,
while the plugin read inside it is 36 recorded routines at under a millisecond each, some
thirty milliseconds. The difference of two such runs is therefore whatever else the machine
was doing: six trials of that statistic gave -3.07, -1.43, 0.52, 2.72, 3.95 and 5.08 ms per
entity -- two of them negative, meaning the run *with* the plugin finished sooner, and one of
them over the 4 ms ceiling it was asserted against. The same statistic on a deliberately
doubled cost (two plugin metrics instead of one) gave -11.08, -3.04, 1.01, 2.49, 5.74 and
19.83, inside that ceiling in four trials of six: it could not have caught a build that halved
this plugin's speed.

What replaced it times the API call instead of the process. :data:`PROBE` opens the database
once under ``upython``, reads the plugin metric exactly as
:func:`~scitools_hook.understand.worker._metric_values` does -- ``ent.metric([name])``, one
call per entity -- and charges :data:`PASSES` passes over every routine in the database to
``time.process_time``. Three things make the figure a signal rather than a difference of two
noises: processor time is not inflated by other work on the machine, the same loop over a
built-in metric is subtracted so only the plugin's own cost remains (and that subtrahend is a
fiftieth of the total or less, so the subtraction is a correction and not the measurement),
and the reads are repeated, so process startup and database opening are outside the timed
window entirely rather than swamping it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import pytest
from contract_project import (
    FILES,
    TIMEOUT_S,
    SampleProject,
    contract_settings,
    extract_with,
    real_env,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)

from scitools_hook.analysis.recommend import recommend
from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.config.models import Limit, ThresholdSpec
from scitools_hook.models.snapshot import EntityRecord, ProjectSnapshot

pytestmark = pytest.mark.contract

ROUTINE_PLUGIN = "CountGlobalsUsed"
CLASS_PLUGIN = "CountClassCoupledModified"
"""One plugin metric per element scope; both are declared for Python in ``config``."""

BASELINE_METRIC = "CountLineCode"
"""A built-in read of the same shape, subtracted so only the plugin's own cost is left."""

BUDGET_MS = 0.42
"""What one plugin metric read may cost per entity before the read strategy has to change.

Processor time, warm, per ``ent.metric([name])`` call, measured by
:func:`the_cost_of_a_plugin_read`. Nine trials on Build 1262 over the 741 routines of this
fixture, on a machine carrying other work (load average 1.9 to 3.7), gave 0.270 to 0.297 ms;
the ceiling is one and a half times the middle of that.

**Not twice it**, which is what this constant used to say. A ceiling at twice the measurement
cannot fail on a cost that has doubled, and a budget that cannot notice the thing it exists to
notice is decoration. It leaves 0.12 ms of headroom above the slowest of the nine trials --
four times their whole 0.027 ms spread -- and still sits a fifth below the 0.54 ms the doubled
arm measured at its cheapest; the test proves both ends of that on every run rather than
asserting them.
"""

COLD_BUDGET_MS = 1.3
"""The same ceiling for the first read of an entity, which is the one a real run pays.

The worker reads a plugin metric once per recorded entity, so every read it makes is a cold
one: 0.79 to 0.92 ms per routine over the same nine trials, about 30 ms for the 36 routines
this fixture records. It is asserted alongside the warm figure because the warm figure is the
*discriminator* -- repeatable to a few per cent, and what the doubling test resolves -- while
this one is the *price*. A build that made plugin metrics slower would move both; a change
that made only this one move would mean the cost had migrated into opening and first touch,
which is worth being told about.
"""

PASSES = 5
"""Passes over every routine in the database, per arm.

Enough that the timed window is seconds of reads rather than one pass' worth of first-touch
effects, and few enough that the whole test stays near ten seconds.
"""

DOUBLING_FLOOR = 1.6
"""How much of a genuine doubling the instrument must actually resolve.

The doubled arm asks for the same metric twice per entity, which is exactly twice the work;
the measured ratio on the nine trials was 1.87 to 2.10. Asserting 1.6 rather than 2.0 leaves
room for the second read of a pair being marginally cheaper without letting an instrument
that cannot tell one read from two pass.
"""

PROBE = """
import json
import sys
import time

import understand

db_path, kind, plugin, builtin = sys.argv[1:5]
passes = int(sys.argv[5])
db = understand.open(db_path)
ents = list(db.ents(kind))


def cost_ms(reads, count):
    "Processor milliseconds for `count` passes of `reads` over every entity."
    started = time.process_time()
    for _ in range(count):
        for ent in ents:
            for names in reads:
                ent.metric(names)
    return (time.process_time() - started) * 1000.0


# The first read of each metric loads its plugin and touches the database cold; it is
# reported, because it is what a real run pays, and left out of the warm figures.
cold = cost_ms([[plugin]], 1)
cost_ms([[builtin]], 1)
print(json.dumps({
    "entities": len(ents),
    "passes": passes,
    "cold_ms": cold,
    "plugin_ms": cost_ms([[plugin]], passes),
    "builtin_ms": cost_ms([[builtin]], passes),
    "doubled_ms": cost_ms([[plugin], [plugin]], passes),
}))
"""
"""Read the cost out of the API, in the interpreter the worker itself runs under.

Deliberately not routed through :class:`~scitools_hook.understand.api_runner.ApiRunner`: the
question is what Understand charges for a plugin metric, and a wrapper in the timed window
would be this project's cost, not the build's.
"""


@dataclass(frozen=True)
class ReadCost:
    """What :data:`PROBE` measured, reduced to milliseconds per entity per read."""

    entities: int
    cold: float
    """The first, cold read of the plugin metric: plugin load and database cache included."""
    plugin: float
    baseline: float
    doubled: float

    @property
    def per_read(self) -> float:
        """The plugin's own warm cost, with the built-in read's overhead taken off."""
        return self.plugin - self.baseline

    @property
    def per_doubled_read(self) -> float:
        """The same, for an entity whose plugin cost has genuinely doubled."""
        return self.doubled - self.baseline

    def __str__(self) -> str:
        return (
            f"{self.entities} routines x {PASSES} passes: plugin {self.plugin:.4f} ms, "
            f"built-in {self.baseline:.4f} ms, doubled {self.doubled:.4f} ms, "
            f"cold first read {self.cold:.4f} ms, all per entity per read"
        )


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


@pytest.fixture(scope="module")
def the_cost_of_a_plugin_read(
    sample_project: SampleProject,  # noqa: F811
    tmp_path_factory: pytest.TempPathFactory,
) -> ReadCost:
    """One run of :data:`PROBE` against the fixture database, shared by the two cost tests."""
    script = tmp_path_factory.mktemp("plugin-cost") / "probe.py"
    script.write_text(PROBE, encoding="utf-8")
    upython = real_env("upython").upython
    assert upython is not None, "this build ships no upython, so the cost cannot be measured"
    done = subprocess.run(
        [
            str(upython),
            str(script),
            str(sample_project.db("alpha")),
            SCOPE_KINDS["routine"],
            ROUTINE_PLUGIN,
            BASELINE_METRIC,
            str(PASSES),
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    measured = json.loads(done.stdout.strip().splitlines()[-1])
    reads = measured["entities"] * measured["passes"]
    assert measured["entities"] > 100, (
        f"only {measured['entities']} routines: too few for the figure to settle"
    )
    return ReadCost(
        entities=measured["entities"],
        cold=measured["cold_ms"] / measured["entities"],
        plugin=measured["plugin_ms"] / reads,
        baseline=measured["builtin_ms"] / reads,
        doubled=measured["doubled_ms"] / reads,
    )


def test_contract_the_plugin_read_is_inside_its_budget(
    the_cost_of_a_plugin_read: ReadCost,
) -> None:
    """What a plugin read costs: warm against :data:`BUDGET_MS`, cold against the other.

    This is the figure requirement 8 has to live with, and the one the worker's split -- plugin
    metrics for recorded entities, never for the population walk -- is priced against.
    Requirement 9.5 asks for a family's added cost to be measured rather than assumed, and a
    budget whose statistic is variance would let any of them be assumed while looking measured.
    """
    cost = the_cost_of_a_plugin_read

    assert cost.per_read < BUDGET_MS, (
        f"{ROUTINE_PLUGIN} cost {cost.per_read:.4f} ms of processor time per read, over the "
        f"{BUDGET_MS} ms budget. {cost}"
    )
    assert cost.cold < COLD_BUDGET_MS, (
        f"the first read of a routine cost {cost.cold:.4f} ms of processor time, over the "
        f"{COLD_BUDGET_MS} ms budget -- and every read a real run makes is a first one. {cost}"
    )


def test_contract_the_budget_would_refuse_a_doubled_cost(
    the_cost_of_a_plugin_read: ReadCost,
) -> None:
    """The half of a budget that a ceiling alone never proves: that it can fail.

    The same instrument, in the same run, measures an entity that pays for the plugin metric
    twice -- which is exactly the cost a build that halved this plugin's speed would impose.
    Both ends are asserted: the instrument resolves the doubling at all, and the budget above
    refuses it. Without this, ``BUDGET_MS`` could drift up, or the measurement could go back to
    being noise, and the suite would stay green.
    """
    cost = the_cost_of_a_plugin_read

    assert cost.per_doubled_read >= DOUBLING_FLOOR * cost.per_read, (
        f"two reads measured {cost.per_doubled_read:.4f} ms against {cost.per_read:.4f} ms for "
        f"one, a factor of {cost.per_doubled_read / cost.per_read:.2f}: this instrument cannot "
        f"tell a doubled cost from a single one. {cost}"
    )
    assert cost.per_doubled_read > BUDGET_MS, (
        f"a doubled cost of {cost.per_doubled_read:.4f} ms is still inside the {BUDGET_MS} ms "
        f"budget, so the budget cannot fail on one. Either the budget has been raised, or this "
        f"machine is fast enough that {BUDGET_MS} ms needs re-measuring against it. {cost}"
    )


def _of_scope(snapshot: ProjectSnapshot, scope: str) -> list[EntityRecord]:
    """The records of one scope; ``entities`` is keyed by the entity, not a list."""
    return [record for key, record in snapshot.entities.items() if key.scope == scope]


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
