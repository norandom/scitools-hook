"""Do the two execution modes answer the same thing? (task 10.1).

``worker.dispatch`` is one implementation, called directly when the host interpreter can
import the Understand API and run as ``upython worker.py <op>`` when it cannot. The whole
design rests on the two paths being interchangeable: ``doctor`` picks a mode per machine, so
a difference between them would be a difference between two developers' gate results, with
nothing in the output to say which mode produced it.

Task 6.6's contract test already compares the ``snapshot`` documents. This module covers the
operations it did not -- ``catalogue``, ``archs`` and ``impact`` -- and compares the **whole
answer document** in each case rather than a field, because a sampled comparison passes for
every difference it did not happen to sample.

``graphs`` is deliberately absent: :data:`~scitools_hook.understand.api_runner.UPYTHON_ONLY_OPS`
routes it to ``upython`` whatever the mode is, so a "parity" test would compare two runs of
the same interpreter. ``tests/understand/test_graphs.py`` covers the routing instead.

The last test is not a parity test but its opposite: one call that the two modes answer
differently, and dangerously so.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Mapping

import pytest
from contract_project import (
    SampleProject,
    real_env,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.understand.api_runner import ApiRunner, Operation

pytestmark = pytest.mark.contract

ARCH = "Directory Structure"

RUN = {
    "scope": "routine",
    "path": "pkg/core.py",
    "longname": "core.Engine.run",
    "parameters": "self,value",
}
"""A method three entities reach transitively, so its impact answer is not empty.

``leaf.Leaf.widen`` would have been the deeper target and answers ``total: 0``: Understand
does not resolve ``self.leaf.widen(value)`` back to the method, so a Python method called
through an instance attribute has an empty blast radius. That is recorded in ``research.md``
rather than asserted here -- this module is about the two modes agreeing, and an empty answer
they agree on would prove nothing.
"""

KNOWN_METRIC = "CountLineCode"
"""A metric this build has, used as the control for the hang below."""

UNKNOWN_METRIC = "CountParams"
"""A metric this build does *not* have -- the Gate computes it itself (req 3.5)."""

ANSWER_BUDGET_S = 30
"""Ceiling for the control probe: a metric that exists is described in milliseconds."""

HANG_BUDGET_S = 10
"""How long the unknown-metric probe is given before it counts as never returning.

Measured: a known metric answers in under 0.01 s, and the unknown one was still running
after 500 s. Ten seconds is therefore not a race -- it is two orders of magnitude of margin.
"""

PROBE = """
import sys
sys.path.append({api_dir!r})
import understand
lookup = getattr(understand.Metric, "lookup", None)
if lookup is None:
    print(repr(understand.Metric.description({metric!r})))
else:
    found = lookup({metric!r})
    print(repr("" if found is None else found.description()))
"""
"""A probe that talks to the API directly: the hang is in Understand, not in the wrapper."""


RESOURCE_BLOCK = re.compile(
    r"<br>|<img\b[^>]*>|<b>Targets By Language:</b>\s*<ul>.*?</ul>", re.DOTALL
)
"""What only the bundled interpreter adds to a description: built from resources it alone finds."""


def described(answer: Mapping[str, object]) -> dict[str, str]:
    """The descriptions in a catalogue answer, without the resource block, whitespace collapsed."""
    descriptions = answer["descriptions"]
    assert isinstance(descriptions, dict), f"no descriptions in {answer!r}"
    return {
        str(name): " ".join(RESOURCE_BLOCK.sub("", str(text)).split())
        for name, text in descriptions.items()
    }


def run_in(mode: str, op: Operation, request: Mapping[str, object]) -> dict[str, object]:
    """Run one operation against the real installation in one mode."""
    return ApiRunner(real_env(mode), NullCommandLog()).run(op, request)


def both_modes(op: Operation, request: Mapping[str, object]) -> tuple[str, str]:
    """The two modes' whole answers, canonicalised so they can be compared as documents."""
    return (
        json.dumps(run_in("upython", op, request), sort_keys=True),
        json.dumps(run_in("inprocess", op, request), sort_keys=True),
    )


def describe_probe(metric: str, budget: int) -> subprocess.CompletedProcess[str]:
    """Ask the host interpreter to describe ``metric``, killing it after ``budget`` seconds."""
    api_dir = str(real_env("inprocess").python_api_dir)
    return subprocess.run(
        [sys.executable, "-c", PROBE.format(api_dir=api_dir, metric=metric)],
        capture_output=True,
        text=True,
        timeout=budget,
        check=False,
    )


# --- the operations the two modes must agree on -----------------------------------


