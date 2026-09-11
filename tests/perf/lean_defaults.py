"""Every lean default measured on one repository, and what a warm check costs with all of them on.

Task 6.3 of ``lean-code-rules`` (requirements 5.7, 6.5, 9.1, 9.5, 1.10) asks for two things a
research log can only carry if a script printed them: the count each lean rule produces on a
real repository at its shipped numbers, with the first findings of each written out so a reader
can judge them, and the wall time of a warm check with every lean rule on against the same
check with every lean rule off. This module is that procedure written down, the way
``warm_run_timing.py`` is the procedure behind the understand-8-features figures, so that task
6.4 -- which changes defaults from these numbers -- and any later re-measurement compare like
with like rather than one person's shell history with another's.

**What it runs.** With a copy of the repository's own configuration plus a ``[lean]`` section
switching all eight rules on at their shipped numbers (:func:`all_on`):

1. ``check --all --format json`` -- the whole-project pass. The JSON carries every finding;
   the ``--verbose`` diagnostics carry the "was not evaluated" line each refused rule prints,
   which is recorded beside the count because a rule the floors refused has a count of zero
   for a reason the number alone hides (requirement 1.8).
2. The same, with both floors lowered to zero (``--lower-floors``), so the dead-code and
   pass-through rules answer on a repository whose accuracy is below the shipped floor. That
   is what they *would* say, recorded so the floor's decision can be judged against it.
3. Unless ``--no-timing``: a warm check with one changed line, every lean rule off and then
   every lean rule on -- each preceded by a first check that builds the before side, so that
   only the second run of each pair is quoted, exactly as ``warm_run_timing`` does it and for
   the same reason. The one-minute load average is read before and after every timed run,
   because this machine has other work on it and a figure without its load is not comparable.

**What it never does.** It never writes into the repository beyond the one appended comment
line the timing needs, which it restores in a ``finally``; with ``--no-timing`` it writes
nothing there at all, which is how a repository somebody else owns is measured read-only. The
configuration it runs is written to a scratch directory and passed with ``--config``. It never
passes a licence switch to ``und`` and never reads ``License.conf``; licensing text in any
output stops it with that output printed verbatim (``.kiro/steering/licensing.md``).

Run it, from anywhere::

    uv run python tests/perf/lean_defaults.py /home/mc/Source/scitools-hook --lower-floors
    uv run python tests/perf/lean_defaults.py /path/to/other --lower-floors --no-timing

The module name does not begin with ``test_``, so ``pytest`` never collects it; the pure parts
-- :func:`all_on`, :func:`parse_refusals`, :func:`count_rows`, :func:`render_first` -- are
what ``test_lean_defaults.py`` drives against captured output.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from warm_run_timing import (
    DEFAULT_SCITOOLS_HOME,
    DEFAULT_TOOL,
    PROBE_LINES,
    RUN_TIMEOUT_S,
    RunRecord,
    Target,
    choose_mode,
    default_probe,
    measurement_env,
    one_changed_line,
    parse_phases,
    parse_resources,
    parse_summary,
    render_phases,
    strip_noise,
)

LEAN_SWITCHES: tuple[tuple[str, str], ...] = (
    ("unused_parameters", "structure.unused_parameter"),
    ("unused_classes", "structure.unused_class"),
    ("unused_variables", "structure.unused_variable"),
    ("pass_through", "structure.pass_through"),
    ("single_implementation", "structure.single_implementation"),
    ("over_export", "structure.over_export"),
    ("duplicates", "structure.duplicate_block"),
    ("similar_routines", "structure.similar_routine"),
)
"""Each ``[lean]`` switch and the rule name its findings carry, in the section's own order."""

SHRINK_RULES: tuple[str, ...] = (
    "routine.LinesPerStatement",
    "routine.CountLineComment",
    "file.RatioCommentToCode",
)
"""The shrink thresholds (requirement 6): on by default as warnings, so counted from the
same run without a switch of their own."""

FIRST = 10
"""How many findings of each rule are written out for a reader to judge (task 6.3)."""

_REFUSAL = re.compile(r"^(?P<rule>structure\.\w+) was not evaluated: (?P<why>.+)$")
_LICENCE = re.compile(
    r"No Server Response|Licensing Error|NoApiLicense|license is Invalid", re.IGNORECASE
)


# --- the configurations -----------------------------------------------------------


