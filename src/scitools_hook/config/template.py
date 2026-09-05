"""Commented TOML rendered by ``scitools-hook init`` and its overwrite guard (req 3.9).

The rendered document parses with ``tomllib`` and validates back into the ``Settings`` it
was rendered from (thresholds are grouped by scope in ``SCOPES`` order). Optional settings
without a value are written as commented examples so the file documents itself.

With a :class:`Proposal` -- what ``init --detect`` builds from ``config.detect`` -- the same
renderer writes the same document plus the lines a detection suggests, **each naming the
evidence that produced it**. That is the whole contract of the detection half: an operator
reads a line, reads the file and the text it came from beside it, and decides. Nothing is
applied by being detected, and the two shapes that would change what the Gate refuses -- a
sub-project scope and a ``[parse]`` acknowledgement -- are written commented out.

The house style for those comments is this repository's own ``scitools-hook.toml``: a line
that deviates from the default says what was measured, not that it was decided.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.detect import PARSE_REASONS, Detection, Evidence, Region
from scitools_hook.config.metric_names import SCOPES
from scitools_hook.config.models import (
    AnalysisSettings,
    BaselineSettings,
    CodeCheckSettings,
    CouplingRule,
    IgnoreRules,
    LayerRule,
    OutputSettings,
    ParseSettings,
    PathScope,
    ProjectSettings,
    RatchetSettings,
    ScopeOverride,
    Settings,
    StructureRules,
    ThresholdSpec,
    UnderstandSettings,
)
from scitools_hook.errors import ConfigError

CONFIG_FILENAME: Final = "scitools-hook.toml"
"""Repository-level configuration file name."""

_INLINE_LIST_ITEMS: Final = 4
"""Longer arrays are written one item per line so operators can edit them comfortably."""

_HEADER = """\
# scitools-hook configuration (written by `scitools-hook init`).
# Every value below is the built-in default; delete a key to keep its default, change it to
# override. Precedence: built-in defaults < ~/.config/scitools-hook/config.toml < this file
# < SCITOOLS_HOOK_* environment variables < command-line options.
# Thresholds: `Metric = 10` means at most 10; `Metric = { min = 0.1 }` means at least 0.1;
# add `severity = "warning"` (does not block) or `ratchet = false` (no worse-than-before
# check) inside the table. Metric names are Understand identifiers plus the synthetic
# CountParams and CountDeclMethodNonStub. A stats prefix (AVG, MEDIAN, MEDIANHIGH,
# MEDIANLOW, MEDIANGROUPED, MODE, STDEV, VARIANCE) evaluates the population of the scope.
"""


def _toml_value(value: object) -> str:
    """Render a scalar or list as a TOML value.

    The two shapes are asked separately -- a list is the only recursive case, and keeping it
    out of the scalar ladder is what holds both routines inside this project's own limits
    (one body measured CyclomaticModified 10 against a maximum of 8; task 10.4).
    """
    if isinstance(value, list):
        return _toml_list(value)
    return _toml_scalar(value)


def _toml_list(value: list[object]) -> str:
    """Render a list, inline while it is short enough to read on one line."""
    items = [_toml_value(item) for item in value]
    if len(items) <= _INLINE_LIST_ITEMS:
        return "[" + ", ".join(items) + "]"
    return "[\n" + "".join(f"    {item},\n" for item in items) + "]"


def _toml_scalar(value: object) -> str:
    """Render one scalar; ``bool`` is asked before ``int`` because it is one in Python."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Path):
        return json.dumps(value.as_posix())
    if isinstance(value, str):
        return json.dumps(value)
    raise TypeError(f"cannot render {type(value).__name__} as TOML")


def _key(name: str) -> str:
    bare = name.replace("_", "").replace("-", "")
    return name if bare.isalnum() and bare.isascii() else json.dumps(name)


def _line(name: str, value: object) -> str:
    return f"{_key(name)} = {_toml_value(value)}"


def _inline_table(items: Mapping[str, object]) -> str:
    return "{ " + ", ".join(_line(key, value) for key, value in items.items()) + " }"


