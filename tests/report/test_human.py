"""The human renderer: grouping, ordering, quiet mode, colour and the agent block.

The four snapshots below are the contract of requirement 7.3 (findings grouped by file,
ordered by severity then by how far over the limit they are, followed by one summary line
naming the counts and the exit code meaning), 7.8 (quiet prints only the summary and the
blocking findings), 7.6 (no escape sequences unless colour was decided on) and 10.4 (a
blocking run ends with instructions for an agent).

Everything the renderer needs to know about the terminal arrives as ``ColorMode``: the
renderer never looks at ``sys.stdout`` or the environment, so these tests never patch either.
:func:`resolve_color` is the pure decision function the CLI feeds with the facts it does own.

The fixture run is deliberately awkward: it mixes a routine, a file, an architecture node and
a project-wide finding, a pre-existing violation, a limit that came from the baseline, a
CodeCheck row whose entity lives in ``details`` and whose line is unknown, and a structural
finding with no value at all -- the shapes tasks 4.3, 4.4 and 4.7 warned the renderers about.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

import pytest

from scitools_hook.config.metric_names import Scope
from scitools_hook.config.models import Severity
from scitools_hook.exit_codes import ExitCode, describe
from scitools_hook.models.findings import (
    Finding,
    FindingKind,
    HighestValue,
    RunResult,
    TightenedLimit,
)
from scitools_hook.models.snapshot import EntityKey, EntityRef, ParseError
from scitools_hook.report.human import (
    PARSE_HEADER,
    ColorMode,
    Verbosity,
    _limit_distance,
    overshoot_ratio,
    render_human,
    resolve_color,
)

ENGINE: Final = "src/analysis/engine.py"
APP: Final = "src/cli/app.py"
TEXT: Final = "src/util/text.cpp"

ANSI: Final = re.compile(r"\x1b\[[0-9;]*m")


def routine_ref(longname: str, path: str, line: int) -> EntityRef:
    """The entity reference a routine-scope finding carries."""
    key = EntityKey(scope="routine", path=path, longname=longname, parameters="")
    return EntityRef(key=key, kind="Python Function", name=longname.rpartition(".")[2], line=line)


def file_ref(path: str) -> EntityRef:
    """The entity reference a file-scope finding carries; its longname is the path itself."""
    key = EntityKey(scope="file", path=path, longname=path)
    return EntityRef(key=key, kind="File", name=path.rpartition("/")[2], line=1)


EVALUATE: Final = routine_ref("engine.Engine.evaluate", ENGINE, 42)


def cyclomatic() -> Finding:
    """Worst offender in ``engine.py``: twice its limit, and it got worse."""
    return Finding(
        kind="threshold",
        rule="routine.CyclomaticStrict",
        metric="CyclomaticStrict",
        scope="routine",
        entity=EVALUATE,
        path=ENGINE,
        line=42,
        value=20,
        before=9,
        limit=10,
        severity="error",
        blocking=True,
        message="routine engine.Engine.evaluate CyclomaticStrict is 20, over the maximum of 10",
        hint="split the decision groups into named routines",
    )


def nesting() -> Finding:
    """Same routine, smaller overshoot: it must print after the cyclomatic finding."""
    return Finding(
        kind="threshold",
        rule="routine.MaxNesting",
        metric="MaxNesting",
        scope="routine",
        entity=EVALUATE,
        path=ENGINE,
        line=42,
        value=5,
        limit=3,
        severity="error",
        blocking=True,
        message="routine engine.Engine.evaluate MaxNesting is 5, over the maximum of 3",
        hint="extract the inner block into its own routine or return early",
    )


def comment_ratio() -> Finding:
    """A warning, and a ``min`` bound: its ratio is below 1 and it sorts last regardless."""
    return Finding(
        kind="threshold",
        rule="file.RatioCommentToCode",
        metric="RatioCommentToCode",
        scope="file",
        entity=file_ref(ENGINE),
        path=ENGINE,
        line=1,
        value=0.05,
        limit=0.1,
        severity="warning",
        message="file src/analysis/engine.py RatioCommentToCode is 0.05, under the minimum 0.1",
        hint="document why the module exists",
    )


def preexisting_lines() -> Finding:
    """An error that was already true before the change, under a limit from the baseline."""
    return Finding(
        kind="threshold",
        rule="file.CountLineCode",
        metric="CountLineCode",
        scope="file",
        entity=file_ref(APP),
        path=APP,
        line=1,
        value=700,
        before=700,
        limit=500,
        limit_source="baseline",
        severity="error",
        preexisting=True,
        message="file src/cli/app.py CountLineCode is 700, over the maximum of 500",
        hint="move a cohesive group of functions into a new module",
    )


def file_cycle() -> Finding:
    """A structural finding with no value and no limit: it has no overshoot ratio."""
    return Finding(
        kind="structural",
        rule="structure.file_cycle",
        scope="file",
        path=APP,
        limit_source="rule",
        severity="error",
        blocking=True,
        message="src/cli/app.py is in a new dependency cycle with src/analysis/engine.py",
        hint="invert one dependency: move the shared type into a module both can import",
    )


def codecheck() -> Finding:
    """A CodeCheck row: no ``entity`` field, no line, and no hint attached yet."""
    return Finding(
        kind="codecheck",
        rule="codecheck.CPP_F016",
        scope="file",
        path=TEXT,
        line=None,
        limit_source="rule",
        severity="warning",
        message="Function 'trim' has no explicit return type",
        details={"check_name": "Explicit return type", "entity": "text::trim"},
    )


def coupling() -> Finding:
    """An architecture-node finding: its path is a node path, not a file (task 4.3)."""
    return Finding(
        kind="structural",
        rule="structure.coupling",
        scope="arch",
        path="Core",
        value=30,
        limit=12,
        limit_source="rule",
        severity="error",
        blocking=True,
        message="Core makes 30 references to Ui after the change, over the maximum of 12",
        hint="narrow the traffic to a single interface",
    )


def project_average() -> Finding:
    """A population threshold: no entity and no path at all."""
    return Finding(
        kind="threshold",
        rule="project.AVG:CyclomaticStrict",
        metric="AVG:CyclomaticStrict",
        scope="project",
        path="",
        value=4.5,
        limit=3,
        severity="error",
        blocking=True,
        message="project AVG:CyclomaticStrict is 4.5, over the maximum of 3",
        hint="fix the worst routines first",
    )


def comment_ratchet() -> Finding:
    """A ratchet finding on a ``min`` threshold: worse than before, yet far inside the limit.

    ``file.RatioCommentToCode`` ships as ``{min = 0.1}`` with ``ratchet = true`` (defaults.py),
    so this is the shape a real run produces: the comment ratio fell from 0.60 to 0.40, which
    is four times the required minimum and therefore no breach at all -- only a regression.
    """
    return Finding(
        kind="ratchet",
        rule="file.RatioCommentToCode",
        metric="RatioCommentToCode",
        scope="file",
        entity=file_ref(ENGINE),
        path=ENGINE,
        line=1,
        value=0.40,
        before=0.60,
        limit=0.1,
        severity="warning",
        message="file src/analysis/engine.py RatioCommentToCode fell from 0.6 to 0.4",
        hint="restore the explanation the change removed",
    )


def run(
    *findings: Finding,
    parse_errors: Sequence[ParseError] = (),
    ignored_counts: Mapping[Scope, int] | None = None,
    unavailable_metrics: Mapping[str, Sequence[str]] | None = None,
    tightened: Sequence[TightenedLimit] = (),
    highest: Sequence[HighestValue] = (),
) -> RunResult:
    """A ``RunResult`` around ``findings`` with the counts the pipeline would have filled in."""
    return RunResult(
        tool_version="0.1.0",
        understand_version="Understand 7.0",
        repo_root="/repo",
        selection="staged",
        started_at="2026-01-01T09:00:00Z",
        seconds=1.5,
        findings=list(findings),
        parse_errors=list(parse_errors),
        ignored_counts=dict(ignored_counts or {}),
        unavailable_metrics={
            language: list(metrics) for language, metrics in (unavailable_metrics or {}).items()
        },
        tightened=list(tightened),
        highest=list(highest),
        analyzed_files=3,
        blocking_count=sum(1 for finding in findings if finding.blocking),
        warning_count=sum(1 for finding in findings if finding.severity == "warning"),
        preexisting_count=sum(1 for finding in findings if finding.preexisting),
    )


def blocking_run() -> RunResult:
    """The mixed run every snapshot below renders."""
    return run(
        cyclomatic(),
        nesting(),
        comment_ratio(),
        preexisting_lines(),
        file_cycle(),
        codecheck(),
        coupling(),
        project_average(),
    )


BLOCKING_TEXT: Final = """\
src/analysis/engine.py
  error    routine.CyclomaticStrict  engine.Engine.evaluate  line 42  2.0x limit, was 9
    routine engine.Engine.evaluate CyclomaticStrict is 20, over the maximum of 10
    hint: split the decision groups into named routines
  error    routine.MaxNesting  engine.Engine.evaluate  line 42  1.7x limit
    routine engine.Engine.evaluate MaxNesting is 5, over the maximum of 3
    hint: extract the inner block into its own routine or return early
  warning  file.RatioCommentToCode  line 1  0.5x limit
    file src/analysis/engine.py RatioCommentToCode is 0.05, under the minimum 0.1
    hint: document why the module exists

