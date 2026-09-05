"""Unit tests for the warm-run timing harness's pure parts (task 1.1, requirement 8.1).

The harness itself drives real ``und`` analyses and takes minutes, so its module name does
not begin with ``test_`` and the default suite never collects it. What *is* collected is
everything in it that turns text into numbers: the phase lines the Gate prints under
``--verbose``, the ``/usr/bin/time -v`` block, and the Markdown tables that go into the
research log.

:data:`VERBOSE_TAIL` and :data:`TIME_V_BLOCK` are **verbatim** output captured on 2026-09-05
from ``uv run scitools-hook --verbose check --worktree`` on this repository with one appended
comment line, and from ``/usr/bin/time -v`` around the same command. Only the absolute paths
in the command-trace lines are elided, to fit the 100-column limit; every phase line, every
summary line and every ``time -v`` figure is exactly what the tools printed. That is the point of
them: a parser written against imagined output agrees with itself and disagrees with the
tool, and the figures it then reports into the research log are fiction with a date on them.

Three properties are load-bearing here:

* **A repeated phase name is two measurements, not one.** A warm run reads the after snapshot
  twice and the before snapshot twice, with different times each -- 4.3 s then 7.1 s. A
  parser that keyed phases by name would silently drop half the run's cost, and that half is
  exactly the second pass requirement 8.3 exists to remove. So the parse keeps occurrences in
  order and the tests count them.
* **The Qt/DBus noise ``und`` writes to standard error is neither a phase nor a summary.** It
  is interleaved with both, so the filter runs before the parse rather than after.
* **A run with no summary line is recorded as such, not guessed.** A check that dies on a
  configuration error still has resource figures worth keeping, and inventing a verdict for
  it would put a fabricated row in the research log.

The noise samples are the documented shapes (``qt.``, ``DBus``, ``dbind``, ``crashpad``)
rather than a capture: this machine's ``und`` ran silent on 2026-09-05, which is why the
filter has to be tested against the shapes rather than against whatever happened to appear.
"""

from __future__ import annotations

import pytest
from warm_run_timing import (
    PhaseTime,
    ResourceUse,
    RunRecord,
    parse_elapsed,
    parse_phases,
    parse_resources,
    parse_summary,
    render_phases,
    render_totals,
    strip_noise,
)

# --- captured samples ------------------------------------------------------------

VERBOSE_TAIL = """\
$ .../und -quiet -db ~/.cache/scitools-hook/7bb/before.und add .../before  [0.54s, rc=0]
... analysing the before database finished in 0.5s
... reading the after snapshot
$ .../upython .../understand/worker.py snapshot  [4.33s, rc=0]
... reading the after snapshot finished in 4.3s
... reading the before snapshot
$ .../upython .../understand/worker.py snapshot  [4.35s, rc=0]
... reading the before snapshot finished in 4.4s
... reading the after snapshot
$ .../upython .../understand/worker.py snapshot  [7.10s, rc=0]
... reading the after snapshot finished in 7.1s
... reading the before snapshot
$ .../upython .../understand/worker.py snapshot  [7.17s, rc=0]
... reading the before snapshot finished in 7.2s
summary: 0 errors, 4 warnings, 0 pre-existing, 0 blocking | exit 0: no blocking violations
"""

TIME_V_BLOCK = """\
\tCommand being timed: "uv run scitools-hook --verbose check --worktree"
\tUser time (seconds): 0.76
\tSystem time (seconds): 0.09
\tPercent of CPU this job got: 84%
\tElapsed (wall clock) time (h:mm:ss or m:ss): 0:01.01
\tMaximum resident set size (kbytes): 46060
\tExit status: 0
"""

NOISY = """\
qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.
dbind-WARNING **: 09:14:02.115: Couldn't connect to accessibility bus
Failed to connect to the DBus session bus
[1262:1262:0905/091402.115:ERROR:crashpad_client_linux.cc(0)] pthread_create
... reading the after snapshot finished in 4.3s
summary: 0 errors, 0 warnings, 0 pre-existing, 0 blocking | exit 0: no blocking violations
"""


def _record(label: str, mode: str = "in-place") -> RunRecord:
    """One record built the way the harness builds it: parsed, never hand-written."""
    return RunRecord(
        label=label,
        mode=mode,
        command="scitools-hook --verbose check --worktree",
        exit_code=0,
        resources=parse_resources(TIME_V_BLOCK),
        phases=parse_phases(VERBOSE_TAIL),
        summary=parse_summary(VERBOSE_TAIL),
    )


# --- the phase lines -------------------------------------------------------------


def test_every_phase_line_becomes_one_measurement_in_order() -> None:
    phases = parse_phases(VERBOSE_TAIL)
    assert [phase.name for phase in phases] == [
        "analysing the before database",
        "reading the after snapshot",
        "reading the before snapshot",
        "reading the after snapshot",
        "reading the before snapshot",
    ]


def test_a_repeated_phase_name_keeps_both_of_its_times() -> None:
    """The two-pass extraction is the cost 8.3 removes; collapsing by name would hide it."""
    after = [phase.seconds for phase in parse_phases(VERBOSE_TAIL) if "after" in phase.name]
    assert after == [4.3, 7.1]


def test_the_announcement_of_a_phase_is_not_a_measurement() -> None:
    """``... reading the after snapshot`` with no time is the *start* line, printed twice."""
    assert len(parse_phases(VERBOSE_TAIL)) == 5


