"""What a warm check costs with every lean rule on, against the same check with every rule off.

Requirement 9.5 of ``lean-code-rules``: "the measured added cost of a warm check on this
repository with every lean-code rule enabled ... shall not exceed one half of today's warm
check". Task 6.3 asks for that as a test that can fail, so this module measures it on **this
repository** -- not on the contract fixture, whose 36 routines would say nothing about a rule
that runs a whole-project pass -- in a clone under a private cache, so the developer's own
cache and working tree are untouched whatever happens to the run.

**Eight runs, two of them quoted.** The shape is ``tests/perf/warm_run_timing.py``'s, for the
reason recorded there: a warm figure is only comparable when the run before it built the
before side under the *same* configuration, and switching a reference or token rule on changes
the extraction fingerprint, so each configuration gets its own first check before the one that
is measured.

1. ``check --all``, every lean rule off -- builds the after database (cold: a clone has no
   cache, and this run's figure is not quoted).
2. first check, one changed line, every rule off -- builds the before side.
3. **warm check, one changed line, every rule off**, twice -- "today's warm check" is the
   faster of the two (see :class:`Costs`).
4. ``doctor`` under the all-on configuration -- records what the build offers, without which
   a configuration that switches a lean rule on is refused (requirement 9.3); not timed.
5. first check, one changed line, every rule on -- re-extracts both sides with references and
   tokens.
6. **warm check, one changed line, every rule on**, twice -- the cost being bounded is the
   faster of the two.

The bound is the requirement's own: ``on - off <= off / 2``. The margin the measurement has is
printed with the verdict, and the phases of both quoted runs are printed beside it, so a
failure says what dominated rather than only that something did. The 2026-09-11 measurement
behind the shipped state is in ``.kiro/specs/lean-code-rules/research.md``.

"Every lean rule" is read off :class:`~scitools_hook.config.models.LeanRules` -- the fields
that take a ``Severity | None`` -- rather than written out here, so a ninth switch added to the
family is measured by this test without anyone remembering to add it. The four dead-code and
pass-through refusals that this repository's accuracy produces at the shipped floors are
asserted on the all-on run, as the proof that the switches were really on.

This module never passes a licence switch to ``und`` and never reads ``License.conf``; the
session probe in ``tests/conftest.py`` decides whether it runs at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from contract_project import upython

from scitools_hook.config.models import LeanRules, Severity

pytestmark = pytest.mark.contract

TOOL_REPO = Path(__file__).resolve().parents[2]
"""This repository: the working tree whose code is both the tool and the subject."""

ALLOWED_SHARE = 0.5
"""Requirement 9.5: the added cost may be at most this share of the all-off warm check."""

RUN_TIMEOUT_S = 1800.0
PROBE_LINES = (
    b"\n# scitools-hook lean cost probe A (task 6.3); removed by the test\n",
    b"\n# scitools-hook lean cost probe B (task 6.3); removed by the test\n",
    b"\n# scitools-hook lean cost probe C (task 6.3); removed by the test\n",
)
"""Three different changed lines: the first check and two warm ones, each a real edit.

The same line twice would give ``und analyze -changed`` nothing to do on the second run and
report a warm figure no developer will ever see (``tests/perf/warm_run_timing.py``).
"""

LEAN_SWITCHES: tuple[str, ...] = tuple(
    name for name, field in LeanRules.model_fields.items() if field.annotation == (Severity | None)
)
"""Every ``[lean]`` rule switch, read off the model (eight on 2026-09-11)."""


@dataclass(frozen=True, slots=True)
class Run:
    """One measured invocation: what it cost and what it printed."""

    label: str
    wall_s: float
    exit_code: int
    stderr: str

    @property
    def phases(self) -> list[str]:
        """The ``... <phase> finished in <n>s`` lines, in order, for the record."""
        return [line for line in self.stderr.splitlines() if line.startswith("... ")]


@dataclass(frozen=True, slots=True)
class Costs:
    """The quoted runs and the ones that made them warm, in the order they ran.

    Each configuration's warm check ran twice, and the faster of the two is the one quoted:
    the gate runs this module beside three other workers driving ``und``, and the better of
    two runs is the figure least contaminated by whichever of them happened to be busy. It
    narrows the noise; it does not move the bound, which stays the requirement's own.
    """

    runs: tuple[Run, ...]

    @property
    def off(self) -> Run:
        return min(self.runs[2:4], key=lambda run: run.wall_s)

    @property
    def on(self) -> Run:
        return min(self.runs[6:8], key=lambda run: run.wall_s)

    @property
    def added_s(self) -> float:
        return self.on.wall_s - self.off.wall_s

    @property
    def allowed_s(self) -> float:
        return ALLOWED_SHARE * self.off.wall_s

    def table(self) -> str:
        head = "| Run | Wall | Exit |\n| --- | --- | --- |\n"
        rows = "\n".join(f"| {r.label} | {r.wall_s:.1f} s | {r.exit_code} |" for r in self.runs)
        return head + rows


# --- the measurement -------------------------------------------------------------


def _cli() -> Path:
    """The installed console script beside the interpreter the suite runs under."""
    found = shutil.which("scitools-hook")
    beside = Path(sys.executable).parent / "scitools-hook"
    if found is not None:
        return Path(found)
    if beside.is_file():
        return beside
    raise RuntimeError("no installed `scitools-hook` console script; run the suite with uv")


def _clone(into: Path) -> Path:
    """A full copy, no hardlinks, so nothing this test does can reach the original's objects."""
    target = into / "scitools-hook"
    subprocess.run(
        ["git", "clone", "--quiet", "--local", "--no-hardlinks", str(TOOL_REPO), str(target)],
        check=True,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )
    return target