def _section(header: str, comments: Iterable[str], lines: Iterable[str]) -> str:
    body = [f"# {text}" for text in comments] + [header, *lines]
    return "\n".join(body) + "\n"


def _understand(cfg: UnderstandSettings) -> str:
    home = _line("home", cfg.home) if cfg.home else '# home = "/opt/scitools"'
    return _section(
        "[understand]",
        [
            "Understand install directory (else --scitools-home, SCITOOLS_HOME, `und` on PATH).",
            'db_location: "cache" (per-user cache dir) or "gitdir" (.git/scitools-hook/).',
            'api_mode: "auto" runs the Python API under Understand\'s upython when present.',
            "sarif = true also writes Understand's own SARIF beside the one --sarif asks for.",
            'before_side: "shadow" exports the base commit; "commit" builds the database from',
            '  it directly (Understand 8.0 only); "auto" takes whichever the build offers.',
            "snapshot_cache reuses an unchanged before side between runs; it changes no finding.",
        ],
        [
            home,
            _line("db_location", cfg.db_location),
            _line("api_mode", cfg.api_mode),
            _line("sarif", cfg.sarif),
            _line("before_side", cfg.before_side),
            _line("snapshot_cache", cfg.snapshot_cache),
        ],
    )


def _project(cfg: ProjectSettings, notes: Mapping[str, str]) -> str:
    languages = (
        _line("languages", cfg.languages)
        if cfg.languages is not None
        else '# languages = ["Python", "C++"]  # default: detected from file types'
    )
    return _section(
        "[project]",
        ["Glob patterns (relative to the repository root) selecting files for analysis."],
        [_line("include", cfg.include), _exclude_line(cfg.exclude, notes), languages],
    )


def _exclude_line(patterns: list[str], notes: Mapping[str, str]) -> str:
    """``exclude``, one pattern per line whenever any of them has evidence to quote.

    A trailing comment inside a TOML array is legal and it is the only place the evidence can
    sit *next to the line it justifies*. Requirement, not decoration: an exclusion an operator
    cannot trace to something they can read is the silent narrowing this whole feature exists
    to avoid.
    """
    annotated = {pattern: notes.get(f"exclude:{pattern}", "") for pattern in patterns}
    if not any(annotated.values()):
        return _line("exclude", patterns)
    items: list[str] = []
    for pattern, note in annotated.items():
        items.extend(f"    # {line}" for line in _wrapped(note) if note)
        items.append(f"    {_toml_scalar(pattern)},")
    return "exclude = [\n" + "\n".join(items) + "\n]"


def _threshold_value(spec: ThresholdSpec) -> str:
    table: dict[str, object] = {}
    if spec.limit.max is not None:
        table["max"] = spec.limit.max
    if spec.limit.min is not None:
        table["min"] = spec.limit.min
    if spec.severity != "error":
        table["severity"] = spec.severity
    if not spec.ratchet:
        table["ratchet"] = False
    if list(table) == ["max"]:
        return _toml_value(spec.limit.max)
    return _inline_table(table)


_SCOPE_NOTES: Final[dict[str, str]] = {
    "routine": "Per function/method; ratcheted against HEAD in staged mode.",
    "class": "Per class/interface/struct; PercentLackOfCohesion is unavailable for Python.",
    "file": "Per file; RatioCommentToCode is a minimum.",
    "project": "Whole project; AVG:/MEDIAN:/... names reduce over the routine population.",
    "arch": "Per architecture node.",
}


def _thresholds(specs: list[ThresholdSpec]) -> str:
    sections = []
    for scope in SCOPES:
        in_scope = [spec for spec in specs if spec.scope == scope]
        if not in_scope:
            continue
        lines = [f"{_key(spec.metric)} = {_threshold_value(spec)}" for spec in in_scope]
        sections.append(_section(f"[thresholds.{scope}]", [_SCOPE_NOTES[scope]], lines))
    return "\n".join(sections)