def test_both_modes_answer_the_same_catalogue_document() -> None:
    """The metric lists decide which thresholds run at all, so a difference is a silent skip.

    The lists must match to the character. The descriptions are compared with one block
    removed: measured on 8.0.1262, ``upython`` finds Understand's documentation resources
    and an ordinary CPython loading the same module does not, so only the bundled
    interpreter's answer carries the ``<br>``, the image and the "Targets By Language" list
    built from them (2892 characters against 2006 for ``CountLineCode``). Nothing the gate
    decides reads a description, so that is documentation depth, not a result -- but the
    text around the block has to agree, and both have to say something.
    """
    request = {
        "kinds": ["python function", "c class", "python file", "architecture"],
        "describe": [KNOWN_METRIC],
    }

    under_upython = run_in("upython", "catalogue", request)
    in_process = run_in("inprocess", "catalogue", request)

    assert KNOWN_METRIC in json.dumps(under_upython["metrics"])
    assert in_process["metrics"] == under_upython["metrics"]
    assert described(in_process)[KNOWN_METRIC]
    assert described(in_process) == described(under_upython)


def test_both_modes_answer_the_same_architecture_document(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """Nodes, members and the list of root architectures, from the same database."""
    request = {"db": str(sample_project.db("alpha")), "architecture": ARCH, "depth": 2}

    under_upython, in_process = both_modes("archs", request)

    assert '"nodes"' in under_upython
    assert in_process == under_upython


def test_both_modes_answer_the_same_impact_document(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """The blast radius (req 9.5): a reverse walk over real references, in both modes."""
    request = {
        "db": str(sample_project.db("alpha")),
        "root": str(sample_project.root("alpha")),
        "kinds_by_scope": dict(SCOPE_KINDS),
        "depth": 2,
        "keys": [RUN],
    }

    under_upython, in_process = both_modes("impact", request)

    assert "entry_point" in under_upython, "the fixture must give this method a caller"
    assert in_process == under_upython


def test_ping_differs_only_in_the_interpreter_that_answered() -> None:
    """The one documented difference, pinned so it cannot widen unnoticed.

    ``ping`` reports the Understand version *and* the Python version of whichever
    interpreter ran it, and the two interpreters are genuinely different: Understand ships
    3.12.0 and the Gate runs on whatever the operator installed it under. Everything else
    the worker answers has to be identical, which is what the tests above assert.
    """
    under_upython = run_in("upython", "ping", {})
    in_process = run_in("inprocess", "ping", {})

    assert under_upython["version"] == in_process["version"]
    # Major.minor.build, whatever the build: this asserted `startswith("6.")` until 8.0 arrived.
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(under_upython["version"]))
    assert in_process["python"] == ".".join(str(part) for part in sys.version_info[:3])
    assert set(under_upython) == set(in_process) == {"version", "python"}


# --- one call the two modes do NOT answer the same way ----------------------------


def test_describing_a_metric_understand_does_not_have_never_returns_in_this_process() -> None:
    """A measured mode difference that no timeout protects against.

    ``understand.Metric.description`` answers ``''`` for an unknown metric under ``upython``
    and **never returns at all** in an ordinary CPython process. The Gate asks exactly that
    question about its two synthetic metrics, which by construction Understand does not have
    (:mod:`scitools_hook.understand.catalogue` falls back to the Gate's own text when the
    answer is empty). The subprocess path is bounded by ``ApiRunner``'s timeout; the
    in-process path is not bounded by anything, so an in-process Gate would stop for good.

    No production caller reaches ``MetricCatalogue.describe`` today, which is the only reason
    this is a hazard rather than a defect -- and the only reason it can be recorded here
    instead of fixed.

    The control run is what keeps this test honest: the same probe, the same interpreter and
    the same API directory, asked about a metric that exists, must answer well inside the
    budget. Without it a broken probe -- a bad path, a missing licence -- would look exactly
    like the hang.
    """
    control = describe_probe(KNOWN_METRIC, ANSWER_BUDGET_S)

    assert control.returncode == 0, control.stderr
    assert "Number of lines" in control.stdout, control.stdout

    if understand_major() >= 8:
        # 8.0's `Metric.lookup` answers None for an unknown id and returns at once (measured:
        # 0.006 s in-process). The hang below is the pre-8.0 classmethod's, and is kept for
        # any 6.x/7.x install this suite still meets.
        # Not UNKNOWN_METRIC: 8.0 ships CountParams as a (disabled) HIS plugin metric, and
        # `lookup` finds it -- measured, `name() == "Parameters"`. Ask for a name no build has.
        answered = describe_probe("NoSuchMetricAtAll", HANG_BUDGET_S)
        assert answered.returncode == 0, answered.stderr
        assert answered.stdout.strip() in ("''", '""'), answered.stdout
        return
    with pytest.raises(subprocess.TimeoutExpired):
        describe_probe(UNKNOWN_METRIC, HANG_BUDGET_S)


def understand_major() -> int:
    """The installed API's major version, read the way ``ping`` reads it."""
    version = str(run_in("upython", "ping", {})["version"])
    return int(version.split(".", 1)[0])
