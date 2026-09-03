"""What Understand's call references actually resolve to, per language, measured.

The call graph is the one structural view the gate reads that **cannot be assumed complete**,
and this module is where that claim is turned from an assumption into a number. It builds a
tree of its own -- not the shared contract project, whose sources several other tasks assert
against verbatim -- holding one call site of every shape that matters, and then reads it back
through the production extractor. Every figure quoted in
:class:`~scitools_hook.models.snapshot.CallResolution`'s documentation and in
``analysis.structure.calls`` comes from a measurement of this shape, and the tests below hold
the code to it.

The four Python shapes are the ones that decide whether a reach rule is trustworthy:

===================================  ==================================================
call site                            what ``ent.refs("call")`` binds to
===================================  ==================================================
``leaf(x)``, a module function       ``leaf`` -- the routine. Resolves.
``self.helper(x)``                   ``helper`` -- the routine. Resolves.
``Holder()``                         the *class*; the graph maps it to ``__init__``.
``self.fn(x)`` where ``self.fn``     ``fn`` -- the **attribute**. Binds to nothing
is a routine assigned in ``__init__``  callable, and is counted unresolved.
``h.method(x)`` on a local           an ambiguous attribute. Also unresolved.
===================================  ==================================================

The assignment ``self.fn = leaf`` yields no call reference at all, so there is nothing
anywhere in the database that would let the graph recover the edge. That is why the rules
report a lower bound and say so.
"""

from __future__ import annotations

import pytest
from contract_project import build_database, extract, write_tree

from scitools_hook.analysis.structure.calls import CallGraph, evaluate_reachable_complexity
from scitools_hook.config.models import Limit
from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot

pytestmark = pytest.mark.contract

SOURCES: dict[str, str] = {
    "shapes.py": '''"""One call site of every shape a Python call graph can meet."""


def leaf(value):
    return value + 1


class Holder(object):
    def __init__(self):
        self.fn = leaf

    def helper(self, value):
        return value - 1

    def module_function(self, value):
        return leaf(value)

    def method_on_self(self, value):
        return self.helper(value)

    def attribute_dispatch(self, value):
        return self.fn(value)


def constructs(value):
    holder = Holder()
    return holder.module_function(value)


def recurse_a(value):
    return recurse_b(value - 1) if value > 0 else 0


def recurse_b(value):
    return recurse_a(value - 1) if value > 0 else 0
''',
    "native.h": """#ifndef CALLS_NATIVE_H
#define CALLS_NATIVE_H

class Base {
public:
    virtual int compute(int x) const;
    int helper(int x) const;
    int wrapper(int x) const;
};

int freefn(int x);
int caller(int x);
int through_pointer(int (*fp)(int), int x);

#endif
""",
    "native.cpp": """#include "native.h"

int Base::compute(int x) const { return x + 1; }
int Base::helper(int x) const { return x - 1; }
int Base::wrapper(int x) const { return helper(compute(x)); }
int freefn(int x) { return x + 3; }

int caller(int x) {
    Base b;
    return b.wrapper(x) + freefn(x);
}

int through_pointer(int (*fp)(int), int x) { return fp(x); }
""",
}
"""One tree, both languages, one call site of every shape the resolution report distinguishes."""

FILES: tuple[str, ...] = tuple(sorted(SOURCES))

HOLDER_METHODS: tuple[str, ...] = (
    "shapes.Holder.module_function",
    "shapes.Holder.method_on_self",
    "shapes.Holder.attribute_dispatch",
)


@pytest.fixture(scope="module")
def shapes(tmp_path_factory: pytest.TempPathFactory) -> ProjectSnapshot:
    """The call-shape tree, analysed once and read back through the production extractor."""
    workdir = tmp_path_factory.mktemp("call-shapes")
    root = write_tree(workdir / "tree", SOURCES)
    database = workdir / "shapes.und"
    build_database(database, root)
    return extract(database, root, FILES)


