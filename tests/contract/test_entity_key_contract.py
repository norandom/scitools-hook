"""``EntityKey`` against a real Understand: does the before/after join hold? (task 10.1).

Requirement 4.4 -- "if an affected entity's metric value after the change is worse than
before, report a ratchet finding" -- is only implementable if the same routine, method,
class or file can be *recognised* in two databases built from two different directories.
Every rule that compares the two sides rests on that join, and the join rests on
:class:`~scitools_hook.models.snapshot.EntityKey`. Nothing above the adapter can notice when
it fails: an entity that does not match is simply treated as new, so the ratchet does not
fire, the finding is not classified pre-existing, and the run is green.

The tests here are deliberately of two kinds:

* **The join holds.** Two databases over token-identical sources under differently named
  roots produce not merely equal key *sets* but equal entity *records*. Anything a root
  leaks into an identity -- the file-longname case that once produced 16 unmatched keys --
  shows up here as a difference.
* **The join's discriminator is measured, not assumed.** ``parameters`` is in the key "to
  distinguish overloads"; these tests measure what it actually distinguishes and what
  survives without it. Task 11.6 read that measurement and kept it: dropping ``parameters``
  merges a real C++ overload pair, so the key keeps it and ``analysis.ratchet`` pairs a
  removed key with an added one instead. Both halves are measured here -- one overload
  re-signed pairs, its untouched sibling stays a separate entity, and two identical projects
  pair nothing.

The edge-case project exists to find entity kinds the key cannot separate at all. Two of them
turn out to be reachable in ordinary Python -- ``def same(x)`` twice and ``@typing.overload``
-- and they are the reason a key names more than one record and the reason the mapping numbers
them instead of dropping all but one.
"""

from __future__ import annotations

import collections

import pytest
from contract_project import (
    FILES,
    SOURCES,
    SampleProject,
    build_database,
    contract_settings,
    extract,
    real_env,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
    write_tree,
)

from scitools_hook.analysis.ratchet import evaluate_ratchet, pair_changed_signatures
from scitools_hook.config.metric_names import parse_metric_name
from scitools_hook.config.models import Limit, ThresholdSpec
from scitools_hook.models.findings import EffectiveThreshold
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.snapshot import SnapshotExtractor, SnapshotTarget

pytestmark = pytest.mark.contract

EDGE_SOURCES: dict[str, str] = {
    # Two module-level routines with the same name and the same signature. Nothing about
    # this is exotic -- a bad merge or a `try/except ImportError` pair produces it.
    "pkg/dup.py": '''"""Two routines Understand records separately and the key cannot separate."""


def same(x):
    return x


def same(x):  # noqa: F811 -- the point of the fixture
    return x + 1
''',
    # `@typing.overload` is the idiomatic way to type a polymorphic Python function, and it
    # puts three routines of the same name and the same signature in one module.
    "pkg/typed.py": '''"""A typing.overload triple: two stubs and one implementation."""

from typing import overload


@overload
def widen(x: int) -> int: ...


@overload
def widen(x: str) -> str: ...


def widen(x):
    return x
''',
    "pkg/anon.py": '''"""A lambda, to find out whether Understand gives it an entity at all."""


def with_lambda(xs):
    step = lambda item: item + 1  # noqa: E731 -- the point of the fixture
    return [step(x) for x in xs]
''',
    "native/tmpl.h": """#ifndef EDGE_TMPL_H
#define EDGE_TMPL_H

template <typename T>
T largest(T left, T right) {
    return left > right ? left : right;
}

#endif
""",
    "native/tmpl.cpp": """#include "tmpl.h"

int use_all() {
    return largest(1, 2) + (int)largest(1.5, 2.5) + (int)largest(3L, 4L);
}
""",
}
"""Constructs whose entity identity the design never settled; measured below."""

EDGE_FILES: tuple[str, ...] = tuple(sorted(EDGE_SOURCES))

OVERLOADED = "Shape::area"
"""The C++ member function declared twice with different parameter lists."""


@pytest.fixture(scope="module")
def edge_snapshot(tmp_path_factory: pytest.TempPathFactory) -> ProjectSnapshot:
    """A snapshot of the constructs that stress entity identity."""
    workdir = tmp_path_factory.mktemp("edge-cases")
    root = write_tree(workdir / "tree", EDGE_SOURCES)
    db = workdir / "edges.und"
    build_database(db, root)
    return extract(db, root, EDGE_FILES)


