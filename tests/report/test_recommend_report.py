"""The ``recommend`` report: the trade table, the verdict line, the tail and the skips.

Rendered from hand-built advice rather than from a snapshot, so an assertion about the layout
cannot be satisfied by an accident of the measurement, and every expected number in this file
is one written two lines above it.
"""

from __future__ import annotations

from scitools_hook.analysis.recommend import (
    Candidate,
    Distribution,
    MetricAdvice,
    Offender,
    Recommendation,
)
from scitools_hook.report.recommend import render_configuration, render_recommendation_report

SHAPE = Distribution(count=7397, p50=1, p90=5, p95=7, p99=12, maximum=45)


def advice(**overrides: object) -> MetricAdvice:
    """One piece of advice: the ``keep 10`` case a real repository produces."""
    fields: dict[str, object] = {
        "rule": "routine.CyclomaticStrict",
        "scope": "routine",
        "metric": "CyclomaticStrict",
        "configured": 10.0,
        "verdict": "keep",
        "proposed": None,
        "distribution": SHAPE,
        "candidates": (
            Candidate(limit=8.0, outside=202, share_outside=202 / 7397),
            Candidate(limit=10.0, outside=94, share_outside=94 / 7397, configured=True),
            Candidate(limit=15.0, outside=20, share_outside=20 / 7397),
        ),
        "offenders": (
            Offender(value=45.0, path="src/model.py", longname="pkg.RunReport.check", line=1254),
        ),
        "tail_ratio": 45 / 7,
        "tail_dominated": False,
    }
    fields.update(overrides)
    return MetricAdvice(**fields)  # type: ignore[arg-type]


def report(*items: MetricAdvice, skipped: tuple[object, ...] = ()) -> str:
    """The whole report for ``items``."""
    return render_recommendation_report(
        Recommendation(
            counts={"routine": 7397, "class": 1345, "file": 756},
            advice=items,
            skipped=skipped,  # type: ignore[arg-type]
        ),
        0.95,
    )


# --- the legend --------------------------------------------------------------------


def test_the_report_leads_with_what_it_is_not() -> None:
    """An operator meeting this output must not read it as a baseline."""
    text = report(advice())

    head = text.splitlines()[0]
    assert "NOT a baseline" in head
    assert "WHERE YOU ARE" in text
    assert "WHERE TO AIM" in text
    assert "Nothing here is written and nothing is applied" in text


def test_the_report_says_how_much_it_measured() -> None:
    """The denominator of every share below it, pluralised."""
    assert "# measured 7397 routines, 1345 classes, 756 files" in report(advice())


# --- the verdict and the trade -----------------------------------------------------


def test_a_kept_limit_says_keep_and_names_the_number_it_is_keeping() -> None:
    assert "routine.CyclomaticStrict  keep 10" in report(advice())


def test_a_raised_limit_shows_both_numbers() -> None:
    """The old and the new, so a reader sees the size of the move, not only its direction."""
    raised = advice(
        verdict="raise",
        proposed=15.0,
        candidates=(
            Candidate(limit=10.0, outside=94, share_outside=94 / 7397, configured=True),
            Candidate(limit=15.0, outside=20, share_outside=20 / 7397, proposed=True),
        ),
    )

    text = report(raised)

    assert "routine.CyclomaticStrict  raise 10 -> 15" in text
    assert "<- proposed" in text


def test_the_shape_line_carries_the_percentiles_and_the_coverage() -> None:
    """The evidence for the verdict sits on the line under it."""
    text = report(advice())

    assert (
        "7397 routines: p50 1, p90 5, p95 7, p99 12, max 45 -- 98.7% inside the configured 10"
    ) in text


def test_every_candidate_is_priced_in_entities_and_in_share() -> None:
    """The trade: at this limit, this many entities are outside."""
    text = report(advice())

    assert "        8       202     2.7%" in text
    assert "       10        94     1.3% <- configured" in text
    assert "       15        20     0.3%" in text


def test_the_configured_limit_is_marked_in_the_table() -> None:
    """Exactly one row, and it is the one in force."""
    marked = [row for row in report(advice()).splitlines() if "<- configured" in row]

    assert len(marked) == 1
    assert marked[0].strip().startswith("10")


def test_the_worst_entities_are_named_with_a_place_to_open_them() -> None:
    text = report(advice())

    assert "worst: 45 pkg.RunReport.check (src/model.py:1254)" in text


