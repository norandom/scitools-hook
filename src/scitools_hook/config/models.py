"""Typed configuration models (req 3.3, 3.6, 3.7); TOML shapes are validated at this boundary.

Every model forbids unknown keys so a misspelt setting fails validation instead of being
ignored. Thresholds arrive either as a ready list of ``ThresholdSpec`` or in the TOML table
shape ``[thresholds.<scope>] Metric = 10`` / ``Metric = {max = 10}`` / ``"AVG:Metric" = 3``;
``Settings`` flattens the table shape itself. Values are only checked for shape here; checks
that need Understand (metric availability, architecture names) live in ``config.validate``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scitools_hook.config.metric_names import (
    DEFAULT_FLOOR_MINIMUM,
    ELEMENT_SCOPES,
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

BeforeSide = Literal["auto", "commit", "shadow"]
"""
Where the before side comes from: an exported shadow tree, the base commit, or whichever of
the two the installed build can do. ``shadow`` is the shipped value, so a repository that
names nothing keeps the route 0.1.0a8 used (req 1.3, 3.3).
"""

DEFAULT_UNUSED_IGNORE: Final[tuple[str, ...]] = (
    r"\.__\w+__$",
    r"(^|\.)test_",
    r"(^|\.)(setup|teardown)(_\w+)?$",
    r"(^|\.)main$",
)
"""
Routines nothing calls **on purpose**, which the unused rule would otherwise report.

Dunder methods are called by the interpreter and never by name; pytest collects ``test_``
functions and its fixtures' ``setup``/``teardown`` hooks; ``main`` is an entry point. Each is
a reference Understand cannot see because there is none to see, which is the documented limit
of reference-based dead-code detection and not a defect in the rule (req 6.3).
"""
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


DECOMPOSITION_COUNTS: Final[frozenset[str]] = frozenset(
    {
        "file.CountDeclFunction",
        "file.CountDeclClass",
        "file.CountLineCode",
        "class.CountDeclMethod",
        "class.CountDeclMethodNonStub",
        "class.CountDeclInstanceVariable",
        "class.CountClassCoupled",
        "class.CountClassDerived",
    }
)
"""Rules whose ratchet is **off** unless a configuration switches it on (task 11.9).

Each of these counts the declarations, collaborators or lines *of the container* -- and each
one goes up when you split the container's contents, which is the remedy every one of the
Gate's own hints names ("extract the inner block into its own routine", "move the methods
that share a subset of the fields into a class of their own", "hold the base as a field and
delegate to it"). Ratcheting them makes the Gate refuse the refactoring it just asked for,
and the cheapest way past a refusal is to undo the extraction.

**The ratchet is only half of that tension, and the other half is not solved here.** Turning
the growth check off stops *this* extraction being refused; the absolute ceiling still refuses
the twentieth. A file of twelve small named helpers is the outcome the routine limits ask for,
so when the two pull against each other the file-level one should yield -- reported by a
session using the Gate on a 770-file project, and measured on this one, where every routine
and class ceiling fits at 99% while 69 of 210 files were outside `CountDeclFunction = 25`.
Whether the shipped 25 is simply too low is a question for a second repository's measurement,
not for this docstring.

The dividing line is **whether the entity being judged can show the improvement**. When a
routine is extracted, the simplification lands on a routine that did not exist before, which
has no pre-change value and is therefore judged by the absolute limits alone (req 4.5); the
container it came out of has nothing left to show but the extra declaration. Where the
improvement *is* visible on the same entity -- a routine flattened in place -- the ratchet
stays on and ``analysis.ratchet`` exempts the measured decomposition instead.

Measured through the installed CLI against Understand 6.5.1204 (task 11.9). Extracting two
helpers out of a six-deep routine: ``file.CountDeclFunction`` 1 -> 3 and ``file.CountLineCode``
10 -> 18 rose, while every routine metric of the routine that was split fell. Extracting two
methods inside a class: ``class.CountDeclMethod`` and ``class.CountDeclMethodNonStub`` 2 -> 4.
Replacing an inheritance layer with composition, which is exactly ``MaxInheritanceTree``'s own
hint: ``class.CountDeclInstanceVariable`` 0 -> 1 and ``class.CountDeclMethod`` 1 -> 2.

The absolute limits are untouched: a file with 40 functions still fails ``file.CountDeclFunction``
at 25. What stops is the *comparison against HEAD*, which is what "worse than before" cannot
answer for a count that decomposition raises by construction.