def _ratchet(cfg: RatchetSettings) -> str:
    return _section(
        "[ratchet]",
        ["strict = true also blocks on violations that already existed before the change."],
        [_line("strict", cfg.strict)],
    )


def _ignore(cfg: IgnoreRules) -> str:
    return _section(
        "[ignore]",
        ["Regular expressions; matching files, classes and routines skip every rule."],
        [
            _line("files", cfg.files),
            _line("classes", cfg.classes),
            _line("routines", cfg.routines),
        ],
    )


def _structure_body(cfg: StructureRules) -> str:
    max_new = cfg.max_new_dependencies_per_file
    new_deps = (
        _line("max_new_dependencies_per_file", max_new)
        if max_new is not None
        else "# max_new_dependencies_per_file = 5  # unset: no limit"
    )
    dupes = (
        _line("duplicate_definitions", cfg.duplicate_definitions)
        if cfg.duplicate_definitions is not None
        else "# duplicate_definitions = 3  # unset: off, and costs no analysis"
    )
    return _section(
        "[structure]",
        [
            "Architecture nodes come from Understand; the directory tree at `depth` by default.",
            "Cycle, fan and new-dependency rules each carry an error/warning severity.",
            "duplicate_definitions = N reports a name bound to the SAME value in more than N",
            "  files. Off by default; put the per-module idiom in the ignore list.",
        ],
        [
            _line("architecture", cfg.architecture),
            _line("depth", cfg.depth),
            _line("file_cycles", cfg.file_cycles),
            _line("arch_cycles", cfg.arch_cycles),
            new_deps,
            _line("new_dependencies_severity", cfg.new_dependencies_severity),
            _line("fan_severity", cfg.fan_severity),
            dupes,
            _line("duplicate_definitions_severity", cfg.duplicate_definitions_severity),
            _line("duplicate_definitions_ignore", cfg.duplicate_definitions_ignore),
            _unused(cfg),
            _line("unused_ignore", cfg.unused_ignore),
            _architecture_options(cfg),
        ],
    )


def _unused(cfg: StructureRules) -> str:
    """``unused_routines`` off by default; enabling it is choosing a severity."""
    if cfg.unused_routines is None:
        return '# unused_routines = "warning"  # unset: off. Reports routines nothing references'
    return _line("unused_routines", cfg.unused_routines)


def _architecture_options(cfg: StructureRules) -> str:
    """Options for a generated architecture, by the names Understand shows in its dialog."""
    if not cfg.architecture_options:
        return '# architecture_options = { "Date Relative to" = "Most Recent Commit" }'
    return f"architecture_options = {_inline_table(dict(cfg.architecture_options))}"


def _fan(cfg: StructureRules) -> str:
    lines = [
        f"{key} = {_inline_table(limit.model_dump(exclude_none=True))}"
        for key, limit in cfg.fan.items()
    ]
    return _section(
        "[structure.fan]",
        ["Dependents (fan_in) and dependencies (fan_out) per file and class."],
        lines or ["# file_fan_out = { max = 20 }"],
    )


def _layers(rules: list[LayerRule]) -> str:
    comments = ["Allowed dependency directions between architecture nodes."]
    if not rules:
        example = LayerRule(
            name="cli", node="Directory Structure/src/cli", may_depend_on=["runner"]
        )
        return _section("# [[structure.layers]]", comments, _commented(_layer_lines(example)))
    return "".join(_section("[[structure.layers]]", comments, _layer_lines(r)) for r in rules)


def _layer_lines(rule: LayerRule) -> list[str]:
    return [
        _line("name", rule.name),
        _line("node", rule.node),
        _line("may_depend_on", rule.may_depend_on),
        _line("severity", rule.severity),
    ]


def _coupling(rules: list[CouplingRule]) -> str:
    comments = ["Maximum references between two architecture nodes."]
    if not rules:
        example = CouplingRule(
            from_node="Directory Structure/src", to_node="Directory Structure/lib", max_refs=50
        )
        return _section("# [[structure.coupling]]", comments, _commented(_coupling_lines(example)))
    return "".join(_section("[[structure.coupling]]", comments, _coupling_lines(r)) for r in rules)


