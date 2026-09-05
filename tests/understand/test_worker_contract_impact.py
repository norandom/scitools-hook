"""The ``impact`` and ``graphs`` operations against the real databases (task 10.1).

The blast radius and the drawings, measured rather than believed: a reverse walk over real
references from the sample project's routines, an SVG per affected entity, and the C++
half of the project, where the referencer is not an entity the gate can key and the walk
has to pass through it without reporting it. ``test_worker_contract`` covers the snapshot.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pytest
from conftest import SampleDatabases
from worker_projects import (
    file_key,
    routine_key,
    run_worker,
    upython_or_skip,
)

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.models.change import GraphFile, ImpactSet
from scitools_hook.models.snapshot import EntityKey

# --- contract: impact and graphs against the real databases -----------------------


def class_key(path: str, longname: str) -> EntityKey:
    """The key of a class defined in ``path``; a class declares no parameters."""
    return EntityKey(scope="class", path=path, longname=longname, parameters=None)


WRAP_LINES: Final = routine_key("util/text.py", "text.wrap_lines", "lines,width")
APPLY_RULES: Final = routine_key("analysis/rules.py", "rules.apply_rules", "names")
ENGINE: Final = class_key("analysis/engine.py", "engine.Engine")
APP_FILE: Final = file_key("cli/app.py")


def contract_reference_request(
    databases: SampleDatabases, keys: Sequence[EntityKey], depth: int
) -> dict[str, object]:
    """The ``impact`` request ``ImpactExpander`` will build: kind strings from ``SCOPE_KINDS``."""
    return {
        "db": str(databases.db("after")),
        "root": str(databases.root("after")),
        "kinds_by_scope": dict(SCOPE_KINDS),
        "keys": [key.model_dump() for key in keys],
        "depth": depth,
    }


def contract_impact(
    databases: SampleDatabases, keys: Sequence[EntityKey], depth: int = 2
) -> dict[EntityKey, ImpactSet]:
    """Run the real worker's ``impact`` and validate what it answered."""
    document = run_worker(
        upython_or_skip(), "impact", contract_reference_request(databases, keys, depth)
    )
    assert document.get("warnings") == [], document
    return {
        EntityKey.from_token(token): ImpactSet.model_validate(impact)
        for token, impact in document["impact"].items()
    }


@pytest.mark.contract
def test_impact_under_upython_reports_the_callers_of_a_sample_routine(
    sample_databases: SampleDatabases,
) -> None:
    # The done-criterion of task 6.3: a real depth-1 impact set for a sample function.
    # Measured on the fixture: `app.main` calls `text.wrap_lines`, and `cli/app.py` imports
    # it, so both the routine and the importing file are one reference away.
    impact = contract_impact(sample_databases, [WRAP_LINES], depth=1)[WRAP_LINES]
    assert impact.by_depth[1], "wrap_lines is called by the sample project"
    names = {ref.key.longname for ref in impact.by_depth[1]}
    # Exactly two entities reference it and both must be reported: the routine that calls it
    # (`callby`) and the file that imports it (`importby`). Anything reached by a narrower
    # filter, or any library entity leaking in, changes this set.
    assert names == {"app.main", "cli/app.py"}
    assert impact.total == len(impact.by_depth[1])
    caller = next(ref for ref in impact.by_depth[1] if ref.key.longname == "app.main")
    assert caller.key == routine_key("cli/app.py", "app.main", "argv")
    assert caller.name == "main"
    assert caller.line is not None


@pytest.mark.contract
def test_impact_under_upython_walks_further_than_one_level(
    sample_databases: SampleDatabases,
) -> None:
    # `rules.apply_rules` <- `engine.Engine.run` <- `app.main` <- `app.check_command`.
    impact = contract_impact(sample_databases, [APPLY_RULES], depth=3)[APPLY_RULES]
    reached = {ref.key.longname for refs in impact.by_depth.values() for ref in refs}
    assert {"engine.Engine.run", "app.main", "app.check_command"} <= reached
    assert impact.total == sum(len(refs) for refs in impact.by_depth.values())
    assert len(reached) == impact.total, "an entity is reported at one depth only"
    assert APPLY_RULES.longname not in reached


@pytest.mark.contract
def test_impact_under_upython_answers_for_a_class_and_a_file(
    sample_databases: SampleDatabases,
) -> None:
    sets = contract_impact(sample_databases, [ENGINE, APP_FILE], depth=1)
    assert "app.main" in {ref.key.longname for ref in sets[ENGINE].by_depth[1]}
    assert sets[APP_FILE].total == 0, "nothing in the sample project imports the cli layer"


