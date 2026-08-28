"""The three views of one change summary: text, Markdown and JSON (req 9.4, 9.6, 9.8).

:func:`render_summary` is a pure function of a
:class:`~scitools_hook.models.change.ChangeSummary`. It renders the same document three ways
-- for a reviewer at a terminal, for a merge request, and for a machine -- and it reads
nothing else: no file, no database, no environment. Requirement 9.7's architecture path
travels on ``EntityDelta.arch_path`` and requirement 9.8's command on
``ChangeSummary.open_command``, so the renderer never has to reach behind the document to
satisfy either.

The decisions, all of them visible in ``tests/report/test_markdown.py``:

* **The JSON view is the document itself**, ``model_dump_json`` at
  :data:`~scitools_hook.report.json_out.INDENT` spaces and without a trailing newline. It
  imports that constant rather than repeating ``2``, so the tool's two JSON outputs stay
  identical in shape, and the round trip back to an equal ``ChangeSummary`` is a test rather
  than a promise -- exactly the argument :mod:`scitools_hook.report.json_out` makes.
* **Order is the producer's.** Files, dependencies, both rankings and the graph list are
  printed in the order the builder wrote them (task 4.8 sorts every one of them), because
  re-sorting here would hide a producer that emits an unstable order instead of fixing it.
  The one exception is ``impact``, a mapping the model itself writes ordered by
  :attr:`~scitools_hook.models.snapshot.EntityKey.token`; the human views use the same order
  so the three views agree line for line.
* **Dependencies are grouped by the runs of source node the producer already ordered**
  (req 9.2). A dependency whose ``crosses_arch`` is set is marked, naming the node it crosses
  into when the far end has one; a dependency outside every node is listed under
  ``(no architecture node)`` and is never marked -- an unknown boundary is not a crossed one.
* **The metrics shown are the ones that moved**: an entity delta's ``delta`` map, in the order
  the builder sorted it. A row that carries no movement at all -- a ``top_by_value`` entry
  narrowed to a metric that stood still -- falls back to the union of its two sides so the row
  still shows its number instead of coming out empty. A side that does not report a metric
  prints ``-``: that is what an added entity's "before" and a removed entity's "after" are.
* **Impact is counted, not listed** (req 9.5). The human views print the producer's total and
  the count at each depth, ascending, and never the entities themselves: a blast radius is
  unbounded even at depth 1, and a reviewer who wants the names has the JSON view, which
  carries every one of them, and the exported graphs. The total is the producer's, never a
  recount, so a truncated impact set says how big it really was.
* **Numbers** drop the trailing ``.0`` that every integral metric would otherwise carry
  (``40``, not ``40.0``) and deltas keep their sign (``+35``, ``-22``), which also keeps
  ``0.19 - 0.2`` from reaching a reviewer as ``-0.010000000000000009``.
* **Paths are printed exactly as they arrive.** A graph file is a path the reviewer opens on
  this machine, so no separator is normalised and nothing is made repo-relative; likewise
  ``Finding``-style caveats do not apply here, because every path in a summary comes from the
  snapshot's own file set or from the graph exporter.
* **Markdown escapes a pipe in every cell it writes** (``\\|``, which GitLab and GitHub both
  read as a literal pipe, inside a code span as well) and wraps every path, qualified name and
  metric name in a code span, so an underscore or an asterisk in a name cannot re-style the
  table. The text view escapes nothing: a pipe in a path is a fact about the data, not about
  the format. The Markdown view also drops one column the text view keeps -- the node a
  crossing dependency lands in -- because a sixth column makes the table unreadable in a
  merge request; it is marked ``yes``/``no`` there and named in the text view.
* **No colour and no ``rich``.** Like :func:`~scitools_hook.report.human.render_human` this
  returns a plain string with a fixed layout that the CLI must print raw; unlike it, there is
  nothing to colour, so the text view is pure ASCII and survives any console.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Final, Literal, NamedTuple

from scitools_hook.models.change import (
    ChangeSummary,
    DependencyDelta,
    EntityDelta,
    GraphFile,
    ImpactSet,
)
from scitools_hook.models.snapshot import EntityKey
from scitools_hook.report.json_out import INDENT

Format = Literal["text", "markdown", "json"]
"""The three views requirement 9.6 asks for."""

DASH: Final = "-"
"""What a side that does not report a metric, or a fact the document lacks, prints as."""

NO_NODE: Final = "(no architecture node)"
"""Heading for the dependencies whose source file no architecture contains."""

NOTHING: Final = "  none"
"""What an empty section prints in the text view."""

NOTHING_MD: Final = "_None._"
"""What an empty section prints in the Markdown view."""

UNCHANGED_MD: Final = "_No entity changed._"
"""What a file whose entities all stood still prints in the Markdown view."""

_STATUS_WIDTH: Final = 8
"""Width of the status column: ``modified`` is the longest value it takes."""

_SCOPE_WIDTH: Final = 7
"""Width of the scope column: ``routine`` is the longest scope an entity delta carries."""

_DEP_STATUS_WIDTH: Final = 7
"""Width of a dependency's status column: ``removed`` is the longer of the two."""

