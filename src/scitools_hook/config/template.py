"""Commented TOML rendered by ``scitools-hook init`` and its overwrite guard (req 3.9).

The rendered document parses with ``tomllib`` and validates back into the ``Settings`` it
was rendered from (thresholds are grouped by scope in ``SCOPES`` order). Optional settings
without a value are written as commented examples so the file documents itself.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.metric_names import SCOPES
from scitools_hook.config.models import (
    BaselineSettings,
    CodeCheckSettings,
    CouplingRule,
    IgnoreRules,
    LayerRule,
    OutputSettings,
    ProjectSettings,
    RatchetSettings,
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
        ],
        [home, _line("db_location", cfg.db_location), _line("api_mode", cfg.api_mode)],
    )


def _project(cfg: ProjectSettings) -> str:
    languages = (
        _line("languages", cfg.languages)
        if cfg.languages is not None
        else '# languages = ["Python", "C++"]  # default: detected from file types'
    )
    return _section(
        "[project]",
        ["Glob patterns (relative to the repository root) selecting files for analysis."],
        [_line("include", cfg.include), _line("exclude", cfg.exclude), languages],
    )


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
    return _section(
        "[structure]",
        [
            "Architecture nodes come from Understand; the directory tree at `depth` by default.",
            "Cycle, fan and new-dependency rules each carry an error/warning severity.",
        ],
        [
            _line("architecture", cfg.architecture),
            _line("depth", cfg.depth),
            _line("file_cycles", cfg.file_cycles),
            _line("arch_cycles", cfg.arch_cycles),
            new_deps,
            _line("new_dependencies_severity", cfg.new_dependencies_severity),
            _line("fan_severity", cfg.fan_severity),
        ],
    )


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


def _commented(lines: list[str]) -> list[str]:
    return [f"# {text}" for text in lines]


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


def render_template(settings: Settings | None = None) -> str:
    """Render ``settings`` (default: the built-in defaults) as a commented TOML document."""
    cfg = settings if settings is not None else default_settings()
    parts = [
        _HEADER,
        _understand(cfg.understand),
        _project(cfg.project),
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
    ]
    return "\n".join(parts)


def write_template(path: Path, *, force: bool = False) -> Path:
    """Write the template to ``path``; refuse to overwrite an existing file unless ``force``."""
    if path.exists() and not force:
        raise ConfigError(
            f"configuration file {path} already exists",
            file=path,
            hint="pass --force to overwrite it",
        )
    path.write_text(render_template(), encoding="utf-8")
    return path