def test_a_file_entity_is_not_named_twice() -> None:
    """A file's long name is its path, so the parenthetical would repeat it verbatim."""
    files = advice(
        rule="file.CountLineCode",
        scope="file",
        metric="CountLineCode",
        offenders=(Offender(value=2516.0, path="src/big.py", longname="src/big.py", line=None),),
    )

    assert "worst: 2516 src/big.py" in report(files)
    assert "(src/big.py)" not in report(files)


# --- the tail ----------------------------------------------------------------------


def test_a_tail_dominated_metric_gets_its_own_section() -> None:
    """The actionable finding on a real repository, and it is not a threshold."""
    tail = advice(
        rule="routine.CountPath",
        metric="CountPath",
        configured=100.0,
        distribution=Distribution(count=7397, p50=1, p90=4, p95=8, p99=48, maximum=955514880.0),
        candidates=(Candidate(limit=100.0, outside=44, share_outside=44 / 7397, configured=True),),
        offenders=(
            Offender(
                value=955514880.0,
                path="src/model.py",
                longname="pkg.RunReport.check",
                line=1254,
            ),
        ),
        tail_ratio=955514880.0 / 8,
        tail_dominated=True,
    )

    text = report(tail)

    assert "# outliers:" in text
    assert "Fix the entities below" in text
    assert "routine.CountPath  p50 1, p95 8, max 9.55515e+08 (119,439,360x p95)" in text
    assert "9.55515e+08 pkg.RunReport.check (src/model.py:1254)" in text


def test_a_repository_with_no_tail_prints_no_outlier_section() -> None:
    """The section is news, not furniture."""
    assert "# outliers:" not in report(advice())


# --- the skips ----------------------------------------------------------------------


def test_a_threshold_that_could_not_be_measured_is_named_with_its_reason() -> None:
    """Silence about two thirds of a configuration would read as a verdict on all of it."""
    from scitools_hook.analysis.recommend import Skipped

    text = report(advice(), skipped=(Skipped(rule="file.RatioCommentToCode", reason="only a min"),))

    assert "# not measured, and why" in text
    assert "file.RatioCommentToCode  only a min" in text


def test_a_run_that_measured_nothing_says_so_rather_than_printing_an_empty_report() -> None:
    text = render_recommendation_report(Recommendation(counts={}, advice=(), skipped=()), 0.95)

    assert "no configured threshold could be measured" in text


# --- the pasteable block ------------------------------------------------------------


def test_the_report_ends_with_the_configuration_to_paste() -> None:
    """One command answers both halves: the evidence, then the lines it justifies."""
    raised = advice(verdict="raise", proposed=15.0)

    text = report(raised)

    assert "THIS IS NOT A BASELINE" in text
    assert text.rstrip().endswith("CyclomaticStrict = 15")


def test_the_configuration_can_be_had_on_its_own() -> None:
    """``--toml`` pipes the block without the report around it."""
    raised = advice(verdict="raise", proposed=15.0)

    block = render_configuration(
        Recommendation(counts={"routine": 7397}, advice=(raised,), skipped=()), 0.95
    )

    assert "CyclomaticStrict = 15" in block
    assert "keep" not in block
    assert "limit   outside" not in block


def test_the_report_is_byte_deterministic() -> None:
    """Two renders of one recommendation are the same bytes; nothing here reads a clock."""
    assert report(advice()) == report(advice())


# --- the disclosure a path scope requires --------------------------------------------


def scoped_report(*names: str) -> str:
    """The report for a run whose configuration carries ``names`` as path scopes."""
    return render_recommendation_report(
        Recommendation(
            counts={"routine": 7397},
            advice=(advice(),),
            skipped=(),
            scoped=names,
        ),
        0.95,
    )


def test_a_configured_path_scope_is_disclosed_because_the_populations_ignore_it() -> None:
    """``keep 10`` over a population containing files nothing judges by 10 overstates itself.

    The populations here are global. A scope that raises the limit for one tree does not split
    them, so the report has to say which scopes exist and that it counted across them --
    otherwise the verdict claims more than the run established.
    """
    text = scoped_report("tests", "vendor")

    assert "[scope.tests], [scope.vendor]" in text
    assert "across the WHOLE project against the GLOBAL limit" in text


def test_a_repository_with_no_path_scope_gets_no_such_note() -> None:
    """The note is a disclosure, not decoration; neither repository this was run against
    configures a scope, and a note there would be noise about nothing."""
    assert "NOTE:" not in report(advice())
    assert "NOTE:" not in scoped_report()
