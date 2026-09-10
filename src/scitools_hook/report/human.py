"""The human view of a run: findings grouped by file, then one summary line (req 7.3).

The renderer is a pure function of a :class:`~scitools_hook.models.findings.RunResult` and
five decisions the caller has already made -- how much to print, whether to colour, whether
the run should end with instructions for a coding agent, and, in :class:`ReportSettings`,
whether the operator asked for the highest values (req 5.6) and which lean-code rules are
switched on (lean-code req 7.6). It reads neither ``sys.stdout`` nor the environment:
requirement 7.6 (no colour on a non-interactive terminal unless forced) is a decision about
the terminal, which belongs to the CLI, so the CLI computes it with :func:`resolve_color` and
passes the answer in. That keeps every rendering test free of monkeypatching and makes the
output byte-deterministic.

Layout, per finding, is a head line and one or two indented lines::

    src/analysis/engine.py
      error    routine.CyclomaticStrict  engine.Engine.evaluate  line 42  2.0x limit, was 9
        routine engine.Engine.evaluate CyclomaticStrict is 20, over the maximum of 10
        hint: split the decision groups into named routines

Decisions worth knowing about, all of them visible in ``tests/report/test_human.py``:

* **Grouping.** Findings group by ``path``, but a path is not always a file: an architecture
  finding carries a node path (task 4.3) and a population threshold carries nothing at all.
  Those groups get their own headers (``architecture node Core``, ``project-wide``) so the
  output never presents a node or the whole project as a file. Files sort first, by path,
  then architecture nodes, then the project group.
* **Order inside a group.** Errors before warnings, then by how far outside its limit the
  value is (:func:`_limit_distance`), worst first. The bound is read from the side of the
  limit the value sits on, the way ``analysis.classify`` reads it, so a ``min``-bound breach
  is measured as ``limit/value``: ``RatioCommentToCode`` at 0.002 against a minimum of 0.1 is
  50 times out and sorts above one at 0.099. A **ratchet** finding has no distance at all --
  it is reported because the value got *worse*, not because it left a limit, and its value is
  usually well inside one -- so it joins the findings that cannot be measured against a limit
  (the structural rules) and is never labelled with a multiple of a limit it does not break.
  Ties fall through a stable tail: rule name, then line (a missing line last), then message.
* **Entity.** The qualified name comes from ``details["entity"]`` first, because a CodeCheck
  finding leaves ``Finding.entity`` empty (task 4.7); it is dropped when it merely repeats
  the group's own path, as it does for every file-scope finding.
* **Parse errors** (req 2.6) print *first*, in their own section, whenever the run carries
  any. They are not a cosmetic note: Understand analyses a file only as far as it parses, so
  the entities after an error are missing from the database and no rule ran on them -- a run
  over a file that failed to parse can report a clean bill of health for code it never saw.
  Since task 11.11 a file *in the selection* that failed to parse is a blocking
  ``analysis.parse_error`` finding as well, so it appears twice: here, with every error
  Understand reported in it, and below with the one thing to do about it. That is not a
  duplicate -- this section is the run's coverage statement and includes the files no commit
  can fix, while the finding is the verdict on the file this change is asking to add.
  The section therefore leads the output it qualifies, the summary line carries the count of
  affected files so the tail of a long run says it too, and the no-findings line stops
  claiming that "this change breaks no rule" when part of the change was never read. The
  files are listed sorted by path, each error under its own file in the order the analysis
  reported it -- the first error is the cause and the rest are its cascade -- and none are
  dropped, because 2.6 asks for the errors, not for a sample of them.
* **Unavailable metrics** (req 5.5) print second, in the same alarm colour, because they are
  the same kind of news: a metric Understand does not provide for a language is a limit that
  was never evaluated, so the findings below cover less than the configured rules do. Since
  task 2.4 a shipped default whose metric the configured language lacks is dropped and
  reported instead of stopping the run, which leaves this section as the only channel saying
  so -- a run can now check fewer rules than the operator configured and still exit 0. Each
  line names the language first (``not available for Python: ...``), because the field is
  keyed by language and a bare mapping of names to names reads either way round.
* **Ignored entities** (req 3.6), **tightened limits** (req 8.3) and **highest values**
  (req 5.6) print after the findings and before the summary: they are notes about the run
  rather than warnings about it, so they sit next to the line that closes it. A scope that
  ignored nothing contributes no count, a run that tightened nothing prints no section, and
  the highest values print only when the operator asked -- 5.6 is the one requirement here
  that is conditional on a flag, so the caller passes the answer in exactly as it does for
  the agent block. Highest values keep the producer's ranking (descending value); everything
  else is sorted here, because no order in the data means anything.
* **Quiet** (req 7.8) prints the summary and the blocking findings, and nothing else -- not
  the warnings, not the pre-existing findings, not the agent block of requirement 10.4, and
  none of the four sections above, whose narrower rule 7.8 is. The summary line still names
  how many files failed to parse and how many metrics went unevaluated, so quiet cannot hide
  that the run checked less than it was asked to; the lists themselves need a run without
  ``--quiet``. The other three carry no such claim -- an ignore pattern is the operator's own
  instruction, tightening only ever narrows a limit, and a highest value is not a statement
  about coverage at all -- so they leave no trace in the quiet line. A caller that wants any
  of this must not ask for quiet.
* **Counts** in the summary are taken from ``result.findings`` rather than from the
  ``*_count`` fields, so the line can never disagree with the findings printed above it;
  ``blocking_count`` is a validated mirror of the same findings and decides the exit code.
* **The net line** closes the run, under the summary, whenever the check had a before side
  to subtract from (lean-code req 7.1, 7.3). It is one line for the whole change rather than
  a finding about a piece of it, and the ``agent-rules`` snippet promises an agent this exact
  shape, so the two ship together: ``net: +12 lloc (+30 lines) over 7 routines``. A run with
  no before side (``--all``) carries no delta and prints **no line at all** -- printing a zero
  there would report a measurement of a change nothing looked at (7.4). It survives ``--quiet``
  because requirement 7.1 puts the figure in the summary and quiet keeps the summary.
* **The lean-already line** follows it when, and only when, all three of requirement 7.6's
  conditions hold: a lean rule is switched on, no lean finding was raised, and the delta is at
  or below zero. Each one is what stops a congratulation from being false -- on a repository
  where no lean rule looked, on a change that collected six lean findings, on a change that
  added two hundred lines -- so they are three separate guards in :func:`_nothing_to_cut`
  rather than one boolean expression, and ``tests/report/test_lean_report.py`` fails a run for
  each of them on its own.
* **The worked example** of a lean finding (req 8.2) prints under its hint at
  ``Verbosity.VERBOSE`` and nowhere else. It is the five-line before/after block the hint
  catalogue ships, attached to the finding by the check pipeline under ``details["example"]``;
  it teaches the shorter form to an agent that asked for detail, and it is five lines per
  finding that the default report and ``--quiet`` are better without.
* **Colour** is emitted as SGR escapes around already-padded text, so stripping the escapes
  gives exactly the uncoloured rendering. The design's ``rich`` console lives in the CLI; a
  renderer that returns a string must not wrap or re-flow the text it is handed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, NamedTuple

from scitools_hook.config.metric_names import SCOPES, Scope
from scitools_hook.config.models import LeanRules, Severity
from scitools_hook.exit_codes import ExitCode, describe
from scitools_hook.models.change import NetDelta
from scitools_hook.models.findings import (
    Finding,
    HighestValue,
    RunResult,
    TightenedLimit,
    UnderstandSarif,
    structure_rule,
)
from scitools_hook.models.snapshot import ParseError
from scitools_hook.report.lean_examples import LEAN_RULES


class Verbosity(StrEnum):
    """How much of a run to print; ``QUIET`` is requirement 7.8, ``VERBOSE`` lean-code 8.2.

    ``VERBOSE`` is ``NORMAL`` plus the worked example under each lean finding's hint. It adds
    detail to a finding rather than adding findings, so every other section reads it exactly
    as it reads ``NORMAL``: only ``QUIET`` narrows what is printed.
    """

    NORMAL = "normal"
    QUIET = "quiet"
    VERBOSE = "verbose"


class ColorMode(StrEnum):
    """The colour decision, already made: the renderer never re-decides it (req 7.6)."""

    OFF = "off"
    ON = "on"


NOTHING_TO_REPORT: Final = "nothing to report: this change breaks no rule"
NOTHING_PARSED_TO_REPORT: Final = "nothing to report in the code that was parsed: it breaks no rule"
UNEVALUATED_SUFFIX: Final = " that was evaluated"
"""Narrows the no-findings claim when a limit never ran (req 5.5).