@pytest.fixture(scope="module")
def alpha(sample_project: SampleProject) -> ProjectSnapshot:  # noqa: F811
    """The ``alpha`` side of the sample project, read through the production extractor."""
    return extract(sample_project.db("alpha"), sample_project.root("alpha"), FILES, "before")


def routines(snapshot: ProjectSnapshot) -> list[EntityKey]:
    """Every routine key of a snapshot."""
    return [key for key in snapshot.entities if key.scope == "routine"]


# --- the join across two roots ---------------------------------------------------


def test_two_databases_from_different_roots_agree_on_every_entity(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """The property requirement 4.4 depends on, asserted at its strongest.

    The sources under ``alpha`` and ``beta`` are byte-identical, so *every* difference
    between the two snapshots is something Understand attached to the analysis root. Equal
    key sets would already catch the file-longname defect; equal **records** also catch a
    metric or an architecture path that carries the root, and cost nothing extra to assert.
    """
    before = extract(sample_project.db("alpha"), sample_project.root("alpha"), FILES, "before")
    after = extract(sample_project.db("beta"), sample_project.root("beta"), FILES, "after")

    assert set(before.entities) == set(after.entities)
    assert before.entities == after.entities
    assert len(before.entities) == 22, sorted(key.token for key in before.entities)


def test_no_key_carries_the_analysis_root(alpha: ProjectSnapshot) -> None:
    """The measured defect this rule exists for: a FILE entity's long name is absolute.

    ``ent.longname()`` on a file answers the whole path including the shadow directory, so a
    key built from it differs between the two sides for every file in the project. The paths
    asserted here are the relative ones a diff names, and they are the same on both sides.
    """
    for key in alpha.entities:
        assert not key.path.startswith("/"), key.token
        assert "alpha" not in key.path, key.token
        assert "alpha" not in key.longname, key.token
    assert {key.path for key in alpha.entities if key.scope == "file"} == set(FILES)


def test_functions_methods_classes_and_files_are_each_keyed(alpha: ProjectSnapshot) -> None:
    """One key per kind requirement 4.4 names, spelled out rather than counted.

    A plain function, a method, a ``classmethod``, a ``staticmethod``, a Python class, a C++
    class, a constructor, a Python module file and a C++ header all have to arrive, because
    the gate's kind strings are the only thing between a scope and an empty population.
    """
    expected = {
        EntityKey(scope="routine", path="main.py", longname="main.main", parameters=""),
        EntityKey(
            scope="routine",
            path="pkg/core.py",
            longname="core.Engine.run",
            parameters="self,value",
        ),
        EntityKey(
            scope="routine", path="pkg/core.py", longname="core.Engine.build", parameters="cls"
        ),
        EntityKey(scope="routine", path="pkg/core.py", longname="core.Engine.label", parameters=""),
        EntityKey(
            scope="routine",
            path="native/shape.cpp",
            longname="Shape::Shape",
            parameters="int side",
        ),
        EntityKey(scope="class", path="pkg/core.py", longname="core.Engine", parameters=None),
        EntityKey(scope="class", path="native/shape.h", longname="Shape", parameters=None),
        EntityKey(scope="file", path="main.py", longname="main.py", parameters=None),
        EntityKey(scope="file", path="native/shape.h", longname="native/shape.h", parameters=None),
    }

    assert expected <= set(alpha.entities)


def test_understand_calls_a_classmethod_and_a_staticmethod_the_same_kind(
    alpha: ProjectSnapshot,
) -> None:
    """Measured, and worth knowing before a rule tries to branch on the kind string.

    ``@classmethod`` and ``@staticmethod`` are both ``python Function Attribute Static``; only
    the first parameter (``cls`` against nothing) tells them apart. A rule that wanted to
    exempt static helpers would exempt class constructors with them.
    """
    kinds = {
        key.longname: alpha.entities[key].ref.kind
        for key in routines(alpha)
        if key.longname in {"core.Engine.build", "core.Engine.label", "core.Engine.run"}
    }

    assert kinds == {
        "core.Engine.build": "python Function Attribute Static",
        "core.Engine.label": "python Function Attribute Static",
        "core.Engine.run": "python Function Attribute",
    }


def test_a_cpp_class_is_keyed_to_its_header_and_its_methods_to_their_definitions(
    alpha: ProjectSnapshot,
) -> None:
    """Where a C++ entity lives, measured: a split declaration does not become two entities.

    The class is attributed to the header that declares it and every member function to the
    source file that defines it, so each routine appears once even though the compiler sees
    its signature twice.
    """
    native = [key for key in alpha.entities if key.path.startswith("native/")]

    assert [key.path for key in native if key.scope == "class"] == ["native/shape.h"]
    assert {key.path for key in native if key.scope == "routine"} == {"native/shape.cpp"}
    assert len([key for key in native if key.scope == "routine"]) == 6


# --- what ``parameters`` really discriminates (evidence for task 11.6) -----------


def test_a_cpp_overload_pair_differs_in_nothing_but_its_parameters(
    alpha: ProjectSnapshot,
) -> None:
    """The measurement task 11.6 needs: can a C++ overload pair be told apart without them?

    ``Shape::area(int) const`` and ``Shape::area(int, int) const`` are two entities in the
    database. Every field a key could be built from is identical between them -- scope, path,
    long name, even Understand's kind string and the entity's short name -- **except**
    ``parameters``. The only other field that differs is the definition line, which moves
    whenever code above it changes and is therefore unusable as identity across two commits.

    So the answer for 11.6 is: dropping ``parameters`` outright merges a real C++ overload
    pair into one entity. A fix has to keep a discriminator for this case.
    """
    overloads = [key for key in routines(alpha) if key.longname == OVERLOADED]

    assert len(overloads) == 2
    assert {key.parameters for key in overloads} == {"int width", "int width,int height"}
    assert {(key.scope, key.path, key.longname) for key in overloads} == {
        ("routine", "native/shape.cpp", OVERLOADED)
    }
    records = [alpha.entities[key] for key in overloads]
    # 6.5 spelled this `C Public Member Const Function`; 8.0 inserts `Method` after `Public`.
    # The kind is informational -- the key never reads it -- so both spellings are one answer.
    (kind,) = {record.ref.kind for record in records}
    assert kind.startswith("C Public") and kind.endswith("Member Const Function"), kind
    assert {record.ref.name for record in records} == {"area"}
    assert len({record.ref.line for record in records}) == 2, "only the line separates them"


def test_only_the_cpp_overloads_need_parameters_to_stay_apart(alpha: ProjectSnapshot) -> None:
    """The other half of 11.6's question, as a census rather than an example.

    Requirement 4.4's join loses a routine whenever its parameter list changes, because the
    key changes with it. This counts what dropping ``parameters`` would cost on a real
    mixed-language project: the two C++ overload pairs collide, and **nothing else does** --
    every Python routine here, ``__init__``, ``classmethod`` and ``staticmethod`` included,
    stays unique on ``(scope, path, longname)`` alone.
    """
    census = collections.Counter((key.scope, key.path, key.longname) for key in alpha.entities)

    assert {name: count for name, count in census.items() if count > 1} == {
        ("routine", "native/shape.cpp", OVERLOADED): 2,
        ("routine", "native/shape.cpp", "scale"): 2,
    }
    python = [name for name in census if name[1].endswith(".py")]
    assert all(census[name] == 1 for name in python)
    assert len(python) == 13


# --- entity kinds the key cannot separate ----------------------------------------


def test_a_cpp_function_template_is_one_entity_with_the_generic_signature(
    edge_snapshot: ProjectSnapshot,
) -> None:
    """Three instantiations, one entity: the ratchet sees a template as a single routine.

    That is the right behaviour for a join -- one edit, one entity -- but it is also a real
    limit on what the gate can see, because the metrics are measured once on the template
    body rather than once per instantiation.
    """
    templates = [key for key in routines(edge_snapshot) if key.longname == "largest"]

    assert len(templates) == 1
    assert templates[0].parameters == "T left,T right"
    assert edge_snapshot.entities[templates[0]].ref.kind == "C Function Template"


def test_a_python_lambda_is_not_a_routine_entity_at_all(edge_snapshot: ProjectSnapshot) -> None:
    """No entity, so no key, so nothing to match -- and nothing measured either.

    A lambda's complexity is counted into its enclosing routine, never on its own, so
    "unmatched entity kinds" has nothing to say about lambdas: they never arrive. The
    assertion is on the whole routine set of the file, not on a name pattern -- the enclosing
    routine is itself called ``with_lambda``, so a substring test would answer either way.
    """
    inside = {key.longname for key in routines(edge_snapshot) if key.path == "pkg/anon.py"}

    assert inside == {"anon.with_lambda"}


def test_two_python_routines_with_one_signature_are_both_kept(
    edge_snapshot: ProjectSnapshot,
) -> None:
    """The measured hole in ``EntityKey``, and what task 11.6 does about it.

    ``def same(x)`` written twice, and ``@typing.overload``'s stub-plus-stub-plus-
    implementation triple, each put several Understand entities behind one
    ``(scope, path, longname, parameters)``. The mapping used to keep the last of each and
    drop the rest -- silently, on both sides, so no rule could notice that an entity was
    never measured. They are numbered now, in file order, and every one of them arrives.
    """
    duplicated = [key for key in routines(edge_snapshot) if key.longname == "dup.same"]
    overloaded = [key for key in routines(edge_snapshot) if key.longname == "typed.widen"]

    assert len(duplicated) == 2, "two routines, two records"
    assert len(overloaded) == 3, "three routines, three records"
    assert {key.parameters for key in duplicated} == {"x"}
    assert {key.ordinal for key in duplicated} == {0, 1}
    assert {key.ordinal for key in overloaded} == {0, 1, 2}


def test_the_numbering_follows_the_order_the_routines_are_written_in(
    edge_snapshot: ProjectSnapshot,
) -> None:
    """The ordinal has to mean the same thing on both sides, so it is read off the source.

    ``pkg/typed.py`` holds the two ``@overload`` stubs and then the implementation, so the
    lines rise with the ordinal. Understand's own walk order is not promised to, which is why
    the numbering sorts rather than counting.
    """
    lines = {
        key.ordinal: edge_snapshot.entities[key].ref.line
        for key in routines(edge_snapshot)
        if key.longname == "typed.widen"
    }

    assert sorted(lines) == [0, 1, 2]
    assert [lines[ordinal] for ordinal in sorted(lines)] == sorted(lines.values())
    assert len(set(lines.values())) == 3, "three distinct definitions, not one read three times"


def test_the_worker_reports_three_records_and_the_snapshot_keeps_three(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Where the numbering happens, measured: Understand reports three, the model keeps three.

    Reading the worker's answer *before* it is validated is what makes the test above mean
    something -- without it "three keys" could not be told from Understand having reported
    three of anything. It also pins the wire form: the worker cannot import the package and
    writes no ordinal at all, and the four field key documents it sends are what the model
    numbers on the way in.
    """
    workdir = tmp_path_factory.mktemp("overload-records")
    root = write_tree(workdir / "tree", {"pkg/typed.py": EDGE_SOURCES["pkg/typed.py"]})
    db = workdir / "typed.und"
    build_database(db, root, ("python",))
    extractor = SnapshotExtractor(
        ApiRunner(real_env("upython"), NullCommandLog()), contract_settings()
    )
    target = SnapshotTarget(db=db, root=root, side="after", files=frozenset({"pkg/typed.py"}))

    answer = extractor.runner.run("snapshot", extractor.wire_request(target))

    records = answer["entities"]
    assert isinstance(records, list)
    arriving = [record for record in records if _longname(record) == "typed.widen"]
    assert len(arriving) == 3
    assert all("ordinal" not in record["ref"]["key"] for record in arriving)  # type: ignore[index]
    surviving = ProjectSnapshot.model_validate(answer)
    assert len([key for key in surviving.entities if key.longname == "typed.widen"]) == 3


# --- the join across a changed signature (task 11.6) ------------------------------

RESIGNED: dict[str, str] = {
    **SOURCES,
    "native/shape.h": SOURCES["native/shape.h"].replace(
        "int area(int width) const;", "int area(int width, int height, int depth) const;"
    ),
    "native/shape.cpp": SOURCES["native/shape.cpp"].replace(
        "int Shape::area(int width) const { return width * side_; }",
        "int Shape::area(int width, int height, int depth) const {\n"
        "    return width * height * depth * side_;\n}",
    ),
}
"""The sample project with **one** overload re-signed: ``area(int)`` gains two parameters.

Its sibling ``area(int, int)`` is untouched, which is what makes this the C++ shape task 11.6
is about: the pair must stay two entities, and only the one that changed may pair.
"""


@pytest.fixture(scope="module")
def resigned(tmp_path_factory: pytest.TempPathFactory) -> ProjectSnapshot:
    """The sample project with one overload's parameter list changed."""
    workdir = tmp_path_factory.mktemp("resigned")
    root = write_tree(workdir / "tree", RESIGNED)
    db = workdir / "resigned.und"
    build_database(db, root)
    return extract(db, root, FILES)


def test_two_databases_over_identical_sources_pair_nothing(
    sample_project: SampleProject,  # noqa: F811
) -> None:
    """The control the pairing must not fire on: nothing changed, so nothing is paired.

    ``alpha`` and ``beta`` hold byte-identical sources under differently named roots, so every
    key is on both sides. A pairing that answered anything here would be matching entities
    that never moved.
    """
    before = extract(sample_project.db("alpha"), sample_project.root("alpha"), FILES, "before")
    after = extract(sample_project.db("beta"), sample_project.root("beta"), FILES, "after")

    assert pair_changed_signatures(after, before) == {}


def test_one_overload_re_signed_pairs_while_its_sibling_stays_a_separate_entity(
    alpha: ProjectSnapshot, resigned: ProjectSnapshot
) -> None:
    """The defect and the invariant, measured together against a real Understand.

    ``Shape::area(int) const`` gained two parameters, so its key changed and requirement 4.4
    lost it. It is now paired with the key it left behind -- and its overload sibling
    ``Shape::area(int, int) const``, which nothing touched, is matched by its own key on both
    sides and is *not* part of the pairing. That is the whole of task 11.6: a signature change
    is followed, and a real overload pair is still two entities.
    """
    both = [key for key in routines(resigned) if key.longname == OVERLOADED]
    assert len(both) == 2, sorted(key.parameters or "" for key in both)

    pairs = pair_changed_signatures(resigned, alpha)

    was = EntityKey(
        scope="routine", path="native/shape.cpp", longname=OVERLOADED, parameters="int width"
    )
    now = EntityKey(
        scope="routine",
        path="native/shape.cpp",
        longname=OVERLOADED,
        parameters="int width,int height,int depth",
    )
    assert pairs == {now: was}
    unchanged = EntityKey(
        scope="routine",
        path="native/shape.cpp",
        longname=OVERLOADED,
        parameters="int width,int height",
    )
    assert unchanged in alpha.entities
    assert unchanged in resigned.entities
    assert unchanged not in pairs


def test_the_re_signed_overload_is_ratcheted_against_the_routine_it_replaced(
    alpha: ProjectSnapshot, resigned: ProjectSnapshot
) -> None:
    """What the pairing is *for*, end of the analysis chain: the growth is reported.

    ``area(int)`` was a one line body and is now a three line one. Without the pairing this
    is an added entity and requirement 4.4 says nothing about it; with it, the ratchet reports
    the routine by name against the value it used to have.
    """
    spec = ThresholdSpec(scope="routine", metric="CountLineCode", limit=Limit(max=60), ratchet=True)
    limit = EffectiveThreshold(
        spec=spec, metric=parse_metric_name("CountLineCode"), limit=spec.limit, source="config"
    )

    findings = evaluate_ratchet(resigned, alpha, set(resigned.entities), [limit])

    reported = {
        finding.entity.key.parameters: (finding.before, finding.value)
        for finding in findings
        if finding.entity is not None and finding.entity.key.longname == OVERLOADED
    }
    assert list(reported) == ["int width,int height,int depth"]
    ((was, now),) = reported.values()
    assert was is not None and now is not None and now > was


def _longname(record: object) -> str:
    """The long name of one wire-form entity record, or ``""`` for anything else."""
    if not isinstance(record, dict):
        return ""
    return str(record["ref"]["key"]["longname"])
