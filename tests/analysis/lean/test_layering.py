"""The over-export rule: a file that holds one definition for one importer (req 4.1-4.3).

Most of what is asserted here is what the rule must **not** say. Requirement 4.2 draws three
lines that a naive "one definition per file is bad" rule would cross: a file nothing depends
on is the dead-code rule's finding and not this one, a file two files depend on is a shared
module doing its job, and a package initialiser holding one re-export is the idiom of four
languages rather than a mistake. Each of those has a test of its own below, because the rule's
value is entirely in its silence -- measured on a 653-file repository, twelve files define
exactly one routine or class and exactly one of them is a finding.

The snapshots are built by hand. Neither this repository nor any other one measured supplies
a genuine instance reliably, so a test that leaned on a real project would assert nothing on
the day the project changed.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.analysis.lean.layering import find_over_exports
from scitools_hook.config.models import DEFAULT_LEAN_OVER_EXPORT_IGNORE, LeanRules
from scitools_hook.models.snapshot import (
    Definition,
    DepEdge,
    EntityKey,
    EntityRecord,
    EntityRef,
    ProjectSnapshot,
)

FACTORY: Final = "src/app/factory.py"
CALLER: Final = "src/app/service.py"
OTHER: Final = "src/app/report.py"


def file_record(path: str, functions: float = 1.0, classes: float = 0.0) -> EntityRecord:
    """The record a snapshot holds for a file, carrying its two declaration counts."""
    key = EntityKey(scope="file", path=path, longname=path)
    return EntityRecord(
        ref=EntityRef(key=key, kind="File", name=path.rpartition("/")[2], line=1),
        language="Python",
        metrics={"CountDeclFunction": functions, "CountDeclClass": classes},
    )


def unmeasured(path: str, **counts: float) -> EntityRecord:
    """A file record carrying only the declaration counts named, and nothing for the rest."""
    key = EntityKey(scope="file", path=path, longname=path)
    return EntityRecord(
        ref=EntityRef(key=key, kind="File", name=path.rpartition("/")[2], line=1),
        language="Python",
        metrics=dict(counts),
    )


def snap(
    records: list[EntityRecord],
    edges: list[DepEdge],
    definitions: list[Definition] | None = None,
) -> ProjectSnapshot:
    """An after snapshot carrying only what the over-export rule reads."""
    return ProjectSnapshot(
        side="after",
        entities={record.key: record for record in records},
        file_edges=edges,
        definitions=definitions or [],
    )


def one_importer(path: str = FACTORY, importer: str = CALLER) -> ProjectSnapshot:
    """The finding's shape: one routine in ``path``, and ``importer`` the only dependant."""
    return snap(
        [file_record(path), file_record(importer, functions=4.0)],
        [DepEdge(src=importer, dst=path, refs=2)],
    )


# --- the finding ------------------------------------------------------------------


def test_one_definition_with_one_dependant_is_reported_against_the_file() -> None:
    """Requirement 4.1: the file is the finding and the dependant is named in it."""
    (finding,) = find_over_exports(one_importer(), {FACTORY})

    assert finding.kind == "structural"
    assert finding.rule == "structure.over_export"
    assert finding.scope == "file"
    assert finding.metric is None
    assert finding.path == FACTORY
    assert finding.value == 1
    assert finding.before is None
    assert finding.limit is None
    assert finding.limit_source == "rule"
    assert finding.severity == "warning"
    assert finding.blocking is False
    assert finding.preexisting is False
    assert finding.hint == ""
    assert finding.details == {"dependant": CALLER}
    assert FACTORY in finding.message
    assert CALLER in finding.message


def test_a_single_class_counts_as_the_one_definition() -> None:
    """The rule is over routines *and* classes: one class alone is the same shape (req 4.1)."""
    after = snap(
        [file_record(FACTORY, functions=0.0, classes=1.0), file_record(CALLER, functions=3.0)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1)],
    )

    (finding,) = find_over_exports(after, {FACTORY})

    assert finding.path == FACTORY