It is a suffix rather than a fourth constant because the two caveats are independent: a
parse error narrows *what was read* (req 2.6), an unavailable metric narrows *which rules
ran* (req 5.5). Composing them keeps all four combinations honest.
"""
"""What the same line may say when part of the change never reached the rules (req 2.6)."""

PARSE_HEADER: Final = "parse errors: these files were NOT fully checked"
_PARSE_LEAD: Final[tuple[str, ...]] = (
    "  Understand could not finish parsing them. Code after a parse error can be missing",
    "  from the analysis, so no rule ran on it: what follows covers only the code that parsed.",
    "  A file in this run's selection that failed to parse is also a blocking analysis.parse_error",
    "  finding below; one outside it -- the interpreter's own standard library, say -- is not.",
)
UNAVAILABLE_HEADER: Final = "unavailable metrics: these limits were NOT evaluated"
_UNAVAILABLE_LEAD: Final[tuple[str, ...]] = (
    "  Understand reports no value for them in the language named, so nothing was measured",
    "  against their limits and no finding for them can exist, whatever the code does.",
)
IGNORED_HEADER: Final = "ignored entities: matched an ignore pattern, so no rule ran on them"
TIGHTENED_HEADER: Final = "tightened limits: the baseline moved down to what this run measured"
ACCURACY_HEADER: Final = "analysis accuracy: how much of each side Understand resolved"
COMPANION_HEADER: Final = "Understand's own SARIF: uploaded beside the Gate's, never merged into it"
HIGHEST_HEADER: Final = (
    "highest values: the largest value per metric, whether or not it breaks a limit"
)
PROJECT_HEADER: Final = "project-wide"
ARCH_HEADER_PREFIX: Final = "architecture node "
AGENT_HEADER: Final = "agent instructions"

EXAMPLE_LABEL: Final = "example: "
"""What opens the worked example's first line, so a reader sees where the block starts."""

