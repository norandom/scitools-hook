"""What Understand records behind the reference facts (6.1; req 1.2, 1.4, 3.1).

The kind strings in ``worker_lean``, asked of the real API on the three databases
``lean_references`` measures. The rules those facts feed are ``test_lean_references_contract``'s
question; this module is about the claims under them.

**On the contract project**, the Python and C++ inheritance kinds answer the derived class,
through the kind the design records for each language, and the reference a subclass carries
back is in neither set.

**On a scratch project with defaulted parameters**, the question task 3.1's review left open:
whether Understand records a ``Set Init`` against a defaulted parameter's own declaration.
Measured: it does not, in either language -- the declaration carries ``Definein`` and nothing
else -- and a parameter the body *reassigns* carries ``Setby``, which is the shape ``setby``
is in the set for.

**On a scratch project in the six languages the contract project does not build**, the
``derive`` direction the design could only read off the documentation: for Ada and Pascal the
kind list pairs it as ``Derive (Derivefrom)`` where C++ and C# pair it as ``Base (Derive)``,
and the design recorded a risk that the two spellings read in opposite directions. Measured,
they do not: ``Derive`` sits on the base naming the derived type in all four, and it is
``Derivefrom`` that the Ada and Pascal derived type carries back. Java, which the kind list
files under ``Couple``, answers ``Extendby Coupleby`` and ``Implementby Coupleby`` on the
base, which the ``extendby`` and ``implementby`` members already match.
"""

from __future__ import annotations

from typing import Final

import pytest
from contract_project import (
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)
from lean_references import (
    BASES,
    DERIVED,
    EXTENDS,
    IMPLEMENTS,
    NEVER_READ,
    OVERRIDDEN_METHOD,
    PARAMETER,
    READ,
    REASSIGNED,
    Defaulted,
    Measured,
    Probed,
)

# pytest registers a fixture from the namespace of the module the test is in, so the three
# module-scoped fixtures are re-exported here by name: built once per module that asks.
from lean_references import defaulted as defaulted
from lean_references import languages as languages
from lean_references import measured as measured

from scitools_hook.understand import worker_lean

pytestmark = pytest.mark.contract

INHERITANCE_KIND: Final = {"Python": "Python Inheritby", "C++": "C Public Derive"}
"""The kind the design records as firing on each base class (tasks 1.6, 1.7), re-measured."""


# --- inheritance on the fixture (req 1.4, 3.1) --------------------------------------------


@pytest.mark.parametrize("language", sorted(BASES))
def test_contract_the_inheritance_kind_answers_the_derived_class(
    measured: Measured, language: str
) -> None:
    """The base class's ``derived`` names its one subclass, through the kind the design records.

    Both halves are asserted: the snapshot's fact, which is what the rule reads, and the
    kind that produced it, which is what the design's per-language claim is about.
    """
    base, derived = BASES[language], DERIVED[language]

    assert measured.facts(base).derived == [derived]
    fired = measured.probed.by_longname(language, base)[0]
    assert fired.kinds(worker_lean.DERIVED_KINDS) == [INHERITANCE_KIND[language]]
    assert fired.targets(worker_lean.DERIVED_KINDS) == [derived.split(".")[-1].split("::")[-1]]


@pytest.mark.parametrize("language", sorted(BASES))
def test_contract_the_derived_class_carries_no_inheritance_kind_of_the_set(
    measured: Measured, language: str
) -> None:
    """The reference a subclass carries back to its base is in neither set (design, 3.2).

    Were it, a subclass would count as a *user* of its base and no base class would ever be
    a single implementation.
    """
    derived = measured.probed.by_longname(language, DERIVED[language])[0]

    assert derived.kinds(worker_lean.DERIVED_KINDS) == []
    assert measured.facts(DERIVED[language]).derived == []


# --- the defaulted-parameter question (req 1.2, task 3.1's review) -----------------------


@pytest.mark.parametrize("language", ["Python", "C++"])
def test_contract_a_defaulted_parameter_carries_no_set_reference_of_its_own(
    defaulted: Defaulted, language: str
) -> None:
    """The declaration of a defaulted parameter records ``Definein`` and nothing else.

    This is the measurement task 3.1's review asked for. Had a ``Set Init`` landed here,
    ``setby`` would make every defaulted parameter read as used; it does not, and the
    parameter is reported.
    """
    never_read = defaulted.parameter(language, NEVER_READ)

    assert [k.split()[-1] for k in never_read.kinds()] == ["Definein"], never_read.refs
    assert never_read.kinds(worker_lean.PARAMETER_USE) == []
    assert defaulted.unused_of(NEVER_READ)[language] == [PARAMETER]


@pytest.mark.parametrize("language", ["Python", "C++"])
def test_contract_a_reassigned_parameter_is_what_setby_is_in_the_set_for(
    defaulted: Defaulted, language: str
) -> None:
    """A parameter the body writes and never reads carries ``Setby`` and is not reported.

    ``PARAMETER_USE`` says the finding is "nothing in the body mentions this", so a write
    counts; this is the case that distinguishes that reading from ``VARIABLE_USE``'s.
    """
    reassigned = defaulted.parameter(language, REASSIGNED)

    assert [k.split()[-1] for k in reassigned.kinds()] == ["Definein", "Setby"], reassigned.refs
    assert defaulted.unused_of(REASSIGNED)[language] == []
    assert defaulted.unused_of(READ)[language] == []


# --- derive, in the languages the design could only read about ---------------------------


@pytest.mark.parametrize("language", sorted(EXTENDS))
def test_contract_the_inheritance_kinds_sit_on_the_base_in_every_language(
    languages: Probed, language: str
) -> None:
    """``DERIVED_KINDS`` on the base names the derived type and on the derived type names nothing.

    For Ada and Pascal this falsifies the design's reading that ``Derive`` is the forward
    reference a derived type carries: measured, ``Derive`` is on the base in both, and what
    the derived type carries is ``Derivefrom``, which the set does not match. Java's
    ``Extendby Coupleby`` is matched by ``extendby``.
    """
    base, derived = EXTENDS[language]

    assert languages.named(language, base).targets(worker_lean.DERIVED_KINDS) == [derived]
    assert languages.named(language, derived).kinds(worker_lean.DERIVED_KINDS) == []


@pytest.mark.parametrize("language", sorted(IMPLEMENTS))
def test_contract_an_interface_names_its_implementer_through_the_set(
    languages: Probed, language: str
) -> None:
    base, derived = IMPLEMENTS[language]

    assert languages.named(language, base).targets(worker_lean.DERIVED_KINDS) == [derived]
    assert languages.named(language, derived).kinds(worker_lean.DERIVED_KINDS) == []


@pytest.mark.parametrize("language", sorted(EXTENDS))
def test_contract_the_override_reference_fires_on_the_derived_method(
    languages: Probed, language: str
) -> None:
    """``OVERRIDE_KINDS`` matches on the overriding method in every language with one.

    Fortran's scratch type declares no procedure, so it is the one language of the six with
    nothing to override and it asserts that nothing fired.
    """
    methods = [
        e
        for e in languages.entities
        if e.language == language and e.name.lower() == OVERRIDDEN_METHOD
    ]
    overriding = [e for e in methods if e.kinds(worker_lean.OVERRIDE_KINDS)]

    if language == "Fortran":
        assert methods == []
        return
    assert len(methods) == 2, [e.longname for e in methods]
    assert len(overriding) == 1, [(e.longname, e.kinds()) for e in methods]
    assert overriding[0].targets(worker_lean.OVERRIDE_KINDS) == [overriding[0].name]
