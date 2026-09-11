"""The five reference rules against the installed build (6.1; req 1.3, 1.4, 2.1, 10.5).

What the rules *read* and what they *report*, on the contract project ``lean_references``
measures: both overrides are flagged, the caller count agrees with the plugin caller metric on
every routine, and each reference rule reports its planted case and nothing else -- which is
also where the *floors* are measured: at the shipped numbers this project's own database is
refused, and the rule tests lower them to reach the cases. What Understand records behind
those facts, kind by kind, is ``test_lean_reference_kinds_contract``'s question.

**The table is printed rather than remembered.** The last test writes what every measurement
of the family found, per language, in the shape the research log carries it, so that the log
and the run cannot disagree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from contract_project import (
    FILES,
    SampleProject,
    real_env,
    run_und,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)
from lean_references import (
    BASE_METHODS,
    BASES,
    DERIVED,
    EXTENDS,
    IMPLEMENTS,
    NEVER_READ,
    OVERRIDDEN_METHOD,
    OVERRIDES,
    PARAMETER,
    READ,
    REASSIGNED,
    Defaulted,
    Entity,
    Measured,
    Probed,
)

# pytest registers a fixture from the namespace of the module the test is in, so the three
# module-scoped fixtures are re-exported here by name: built once per module that asks.
from lean_references import defaulted as defaulted
from lean_references import languages as languages
from lean_references import measured as measured

from scitools_hook.analysis.lean.dead import (
    Trust,
    find_unused_classes,
    find_unused_parameters,
    find_unused_variables,
)
from scitools_hook.analysis.lean.layering import (
    PassThroughLimits,
    find_pass_through,
    find_single_implementations,
)
from scitools_hook.understand import worker_lean
from scitools_hook.understand.und_cli import ACCURACY_LINE

pytestmark = pytest.mark.contract

UNUSED_PARAMETERS: Final = {("dead.advance", "verbose"), ("native_advance", "verbose")}
UNUSED_CLASSES: Final = {"dead.ForgottenReport", "ForgottenNativeReport"}
UNUSED_VARIABLES: Final = {("lean/dead.py", "RETRY_LIMIT"), ("native/lean_dead.cpp", "kRetryLimit")}
PASS_THROUGHS: Final = {
    ("layers.display_name", "layers.canonical_name"),
    ("display_native_name", "canonical_native_name"),
}
SINGLE_IMPLEMENTATIONS: Final = {
    ("layers.BaseChannel", "layers.OnlyChannel"),
    ("BaseNativeChannel", "OnlyNativeChannel"),
}
"""One planted case per language per rule, and nothing else may be reported."""


# --- the accuracy figure, and the two trusts built on it (req 1.8) ------------------------


def measure_accuracy(db: Path) -> float:
    """What ``und analyze -accuracy`` reports for the database, re-analysed whole."""
    done = run_und("-db", str(db), "analyze", "-all", "-accuracy")
    assert done.returncode == 0, done.stderr
    for line in done.stdout.splitlines():
        found = ACCURACY_LINE.match(line)
        if found is not None:
            return int(found["clean"]) / int(found["parsed"])
    pytest.fail(f"no accuracy line in:\n{done.stdout}")


@pytest.fixture(scope="module")
def accuracy(sample_project: SampleProject) -> float:  # noqa: F811
    """The alpha side's accuracy figure, which is what every trust below is built on."""
    return measure_accuracy(sample_project.db("alpha"))


def admitted(accuracy: float) -> Trust:
    """The measured accuracy with both floors lowered to zero.

    The rule tests are about what the reference facts *say*, and at the shipped floors this
    fixture's own database is refused -- which is measured by its own test below rather
    than hidden here.
    """
    return Trust(accuracy=accuracy, resolution_floor=0.0, accuracy_floor=0.0)


def shipped(accuracy: float) -> Trust:
    """The measured accuracy at the floors ``[lean]`` ships."""
    return Trust(accuracy=accuracy)


# --- the override fact on the fixture (req 1.4) --------------------------------------------


@pytest.mark.parametrize("language", sorted(BASES))
def test_contract_the_override_is_flagged_and_the_base_method_is_not(
    measured: Measured, language: str
) -> None:
    """Requirement 1.4's exclusion has a reference behind it in both languages.

    The C++ half is the one the fixture could not assume: ``native/lean_layers.cpp`` was
    written so that its override is excused by requirement 2.2 whether or not this build
    records an ``overrides`` reference for a virtual member. It does.
    """
    assert measured.facts(OVERRIDES[language]).overrides is True
    assert measured.facts(BASE_METHODS[language]).overrides is False


# --- callers against the plugin metric (req 2.1) -------------------------------------------