src/cli/app.py
  error    file.CountLineCode  line 1  1.4x limit, was 700, pre-existing, limit from baseline
    file src/cli/app.py CountLineCode is 700, over the maximum of 500
    hint: move a cohesive group of functions into a new module
  error    structure.file_cycle
    src/cli/app.py is in a new dependency cycle with src/analysis/engine.py
    hint: invert one dependency: move the shared type into a module both can import

src/util/text.cpp
  warning  codecheck.CPP_F016  text::trim
    Function 'trim' has no explicit return type

architecture node Core
  error    structure.coupling  2.5x limit
    Core makes 30 references to Ui after the change, over the maximum of 12
    hint: narrow the traffic to a single interface

project-wide
  error    project.AVG:CyclomaticStrict  1.5x limit
    project AVG:CyclomaticStrict is 4.5, over the maximum of 3
    hint: fix the worst routines first

summary: 6 errors, 2 warnings, 1 pre-existing, 5 blocking | exit 1: blocking violations found

agent instructions
  5 findings block this commit; fix the code, do not relax the limits.
  Re-run while editing:  scitools-hook check --worktree
  Re-run before commit:  scitools-hook check --staged
  Each finding's "hint:" line says what to change; --format json carries the same hints."""

WARNINGS_TEXT: Final = """\
src/analysis/engine.py
  warning  file.RatioCommentToCode  line 1  0.5x limit
    file src/analysis/engine.py RatioCommentToCode is 0.05, under the minimum 0.1
    hint: document why the module exists

src/util/text.cpp
  warning  codecheck.CPP_F016  text::trim
    Function 'trim' has no explicit return type

summary: 0 errors, 2 warnings, 0 pre-existing, 0 blocking | exit 0: no blocking violations"""

QUIET_TEXT: Final = """\
src/analysis/engine.py
  error    routine.CyclomaticStrict  engine.Engine.evaluate  line 42  2.0x limit, was 9
    routine engine.Engine.evaluate CyclomaticStrict is 20, over the maximum of 10
    hint: split the decision groups into named routines
  error    routine.MaxNesting  engine.Engine.evaluate  line 42  1.7x limit
    routine engine.Engine.evaluate MaxNesting is 5, over the maximum of 3
    hint: extract the inner block into its own routine or return early

src/cli/app.py
  error    structure.file_cycle
    src/cli/app.py is in a new dependency cycle with src/analysis/engine.py
    hint: invert one dependency: move the shared type into a module both can import

architecture node Core
  error    structure.coupling  2.5x limit
    Core makes 30 references to Ui after the change, over the maximum of 12
    hint: narrow the traffic to a single interface

project-wide
  error    project.AVG:CyclomaticStrict  1.5x limit
    project AVG:CyclomaticStrict is 4.5, over the maximum of 3
    hint: fix the worst routines first

summary: 6 errors, 2 warnings, 1 pre-existing, 5 blocking | exit 1: blocking violations found"""

EMPTY_TEXT: Final = """\
nothing to report: this change breaks no rule
summary: 0 errors, 0 warnings, 0 pre-existing, 0 blocking | exit 0: no blocking violations"""


# --- the four required snapshots ------------------------------------------------


def test_blocking_run_snapshot() -> None:
    got = render_human(blocking_run(), Verbosity.NORMAL, ColorMode.OFF, True)
    assert got == BLOCKING_TEXT