LEAN_ALREADY_PREFIX: Final = "lean already: nothing to cut, net "
"""Requirement 7.6's one line, and the whole claim it makes: the rules looked and found none.

It repeats the figure the net line above it already carries, deliberately: this line is the
answer to "is there anything left to remove", and an answer that has to be read together with
the line above it is one an agent can quote without the number that qualifies it.
"""

_LEAN_RULE_NAMES: Final[frozenset[str]] = frozenset(structure_rule(name) for name in LEAN_RULES)
"""The rule names whose presence means a lean rule *did* find something (req 7.6).

Derived from :data:`~scitools_hook.report.lean_examples.LEAN_RULES`, which is the report
layer's single naming of the family, so a tenth rule joins this set by being added there
rather than by being remembered here.
"""

_AGENT_LINES: Final[tuple[str, ...]] = (
    "  Re-run while editing:  scitools-hook check --worktree",
    "  Re-run before commit:  scitools-hook check --staged",
    '  Each finding\'s "hint:" line says what to change; --format json carries the same hints.',
)

_SEVERITY_WIDTH: Final = 7
"""Width of the severity column: ``warning`` is the longest value ``Severity`` takes."""

_BOLD: Final = "\x1b[1m"
_RED: Final = "\x1b[1;31m"
_YELLOW: Final = "\x1b[33m"
_DIM: Final = "\x1b[2m"
_RESET: Final = "\x1b[0m"

_SEVERITY_SGR: Final[dict[Severity, str]] = {"error": _RED, "warning": _YELLOW}
_SEVERITY_RANK: Final[dict[Severity, int]] = {"error": 0, "warning": 1}

_GroupKind = Literal["file", "arch", "project"]
_GROUP_ORDER: Final[dict[_GroupKind, int]] = {"file": 0, "arch": 1, "project": 2}


def resolve_color(force: bool | None, *, is_tty: bool, no_color: bool) -> ColorMode:
    """Decide whether to colour, from facts only the caller can see (req 7.6).

    ``force`` is the CLI's ``--color/--no-color`` flag: ``True`` forces colour even when the
    output is piped or ``NO_COLOR`` is set, ``False`` forbids it, ``None`` leaves the decision
    to the terminal -- colour only for an interactive terminal without ``NO_COLOR``.
    """
    if force is not None:
        return ColorMode.ON if force else ColorMode.OFF
    return ColorMode.ON if is_tty and not no_color else ColorMode.OFF


def overshoot_ratio(finding: Finding) -> float | None:
    """The displayed ratio of a value to its limit, ``value / limit``; ``None`` when unknown.

    This is the number the ``2.0x limit`` tag shows, so it stays the plain ratio in both
    directions: 0.05 against a minimum of 0.1 reads as ``0.5x limit``. Ordering does *not* use
    it -- see :func:`_limit_distance`, which measures a ``min`` bound the other way round.

    A finding without a value or without a limit (every structural rule) has no ratio. A zero
    limit cannot be divided by: any positive value over it is infinitely far out.
    """
    if finding.value is None or finding.limit is None:
        return None
    if finding.limit == 0:
        return math.inf if finding.value > 0 else 1.0
    return finding.value / finding.limit


def _limit_distance(finding: Finding) -> float | None:
    """How far outside its limit a value is, in whichever direction that limit runs.

    Requirement 7.3 orders findings by how far over the limit they are, and a ``min`` bound is
    broken by falling far *below* it: 0.002 against a minimum of 0.1 is 50 times out, while the
    raw ``value/limit`` ratio (0.02) would sort the worst breach last. Which bound broke is read
    from the side of the limit the value sits on, exactly as ``analysis.classify._bound_of``
    reads it, so nothing new has to be recorded on :class:`Finding`.

    A ratchet finding returns ``None``: it exists because the value got worse (req 4.4), not
    because it left a limit, so measuring it against one would rank a healthy metric above a
    real breach. It sorts with the findings that have no measurable distance and is ordered
    among them by the stable tail. Its regression is deliberately not mixed into this ranking
    -- "twice as far past the limit" and "worsened by half" are not the same scale.
    """
    if finding.kind == "ratchet" or finding.value is None or finding.limit is None:
        return None
    if finding.value < finding.limit:
        return math.inf if finding.value <= 0 else finding.limit / finding.value
    return overshoot_ratio(finding)


