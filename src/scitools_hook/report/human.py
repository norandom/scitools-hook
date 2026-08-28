"""The human view of a run: findings grouped by file, then one summary line (req 7.3).

The renderer is a pure function of a :class:`~scitools_hook.models.findings.RunResult` and
three decisions the caller has already made -- how much to print, whether to colour, and
whether the run should end with instructions for a coding agent. It reads neither
``sys.stdout`` nor the environment: requirement 7.6 (no colour on a non-interactive terminal
unless forced) is a decision about the terminal, which belongs to the CLI, so the CLI computes
it with :func:`resolve_color` and passes the answer in. That keeps every rendering test free
of monkeypatching and makes the output byte-deterministic.

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
* **Quiet** (req 7.8) prints the summary and the blocking findings, and nothing else -- not
  the warnings, not the pre-existing findings and not the agent block of requirement 10.4,
  whose narrower rule 7.8 is. A caller that wants the block must not ask for quiet.
* **Counts** in the summary are taken from ``result.findings`` rather than from the
  ``*_count`` fields, so the line can never disagree with the findings printed above it;
  ``blocking_count`` is a validated mirror of the same findings and decides the exit code.
* **Colour** is emitted as SGR escapes around already-padded text, so stripping the escapes
  gives exactly the uncoloured rendering. The design's ``rich`` console lives in the CLI; a
  renderer that returns a string must not wrap or re-flow the text it is handed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, NamedTuple

from scitools_hook.config.models import Severity
from scitools_hook.exit_codes import ExitCode, describe
from scitools_hook.models.findings import Finding, RunResult


class Verbosity(StrEnum):
    """How much of a run to print; ``QUIET`` is requirement 7.8."""

    NORMAL = "normal"
    QUIET = "quiet"


class ColorMode(StrEnum):
    """The colour decision, already made: the renderer never re-decides it (req 7.6)."""

    OFF = "off"
    ON = "on"


NOTHING_TO_REPORT: Final = "nothing to report: this change breaks no rule"
PROJECT_HEADER: Final = "project-wide"
ARCH_HEADER_PREFIX: Final = "architecture node "
AGENT_HEADER: Final = "agent instructions"

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


def render_human(
    result: RunResult,
    verbosity: Verbosity = Verbosity.NORMAL,
    color: ColorMode = ColorMode.OFF,
    show_agent_block: bool = True,
) -> str:
    """Render ``result`` as text, without a trailing newline (req 7.3, 7.6, 7.8, 10.4)."""
    style = _Style(color)
    summary = _summary_line(result, style)
    if not result.findings:
        return f"{NOTHING_TO_REPORT}\n{summary}"
    sections = [_render_group(group, style) for group in _groups(_visible(result, verbosity))]
    sections.append(summary)
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


def _render_group(group: _Group, style: _Style) -> str:
    """The group header followed by each of its findings."""
    lines = [style.strong(group.header)]
    for finding in group.findings:
        lines.extend(_finding_lines(finding, group.path, style))
    return "\n".join(lines)


def _finding_lines(finding: Finding, path: str, style: _Style) -> list[str]:
    """One finding: the head line, its message, and its hint when the pipeline attached one."""
    lines = ["  " + "  ".join(_head_parts(finding, path, style)), f"    {finding.message}"]
    if finding.hint:
        lines.append(style.hint(f"    hint: {finding.hint}"))
    return lines


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
    """Counts per severity and the exit code with its documented meaning (req 7.3, 1.6)."""
    counts = _counts(result.findings)
    code = ExitCode.VIOLATIONS if counts.blocking else ExitCode.OK
    return style.strong(
        f"summary: {_plural(counts.errors, 'error')}, {_plural(counts.warnings, 'warning')}, "
        f"{counts.preexisting} pre-existing, {counts.blocking} blocking "
        f"| exit {code.value}: {describe(code)}"
    )


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