``class.MaxInheritanceTree`` is deliberately **not** here even though extracting a superclass
raises it (measured: 0 -> 1). No hint in the catalogue asks for another inheritance layer --
``MaxInheritanceTree``'s own hint asks for one fewer -- so it is not a count this defect is
about, and ``report.hints`` is where that claim can be checked.
"""


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

    @model_validator(mode="after")
    def _a_decomposition_count_does_not_ratchet_unless_asked(self) -> ThresholdSpec:
        """Resolve the ``ratchet`` default from the metric (:data:`DECOMPOSITION_COUNTS`).

        The default lives here rather than in ``config.defaults`` because it belongs to the
        *metric*, not to the shipped limit: an operator who writes
        ``[thresholds.file] CountDeclFunction = 40`` in their own file is asking the same
        ambiguous question, and would otherwise silently get the ratchet back. ``ratchet``
        written explicitly -- in TOML, or by a caller constructing the spec -- always wins,
        in both directions, which is what ``model_fields_set`` is consulted for.

        A metric name that does not parse leaves the plain default in place instead of
        raising. ``_metric_name_parses`` makes that unreachable on the validated path, but a
        spec built with ``model_construct`` skips it and is then re-validated when it is put
        into a ``Settings`` (measured). ``config.validate`` is the step that exists to catch
        exactly that spec and report it as ``thresholds.<scope>.<metric>``; a ``ConfigError``
        thrown from here escapes pydantic's protocol and loses the dotted key.
        """
        if "ratchet" in self.model_fields_set:
            return self
        try:
            rule = self.rule
        except ConfigError:
            return self
        if rule in DECOMPOSITION_COUNTS:
            self.ratchet = False
        return self

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
    duplicate_definitions: int | None = Field(default=None, ge=1)
    duplicate_definitions_severity: Severity = "warning"
    duplicate_definitions_ignore: list[str] = Field(default_factory=list)
    fan: dict[FanKey, Limit] = Field(default_factory=dict)
    fan_severity: Severity = "warning"
    layers: list[LayerRule] = Field(default_factory=list)
    coupling: list[CouplingRule] = Field(default_factory=list)
    # Call-graph rules. `reachable_complexity` is the summed CyclomaticStrict of everything a
    # routine transitively reaches -- the thing a reader or an agent has to hold in mind to
    # change it safely, which a per-routine metric cannot express. Both ship OFF: measured on a
    # 770-file project, a limit of 200 fires on 26 of 3187 routines (p50 4, p90 33, p95 66,
    # p99 189, max 1474), and `call_cycles` found 2 cycles there, both deliberate recursive
    # descent. A rule whose first run on a real repository is mostly false positives should be
    # opted into, not opted out of.
    # Where a declared architecture comes from. `structure.architecture` names WHICH
    # architecture the rules read; this says where the declaration is loaded from, so a
    # repository can express its layers by intent rather than by folder layout. `null` turns
    # the import off without deleting the file, which is what you want while bisecting a false
    # positive. Shape follows `baseline.file`.
    architecture_file: Path | None = Path("scitools-hook.arch.xml")
    reachable_complexity: Limit | None = None
    reachable_complexity_severity: Severity = "warning"
    call_cycles: Severity | None = None
    unused_routines: Severity | None = None
    unused_ignore: list[str] = Field(default_factory=lambda: list(DEFAULT_UNUSED_IGNORE))
    architecture_options: dict[str, str] = Field(default_factory=dict)

    @field_validator("unused_ignore")
    @classmethod
    def _unused_patterns_compile(cls, patterns: list[str]) -> list[str]:
        return compile_patterns(patterns)


class CodeCheckSettings(StrictModel):
    """Optional Understand CodeCheck configuration to run on staged files (req 6.9)."""

    config: str | None = None
    severity: Severity = "warning"


class BaselineSettings(StrictModel):
    """Where the adaptive baseline lives, and whether a run **tightens** it (req 8).

    ``adaptive`` does not decide whether the baseline is applied, and this docstring said it
    did until it was measured. One repository, one baseline file, one key changed between two
    runs of ``check --all`` through the installed CLI::

        adaptive = false   routine.CyclomaticStrict effective max = 2.0  (configured 10)
        adaptive = true    routine.CyclomaticStrict effective max = 2.0  (configured 10)

    ``CheckPipeline.run`` loads the store and calls ``analysis.baseline.apply`` before it
    looks at any setting, so a baseline file present on disk narrows the limits either way;
    what ``adaptive`` gates is ``CheckPipeline._adapt``, which lowers the *stored* values a
    ``check --all`` run beat (req 8.3). Requirement 8.2 says application is gated too --
    "while adaptive mode is enabled ... the Gate shall use ... the lower of the configured
    maximum and the baseline value" -- so the behaviour and the requirement disagree, and the
    disagreement is recorded here rather than described the other way round. Closing it means
    changing ``runner.check``, ``cli.agent_rules`` (which documents today's behaviour on
    purpose) and ``config.template`` together; task 11.15 owns none of the three.

    **A baseline is not an amnesty, and expecting one is the other half of the confusion.**
    ``analysis.baseline._narrow`` takes ``min(configured, baseline)`` and requirement 8.4
    forbids ever raising a stored value, so a threshold the project *violates* records a
    baseline above its configured limit and is left at the configured one. Measured on a
    project whose worst routine is 72 lines against a maximum of 60: the baseline records
    72.0, the effective limit stays 60.0, and the finding reads ``limit_source: config`` --
    while ``routine.CyclomaticStrict``, which the project does not violate, is narrowed 10 ->
    2 and reads ``baseline``. So *every* finding a whole-project run reports after a fresh
    capture necessarily says ``config``: that is arithmetic, not a baseline that went
    unapplied. Forgiving what was already broken is ``preexisting`` (req 4.6), which needs a
    before side and therefore exists in staged and worktree mode and not under ``--all``
    (req 4.8) -- which is why ``--all`` reports ``preexisting_count 0``.
    """

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
    sarif: bool = False
    before_side: BeforeSide = "shadow"
    snapshot_cache: bool = True


class AnalysisSettings(StrictModel):
    """How much of an analysis Understand resolved, and the floor below which that is news.

    ``accuracy_floor`` is a fraction of the files an analysis parsed without an error or a
    warning, which is what ``und analyze -accuracy`` reports. Unset by default, and the
    finding it raises never blocks (req 7.3): a poorly resolved analysis makes the rest of a
    run less trustworthy, and saying so is useful, but refusing a commit over the state of
    somebody else's third-party imports is not.
    """

    accuracy_floor: float | None = Field(default=None, ge=0.0, le=1.0)


class OutputSettings(StrictModel):
    """Review-aid sizing and optional sections of the report (req 5.6, 9)."""

    graphs_max: int = Field(default=20, ge=0)
    impact_depth: int = Field(default=3, ge=0)
    show_highest: bool = False


class RatchetSettings(StrictModel):
    """The two levers over the before/after comparison (req 4.4, 4.7; task 11.15).

    ``strict = true`` makes pre-existing violations block (req 4.7).
    ``below_limit_severity`` is the severity ceiling a ratchet finding gets while the entity
    is **still inside its own limit after the change**, and shipping it as ``warning`` is what
    stops the gate freezing every routine it is pointed at.

    **The defect it fixes, measured through the installed CLI against Understand 6.5.1204.**
    A repository whose ``HEAD`` holds a 27-line registrar, the shipped limits
    (``routine.CountLineCode`` 60, ``routine.CountStmt`` 40), one route added::

        error  routine.CountLineCode  big.grow  line 4  worse than before, was 27
          routine big.grow CountLineCode rose from 27 to 28
        error  routine.CountStmt      big.grow  line 4  worse than before, was 27
        summary: 2 errors ... 2 blocking | exit 1

    Twenty-eight lines against a maximum of sixty, refused. Every value in the field report
    this came from has that shape -- ``CountLineCode`` 29 -> 30 of 60, ``CountStmt`` 2 -> 3 of
    40, ``CyclomaticStrict`` 5 -> 6 of 10, ``file.MaxCyclomaticStrict`` 5 -> 6 of 15 -- and
    every one of them was produced *while splitting the routines the same gate had asked the
    reporter to split*. A gate whose own remedy it refuses is turned off, and it was: that
    day's commits went in under ``SCITOOLS_HOOK_SKIP=1``.

    **The rule.** The configured limit is the team's own statement of what is acceptable, so
    that is where the refusal belongs: growth inside the limit is reported and does not block,
    growth that crosses the limit blocks, and growth on an entity already over the limit
    blocks. The last two need no special case here -- a value outside its limit is a threshold
    violation that ``analysis.classify`` refuses to call pre-existing once it has worsened --
    so what this setting governs is exactly the case requirement 4.4 was written for and the
    absolute limits cannot see.

    **The decay this accepts, stated rather than hidden.** A routine may now creep from 29
    lines to 60 one commit at a time without a single refusal. Two things are left watching it
    and neither is the ratchet: every one of those commits still *prints* the growth, because
    the finding is reported and only demoted; and ``project.AVG:CountLineCode`` -- shipped at
    30, half the routine limit -- blocks the commit that pushes the mean routine length past
    it. That backstop was measured, not assumed: one 32-line routine in a staged-mode run
    reports ``project AVG:CountLineCode is 32, which exceeds the maximum 30`` and exits 1.
    What is genuinely given up is the case where a *few* routines fatten towards their limit
    while the project mean stays under 30; that is the price of not freezing every file an
    agent touches, and ``below_limit_severity = "error"`` buys the old behaviour back exactly.

    The value is a **ceiling, never a promotion**: a rule an operator demoted to ``warning``
    stays a warning even under ``below_limit_severity = "error"``, because this setting exists
    to soften a refusal and must not manufacture one.
    """

    strict: bool = False
    below_limit_severity: Severity = "warning"


# --- path patterns and path scopes -----------------------------------------------

_PATTERN_CACHE_SIZE: Final = 512
"""Compiled-pattern cache; a configuration holds tens of patterns, never hundreds."""


@lru_cache(maxsize=_PATTERN_CACHE_SIZE)
def compile_path_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one configured glob into a regular expression over a relative POSIX path.

    The language is the one ``[project] include``/``exclude`` already speaks, and it is
    deliberately **the same implementation shape** as ``git.shadow._translate``: ``**``
    spans whole segments, a lone ``*`` and ``?`` stay inside one, a leading ``/`` anchors
    the pattern to the repository root and everything else is literal, character classes
    included. ``config`` sits below ``git`` in the import matrix and cannot reuse the
    shadow's copy, so the agreement is asserted instead --
    ``tests/config/test_detect.py`` drives both implementations over the same corpus of
    patterns and paths and fails when they disagree. Without that test this is two
    languages with one name.
    """
    anchored = pattern.startswith("/")
    body = _translate_path_pattern(pattern.lstrip("/").rstrip("/"))
    return re.compile(body if anchored else f"(?:.*/)?{body}")


