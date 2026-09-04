"""The duplicate-definition rule: one value copied into many files (req 6.5's sibling).

The rule keys on **name and value together**, and most of what is asserted here is that
distinction holding under pressure. A name repeated with a different value in each file is
local vocabulary -- ``HELP`` in every subcommand module of this project -- and flagging it
would bury the real finding under the idiom; a name repeated with the *same* value is a
decision that was copied instead of shared.

The counting is over the whole project and the reporting is over the affected files, so the
two are tested apart: a commit that adds the fourth copy must be told about the other three,
and a commit that touches none of them must be told nothing.
"""

from __future__ import annotations

from typing import Final

import pytest

from scitools_hook.analysis.structure.definitions import find_duplicate_definitions
from scitools_hook.config.models import Severity
from scitools_hook.models.snapshot import Definition

ZERO: Final = 'Decimal("0")'


def binding(path: str, name: str = "_ZERO", value: str | None = ZERO, line: int = 10) -> Definition:
    """One module-level binding."""
    return Definition(name=name, path=path, line=line, value=value)


def copies(count: int, name: str = "_ZERO", value: str | None = ZERO) -> list[Definition]:
    """``count`` files binding ``name`` to the same value."""
    return [binding(f"src/mod{index}.py", name, value) for index in range(count)]


# --- what counts as a duplicate --------------------------------------------------


def test_the_same_value_in_more_files_than_the_limit_is_reported() -> None:
    """Four files sharing one value, a limit of three: every affected copy is named."""
    found = find_duplicate_definitions(copies(4), {"src/mod0.py"}, 3)

    (finding,) = found
    assert finding.rule == "structure.duplicate_definition"
    assert finding.scope == "file"
    assert finding.path == "src/mod0.py"
    assert finding.value == 4
    assert finding.details["definition"] == "_ZERO"
    assert finding.details["bound_to"] == ZERO
    assert finding.details["also_in"] == ["src/mod1.py", "src/mod2.py", "src/mod3.py"]


def test_a_count_exactly_at_the_limit_is_allowed() -> None:
    """A `>` rule, as every other coupling limit in this project is."""
    assert find_duplicate_definitions(copies(3), {"src/mod0.py"}, 3) == []


def test_the_same_name_bound_to_different_values_is_not_a_duplicate() -> None:
    """`HELP` in every subcommand module is vocabulary, not scattered state."""
    definitions = [
        binding("src/check.py", "HELP", '"check a change"'),
        binding("src/explain.py", "HELP", '"explain a change"'),
        binding("src/doctor.py", "HELP", '"report the installation"'),
        binding("src/baseline.py", "HELP", '"capture the baseline"'),
    ]

    assert find_duplicate_definitions(definitions, {"src/check.py"}, 3) == []


def test_a_value_that_could_not_be_read_is_never_grouped() -> None:
    """Two unreadable initialisers are not evidence that two definitions agree.

    ``value`` is ``None`` for an augmented assignment, a tuple unpacking or a bare
    annotation. Grouping them would report unrelated bindings as copies of one another.
    """
    unreadable = [binding(f"src/mod{index}.py", "TOTAL", None) for index in range(5)]

    assert find_duplicate_definitions(unreadable, {"src/mod0.py"}, 3) == []


def test_one_file_binding_the_same_name_twice_is_one_home() -> None:
    """Distinct files, not occurrences. A module that repeats itself has another problem."""
    twice = [
        binding("src/a.py", line=10),
        binding("src/a.py", line=20),
        binding("src/b.py"),
        binding("src/c.py"),
    ]

    assert find_duplicate_definitions(twice, {"src/a.py"}, 3) == []


def test_a_name_the_configuration_excuses_is_not_grouped() -> None:
    """The per-module idiom: written out in every module by design.

    ``log = logging.getLogger(__name__)`` and ``pytestmark = pytest.mark.slow`` are identical
    in every file on purpose. Reporting them would bury the findings that matter.
    """
    idiom = [
        binding(f"src/mod{index}.py", "log", "logging.getLogger(__name__)") for index in range(9)
    ]

    assert find_duplicate_definitions(idiom, {"src/mod0.py"}, 3, "warning", ["log"]) == []


def test_excusing_one_name_does_not_excuse_another() -> None:
    """The escape hatch is a list of names, not a switch for the rule."""
    definitions = [
        *[binding(f"src/m{i}.py", "log", "logging.getLogger(__name__)") for i in range(5)],
        *copies(5),
    ]

    found = find_duplicate_definitions(definitions, {"src/mod0.py"}, 3, "warning", ["log"])

    assert [finding.details["definition"] for finding in found] == ["_ZERO"]


# --- which files hear about it ---------------------------------------------------


def test_only_the_affected_files_are_reported() -> None:
    """The count is project-wide; the finding belongs to the commit that touched a copy."""
    found = find_duplicate_definitions(copies(5), {"src/mod2.py"}, 3)

    assert [finding.path for finding in found] == ["src/mod2.py"]
    assert found[0].value == 5


def test_a_change_touching_no_copy_hears_nothing() -> None:
    """Existing duplication is not this commit's doing."""
    assert find_duplicate_definitions(copies(9), {"src/unrelated.py"}, 3) == []


def test_every_affected_copy_gets_its_own_finding() -> None:
    """A commit that adds two copies at once is told about both."""
    found = find_duplicate_definitions(copies(6), {"src/mod0.py", "src/mod4.py"}, 3)

    assert [finding.path for finding in found] == ["src/mod0.py", "src/mod4.py"]


def test_the_finding_points_at_the_line_the_name_is_bound_on() -> None:
    """A file-scope finding with a line, so a reader lands on the copy rather than the file."""
    definitions = [binding("src/a.py", line=42), *copies(3)]

    (finding,) = find_duplicate_definitions(definitions, {"src/a.py"}, 3)

    assert finding.line == 42


# --- how it is reported ----------------------------------------------------------


@pytest.mark.parametrize("severity", ["error", "warning"])
def test_the_configured_severity_decides_whether_it_blocks(severity: Severity) -> None:
    (finding,) = find_duplicate_definitions(copies(4), {"src/mod0.py"}, 3, severity)

    assert finding.severity == severity
    assert finding.blocking is (severity == "error")


def test_the_message_names_the_value_and_stops_listing_at_three() -> None:
    """A constant in twenty files must not produce a twenty-line finding."""
    (finding,) = find_duplicate_definitions(copies(20), {"src/mod0.py"}, 3)

    assert "_ZERO" in finding.message
    assert ZERO in finding.message
    assert "19 other files" in finding.message
    assert "and 16 more" in finding.message


def test_a_single_other_home_is_said_in_the_singular() -> None:
    (finding,) = find_duplicate_definitions(copies(2), {"src/mod0.py"}, 1)

    assert "1 other file also bind" in finding.message
