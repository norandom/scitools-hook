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
from typing import Literal

from scitools_hook.config.models import SeverityMap
from scitools_hook.models.findings import Finding

Bound = Literal["max", "min"]
"""Which bound of its limit a finding's value broke."""


def classify(findings: Iterable[Finding], strict: bool, severities: SeverityMap) -> list[Finding]:
    """Apply the severity map, derive pre-existing status and set ``blocking`` accordingly.

    ``strict`` is ``settings.ratchet.strict`` (req 4.7). Every finding is returned, in the
    order it was given; nothing is filtered here, because warnings and pre-existing findings
    are still reported, just not counted as blocking (req 7.9).
    """
    return [_classify_one(finding, strict, severities) for finding in findings]


def _classify_one(finding: Finding, strict: bool, severities: SeverityMap) -> Finding:
    """Severity, pre-existing status and blocking flag, decided together.

    Together, because the model rejects a blocking finding that is not an error: an override
    that turns a rule into a warning has to clear ``blocking`` in the same breath (req 3.7).
    """
    severity = severities.get(finding.rule, finding.severity)
    preexisting = finding.preexisting or _is_preexisting(finding)
    blocking = severity == "error" and (strict or not preexisting)
    return Finding.model_validate(
        finding.model_dump()
        | {"severity": severity, "preexisting": preexisting, "blocking": blocking}
    )


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