@pytest.mark.contract
def test_impact_under_upython_warns_about_an_entity_the_database_lost(
    sample_databases: SampleDatabases,
) -> None:
    # `app.legacy_main` exists in neither side: the summary still has to be produced.
    gone = routine_key("cli/app.py", "app.legacy_main", "argv")
    document = run_worker(
        upython_or_skip(), "impact", contract_reference_request(sample_databases, [gone], 1)
    )
    assert document["impact"][gone.token] == {"by_depth": {}, "total": 0}
    assert len(document["warnings"]) == 1
    assert "app.legacy_main" in document["warnings"][0]


def contract_graph_request(
    databases: SampleDatabases, targets: Sequence[tuple[EntityKey, str]], out_dir: Path
) -> dict[str, object]:
    """The ``graphs`` request ``GraphExporter`` will build."""
    return {
        "db": str(databases.db("after")),
        "root": str(databases.root("after")),
        "kinds_by_scope": dict(SCOPE_KINDS),
        "targets": [{"key": key.model_dump(), "graph": graph} for key, graph in targets],
        "out_dir": str(out_dir),
    }


@pytest.mark.contract
def test_graphs_under_upython_exports_an_svg_for_every_graph_type(
    sample_databases: SampleDatabases, tmp_path: Path
) -> None:
    # The done-criterion of task 6.3: at least one SVG per graph type. Verified live that
    # routines and classes render `Butterfly` and files render `Depends On`.
    out_dir = tmp_path / "graphs"
    targets = [(WRAP_LINES, "Butterfly"), (ENGINE, "Butterfly"), (APP_FILE, "Depends On")]
    document = run_worker(
        upython_or_skip(), "graphs", contract_graph_request(sample_databases, targets, out_dir)
    )
    assert document["warnings"] == [], document
    files = [GraphFile.model_validate(entry) for entry in document["graphs"]]
    assert [(entry.key, entry.graph) for entry in files] == targets
    for entry in files:
        assert entry.path.parent == out_dir
        assert entry.path.suffix == ".svg"
        assert entry.path.stat().st_size > 0
        assert "<svg" in entry.path.read_text(encoding="utf-8")
    assert len({entry.path for entry in files}) == 3
    assert sorted(path.name for path in out_dir.iterdir()) == sorted(
        entry.path.name for entry in files
    )


@pytest.mark.contract
def test_graphs_under_upython_writes_numbers_a_renderer_will_accept(
    sample_databases: SampleDatabases, tmp_path: Path
) -> None:
    # The exported picture is only useful if it renders: measured, a German LC_NUMERIC makes
    # Understand write `stroke-opacity="0,000000"` into every graph. Point lists legitimately
    # separate a coordinate pair with a comma, so only the numeric attributes are checked.
    out_dir = tmp_path / "graphs"
    document = run_worker(
        upython_or_skip(),
        "graphs",
        contract_graph_request(sample_databases, [(WRAP_LINES, "Butterfly")], out_dir),
    )
    assert document["warnings"] == [], document
    drawing = GraphFile.model_validate(document["graphs"][0]).path.read_text(encoding="utf-8")
    assert "opacity=" in drawing, "the graph is expected to set opacity at all"
    assert not re.search(r'opacity="[0-9]+,[0-9]+"', drawing), "a comma is not a decimal point"


@pytest.mark.contract
def test_graphs_under_upython_records_a_graph_a_routine_cannot_draw_as_a_warning(
    sample_databases: SampleDatabases, tmp_path: Path
) -> None:
    # Verified live: `Depends On` on a routine raises UnderstandError('Unknown Graph').
    out_dir = tmp_path / "graphs"
    targets = [(WRAP_LINES, "Depends On"), (APP_FILE, "Depends On")]
    document = run_worker(
        upython_or_skip(), "graphs", contract_graph_request(sample_databases, targets, out_dir)
    )
    files = [GraphFile.model_validate(entry) for entry in document["graphs"]]
    assert [entry.key for entry in files] == [APP_FILE]
    assert len(document["warnings"]) == 1
    assert "text.wrap_lines" in document["warnings"][0]
    assert "Unknown Graph" in document["warnings"][0]
    assert [path.name for path in out_dir.iterdir()] == [files[0].path.name]


# --- contract: the C++ blast radius, where the referencer is not an entity we can key ---

BOX: Final = class_key("native/shapes.cpp", "Box")
EXTENT: Final = class_key("native/shapes.cpp", "Extent")
METER: Final = class_key("native/shapes.cpp", "Meter")
SCAN_ERROR: Final = class_key("native/shapes.cpp", "ScanError")