def test_warnings_only_snapshot() -> None:
    result = run(comment_ratio(), codecheck())
    got = render_human(result, Verbosity.NORMAL, ColorMode.OFF, True)
    assert got == WARNINGS_TEXT


def test_quiet_snapshot() -> None:
    got = render_human(blocking_run(), Verbosity.QUIET, ColorMode.OFF, True)
    assert got == QUIET_TEXT


def test_non_tty_snapshot_carries_no_escape_sequences() -> None:
    """What the CLI decides for output redirected into a file: no TTY, no ``NO_COLOR``."""
    color = resolve_color(None, is_tty=False, no_color=False)
    got = render_human(blocking_run(), Verbosity.NORMAL, color, True)
    assert color is ColorMode.OFF
    assert "\x1b" not in got
    assert got == BLOCKING_TEXT


# --- colour ---------------------------------------------------------------------


def test_colour_on_wraps_text_but_changes_nothing_else() -> None:
    colored = render_human(blocking_run(), Verbosity.NORMAL, ColorMode.ON, True)
    assert "\x1b[" in colored
    assert ANSI.sub("", colored) == BLOCKING_TEXT


def test_colour_off_is_plain_text() -> None:
    plain = render_human(blocking_run(), Verbosity.NORMAL, ColorMode.OFF, True)
    assert "\x1b" not in plain


@pytest.mark.parametrize(
    ("force", "is_tty", "no_color", "expected"),
    [
        (None, True, False, ColorMode.ON),
        (None, False, False, ColorMode.OFF),
        (None, True, True, ColorMode.OFF),
        (True, False, True, ColorMode.ON),
        (False, True, False, ColorMode.OFF),
    ],
)
def test_resolve_color(
    force: bool | None, is_tty: bool, no_color: bool, expected: ColorMode
) -> None:
    assert resolve_color(force, is_tty=is_tty, no_color=no_color) is expected


# --- ordering -------------------------------------------------------------------


def rules_in(text: str) -> list[str]:
    """Every rule name in the order the renderer printed it."""
    return re.findall(r"^  (?:error|warning)\s+(\S+)", text, flags=re.MULTILINE)


def test_errors_print_before_warnings_in_the_same_file() -> None:
    text = render_human(run(comment_ratio(), nesting(), cyclomatic()), color=ColorMode.OFF)
    assert rules_in(text) == [
        "routine.CyclomaticStrict",
        "routine.MaxNesting",
        "file.RatioCommentToCode",
    ]


def test_the_larger_overshoot_prints_first() -> None:
    text = render_human(run(nesting(), cyclomatic()), color=ColorMode.OFF)
    assert rules_in(text) == ["routine.CyclomaticStrict", "routine.MaxNesting"]


def test_a_finding_without_a_ratio_sorts_after_one_with_a_ratio() -> None:
    text = render_human(run(file_cycle(), preexisting_lines()), color=ColorMode.OFF)
    assert rules_in(text) == ["file.CountLineCode", "structure.file_cycle"]


def graded(
    rule: str,
    severity: Severity = "error",
    value: float | None = None,
    limit: float | None = None,
    kind: FindingKind = "threshold",
) -> Finding:
    """A finding in ``engine.py`` reduced to what ordering reads: rule, severity, overshoot.

    The rules below are chosen so that alphabetical order *contradicts* the order requirement
    7.3 asks for; a renderer that fell back on the tie-breakers would print them the wrong way
    round.
    """
    return Finding(
        kind=kind,
        rule=rule,
        scope="file",
        path=ENGINE,
        value=value,
        limit=limit,
        severity=severity,
        message=f"{rule} is outside its limit",
    )


def test_a_larger_overshoot_beats_an_alphabetically_earlier_rule() -> None:
    small = graded("file.CountDeclFunction", value=11, limit=10)
    large = graded("file.MaxCyclomaticStrict", value=30, limit=10)
    text = render_human(run(small, large), color=ColorMode.OFF)
    assert rules_in(text) == ["file.MaxCyclomaticStrict", "file.CountDeclFunction"]


def test_a_ratioless_finding_sorts_last_even_when_its_rule_comes_first() -> None:
    check = graded("codecheck.CPP_F016", kind="codecheck")
    measured = graded("file.CountDeclFunction", value=12, limit=10)
    text = render_human(run(check, measured), color=ColorMode.OFF)
    assert rules_in(text) == ["file.CountDeclFunction", "codecheck.CPP_F016"]


def test_an_error_beats_a_warning_that_is_further_over_its_limit() -> None:
    warning = graded("file.CountDeclClass", severity="warning", value=15, limit=3)
    error = graded("file.MaxCyclomaticStrict", value=11, limit=10)
    text = render_human(run(warning, error), color=ColorMode.OFF)
    assert rules_in(text) == ["file.MaxCyclomaticStrict", "file.CountDeclClass"]


def declared_classes(value: float, severity: Severity = "warning") -> Finding:
    """A genuine ``max`` breach in ``engine.py``: three classes allowed, ``value`` declared."""
    return graded("file.CountDeclClass", severity=severity, value=value, limit=3)


def test_a_ratchet_finding_does_not_outrank_a_genuine_breach() -> None:
    """A value four times inside its minimum must not be ranked as four times outside it.

    Both findings are warnings, so severity cannot decide the order and the distance rule is
    what is under test: the ``CountDeclClass`` breach is 2.7 times over its maximum, while the
    ratchet finding is not over anything.
    """
    text = render_human(run(comment_ratchet(), declared_classes(8)), color=ColorMode.OFF)
    assert rules_in(text) == ["file.CountDeclClass", "file.RatioCommentToCode"]


def test_a_ratchet_finding_is_never_labelled_with_a_multiple_of_its_limit() -> None:
    text = render_human(run(comment_ratchet()), color=ColorMode.OFF)
    assert "x limit" not in text
    assert "worse than before" in text
    assert "was 0.6" in text


def test_a_min_bound_breach_is_ranked_by_how_far_below_the_limit_it_falls() -> None:
    """0.002 against a minimum of 0.1 is 50 times out; 0.099 is barely out (req 7.3).

    The rule names also run the other way (``class.`` sorts before ``file.``), so a renderer
    that fell back on the tie-break tail, or that ranked by the raw ``value/limit`` ratio,
    would print the near-total violation last.
    """
    nearly_fine = graded("class.RatioCommentToCode", severity="warning", value=0.099, limit=0.1)
    almost_none = graded("file.RatioCommentToCode", severity="warning", value=0.002, limit=0.1)
    text = render_human(run(nearly_fine, almost_none), color=ColorMode.OFF)
    assert rules_in(text) == ["file.RatioCommentToCode", "class.RatioCommentToCode"]