def test_contract_the_caller_count_agrees_with_the_plugin_caller_metric(
    measured: Measured,
) -> None:
    """``LeanFacts.callers`` equals ``CountCallbyUnique`` for every routine of the fixture.

    The two count different things -- the worker counts distinct project *routines* whose
    bodies call the entity, the plugin counts distinct referencing entities of any kind from
    any file -- and the module docstring of ``worker_lean`` records the two shapes where they
    part: a call from module scope, which the plugin counts and the worker does not, and a
    call from outside the analysis root. This fixture contains neither, so the two agree on
    every routine, and this test is what says so rather than a sentence. Overloads share a
    long name, so the comparison is between sorted count lists per name.
    """
    probed = {(e.language, e.longname): e for e in measured.probed.routines()}
    by_name: dict[tuple[str, str], list[int]] = {}
    for key, record in measured.snapshot.entities.items():
        if key.scope == "routine":
            assert record.lean is not None and record.lean.callers is not None, key
            by_name.setdefault((record.language, key.longname), []).append(record.lean.callers)
    assert by_name, "the fixture must record routines"

    disagreements = {}
    for name, counts in by_name.items():
        plugin = sorted(
            e.callby_unique or 0
            for e in measured.probed.entities
            if (e.language, e.longname) == name and e.callby is not None
        )
        if sorted(counts) != plugin:
            disagreements[name] = (sorted(counts), plugin)
    assert disagreements == {}, disagreements
    assert set(by_name) <= set(probed), set(by_name) - set(probed)


# --- the floors on a real database (req 1.8) ---------------------------------------------


def test_contract_at_the_shipped_floors_this_database_is_refused(
    measured: Measured, accuracy: float
) -> None:
    """The contract project itself is below both floors, and the rules say so.

    Not a defect in the fixture: its C++ translation units each carry clang's libstdc++
    note, which ``-accuracy`` counts against them, and its Python resolves under half of its
    call sites, both printed by the table test. It is the shape requirement 1.8 was written
    for, and a rule that reported the planted cases through it would be reporting them for
    the wrong reason.
    """
    outcome = find_unused_parameters(measured.snapshot, measured.keys, trust=shipped(accuracy))

    assert outcome.findings == []
    assert len(outcome.unavailable) == 1
    assert "below the accuracy floor" in outcome.unavailable[0], outcome.unavailable


# --- each reference rule reports its planted case and nothing else -----------------------


def test_contract_the_parameter_rule_reports_the_planted_case_and_nothing_else(
    measured: Measured, accuracy: float
) -> None:
    outcome = find_unused_parameters(
        measured.snapshot,
        measured.keys,
        ignore=measured.rules.unused_parameters_ignore,
        trust=admitted(accuracy),
    )

    assert outcome.unavailable == ()
    assert {
        (f.details["longname"], f.details["parameter"]) for f in outcome.findings
    } == UNUSED_PARAMETERS


def test_contract_the_class_rule_reports_the_planted_case_and_nothing_else(
    measured: Measured, accuracy: float
) -> None:
    outcome = find_unused_classes(
        measured.snapshot,
        measured.keys,
        ignore=measured.rules.unused_classes_ignore,
        trust=admitted(accuracy),
    )

    assert outcome.unavailable == ()
    assert {f.details["longname"] for f in outcome.findings} == UNUSED_CLASSES


def test_contract_the_variable_rule_reports_the_planted_case_and_nothing_else(
    measured: Measured, accuracy: float
) -> None:
    """The rule that needs the definitions walk, on a configuration that enabled it alone.

    An empty ``definitions`` list is indistinguishable from a project without module
    bindings, so this is the rule that can fall silent without saying so; the snapshot here
    was extracted with the five reference rules on and nothing else, which is the
    configuration that has to switch the walk on.
    """
    outcome = find_unused_variables(
        measured.snapshot,
        set(FILES),
        ignore=measured.rules.unused_variables_ignore,
        trust=admitted(accuracy),
    )

    assert outcome.unavailable == ()
    assert {(f.path, f.details["definition"]) for f in outcome.findings} == UNUSED_VARIABLES


def test_contract_the_pass_through_rule_reports_the_planted_case_and_nothing_else(
    measured: Measured, accuracy: float
) -> None:
    rules = measured.rules
    outcome = find_pass_through(
        measured.snapshot,
        measured.keys,
        limits=PassThroughLimits(rules.pass_through_max_statements, rules.pass_through_ignore),
        trust=admitted(accuracy),
    )

    assert outcome.unavailable == ()
    assert {
        (f.details["longname"], f.details["forwards_to"]) for f in outcome.findings
    } == PASS_THROUGHS


def test_contract_the_single_implementation_rule_reports_the_planted_case_and_nothing_else(
    measured: Measured,
) -> None:
    outcome = find_single_implementations(
        measured.snapshot, measured.keys, ignore=measured.rules.single_implementation_ignore
    )

    assert outcome.unavailable == ()
    assert {
        (f.details["longname"], f.details["derived_class"]) for f in outcome.findings
    } == SINGLE_IMPLEMENTATIONS


# --- the table ----------------------------------------------------------------------------


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _short(kinds: list[str]) -> str:
    return ", ".join(kinds) if kinds else "(none)"


