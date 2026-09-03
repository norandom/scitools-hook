"""Pre-existing, strict-mode and blocking classification of findings (req 4.6, 4.7, 7.9).

:func:`classify` is the last rule step of a run and the only place that decides what blocks
a commit. It answers two questions per finding:

* **Which severity applies?** The configured severity map (req 3.7) is keyed by rule name
  and overrides the severity the evaluator carried, in both directions.
* **Was this already true before the change?** A threshold finding whose before value had
  already broken the same limit, and whose value did not get worse, is *pre-existing*: the
  change did not cause it, so it does not block a commit unless strict mode is on
  (req 4.6, 4.7). A ratchet finding can never be pre-existing -- it exists precisely because
  the value just got worse -- and a finding another evaluator already marked pre-existing
  keeps that judgement.

From those two, ``blocking = severity == "error" and (strict or not preexisting)``: only an
error blocks (req 3.7), and strict mode is what lets a pre-existing error block (req 4.7).
``RunResult.blocking_count`` is counted from the result and decides the exit code (req 7.9).

There is a third question, and it is the one task 11.15 added: **is this ratchet finding's
entity still inside its own limit?** ``analysis.ratchet.within_limit`` answers it from the
finding, and a "yes" caps the severity at ``settings.ratchet.below_limit_severity`` --
``warning`` as shipped, so the movement is reported and does not block. Requirement 4.4 asks
for a *report* of an entity that got worse within its limit and still gets one; what it never
said, and what froze the tool, is that the report must refuse the commit. A 27-line routine
gaining one line against a maximum of 60 was two blocking errors before this; the measurement
and the decay it accepts are in ``config.models.RatchetSettings``. The cap is applied *after*
the severity map, because the map holds an entry for every configured threshold and would
otherwise promote the demoted finding straight back to an error.

There is one more, narrow, answer to the first question, and it is deliberately not a
severity override: an **acknowledged parse error**. Task 11.11 made a file the analysis could
not read a blocking finding, which is right, and a project whose linter mandates a construct
Understand 6.5 cannot parse then cannot commit at all. ``[parse] acknowledged`` lets the
operator name those files with a reason; :func:`classify` clears ``blocking`` on them and
does nothing else. The finding keeps its ``error`` severity, keeps its place in the report,
and gains a sentence saying the file is measured only up to the construct that stopped the
parse -- because the one thing an acknowledgement must never do is read as a pass. Strict
mode does not override it: an acknowledgement is a statement about the *analyser*, not about
whether a violation is old.

The before value the pre-existing test needs is filled in by ``analysis.ratchet``'s
``attach_before``, which sees both snapshots; ``classify`` works on findings alone, so it
reads the direction of a limit from the finding itself: a violation exists because the value
is outside the limit, so a value below its limit broke a ``min`` bound and any other value
broke a ``max`` one -- which is why the kind of a finding matters: a ratchet finding's value
may be well inside the limit. Findings are rebuilt through the model rather than edited, so
no input is modified in place.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Literal

from scitools_hook.analysis.ratchet import within_limit
from scitools_hook.config.models import ParseSettings, RatchetSettings, Severity, SeverityMap
from scitools_hook.models.findings import Finding

Bound = Literal["max", "min"]
"""Which bound of its limit a finding's value broke."""

ACKNOWLEDGED_DETAIL: Final = "acknowledged"
"""``Finding.details`` key holding the reason, so JSON and SARIF carry it as data too."""

PARTIAL_WARNING: Final = (
    "the file is measured only up to the construct that stopped the parse; nothing after it "
    "was read"
)
"""Appended to every acknowledged finding, so an acknowledged file never reads as a clean one."""