def _coupling_lines(rule: CouplingRule) -> list[str]:
    return [
        _line("from_node", rule.from_node),
        _line("to_node", rule.to_node),
        _line("max_refs", rule.max_refs),
        _line("severity", rule.severity),
    ]


def _commented(lines: Sequence[str]) -> list[str]:
    return [f"# {text}" for text in lines]


_COMMENT_WIDTH: Final = 94
"""Where an evidence comment wraps, so a generated file stays inside this project's own limit."""


def _wrapped(text: str) -> list[str]:
    """One comment split over as many lines as it needs; never an empty list."""
    return textwrap.wrap(text, width=_COMMENT_WIDTH) or [text]


def _codecheck(cfg: CodeCheckSettings) -> str:
    config = _line("config", cfg.config) if cfg.config else '# config = "MyCodeCheckConfig"'
    return _section(
        "[codecheck]",
        ["Name of an Understand CodeCheck configuration to run on staged files (optional)."],
        [config, _line("severity", cfg.severity)],
    )


def _baseline(cfg: BaselineSettings) -> str:
    return _section(
        "[baseline]",
        ["adaptive = true uses min(configured limit, recorded baseline) and tightens over time."],
        [_line("file", cfg.file), _line("adaptive", cfg.adaptive)],
    )


def _hints(hints: dict[str, str]) -> str:
    lines = [_line(rule, text) for rule, text in hints.items()]
    return _section(
        "[hints]",
        ['Remediation hint overrides keyed by rule name, e.g. "routine.MaxNesting".'],
        lines or ['# "routine.MaxNesting" = "Extract the inner block or use guard clauses."'],
    )


def _output(cfg: OutputSettings) -> str:
    return _section(
        "[output]",
        ["Review-aid sizing: graphs per explain run, impact depth, highest-value listing."],
        [
            _line("graphs_max", cfg.graphs_max),
            _line("impact_depth", cfg.impact_depth),
            _line("show_highest", cfg.show_highest),
        ],
    )


# --- a configuration a detection proposes ------------------------------------------

PROPOSED_TEST_THRESHOLDS: Final[dict[str, dict[str, object]]] = {
    "routine": {"CyclomaticStrict": 15, "CountLineCode": 120},
    "file": {"CountDeclFunction": False},
}
"""Starting numbers for a detected test tree. **Proposals, not measurements.**

They are written into the generated file as real values because an operator has to be able to
see and edit them, and the comment above them says what they are: a fixture branches more than
the code it sets up, and a test file is many small tests rather than one large module, so the
routine limits go up and the per-file function count comes off. Nobody has measured *this*
repository's test tree to get them -- the operator is expected to.

A test tree gets a scope and never an exclusion. Task 10.4 refused a blanket ``tests/**``
ignore that would have removed 65 findings in one regular expression, because the same regular
expression also hid a 2598-line test module; a scope keeps the module visible and judges it by
numbers that say what it is.
"""

_EXCLUDED_ROLES: Final = ("vendored", "generated")
"""The roles a detection proposes as ``[project] exclude`` lines: code nobody here wrote."""


@dataclass(frozen=True, slots=True)
class Proposal:
    """A configuration a detection suggests, and the evidence behind every line it adds.

    ``settings`` is a normal :class:`Settings`, so the same renderer produces it; ``notes``
    carries the comment that goes above or beside each line the detection is responsible for,
    keyed ``exclude:<pattern>`` and ``scope:<name>``; ``suggestions`` holds the blocks that are
    written **commented out**, because acting on them is a decision only an operator can take.
    """

    settings: Settings
    notes: dict[str, str] = field(default_factory=dict)
    suggestions: tuple[str, ...] = ()


