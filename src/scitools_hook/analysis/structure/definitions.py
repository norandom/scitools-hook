"""The duplicate-definition rule: one value copied into many files, never collected.

The shape this catches is a constant that has no home. Measured on a 770-file project:
``_HORIZON_DAYS = 20`` in fifteen modules and ``_HORIZON_DAYS = 5`` in a sixteenth,
``_ZERO = Decimal("0")`` in nine, ``_ONE = Decimal("1")`` in seven with a tenth file binding
the same name to the float ``1.0``. Nothing in that project is over any complexity limit
because of it, and every one of those files reads perfectly well on its own. The cost lands
on whoever has to *change* the policy: the definition is the fifteen files, and one of them
disagrees, and no amount of reading one file reveals either fact.

That is why the rule keys on **name and value together** rather than on the name alone. A
name repeated with a different value in each file is usually deliberate local vocabulary --
``HELP`` in every subcommand module of this project, ``__all__`` in every package -- and
flagging it would bury the real finding under the idiom. A name repeated with the *same*
value is a decision that was copied instead of shared.

The count is over the whole project and the finding is reported against the affected files,
so the commit that adds the sixteenth copy is told about the fifteen, and a commit that
touches none of them is told nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence

from scitools_hook.config.models import Severity
from scitools_hook.models.findings import Finding, structure_rule
from scitools_hook.models.snapshot import Definition

_RULE = structure_rule("duplicate_definition")

NAMED_ELSEWHERE = 3
"""How many other homes a finding names before it stops listing them.

A constant in twenty files would otherwise produce a twenty-line finding, and the point of
the message is that the reader should go and look, not that they should read the list here.
"""


def find_duplicate_definitions(
    definitions: Sequence[Definition],
    files: Collection[str],
    max_files: int,
    severity: Severity = "warning",
    ignore: Collection[str] = (),
) -> list[Finding]:
    """Report each affected file that repeats a definition more than ``max_files`` files share.

    ``definitions`` is the whole project's module-level bindings, ``files`` the affected set.
    A definition whose value could not be read is skipped rather than grouped: two unreadable
    initialisers are not evidence of anything, and treating them as equal would report an
    augmented assignment and a tuple unpacking as copies of one another.

    ``ignore`` names bindings the rule does not ask about. It exists for the per-module idiom:
    ``log = logging.getLogger(__name__)`` and ``pytestmark = pytest.mark.slow`` are written
    identically in every module *by design*, and a rule that reported them would bury the
    findings that matter. It is deliberately a list of names rather than a pattern over
    values, because the shape it excuses is "this name is always written out", and the
    similar-looking ``PROJECT_ROOT = Path(__file__).resolve().parents[2]`` -- which the same
    project also writes with ``parents[1]`` in six other files -- is a real finding.

    One finding per affected file per repeated definition, in path then line order.
    """
    excused = frozenset(ignore)
    groups = _by_name_and_value(definitions, excused)
    findings: list[Finding] = []
    for (name, value), places in sorted(groups.items()):
        if len(_homes(places)) <= max_files:
            continue
        for place in places:
            if place.path in files:
                findings.append(_finding(name, value, place, places, severity))
    return sorted(findings, key=lambda found: (found.path, found.line or 0, found.rule))


def _by_name_and_value(
    definitions: Sequence[Definition], excused: frozenset[str]
) -> dict[tuple[str, str], list[Definition]]:
    """Group the readable definitions by the name *and* the text bound to it."""
    groups: dict[tuple[str, str], list[Definition]] = defaultdict(list)
    for definition in definitions:
        if definition.value is not None and definition.name not in excused:
            groups[(definition.name, definition.value)].append(definition)
    return groups


def _homes(places: Sequence[Definition]) -> set[str]:
    """The distinct files a definition is written in.

    Distinct *files*, not occurrences: a module that binds the same name twice at different
    lines has a problem of its own, and it is not this one.
    """
    return {place.path for place in places}


def _finding(
    name: str,
    value: str,
    place: Definition,
    places: Sequence[Definition],
    severity: Severity,
) -> Finding:
    """One affected file's copy of a definition that lives in several files."""
    elsewhere = sorted(_homes(places) - {place.path})
    return Finding(
        kind="structural",
        rule=_RULE,
        scope="file",
        path=place.path,
        line=place.line,
        value=float(len(_homes(places))),
        limit=None,
        limit_source="rule",
        severity=severity,
        blocking=severity == "error",
        message=_message(name, value, place, elsewhere),
        details={"definition": name, "bound_to": value, "also_in": elsewhere},
    )


def _message(name: str, value: str, place: Definition, elsewhere: Sequence[str]) -> str:
    """One line naming the constant, what it is bound to, and where else it is written."""
    shown = ", ".join(elsewhere[:NAMED_ELSEWHERE])
    rest = len(elsewhere) - NAMED_ELSEWHERE
    more = f" and {rest} more" if rest > 0 else ""
    return (
        f"{place.path} binds {name} to {value}, which {len(elsewhere)} other "
        f"{'file' if len(elsewhere) == 1 else 'files'} also bind to the same value: "
        f"{shown}{more}"
    )