@dataclass(frozen=True, slots=True)
class ReportSettings:
    """What the effective configuration, rather than the command line, tells the renderer.

    These two answers arrive in one object instead of as parameters of their own, and the
    reason is the finding the Gate raised on this file when the second one landed:
    :func:`render_human` reached six parameters against ``routine.CountParams``, whose own hint
    asks for the parameters that always travel together to become one object. These do travel
    together -- both are read from ``Settings`` at the one call site that has one, while
    ``verbosity``, ``color`` and ``show_agent_block`` are decided from the terminal and the
    command -- so the line is drawn where the answers come from, not wherever the count
    happened to fit.
    """

    show_highest: bool = False
    """Requirement 5.6: the operator asked for the highest value per metric."""

    lean: LeanRules | None = None
    """The effective ``[lean]`` section, for requirement 7.6's "nothing to cut" line.

    Passed whole rather than as a "lean rules are on" boolean so that a caller cannot answer
    the question narrowly by mistake: ``LeanRules.wants_references`` leaves three of the nine
    switches out on purpose (see :func:`_lean_is_on`), and 7.6 asks about all nine. ``None``
    is a caller with no lean configuration to speak for, and prints no such line.
    """


UNCONFIGURED: Final = ReportSettings()
"""What a caller with no configuration to speak for passes: nothing asked for, nothing on.

A module-level singleton because it is the default of :func:`render_human`, and building one
in the signature would build it at import time under a name nobody can see -- the shape
``ruff``'s ``B008`` refuses. It is frozen, so the one instance is safe to share.
"""


def render_human(
    result: RunResult,
    verbosity: Verbosity = Verbosity.NORMAL,
    color: ColorMode = ColorMode.OFF,
    show_agent_block: bool = True,
    settings: ReportSettings = UNCONFIGURED,
) -> str:
    """Render ``result`` as text, without a trailing newline.

    Covers requirements 7.3, 7.6, 7.8 and 10.4, the parse errors of 2.6, and the run facts
    3.6, 5.5, 8.3 and -- when ``settings`` says the operator asked for them -- 5.6. The
    lean-code family adds the net line (its 7.1, 7.3), the lean-already line (7.6) and, at
    ``Verbosity.VERBOSE``, the worked example under each hint (8.2).
    """
    style = _Style(color)
    sections = []
    if _shows_parse_errors(result, verbosity):
        sections.append(_parse_error_section(result.parse_errors, style))
    if _shows_unavailable(result, verbosity):
        sections.append(_unavailable_section(result.unavailable_metrics, style))
    sections.extend(
        _render_group(group, verbosity, style) for group in _groups(_visible(result, verbosity))
    )
    sections.extend(_note_sections(result, verbosity, settings.show_highest, style))
    sections.append(_closing(result, settings.lean, style))
    if _wants_agent_block(result, verbosity, show_agent_block):
        sections.append(_agent_block(result, style))
    return "\n\n".join(sections)


@dataclass(frozen=True, slots=True)
class _Style:
    """Colour decisions applied to already-laid-out text, or not applied at all."""

    color: ColorMode

    def _wrap(self, text: str, sgr: str) -> str:
        # Colour only on an explicit ON: anything else stays plain, because emitting escapes
        # into a pipe is the failure requirement 7.6 exists to prevent.
        return f"{sgr}{text}{_RESET}" if self.color is ColorMode.ON else text

    def strong(self, text: str) -> str:
        """A header or the summary line."""
        return self._wrap(text, _BOLD)

    def severity(self, severity: Severity) -> str:
        """The padded severity column, coloured by its level."""
        return self._wrap(f"{severity:<{_SEVERITY_WIDTH}}", _SEVERITY_SGR[severity])

    def hint(self, text: str) -> str:
        """The remediation line, kept visually behind the finding itself."""
        return self._wrap(text, _DIM)

    def alarm(self, text: str) -> str:
        """A header that reports lost coverage: as loud as an error, because it hides errors."""
        return self._wrap(text, _RED)


class _Group(NamedTuple):
    """One header and the findings under it, already ordered."""

    kind: _GroupKind
    path: str
    findings: tuple[Finding, ...]

    @property
    def header(self) -> str:
        """What the group is: a file path, a named architecture node, or the project."""
        if self.kind == "arch":
            return f"{ARCH_HEADER_PREFIX}{self.path}"
        if self.kind == "project":
            return PROJECT_HEADER
        return self.path


def _visible(result: RunResult, verbosity: Verbosity) -> list[Finding]:
    """The findings this verbosity prints: everything, or only what blocks (req 7.8)."""
    if verbosity is Verbosity.QUIET:
        return [finding for finding in result.findings if finding.blocking]
    return list(result.findings)


def _group_key(finding: Finding) -> tuple[_GroupKind, str]:
    """Which group a finding belongs to; an architecture path is never shown as a file."""
    if finding.scope == "arch":
        return ("arch", finding.path)
    if not finding.path:
        return ("project", "")
    return ("file", finding.path)


def _groups(findings: Iterable[Finding]) -> list[_Group]:
    """Group by path, files first, then architecture nodes, then the project group."""
    buckets: dict[tuple[_GroupKind, str], list[Finding]] = {}
    for finding in findings:
        buckets.setdefault(_group_key(finding), []).append(finding)
    keys = sorted(buckets, key=lambda key: (_GROUP_ORDER[key[0]], key[1]))
    return [
        _Group(kind, path, tuple(sorted(buckets[(kind, path)], key=_sort_key)))
        for kind, path in keys
    ]


def _sort_key(finding: Finding) -> tuple[int, int, float, str, int, int, str]:
    """Severity, then distance from the limit descending, then a stable tail."""
    distance = _limit_distance(finding)
    return (
        _SEVERITY_RANK[finding.severity],
        1 if distance is None else 0,
        -(distance if distance is not None else 0.0),
        finding.rule,
        1 if finding.line is None else 0,
        finding.line if finding.line is not None else 0,
        finding.message,
    )