def test_several_edges_between_one_pair_are_one_dependant() -> None:
    """Understand emits an edge per reference kind; the rule counts files, not edges."""
    after = snap(
        [file_record(FACTORY), file_record(CALLER, functions=4.0)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1), DepEdge(src=CALLER, dst=FACTORY, refs=7)],
    )

    (finding,) = find_over_exports(after, {FACTORY})

    assert finding.details["dependant"] == CALLER


def test_a_file_depending_on_itself_is_not_its_own_dependant() -> None:
    """A self-reference is not a coupling here, as it is not a cycle in the graph rules."""
    after = snap(
        [file_record(FACTORY), file_record(CALLER, functions=4.0)],
        [DepEdge(src=FACTORY, dst=FACTORY, refs=1), DepEdge(src=CALLER, dst=FACTORY, refs=1)],
    )

    (finding,) = find_over_exports(after, {FACTORY})

    assert finding.details["dependant"] == CALLER


def test_an_error_severity_makes_the_finding_blocking() -> None:
    """Severity travels from the configured rule, as every structural rule's does (req 4.3)."""
    (finding,) = find_over_exports(one_importer(), {FACTORY}, severity="error")

    assert finding.severity == "error"
    assert finding.blocking is True


def test_findings_are_reported_in_path_order() -> None:
    """Two over-exporting files in one change, in the order a reader meets them."""
    after = snap(
        [
            file_record(FACTORY),
            file_record(OTHER),
            file_record(CALLER, functions=4.0),
        ],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1), DepEdge(src=CALLER, dst=OTHER, refs=1)],
    )

    found = find_over_exports(after, {FACTORY, OTHER})

    assert [finding.path for finding in found] == [FACTORY, OTHER]


# --- what the rule refuses to say -------------------------------------------------


def test_a_file_nothing_depends_on_is_not_this_rules_finding() -> None:
    """Requirement 4.2, said explicitly: no dependant is the dead-code rule's question."""
    after = snap([file_record(FACTORY)], [])

    assert find_over_exports(after, {FACTORY}) == []


def test_a_file_two_files_depend_on_is_a_shared_module() -> None:
    """Requirement 4.2: two importers make it a boundary, which is what a module is for."""
    after = snap(
        [file_record(FACTORY), file_record(CALLER, functions=4.0), file_record(OTHER)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1), DepEdge(src=OTHER, dst=FACTORY, refs=1)],
    )

    assert find_over_exports(after, {FACTORY}) == []


def test_a_second_module_level_definition_takes_the_file_out_of_the_rule() -> None:
    """One routine beside a module constant is a module with state, not a renamed name."""
    after = snap(
        [file_record(FACTORY), file_record(CALLER, functions=4.0)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1)],
        [Definition(name="TIMEOUT_S", path=FACTORY, line=3, value="30")],
    )

    assert find_over_exports(after, {FACTORY}) == []


def test_a_definition_in_another_file_does_not_excuse_this_one() -> None:
    """The definitions walk is project-wide; only the file's own bindings count against it."""
    after = snap(
        [file_record(FACTORY), file_record(CALLER, functions=4.0)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1)],
        [Definition(name="TIMEOUT_S", path=CALLER, line=3, value="30")],
    )

    (finding,) = find_over_exports(after, {FACTORY})

    assert finding.path == FACTORY


def test_two_declarations_are_not_one_definition() -> None:
    """A file with a routine *and* a class holds more than the one name this rule is about."""
    after = snap(
        [file_record(FACTORY, functions=1.0, classes=1.0), file_record(CALLER, functions=4.0)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1)],
    )

    assert find_over_exports(after, {FACTORY}) == []


def test_a_file_declaring_nothing_is_not_reported() -> None:
    """A namespace file with one importer declares no name to move anywhere."""
    after = snap(
        [file_record(FACTORY, functions=0.0), file_record(CALLER, functions=4.0)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1)],
    )

    assert find_over_exports(after, {FACTORY}) == []