def _translate_path_pattern(pattern: str) -> str:
    """Expand the four metacharacters and escape everything else."""
    out: list[str] = []
    at = 0
    while at < len(pattern):
        if pattern.startswith("**/", at):
            out.append("(?:.*/)?")
            at += 3
        elif pattern.startswith("**", at):
            out.append(".*")
            at += 2
        elif pattern[at] == "*":
            out.append("[^/]*")
            at += 1
        elif pattern[at] == "?":
            out.append("[^/]")
            at += 1
        else:
            out.append(re.escape(pattern[at]))
            at += 1
    return "".join(out)


def path_prefixes(rel: str) -> list[str]:
    """``a/b/c.py`` as ``["a", "a/b", "a/b/c.py"]`` -- the path and every directory above it."""
    parts = rel.split("/")
    return ["/".join(parts[: at + 1]) for at in range(len(parts))]


def matching_pattern(patterns: Sequence[str], rel: str) -> str | None:
    """The first pattern in ``patterns`` that covers ``rel``, or ``None``.

    The *pattern* rather than a boolean, because every caller here has to be able to say
    **which line** decided: an exclusion an operator cannot trace back to a line they can
    read is exactly the silent narrowing this feature exists not to add. A pattern matches
    the path itself or any directory containing it, so ``build`` covers ``build/out.o``.
    An empty pattern is skipped rather than treated as ``**``: a stray blank entry must not
    switch a whole project off.
    """
    prefixes = path_prefixes(rel)
    for pattern in patterns:
        if not pattern:
            continue
        compiled = compile_path_pattern(pattern)
        if any(compiled.fullmatch(prefix) for prefix in prefixes):
            return pattern
    return None


class ScopeOverride(StrictModel):
    """What one path scope says about one rule: a limit of its own, or ``false`` to switch it off.

    The TOML shapes are the shapes a global threshold already takes, plus ``false``::

        [scope.tests.thresholds.routine]
        CyclomaticStrict = 15                              # a maximum
        CountLineCode    = { max = 120, severity = "warning" }
        [scope.tests.thresholds.file]
        CountDeclFunction = false                          # off for this scope

    ``false`` is a *disable*, not a limit of zero, and it is spelled as a value rather than
    as a separate ``disabled`` list so that one line per rule says the whole story. ``true``
    is refused: it reads as "on", and "on with which limit?" has no answer here.
    """

    disabled: bool = False
    limit: Limit | None = None
    severity: Severity | None = None
    ratchet: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: object) -> object:
        if data is False:
            return {"disabled": True}
        if data is True:
            raise ValueError(
                "'true' does not say what the limit is; write the limit, or 'false' to "
                "switch the rule off for this scope"
            )
        if is_number(data):
            return {"limit": {"max": data}}
        if isinstance(data, ScopeOverride):
            return data
        if not isinstance(data, Mapping):
            raise ValueError("a scope threshold is a number, a table with 'max'/'min', or false")
        return _split_scope_limit(data)

    @model_validator(mode="after")
    def _says_something(self) -> ScopeOverride:
        if self.disabled and (self.limit or self.severity or self.ratchet is not None):
            raise ValueError("'false' switches the rule off; it cannot carry a limit as well")
        if not self.disabled and self.limit is None and self.severity is None:
            if self.ratchet is None:
                raise ValueError("a scope threshold needs a limit, a severity, or false")
        return self