def propose(detection: Detection, base: Settings | None = None) -> Proposal:
    """Turn a detection into a configuration to review -- never one to apply unread.

    Three shapes come out, and which is which is the whole of the policy:

    * a **vendored** or **generated** region with tracked files under it becomes a
      ``[project] exclude`` line, with the declaration that produced it beside it;
    * a **tests** region becomes a ``[scope.<name>]`` with its own thresholds, never an
      exclusion;
    * a **sub-project** and every acknowledged-parse candidate is written **commented out**.
      A manifest says a project starts somewhere; it does not say whose code it is, and
      guessing is what this module exists to replace. An acknowledgement stops a file blocking
      a commit, which is not a thing detection may decide.

    A declaration covering no tracked file produces no line at all. It is real and it is
    reported by ``config --detect``, but a repository that ``.gitignore``s the tree it declares
    generated -- the shape measured on the repository this was built against -- would otherwise
    collect an exclusion that can never match anything.
    """
    settings = (base if base is not None else default_settings()).model_copy(deep=True)
    notes: dict[str, str] = {}
    _propose_excludes(settings, detection, notes)
    _propose_test_scopes(settings, detection, notes)
    suggestions = (*_subproject_suggestions(detection), *_parse_suggestions(detection))
    return Proposal(settings=settings, notes=notes, suggestions=suggestions)


def _propose_excludes(settings: Settings, detection: Detection, notes: dict[str, str]) -> None:
    """Add one exclusion per vendored or generated region that covers tracked files."""
    for role in _EXCLUDED_ROLES:
        for region in detection.with_role(role):
            if region.covered and region.pattern not in settings.project.exclude:
                settings.project.exclude.append(region.pattern)
                notes[f"exclude:{region.pattern}"] = _evidence_note(region)


def _propose_test_scopes(settings: Settings, detection: Detection, notes: dict[str, str]) -> None:
    """Give every detected test tree a scope of its own, with the proposed thresholds."""
    for region in detection.with_role("tests"):
        if not region.covered or region.name in settings.scope:
            continue
        settings.scope[region.name] = PathScope.model_validate(
            {"paths": [region.pattern], "thresholds": PROPOSED_TEST_THRESHOLDS}
        )
        notes[f"scope:{region.name}"] = _evidence_note(region)


def _evidence_note(region: Region) -> str:
    """The comment beside a generated line: what was read, and how much it covers."""
    files = "1 tracked file" if region.covered == 1 else f"{region.covered} tracked files"
    return f"evidence: {region.evidence.describe()} ({files})"


def _subproject_suggestions(detection: Detection) -> tuple[str, ...]:
    """A commented ``[scope]`` per sub-project, for an operator who wants one."""
    regions = [
        region
        for region in detection.regions
        if region.evidence.signal == "subproject" and region.covered
    ]
    if not regions:
        return ()
    blocks = [
        _section(
            f"# [scope.{_key(region.name)}]",
            [
                *_wrapped(_evidence_note(region)),
                "a separate project; give it thresholds of its own here, or exclude it",
            ],
            [f"# paths = [{_toml_scalar(region.pattern)}]"],
        )
        for region in regions
    ]
    return tuple(blocks)


def _parse_suggestions(detection: Detection) -> tuple[str, ...]:
    """A commented ``[parse]`` acknowledgement for every file Understand cannot finish.

    Written commented, and that is the point: uncommenting it is the operator deciding that a
    partially measured file may be committed. Detection can say *which* files and *why*; it
    must not be what stops them blocking.
    """
    grouped = _limitations_by_signal(detection)
    if not grouped:
        return ()
    lines = [
        "# --- files Understand could not read -----------------------------------------",
        *_commented(_PARSE_HELP),
    ]
    for signal, items in grouped.items():
        lines.extend(_acknowledgement_block(signal, items))
    return ("\n".join(lines) + "\n",)


def _limitations_by_signal(detection: Detection) -> dict[str, list[Evidence]]:
    """The unreadable files grouped by *what it cost*, which is what a reason has to say."""
    grouped: dict[str, list[Evidence]] = {}
    for item in detection.limitations:
        if item.signal in PARSE_REASONS:
            grouped.setdefault(item.signal, []).append(item)
    return grouped