def test_a_file_with_no_comments_at_all_renders_and_ranks_worst() -> None:
    """A comment ratio of exactly 0 against a minimum renders, and is the furthest out.

    This is the shipped default path: ``file.RatioCommentToCode`` has ``min = 0.1``, and a
    file with no comments scores 0, so the distance is a division by that value. Guarding it
    is what keeps ``render_human`` from raising on an ordinary run (req 7.3).
    """
    no_comments = graded("file.RatioCommentToCode", severity="warning", value=0.0, limit=0.1)
    shallow = graded("class.RatioCommentToCode", severity="warning", value=0.05, limit=0.1)

    text = render_human(run(no_comments, shallow), color=ColorMode.OFF)

    assert _limit_distance(no_comments) == math.inf
    assert rules_in(text) == ["file.RatioCommentToCode", "class.RatioCommentToCode"]


def test_a_value_far_under_its_minimum_is_not_tagged_as_zero() -> None:
    """``0.0x limit`` would understate a comment ratio of 0.002 against a minimum of 0.1."""
    starved = graded("file.RatioCommentToCode", severity="warning", value=0.002, limit=0.1)
    text = render_human(run(starved), color=ColorMode.OFF)
    assert "0.0x limit" not in text
    assert "0.02x limit" in text


def test_the_usual_overshoot_keeps_one_decimal() -> None:
    text = render_human(run(comment_ratio(), cyclomatic()), color=ColorMode.OFF)
    assert "0.5x limit" in text
    assert "2.0x limit" in text


def test_a_min_bound_breach_outranks_a_smaller_max_bound_breach() -> None:
    starved = graded("file.RatioCommentToCode", severity="warning", value=0.02, limit=0.1)
    over = declared_classes(4)
    text = render_human(run(over, starved), color=ColorMode.OFF)
    assert rules_in(text) == ["file.RatioCommentToCode", "file.CountDeclClass"]


def headers_in(text: str) -> list[str]:
    """Every group header, in the order the renderer printed it."""
    starts = (" ", "summary", "agent", "nothing")
    return [line for line in text.splitlines() if line and not line.startswith(starts)]


def in_file(path: str, rule: str, value: float, limit: float) -> Finding:
    """An error finding whose only interesting properties are its group and its rule."""
    return Finding(
        kind="threshold",
        rule=rule,
        scope="file",
        path=path,
        value=value,
        limit=limit,
        severity="error",
        message=f"{path} {rule} is outside its limit",
    )


def test_groups_are_ordered_by_path_not_by_how_many_findings_they_hold() -> None:
    """The one finding in ``src/a.py`` still prints before the three in ``src/z.py``."""
    lonely = in_file("src/a.py", "file.CountLineCode", 900, 500)
    crowded = [
        in_file("src/z.py", "file.CountDeclClass", 9, 3),
        in_file("src/z.py", "file.CountDeclFunction", 40, 25),
        in_file("src/z.py", "file.MaxCyclomaticStrict", 12, 10),
    ]
    text = render_human(run(lonely, *crowded), color=ColorMode.OFF)
    assert headers_in(text) == ["src/a.py", "src/z.py"]


def tied(rule: str, line: int | None = 7, message: str = "outside its limit") -> Finding:
    """Findings that differ only in the field under test; the sort tail decides the order."""
    return Finding(
        kind="threshold",
        rule=rule,
        scope="file",
        path=ENGINE,
        line=line,
        value=20,
        limit=10,
        severity="error",
        message=message,
    )


def test_equal_findings_are_ordered_by_rule_name() -> None:
    text = render_human(
        run(tied("file.MaxCyclomaticStrict"), tied("file.CountDeclFunction")), color=ColorMode.OFF
    )
    assert rules_in(text) == ["file.CountDeclFunction", "file.MaxCyclomaticStrict"]


def test_equal_findings_of_one_rule_are_ordered_by_line() -> None:
    rule = "file.MaxCyclomaticStrict"
    text = render_human(run(tied(rule, line=90), tied(rule, line=10)), color=ColorMode.OFF)
    assert re.findall(r"line (\d+)", text) == ["10", "90"]


def test_an_equal_finding_without_a_line_sorts_after_one_with_a_line() -> None:
    rule = "file.MaxCyclomaticStrict"
    text = render_human(run(tied(rule, line=None), tied(rule, line=10)), color=ColorMode.OFF)
    heads = [line for line in text.splitlines() if line.startswith("  error")]
    assert heads[0].endswith("line 10  2.0x limit")
    assert heads[1].endswith("2.0x limit")
    assert "line" not in heads[1]


def test_equal_findings_on_one_line_are_ordered_by_message() -> None:
    rule = "file.MaxCyclomaticStrict"
    text = render_human(
        run(tied(rule, message="second problem"), tied(rule, message="first problem")),
        color=ColorMode.OFF,
    )
    assert text.index("first problem") < text.index("second problem")


def test_groups_are_files_then_architecture_nodes_then_project() -> None:
    text = render_human(blocking_run(), color=ColorMode.OFF)
    assert headers_in(text)[:5] == [
        ENGINE,
        APP,
        TEXT,
        "architecture node Core",
        "project-wide",
    ]


@pytest.mark.parametrize(
    ("value", "limit", "expected"),
    [(20.0, 10.0, 2.0), (None, 10.0, None), (20.0, None, None), (0.05, 0.1, 0.5)],
)
def test_overshoot_ratio(value: float | None, limit: float | None, expected: float | None) -> None:
    finding = cyclomatic().model_copy(update={"value": value, "limit": limit})
    assert overshoot_ratio(finding) == expected


def test_overshoot_ratio_guards_a_zero_limit() -> None:
    finding = cyclomatic().model_copy(update={"value": 3.0, "limit": 0.0})
    ratio = overshoot_ratio(finding)
    assert ratio is not None
    assert ratio == float("inf")


# --- the awkward finding shapes -------------------------------------------------


def test_codecheck_entity_is_read_from_details() -> None:
    text = render_human(run(codecheck()), color=ColorMode.OFF)
    assert "codecheck.CPP_F016  text::trim" in text