def classify(
    findings: Iterable[Finding],
    ratchet: RatchetSettings,
    severities: SeverityMap,
    parse: ParseSettings | None = None,
) -> list[Finding]:
    """Apply the severity map, derive pre-existing status and set ``blocking`` accordingly.

    ``ratchet`` is ``settings.ratchet``: ``strict`` (req 4.7) and the ``below_limit_severity``
    ceiling of task 11.15, which travel together because both of them decide only whether a
    finding blocks. Every finding is returned, in the order it was given; nothing is filtered
    here, because warnings and pre-existing findings are still reported, just not counted as
    blocking (req 7.9). ``parse`` is ``settings.parse``; omitting it is the shipped behaviour,
    in which every unreadable file blocks.
    """
    classified = [_classify_one(finding, ratchet, severities) for finding in findings]
    if parse is None or not parse.acknowledged:
        return classified
    return [_acknowledge(finding, parse) for finding in classified]


def _acknowledge(finding: Finding, parse: ParseSettings) -> Finding:
    """Clear ``blocking`` on an acknowledged parse error, and say so in the finding.

    Only a ``parse`` finding is eligible. An acknowledgement that reached a threshold or a
    structural finding would be an ignore list wearing another name -- and one that could
    hide a measurement, which is the opposite of what this is for.
    """
    if finding.kind != "parse":
        return finding
    entry = parse.acknowledgement(finding.path)
    if entry is None:
        return finding
    return Finding.model_validate(
        finding.model_dump()
        | {
            "blocking": False,
            "message": f"{finding.message} -- acknowledged: {entry.reason}; {PARTIAL_WARNING}",
            "details": dict(finding.details) | {ACKNOWLEDGED_DETAIL: entry.reason},
        }
    )


def _classify_one(finding: Finding, ratchet: RatchetSettings, severities: SeverityMap) -> Finding:
    """Severity, pre-existing status and blocking flag, decided together.

    Together, because the model rejects a blocking finding that is not an error: an override
    that turns a rule into a warning has to clear ``blocking`` in the same breath (req 3.7).
    """
    severity = _severity_of(finding, ratchet, severities)
    preexisting = finding.preexisting or _is_preexisting(finding)
    blocking = severity == "error" and (ratchet.strict or not preexisting)
    return Finding.model_validate(
        finding.model_dump()
        | {"severity": severity, "preexisting": preexisting, "blocking": blocking}
    )


def _severity_of(finding: Finding, ratchet: RatchetSettings, severities: SeverityMap) -> Severity:
    """The severity this finding is judged at: the map's, then the below-limit ceiling.

    In that order, and the second is a ceiling rather than a second override. The map is the
    operator's word on the *rule* (req 3.7) and must keep the last say in the direction that
    matters -- a rule demoted to ``warning`` stays a warning here whatever
    ``below_limit_severity`` holds -- while the ceiling is a statement about this one
    *finding*: the entity is still inside its own limit, so the movement is worth printing and
    not worth refusing a commit over (task 11.15).

    Applying it after the map is what makes the setting work at all. The pipeline builds the
    severity map from every configured threshold, so ``routine.CountLineCode -> error`` is
    always in it; a ratchet finding demoted where it is built would be promoted straight back
    on the next line.
    """
    severity = severities.get(finding.rule, finding.severity)
    if not within_limit(finding):
        return severity
    return _weaker(severity, ratchet.below_limit_severity)


def _weaker(one: Severity, other: Severity) -> Severity:
    """The less severe of two severities; ``warning`` wins because it never blocks."""
    return "warning" if "warning" in (one, other) else "error"


def _is_preexisting(finding: Finding) -> bool:
    """Whether this violation already broke its limit before the change and did not worsen."""
    if finding.kind != "threshold":
        return False
    if finding.before is None or finding.value is None or finding.limit is None:
        return False
    if _bound_of(finding.value, finding.limit) == "max":
        return finding.before > finding.limit and finding.value <= finding.before
    return finding.before < finding.limit and finding.value >= finding.before


def _bound_of(value: float, limit: float) -> Bound:
    """Which bound the finding broke, read from the side of the limit its value is on."""
    return "min" if value < limit else "max"
