"""The warm-run timing harness: what a check costs, measured the same way every time (8.1).

Requirement 8 is a performance requirement, and a performance requirement is only as good as
the measurement behind it. The figures already in ``.kiro/specs/understand-8-features/
research.md`` were taken by hand on 2026-09-05 with ``/usr/bin/time -v`` around
``check --worktree``; this module is that procedure written down, so that tasks 4.2, 9.4 and
9.5 can re-run it after each lever and compare like with like instead of comparing one
person's shell history with another's.

It measures five runs per repository, in this order:

1. **warm-up whole project** -- ``check --all``, which fills the analysis cache. In clone mode
   this run is genuinely *cold* and is labelled so, because a fresh clone has an empty cache
   and its figure is not comparable with anything else in the table.
2. **first check, one changed line** -- one appended comment line, run once to build and
   analyse the *before* shadow tree. Without it the headline figure below depends on whatever
   the machine happened to have cached from an earlier session, and a figure that depends on
   ambient state cannot be compared with the same figure taken after the next change. This
   row is reported rather than discarded: the gap between it and run 3 is the before side's
   cost, which is what requirement 8.2 proposes to remove.
3. **warm check, one changed line** -- a *different* appended line, with the file restored in
   between, so the before side is cached but the after side still has a real edit to analyse.
   This is the run requirements 8.2, 8.3 and 8.4 are about, and the only one to quote as "the
   warm check".
4. **whole project** -- ``check --all`` again, now warm.
5. **no selection** -- ``check --worktree`` on a clean tree. Requirement 8.5's control: it
   must run no analysis at all, and it is the cheap proof that the harness is measuring the
   Gate rather than the machine.

**Why there are two modes.** ``in-place`` edits the target repository and restores it in a
``finally``; it is what produced the 2026-09-05 figures, so it is the comparable mode, and it
is refused outright on a tree with uncommitted work rather than risk overwriting it.
``clone`` takes a ``git clone --local --no-hardlinks`` into a scratch directory and measures
there; it is the only safe way to measure a repository somebody else is working in, and it
pays for that with a cold cache -- hence the warm-up run above. Every row says which mode
produced it, because a clone figure and an in-place figure are different measurements.

**Why the pure parts are separate and tested.** Everything that turns text into numbers --
:func:`parse_phases`, :func:`parse_resources`, :func:`parse_summary`, :func:`render_totals`,
:func:`render_phases` -- takes a string and returns a value, and ``test_warm_run_timing.py``
drives all of it against captured output. The rest of the module starts processes and cannot
be unit tested, so it is kept to the thin shell around those functions. A parser that agreed
only with itself would still fill the research log, with fiction.

**Licensing is not this module's business.** It never passes a licence switch to ``und`` and
never reads ``License.conf``. If a run comes back with licensing text it stops and prints the
output verbatim, because that is a support question for the operator and the vendor, not
something to retry around (``.kiro/steering/licensing.md``).

Run it, from anywhere::

    uv run python tests/perf/warm_run_timing.py /home/mc/Source/scitools-hook
    uv run python tests/perf/warm_run_timing.py /path/to/other --mode clone

The module name does not begin with ``test_``, so ``pytest`` never collects it and a
multi-minute measurement never lands in the default suite.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

TOOL_REPO = Path(__file__).resolve().parents[2]
"""This repository: the working tree whose code is measured, not whatever is on ``PATH``."""

PROBE_LINES = (
    b"\n# scitools-hook warm-run timing probe A (task 1.1); removed by the harness\n",
    b"\n# scitools-hook warm-run timing probe B (task 1.1); removed by the harness\n",
)
"""The two changed lines: a trailing comment, touching no behaviour and no other file.