def all_on(base: str, *, lower_floors: bool) -> str:
    """The repository's own configuration plus every lean switch on at its shipped numbers.

    Only the switches are written, so every number (``duplicates_min_lines``, the threshold,
    the ignore lists) is the shipped default -- which is the thing being measured. The base
    must carry no ``[lean]`` section of its own; TOML refuses a table declared twice, and a
    base that already switches rules on is not the shipped state this measures.
    """
    lines = [base.rstrip("\n"), "", "[lean]"]
    lines += [f'{switch} = "warning"' for switch, _ in LEAN_SWITCHES]
    if lower_floors:
        lines += ["resolution_floor = 0.0", "accuracy_floor = 0.0"]
    return "\n".join(lines) + "\n"


# --- the pure parts: text in, numbers out ----------------------------------------


def parse_refusals(text: str) -> dict[str, str]:
    """Each rule's once-per-run "was not evaluated" line, keyed by rule (requirement 1.8)."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = _REFUSAL.match(line.strip())
        if match is not None:
            found.setdefault(match["rule"], match["why"])
    return found


def count_rows(
    document: Mapping[str, Any], refusals: Mapping[str, str]
) -> list[tuple[str, int, str]]:
    """``(rule, count, note)`` per lean rule and shrink threshold, in a fixed order.

    The note is the refusal line for a rule the floors stopped and empty otherwise, so that a
    zero has its reason beside it.
    """
    counts = Counter(str(finding["rule"]) for finding in document.get("findings", ()))
    rules = [rule for _, rule in LEAN_SWITCHES] + list(SHRINK_RULES)
    return [(rule, counts.get(rule, 0), refusals.get(rule, "")) for rule in rules]


def render_counts(label: str, rows: Sequence[tuple[str, int, str]]) -> str:
    """One Markdown table: rule, count, and the refusal that explains a zero when there is one."""
    head = (f"| rule ({label}) | count | note |", "| --- | --- | --- |")
    body = [f"| `{rule}` | {count} | {note} |" for rule, count, note in rows]
    return "\n".join([*head, *body])


def render_first(document: Mapping[str, Any], rule: str, limit: int = FIRST) -> str:
    """The first ``limit`` findings of one rule, in the order the check printed them."""
    found = [f for f in document.get("findings", ()) if f.get("rule") == rule]
    lines = [f"**`{rule}`**: {len(found)} findings, first {min(limit, len(found))}:"]
    lines += [f"- `{f['path']}:{f['line']}` {f['message']}" for f in found[:limit]]
    return "\n".join(lines)


def loadavg() -> str:
    """The one-minute load average, as ``/proc/loadavg`` prints it; ``?`` where there is none."""
    try:
        return Path("/proc/loadavg").read_text(encoding="utf-8").split()[0]
    except OSError:
        return "?"


# --- one measured run ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Measured:
    """One invocation with what the harness needs beside the resource figures."""

    record: RunRecord
    text: str
    load_before: str
    load_after: str


def run_measured(
    target: Target, label: str, argv: Sequence[str], env: Mapping[str, str]
) -> Measured:
    """One invocation under ``/usr/bin/time -v``, the load read around it; never raises on rc."""
    before = loadavg()
    done = subprocess.run(
        ["/usr/bin/time", "-v", *argv],
        cwd=target.repo,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
        timeout=RUN_TIMEOUT_S,
    )
    after = loadavg()
    text = strip_noise(f"{done.stdout}\n{done.stderr}")
    if _LICENCE.search(text):
        raise SystemExit(f"licensing text in the output of `{' '.join(argv)}`:\n{text}")
    record = RunRecord(
        label=label,
        mode=target.mode,
        command=" ".join(argv),
        exit_code=done.returncode,
        resources=parse_resources(text),
        phases=parse_phases(text),
        summary=parse_summary(text),
    )
    return Measured(record, text, before, after)


def render_runs(runs: Sequence[Measured]) -> str:
    """The totals table with the load column task 6.3 asks for, then every phase of every run."""
    head = (
        "| Run | Wall | User CPU | Sys CPU | CPU% | Peak RSS | load 1m before -> after | Exit |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    )
    rows = []
    for run in runs:
        use = run.record.resources
        rows.append(
            f"| {run.record.label} | {use.wall_s:.1f} s | {use.user_s:.1f} s "
            f"| {use.system_s:.1f} s "
            f"| {use.cpu_percent}% | {use.peak_rss_kb / 1024:.0f} MB "
            f"| {run.load_before} -> {run.load_after} | {run.record.exit_code} |"
        )
    return "\n".join([*head, *rows, "", render_phases([run.record for run in runs])])


# --- the two measurements ---------------------------------------------------------


def whole_project(target: Target, tool: Sequence[str], config: Path, env: Mapping[str, str]) -> str:
    """``check --all`` under ``config``: the counts, the refusals, the first findings, the cost."""
    output = config.with_suffix(".json")
    argv = [*tool, "--verbose", "--config", str(config), "check", "--all"]
    argv += ["--format", "json", "--output", str(output)]
    run = run_measured(target, f"whole project ({config.stem})", argv, env)
    if not output.is_file():
        return f"`{' '.join(argv)}` wrote no JSON (exit {run.record.exit_code}):\n\n{run.text}"
    document = json.loads(output.read_text(encoding="utf-8"))
    refusals = parse_refusals(run.text)
    rows = count_rows(document, refusals)
    parts = [
        f"- **accuracy** (after side, from the JSON): {document.get('accuracy')}",
        f"- **analysed files**: {document.get('analyzed_files')}",
        "",
        render_counts(config.stem, rows),
        "",
        *(render_first(document, rule) + "\n" for rule, _, _ in rows),
        render_runs([run]),
    ]
    return "\n".join(parts)


def warm_pair(
    target: Target, tool: Sequence[str], config: Path, env: Mapping[str, str]
) -> list[Measured]:
    """A first check and then the warm check, one changed line each, under one configuration."""
    argv = [*tool, "--verbose", "--config", str(config), "check", "--worktree"]
    probe = target.repo / target.probe
    runs = []
    with one_changed_line(probe, PROBE_LINES[0]):
        runs.append(
            run_measured(target, f"first check, one changed line ({config.stem})", argv, env)
        )
    with one_changed_line(probe, PROBE_LINES[1]):
        runs.append(
            run_measured(target, f"warm check, one changed line ({config.stem})", argv, env)
        )
    return runs


# --- the command line ------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", type=Path, help="the repository to measure")
    parser.add_argument(
        "--config", default=None, help="the base configuration; default <repo>/scitools-hook.toml"
    )
    parser.add_argument(
        "--lower-floors",
        action="store_true",
        help="also run the whole project with both [lean] floors at zero",
    )
    parser.add_argument(
        "--no-timing", action="store_true", help="skip the warm checks: write nothing to the repo"
    )
    parser.add_argument(
        "--probe-file", default=None, help="the tracked file to change, repo-relative"
    )
    parser.add_argument("--scitools-home", default=DEFAULT_SCITOOLS_HOME)
    parser.add_argument("--tool", default=" ".join(DEFAULT_TOOL), help="the command measured")
    return parser


def _configs(base: Path, scratch: Path, *, lower_floors: bool) -> dict[str, Path]:
    """The generated configurations, written to ``scratch`` and named by what they switch."""
    text = base.read_text(encoding="utf-8")
    written = {"off": scratch / "lean-off.toml", "on": scratch / "lean-on.toml"}
    written["off"].write_text(text, encoding="utf-8")
    written["on"].write_text(all_on(text, lower_floors=False), encoding="utf-8")
    if lower_floors:
        written["on-floors-zero"] = scratch / "lean-on-floors-zero.toml"
        written["on-floors-zero"].write_text(all_on(text, lower_floors=True), encoding="utf-8")
    return written


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = args.repo.resolve()
    base = Path(args.config).resolve() if args.config else repo / "scitools-hook.toml"
    env = measurement_env(args.scitools_home)
    tool = args.tool.split()
    mode = "read-only" if args.no_timing else choose_mode(repo, "in-place", allow_untracked=True)
    probe = Path(args.probe_file) if args.probe_file else default_probe(repo)
    target = Target(repo.name, repo, mode, probe)
    with tempfile.TemporaryDirectory(prefix="lean-defaults-") as scratch:
        configs = _configs(base, Path(scratch), lower_floors=args.lower_floors)
        print(f"- **repository**: `{repo}` (mode {mode}, base configuration `{base}`)")
        print(f"- **tool**: `{' '.join(tool)}`; SCITOOLS_HOME=`{env['SCITOOLS_HOME']}`")
        for name in [key for key in configs if key != "off"]:
            print(f"\n## Whole project, lean {name}\n")
            print(whole_project(target, tool, configs[name], env))
        if not args.no_timing:
            print("\n## Warm check, one changed line, lean off and then on\n")
            runs = warm_pair(target, tool, configs["off"], env) + warm_pair(
                target, tool, configs["on"], env
            )
            print(render_runs(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