def _split_scope_limit(data: Mapping[str, object]) -> dict[str, object]:
    """Lift ``max``/``min`` out of the inline table into the nested ``limit``.

    Written out rather than handed to ``Limit`` wholesale because the table also carries
    ``severity`` and ``ratchet``, and ``Limit`` forbids unknown keys. A table that already
    spells ``limit`` is passed through, so a round trip through ``model_dump`` re-validates.
    """
    split = {key: value for key, value in data.items() if key not in {"max", "min"}}
    bounds = {key: value for key, value in data.items() if key in {"max", "min"}}
    if bounds:
        if "limit" in split:
            raise ValueError("write the bounds either as 'max'/'min' or inside 'limit', not both")
        split["limit"] = bounds
    return split


class PathScope(StrictModel):
    """A named region of the repository whose thresholds differ from the global ones.

    ``paths`` are the same glob patterns ``[project] include``/``exclude`` take, relative to
    the repository root. A scope **never removes** a path from the analysis: task 10.4
    refused a blanket ``tests/**`` ignore that would have hidden a 2598-line test module, and
    a scope is what that refusal asked for instead -- the files are still measured, against
    numbers that say what the region is for.
    """

    paths: list[str] = Field(default_factory=list)
    thresholds: dict[Scope, dict[str, ScopeOverride]] = Field(default_factory=dict)

    @field_validator("thresholds")
    @classmethod
    def _only_element_scopes(
        cls, tables: dict[Scope, dict[str, ScopeOverride]]
    ) -> dict[Scope, dict[str, ScopeOverride]]:
        """Refuse ``project`` and ``arch``: neither is per file, so neither can be per path.

        A ``[scope.tests.thresholds.project]`` table would look like it narrowed the mean
        over the routine population to the test tree. It cannot: the population is the whole
        project's, reduced once. Accepting the table and ignoring it is the silent-narrowing
        failure this feature exists to avoid, so it is a configuration error instead.
        """
        outside = [scope for scope in tables if scope not in ELEMENT_SCOPES]
        if outside:
            raise ValueError(
                f"a path scope cannot carry {', '.join(sorted(outside))} thresholds: "
                f"they are reduced over the whole project, not per file "
                f"(one of {', '.join(ELEMENT_SCOPES)})"
            )
        for scope, table in tables.items():
            for metric in table:
                _check_scope_metric(scope, metric)
        return tables

    def matched_by(self, path: str) -> str | None:
        """The pattern of this scope that covers ``path``, or ``None``."""
        return matching_pattern(self.paths, path)


def _check_scope_metric(scope: Scope, metric: str) -> None:
    """Reject a metric name a scope table cannot mean, here rather than during a run.

    Without this, a typo inside ``[scope.tests.thresholds.routine]`` survives configuration
    loading and raises from ``analysis.thresholds`` while a commit is being checked -- an
    internal-looking failure, far from the line that caused it. A stats prefix is refused for
    the same reason ``project`` and ``arch`` tables are: a population is reduced over the
    whole project and cannot be narrowed to a path.
    """
    key = f"scope thresholds.{scope}.{metric}"
    try:
        ref = parse_metric_name(metric)
    except ConfigError as err:
        raise ValueError(f"{key}: invalid metric name: {err.message}") from err
    if ref.is_population:
        raise ValueError(
            f"{key}: a stats prefix reduces the whole project's population, "
            "so it cannot be scoped to a path"
        )


class ParseAcknowledgement(StrictModel):
    """Files the operator has declared Understand cannot read to the end, and why.

    Task 11.11 made an unreadable file in the selection a **blocking** finding, and that was
    the right fix: the Gate had been certifying files it never read. But a real project met
    the fix head-on -- ruff's ``UP046``/``UP047`` rewrite an explicit ``TypeVar`` into PEP 695
    type-parameter syntax, Understand 6.5 cannot parse a type-parameter list, and one such
    declaration takes the rest of the file out of the database. Their linter mandates the
    construct; the Gate refuses the file; nobody can move.

    An acknowledgement resolves that **without becoming a silencer**, and the three
    properties are the whole design:

    * The finding is still produced, still reported and still names the file. What changes is
      ``blocking``, and nothing else -- ``analysis.classify`` is where that happens.
    * ``reason`` is required and must say something. An entry that could be written without a
      reason would be an ignore list with a longer name, and the reason is what the report
      quotes so a reader can see that the file was *not* fully checked.
    * A bare path string is refused with a message asking for the reason, rather than
      accepted as an unexplained entry.

    An acknowledgement covers ``analysis.parse_error`` and nothing else; it can never demote
    a finding about the code, because the code it would be about was never read.
    """

    paths: list[str] = Field(min_length=1)
    reason: str

    @model_validator(mode="before")
    @classmethod
    def _refuse_a_bare_path(cls, data: object) -> object:
        if isinstance(data, str):
            raise ValueError(
                f'write {{ paths = [{data!r}], reason = "..." }}: an acknowledged file '
                "stops blocking the commit, so the entry has to say why"
            )
        return data

    @field_validator("paths")
    @classmethod
    def _patterns_are_not_blank(cls, patterns: list[str]) -> list[str]:
        if any(not pattern.strip() for pattern in patterns):
            raise ValueError("a blank path pattern acknowledges nothing; remove it")
        return patterns

    @field_validator("reason")
    @classmethod
    def _reason_says_something(cls, reason: str) -> str:
        if not reason.strip():
            raise ValueError("'reason' is what the report quotes; it cannot be empty")
        return reason

    def matched_by(self, path: str) -> str | None:
        """The pattern of this acknowledgement that covers ``path``, or ``None``."""
        return matching_pattern(self.paths, path)


class ParseSettings(StrictModel):
    """``[parse]``: which unreadable files are acknowledged, and why (task 11.11's escape hatch).

    Empty by default, which is the shipped behaviour: every file in the selection that
    Understand could not read blocks the commit.
    """

    acknowledged: list[ParseAcknowledgement] = Field(default_factory=list)

    def acknowledgement(self, path: str) -> ParseAcknowledgement | None:
        """The first acknowledgement covering ``path``, or ``None``."""
        return next((entry for entry in self.acknowledged if entry.matched_by(path)), None)

    def unused(self, paths: Sequence[str]) -> list[ParseAcknowledgement]:
        """Acknowledgements that cover none of ``paths``.

        A stale entry is the failure mode this shape has: it keeps a file from blocking long
        after the file was fixed or deleted, and it does so silently, because an entry that
        matches nothing produces no output of its own. Reported rather than removed -- it is
        the operator's line to delete.
        """
        return [entry for entry in self.acknowledged if not any(map(entry.matched_by, paths))]


