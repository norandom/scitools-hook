"""Typed configuration models (req 3.3, 3.6, 3.7); TOML shapes are validated at this boundary.

Every model forbids unknown keys so a misspelt setting fails validation instead of being
ignored. Thresholds arrive either as a ready list of ``ThresholdSpec`` or in the TOML table
shape ``[thresholds.<scope>] Metric = 10`` / ``Metric = {max = 10}`` / ``"AVG:Metric" = 3``;
``Settings`` flattens the table shape itself. Values are only checked for shape here; checks
that need Understand (metric availability, architecture names) live in ``config.validate``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scitools_hook.config.metric_names import (
    MetricRef,
    Scope,
    format_metric_name,
    parse_metric_name,
)
from scitools_hook.config.models_validation import (
    compile_patterns,
    is_number,
    threshold_entries,
)
from scitools_hook.errors import ConfigError

# Written as an explicit ``TypeVar`` rather than PEP 695 ``[T]`` syntax: Understand 6.5
# cannot parse a type-parameter list, and one such declaration costs the rest of the file
# from the analysis (measured in task 10.4).
KeyT = TypeVar("KeyT", bound=str)
"""Threshold-table key: any ``str`` subtype, so a ``Mapping[Scope, ...]`` is accepted."""

Severity = Literal["error", "warning"]
"""Only ``error`` findings block a commit (req 3.7)."""

SeverityMap = dict[str, Severity]
"""Rule name (``<scope>.<metric>``, ``structure.<rule>``, ``codecheck``) -> severity."""

DbLocation = Literal["cache", "gitdir"]
ApiMode = Literal["auto", "inprocess", "upython"]
FanKey = Literal["file_fan_in", "file_fan_out", "class_fan_in", "class_fan_out"]
FAN_KEYS: Final[tuple[FanKey, ...]] = get_args(FanKey)


class StrictModel(BaseModel):
    """Base of every settings model: unknown keys are a validation error (req 3.8)."""

    model_config = ConfigDict(extra="forbid")


class Limit(StrictModel):
    """Bounds of a threshold; a bare number in TOML means ``{max = number}``."""

    max: float | None = None
    min: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _scalar_means_max(cls, data: object) -> object:
        if is_number(data):
            return {"max": data}
        if isinstance(data, Limit):
            return data
        if not isinstance(data, Mapping):
            raise ValueError("a limit is a number (the maximum) or a table with 'max' and/or 'min'")
        for key in ("max", "min"):
            value = data.get(key)
            if value is not None and not is_number(value):
                raise ValueError(f"'{key}' must be a number, got {type(value).__name__}")
        return data

    @model_validator(mode="after")
    def _check_bounds(self) -> Limit:
        if self.max is None and self.min is None:
            raise ValueError("a limit needs 'max' or 'min' (or both)")
        if self.max is not None and self.min is not None and self.max < self.min:
            raise ValueError(f"'max' ({self.max}) is below 'min' ({self.min})")
        return self


class ThresholdSpec(StrictModel):
    """One configured threshold; ``metric`` keeps the raw, possibly prefixed, name (req 3.4)."""

    scope: Scope
    metric: str
    limit: Limit
    severity: Severity = "error"
    ratchet: bool = True

    @field_validator("metric")
    @classmethod
    def _metric_name_parses(cls, value: str) -> str:
        try:
            parse_metric_name(value)
        except ConfigError as err:
            raise ValueError(f"invalid metric name: {err.message}") from err
        return value

    @property
    def ref(self) -> MetricRef:
        """The parsed metric name (canonical prefix)."""
        return parse_metric_name(self.metric)

    @property
    def rule(self) -> str:
        """Rule name ``<scope>.<metric>`` shared by hints, baselines and severity maps."""
        return f"{self.scope}.{format_metric_name(self.ref)}"


def thresholds_from_tables(
    tables: Mapping[KeyT, Mapping[str, object]],
) -> list[ThresholdSpec]:
    """Validate the TOML threshold tables into specs.

    A malformed entry raises ``ValueError`` naming ``thresholds.<scope>.<metric>``; an entry
    that is well-formed but invalid (unknown metric name, ``max`` below ``min``, unknown
    severity) raises a pydantic ``ValidationError`` located by list index, not by key.
    ``config.loader`` flattens the tables itself and maps both onto a ``ConfigError`` naming
    the file and the dotted key (req 3.8).
    """
    return [ThresholdSpec.model_validate(entry) for entry in threshold_entries(tables)]


class LayerRule(StrictModel):
    """Allowed dependency directions from one architecture node (req 6.3)."""

    name: str
    node: str
    may_depend_on: list[str] = Field(default_factory=list)
    severity: Severity = "error"


class CouplingRule(StrictModel):
    """Maximum references between two architecture nodes (req 6.6)."""

    from_node: str
    to_node: str
    max_refs: int = Field(ge=0)
    severity: Severity = "error"


class StructureRules(StrictModel):
    """Structural rules: cycles, fan limits, new-dependency limit, layers, coupling (req 6)."""

    architecture: str = "Directory Structure"
    depth: int = Field(default=2, ge=1)
    file_cycles: Severity = "error"
    arch_cycles: Severity = "error"
    max_new_dependencies_per_file: int | None = Field(default=5, ge=0)
    new_dependencies_severity: Severity = "error"
    fan: dict[FanKey, Limit] = Field(default_factory=dict)
    fan_severity: Severity = "warning"
    layers: list[LayerRule] = Field(default_factory=list)
    coupling: list[CouplingRule] = Field(default_factory=list)


class CodeCheckSettings(StrictModel):
    """Optional Understand CodeCheck configuration to run on staged files (req 6.9)."""

    config: str | None = None
    severity: Severity = "warning"


class BaselineSettings(StrictModel):
    """Where the adaptive baseline lives and whether it is applied (req 8)."""

    file: Path = Path("scitools-hook.baseline.json")
    adaptive: bool = False


class IgnoreRules(StrictModel):
    """Regular expressions excluding entities from every evaluation (req 3.6)."""

    files: list[str] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    routines: list[str] = Field(default_factory=list)

    @field_validator("files", "classes", "routines")
    @classmethod
    def _patterns_compile(cls, patterns: list[str]) -> list[str]:
        return compile_patterns(patterns)


class ProjectSettings(StrictModel):
    """Which files enter the database and which languages are enabled (req 2.4, 2.5)."""

    include: list[str] = Field(default_factory=lambda: ["**"])
    exclude: list[str] = Field(default_factory=list)
    languages: list[str] | None = None


class UnderstandSettings(StrictModel):
    """Understand location, database placement and API execution mode (req 1.1, 2.1)."""

    home: Path | None = None
    db_location: DbLocation = "cache"
    api_mode: ApiMode = "auto"


class OutputSettings(StrictModel):
    """Review-aid sizing and optional sections of the report (req 5.6, 9)."""

    graphs_max: int = Field(default=20, ge=0)
    impact_depth: int = Field(default=3, ge=0)
    show_highest: bool = False


class RatchetSettings(StrictModel):
    """``[ratchet] strict = true`` makes pre-existing violations block (req 4.7)."""

    strict: bool = False


class Settings(StrictModel):
    """Effective configuration; ``thresholds`` accepts the TOML table shape or a list."""

    understand: UnderstandSettings = Field(default_factory=UnderstandSettings)
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    thresholds: list[ThresholdSpec] = Field(default_factory=list)
    ratchet: RatchetSettings = Field(default_factory=RatchetSettings)
    ignore: IgnoreRules = Field(default_factory=IgnoreRules)
    structure: StructureRules = Field(default_factory=StructureRules)
    codecheck: CodeCheckSettings = Field(default_factory=CodeCheckSettings)
    baseline: BaselineSettings = Field(default_factory=BaselineSettings)
    hints: dict[str, str] = Field(default_factory=dict)
    output: OutputSettings = Field(default_factory=OutputSettings)

    @field_validator("thresholds", mode="before")
    @classmethod
    def _flatten_threshold_tables(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return threshold_entries(value)
        return value


class Provenance(StrictModel):
    """Dotted key -> source: ``default``, ``user:<path>``, ``repo:<path>``, ``env:<VAR>``, ``cli``.

    The loader fills one entry per leaf so ``config`` can print where every value came from.
    """

    values: dict[str, str] = Field(default_factory=dict)
