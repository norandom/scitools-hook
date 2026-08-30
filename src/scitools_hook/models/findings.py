"""Findings and run results: the one contract every rule and every renderer speaks (req 7.1).

The rule-name grammar lives here and nowhere else, because hints (7.2), SARIF rule ids
(7.5), the severity map (3.7) and baseline keys (8.1) all key on it::

    <scope>.<metric>            e.g. "routine.CyclomaticStrict", "project.AVG:CountLineCode"
    structure.<rule>            one of STRUCTURE_RULES
    codecheck.<check_id>        the CodeCheck check id

``Finding.hint`` and ``Finding.before`` are deliberately empty when an evaluator produces a
finding: the ratchet step fills ``before`` and the pipeline attaches the hint, so JSON and
SARIF carry the same text as the human output.
"""

from __future__ import annotations

from typing import Final, Literal, NamedTuple, Self, get_args

from pydantic import Field, model_validator

from scitools_hook.config.metric_names import (
    MetricRef,
    Scope,
    format_metric_name,
    is_valid_scope,
    parse_metric_name,
)
from scitools_hook.config.models import Limit, Severity, ThresholdSpec
from scitools_hook.errors import ConfigError
from scitools_hook.models.snapshot import DataModel, EntityRef, ParseError

FindingKind = Literal["threshold", "ratchet", "structural", "codecheck"]
"""Which evaluator produced a finding."""

LimitSource = Literal["config", "baseline", "rule"]
"""Where the limit in a finding came from; ``rule`` covers structural rules with no number."""

StructureRuleName = Literal[
    "file_cycle", "arch_cycle", "layer", "fan_in", "fan_out", "new_dependencies", "coupling"
]
STRUCTURE_RULES: Final[tuple[StructureRuleName, ...]] = get_args(StructureRuleName)
"""Every structural rule name, in the order they are documented in the design."""

STRUCTURE_CATEGORY: Final = "structure"
CODECHECK_CATEGORY: Final = "codecheck"

_GRAMMAR_HINT: Final = (
    "expected '<scope>.<metric>', 'structure.<rule>' or 'codecheck.<check_id>', "
    f"where <rule> is one of {', '.join(STRUCTURE_RULES)}"
)


class ParsedRule(NamedTuple):
    """A rule name split into its parts; exactly one of ``metric``/``name`` is set."""

    category: Literal["threshold", "structure", "codecheck"]
    scope: Scope | None
    metric: MetricRef | None
    name: str | None


def build_rule_name(scope: Scope, metric: str) -> str:
    """Rule name of a threshold; identical to ``ThresholdSpec.rule`` for the same values."""
    return f"{scope}.{format_metric_name(parse_metric_name(metric))}"


def structure_rule(name: StructureRuleName) -> str:
    """Rule name of a structural rule."""
    return f"{STRUCTURE_CATEGORY}.{name}"


def codecheck_rule(check_id: str) -> str:
    """Rule name of one CodeCheck check."""
    if not check_id.strip():
        raise ConfigError("a CodeCheck rule name needs a check id", hint=_GRAMMAR_HINT)
    return f"{CODECHECK_CATEGORY}.{check_id}"


def parse_rule_name(raw: str) -> ParsedRule:
    """Split ``raw`` into its parts; raise ``ConfigError`` when it is outside the grammar."""
    category, separator, rest = raw.partition(".")
    if not separator or not rest:
        raise ConfigError(
            f"rule name {raw!r} has no '<category>.<name>' form", key=raw, hint=_GRAMMAR_HINT
        )
    if category == STRUCTURE_CATEGORY:
        if rest not in STRUCTURE_RULES:
            raise ConfigError(f"unknown structural rule {rest!r}", key=raw, hint=_GRAMMAR_HINT)
        return ParsedRule(category="structure", scope=None, metric=None, name=rest)
    if category == CODECHECK_CATEGORY:
        return ParsedRule(category="codecheck", scope=None, metric=None, name=rest)
    if not is_valid_scope(category):
        raise ConfigError(
            f"unknown scope {category!r} in rule name {raw!r}", key=raw, hint=_GRAMMAR_HINT
        )
    return ParsedRule(
        category="threshold", scope=category, metric=parse_metric_name(rest), name=None
    )