def test_a_finding_without_a_line_prints_no_line() -> None:
    text = render_human(run(codecheck()), color=ColorMode.OFF)
    assert "line " not in text


def test_an_architecture_node_is_not_presented_as_a_file() -> None:
    text = render_human(run(coupling()), color=ColorMode.OFF)
    assert "architecture node Core" in text
    assert not text.startswith("Core\n")


def test_a_project_finding_is_grouped_without_a_path() -> None:
    text = render_human(run(project_average()), color=ColorMode.OFF)
    assert text.startswith("project-wide\n")


def test_a_file_scope_entity_is_not_repeated_next_to_its_own_path() -> None:
    text = render_human(run(preexisting_lines()), color=ColorMode.OFF)
    assert f"file.CountLineCode  {APP}" not in text
    assert "  error    file.CountLineCode  line 1" in text


def test_a_pre_existing_finding_is_marked() -> None:
    text = render_human(run(preexisting_lines()), color=ColorMode.OFF)
    assert "pre-existing" in text


def test_a_limit_from_the_baseline_says_so() -> None:
    text = render_human(run(preexisting_lines()), color=ColorMode.OFF)
    assert "limit from baseline" in text


def test_the_before_value_is_shown_when_known() -> None:
    text = render_human(run(cyclomatic()), color=ColorMode.OFF)
    assert "was 9" in text


def test_a_finding_without_a_hint_prints_no_hint_line() -> None:
    text = render_human(run(codecheck()), color=ColorMode.OFF)
    assert "hint:" not in text


# --- summary and agent block ----------------------------------------------------


def test_empty_result_reports_nothing_to_report() -> None:
    assert render_human(run(), color=ColorMode.OFF) == EMPTY_TEXT


def test_summary_names_the_blocking_exit_code_and_its_meaning() -> None:
    text = render_human(blocking_run(), color=ColorMode.OFF)
    assert f"exit {ExitCode.VIOLATIONS.value}: {describe(ExitCode.VIOLATIONS)}" in text


def test_summary_names_the_clean_exit_code_and_its_meaning() -> None:
    text = render_human(run(comment_ratio()), color=ColorMode.OFF)
    assert f"exit {ExitCode.OK.value}: {describe(ExitCode.OK)}" in text


def test_summary_uses_the_singular_for_a_single_error_and_warning() -> None:
    text = render_human(run(cyclomatic(), comment_ratio()), color=ColorMode.OFF)
    assert "summary: 1 error, 1 warning, 0 pre-existing, 1 blocking" in text


def test_the_agent_block_counts_one_blocking_finding_in_the_singular() -> None:
    text = render_human(run(cyclomatic()), Verbosity.NORMAL, ColorMode.OFF, True)
    assert "1 finding blocks this commit" in text


def test_summary_counts_every_finding_even_in_quiet_mode() -> None:
    text = render_human(blocking_run(), Verbosity.QUIET, ColorMode.OFF, True)
    assert "6 errors, 2 warnings, 1 pre-existing, 5 blocking" in text


def test_quiet_mode_hides_warnings_and_pre_existing_findings() -> None:
    text = render_human(blocking_run(), Verbosity.QUIET, ColorMode.OFF, True)
    assert "file.RatioCommentToCode" not in text
    assert "file.CountLineCode" not in text
    assert "codecheck.CPP_F016" not in text
    assert "routine.CyclomaticStrict" in text


def test_agent_block_is_appended_when_the_run_blocks() -> None:
    text = render_human(blocking_run(), Verbosity.NORMAL, ColorMode.OFF, True)
    assert text.endswith("--format json carries the same hints.")
    assert "scitools-hook check --staged" in text
    assert "scitools-hook check --worktree" in text


def test_agent_block_is_absent_when_nothing_blocks() -> None:
    text = render_human(run(comment_ratio()), Verbosity.NORMAL, ColorMode.OFF, True)
    assert "agent instructions" not in text


def test_agent_block_is_absent_when_the_caller_switches_it_off() -> None:
    text = render_human(blocking_run(), Verbosity.NORMAL, ColorMode.OFF, False)
    assert "agent instructions" not in text


def test_agent_block_is_absent_in_quiet_mode() -> None:
    """Requirement 7.8 is the narrower rule: quiet prints the summary and blocking findings."""
    text = render_human(blocking_run(), Verbosity.QUIET, ColorMode.OFF, True)
    assert "agent instructions" not in text


def test_output_is_ascii_so_it_survives_any_console() -> None:
    assert render_human(blocking_run(), Verbosity.NORMAL, ColorMode.ON, True).isascii()


# --- parse errors: the coverage warning of requirement 2.6 ----------------------


def parse_error(path: str, line: int | None, message: str) -> ParseError:
    """One error exactly as ``und analyze`` reported it (models/snapshot.py)."""
    return ParseError(path=Path(path), line=line, message=message)


def cascade() -> list[ParseError]:
    """The measured shape: a star-in-list literal, then the errors it cascades into.

    Understand 6.5.1204 fails on ``["k", *xs]`` and then reports every routine after it as
    absent, so this is not one annotated line -- it is a file the gate never checked past
    line 12. The second error carries no line, which is the other shape ``ParseError`` takes.
    """
    return [
        parse_error(ENGINE, 12, "expected token ']' at token *"),
        parse_error(ENGINE, None, "expected newline at token dedent"),
    ]


def two_files() -> list[ParseError]:
    """Errors in two files, handed over in the wrong path order on purpose.

    The producer lists ``text.cpp`` first, so the snapshot below -- which puts ``engine.py``
    first -- fails if the renderer stops sorting the files it groups.
    """
    return [parse_error(TEXT, 3, "unknown token")] + cascade()


def blocking_run_with(parse_errors: Sequence[ParseError]) -> RunResult:
    """The mixed run of the snapshots, plus the parse errors of the run that produced it."""
    return run(*blocking_run().findings, parse_errors=parse_errors)


PARSE_BLOCK_TEXT: Final = """\
parse errors: these files were NOT fully checked
  Understand could not finish parsing them. Code after a parse error can be missing
  from the analysis, so no rule ran on it: what follows covers only the code that parsed.
  A file in this run's selection that failed to parse is also a blocking analysis.parse_error
  finding below; one outside it -- the interpreter's own standard library, say -- is not.
  src/analysis/engine.py
    line 12: expected token ']' at token *
    expected newline at token dedent
  src/util/text.cpp
    line 3: unknown token"""

