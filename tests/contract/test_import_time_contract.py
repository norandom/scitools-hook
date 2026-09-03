"""Which file dependencies Understand reports, and which of them an import actually runs.

The gate reported a nine-file dependency cycle on a real 770-file project across two packages
that have no runtime cycle at all: the module closing it imports the other package four times
inside ``if TYPE_CHECKING:`` and twice inside a function body, the second carrying a comment
saying it is written that way to keep the module importable. ``Ent.depends()`` counts every
reference regardless of guard or scope, so nothing in the count could tell the two apart.

This module builds the same shape against the installed Understand and holds three claims to
it, each with the control that stops a fix from being an over-correction:

* a dependency made only of deferred imports is measured at ``import_time`` zero, and no cycle
  is reported through it;
* a dependency made of ordinary module-level imports is measured above zero, and the cycle it
  closes is reported exactly as before -- **the negative control**;
* a C++ file edge is not measured at all, because ``#include`` produces no import reference
  (measured: ``Include``, ``Type``, ``Use``, ``Init``, ``Return``), and an unmeasured edge
  keeps the older behaviour rather than being scored zero.
"""

from __future__ import annotations

import pytest
from contract_project import build_database, extract, write_tree

from scitools_hook.analysis.structure.cycles import at_import_time, find_new_cycles
from scitools_hook.models.snapshot import DepEdge, ProjectSnapshot

pytestmark = pytest.mark.contract

SOURCES: dict[str, str] = {
    # The false positive, reproduced: alpha reaches beta only through imports that the
    # interpreter never runs at module load, and beta imports alpha for real.
    "alpha.py": '''"""Reaches beta only through imports an import of this module never runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from beta import Widget


def make(value):
    # Local import: the standard idiom for breaking an import cycle.
    from beta import build

    return build(value)


def annotate(widget: Widget) -> Widget:
    return widget
''',
    "beta.py": '''"""Imports alpha at module level, which is a real import-time dependency."""

from alpha import make


class Widget(object):
    pass


def build(value):
    return Widget()


def use_alpha(value):
    return make(value)
''',
    # The negative control: an ordinary module-level cycle that must keep being reported.
    "gamma.py": '''"""One half of a real module-level cycle."""

import delta


def g(value):
    return delta.d(value)
''',
    "delta.py": '''"""The other half of the same real cycle."""

import gamma


def d(value):
    return value + 1


def back(value):
    return gamma.g(value)
''',
    "native.h": """#ifndef IMPORT_TIME_NATIVE_H
#define IMPORT_TIME_NATIVE_H
struct Payload {
    int value;
};
int consume(const Payload& p);
#endif
""",
    "native.cpp": """#include "native.h"

int consume(const Payload& p) { return p.value + 1; }
""",
}

FILES: tuple[str, ...] = tuple(sorted(SOURCES))


@pytest.fixture(scope="module")
def imports(tmp_path_factory: pytest.TempPathFactory) -> ProjectSnapshot:
    """The tree above, analysed once and read back through the production extractor."""
    workdir = tmp_path_factory.mktemp("import-time")
    root = write_tree(workdir / "tree", SOURCES)
    database = workdir / "imports.und"
    build_database(database, root)
    return extract(database, root, FILES)


def edge_between(snapshot: ProjectSnapshot, src: str, dst: str) -> DepEdge:
    """The one file edge between two paths; a missing edge fails the test rather than skipping."""
    found = [edge for edge in snapshot.file_edges if edge.src == src and edge.dst == dst]
    assert found, f"no file edge {src} -> {dst} in {[(e.src, e.dst) for e in snapshot.file_edges]}"
    return found[0]


def cycles(snapshot: ProjectSnapshot) -> list[list[str]]:
    """The file cycles the shipped rule reports, as lists of members."""
    findings = find_new_cycles(None, snapshot.file_edges, "error", "file")
    return [sorted(str(name) for name in finding.details["members"]) for finding in findings]


# --- the deferred half ------------------------------------------------------------


def test_a_dependency_made_only_of_deferred_imports_is_measured_at_zero(
    imports: ProjectSnapshot,
) -> None:
    """``alpha`` really does reference ``beta`` -- and importing ``alpha`` does not import it."""
    edge = edge_between(imports, "alpha.py", "beta.py")
    assert edge.refs > 0, "the coupling is real and the reference count must still show it"
    assert edge.import_time == 0


def test_the_module_level_half_of_the_same_pair_is_measured_above_zero(
    imports: ProjectSnapshot,
) -> None:
    edge = edge_between(imports, "beta.py", "alpha.py")
    assert edge.import_time is not None and edge.import_time > 0


def test_no_cycle_is_reported_between_the_deferred_pair(imports: ProjectSnapshot) -> None:
    assert ["alpha.py", "beta.py"] not in cycles(imports)


def test_counting_every_reference_would_have_reported_that_cycle(
    imports: ProjectSnapshot,
) -> None:
    """The defect, reproduced: without the reduction this pair *is* a cycle in the graph.

    Without this the test above would pass on a project that simply has no cycle anywhere, and
    would prove nothing about the reduction.
    """
    unreduced = [edge.model_copy(update={"import_time": None}) for edge in imports.file_edges]
    members = [
        sorted(str(name) for name in finding.details["members"])
        for finding in find_new_cycles(None, unreduced, "error", "file")
    ]
    assert ["alpha.py", "beta.py"] in members


# --- the negative control ---------------------------------------------------------


def test_a_real_module_level_cycle_is_still_reported(imports: ProjectSnapshot) -> None:
    """A fix that silenced this one too would be a worse defect than the one it replaces."""
    assert ["delta.py", "gamma.py"] in cycles(imports)


def test_both_halves_of_the_real_cycle_are_measured_above_zero(
    imports: ProjectSnapshot,
) -> None:
    for src, dst in (("gamma.py", "delta.py"), ("delta.py", "gamma.py")):
        edge = edge_between(imports, src, dst)
        assert edge.import_time is not None and edge.import_time > 0, f"{src} -> {dst}"


def test_exactly_one_file_cycle_survives_the_reduction(imports: ProjectSnapshot) -> None:
    assert cycles(imports) == [["delta.py", "gamma.py"]]


# --- the language guard -----------------------------------------------------------


def test_a_cpp_file_edge_carries_no_import_time_measurement(
    imports: ProjectSnapshot,
) -> None:
    """C++ has no import reference kind at all, so measuring it would score every edge zero."""
    native = [edge for edge in imports.file_edges if edge.src.endswith((".cpp", ".h"))]
    assert native, "the C++ half of the fixture produced no file edge to check"
    assert {edge.import_time for edge in native} == {None}


def test_an_unmeasured_edge_is_kept_by_the_reduction(imports: ProjectSnapshot) -> None:
    """``None`` is not zero: every C++ edge survives into the graph cycles are found in."""
    native = {(e.src, e.dst) for e in imports.file_edges if e.src.endswith((".cpp", ".h"))}
    kept = {(e.src, e.dst) for e in at_import_time(imports.file_edges)}
    assert native <= kept


def test_every_python_file_edge_of_this_tree_was_measured(imports: ProjectSnapshot) -> None:
    """A silent failure to parse would look exactly like a project with no deferred imports."""
    python = [edge for edge in imports.file_edges if edge.src.endswith(".py")]
    assert python
    assert all(edge.import_time is not None for edge in python)
