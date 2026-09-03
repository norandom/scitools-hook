"""The human view of a run: findings grouped by file, then one summary line (req 7.3).

The renderer is a pure function of a :class:`~scitools_hook.models.findings.RunResult` and
four decisions the caller has already made -- how much to print, whether to colour, whether
the run should end with instructions for a coding agent, and whether the operator asked for
the highest values (req 5.6). It reads neither ``sys.stdout`` nor the environment:
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
from scitools_hook.config.models import Severity
from scitools_hook.exit_codes import ExitCode, describe
from scitools_hook.models.findings import Finding, HighestValue, RunResult, TightenedLimit
from scitools_hook.models.snapshot import ParseError


class Verbosity(StrEnum):
    """How much of a run to print; ``QUIET`` is requirement 7.8."""

    NORMAL = "normal"
    QUIET = "quiet"


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
HIGHEST_HEADER: Final = (
    "highest values: the largest value per metric, whether or not it breaks a limit"
)
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
    show_highest: bool = False,
) -> str:
    """Render ``result`` as text, without a trailing newline.

    Covers requirements 7.3, 7.6, 7.8 and 10.4, the parse errors of 2.6, and the run facts
    3.6, 5.5, 8.3 and -- when ``show_highest`` says the operator asked for them -- 5.6.
    """
    style = _Style(color)
    sections = []
    if _shows_parse_errors(result, verbosity):
        sections.append(_parse_error_section(result.parse_errors, style))
    if _shows_unavailable(result, verbosity):
        sections.append(_unavailable_section(result.unavailable_metrics, style))
    sections.extend(_render_group(group, style) for group in _groups(_visible(result, verbosity)))
    sections.extend(_note_sections(result, verbosity, show_highest, style))
    sections.append(_closing(result, style))
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
    return sections


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


def _closing(result: RunResult, style: _Style) -> str:
    """The summary line, led by the no-findings line when no finding was printed above it."""
    summary = _summary_line(result, style)
    return summary if result.findings else f"{_nothing_line(result)}\n{summary}"


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