CLEAN_PARSE_SUMMARY: Final = (
    "summary: 0 errors, 0 warnings, 0 pre-existing, 0 blocking "
    "| 2 files failed to parse, not fully checked "
    "| exit 0: no blocking violations"
)

CLEAN_PARSE_TEXT: Final = (
    f"{PARSE_BLOCK_TEXT}\n\n"
    "nothing to report in the code that was parsed: it breaks no rule\n"
    f"{CLEAN_PARSE_SUMMARY}"
)


def test_a_run_without_parse_errors_renders_byte_identically() -> None:
    """The whole section is absent when nothing failed to parse, in both shapes of output."""
    assert render_human(blocking_run(), Verbosity.NORMAL, ColorMode.OFF, True) == BLOCKING_TEXT
    assert render_human(run(), color=ColorMode.OFF) == EMPTY_TEXT
    assert "parse" not in BLOCKING_TEXT
    assert "parse" not in EMPTY_TEXT


def test_a_clean_run_with_parse_errors_is_not_reported_as_clean() -> None:
    """The silent-green case: no finding, because most of the file was never analysed."""
    got = render_human(run(parse_errors=two_files()), Verbosity.NORMAL, ColorMode.OFF, True)
    assert got == CLEAN_PARSE_TEXT
    assert "this change breaks no rule" not in got


def test_one_parse_error_names_its_file_its_line_and_its_message() -> None:
    text = render_human(
        run(parse_errors=[parse_error(TEXT, 3, "unknown token")]), color=ColorMode.OFF
    )
    assert "  src/util/text.cpp" in text
    assert "    line 3: unknown token" in text


def test_the_section_says_the_code_was_not_checked() -> None:
    """A reader must not be able to mistake a parse error for a cosmetic note (req 2.6)."""
    text = render_human(run(parse_errors=cascade()), color=ColorMode.OFF)
    assert "NOT fully checked" in text
    assert "no rule ran on it" in text


def test_every_error_of_every_file_is_listed_under_its_own_file() -> None:
    text = render_human(run(parse_errors=two_files()), color=ColorMode.OFF)
    assert PARSE_BLOCK_TEXT in text
    assert text.index(ENGINE) < text.index(TEXT)
    assert text.count("line 12: expected token ']' at token *") == 1
    assert "expected newline at token dedent" in text
    assert "unknown token" in text


def test_an_error_without_a_line_prints_its_message_alone() -> None:
    text = render_human(
        run(parse_errors=[parse_error(TEXT, None, "file not read")]), color=ColorMode.OFF
    )
    assert "    file not read" in text
    assert "line None" not in text


def test_the_summary_counts_the_files_that_failed_to_parse() -> None:
    text = render_human(run(parse_errors=two_files()), color=ColorMode.OFF)
    assert "| 2 files failed to parse, not fully checked |" in text


def test_the_summary_counts_one_file_in_the_singular_however_many_errors_it_has() -> None:
    text = render_human(run(parse_errors=cascade()), color=ColorMode.OFF)
    assert "| 1 file failed to parse, not fully checked |" in text


def test_the_summary_says_nothing_about_parsing_when_everything_parsed() -> None:
    text = render_human(blocking_run(), color=ColorMode.OFF)
    assert "failed to parse" not in text


def test_the_section_precedes_the_findings_and_leaves_them_untouched() -> None:
    """A blocking run gains the section and the summary tag, and nothing else moves."""
    text = render_human(
        run(*blocking_run().findings, parse_errors=two_files()),
        Verbosity.NORMAL,
        ColorMode.OFF,
        True,
    )
    assert text.startswith(f"{PARSE_BLOCK_TEXT}\n\n")
    unchanged = text[len(PARSE_BLOCK_TEXT) + 2 :].replace(
        " | 2 files failed to parse, not fully checked |", " |"
    )
    assert unchanged == BLOCKING_TEXT


def test_quiet_still_reports_the_parse_failures_in_its_summary() -> None:
    """Requirement 7.8 keeps the list out of quiet output; the count is in the line it prints."""
    text = render_human(blocking_run_with(two_files()), Verbosity.QUIET, ColorMode.OFF, True)
    assert "| 2 files failed to parse, not fully checked |" in text
    assert "parse errors: these files were NOT fully checked" not in text


def test_the_section_colours_and_strips_back_to_the_plain_rendering() -> None:
    result = blocking_run_with(two_files())
    colored = render_human(result, Verbosity.NORMAL, ColorMode.ON, True)
    plain = render_human(result, Verbosity.NORMAL, ColorMode.OFF, True)
    assert f"\x1b[1;31m{PARSE_HEADER}\x1b[0m" in colored
    assert ANSI.sub("", colored) == plain
    assert colored.isascii()


def test_quiet_with_no_findings_still_reports_the_parse_failures() -> None:
    """The worst case: nothing to report, and part of the change was never read."""
    text = render_human(run(parse_errors=two_files()), Verbosity.QUIET, ColorMode.OFF, True)
    assert "| 2 files failed to parse, not fully checked |" in text


# --- the run facts beyond the findings: unavailable, ignored, tightened, highest ---


UNAVAILABLE: Final[dict[str, list[str]]] = {
    "Python": ["PercentLackOfCohesion", "CountClassCoupling"],
    "C++": ["Knots"],
}
"""Language -> the metrics Understand has no value for there (``RunResult`` states the key).

Handed over unsorted in both directions, so the snapshot below fails if the renderer prints
whatever order the producer happened to build.
"""

IGNORED: Final[dict[Scope, int]] = {"file": 1, "routine": 8, "class": 3}
"""Also unsorted: the scopes must print in the canonical ``SCOPES`` order, not insertion order."""

TIGHTENED: Final[list[TightenedLimit]] = [
    TightenedLimit(rule="routine.CyclomaticStrict", previous=10, current=8),
    TightenedLimit(rule="file.CountLineCode", previous=500, current=430),
]
"""Two limits an adaptive run lowered, in an order the renderer must not keep (req 8.3)."""

HIGHEST: Final[list[HighestValue]] = [
    HighestValue(scope="routine", metric="CyclomaticStrict", value=20, entity=EVALUATE),
    HighestValue(scope="file", metric="CountLineCode", value=700, entity=file_ref(APP)),
    HighestValue(scope="project", metric="AVG:CyclomaticStrict", value=4.5),
]
"""Ranked by descending value, the order ``analysis/thresholds`` produces and 5.6 wants kept.

The last one has no entity at all: a population metric belongs to no single element.
"""