def routines(snapshot: ProjectSnapshot) -> dict[str, EntityKey]:
    """Every routine the snapshot recorded, keyed by its qualified name."""
    return {key.longname: key for key in snapshot.entities if key.scope == "routine"}


def calls_from(snapshot: ProjectSnapshot, name: str) -> set[str]:
    """The routines ``name`` calls, by qualified name, as the published edges report them."""
    source = routines(snapshot)[name].token
    return {
        EntityKey.from_token(edge.dst).longname
        for edge in snapshot.call_edges
        if edge.src == source
    }


def blind_spots(snapshot: ProjectSnapshot, name: str) -> int:
    """How many call sites of ``name`` bound to nothing callable."""
    token = routines(snapshot)[name].token
    return next(node.unresolved_calls for node in snapshot.call_nodes if node.node == token)


# --- the four Python shapes ------------------------------------------------------


def test_a_call_to_a_module_function_resolves(shapes: ProjectSnapshot) -> None:
    assert calls_from(shapes, "shapes.Holder.module_function") == {"shapes.leaf"}


def test_a_method_called_on_self_resolves(shapes: ProjectSnapshot) -> None:
    assert calls_from(shapes, "shapes.Holder.method_on_self") == {"shapes.Holder.helper"}


def test_a_call_through_an_instance_attribute_resolves_to_nothing_callable(
    shapes: ProjectSnapshot,
) -> None:
    """``self.fn(x)`` where ``self.fn = leaf``: the whole reason every finding is a lower bound.

    Understand binds the call to the *attribute* ``fn``, which is not callable, so no edge
    exists and the routine looks like a leaf. It must not read like one: the call site is
    counted against the routine, so the graph can say "this answer is missing something" and
    name where.
    """
    assert calls_from(shapes, "shapes.Holder.attribute_dispatch") == set()
    assert blind_spots(shapes, "shapes.Holder.attribute_dispatch") == 1


def test_the_assignment_that_makes_the_dispatch_possible_is_no_call_at_all(
    shapes: ProjectSnapshot,
) -> None:
    """``self.fn = leaf`` in ``__init__`` yields no call reference, so nothing can recover it."""
    assert calls_from(shapes, "shapes.Holder.__init__") == set()
    assert blind_spots(shapes, "shapes.Holder.__init__") == 0


def test_a_call_on_a_constructed_instance_also_resolves_to_nothing(
    shapes: ProjectSnapshot,
) -> None:
    """``Holder().module_function(x)``: the construction resolves, the method call does not.

    Measured, and worse than the attribute case looks: the object was constructed two lines
    above, in the same routine, from a class in the same file, and Understand still binds the
    method call to an ambiguous attribute.
    """
    assert calls_from(shapes, "shapes.constructs") == {"shapes.Holder.__init__"}
    assert blind_spots(shapes, "shapes.constructs") == 1


def test_a_call_on_a_class_becomes_an_edge_to_its_constructor(
    shapes: ProjectSnapshot,
) -> None:
    assert "shapes.Holder.__init__" in calls_from(shapes, "shapes.constructs")


def test_mutual_recursion_is_two_resolved_edges(shapes: ProjectSnapshot) -> None:
    assert calls_from(shapes, "shapes.recurse_a") == {"shapes.recurse_b"}
    assert calls_from(shapes, "shapes.recurse_b") == {"shapes.recurse_a"}


# --- the C++ shapes ---------------------------------------------------------------


def test_a_cpp_member_call_and_a_free_call_both_resolve(shapes: ProjectSnapshot) -> None:
    assert calls_from(shapes, "caller") >= {"Base::wrapper", "freefn"}


def test_an_unqualified_member_call_inside_a_method_resolves(
    shapes: ProjectSnapshot,
) -> None:
    assert calls_from(shapes, "Base::wrapper") == {"Base::helper", "Base::compute"}


def test_a_cpp_call_through_a_function_pointer_resolves_to_nothing_callable(
    shapes: ProjectSnapshot,
) -> None:
    """C++ has a blind spot too; it is one shape rather than the ordinary case."""
    assert calls_from(shapes, "through_pointer") == set()
    assert blind_spots(shapes, "through_pointer") == 1