def _acknowledgement_block(signal: str, items: Sequence[Evidence]) -> list[str]:
    """One commented ``[[parse.acknowledged]]``: the reason, the files, and where in each."""
    reason = PARSE_REASONS[signal]
    return [
        "#",
        *_commented(_wrapped(reason)),
        *(f"#   {item.source}  ({item.detail})" for item in items),
        "#",
        "# [[parse.acknowledged]]",
        f"# reason = {_toml_scalar(reason)}",
        "# paths = [",
        *(f"#     {_toml_scalar(item.source)}," for item in items),
        "# ]",
    ]


# --- path scopes and acknowledged parse limitations --------------------------------


def _scopes(scopes: Mapping[str, PathScope], notes: Mapping[str, str]) -> str:
    """One ``[scope.<name>]`` block per configured scope, evidence first.

    An empty configuration writes the shape as a commented example, like every other optional
    section, so the file says what a scope is without anyone having to look it up.
    """
    if not scopes:
        return _section("# [scope.tests]", _SCOPE_HELP, _commented(_EXAMPLE_SCOPE))
    blocks = [_one_scope(name, scope, notes.get(f"scope:{name}")) for name, scope in scopes.items()]
    return "".join(blocks)


def _one_scope(name: str, scope: PathScope, note: str | None) -> str:
    """One scope: its paths, then a threshold table per threshold scope it overrides."""
    comments = [*_SCOPE_HELP]
    if note:
        comments[:0] = _wrapped(note)
        comments.extend(_wrapped(_PROPOSED_NUMBERS))
    head = _section(f"[scope.{_key(name)}]", comments, [_line("paths", scope.paths)])
    tables = [
        _section(
            f"[scope.{_key(name)}.thresholds.{threshold_scope}]",
            [_SCOPE_NOTES[threshold_scope]],
            [f"{_key(metric)} = {_scope_override(override)}" for metric, override in table.items()],
        )
        for threshold_scope, table in scope.thresholds.items()
    ]
    return "\n".join([head, *tables])


def _scope_override(override: ScopeOverride) -> str:
    """One scope threshold: ``false``, a bare maximum, or the table that says more."""
    if override.disabled:
        return "false"
    table: dict[str, object] = {}
    if override.limit is not None:
        if override.limit.max is not None:
            table["max"] = override.limit.max
        if override.limit.min is not None:
            table["min"] = override.limit.min
    if override.severity is not None:
        table["severity"] = override.severity
    if override.ratchet is not None:
        table["ratchet"] = override.ratchet
    if list(table) == ["max"]:
        return _toml_value(table["max"])
    return _inline_table(table)


def _parse(cfg: ParseSettings, suggested: bool = False) -> str:
    """``[parse]``: the files whose unreadability the operator has acknowledged.

    ``suggested`` says a detection has already written a block for this repository, in which
    case the generic example is left out: two commented ``[[parse.acknowledged]]`` blocks in
    one file is an invitation to uncomment the wrong one.
    """
    if not cfg.acknowledged:
        if suggested:
            return ""
        return _section("# [[parse.acknowledged]]", _PARSE_HELP, _commented(_EXAMPLE_PARSE))
    blocks = [
        _section(
            "[[parse.acknowledged]]",
            _PARSE_HELP,
            [_line("paths", entry.paths), _line("reason", entry.reason)],
        )
        for entry in cfg.acknowledged
    ]
    return "".join(blocks)


_SCOPE_HELP: Final[tuple[str, ...]] = (
    "A named region with thresholds of its own. `paths` are globs from the repository root;",
    "a scope changes the numbers a file is judged by and NEVER removes it from the analysis.",
    "Precedence: built-in defaults < user file < this file < environment < CLI < scope.",
    "`Metric = false` switches a rule off for the region. Two scopes matching one file both",
    "apply, in the order they appear here, and the later one wins per rule.",
)

_PROPOSED_NUMBERS: Final = (
    "the numbers below are a starting point, not a measurement of this repository -- review them"
)