def _render_group(group: _Group, verbosity: Verbosity, style: _Style) -> str:
    """The group header followed by each of its findings."""
    lines = [style.strong(group.header)]
    for finding in group.findings:
        lines.extend(_finding_lines(finding, group.path, verbosity, style))
    return "\n".join(lines)


def _finding_lines(finding: Finding, path: str, verbosity: Verbosity, style: _Style) -> list[str]:
    """One finding: the head line, its message, its hint, and -- verbose -- its example."""
    lines = ["  " + "  ".join(_head_parts(finding, path, style)), f"    {finding.message}"]
    if finding.hint:
        lines.append(style.hint(f"    hint: {finding.hint}"))
    lines.extend(_example_lines(finding, verbosity, style))
    return lines


def _example_lines(finding: Finding, verbosity: Verbosity, style: _Style) -> list[str]:
    """The worked before-and-after example under the hint, verbose only (lean-code req 8.2).

    The example is the catalogue's text, attached to the finding by the check pipeline, and it
    is printed **whole**: its first line names the location and the tag, and the block under it
    is the shorter form. Quoting the head alone would leave an agent the description of an edit
    it was meant to be shown, which is the failure task 2.4's review recorded on the hints.

    ``details`` is a free-form bag, so a value that is not text is skipped rather than
    rendered, and an example an operator emptied through ``[hints]`` prints nothing at all --
    silencing one is a thing to be able to do, and a bare ``example:`` heading over nothing
    would not be silence.

    The surrounding newlines go before anything else, because requirement 8.6 lets an operator
    write the replacement in ``[hints]`` and TOML's multi-line string opens and closes on its
    own line. Left in, the leading one would print ``example:`` over an empty line and the
    trailing one would end the finding with a blank line, which in this layout reads as the
    end of the group.
    """
    if verbosity is not Verbosity.VERBOSE:
        return []
    example = finding.details.get("example")
    if not isinstance(example, str):
        return []
    body = example.strip("\n")
    if not body:
        return []
    head, *rest = body.split("\n")
    return [
        style.hint(f"    {EXAMPLE_LABEL}{head}"),
        *(_example_line(line, style) for line in rest),
    ]


def _example_line(text: str, style: _Style) -> str:
    """One continuation line of an example, indented under the finding; a blank stays blank.

    An empty line wrapped in SGR escapes renders as an empty line and greps as one that is
    not, so the example's own paragraph break is emitted as nothing rather than as colour
    around nothing.
    """
    return style.hint(f"    {text}") if text else ""


def _head_parts(finding: Finding, path: str, style: _Style) -> list[str]:
    """Severity, rule, entity, line and tags -- each part omitted when it says nothing."""
    parts = [style.severity(finding.severity), finding.rule]
    label = _entity_label(finding, path)
    if label:
        parts.append(label)
    if finding.line is not None:
        parts.append(f"line {finding.line}")
    tags = _tags(finding)
    if tags:
        parts.append(", ".join(tags))
    return parts


def _entity_label(finding: Finding, path: str) -> str:
    """The entity's qualified name (req 7.1), from ``details`` first for CodeCheck rows."""
    detail = finding.details.get("entity")
    label = detail if isinstance(detail, str) else ""
    if not label and finding.entity is not None:
        label = finding.entity.key.longname
    return "" if label == path else label


def _tags(finding: Finding) -> list[str]:
    """The facts that qualify a finding: how far out, where it came from, whether it is old."""
    tags = _distance_tag(finding)
    if finding.before is not None:
        tags.append(f"was {_number(finding.before)}")
    if finding.preexisting:
        tags.append("pre-existing")
    if finding.limit_source == "baseline":
        tags.append("limit from baseline")
    return tags


def _distance_tag(finding: Finding) -> list[str]:
    """How far out the value is -- but never as a multiple of a limit it does not break.

    A ratchet finding is reported for getting worse, not for leaving its limit (req 4.4);
    ``4.0x limit`` on a value four times *better* than the minimum it must clear would be a
    plain lie, so it says what actually happened and lets the ``was N`` tag carry the number.
    """
    if finding.kind == "ratchet":
        return ["worse than before"] if finding.before is not None else []
    ratio = overshoot_ratio(finding)
    return [] if ratio is None else [_ratio_tag(ratio)]


def _ratio_tag(ratio: float) -> str:
    """``2.0x limit`` -- but never rounded to ``0.0x`` for a value far under a minimum.

    One decimal reads best for the overshoots that dominate the output; a comment ratio of
    0.002 against a minimum of 0.1 would collapse to ``0.0x limit`` under it and understate a
    near-total violation, so anything below a tenth of its limit switches to two significant
    digits (``0.02x limit``). The ordering of such a finding is decided by
    :func:`_limit_distance`, not by this text.
    """
    if math.isinf(ratio):
        return "limit is zero"
    return f"{ratio:.1f}x limit" if ratio >= 0.1 else f"{ratio:.2g}x limit"