# --- the lean-code family --------------------------------------------------------

DEFAULT_LEAN_PARAMETER_IGNORE: Final[tuple[str, ...]] = (
    r"^(self|cls|this)$",
    r"^_",
    r"^(args|kwargs)$",
)
"""Parameters a routine declares without reading, **on purpose** (req 1.4, 1.5).

The receiver is the language's, not the author's: Python spells it ``self``/``cls``, and a
method that never touches it still has to declare it. A leading underscore is the convention
every linter already reads as "declared and deliberately unused", and ``*args``/``**kwargs``
are how a signature says it forwards what it was given. Each is a parameter the rule would
report and no author would delete, which is the definition of noise.

**Measured on two repositories with the floors at zero, 2026-09-11, and kept as it is**
(research.md, task 6.4). The list is over parameter *names*, and the noise the rule
produces at the shipped numbers is a property of the *routine*: of this repository's 68
findings, 56 are pytest fixtures declared by ``test_`` functions and 4 are the two
``@overload`` stubs of one routine; of facdrone's 58, 39 and 16. What is left -- 8 and 3 --
holds the genuine ones (facdrone's two already carry ``# noqa: ARG001``). No pattern over a
parameter name reaches a fixture called ``assembler`` or a stub body of ``...``, so the list
is confirmed rather than extended, and the remedy is a routine-shape exclusion in the rule
itself -- the ``test_`` shape :data:`DEFAULT_UNUSED_IGNORE` already names, and a long name
declared twice in one file -- which is outside a defaults task and recorded there for the
one that owns it. Until then the rule ships off, as every rule here does, and an operator
who enables it on Python should read its first ten before trusting its count.
"""

DEFAULT_LEAN_CLASS_IGNORE: Final[tuple[str, ...]] = (
    r"(^|\.)Test",
    r"Error$",
    r"Exception$",
)
"""Classes nothing in the project references, by construction (req 1.5).

An exception class is *raised* and *caught*; in most languages neither is a reference to the
class from a second file that a caller-based rule can see, so an error hierarchy reads as
entirely dead code. pytest collects ``Test``-prefixed classes by name discovery, the same
blind spot ``DEFAULT_UNUSED_IGNORE`` records for ``test_`` functions.

**Confirmed on two repositories, 2026-09-11** (research.md, task 6.4): with the floors at
zero the rule found 0 of this repository's 338 classes and 18 of facdrone's 1 809, and eight
of facdrone's first ten are named in no other tracked source file. Nothing in either list
was an exception or a test class, so the list is neither short nor long by measurement.
"""

DEFAULT_LEAN_VARIABLE_IGNORE: Final[tuple[str, ...]] = (
    r"^__\w+__$",
    r"^(log|logger|pytestmark)$",
    r"^_$",
)
"""Module-level names the runtime reads rather than the code (req 1.5).

``__all__``, ``__version__`` and their kind are read by the interpreter, the packaging tools
and the documentation build. A module logger is assigned once and used through the logging
machinery; ``pytestmark`` is collected by pytest. None has a reference to find.

**``^_$`` was added on 2026-09-11 from a measurement** (research.md, task 6.4): with the
floors at zero, 5 of this repository's 17 unused-binding findings were the module-level
discard ``_`` -- ``_ = something()`` at import time, the spelling every language uses for
"bound on purpose, read by nobody" -- and 1 of facdrone's 55. It is ``^_$`` and not the
parameter list's ``^_``, because a *leading* underscore on a module binding is a private
constant, and facdrone's ``_SAMPLE_EVERY`` and ``_MAX_LEVERAGE_STEPS`` were bound and never
mentioned again: the rule's genuine finding, which the wider pattern would have silenced.
What the list leaves, on both repositories, splits about evenly between bindings named
nowhere else and bindings named in other files that the analysis did not resolve, which is
the case the accuracy floor exists for.
"""

DEFAULT_LEAN_IMPLEMENTATION_IGNORE: Final[tuple[str, ...]] = (r"Error$", r"Exception$")
"""Base classes that exist for one implementation and are still right (req 3.3).

An exception base with a single subclass is a hierarchy an operator will keep: it is what
``except <Base>`` catches, and collapsing it changes what callers can catch. The rule would
otherwise report every project's first two error classes.
"""

DEFAULT_LEAN_FAMILY_IGNORE: Final[tuple[str, ...]] = (
    r"(^|[.:])__\w+__$",
    r"(^|[.:])(setUp|tearDown|setup|teardown)(_\w+)?$",
)
"""Routine names whose family is an idiom rather than a duplication (req 5.10).

Name patterns, not paths: these routines are idiomatic wherever they are written, so the file
they sit in decides nothing. They are the ``similar_routines`` rule's own list because a
family is reported against a *name* the language or a framework fixes -- one per class, on
purpose -- and merging such a family is the one remedy the finding may not suggest.

**Measured on a 417-file codebase**: of the 224 routines the rule puts in families at a
threshold of 0.8, **thirty are** ``__post_init__`` **validators** across unrelated data
shapes. They are near-identical because a dataclass validator has one shape, and there is no
routine to merge them into: each belongs to its own class. The dunder pattern covers those
with the per-class constructor (``__init__``) and every other hook the interpreter calls by
name; the second covers the xUnit and pytest fixtures, which are one per test class for the
same reason.

**The honest limit**, recorded here rather than papered over: a constructor is a dunder only
where the language spells it as one. ``Shape::Shape`` in C++ and ``Shape.Shape`` in Java or
C# are per-class constructors this list does not cover, and the pattern that would --
``(^|[.:])(\\w+)[.:]+\\2$`` -- also excuses every Python module-level function whose name
matches its module's (``report.sarif.sarif``), which is a real finding silenced. An operator
whose project is written in one of those languages adds it to ``similar_name_ignore``; the
shipped list stays where the measurement is.
"""