UNAVAILABLE_BLOCK: Final = """\
unavailable metrics: these limits were NOT evaluated
  Understand reports no value for them in the language named, so nothing was measured
  against their limits and no finding for them can exist, whatever the code does.
  not available for C++: Knots
  not available for Python: CountClassCoupling, PercentLackOfCohesion"""

IGNORED_BLOCK: Final = """\
ignored entities: matched an ignore pattern, so no rule ran on them
  routine 8, class 3, file 1"""

TIGHTENED_BLOCK: Final = """\
tightened limits: the baseline moved down to what this run measured
  file.CountLineCode  500 -> 430
  routine.CyclomaticStrict  10 -> 8"""

HIGHEST_BLOCK: Final = """\
highest values: the largest value per metric, whether or not it breaks a limit
  routine.CyclomaticStrict  20  engine.Engine.evaluate  src/analysis/engine.py  line 42
  file.CountLineCode  700  src/cli/app.py  line 1
  project.AVG:CyclomaticStrict  4.5"""

BLOCKING_SUMMARY: Final = (
    "summary: 6 errors, 2 warnings, 1 pre-existing, 5 blocking | exit 1: blocking violations found"
)

UNAVAILABLE_SEGMENT: Final = "3 metrics unavailable, those limits were not evaluated"

FACTS_SUMMARY: Final = (
    "summary: 6 errors, 2 warnings, 1 pre-existing, 5 blocking "
    f"| {UNAVAILABLE_SEGMENT} "
    "| exit 1: blocking violations found"
)

FACTS_TEXT: Final = f"{UNAVAILABLE_BLOCK}\n\n" + BLOCKING_TEXT.replace(
    BLOCKING_SUMMARY,
    f"{IGNORED_BLOCK}\n\n{TIGHTENED_BLOCK}\n\n{HIGHEST_BLOCK}\n\n{FACTS_SUMMARY}",
)
"""The whole shape: the coverage warning leads, the three notes sit between the findings and
the summary line, and the agent block still has the last word."""


def facts_run() -> RunResult:
    """The mixed run of the snapshots, carrying every fact this section renders."""
    return run(
        *blocking_run().findings,
        ignored_counts=IGNORED,
        unavailable_metrics=UNAVAILABLE,
        tightened=TIGHTENED,
        highest=HIGHEST,
    )


def test_the_blocking_summary_line_is_the_one_the_snapshots_carry() -> None:
    """Guards the two snapshots below, which are built by substituting into ``BLOCKING_TEXT``."""
    assert BLOCKING_SUMMARY in BLOCKING_TEXT
    assert BLOCKING_SUMMARY in QUIET_TEXT


def test_a_run_carrying_none_of_these_facts_renders_byte_identically() -> None:
    """The no-op run: empty -- and zero-valued -- fields must not add a single character."""
    blocking = render_human(blocking_run(), Verbosity.NORMAL, ColorMode.OFF, True, True)
    assert blocking == BLOCKING_TEXT
    assert render_human(run(), Verbosity.NORMAL, ColorMode.OFF, True, True) == EMPTY_TEXT
    nothing = run(
        *blocking_run().findings,
        ignored_counts={"routine": 0, "class": 0},
        unavailable_metrics={"Python": [], "C++": []},
        tightened=[],
        highest=[],
    )
    assert render_human(nothing, Verbosity.NORMAL, ColorMode.OFF, True, True) == BLOCKING_TEXT


def test_every_fact_renders_in_its_place_around_the_findings() -> None:
    assert render_human(facts_run(), Verbosity.NORMAL, ColorMode.OFF, True, True) == FACTS_TEXT


def test_the_facts_render_without_findings_too() -> None:
    """A clean run still owes 3.6, 5.5, 5.6 and 8.3 -- and is where 5.6 matters most."""
    result = run(
        ignored_counts=IGNORED,
        unavailable_metrics=UNAVAILABLE,
        tightened=TIGHTENED,
        highest=HIGHEST,
    )
    text = render_human(result, Verbosity.NORMAL, ColorMode.OFF, True, True)
    assert text == (
        f"{UNAVAILABLE_BLOCK}\n\n{IGNORED_BLOCK}\n\n{TIGHTENED_BLOCK}\n\n{HIGHEST_BLOCK}\n\n"
        "nothing to report: this change breaks no rule that was evaluated\n"
        "summary: 0 errors, 0 warnings, 0 pre-existing, 0 blocking "
        f"| {UNAVAILABLE_SEGMENT} "
        "| exit 0: no blocking violations"
    )


# --- unavailable metrics: the channel that says a limit went unevaluated (req 5.5) ---


def test_the_unavailable_section_names_each_language_and_its_metrics() -> None:
    text = render_human(run(unavailable_metrics=UNAVAILABLE), color=ColorMode.OFF)
    assert UNAVAILABLE_BLOCK in text
    assert "not available for Python: CountClassCoupling, PercentLackOfCohesion" in text
    assert "not available for C++: Knots" in text


def test_the_unavailable_section_says_the_limit_was_never_evaluated() -> None:
    """A reader must not take an unevaluated limit for a limit that passed (req 5.5)."""
    text = render_human(run(unavailable_metrics=UNAVAILABLE), color=ColorMode.OFF)
    assert "NOT evaluated" in text
    assert "no finding for them can exist" in text


def test_the_unavailable_section_leads_the_findings_it_qualifies() -> None:
    result = run(*blocking_run().findings, unavailable_metrics=UNAVAILABLE)
    text = render_human(result, Verbosity.NORMAL, ColorMode.OFF, True)
    assert text.startswith(f"{UNAVAILABLE_BLOCK}\n\n{ENGINE}\n")


def test_the_two_coverage_warnings_lead_with_the_parse_errors_first() -> None:
    result = run(
        *blocking_run().findings, parse_errors=two_files(), unavailable_metrics=UNAVAILABLE
    )
    text = render_human(result, Verbosity.NORMAL, ColorMode.OFF, True)
    assert text.startswith(f"{PARSE_BLOCK_TEXT}\n\n{UNAVAILABLE_BLOCK}\n\n")
    assert (f"| 2 files failed to parse, not fully checked | {UNAVAILABLE_SEGMENT} |") in text