def test_a_package_initialiser_is_excluded_by_the_shipped_ignore_list() -> None:
    """Requirement 4.2: ``__init__.py`` re-exporting one name is the idiom, not the finding."""
    initialiser = "src/app/thing/__init__.py"
    after = snap(
        [file_record(initialiser), file_record(CALLER, functions=4.0)],
        [DepEdge(src=CALLER, dst=initialiser, refs=1)],
    )

    found = find_over_exports(after, {initialiser}, ignore=list(DEFAULT_LEAN_OVER_EXPORT_IGNORE))

    assert found == []


def test_the_shipped_list_excuses_the_initialiser_of_every_language_the_rule_meets() -> None:
    """``index.*``, ``mod.rs`` and ``__main__.py`` are the same idiom in three other worlds."""
    for path in ("src/ui/index.ts", "src/lib/mod.rs", "src/app/__main__.py"):
        after = snap(
            [file_record(path), file_record(CALLER, functions=4.0)],
            [DepEdge(src=CALLER, dst=path, refs=1)],
        )

        assert (
            find_over_exports(after, {path}, ignore=list(DEFAULT_LEAN_OVER_EXPORT_IGNORE)) == []
        ), path


def test_an_operators_own_pattern_excuses_a_file() -> None:
    """The list is path globs in the language ``[project]`` speaks, not entity names."""
    assert find_over_exports(one_importer(), {FACTORY}, ignore=["src/app/*.py"]) == []


def test_a_file_the_change_did_not_touch_is_not_reported() -> None:
    """This is a gate on a commit: another file's over-export is not this change's finding."""
    assert find_over_exports(one_importer(), {CALLER}) == []


def test_a_file_measured_for_neither_count_is_not_judged() -> None:
    """No declaration counts is not "no declarations": an unreadable file is not evidence.

    This case does **not** prove the presence guard on its own, and it used to claim it did:
    with both counts absent the sum is not 1 either, so deleting ``None not in counts`` from
    ``_holds_one_export`` leaves this test green. The two tests below are the discriminating
    ones -- and the reason coverage did not catch the gap is that an ``and`` short-circuit
    records no arc, so a guard fused into a boolean expression can be deleted with the branch
    number unchanged. That holds for every rule in this family.
    """
    after = snap(
        [unmeasured(FACTORY), file_record(CALLER, functions=4.0)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1)],
    )

    assert find_over_exports(after, {FACTORY}) == []


def test_a_file_measured_for_only_one_of_the_two_counts_is_not_judged() -> None:
    """One routine and **no class measurement** is not evidence of a single definition.

    The record that discriminates: the sum is 1 and the file has one dependant, so every
    other condition of the rule is met and only the presence guard stands between this and a
    finding about a file whose classes were never counted.
    """
    after = snap(
        [unmeasured(FACTORY, CountDeclFunction=1.0), file_record(CALLER, functions=4.0)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1)],
    )

    assert find_over_exports(after, {FACTORY}) == []


def test_a_file_measured_for_classes_alone_is_not_judged() -> None:
    """The same case with the counts the other way round, so neither metric is special."""
    after = snap(
        [unmeasured(FACTORY, CountDeclClass=1.0), file_record(CALLER, functions=4.0)],
        [DepEdge(src=CALLER, dst=FACTORY, refs=1)],
    )

    assert find_over_exports(after, {FACTORY}) == []


def test_a_file_with_no_record_at_all_is_not_judged() -> None:
    """An affected path outside the analysis root has no metrics to reason from."""
    after = snap([file_record(CALLER, functions=4.0)], [DepEdge(src=CALLER, dst=FACTORY, refs=1)])

    assert find_over_exports(after, {FACTORY}) == []


def test_nothing_affected_is_nothing_reported() -> None:
    """The empty change, which every rule here has to survive."""
    assert find_over_exports(one_importer(), set()) == []


# --- the shipped stance -----------------------------------------------------------


def test_the_rule_ships_off() -> None:
    """Requirement 4.3: the whole family is silent until an operator names a severity."""
    assert LeanRules().over_export is None


def test_the_rule_defaults_to_a_warning_when_it_is_called() -> None:
    """Requirement 4.3's second half: enabled, it warns rather than blocks."""
    (finding,) = find_over_exports(one_importer(), {FACTORY})

    assert finding.severity == "warning"
