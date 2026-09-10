"""Every lean measurement the extractor makes, in a file the worker loads by path.

**One measurement so far.** `routine_facts` answers the callers, callees, forwarding target,
override flag and unused parameter names three rules read; `class_facts` and
`variable_referenced` arrive with the rest of the reference facts and `token_index` with the
duplication rules. The file itself, its place in the architecture gate and the rule it is
held to came first, before any of them, because a file that appears later appears without.

**Why a second file rather than more of `worker.py`.** `worker.py` measures 1 060 of its
1 200 permitted code lines and 119 of its 130 functions, and six lean extractions do not fit
in what is left. The alternatives were both worse: raising the ceiling would be adapting the
tool's own limits to fit a feature it is measuring, and a second *op* would open the database
and walk every entity a second time against a 6.5 s budget. So the measurements sit here, and
`worker.py` calls them from the walk it already makes (design.md, *Architecture Pattern
Evaluation*, option C).

**Why this file may import nothing from `scitools_hook`.** The worker runs under Understand's
own interpreter, `<home>/bin/<plat>/upython`, where this package is not on `sys.path` at all.
`worker.py` reaches this file through `importlib.util.spec_from_file_location` on its own
directory -- a *path* load, not an import statement -- so the file is executed by that same
interpreter and is bound by the same rule: the standard library and nothing else. Not even
the package leaf. `tests/test_import_direction.py` holds both files to an allowance of
`frozenset()` and proves it twice, once by parsing the source and once by executing this
module under `python -I -S` and looking for `scitools_hook` in the modules it created.

A path load leaves no trace in the import graph, which is exactly why the rule is written
down here as well: nothing a reader follows from `worker.py` would lead them to this file,
and nothing a checker walks would either.

**What the worker hands over instead of imports.** Because there is nothing to import, the
two helpers the measurements need -- the project-relative path function and the `understand`
module itself -- arrive as arguments, in a small context object built by `worker.py`. That
keeps one copy of each helper across the two files rather than a copy per file.

**What belongs here and what does not.** This file counts, lists and hashes; it never judges.
Thresholds, ignore lists and severities are the analysis layer's, which reads the facts back
off the snapshot. A measurement that decided whether a number was too large would put the
policy in the one process that cannot be unit-tested without a licence.

**One more consequence.** `snapshot_cache.worker_digest()` hashes the worker's source so that
a changed measurement cannot be answered out of the before-side cache. It has to hash this
file too, or a change confined to the sibling would be served stale.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final


class LeanContext:
    """What the measurements need from ``worker.py``, since they may import nothing from it.

    ``project_path`` is ``worker._project_path``: it answers the repository-relative path of
    a *file* entity and ``None`` for anything Understand injected or anything outside the
    analysis root, which is the whole meaning of "project" in every fact below. ``api`` is the
    ``understand`` module, carried for the measurements that have to catch its own exception
    type; ``routine_facts`` never reads it.

    **Plain attributes rather than a dataclass, and that is not a style choice.** Measured
    here: ``@dataclass`` on a module loaded through ``spec_from_file_location`` without being
    registered in ``sys.modules`` raises ``AttributeError`` inside ``dataclasses._is_type``,
    which reads ``sys.modules.get(cls.__module__).__dict__``. That load is exactly the one
    ``worker._lean_module()`` performs, and the isolated-interpreter test in
    ``tests/test_import_direction.py`` performs it too -- it is what caught this.
    """

    def __init__(self, api: Any, project_path: Callable[[Any, str], str | None], root: str) -> None:
        self.api = api
        self.project_path = project_path
        self.root = root


CALLER_KINDS: Final = "callby"
CALLEE_KINDS: Final = "call"
OVERRIDE_KINDS: Final = "overrides"

CONTAINER_KINDS: Final = "definein, declarein"
"""Reference kinds leading from an entity to the file it is written in: ``worker``'s own.

The third string this file has to write out twice, and bound to ``worker.CONTAINER_REFS`` by
a test for the same reason as the other two: the fake ignores an ``Ent.ref`` filter, so a
kind dropped from here is invisible to every unit test, and a callee whose file this file
could not find is a callee the snapshot's own walk did find.
"""

PARAMETER_REFS: Final = "Define"
PARAMETER_KINDS: Final = "Parameter ~Catch"
"""The pair ``worker._count_params`` already asks ``Ent.ents`` for, spelled the same way.

``CountParams`` and ``unused_parameters`` are two answers about one declaration list, and a
routine whose parameters the two functions disagreed about would report a name the count
says is not there. A test binds the two strings, which is all that can bind them across a
file that may not import.
"""

PARAMETER_USE: Final = "useby, setby, modifyby, callby"
"""What touching a parameter looks like, in every direction Understand records one.

``callby`` is here because measurement put it here: a parameter used only as ``fn()`` has no
``Useby``, ``Setby`` or ``Modifyby`` reference at all, so a set without it reports every
callback parameter as dead. ``setby`` counts although a write is not a read -- the finding is
"nothing in the body mentions this", not "nothing reads it" -- which is the opposite of the
decision the design records for ``variable_referenced``, where a write is deliberately not a
use. The two differ because a parameter's defining assignment is the call site's rather than
the body's, so no reference here is the declaration's own.
"""

ROUTINE_KINDS: Final = (
    "function ~unknown ~unresolved, method ~unknown ~unresolved, "
    "procedure ~unknown ~unresolved, routine ~unknown ~unresolved, "
    "classmethod ~unknown ~unresolved"
)
"""What counts as a routine at either end of a call: ``config.metric_names.SCOPE_KINDS``.