def _shows_parse_errors(result: RunResult, verbosity: Verbosity) -> bool:
    """The section belongs to any run that has parse errors, except a quiet one (req 7.8)."""
    return bool(result.parse_errors) and verbosity is not Verbosity.QUIET


def _parse_error_section(parse_errors: Sequence[ParseError], style: _Style) -> str:
    """The files Understand could not finish, and every error it reported in them (req 2.6).

    The wording claims only what was measured: the analysis stops where the parse stops, so
    the code after an error is absent from the database and no rule was evaluated on it. It
    does not guess which entities were lost -- the gate cannot know -- and it does not soften
    it either, because a reader who takes this for a formatting nit will read the findings
    below as coverage of a file that was never read.
    """
    lines = [style.alarm(PARSE_HEADER)] + list(_PARSE_LEAD)
    for path, errors in _parse_errors_by_file(parse_errors):
        lines.append(f"  {path}")
        lines.extend(f"    {_parse_error_line(error)}" for error in errors)
    return "\n".join(lines)


def _parse_errors_by_file(parse_errors: Sequence[ParseError]) -> list[tuple[str, list[ParseError]]]:
    """Group by file, files sorted by path, errors kept in the order the analysis reported.

    Sorting the files matches the findings section and survives a producer that lists them in
    whatever order Understand printed; the errors inside a file are *not* re-ordered, because
    the first one is the cause and the rest are the cascade it set off.
    """
    buckets: dict[str, list[ParseError]] = {}
    for error in parse_errors:
        buckets.setdefault(error.path.as_posix(), []).append(error)
    return [(path, buckets[path]) for path in sorted(buckets)]


def _parse_error_line(error: ParseError) -> str:
    """One error, with its line when the analysis gave one."""
    if error.line is None:
        return error.message
    return f"line {error.line}: {error.message}"


def _shows_unavailable(result: RunResult, verbosity: Verbosity) -> bool:
    """The section belongs to any run that skipped a metric, except a quiet one (req 7.8)."""
    return bool(_unavailable_metrics(result)) and verbosity is not Verbosity.QUIET


def _unavailable_metrics(result: RunResult) -> set[str]:
    """Every metric that went unevaluated for at least one language (req 5.5).

    Counted per metric rather than per (language, metric) pair, because the question the
    number answers is "how many of my limits were not checked", and a metric missing in two
    languages is still one unchecked limit. A language whose list is empty contributes
    nothing, so a mapping full of empty lists renders no section and adds no summary segment.
    """
    return {metric for metrics in result.unavailable_metrics.values() for metric in metrics}


def _unavailable_section(unavailable: Mapping[str, Sequence[str]], style: _Style) -> str:
    """Which metrics Understand has no value for, per language (req 5.5).

    This is a coverage warning, not a note: a threshold whose metric is unavailable is never
    evaluated, so it can never produce a finding however bad the code is. Since task 2.4 a
    shipped default whose metric the configured language lacks is dropped and reported rather
    than fatal, which makes this the only place a human is told that the gate checked less
    than the configuration asked for -- hence the alarm colour and the leading position, both
    borrowed from the parse-error section for the same reason.

    ``unavailable`` is keyed by language, which no reader can tell from a mapping of names to
    names, so each line spells the direction out instead of printing a bare pair.
    """
    lines = [style.alarm(UNAVAILABLE_HEADER)] + list(_UNAVAILABLE_LEAD)
    for language in sorted(unavailable):
        metrics = sorted(unavailable[language])
        if metrics:
            lines.append(f"  not available for {language}: {', '.join(metrics)}")
    return "\n".join(lines)


def _note_sections(
    result: RunResult, verbosity: Verbosity, show_highest: bool, style: _Style
) -> list[str]:
    """What the run did besides finding things; none of it survives quiet mode (req 7.8)."""
    if verbosity is Verbosity.QUIET:
        return []
    sections = []
    if _ignored_parts(result.ignored_counts):
        sections.append(_ignored_section(result.ignored_counts, style))
    if result.tightened:
        sections.append(_tightened_section(result.tightened, style))
    if show_highest and result.highest:
        sections.append(_highest_section(result.highest, style))
    if result.accuracy:
        sections.append(_accuracy_section(result.accuracy, style))
    if result.understand_sarif:
        sections.append(_companion_section(result.understand_sarif, style))
    return sections


def _accuracy_section(figures: Mapping[str, float], style: _Style) -> str:
    """What share of each side's files Understand parsed with no error and no warning (7.1).

    A run fact rather than a finding: it says how much to trust everything above it, and a
    figure alone breaks no rule. The finding, where a floor is configured, is
    ``analysis.accuracy`` and appears with the others.
    """
    lines = [style.strong(ACCURACY_HEADER)]
    for side, found in sorted(figures.items()):
        lines.append(f"  {side}  {found:.0%}")
    return "\n".join(lines)


def _companion_section(companions: Sequence[UnderstandSarif], style: _Style) -> str:
    """Where Understand's own SARIF documents went, and why any of them did not (2.1, 2.4).

    Every entry says one of three things: written *here*, prepared but not asked for, or
    absent with the reason. None of them is a finding and none of them moves the exit code --
    they are what the run did with the files, reported so the operator can upload them.
    """
    lines = [style.strong(COMPANION_HEADER)]
    for one in companions:
        lines.append(f"  {one.kind}  {_companion_state(one)}")
    return "\n".join(lines)