def _probe_file(repo: Path) -> Path:
    """The median tracked Python leaf under ``src/``: deterministic, never an ``__init__``."""
    listed = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "src/*.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    leaves = sorted(path for path in listed if not path.endswith("__init__.py"))
    return repo / leaves[len(leaves) // 2]


def _all_on(base: str) -> str:
    """The clone's own configuration plus every lean switch on, at its shipped numbers."""
    switches = "\n".join(f'{name} = "warning"' for name in LEAN_SWITCHES)
    return f"{base.rstrip()}\n\n[lean]\n{switches}\n"


@contextmanager
def _one_changed_line(path: Path, marker: bytes) -> Iterator[None]:
    original = path.read_bytes()
    try:
        path.write_bytes(original + marker)
        yield
    finally:
        path.write_bytes(original)


def _measure(label: str, argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> Run:
    started = time.monotonic()
    done = subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
        timeout=RUN_TIMEOUT_S,
    )
    return Run(label, time.monotonic() - started, done.returncode, done.stderr)


def _pair(label: str, config: Path, repo: Path, env: Mapping[str, str]) -> list[Run]:
    """A first check and then the warm one, one changed line each, under ``config``."""
    argv = [str(_cli()), "--verbose", "--config", str(config), "check", "--worktree"]
    probe = _probe_file(repo)
    runs = []
    with _one_changed_line(probe, PROBE_LINES[0]):
        runs.append(_measure(f"first check, one changed line, lean {label}", argv, repo, env))
    for marker in PROBE_LINES[1:]:
        with _one_changed_line(probe, marker):
            runs.append(_measure(f"warm check, one changed line, lean {label}", argv, repo, env))
    return runs


@pytest.fixture(scope="module")
def measured(tmp_path_factory: pytest.TempPathFactory) -> Costs:
    """The five runs on a clone of this repository, under a cache of their own."""
    sandbox = tmp_path_factory.mktemp("lean-cost")
    repo = _clone(sandbox)
    env = dict(os.environ)
    env["SCITOOLS_HOME"] = str(upython().parent.parent.parent)
    env["XDG_CACHE_HOME"] = str(sandbox / "cache")
    base = repo / "scitools-hook.toml"
    off = sandbox / "lean-off.toml"
    on = sandbox / "lean-on.toml"
    off.write_text(base.read_text(encoding="utf-8"), encoding="utf-8")
    on.write_text(_all_on(base.read_text(encoding="utf-8")), encoding="utf-8")
    whole = [str(_cli()), "--verbose", "--config", str(off), "check", "--all"]
    doctor = [str(_cli()), "--config", str(on), "doctor"]
    runs = [_measure("whole project, cold cache, lean off", whole, repo, env)]
    runs += _pair("off", off, repo, env)
    # A fresh cache holds no measurement of what the build offers, and a configuration that
    # switches a lean rule on is refused until one exists (requirement 9.3, exit 2): the
    # first version of this fixture measured that refusal as a one-second "warm check" and
    # the bound passed on a negative added cost. `doctor` records the probe once, as the
    # error's own hint says; it is not timed and not quoted.
    runs.append(_measure("doctor (feature probe), lean on", doctor, repo, env))
    runs += _pair("on", on, repo, env)
    costs = Costs(tuple(runs))
    print(f"\n{costs.table()}\n")
    for run in (costs.off, costs.on):
        print(f"{run.label}:\n  " + "\n  ".join(run.phases))
    return costs


# --- the assertions --------------------------------------------------------------


def test_contract_both_warm_checks_ran_and_every_lean_rule_was_on(measured: Costs) -> None:
    """The instrument measured what it claims: two clean runs, and the all-on run refused
    the four floor-gated rules at this repository's accuracy, which only happens when their
    switches are on."""
    assert measured.off.exit_code == 0, measured.off.stderr
    assert measured.on.exit_code == 0, measured.on.stderr
    for rule in ("unused_parameter", "unused_class", "unused_variable", "pass_through"):
        assert f"structure.{rule} was not evaluated" in measured.on.stderr, measured.on.stderr


def test_contract_every_lean_rule_on_costs_at_most_half_of_todays_warm_check(
    measured: Costs,
) -> None:
    """Requirement 9.5, as a bound that can fail and a message that says what dominated."""
    margin = measured.allowed_s - measured.added_s
    verdict = (
        f"warm check, lean off: {measured.off.wall_s:.1f} s; lean on: {measured.on.wall_s:.1f} s; "
        f"added {measured.added_s:.1f} s against {measured.allowed_s:.1f} s allowed "
        f"({ALLOWED_SHARE:.0%} of the all-off run): margin {margin:+.1f} s\n"
        f"{measured.table()}\n"
        f"phases, lean off:\n  " + "\n  ".join(measured.off.phases) + "\n"
        "phases, lean on:\n  " + "\n  ".join(measured.on.phases)
    )
    print(verdict)
    # A run that was refused costs a second and would pass the bound with a negative added
    # cost; the guard test above says why, this one refuses to read such a run as a figure.
    assert measured.on.exit_code == 0 and measured.off.exit_code == 0, verdict
    assert measured.added_s <= measured.allowed_s, verdict