# --- the resolution report --------------------------------------------------------


def test_both_languages_get_a_resolution_figure_of_their_own(
    shapes: ProjectSnapshot,
) -> None:
    assert set(shapes.call_resolution) == {"Python", "C++"}


def test_every_call_site_is_counted_exactly_once(shapes: ProjectSnapshot) -> None:
    for language, resolution in shapes.call_resolution.items():
        assert resolution.total > 0, language
        assert resolution.total == (
            resolution.resolved + resolution.external + resolution.unresolved
        )


def test_python_resolves_a_smaller_share_of_its_calls_than_cpp(
    shapes: ProjectSnapshot,
) -> None:
    """The measured claim the rules' documentation makes, held to the installed Understand.

    This is the fact that makes the per-language report necessary rather than decorative: one
    averaged figure would let the language with the worse substrate hide behind the better one.
    """
    python = shapes.call_resolution["Python"]
    native = shapes.call_resolution["C++"]
    assert python.bound is not None and native.bound is not None
    assert python.bound < native.bound


def test_the_confidence_clause_names_the_language_and_the_count(
    shapes: ProjectSnapshot,
) -> None:
    clause = CallGraph.of(shapes).confidence("Python")
    python = shapes.call_resolution["Python"]
    assert f"{python.unresolved} of {python.total} Python call sites" in clause


# --- the rule, over a real database -----------------------------------------------


def test_the_reach_rule_sums_real_complexity_over_a_real_call_graph(
    shapes: ProjectSnapshot,
) -> None:
    """``shapes.constructs`` reaches its constructor and the routine that constructor calls."""
    calls = CallGraph.of(shapes)
    reach = calls.reach(routines(shapes)["shapes.constructs"].token, shapes.unparsed_files)
    reached = {EntityKey.from_token(name).longname for name in reach.routines}
    assert reached == {"shapes.constructs", "shapes.Holder.__init__"}
    assert reach.unresolved_calls == 1
    assert reach.complexity > 0


def test_the_reach_rule_reports_a_real_routine_over_a_real_limit(
    shapes: ProjectSnapshot,
) -> None:
    subject = routines(shapes)["shapes.constructs"]
    assert evaluate_reachable_complexity(shapes, [subject], Limit(max=1000)) == []
    findings = evaluate_reachable_complexity(shapes, [subject], Limit(max=0.5))
    assert len(findings) == 1
    assert findings[0].rule == "structure.reachable_complexity"
    assert "lower bound" in findings[0].message
    assert "Python call sites" in findings[0].message


def test_every_recorded_routine_that_was_analysed_is_a_node_of_the_graph(
    shapes: ProjectSnapshot,
) -> None:
    """The whole requested tree is the seed set, so no recorded routine may be missing.

    A routine the graph does not hold is one no rule may judge; if the extraction dropped
    routines silently, the rules would report a clean answer over code nobody looked at.
    """
    missing = {
        name for name, key in routines(shapes).items() if key.token not in shapes.call_graph_holds
    }
    assert missing == set()


def test_the_measured_resolution_is_reported_for_the_record(
    shapes: ProjectSnapshot, capsys: pytest.CaptureFixture[str]
) -> None:
    """Print the figures this module's documentation quotes, so a reader can re-measure them.

    Not an assertion about a number: the exact counts move with the fixture and with the
    Understand build. What is asserted elsewhere is the *shape* -- Python worse than C++, every
    call site counted once -- and this test exists so that the numbers behind that shape are
    visible in the run rather than only in a docstring.
    """
    with capsys.disabled():
        for language, found in sorted(shapes.call_resolution.items()):
            print(
                f"\n  {language}: {found.total} call sites, "
                f"resolved {found.resolved} ({found.internal:.1%}), "
                f"external {found.external}, "
                f"unresolved {found.unresolved} ({1 - (found.bound or 0):.1%})"
            )