def _companion_state(one: UnderstandSarif) -> str:
    """One companion's outcome, in the order a reader cares about: where, or why not."""
    if one.written is not None:
        return one.written
    if one.problem:
        return f"not written: {one.problem}"
    return f"prepared at {one.source} (pass --sarif PATH to write it beside the Gate's)"


def _ignored_section(counts: Mapping[Scope, int], style: _Style) -> str:
    """How many entities the ignore lists excluded from every rule, per scope (req 3.6)."""
    return "\n".join([style.strong(IGNORED_HEADER), f"  {', '.join(_ignored_parts(counts))}"])


def _ignored_parts(counts: Mapping[Scope, int]) -> list[str]:
    """``routine 8`` for each scope that excluded something, in the canonical scope order.

    A scope that ignored nothing says nothing, so a zero -- which the producer omits but a
    hand-assembled result can still carry -- prints no part, and a mapping of nothing but
    zeros prints no section at all.
    """
    return [f"{scope} {counts[scope]}" for scope in SCOPES if counts.get(scope, 0) > 0]


def _tightened_section(tightened: Sequence[TightenedLimit], style: _Style) -> str:
    """Which limits this run lowered, and from what to what (req 8.3).

    Sorted by rule name: the baseline is a mapping, so the order it yields entries in carries
    no meaning that ranking them by name would destroy.
    """
    lines = [style.strong(TIGHTENED_HEADER)]
    for limit in sorted(tightened, key=lambda entry: entry.rule):
        lines.append(f"  {limit.rule}  {_number(limit.previous)} -> {_number(limit.current)}")
    return "\n".join(lines)


def _highest_section(highest: Sequence[HighestValue], style: _Style) -> str:
    """The largest value per metric and the entity holding it, when asked for (req 5.6).

    The producer's order is kept, because it *is* a ranking -- ``analysis.thresholds`` sorts
    by descending value -- and re-sorting it here would throw away the one thing that tells a
    reader where to look first.
    """
    lines = [style.strong(HIGHEST_HEADER)]
    lines.extend(f"  {'  '.join(_highest_parts(item))}" for item in highest)
    return "\n".join(lines)


def _highest_parts(item: HighestValue) -> list[str]:
    """Rule name, value, and where the value lives -- each part omitted when it says nothing.

    The rule name is the one findings use (``<scope>.<metric>``) so the two sections can be
    read together. A population metric belongs to no entity and stops after the value; a
    file's longname is its own path, so it is printed once rather than twice.
    """
    parts = [f"{item.scope}.{item.metric}", _number(item.value)]
    ref = item.entity
    if ref is None:
        return parts
    parts.append(ref.key.longname)
    if ref.key.path and ref.key.path != ref.key.longname:
        parts.append(ref.key.path)
    if ref.line is not None:
        parts.append(f"line {ref.line}")
    return parts


def _closing(result: RunResult, lean: LeanRules | None, style: _Style) -> str:
    """The summary line, the no-findings line before it, and the change's delta after it."""
    summary = _summary_line(result, style)
    lines = [summary] if result.findings else [_nothing_line(result), summary]
    return "\n".join(lines + _delta_lines(result, lean))


def _delta_lines(result: RunResult, lean: LeanRules | None) -> list[str]:
    """What this change did to the project's length, in one line or two (req 7.1, 7.3, 7.6).

    Neither line is coloured: the summary above them is the run's verdict and wears the only
    emphasis in this block, while these two are measurements to be read after it.
    """
    delta = result.net_delta
    if delta is None:
        return []
    lines = [net_line(delta)]
    if _nothing_to_cut(result, delta, lean):
        lines.append(f"{LEAN_ALREADY_PREFIX}{delta.statements:+d} lloc")
    return lines


def net_line(delta: NetDelta) -> str:
    """``net: +12 lloc (+30 lines) over 7 routines`` -- the shape ``agent-rules`` promises.

    The sign is always written, in both directions and on a zero: the figure is a movement,
    and ``net: 0 lloc`` would read as a measurement that was not taken rather than as a change
    that replaced exactly what it removed. Statements lead because formatting cannot move
    them; the source-line delta beside them is what says a change spread the same logic wider.

    **Public because the promise and the output must be one string.**
    :mod:`scitools_hook.report.agent_rules` shows an agent this line before it writes anything
    and calls this function to produce the example, so a change to the format changes the
    document that promised it. Written out twice they agreed on the day they were written and
    nothing afterwards -- which is exactly how task 2.4 re-introduced the defect task 2.3's
    review had just fixed, by quoting a hint instead of sharing it.
    """
    return (
        f"net: {delta.statements:+d} lloc ({delta.lines:+d} lines) "
        f"over {_plural(delta.routines, 'routine')}"
    )