DEFAULT_LEAN_OVER_EXPORT_IGNORE: Final[tuple[str, ...]] = (
    "**/__init__.py",
    "**/index.*",
    "**/mod.rs",
    "**/__main__.py",
)
"""Files whose job **is** to hold one definition for one importer (req 4.2).

Path patterns, not name patterns: these are module and package initialisers, and a language
decides them by file name. A package ``__init__.py`` that re-exports one symbol is the
idiom, not the finding; ``index.*`` is the same idiom in the JavaScript and TypeScript
worlds, ``mod.rs`` in Rust, and ``__main__.py`` is an entry point with exactly one caller
that is not a file at all.
"""


DEFAULT_RESOLUTION_FLOOR: Final = 0.75
"""The share of a language's call sites that must resolve before a rule may speak (req 1.8).

**Measured on two repositories on 2026-09-11 and kept, not calibrated** (research.md, task
6.4). Requirement 1.10's measurement paired this figure with a false-positive count for the
first time: this repository resolves 45.9% of its Python call sites (12 861 of 28 029) and
facdrone 32.0% (16 986 of 53 153), and with both floors at zero the four gated rules answered
between 0 and 8 genuine findings in their first ten -- the pass-through rule 1 and 2, the
parameter rule 0 and 2, the class rule none here and 8 there. Neither corpus is above 0.75,
so neither says what the rules do where they are allowed to run, and no corpus above it has
been measured. Lowering the floor onto 32% would license the pass-through rule at one right
answer in ten; the number stays where nothing measured reaches it, and it moves when a
repository above it is measured, not before.

Silence is not absence: below the floor the rules say what stopped them and at what measured
value, which is a different product from a rule that is simply off.

It lives here rather than beside the rules that read it because two places would otherwise
own it -- ``LeanRules.resolution_floor``'s default and ``analysis.lean.dead.Trust``'s -- and
this project has shipped that defect. ``dead`` imports it from here, which is the direction
the layer order allows.
"""

DEFAULT_ACCURACY_FLOOR: Final = 0.75
"""The share of files the analysis must have parsed cleanly before a rule may speak (1.8).

Kept on the same terms as :data:`DEFAULT_RESOLUTION_FLOOR`, from the same measurement
(research.md, task 6.4, 2026-09-11). Both corpora sit far below it -- 19.1% here, 25.9% on
facdrone -- and with the floor at zero the rule it exists for, the unused-variable rule,
answered 5 genuine, 5 discards and 0 misreads in its first ten here, and 3 named nowhere
else, 4 mentioned once more in their own file and 3 named in four to six other files on
facdrone. That last third is the failure this floor bounds: a binding whose use sites the
analysis did not read, reported as unread. At 26% it is three findings in ten. No corpus
above the floor has been measured, so the rate at which that third vanishes is unknown, and
a floor lowered to 0.25 would be a claim that it has. The floor stays until a repository
above it is measured; the discard half of this repository's noise was a list entry, not a
floor question, and :data:`DEFAULT_LEAN_VARIABLE_IGNORE` now carries it.

**This is not** ``analysis.accuracy_floor``, **and the collision is deliberate rather than an
oversight**; :attr:`LeanRules.accuracy_floor` argues it where an operator reads it.
"""

REFERENCE_RULES: Final[tuple[str, ...]] = (
    "unused_parameters",
    "unused_classes",
    "unused_variables",
    "pass_through",
    "single_implementation",
)
"""The five ``[lean]`` switches that need the worker's per-entity reference walk (req 9.4).

``over_export`` is deliberately not among them: it is answered from file metrics,
``file_edges`` and the definitions walk the snapshot already carries, so it needs no ``refs``
call on any entity. Adding it here would make an over-export-only configuration pay for a
reference call on every recorded entity and read none of the answers.

**One list, three readers.** :attr:`LeanRules.wants_references` decides whether the extractor
pays for the walk, ``understand.features.ASKED_BY`` decides which configuration keys the
build must offer :attr:`~scitools_hook.models.understand.Feature.LEAN_REFERENCES` for, and
the suite parametrises over it. An earlier draft of the design said six in one place and five
in another; written out three times, the three would agree on the day they were written and
never again, and the failure is silent -- a rule missing from the extractor's list reads its
facts as "not asked" and reports itself unavailable on every run.
"""

TOKEN_RULES: Final[tuple[str, ...]] = ("duplicates", "similar_routines")
"""The two ``[lean]`` switches answered from the worker's whole-project token pass (req 9.4).

The other half of :data:`REFERENCE_RULES`, with the same three readers and for the same
reason: :attr:`LeanRules.wants_tokens` decides whether the extractor pays for a lexer pass
over every file, ``understand.features.ASKED_BY`` decides which configuration keys the build
must offer :attr:`~scitools_hook.models.understand.Feature.LEAN_TOKENS` for, and the suite
parametrises over it.

Written out twice, the pair fails **asymmetrically and silently**, which is why it is written
once. A rule this list forgets while ``wants_tokens`` remembers it is one an operator can
enable on a build that was never asked whether its ``Ent.lexer`` answers, and the run then
reports nothing while looking as though it looked; a rule ``wants_tokens`` forgets while this
list remembers it is refused on a build that could have run it. Neither end can see the other.
"""