The same string, written out because this file may not import it, and bound to the original
by a test. It has to be the same one: the population these facts count over is the population
the snapshot walk records, and a callee outside it is a name the rule cannot resolve.

**A call reference is not a callee on its own**, in two shapes task 1.6 recorded on the
contract project, each excluded for its own reason:

* An **unresolved** target still sits in a project file -- ``pkg/core.py``'s ``run`` calls
  ``widen``, which Understand records as a ``python Unknown Ambiguous Attribute`` written in
  ``pkg/inner/leaf.py`` -- so nothing about *where* it sits refuses it. Counted, it gives a
  routine a callee it does not have and a pass-through finding whose remedy is to delete a
  call that binds at runtime. That argument is about a real repository, not about the
  fixture: ``run`` was given a statement of its own so that the fixture's one pass-through
  case stays unique whichever way an implementation reads "callee", and citing it as
  *evidence* for this filter was a defect in an earlier draft of this docstring.
* A **constructor** call binds to the class -- ``app/entry.py``'s ``entry_point`` calls
  ``Engine().run(...)`` and the sibling target is ``core.Engine``, a Class. It is excluded
  because the contract says a callee is a project routine and ``forwards_to`` has to name
  one: a pass-through hint asks an agent to inline the callee into its caller, which is not
  something a class can be. Note the direction: refusing it *lowers* a callee count and so
  can only move a routine closer to the rule, which is why the fixture pins ``entry_point``
  with a statement rather than with this filter either.
"""


def routine_facts(ent: Any, ctx: LeanContext) -> dict[str, object]:
    """The reference facts three rules read, for one routine (requirements 1.2-1.4, 2.1).

    ``callers`` and ``callees`` are distinct project routines rather than call sites;
    ``forwards_to`` is the callee's long name when there is exactly one, which is the routine
    a pass-through finding names; ``overrides`` says only that an override reference exists,
    because whether that excuses the routine is the rule's decision; ``unused_parameters``
    holds every parameter name no project reference touches, in declaration order, receivers
    included -- the ignore list one layer up is what excuses ``self``.
    """
    callees = _project_callees(ent, ctx)
    return {
        "callers": len(_project_callers(ent, ctx)),
        "callees": len(callees),
        "forwards_to": str(callees[0].longname()) if len(callees) == 1 else None,
        "overrides": bool(ent.refs(OVERRIDE_KINDS)),
        "unused_parameters": _unused_parameters(ent, ctx),
    }


def _project_callers(ent: Any, ctx: LeanContext) -> list[Any]:
    """The distinct project routines whose bodies call ``ent``.

    Judged by the file the *reference* is written in, which is what the requirements mean by
    a project reference and what ``worker._referenced`` already asks of the routine rule. The
    calling entity's own definition file is a different question and is deliberately not the
    one asked, so that a reference written in an excluded file cannot be counted by way of a
    caller declared in an included one.

    **A call from module scope is not a caller, and in one shape that is a false finding
    rather than a missing one.** Python records a call outside any routine against the file,
    and a file is not a routine. Where module scope is the *only* call site, the routine reads
    as having no caller at all and the pass-through rule cannot reach it, which errs the safe
    way. Where a routine is called once from another routine and once from module scope, the
    count falls from two to one, which is exactly the predicate of requirement 2.1, and the
    rule reports a pass-through for a routine that has two call sites -- a shape the rule
    layer cannot currently tell from a true one, because this document carries a count and not
    the call sites behind it. The error is accepted rather than fixed with a sixth fact: the
    document is the design's contract, and a caller a rule can name belongs in that contract
    rather than beside it.
    """
    found: dict[int, Any] = {}
    for ref in ent.refs(CALLER_KINDS):
        caller = ref.ent()
        if ctx.project_path(ref.file(), ctx.root) is not None and _is_routine(caller):
            found[caller.id()] = caller
    return list(found.values())


def _project_callees(ent: Any, ctx: LeanContext) -> list[Any]:
    """The distinct project routines ``ent`` calls.

    The reference's own file is ``ent``'s and says nothing, so the target decides twice: it
    has to be a routine kind, and it has to be defined in a project file.
    """
    found: dict[int, Any] = {}
    for ref in ent.refs(CALLEE_KINDS):
        callee = ref.ent()
        if _is_routine(callee) and _definition_path(callee, ctx) is not None:
            found[callee.id()] = callee
    return list(found.values())


def _unused_parameters(ent: Any, ctx: LeanContext) -> list[str]:
    """The names of the declared parameters no project reference touches, in order."""
    return [
        str(param.name())
        for param in ent.ents(PARAMETER_REFS, PARAMETER_KINDS)
        if not _used_in_project(param, ctx)
    ]


def _used_in_project(ent: Any, ctx: LeanContext) -> bool:
    """Whether any :data:`PARAMETER_USE` reference to ``ent`` is written in a project file."""
    return any(
        ctx.project_path(ref.file(), ctx.root) is not None for ref in ent.refs(PARAMETER_USE)
    )


def _is_routine(ent: Any) -> bool:
    """Whether an entity is one of the routines the snapshot walk records."""
    return bool(ent.kind().check(ROUTINE_KINDS))


def _definition_path(ent: Any, ctx: LeanContext) -> str | None:
    """The project path of the file an entity is written in, or ``None`` when it has none."""
    ref = ent.ref(CONTAINER_KINDS)
    return None if ref is None else ctx.project_path(ref.file(), ctx.root)