def _nothing_to_cut(result: RunResult, delta: NetDelta, lean: LeanRules | None) -> bool:
    """Requirement 7.6's three conditions, one guard each so that none can be lost.

    A boolean expression would fuse them: branch coverage records no arc for an ``and``
    short-circuit, so a clause deleted from one would leave the module at 100% with every test
    green. Each condition below is what keeps the line from being a false congratulation --
    on a repository where no lean rule ran, on a change that raised six lean findings, or on
    a change that added two hundred lines -- so each is tested by a run where it alone fails.
    """
    if not _lean_is_on(lean):
        return False
    if delta.statements > 0:
        return False
    return not any(finding.rule in _LEAN_RULE_NAMES for finding in result.findings)


def _lean_is_on(lean: LeanRules | None) -> bool:
    """Whether any switch in ``[lean]`` is set, which is what "enabled" means in 7.6.

    All nine, not the five ``wants_references`` asks about: that property answers "must the
    worker walk references", which deliberately excludes ``over_export`` (answered from
    metrics and edges the snapshot already carries) and knows nothing of the net-growth
    maximum. ``report.agent_rules._lean_labels`` enumerates the same nine to print them, and
    the two lists must agree -- a rule an operator switched on and that this list forgets
    would let the Gate report "nothing to cut" about a rule that did look.

    ``max_net_growth`` rather than ``net_growth_severity`` decides the last one, as it does in
    ``agent_rules._net_growth_label``: the severity always has a value and the maximum is what
    an operator sets to turn the rule on.
    """
    if lean is None:
        return False
    if lean.wants_references or lean.wants_tokens:
        return True
    return lean.over_export is not None or lean.max_net_growth is not None


def _nothing_line(result: RunResult) -> str:
    """What "no findings" means -- which is less than it sounds when coverage was lost.

    "This change breaks no rule" is a claim about rules that ran. A metric Understand had
    no value for never ran, so saying it unqualified is the same misreading requirement 2.6
    already forces the parse-error wording to avoid -- and since task 2.4 the Gate can drop
    a shipped threshold and still exit 0, which makes this the common case rather than the
    exotic one.
    """
    line = NOTHING_PARSED_TO_REPORT if result.parse_errors else NOTHING_TO_REPORT
    return f"{line}{UNEVALUATED_SUFFIX}" if _unavailable_metrics(result) else line


class _Counts(NamedTuple):
    """What the summary line reports, counted from the findings themselves."""

    errors: int
    warnings: int
    preexisting: int
    blocking: int


def _counts(findings: Sequence[Finding]) -> _Counts:
    """Count per severity plus the two qualifiers requirement 7.3's summary needs."""
    return _Counts(
        errors=sum(1 for finding in findings if finding.severity == "error"),
        warnings=sum(1 for finding in findings if finding.severity == "warning"),
        preexisting=sum(1 for finding in findings if finding.preexisting),
        blocking=sum(1 for finding in findings if finding.blocking),
    )


def _summary_line(result: RunResult, style: _Style) -> str:
    """Counts per severity, the parse failures, and the exit code's meaning (req 7.3, 1.6, 2.6).

    The parse-failure and unavailable-metric segments appear only when there is one, so a run
    that parsed everything and measured every metric renders exactly the line it always did.
    They are in the summary rather than only in the sections above because this line is the
    last thing a long run prints and the only thing a quiet run prints -- and "0 errors" next
    to an unparsed file, or next to a threshold that was never evaluated, is the misreading
    requirements 2.6 and 5.5 exist to prevent. The exclusions, the tightening and the highest
    values make no claim about coverage and stay out of this line.
    """
    counts = _counts(result.findings)
    code = ExitCode.VIOLATIONS if counts.blocking else ExitCode.OK
    segments = [
        f"summary: {_plural(counts.errors, 'error')}, {_plural(counts.warnings, 'warning')}, "
        f"{counts.preexisting} pre-existing, {counts.blocking} blocking"
    ]
    unparsed = _unparsed_files(result)
    if unparsed:
        segments.append(f"{_plural(unparsed, 'file')} failed to parse, not fully checked")
    unavailable = _unavailable_metrics(result)
    if unavailable:
        segments.append(
            f"{_plural(len(unavailable), 'metric')} unavailable, those limits were not evaluated"
        )
    segments.append(f"exit {code.value}: {describe(code)}")
    return style.strong(" | ".join(segments))


def _unparsed_files(result: RunResult) -> int:
    """How many distinct files failed to parse; several errors in one file are one file."""
    return len({error.path for error in result.parse_errors})


def _wants_agent_block(result: RunResult, verbosity: Verbosity, show: bool) -> bool:
    """The block belongs to a blocking run the caller asked for it on, and never to quiet."""
    return show and verbosity is not Verbosity.QUIET and result.blocking_count > 0


def _agent_block(result: RunResult, style: _Style) -> str:
    """How an agent re-runs the gate and where the remediation text is (req 10.4)."""
    blocking = result.blocking_count
    subject = "1 finding blocks" if blocking == 1 else f"{blocking} findings block"
    opening = f"  {subject} this commit; fix the code, do not relax the limits."
    return "\n".join([style.strong(AGENT_HEADER), opening, *_AGENT_LINES])


def _plural(count: int, word: str) -> str:
    """``1 error`` but ``2 errors``."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _number(value: float) -> str:
    """Render a metric value without the trailing ``.0`` most metrics would carry."""
    return f"{value:g}"