They differ, and that difference is the whole point. The warm-up check and the measured check
must not present ``und`` with the *same* after tree, or ``analyze -changed`` finds nothing to
do on the second and reports 0.3 s instead of the ~5 s a real edit costs -- a warm figure
five seconds better than any developer will ever see. The file is restored between them, so
each run is one changed line against ``HEAD``, and only the second is measured.
"""

DEFAULT_TOOL = ("uv", "run", "--project", str(TOOL_REPO), "scitools-hook")
DEFAULT_SCITOOLS_HOME = "/home/mc/scitools"
RUN_TIMEOUT_S = 3600.0
"""An hour per run. Long enough that a large repository finishes, short enough to not hang."""

_PHASE_LINE = re.compile(r"^\.\.\.\s+(?P<name>.+?)\s+finished in\s+(?P<seconds>[0-9.]+)s$")
_NOISE = re.compile(r"qt\.|DBus|dbind|crashpad")
_LICENCE = re.compile(
    r"No Server Response|Licensing Error|NoApiLicense|license is Invalid", re.IGNORECASE
)
_TIME_FIELDS = (
    "User time (seconds)",
    "System time (seconds)",
    "Percent of CPU this job got",
    "Elapsed (wall clock) time (h:mm:ss or m:ss)",
    "Maximum resident set size (kbytes)",
)


# --- what one run produced -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PhaseTime:
    """One ``... <name> finished in <n>s`` line. Repeats are separate values, never merged."""

    name: str
    seconds: float


@dataclass(frozen=True, slots=True)
class ResourceUse:
    """The five figures ``/usr/bin/time -v`` gives that requirement 8 asks to be recorded."""

    wall_s: float
    user_s: float
    system_s: float
    cpu_percent: int
    peak_rss_kb: int


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One measured invocation, whole: what ran, in which mode, what it cost, what it said."""

    label: str
    mode: str
    command: str
    exit_code: int
    resources: ResourceUse
    phases: tuple[PhaseTime, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class Target:
    """A repository ready to be measured, with the mode that made it ready."""

    label: str
    repo: Path
    mode: str
    probe: Path


# --- the pure parts: text in, numbers out ----------------------------------------


def strip_noise(text: str) -> str:
    """Drop the Qt/DBus lines ``und`` writes to standard error, keeping everything else."""
    return "\n".join(line for line in text.splitlines() if not _NOISE.search(line))


def parse_phases(text: str) -> tuple[PhaseTime, ...]:
    """Every finished-phase line, in the order printed, repeats kept apart (see 8.3)."""
    matches = (_PHASE_LINE.match(line.rstrip()) for line in text.splitlines())
    return tuple(
        PhaseTime(name=found["name"], seconds=float(found["seconds"]))
        for found in matches
        if found is not None
    )


def parse_summary(text: str) -> str:
    """The run's verdict line, or ``""`` when the run never got far enough to have one."""
    for line in reversed(text.splitlines()):
        if line.startswith("summary:"):
            return line.rstrip()
    return ""


def parse_elapsed(printed: str) -> float:
    """``h:mm:ss`` or ``m:ss.hh`` -- the two shapes ``time -v`` chooses between, in seconds."""
    seconds = 0.0
    for part in printed.strip().split(":"):
        seconds = seconds * 60.0 + float(part)
    return seconds


def _percent(printed: str) -> int:
    """``84%``, or ``?%`` when the run was too short for the division; unknown reads as 0."""
    digits = printed.strip().rstrip("%")
    return int(digits) if digits.isdigit() else 0


def _time_fields(text: str) -> dict[str, str]:
    """The ``time -v`` block as a mapping, ignoring the lines this harness does not record."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.strip().partition(": ")
        if separator and key in _TIME_FIELDS:
            fields[key] = value.strip()
    return fields


def parse_resources(text: str) -> ResourceUse:
    """Wall clock, both CPU times, the CPU percentage and peak RSS, from one ``time -v`` block."""
    found = _time_fields(text)
    return ResourceUse(
        wall_s=parse_elapsed(found.get(_TIME_FIELDS[3], "0:00")),
        user_s=float(found.get(_TIME_FIELDS[0], "0")),
        system_s=float(found.get(_TIME_FIELDS[1], "0")),
        cpu_percent=_percent(found.get(_TIME_FIELDS[2], "0%")),
        peak_rss_kb=int(found.get(_TIME_FIELDS[4], "0")),
    )


# --- the tables the research log takes -------------------------------------------


def _totals_row(record: RunRecord) -> str:
    use = record.resources
    return (
        f"| {record.label} | {record.mode} | {use.wall_s:.1f} s | {use.user_s:.1f} s "
        f"| {use.system_s:.1f} s | {use.cpu_percent}% | {use.peak_rss_kb / 1024:.0f} MB "
        f"| {record.exit_code} | {record.summary or '(no summary line)'} |"
    )


def render_totals(records: Sequence[RunRecord]) -> str:
    """One row per run: the figures requirement 8.1 asks to be recorded before any change."""
    head = (
        "| Run | Mode | Wall | User CPU | Sys CPU | CPU% | Peak RSS | Exit | Summary |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    )
    return "\n".join([*head, *(_totals_row(record) for record in records)])


def render_phases(records: Sequence[RunRecord]) -> str:
    """One row per phase per run, numbered, so a repeated phase reads as two measurements."""
    head = ("| Run | # | Phase | Time |", "| --- | --- | --- | --- |")
    rows = [
        f"| {record.label} | {index} | `{phase.name}` | {phase.seconds:.1f} s |"
        for record in records
        for index, phase in enumerate(record.phases, start=1)
    ] or [
        f"| {record.label} | -- | no phase reached the verbose output | -- |" for record in records
    ]
    return "\n".join([*head, *rows])


def render_report(context: Mapping[str, str], records: Sequence[RunRecord]) -> str:
    """The whole block, ready to paste under a dated heading in the research log."""
    preamble = [f"- **{name}**: {value}" for name, value in context.items()]
    return "\n".join([*preamble, "", render_totals(records), "", render_phases(records)])


# --- the impure parts: processes, clones, one changed line -----------------------


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def measurement_env(scitools_home: str) -> dict[str, str]:
    """The ambient environment plus the Understand install every run must agree on."""
    env = dict(os.environ)
    env["SCITOOLS_HOME"] = scitools_home
    return env


def run_measured(
    target: Target, label: str, argv: Sequence[str], env: Mapping[str, str]
) -> RunRecord:
    """One invocation under ``/usr/bin/time -v``, parsed into a record. Never raises on rc."""
    done = subprocess.run(
        ["/usr/bin/time", "-v", *argv],
        cwd=target.repo,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
        timeout=RUN_TIMEOUT_S,
    )
    text = strip_noise(f"{done.stdout}\n{done.stderr}")
    if _LICENCE.search(text):
        raise SystemExit(f"licensing text in the output of `{' '.join(argv)}`:\n{text}")
    return RunRecord(
        label=label,
        mode=target.mode,
        command=" ".join(argv),
        exit_code=done.returncode,
        resources=parse_resources(text),
        phases=parse_phases(text),
        summary=parse_summary(text),
    )


@contextmanager
def one_changed_line(path: Path, marker: bytes) -> Iterator[None]:
    """Append one comment line, then put the file back byte for byte whatever happened."""
    original = path.read_bytes()
    try:
        path.write_bytes(original + marker)
        yield
    finally:
        path.write_bytes(original)


def default_probe(repo: Path) -> Path:
    """A tracked Python file to change: never a package ``__init__``, whose dependents are all
    of it, and never the first alphabetically, which is an arbitrary corner of the tree. The
    median of the sorted list under ``src/`` is deterministic and re-runnable, which is what
    comparing one measurement with the next needs."""
    tracked = [Path(line) for line in _git(repo, "ls-files", "*.py").splitlines() if line]
    leaves = [path for path in tracked if path.name != "__init__.py"]
    pool = [path for path in leaves if path.parts[:1] == ("src",)] or leaves or tracked
    if not pool:
        raise SystemExit(f"{repo}: no tracked Python file to change")
    return sorted(pool)[len(pool) // 2]


def dirty_paths(repo: Path, *, tracked_only: bool) -> tuple[str, ...]:
    """The porcelain lines that make a tree unclean, optionally ignoring untracked files."""
    lines = tuple(line for line in _git(repo, "status", "--porcelain").splitlines() if line.strip())
    return tuple(line for line in lines if not line.startswith("??")) if tracked_only else lines


def choose_mode(repo: Path, wanted: str, *, allow_untracked: bool) -> str:
    """Refuse ``in-place`` on a tree with work in it. Restoring a file the harness did not
    write is data loss, so the check is a precondition and not a warning."""
    if wanted == "clone":
        return "clone"
    blocking = dirty_paths(repo, tracked_only=allow_untracked)
    if not blocking:
        return "in-place"
    listed = "\n  ".join(blocking[:10])
    if wanted == "in-place":
        raise SystemExit(f"{repo} is not clean; refusing to edit it in place:\n  {listed}")
    return "clone"


def clone_for_measurement(repo: Path, into: Path) -> Path:
    """A full copy, no hardlinks: nothing the harness does can reach the original's objects."""
    destination = into / repo.name
    subprocess.run(
        ["git", "clone", "--local", "--no-hardlinks", str(repo), str(destination)],
        capture_output=True,
        text=True,
        check=True,
        timeout=RUN_TIMEOUT_S,
    )
    return destination


@contextmanager
def prepared_target(repo: Path, mode: str, probe: str | None) -> Iterator[Target]:
    """Hand back a repository that is safe to measure, cloning it first when it has to be."""
    if mode == "in-place":
        yield Target(repo.name, repo, mode, Path(probe) if probe else default_probe(repo))
        return
    with tempfile.TemporaryDirectory(prefix="warm-run-timing-") as scratch:
        clone = clone_for_measurement(repo, Path(scratch))
        yield Target(repo.name, clone, mode, Path(probe) if probe else default_probe(clone))


def measure_target(target: Target, tool: Sequence[str], env: Mapping[str, str]) -> list[RunRecord]:
    """The five runs, in the order that makes the headline one warm (see the module docstring)."""
    worktree = [*tool, "--verbose", "check", "--worktree"]
    whole = [*tool, "--verbose", "check", "--all"]
    first = (
        "warm-up whole project (cold cache)" if target.mode == "clone" else "warm-up whole project"
    )
    records = [run_measured(target, first, whole, env)]
    probe = target.repo / target.probe
    # Two different changed lines, each restored before the next: the first builds and
    # analyses the before shadow, the second is the measured warm run and still pays for a
    # genuinely new edit on the after side.
    with one_changed_line(probe, PROBE_LINES[0]):
        records.append(run_measured(target, "first check, one changed line", worktree, env))
    with one_changed_line(probe, PROBE_LINES[1]):
        records.append(run_measured(target, "warm check, one changed line", worktree, env))
    records.append(run_measured(target, "whole project (--all)", whole, env))
    records.append(run_measured(target, "no selection (nothing changed)", worktree, env))
    return records


# --- the command line ------------------------------------------------------------


def _describe(argv: Sequence[str], env: Mapping[str, str]) -> str:
    """One line of version output, or the reason there is none. Never a licence switch."""
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, check=False, env=dict(env), timeout=120.0
        )
    except OSError as failure:  # noqa: BLE001 - the reason belongs in the log, not a traceback
        return f"(unavailable: {failure})"
    return strip_noise(f"{done.stdout}\n{done.stderr}").strip().splitlines()[-1:][0] or "(silent)"