_GRAPH_WIDTH: Final = 10
"""Width of the graph-kind column: ``Depends On`` is the longer of the two Understand names."""

_RANK_WIDTH: Final = 2
"""Width of a ranking's position, so the default top ten stays aligned."""

_METRIC_COLUMNS: Final = ("Entity", "Scope", "Line", "Status", "Metric", "Before", "After", "Δ")
_DEPENDENCY_COLUMNS: Final = ("Architecture node", "Change", "From", "To", "Crosses")
_DELTA_COLUMNS: Final = ("#", "Entity", "Metric", "Before", "After", "Δ")
_VALUE_COLUMNS: Final = ("#", "Entity", "Metric", "Value")
_IMPACT_COLUMNS: Final = ("Entity", "Total", "By depth")
_GRAPH_COLUMNS: Final = ("Entity", "Graph", "File")


class _MetricRow(NamedTuple):
    """One metric of one entity delta, already rendered: what it was, is, and moved by."""

    metric: str
    before: str
    after: str
    change: str


def render_summary(summary: ChangeSummary, fmt: Format) -> str:
    """Render ``summary`` as text, Markdown or JSON, without a trailing newline (req 9.6)."""
    renderer = _RENDERERS.get(fmt)
    if renderer is None:
        raise ValueError(f"unknown change-summary format: {fmt!r}")
    return renderer(summary)


# --- the text view --------------------------------------------------------------


def _render_text(summary: ChangeSummary) -> str:
    """The terminal view: one indented block per section, the GUI command last (req 9.8)."""
    sections = [
        f"change summary\n  database: {summary.db_path}",
        _text_section("files", len(summary.files), _text_files(summary.files)),
        _text_section(
            "dependencies", len(summary.dependencies), _text_dependencies(summary.dependencies)
        ),
        _text_section(
            "largest deltas", len(summary.top_by_delta), _text_deltas(summary.top_by_delta)
        ),
        _text_section(
            "largest values", len(summary.top_by_value), _text_values(summary.top_by_value)
        ),
        _text_section("impact", len(summary.impact), _text_impact(summary.impact)),
        _text_section("graphs", len(summary.graphs), _text_graphs(summary.graphs)),
        f"open in the Understand GUI: {summary.open_command}",
    ]
    return "\n\n".join(sections)


def _text_section(title: str, count: int, lines: Sequence[str]) -> str:
    """One section: its title with what it counts, then its lines or ``none``."""
    return "\n".join([f"{title} ({count})", *(lines or [NOTHING])])


def _text_files(files: Mapping[str, list[EntityDelta]]) -> list[str]:
    """Every affected file with the entities that moved inside it (req 9.1, 9.7)."""
    lines: list[str] = []
    for path, deltas in files.items():
        lines.append(_text_file_header(path, _arch_of(deltas)))
        if not deltas:
            lines.append("    no entity changed")
        for delta in deltas:
            lines.extend(_text_entity(delta))
    return lines


