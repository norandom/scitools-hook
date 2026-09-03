"""Built-in defaults: thresholds per scope, file patterns, severities (req 2.5, 3.1, 5.1-5.4).

The thresholds target reviewable agent output: a routine a human reads in one sitting, a
class with a bounded surface, a file that fits one review. They are the lowest-precedence
layer of the configuration; a user or repository file overrides only the keys it defines.
Project-scope thresholds with a stats prefix (``AVG:CyclomaticStrict``) are reduced over
the routine population; plain project-scope names are project-level Understand metrics.

**Every threshold here has a limit; not every one has a ratchet.** Eight of them count what
a decomposition adds to the container it came out of, and are therefore shipped with the
ratchet off -- the list, the measurements behind it and the reason are
:data:`~scitools_hook.config.models.DECOMPOSITION_COUNTS`, which is where the default is
resolved so that an operator's own threshold on the same metric inherits it. Nothing in this
table says ``ratchet`` explicitly, so nothing here overrides that.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.config.metric_names import Scope, format_metric_name, parse_metric_name
from scitools_hook.config.models import (
    CodeCheckSettings,
    FanKey,
    Limit,
    ProjectSettings,
    Settings,
    SeverityMap,
    StructureRules,
    ThresholdSpec,
    thresholds_from_tables,
)
from scitools_hook.errors import ConfigError

ThresholdTable = dict[str, float | dict[str, float]]
"""Metric name -> scalar maximum or ``{max, min}`` table, i.e. the TOML shape."""

DEFAULT_THRESHOLDS: Final[dict[Scope, ThresholdTable]] = {
    "routine": {
        "CyclomaticStrict": 10,
        "CyclomaticModified": 8,
        "Essential": 4,
        "MaxNesting": 3,
        "CountLineCode": 60,
        "CountStmt": 40,
        "CountParams": 5,
        "CountPath": 100,
    },
    "class": {
        "CountDeclMethod": 20,
        "CountDeclMethodNonStub": 15,
        "CountDeclInstanceVariable": 10,
        "MaxInheritanceTree": 4,
        "CountClassDerived": 8,
        "CountClassCoupled": 12,
        # Unavailable for Python classes (reported once per run, req 5.5); harmless elsewhere.
        "PercentLackOfCohesion": 70,
    },
    "file": {
        "CountLineCode": 500,
        "CountDeclFunction": 25,
        "CountDeclClass": 3,
        "MaxCyclomaticStrict": 10,
        "RatioCommentToCode": {"min": 0.1},
    },
    "project": {
        "AVG:CyclomaticStrict": 3,  # mean over the routine population
        "MaxCyclomaticStrict": 15,
        "AVG:CountLineCode": 30,  # mean routine length
        "MaxNesting": 5,
    },
}
"""Scope -> metric -> limit, in the shape ``[thresholds.<scope>]`` takes in TOML."""

DEFAULT_INCLUDES: Final[list[str]] = ["**"]

DEFAULT_EXCLUDES: Final[list[str]] = [
    ".git/**",
    "node_modules/**",
    ".venv/**",
    "venv/**",
    "build/**",
    "dist/**",
    "target/**",
    "__pycache__/**",
    "*.min.js",
    "*.generated.*",
    "*.lock",
    "uv.lock",
    "package-lock.json",
]
"""Version-control metadata, dependency directories, build outputs and generated files (2.5)."""

DEFAULT_FAN: Final[dict[FanKey, float]] = {
    "file_fan_in": 50,
    "file_fan_out": 20,
    "class_fan_in": 30,
    "class_fan_out": 12,
}
"""Fan-in/fan-out maxima for files and classes (req 6.4); reported as warnings by default."""

_SOFT_THRESHOLDS: Final[frozenset[str]] = frozenset(
    {"file.RatioCommentToCode", "class.PercentLackOfCohesion"}
)


def _threshold_severities() -> SeverityMap:
    severities: SeverityMap = {}
    for scope, table in DEFAULT_THRESHOLDS.items():
        for metric in table:
            rule = f"{scope}.{metric}"
            severities[rule] = "warning" if rule in _SOFT_THRESHOLDS else "error"
    return severities


DEFAULT_SEVERITIES: Final[SeverityMap] = {
    **_threshold_severities(),
    "structure.file_cycle": "error",
    "structure.arch_cycle": "error",
    "structure.fan_in": "warning",
    "structure.fan_out": "warning",
    "structure.new_dependencies": "error",
    "structure.layer": "error",
    "structure.coupling": "error",
    "codecheck": "warning",
}
"""Rule name -> default severity (req 3.7). Ratio and cohesion thresholds only warn."""

DEFAULT_HINTS: Final[dict[str, str]] = {}
"""Operator hint overrides; the built-in hint catalogue lives in ``report.hints``."""


def _default_thresholds() -> list[ThresholdSpec]:
    specs = thresholds_from_tables(DEFAULT_THRESHOLDS)
    for spec in specs:
        spec.severity = DEFAULT_SEVERITIES[spec.rule]
    return specs


def _default_structure() -> StructureRules:
    return StructureRules(
        file_cycles=DEFAULT_SEVERITIES["structure.file_cycle"],
        arch_cycles=DEFAULT_SEVERITIES["structure.arch_cycle"],
        new_dependencies_severity=DEFAULT_SEVERITIES["structure.new_dependencies"],
        fan={key: Limit(max=value) for key, value in DEFAULT_FAN.items()},
        fan_severity=DEFAULT_SEVERITIES["structure.fan_out"],
    )


_DEFAULT_RULES: Final[frozenset[str]] = frozenset(
    f"{scope}.{format_metric_name(parse_metric_name(metric))}"
    for scope, table in DEFAULT_THRESHOLDS.items()
    for metric in table
)
"""Every shipped threshold as a canonical rule name, the form ``ThresholdSpec.rule`` takes."""


def is_default_threshold(scope: Scope, metric: str) -> bool:
    """True when the built-in defaults ship a threshold for ``scope`` and ``metric`` (req 3.1).

    ``config.validate`` asks this about a threshold whose metric the Understand catalogue does
    not have, to tell a default the Gate itself ships — dropped and reported, req 5.5 — from a
    metric an operator asked for, which is a configuration error (req 3.8). The name is
    compared in the canonical form ``ThresholdSpec.rule`` uses, so the case-insensitive stats
    prefix of ``avg:CyclomaticStrict`` matches the shipped ``AVG:CyclomaticStrict`` and the
    two ways of writing one threshold cannot disagree about who owns it. A string that is not
    a metric name at all is nobody's default.
    """
    try:
        ref = parse_metric_name(metric)
    except ConfigError:
        return False
    return f"{scope}.{format_metric_name(ref)}" in _DEFAULT_RULES


def default_settings() -> Settings:
    """Build a fresh, fully validated ``Settings`` from the built-in defaults (req 3.1)."""
    return Settings(
        project=ProjectSettings(include=list(DEFAULT_INCLUDES), exclude=list(DEFAULT_EXCLUDES)),
        thresholds=_default_thresholds(),
        structure=_default_structure(),
        codecheck=CodeCheckSettings(severity=DEFAULT_SEVERITIES["codecheck"]),
        hints=dict(DEFAULT_HINTS),
    )