def test_a_language_that_lost_no_metric_prints_no_line() -> None:
    text = render_human(
        run(unavailable_metrics={"Python": ["Knots"], "C++": []}), color=ColorMode.OFF
    )
    assert "not available for Python: Knots" in text
    assert "C++" not in text


def test_the_summary_counts_the_metrics_that_were_never_evaluated() -> None:
    text = render_human(run(unavailable_metrics=UNAVAILABLE), color=ColorMode.OFF)
    assert f"| {UNAVAILABLE_SEGMENT} |" in text


def test_the_summary_counts_one_metric_in_the_singular() -> None:
    text = render_human(
        run(unavailable_metrics={"Python": ["Knots"], "C++": ["Knots"]}), color=ColorMode.OFF
    )
    assert "| 1 metric unavailable, those limits were not evaluated |" in text


def test_the_summary_says_nothing_about_metrics_when_every_one_was_evaluated() -> None:
    text = render_human(blocking_run(), color=ColorMode.OFF)
    assert "unavailable" not in text


def test_quiet_withholds_the_unavailable_list_but_keeps_its_count() -> None:
    """5.5's precedent: 7.8 is the narrower rule, but quiet may not hide an incomplete run."""
    text = render_human(facts_run(), Verbosity.QUIET, ColorMode.OFF, True, True)
    assert f"| {UNAVAILABLE_SEGMENT} |" in text
    assert "unavailable metrics: these limits were NOT evaluated" not in text


def test_the_unavailable_section_is_as_loud_as_the_parse_errors() -> None:
    """Both report coverage that was lost, so both wear the alarm colour."""
    result = facts_run()
    colored = render_human(result, Verbosity.NORMAL, ColorMode.ON, True, True)
    plain = render_human(result, Verbosity.NORMAL, ColorMode.OFF, True, True)
    header = UNAVAILABLE_BLOCK.splitlines()[0]
    assert f"\x1b[1;31m{header}\x1b[0m" in colored
    assert ANSI.sub("", colored) == plain
    assert colored.isascii()


# --- ignored entities (req 3.6), tightened limits (req 8.3), highest values (req 5.6) ---


def test_the_ignored_section_counts_every_scope_that_excluded_something() -> None:
    text = render_human(run(ignored_counts=IGNORED), color=ColorMode.OFF)
    assert IGNORED_BLOCK in text


def test_the_ignored_counts_print_in_scope_order_not_insertion_order() -> None:
    text = render_human(run(ignored_counts=IGNORED), color=ColorMode.OFF)
    assert "  routine 8, class 3, file 1" in text


def test_a_scope_that_ignored_nothing_is_not_listed() -> None:
    text = render_human(run(ignored_counts={"routine": 4, "class": 0}), color=ColorMode.OFF)
    assert "  routine 4" in text
    assert "class" not in text


def test_the_tightened_section_names_each_limit_and_both_values() -> None:
    text = render_human(run(tightened=TIGHTENED), color=ColorMode.OFF)
    assert TIGHTENED_BLOCK in text
    assert "  routine.CyclomaticStrict  10 -> 8" in text


def test_the_highest_values_print_only_when_the_operator_asks() -> None:
    """Requirement 5.6 is conditional on ``--show-highest``; nothing else turns it on."""
    asked = render_human(run(highest=HIGHEST), Verbosity.NORMAL, ColorMode.OFF, True, True)
    unasked = render_human(run(highest=HIGHEST), Verbosity.NORMAL, ColorMode.OFF, True, False)
    assert HIGHEST_BLOCK in asked
    assert "highest" not in unasked
    assert unasked == EMPTY_TEXT


def test_the_highest_value_names_the_entity_that_has_it() -> None:
    text = render_human(run(highest=HIGHEST), Verbosity.NORMAL, ColorMode.OFF, True, True)
    assert "  routine.CyclomaticStrict  20  engine.Engine.evaluate  src/analysis/engine.py" in text
    assert "line 42" in text


def test_a_file_scope_highest_does_not_repeat_its_path() -> None:
    text = render_human(run(highest=HIGHEST), Verbosity.NORMAL, ColorMode.OFF, True, True)
    assert "  file.CountLineCode  700  src/cli/app.py  line 1" in text


def test_a_population_highest_prints_without_an_entity() -> None:
    text = render_human(run(highest=HIGHEST), Verbosity.NORMAL, ColorMode.OFF, True, True)
    assert text.endswith("  project.AVG:CyclomaticStrict  4.5\n\n" + EMPTY_TEXT)


def test_quiet_prints_none_of_the_three_notes() -> None:
    """7.8 prints the summary and the blocking findings; the notes wait for a normal run."""
    text = render_human(facts_run(), Verbosity.QUIET, ColorMode.OFF, True, True)
    assert text == QUIET_TEXT.replace(BLOCKING_SUMMARY, FACTS_SUMMARY)
    assert "ignored entities" not in text
    assert "tightened limits" not in text
    assert "highest values" not in text


# --- the no-findings claim must not outrun what the run actually checked -----------


def test_a_clean_run_with_an_unevaluated_limit_does_not_claim_it_breaks_no_rule() -> None:
    """Since 2.4 the Gate can drop a shipped threshold and exit 0; 'breaks no rule' would lie."""
    text = render_human(run(unavailable_metrics=UNAVAILABLE), color=ColorMode.OFF)
    assert "this change breaks no rule that was evaluated" in text
    assert "this change breaks no rule\n" not in text


def test_the_two_coverage_caveats_compose_in_the_nothing_line() -> None:
    """A parse error narrows what was READ; an unavailable metric narrows which rules RAN."""
    text = render_human(
        run(parse_errors=two_files(), unavailable_metrics=UNAVAILABLE), color=ColorMode.OFF
    )
    assert (
        "nothing to report in the code that was parsed: it breaks no rule that was evaluated"
        in text
    )


def test_quiet_still_refuses_the_unqualified_claim_when_a_limit_went_unevaluated() -> None:
    """Quiet prints the nothing-line, so the mode most likely to be skimmed must not lie."""
    text = render_human(run(unavailable_metrics=UNAVAILABLE), Verbosity.QUIET, ColorMode.OFF, True)
    assert "that was evaluated" in text


def test_a_run_that_evaluated_everything_makes_the_plain_claim() -> None:
    """The hedge must not leak into a run with full coverage."""
    text = render_human(run(), color=ColorMode.OFF)
    assert "nothing to report: this change breaks no rule" in text
    assert "that was evaluated" not in text