def is_valid_rule_name(raw: str) -> bool:
    """Whether ``raw`` follows the rule-name grammar."""
    try:
        parse_rule_name(raw)
    except ConfigError:
        return False
    return True


class Finding(DataModel):
    """One violation, in the shape requirement 7.1 asks for.

    ``blocking`` may only be set on an ``error``; whether a pre-existing violation blocks is
    decided by ``analysis.classify`` from strict mode (req 4.6, 4.7), so that combination is
    left open here.
    """

    kind: FindingKind
    rule: str
    metric: str | None = None
    scope: Scope
    entity: EntityRef | None = None
    path: str = ""
    line: int | None = None
    value: float | None = None
    before: float | None = None
    limit: float | None = None
    limit_source: LimitSource = "config"
    severity: Severity = "error"
    blocking: bool = False
    preexisting: bool = False
    message: str
    hint: str = ""
    details: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_rule_and_blocking(self) -> Self:
        try:
            parse_rule_name(self.rule)
        except ConfigError as err:
            raise ValueError(f"invalid rule name: {err.message}") from err
        if self.blocking and self.severity != "error":
            raise ValueError(f"a {self.severity} finding cannot be blocking")
        return self


class EffectiveThreshold(DataModel):
    """A configured threshold with the limit that actually applies after the baseline (8.2)."""

    spec: ThresholdSpec
    metric: MetricRef
    limit: Limit
    source: Literal["config", "baseline"] = "config"

    @property
    def rule(self) -> str:
        """Rule name shared with hints, findings, severities and baseline keys."""
        return self.spec.rule


class TightenedLimit(DataModel):
    """One baseline value lowered by an adaptive run (req 8.3)."""

    rule: str
    previous: float
    current: float


class HighestValue(DataModel):
    """The highest value seen for a metric among the affected entities (req 5.6)."""

    scope: Scope
    metric: str
    value: float
    entity: EntityRef | None = None


class RunResult(DataModel):
    """Everything one ``check`` run produced; the JSON output contract (req 7.4).

    ``blocking_count`` is validated against ``findings`` because the exit code is derived
    from it (req 7.9). ``warning_count`` and ``preexisting_count`` are reported as counted
    by the pipeline.
    """

    schema_version: Literal[1] = 1
    tool_version: str
    understand_version: str
    repo_root: str
    selection: str
    started_at: str
    seconds: float
    effective_thresholds: list[ThresholdSpec] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    ignored_counts: dict[Scope, int] = Field(default_factory=dict)
    unavailable_metrics: dict[str, list[str]] = Field(default_factory=dict)
    """Metrics skipped for want of catalogue support, keyed **language -> metrics** (req 5.5).

    The orientation is not deducible from the type and has been written backwards once, so it
    is stated here: the key is the language. This matches ``ProjectSnapshot.unavailable``,
    ``AvailabilityReport.unavailable`` and ``evaluate_thresholds(catalogue_unavailable=...)``,
    which lets a pipeline pass one through to the other without transposing.
    """
    parse_errors: list[ParseError] = Field(default_factory=list)
    tightened: list[TightenedLimit] = Field(default_factory=list)
    highest: list[HighestValue] = Field(default_factory=list)
    analyzed_files: int = 0
    blocking_count: int = 0
    warning_count: int = 0
    preexisting_count: int = 0

    @model_validator(mode="after")
    def _blocking_count_matches_findings(self) -> Self:
        blocking = sum(1 for finding in self.findings if finding.blocking)
        if blocking != self.blocking_count:
            raise ValueError(
                f"blocking_count is {self.blocking_count} but {blocking} findings are blocking"
            )
        return self