def _text_file_header(path: str, arch: str | None) -> str:
    """The file and, when an architecture contains it, the node to find it under (req 9.7)."""
    return f"  {path}" if arch is None else f"  {path}  [{arch}]"


def _text_entity(delta: EntityDelta) -> list[str]:
    """One entity: what the change did to it, then one line per metric that moved."""
    status = f"{delta.status:<{_STATUS_WIDTH}}"
    scope = f"{delta.ref.key.scope:<{_SCOPE_WIDTH}}"
    head = f"    {status}  {scope}  {delta.ref.key.longname}"
    if delta.ref.line is not None:
        head = f"{head}  line {delta.ref.line}"
    return [head, *(_text_metric(row) for row in _metric_rows(delta))]


def _text_metric(row: _MetricRow) -> str:
    """``CountLineCode  40 -> 75  (+35)``."""
    return f"      {row.metric}  {row.before} -> {row.after}  ({row.change})"


def _text_dependencies(dependencies: Sequence[DependencyDelta]) -> list[str]:
    """The added and removed dependencies, grouped by source node and marked (req 9.2)."""
    lines: list[str] = []
    heading: str | None = None
    for dependency in dependencies:
        node = dependency.src_node or NO_NODE
        if node != heading:
            heading = node
            lines.append(f"  {node}")
        lines.extend(_text_dependency(dependency))
    return lines


def _text_dependency(dependency: DependencyDelta) -> list[str]:
    """One dependency and, when it crosses an architecture boundary, what it crosses into."""
    status = f"{dependency.status:<{_DEP_STATUS_WIDTH}}"
    lines = [f"    {status}  {dependency.src} -> {dependency.dst}"]
    crossing = _crossing(dependency)
    if crossing:
        lines.append(f"      {crossing}")
    return lines


def _text_deltas(deltas: Sequence[EntityDelta]) -> list[str]:
    """The largest movements, worst first, each showing the metric it ranks on (req 9.3)."""
    return [
        f"  {index:>{_RANK_WIDTH}}. {_located(delta.ref.key)}  {row.metric}  "
        f"{row.before} -> {row.after}  ({row.change})"
        for index, delta, row in _ranked(deltas)
    ]


def _text_values(deltas: Sequence[EntityDelta]) -> list[str]:
    """The largest values the change leaves behind, worst first (req 9.3)."""
    return [
        f"  {index:>{_RANK_WIDTH}}. {_located(delta.ref.key)}  {row.metric}  {row.after}"
        for index, delta, row in _ranked(deltas)
    ]


def _text_impact(impact: Mapping[EntityKey, ImpactSet]) -> list[str]:
    """The blast radius of each modified entity, counted per depth (req 9.5)."""
    return [f"  {_located(key)}  {_blast(value)}" for key, value in _ordered_impact(impact)]


def _text_graphs(graphs: Sequence[GraphFile]) -> list[str]:
    """The exported graphs, as paths a reviewer can open (req 9.4)."""
    return [
        f"  {graph.graph:<{_GRAPH_WIDTH}}  {_located(graph.key)}  {graph.path}" for graph in graphs
    ]


# --- the Markdown view ----------------------------------------------------------


def _render_markdown(summary: ChangeSummary) -> str:
    """The merge-request view: the same facts as tables, the GUI command last (req 9.8)."""
    blocks = [
        f"# Change summary\n\nDatabase: {_code(summary.db_path)}",
        *_md_section("Files", len(summary.files), _md_files(summary.files)),
        *_md_section(
            "Dependencies", len(summary.dependencies), _md_dependencies(summary.dependencies)
        ),
        *_md_section("Largest deltas", len(summary.top_by_delta), _md_deltas(summary.top_by_delta)),
        *_md_section("Largest values", len(summary.top_by_value), _md_values(summary.top_by_value)),
        *_md_section("Impact", len(summary.impact), _md_impact(summary.impact)),
        *_md_section("Graphs", len(summary.graphs), _md_graphs(summary.graphs)),
        f"Open in the Understand GUI: {_code(summary.open_command)}",
    ]
    return "\n\n".join(blocks)