class LeanRules(StrictModel):
    """``[lean]``: the lean-code family's switches, numbers and ignore lists (req 1.5-7.5).

    Its own section rather than more fields on ``StructureRules`` because an operator
    configures one section for one question, and ``StructureRules`` already carries twenty
    fields. The findings keep the ``structure.`` category all the same, so the severity map,
    the SARIF rule ids, the scope overrides and the hint lookup work unchanged (req 9.6).

    Every rule follows ``StructureRules.unused_routines``: ``Severity | None``, where ``None``
    is off, so the whole family ships silent and each rule is enabled by naming a severity
    (req 9.1). ``wants_references`` and ``wants_tokens`` are what requirement 9.4 turns on --
    while both are false the extractor asks the worker for nothing on the family's behalf and
    a warm check costs what it costs today.

    **The numbers were starting values, and on 2026-09-11 they became measured ones.** They
    were first chosen on this repository with Python's own tokenizer as a stand-in for
    Understand's lexer (research.md): exact duplicate windows fell from 641 at 5 lines to 72
    at 12, of which 24 were in ``src/`` and 2 judged genuine; similar routines at ratio >= 0.9
    numbered 184, of which 10 were in ``src/`` and all 10 real copies. Hence 12, 0.9 and a
    six-statement floor. Requirement 5.7 asked for the same counts under Understand's own
    lexer, and tasks 6.3 and 6.4 took them on two repositories (research.md):

    * ``similar_routines`` at 0.9 / 6 statements / a family of 2: 163 families here and 129 on
      facdrone, and of the first ten on each side 6 and 7 are copies a reviewer would merge,
      the rest twins kept apart on purpose, **none noise**. Every number kept.
    * ``duplicates`` at 12 lines: 48 findings here (about 24 places, both ends reported) and
      152 on facdrone. Over the whole lists, 6 of 48 and 30 of 152 are name lists -- an
      ``__all__`` or an import block of twelve or more names, which the lexer sees as twelve
      code lines -- and the rest code. Raising the window to 15 would keep 16 of the 42 code
      findings here and 59 of the 122 there to lose 4 and 17 lists; a path pattern cannot
      name an ``__all__``. So 12 stays, the 20% of name lists is recorded, and the exclusion
      that would remove them -- a window made only of names and commas -- belongs to the
      rule rather than to a number.
    """

    unused_parameters: Severity | None = None
    unused_parameters_ignore: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LEAN_PARAMETER_IGNORE)
    )
    unused_classes: Severity | None = None
    unused_classes_ignore: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LEAN_CLASS_IGNORE)
    )
    unused_variables: Severity | None = None
    unused_variables_ignore: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LEAN_VARIABLE_IGNORE)
    )
    # Requirement 1.8's two floors: how well this run resolved calls, and how much of it
    # Understand parsed at all, below which the dead-code and pass-through rules make no
    # finding and say which floor stopped them at what measured value. Unlike every switch
    # above they ship *set*, because they are what keeps the rules from deleting working
    # code and a floor an operator has to remember to add is not a guard.
    resolution_floor: float = Field(default=DEFAULT_RESOLUTION_FLOOR, ge=0.0, le=1.0)
    accuracy_floor: float = Field(default=DEFAULT_ACCURACY_FLOOR, ge=0.0, le=1.0)
    """The accuracy below which a lean rule refuses to judge -- **not** ``analysis.accuracy_floor``.

    Two keys read the same measurement, the share of files ``und analyze -accuracy`` parsed
    without an error or a warning, and they do opposite jobs:

    * ``analysis.accuracy_floor`` **reports**. It ships unset, and when an operator sets it
      the run raises one non-blocking finding per side saying that everything below it says
      less than it appears to. It never blocks and never silences anything.
    * this key **refuses**. It ships at 0.75, and below it the three dead-code rules and the
      pass-through rule evaluate nothing and say so once per run.

    **Why not one key.** Sharing would make the safety floor a side effect of tuning a report.
    An operator whose third-party headers do not resolve lowers the reporting floor to stop
    the warning nagging -- and would thereby unlock the dead-code rules at that accuracy,
    which is exactly the configuration this repository's own measurement was taken in: at 19%
    accuracy, every one of the sixteen module bindings the snapshot answers
    ``referenced: false`` for is in fact read, a hundred per cent false-positive rate. A
    number whose job is to refuse must not be movable by someone who believes they are
    silencing a report. Merging the other way is no better: giving ``analysis.accuracy_floor``
    the default 0.75 would start a new warning on every repository below it, changing the
    shipped behaviour of a feature that is deliberately unset (understand-8-features req 7.3).

    **What stops a reader setting the wrong one.** Each lives in the section that names its
    subject -- ``[analysis]`` reports on the analysis, ``[lean]`` configures the lean rules --
    each docstring names the other and says which job it does, and setting either one is
    pinned as changing nothing the other governs. This family's recurring defect is two
    artefacts that must agree with nothing binding them; these two must *differ*, so the
    binding artefact is the pair of tests that fail if one ever starts reading the other.
    """

    pass_through: Severity | None = None
    # At most two statements, and two means a body of ONE: measured on 2026-09-11 (research.md,
    # task 6.4), Understand's `CountStmt` counts the `def` line, so `return callee(x)` scores 2
    # and no routine scores 1 -- of the 274 routines here and 459 on facdrone with one project
    # caller and one project callee, 56 and 86 are at 2 and none below. Requirement 2.2 draws
    # the line at "a routine with one caller and a body of its own is a decomposition the
    # Gate's own hints ask for", and 2 is that line; a guard clause would need 3. The number
    # is confirmed and is not what makes the rule noisy: with the floors at zero its first
    # ten held one forwarder here and two on facdrone, the rest one-line comprehensions and
    # expressions holding a single project call, which score 2 exactly as a forwarder does.
    # That is the predicate -- "one call edge and no more than two statements" -- and it is
    # the rule's to tighten, not this key's.
    pass_through_max_statements: int = Field(default=2, ge=1)
    pass_through_ignore: list[str] = Field(default_factory=lambda: list(DEFAULT_UNUSED_IGNORE))
    single_implementation: Severity | None = None
    single_implementation_ignore: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LEAN_IMPLEMENTATION_IGNORE)
    )
    over_export: Severity | None = None
    over_export_ignore: list[str] = Field(
        default_factory=lambda: list(DEFAULT_LEAN_OVER_EXPORT_IGNORE)
    )
    duplicates: Severity | None = None
    # Confirmed on two repositories on 2026-09-11; the whole-list count is in the class docstring.
    duplicates_min_lines: int = Field(default=12, ge=3)
    duplicates_ignore: list[str] = Field(default_factory=list)
    similar_routines: Severity | None = None
    # Both confirmed on two repositories on 2026-09-11: no noise in either first ten, see above.
    similar_min_statements: int = Field(default=6, ge=2)
    similar_threshold: float = Field(default=0.9, gt=0.0, le=1.0)
    # Requirement 5.9's family minimum. Two, so a plain twin is still a family and nothing the
    # pair-shaped rule would have found is lost by reporting families instead; `ge=2` because
    # a family of one is a routine similar to nothing, which is every routine in the project.
    similar_min_family: int = Field(default=2, ge=2)
    similar_ignore: list[str] = Field(default_factory=list)
    similar_name_ignore: list[str] = Field(default_factory=lambda: list(DEFAULT_LEAN_FAMILY_IGNORE))
    # The statement floor `LinesPerStatement` is judged above (requirement 6.3 asks for a
    # *configurable* minimum, and a constant would leave it unsatisfied). It lives here rather
    # than in `[thresholds.routine]` because it is not a limit on anything: it says which
    # routines the ratio is a statement about at all. The default is the declaration's own --
    # `SYNTHETIC_METRICS["LinesPerStatement"].floor` -- measured on this repository, where the
    # 2 851 routines of at least five statements score p50 1.20, p90 2.00, p95 2.29, max 6.6,
    # while below five the ratio is arithmetic: a two-statement routine spread over six lines
    # scores 3.0 and outranks a genuinely long one. `ge=1` because a floor of zero would judge
    # a routine with no statements at all, whose ratio is undefined rather than large.
    verbosity_min_statements: int = Field(default=DEFAULT_FLOOR_MINIMUM, ge=1)
    # A maximum on the net LLOC delta, off by default: requirement 7.5 says the delta never
    # blocks unless an operator asks it to. ``0`` is a legal maximum and means "this change
    # may not make the project longer", which is why the bound is ``ge=0`` and not ``ge=1``.
    max_net_growth: int | None = Field(default=None, ge=0)
    net_growth_severity: Severity = "warning"

    @field_validator(
        "unused_parameters_ignore",
        "unused_classes_ignore",
        "unused_variables_ignore",
        "pass_through_ignore",
        "single_implementation_ignore",
        "similar_name_ignore",
    )
    @classmethod
    def _name_patterns_compile(cls, patterns: list[str]) -> list[str]:
        return compile_patterns(patterns)

    @field_validator("over_export_ignore", "duplicates_ignore", "similar_ignore")
    @classmethod
    def _path_patterns_are_usable(cls, patterns: list[str]) -> list[str]:
        """Prove each path pattern says something, in the glob language ``[project]`` speaks.

        These three lists name *files*, not entities, so they are compiled by
        ``compile_path_pattern`` rather than as regular expressions -- writing ``.*\\.py$``
        here would be a literal that matches nothing. A glob cannot fail to compile (every
        character but ``*`` and ``?`` is escaped), so the failure worth catching is the blank
        entry: ``matching_pattern`` skips it, which makes it a line that looks like an
        exclusion and excludes nothing.
        """
        for pattern in patterns:
            if not pattern.strip():
                raise ValueError("a blank path pattern ignores nothing; remove it")
            compile_path_pattern(pattern)
        return patterns

    @property
    def wants_references(self) -> bool:
        """Whether any rule needing the per-entity reference walk is on (req 9.4).

        **Five rules, and ``over_export`` is deliberately not one of them.** Over-export is
        answered from file metrics, ``file_edges`` and the definitions walk the snapshot
        already carries, so it needs no ``refs`` call on any entity and has no feature probe
        of its own; what it turns on is the fingerprint's ``definitions`` key, next to
        ``structure.duplicate_definitions``. Adding it here would make an over-export-only
        configuration pay for a reference call on every recorded entity and read none of the
        answers, which is the cost requirement 9.4 forbids while a rule is off and
        requirement 9.5 puts a ceiling on when one is on.

        The names are read from :data:`REFERENCE_RULES` rather than written out here, because
        an earlier draft of the design said six in one place and five in another and the two
        must stay in agreement or the extractor pays for the difference. That list is also
        what ``understand.features`` maps to the build capability these five need, so the
        question is asked in one place and answered in one place.
        """
        return any(getattr(self, rule) is not None for rule in REFERENCE_RULES)

    @property
    def wants_tokens(self) -> bool:
        """Whether either rule answered from the token index is on (req 9.4).

        The names come from :data:`TOKEN_RULES` rather than being written out here, for the
        reason :attr:`wants_references` reads its own list: that constant is also what
        ``understand.features`` maps to the build capability these two need, so the question
        is asked in one place and answered in one place.
        """
        return any(getattr(self, rule) is not None for rule in TOKEN_RULES)


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
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    scope: dict[str, PathScope] = Field(default_factory=dict)
    parse: ParseSettings = Field(default_factory=ParseSettings)
    lean: LeanRules = Field(default_factory=LeanRules)

    @field_validator("thresholds", mode="before")
    @classmethod
    def _flatten_threshold_tables(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return threshold_entries(value)
        return value

    @property
    def wants_definitions(self) -> bool:
        """Whether any rule needs the worker's module-level definitions walk.

        Three rules do, and they live in different sections, which is why this sits on
        ``Settings`` rather than beside ``LeanRules.wants_references`` and ``wants_tokens``:
        ``structure.duplicate_definitions`` compares one value written out in many files,
        ``lean.over_export`` needs the same walk to tell "one routine and nothing else" from
        "one routine beside a module constant", and ``lean.unused_variables`` reads the
        ``referenced`` flag the walk writes on each binding, so without it that rule has
        nothing to judge.

        **The third was missing, and task 6.1 measured what that costs.** With
        ``unused_variables`` on and neither of the other two, the request asked for no
        definitions, the snapshot carried an empty list, and the rule reported nothing -- and
        said nothing, because an empty list is indistinguishable from a project without
        module bindings, so its unavailable-note guard saw no ``None`` to object to. On the
        contract project that is both planted cases lost in silence, which is exactly the
        failure requirement 1.6 exists to make audible. The rule being in
        :data:`REFERENCE_RULES` was no protection: that list turns on the per-entity walk,
        and the definitions walk is a different question.

        **It is a property rather than an expression at each site because the two sites
        disagreeing is a defect this project has already shipped.** The extractor builds the
        request and the fingerprint decides whether a cached snapshot still describes the
        project; with the fingerprint keyed on the disjunction and the request keyed on the
        duplicate rule alone, an over-export-only configuration got a *fresh* snapshot with no
        definitions in it, the rule's module-level-binding guard saw nothing, and every
        candidate file with a constant beside its one routine became a finding. Written out
        twice, the two agreed on the day they were written and nothing afterwards; a seventh
        rule needing the walk would have to be remembered in both places. Read from here, it
        cannot be remembered in only one.
        """
        return (
            self.structure.duplicate_definitions is not None
            or self.lean.over_export is not None
            or self.lean.unused_variables is not None
        )


class Provenance(StrictModel):
    """Dotted key -> source: ``default``, ``user:<path>``, ``repo:<path>``, ``env:<VAR>``, ``cli``.

    The loader fills one entry per leaf so ``config`` can print where every value came from.
    """

    values: dict[str, str] = Field(default_factory=dict)