_EXAMPLE_SCOPE: Final[tuple[str, ...]] = (
    'paths = ["tests/**"]',
    "[scope.tests.thresholds.routine]",
    "CyclomaticStrict = 15",
    "[scope.tests.thresholds.file]",
    "CountDeclFunction = false",
)

_PARSE_HELP: Final[tuple[str, ...]] = (
    "Files Understand cannot read to the end. An acknowledged file is still analysed, still",
    "reported and still named -- it stops BLOCKING the commit, and nothing else. It is not",
    "'checked and clean': it is checked up to the construct that stopped the parse.",
    "`reason` is required, and is what the report quotes.",
)

_EXAMPLE_PARSE: Final[tuple[str, ...]] = (
    'paths = ["src/pkg/generic.py"]',
    'reason = "PEP 695 type parameters; Understand 6.5 stops at the declaration."',
)


def render_template(settings: Settings | None = None, *, proposal: Proposal | None = None) -> str:
    """Render a configuration as a commented TOML document.

    With neither argument the built-in defaults are rendered. With a ``proposal`` -- what
    ``init --detect`` builds -- its settings are rendered with the evidence beside every line
    the detection added, and its commented suggestions are appended.
    """
    cfg = _rendered_settings(settings, proposal)
    notes = proposal.notes if proposal is not None else {}
    parts = [
        _HEADER,
        _understand(cfg.understand),
        _project(cfg.project, notes),
        _thresholds(cfg.thresholds),
        _ratchet(cfg.ratchet),
        _ignore(cfg.ignore),
        _structure_body(cfg.structure),
        _fan(cfg.structure),
        _layers(cfg.structure.layers),
        _coupling(cfg.structure.coupling),
        _codecheck(cfg.codecheck),
        _baseline(cfg.baseline),
        _hints(cfg.hints),
        _output(cfg.output),
        _analysis(cfg.analysis),
        _scopes(cfg.scope, notes),
        _parse(cfg.parse, suggested=_has_parse_suggestion(proposal)),
        *(proposal.suggestions if proposal is not None else ()),
    ]
    return "\n".join(part for part in parts if part)


def _has_parse_suggestion(proposal: Proposal | None) -> bool:
    """Whether a proposal already carries a commented ``[parse]`` block for this repository."""
    return proposal is not None and any(
        "[[parse.acknowledged]]" in block for block in proposal.suggestions
    )


def _analysis(cfg: AnalysisSettings) -> str:
    """The accuracy floor: unset by default, and never a blocking finding when it is set."""
    floor = (
        _line("accuracy_floor", cfg.accuracy_floor)
        if cfg.accuracy_floor is not None
        else "# accuracy_floor = 0.8  # unset: off"
    )
    return _section(
        "[analysis]",
        [
            "accuracy_floor is the share of files Understand parsed with no error or warning,",
            "  as `und analyze -accuracy` reports it. Below the floor the run says so and",
            "  carries on: a poorly resolved analysis is news, not a reason to refuse a commit.",
        ],
        [floor],
    )


def _rendered_settings(settings: Settings | None, proposal: Proposal | None) -> Settings:
    """Which settings a render is about: the explicit ones, the proposal's, or the defaults."""
    if settings is not None:
        return settings
    if proposal is not None:
        return proposal.settings
    return default_settings()


def write_template(path: Path, *, force: bool = False, proposal: Proposal | None = None) -> Path:
    """Write the template to ``path``; refuse to overwrite an existing file unless ``force``."""
    if path.exists() and not force:
        raise ConfigError(
            f"configuration file {path} already exists",
            file=path,
            hint="pass --force to overwrite it",
        )
    path.write_text(render_template(proposal=proposal), encoding="utf-8")
    return path


# --- a configuration a measurement proposes ----------------------------------------