TOWARD_BASE: Final = frozenset({"Base", "Inherit", "Derivefrom", "Extend", "Implement", "Couple"})
"""The last word of every kind a derived class carries back to its base, as measured."""


def _toward_base(entity: Entity) -> str:
    """The kinds a derived class or implementer carries back, which no set may match."""
    return _short([k for k in entity.kinds() if k.split()[-1] in TOWARD_BASE])


def _derived_column(entity: Entity) -> str:
    return _short(entity.kinds(worker_lean.DERIVED_KINDS))


def _override_column(probed: Probed, language: str) -> str:
    """The override kind on the one overriding ``send`` of a language, or none."""
    overriding = [
        e.kinds(worker_lean.OVERRIDE_KINDS)
        for e in probed.entities
        if e.language == language and e.name.lower() == OVERRIDDEN_METHOD
    ]
    return _short([kind for kinds in overriding for kind in kinds])


def _interface_column(probed: Probed, language: str) -> str:
    if language not in IMPLEMENTS:
        return ""
    iface, impl = IMPLEMENTS[language]
    return (
        f"; interface: {_derived_column(probed.named(language, iface))}"
        f" / implementer: {_toward_base(probed.named(language, impl))}"
    )


def _figure_rows(measured: Measured, accuracy: float) -> list[str]:
    """The figures the floor test rests on: accuracy, and call resolution per language."""
    return [
        _row("figure", "value"),
        _row("---", "---"),
        _row("accuracy (`und analyze -accuracy`, alpha)", f"{accuracy:.1%}"),
        *(
            _row(
                f"call resolution, {language}",
                f"{found.resolved} of {found.total} call sites resolved ({found.internal:.1%})",
            )
            for language, found in sorted(measured.snapshot.call_resolution.items())
        ),
    ]


def _fixture_kind_row(fixture: Probed, language: str) -> str:
    """One language of the contract project: what the sets match, and every caller kind seen."""
    seen = {k for e in fixture.routines() if e.language == language for k in e.callby_kinds}
    override = fixture.by_longname(language, OVERRIDES[language])[0]
    return _row(
        language,
        _derived_column(fixture.by_longname(language, BASES[language])[0]),
        _toward_base(fixture.by_longname(language, DERIVED[language])[0]),
        _short(override.kinds(worker_lean.OVERRIDE_KINDS)),
        _short(sorted(seen)),
    )


def _scratch_kind_row(languages: Probed, language: str) -> str:
    """One language of the scratch project, with its interface pair where it has one."""
    base, derived = EXTENDS[language]
    return _row(
        f"{language} (scratch)",
        _derived_column(languages.named(language, base)) + _interface_column(languages, language),
        _toward_base(languages.named(language, derived)),
        _override_column(languages, language),
        "n/a",
    )


def _kind_rows(measured: Measured, languages: Probed) -> list[str]:
    """The inheritance and override kinds, on the fixture and on the six scratch languages."""
    return [
        _row("language", "on the base", "on the derived class", "override", "callby kinds seen"),
        _row("---", "---", "---", "---", "---"),
        *(_fixture_kind_row(measured.probed, language) for language in sorted(BASES)),
        *(_scratch_kind_row(languages, language) for language in sorted(EXTENDS)),
    ]


def _parameter_rows(defaulted: Defaulted) -> list[str]:
    """Every reference on each ``verbose`` declaration, and whether the rule reported it."""
    rows = [
        _row("language", "parameter", "every reference on its declaration", "reported unused"),
        _row("---", "---", "---", "---"),
    ]
    for language in ("Python", "C++"):
        for routine in (NEVER_READ, READ, REASSIGNED):
            rows.append(
                _row(
                    language,
                    f"`{routine}({PARAMETER})`",
                    _short(defaulted.parameter(language, routine).kinds()),
                    "yes" if PARAMETER in defaulted.unused_of(routine)[language] else "no",
                )
            )
    return rows


def _caller_line(measured: Measured) -> str:
    """How many routines the caller comparison covered, and where the two plugin metrics agree."""
    routines = measured.probed.routines()
    agreeing = sum(1 for e in routines if e.callby == e.callby_unique)
    return (
        f"callers: {len(routines)} routines probed; `LeanFacts.callers` == `CountCallbyUnique` on "
        f"every one, and `CountCallby` == `CountCallbyUnique` on {agreeing} of {len(routines)}"
    )


def test_contract_the_kind_table_is_printed_for_the_record(
    measured: Measured,
    accuracy: float,
    defaulted: Defaulted,
    languages: Probed,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print every kind the family's tests measured, per language, in the research log's shape.

    Not an assertion: the assertions are in this module and its sibling. This exists so the
    log is a copy of a run rather than a transcription, and so the figures the floor test
    rests on are visible.
    """
    lines = [
        "",
        f"und: {real_env('upython').und}",
        "",
        *_figure_rows(measured, accuracy),
        "",
        *_kind_rows(measured, languages),
        "",
        *_parameter_rows(defaulted),
        "",
        _caller_line(measured),
        "",
    ]
    with capsys.disabled():
        print("\n".join(lines))