@pytest.mark.contract
def test_impact_under_upython_sees_a_class_used_only_through_a_local(
    sample_databases: SampleDatabases,
) -> None:
    # Measured: `Box`'s only reverse reference is `C Typedby` to the local `box`, which is a
    # `C Object Local` — no scope of this gate can key it. Stopping there answers "nothing
    # depends on this class" for the commonest way a C++ type is used.
    impact = contract_impact(sample_databases, [BOX], depth=1)[BOX]
    assert "box_area" in {ref.key.longname for ref in impact.by_depth[1]}


@pytest.mark.contract
def test_impact_under_upython_sees_a_struct_used_only_as_a_parameter(
    sample_databases: SampleDatabases,
) -> None:
    impact = contract_impact(sample_databases, [EXTENT], depth=1)[EXTENT]
    assert "extent_area" in {ref.key.longname for ref in impact.by_depth[1]}


@pytest.mark.contract
def test_impact_under_upython_sees_a_class_used_only_as_a_member(
    sample_databases: SampleDatabases,
) -> None:
    # `Meter` is reached only through `Gauge::meter_`, a `C Private Member Object`.
    impact = contract_impact(sample_databases, [METER], depth=1)[METER]
    assert "Gauge::read" in {ref.key.longname for ref in impact.by_depth[1]}


@pytest.mark.contract
def test_impact_under_upython_sees_an_exception_that_is_thrown_and_caught(
    sample_databases: SampleDatabases,
) -> None:
    # Measured: the throw carries `C Throwby Exception` and the catch `C Catchby Exception`;
    # neither reference kind is a `callby` or a `useby`, so a hand-listed reverse filter
    # loses the catching routine altogether.
    impact = contract_impact(sample_databases, [SCAN_ERROR], depth=1)[SCAN_ERROR]
    names = {ref.key.longname for ref in impact.by_depth[1]}
    assert {"raise_scan_error", "guarded_scan"} <= names


@pytest.mark.contract
def test_impact_under_upython_never_reports_an_entity_it_cannot_key(
    sample_databases: SampleDatabases,
) -> None:
    # Seeing through an opaque entity must not turn it into an answer: a local, a parameter
    # and a member object have no EntityKey the rest of the gate could use.
    sets = contract_impact(sample_databases, [BOX, EXTENT, METER, SCAN_ERROR], depth=3)
    reported = {
        ref.key.longname
        for impact in sets.values()
        for refs in impact.by_depth.values()
        for ref in refs
    }
    assert not reported & {"box", "size", "Gauge::meter_", "err", "Box::side_"}


WIDE: Final = class_key("native/shapes.cpp", "lens::Wide")
TIGHT: Final = class_key("native/shapes.cpp", "lens::Tight")
GAUGE_READ: Final = routine_key("native/shapes.cpp", "Gauge::read", "")


@pytest.mark.contract
def test_impact_under_upython_keeps_two_classes_of_one_namespace_apart(
    sample_databases: SampleDatabases,
) -> None:
    # `lens::Wide` and `lens::Tight` are used by one routine each, and neither routine
    # mentions the other class. Measured: defining the members inside a second
    # `namespace lens { … }` block makes each class carry `C Nameby` to the namespace, and the
    # namespace carries a reference back to everything that names it — so a walk that goes
    # through it hands both classes the same, wrong answer. Identical sets are the tell.
    sets = contract_impact(sample_databases, [WIDE, TIGHT], depth=2)
    wide = {ref.key.longname for refs in sets[WIDE].by_depth.values() for ref in refs}
    tight = {ref.key.longname for refs in sets[TIGHT].by_depth.values() for ref in refs}
    assert "uses_wide" in wide
    assert "uses_tight" in tight
    assert "uses_tight" not in wide, "uses_tight holds no reference to lens::Wide"
    assert "uses_wide" not in tight, "uses_wide holds no reference to lens::Tight"
    assert "lens" not in wide | tight, "a namespace is not an entity the gate can key"


@pytest.mark.contract
def test_impact_under_upython_does_not_report_the_class_a_method_is_declared_in(
    sample_databases: SampleDatabases,
) -> None:
    # Measured: a member function carries `C Declarein` to its class. A class holds a method,
    # it does not depend on it, and reporting it would drag the class's whole blast radius
    # into every method's answer one level further out.
    impact = contract_impact(sample_databases, [GAUGE_READ], depth=2)[GAUGE_READ]
    reached = {ref.key.longname for refs in impact.by_depth.values() for ref in refs}
    assert "Gauge" not in reached