RECOMMEND_HEADER: Final[tuple[str, ...]] = (
    "scitools-hook recommend -- limits measured against the shape of this repository.",
    "",
    "THIS IS NOT A BASELINE, and the two answer different questions:",
    "  `baseline` records WHERE YOU ARE -- today's worst value per rule, so existing debt",
    "    reports as pre-existing and nothing gets worse. It moves every time the code does.",
    "  `recommend` says WHERE TO AIM -- a ceiling chosen to contain the bulk of this",
    "    repository, leaving the entities outside it as work to do. It is not written",
    "    anywhere, it is not applied, and it never lowers a limit you already hold.",
    "",
    "Every line below deviates from what is currently in force, and carries the measurement",
    "that produced it. Paste only the ones you agree with; a limit nobody agreed to is a",
    "limit somebody will delete.",
)
"""The block above a recommended configuration. It leads with what this is *not* on purpose.

An operator who pastes a recommendation believing it is a baseline gets a gate that reports
nothing today and blocks the first commit that touches the tail; one who runs ``baseline``
believing it is a recommendation freezes their worst routine as the limit. The two failures
are symmetrical and both are silent, so the distinction is stated in the artefact itself
rather than only in the command that produced it.
"""

RECOMMEND_NOTHING: Final[tuple[str, ...]] = (
    "Nothing to paste: every configured ceiling already contains at least the target share of",
    "this repository, so the numbers in force fit it and this run proposes no change.",
    "That is the expected answer for a repository whose limits were chosen well -- a tool that",
    "always proposes a change is a tool nobody trusts. The evidence for each one, and the cost",
    "of holding a tighter line, is in the report this block came with.",
)
"""What a run with no deviations writes. It says why silence is an answer, not an omission."""


@dataclass(frozen=True, slots=True)
class RecommendedThreshold:
    """One threshold a measurement proposes, and the one-line measurement behind it.

    Deliberately a plain record and deliberately declared *here*: ``config`` is the bottom
    layer and may import nothing above itself, so the analysis that measures a repository
    builds these and hands them down. That keeps one renderer for the file this project
    writes, instead of a second one in ``analysis`` that would drift from this module's
    quoting, key escaping and comment width.
    """

    scope: str
    metric: str
    limit: float
    evidence: str


def render_recommendation(
    items: Sequence[RecommendedThreshold], measured: str, target: float
) -> str:
    """The pasteable ``[thresholds.<scope>]`` tables, deviations only, evidence per line.

    ``measured`` is what the run covered (``7429 routines, 1345 classes, 770 files``) and
    ``target`` the coverage share the proposals were chosen against; both go in the header so
    a block pasted into a file six months from now still says what it was derived from.

    An empty ``items`` renders the header and :data:`RECOMMEND_NOTHING` rather than nothing at
    all. A command that printed an empty string when every limit already fits would leave an
    operator unable to tell "the defaults are right" from "the run failed to measure".
    """
    preamble = [
        *RECOMMEND_HEADER,
        "",
        f"Measured {measured}; a ceiling is reported as fitting when it contains "
        f"{target:.0%} of its population.",
    ]
    if not items:
        return _commented_block([*preamble, "", *RECOMMEND_NOTHING])
    body = [_commented_block(preamble)]
    body.extend(
        _recommended_table(scope, [item for item in items if item.scope == scope])
        for scope in SCOPES
        if any(item.scope == scope for item in items)
    )
    return "\n".join(body)


def _commented_block(lines: Sequence[str]) -> str:
    """Comment every line, wrapping the long ones, and keep the blank ones blank."""
    out: list[str] = []
    for line in lines:
        if not line:
            out.append("#")
            continue
        out.extend(f"# {text}" for text in _wrapped(line))
    return "\n".join(out) + "\n"


def _recommended_table(scope: str, items: Sequence[RecommendedThreshold]) -> str:
    """One scope's proposals: the table header, then evidence-and-line for each metric."""
    lines: list[str] = []
    for item in items:
        lines.extend(f"# {text}" for text in _wrapped(item.evidence))
        lines.append(_line(item.metric, item.limit))
    return _section(f"[thresholds.{scope}]", [_SCOPE_NOTES[scope]], lines)