def _md_section(title: str, count: int, blocks: Sequence[str]) -> list[str]:
    """One heading with what it counts, then its blocks or the empty marker."""
    return [f"## {title} ({count})", *(blocks or [NOTHING_MD])]


def _md_files(files: Mapping[str, list[EntityDelta]]) -> list[str]:
    """One sub-heading, architecture path and metric table per affected file (req 9.1, 9.7)."""
    blocks: list[str] = []
    for path, deltas in files.items():
        blocks.append(f"### {_code(path)}")
        arch = _arch_of(deltas)
        if arch is not None:
            blocks.append(f"Architecture: {_code(arch)}")
        blocks.append(_md_file_table(deltas))
    return blocks


def _md_file_table(deltas: Sequence[EntityDelta]) -> str:
    """The entities of one file, one row per moved metric."""
    if not deltas:
        return UNCHANGED_MD
    rows = [_md_metric_row(delta, row) for delta in deltas for row in _metric_rows(delta)]
    return _table(_METRIC_COLUMNS, rows)


def _md_metric_row(delta: EntityDelta, row: _MetricRow) -> list[str]:
    """One entity, one metric: what it was, what it is, and what it moved by."""
    line = DASH if delta.ref.line is None else str(delta.ref.line)
    return [
        _code(delta.ref.key.longname),
        delta.ref.key.scope,
        line,
        delta.status,
        _code(row.metric),
        row.before,
        row.after,
        row.change,
    ]


def _md_dependencies(dependencies: Sequence[DependencyDelta]) -> list[str]:
    """The dependency changes, by architecture node, marked where they cross (req 9.2)."""
    if not dependencies:
        return []
    rows = [
        [
            _code(dependency.src_node or NO_NODE),
            dependency.status,
            _code(dependency.src),
            _code(dependency.dst),
            "yes" if dependency.crosses_arch else "no",
        ]
        for dependency in dependencies
    ]
    return [_table(_DEPENDENCY_COLUMNS, rows)]


def _md_deltas(deltas: Sequence[EntityDelta]) -> list[str]:
    """The largest movements as a ranked table (req 9.3)."""
    if not deltas:
        return []
    rows = [
        [
            str(index),
            _code(_located(delta.ref.key)),
            _code(row.metric),
            row.before,
            row.after,
            row.change,
        ]
        for index, delta, row in _ranked(deltas)
    ]
    return [_table(_DELTA_COLUMNS, rows)]


def _md_values(deltas: Sequence[EntityDelta]) -> list[str]:
    """The largest values the change leaves behind, as a ranked table (req 9.3)."""
    if not deltas:
        return []
    rows = [
        [str(index), _code(_located(delta.ref.key)), _code(row.metric), row.after]
        for index, delta, row in _ranked(deltas)
    ]
    return [_table(_VALUE_COLUMNS, rows)]


def _md_impact(impact: Mapping[EntityKey, ImpactSet]) -> list[str]:
    """The blast radius per entity, counted per depth (req 9.5)."""
    if not impact:
        return []
    rows = [
        [_code(_located(key)), str(value.total), _depths(value) or DASH]
        for key, value in _ordered_impact(impact)
    ]
    return [_table(_IMPACT_COLUMNS, rows)]


def _md_graphs(graphs: Sequence[GraphFile]) -> list[str]:
    """The exported graphs with the entity and kind each belongs to (req 9.4)."""
    if not graphs:
        return []
    rows = [[_code(_located(graph.key)), graph.graph, _code(str(graph.path))] for graph in graphs]
    return [_table(_GRAPH_COLUMNS, rows)]