def _context(
    target: Target, source: Path, tool: Sequence[str], env: Mapping[str, str]
) -> dict[str, str]:
    und = Path(env["SCITOOLS_HOME"]) / "bin" / "linux64" / "und"
    return {
        "measured": date.today().isoformat(),
        "repository": f"`{source}`",
        "mode": f"{target.mode} (probe file `{target.probe}`)",
        "binary": f"`{' '.join(tool)}`",
        "tool version": _describe([*tool, "--version"], env),
        "Understand": _describe([str(und), "version"], env),
        "machine": f"{os.cpu_count()} cores, SCITOOLS_HOME=`{env['SCITOOLS_HOME']}`",
        "untracked at measurement": str(len(dirty_paths(source, tracked_only=False))),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", type=Path, help="the repository to measure")
    parser.add_argument(
        "--mode",
        choices=("auto", "in-place", "clone"),
        default="auto",
        help="auto falls back to clone when the tree is not clean; in-place refuses instead",
    )
    parser.add_argument(
        "--allow-untracked",
        action="store_true",
        help="let in-place run with untracked files present, which the probe never touches",
    )
    parser.add_argument(
        "--probe-file", default=None, help="the tracked file to change, repo-relative"
    )
    parser.add_argument("--scitools-home", default=DEFAULT_SCITOOLS_HOME)
    parser.add_argument("--tool", default=" ".join(DEFAULT_TOOL), help="the command measured")
    parser.add_argument(
        "--target-wall",
        type=float,
        default=None,
        help="exit 1 when the warm one-line run is slower than this many seconds (8.4)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.repo.resolve()
    env = measurement_env(args.scitools_home)
    tool = args.tool.split()
    mode = choose_mode(source, args.mode, allow_untracked=args.allow_untracked)
    with prepared_target(source, mode, args.probe_file) as target:
        records = measure_target(target, tool, env)
        print(render_report(_context(target, source, tool, env), records))
    warm = next(record for record in records if record.label.startswith("warm check"))
    if args.target_wall is not None and warm.resources.wall_s > args.target_wall:
        print(
            f"\nOVER TARGET: {warm.resources.wall_s:.1f} s > {args.target_wall:.1f} s",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
