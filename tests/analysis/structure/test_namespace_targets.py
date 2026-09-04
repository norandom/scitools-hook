"""Targets that hold no code, and the filter that keeps them out of the dependency count.

Separate from ``test_coupling.py`` because it is a separate question. That file asks what
:func:`new_dependencies` does with a set of edges; this one asks which edges should have
reached it -- a package initialiser an import merely *traverses* is not something the
importer depends on.

The distinction that carries the weight is **absent is not zero**: a file the analyser could
not read has no metrics at all, and reading that as "holds no code" would drop its edges and
report a smaller number over code nobody analysed.
"""

from __future__ import annotations

from typing import Final

from fixtures import APP, ENGINE

from scitools_hook.analysis.structure.coupling import (
    namespace_targets,
    new_dependencies,
    without_namespace_targets,
)
from scitools_hook.models.snapshot import (
    DepEdge,
    EntityKey,
    EntityRecord,
    EntityRef,
    ProjectSnapshot,
)


def edge(src: str, dst: str, refs: int = 1) -> DepEdge:
    """One dependency edge with a reference count."""
    return DepEdge(src=src, dst=dst, refs=refs)


def deps(src: str, count: int, prefix: str = "src/new") -> list[DepEdge]:
    """``count`` distinct dependencies of ``src``, named so that their order is obvious."""
    return [edge(src, f"{prefix}/dep{index}.py") for index in range(count)]


NAMESPACE: Final = "src/cli/__init__.py"


def file_record(path: str, code_lines: float | None) -> EntityRecord:
    """One file entity; ``code_lines`` of ``None`` means the metric was never measured."""
    return EntityRecord(
        ref=EntityRef(
            key=EntityKey(scope="file", path=path, longname=path),
            kind="File",
            name=path.rsplit("/", 1)[-1],
        ),
        language="Python",
        metrics={} if code_lines is None else {"CountLineCode": code_lines},
    )


def snapshot_of(*files: tuple[str, float | None]) -> ProjectSnapshot:
    """An after snapshot holding just the named file entities."""
    records = [file_record(path, code) for path, code in files]
    return ProjectSnapshot(side="after", entities={record.key: record for record in records})


def test_a_file_holding_no_code_is_a_namespace() -> None:
    """``CountLineCode`` 0 is a docstring-only package initialiser: nothing to depend on."""
    assert namespace_targets(snapshot_of((NAMESPACE, 0.0))) == frozenset({NAMESPACE})


def test_an_initialiser_that_declares_something_is_not_a_namespace() -> None:
    """A package whose ``__init__.py`` holds the API is a real dependency."""
    assert namespace_targets(snapshot_of((NAMESPACE, 20.0))) == frozenset()


def test_an_unmeasured_file_is_not_treated_as_empty() -> None:
    """Absent is not zero.

    A file the analyser could not read has no metrics at all. Reading that as "holds no
    code" would drop its edges from the count and report a smaller number over code nobody
    analysed -- the silent green this project keeps meeting.
    """
    assert namespace_targets(snapshot_of((NAMESPACE, None))) == frozenset()


def test_an_empty_target_does_not_count_towards_the_limit() -> None:
    """Six edges, one of them to a namespace, is five dependencies and does not block."""
    after = [*deps(APP, 5), edge(APP, NAMESPACE)]

    kept = without_namespace_targets(after, {NAMESPACE})

    assert kept is not None
    assert new_dependencies([], kept, {APP}, 5) == []
    assert len(new_dependencies([], after, {APP}, 5)) == 1


def test_filtering_a_target_does_not_invent_a_gain_on_the_before_side() -> None:
    """The filter runs on both sides, so a dropped target cannot look like a new one."""
    before = [edge(APP, NAMESPACE), edge(APP, ENGINE)]
    after = [edge(APP, NAMESPACE), edge(APP, ENGINE), *deps(APP, 1)]
    skip = {NAMESPACE}

    kept_after = without_namespace_targets(after, skip)

    assert kept_after is not None
    assert new_dependencies(without_namespace_targets(before, skip), kept_after, {APP}, 1) == []


def test_whole_project_mode_survives_the_filter_as_an_absent_before_side() -> None:
    """``None`` means "there is no before side", which an empty list does not."""
    assert without_namespace_targets(None, {NAMESPACE}) is None