def _table(columns: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """A GitLab/GitHub table: a header, its rule, and one line per row."""
    lines = [_row(columns), _row([DASH * 3] * len(columns))]
    lines.extend(_row(row) for row in rows)
    return "\n".join(lines)


def _row(cells: Iterable[str]) -> str:
    """One table line; every cell that carries data was escaped by :func:`_code`."""
    return f"| {' | '.join(cells)} |"


def _code(text: str) -> str:
    """A code span, so an underscore or an asterisk in a name cannot re-style the table."""
    return f"`{_cell(text)}`"


def _cell(text: str) -> str:
    """A pipe inside a cell ends the cell unless it is escaped -- inside a code span too."""
    return text.replace("|", "\\|")


# --- the JSON view --------------------------------------------------------------


def _render_json(summary: ChangeSummary) -> str:
    """The machine view: the document itself, shaped like every other JSON the tool emits."""
    return summary.model_dump_json(indent=INDENT)


_RENDERERS: Final[Mapping[str, Callable[[ChangeSummary], str]]] = {
    "text": _render_text,
    "markdown": _render_markdown,
    "json": _render_json,
}


# --- shared -------------------------------------------------------------------


def _located(key: EntityKey) -> str:
    """An entity as a reviewer looks for it: its qualified name and the file it lives in."""
    return key.longname if key.longname == key.path else f"{key.longname} ({key.path})"


def _arch_of(deltas: Sequence[EntityDelta]) -> str | None:
    """The architecture node of a file: every entity it defines inherits it (req 9.7)."""
    return deltas[0].arch_path if deltas else None


def _crossing(dependency: DependencyDelta) -> str:
    """How a dependency crosses an architecture boundary, or nothing when it does not."""
    if not dependency.crosses_arch:
        return ""
    if dependency.dst_node is None:
        return "crosses an architecture boundary"
    return f"crosses into {dependency.dst_node}"


def _ranked(deltas: Sequence[EntityDelta]) -> Iterator[tuple[int, EntityDelta, _MetricRow]]:
    """Every ranked row with its position; the builder narrows an entry to one metric."""
    index = 0
    for delta in deltas:
        for row in _metric_rows(delta):
            index += 1
            yield index, delta, row


def _metric_rows(delta: EntityDelta) -> list[_MetricRow]:
    """The metrics that moved, or -- for a row that carries no movement -- what it holds."""
    metrics = list(delta.delta) or list(dict.fromkeys([*delta.after, *delta.before]))
    return [
        _MetricRow(
            metric=metric,
            before=_number_of(delta.before, metric),
            after=_number_of(delta.after, metric),
            change=_signed(delta.delta[metric]) if metric in delta.delta else "0",
        )
        for metric in metrics
    ]


def _number_of(metrics: Mapping[str, float], metric: str) -> str:
    """One side's value, or ``-`` when that side does not report the metric at all."""
    return _number(metrics[metric]) if metric in metrics else DASH


def _ordered_impact(impact: Mapping[EntityKey, ImpactSet]) -> list[tuple[EntityKey, ImpactSet]]:
    """The impact map in the order the model writes it, so the three views agree."""
    return sorted(impact.items(), key=lambda pair: pair[0].token)


def _blast(impact: ImpactSet) -> str:
    """``5 total; depth 1: 2, depth 2: 3`` -- the producer's total, never a recount."""
    depths = _depths(impact)
    return f"{impact.total} total; {depths}" if depths else f"{impact.total} total"


def _depths(impact: ImpactSet) -> str:
    """How many entities reference the entity at each depth, nearest depth first (req 9.5)."""
    return ", ".join(
        f"depth {depth}: {len(impact.by_depth[depth])}" for depth in sorted(impact.by_depth)
    )


def _number(value: float) -> str:
    """Render a metric value without the trailing ``.0`` most metrics would carry."""
    return f"{value:g}"


def _signed(value: float) -> str:
    """Render a movement with the sign that says which way it went."""
    return f"{value:+g}"