def test_a_command_trace_carrying_seconds_is_not_a_phase() -> None:
    """``$ ... [4.33s, rc=0]`` is the child process, already counted inside its phase."""
    assert all("upython" not in phase.name for phase in parse_phases(VERBOSE_TAIL))


def test_the_phase_total_is_the_sum_of_the_recorded_phases() -> None:
    assert sum(phase.seconds for phase in parse_phases(VERBOSE_TAIL)) == pytest.approx(23.5)


# --- the noise -------------------------------------------------------------------


def test_the_qt_and_dbus_noise_is_dropped_before_anything_is_parsed() -> None:
    kept = strip_noise(NOISY).splitlines()
    assert len(kept) == 2
    assert kept[0].startswith("... reading the after snapshot")


def test_a_phase_survives_the_noise_it_was_interleaved_with() -> None:
    phases = parse_phases(strip_noise(NOISY))
    assert phases == (PhaseTime(name="reading the after snapshot", seconds=4.3),)


def test_the_summary_survives_the_noise_it_was_interleaved_with() -> None:
    assert parse_summary(strip_noise(NOISY)).startswith("summary: 0 errors, 0 warnings")


# --- the verdict -----------------------------------------------------------------


def test_the_summary_line_is_taken_whole() -> None:
    assert parse_summary(VERBOSE_TAIL) == (
        "summary: 0 errors, 4 warnings, 0 pre-existing, 0 blocking | exit 0: no blocking violations"
    )


def test_a_run_without_a_summary_reports_none_rather_than_guessing_one() -> None:
    assert parse_summary("Error: No such option: --verbose\n") == ""


# --- the resource block ----------------------------------------------------------


def test_the_time_v_block_gives_wall_cpu_and_peak_memory() -> None:
    assert parse_resources(TIME_V_BLOCK) == ResourceUse(
        wall_s=1.01,
        user_s=0.76,
        system_s=0.09,
        cpu_percent=84,
        peak_rss_kb=46060,
    )


@pytest.mark.parametrize(
    ("printed", "seconds"),
    [("0:01.01", 1.01), ("0:32.60", 32.6), ("2:05.00", 125.0), ("1:02:03", 3723.0)],
)
def test_both_elapsed_shapes_time_v_prints_are_read(printed: str, seconds: float) -> None:
    """``h:mm:ss`` for a run over an hour, ``m:ss`` below it -- facdrone may reach either."""
    assert parse_elapsed(printed) == pytest.approx(seconds)


def test_an_unknown_cpu_percentage_does_not_abort_the_measurement() -> None:
    """``time -v`` prints ``?%`` when the run was too short to divide by; the wall still counts."""
    unknown = TIME_V_BLOCK.replace("84%", "?%")
    assert parse_resources(unknown).cpu_percent == 0
    assert parse_resources(unknown).wall_s == pytest.approx(1.01)


# --- the tables that go into the research log ------------------------------------


def test_the_totals_table_carries_one_row_per_run_with_its_mode() -> None:
    table = render_totals([_record("warm check, one changed line"), _record("clone run", "clone")])
    rows = [line for line in table.splitlines() if line.startswith("| ")]
    assert len(rows) == 4  # header, separator, two runs
    assert "| warm check, one changed line | in-place |" in rows[2]
    assert "| clone run | clone |" in rows[3]


def test_the_totals_row_reports_wall_cpu_memory_and_the_verdict() -> None:
    row = render_totals([_record("warm")]).splitlines()[2]
    assert "1.0 s" in row and "0.8 s" in row and "84%" in row
    assert "45 MB" in row
    assert "0 errors, 4 warnings" in row


def test_a_run_without_a_summary_says_so_in_the_table() -> None:
    silent = RunRecord(
        label="failed",
        mode="in-place",
        command="scitools-hook --verbose check --worktree",
        exit_code=2,
        resources=parse_resources(TIME_V_BLOCK),
        phases=(),
        summary="",
    )
    assert "(no summary line)" in render_totals([silent])


def test_the_phase_table_numbers_each_repeat_of_a_phase() -> None:
    rows = render_phases([_record("warm")]).splitlines()[2:]
    assert len(rows) == 5
    assert rows[1].startswith("| warm | 2 | `reading the after snapshot` | 4.3 s |")
    assert rows[3].startswith("| warm | 4 | `reading the after snapshot` | 7.1 s |")


def test_a_run_that_reached_no_phase_still_renders_a_table() -> None:
    """The no-selection control runs no analysis at all -- an empty body, not a crash."""
    quiet = RunRecord(
        label="no selection",
        mode="in-place",
        command="scitools-hook --verbose check --worktree",
        exit_code=0,
        resources=parse_resources(TIME_V_BLOCK),
        phases=(),
        summary=parse_summary(VERBOSE_TAIL),
    )
    body = render_phases([quiet]).splitlines()[2:]
    assert body == ["| no selection | -- | no phase reached the verbose output | -- |"]


def test_both_tables_are_pasteable_markdown() -> None:
    records = [_record("warm")]
    for table in (render_totals(records), render_phases(records)):
        lines = table.splitlines()
        assert lines[1].startswith("| --- |")
        assert all(line.startswith("| ") and line.endswith(" |") for line in lines)
