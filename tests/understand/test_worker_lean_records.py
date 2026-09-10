"""What the worker records once it loads the sibling, and what it costs when it does not.

Requirement 9.4 decides the shape of this module: while every lean-code rule is off the
extraction must do nothing on the family's behalf -- no load, no reference walk, no key in
the document -- so the tests come in pairs, one with the request key and one without it. A
pair is the only arrangement that can fail: an assertion about the loaded side alone passes
against a worker that measures unconditionally, which is exactly the cost requirement 9.4
forbids.

The three-state discipline is the other half. A fact nobody asked for is an absent key,
which the model reads back as ``None``, and never ``False`` and never an empty list: a rule
that read an unmeasured record as a measured one would report a project full of dead code
(requirements 1.6, 2.5).

**The measurements themselves are not re-tested here.** ``test_worker_lean.py`` owns them,
against the same fakes; this module owns the wiring -- that the sibling is reached at all,
that it is reached once, that what it answers lands on the right record, and that a run
which asked for nothing pays for nothing.
"""

from __future__ import annotations

import sys
from typing import Any, Final

import pytest
from api_fakes import FakeDb, FakeEnt, FakeRef, FakeUnderstand, install
from worker_projects import (
    CLASS_KINDS,
    FILE_KIND,
    ROUTINE_KINDS,
    FakeProject,
    a_routine,
    a_variable,
    fake_project,
    listing,
    mapping,
    records,
    snapshot_request,
)

from scitools_hook.models.snapshot import EntityKey, ProjectSnapshot, TokenIndex
from scitools_hook.understand import worker, worker_lean

REFERENCES: dict[str, object] = {"lean_references": True}
"""The request key the five reference rules turn on (design.md, *Snapshot request*)."""

TOKENS: dict[str, object] = {"lean_tokens": True}
"""The request key the two duplication rules turn on (design.md, *Snapshot request*)."""

DEFINITIONS: dict[str, object] = {"include_definitions": True}


# --- driving one extraction ------------------------------------------------------------


def a_document(
    monkeypatch: pytest.MonkeyPatch, project: FakeProject | None = None, **overrides: object
) -> dict[str, Any]:
    """Run the ``snapshot`` operation over a fake project and answer its document."""
    install(monkeypatch, FakeUnderstand(db=(project or fake_project()).db))
    document: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**overrides))
    assert "error" not in document, document
    return document


def loads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every load of the sibling a run performs, recorded rather than counted by hand.

    The spy sits on ``worker._lean_module`` because that is the seam the design names, and
    it calls through to it: a stub answering a stand-in module would prove the extractor
    called something and nothing about what the something measured.
    """
    seen: list[str] = []
    real = worker._lean_module

    def watched() -> Any:
        seen.append(worker.LEAN_PATH)
        return real()

    monkeypatch.setattr(worker, "_lean_module", watched)
    return seen


def token_passes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every whole-project lexer pass a run performs, recorded rather than inferred.

    The absence of an index does not prove the pass did not run. A worker that measured on
    every load and recorded only when asked would satisfy every assertion in this module,
    and requirement 9.4's whole point is the cost, not the field. Measured: such a worker
    passed the full suite before this spy existed.

    It sits on the sibling's own ``token_index`` and calls through, for the reason ``loads``
    does: a stub answering a stand-in document would prove something was called and nothing
    about what it measured. The module is the one ``_lean_module`` caches, so the patch is
    torn down with the test.
    """
    module = worker._lean_module()
    seen: list[str] = []
    real = module.token_index

    def watched(file_ents: Any, routines: Any, ctx: Any) -> Any:
        seen.append(worker.LEAN_PATH)
        return real(file_ents, routines, ctx)

    monkeypatch.setattr(module, "token_index", watched)
    return seen


def a_db_with_variables(project: FakeProject, variables: list[FakeEnt]) -> FakeDb:
    """The fake project's database with module-level bindings added to it.

    Built from the project's own architecture and entity lists rather than from a second
    fixture, so the definitions walk reads the same files, routines and classes every other
    test in this module does.
    """
    return FakeDb(
        project.db.root_archs(),
        entities={
            FILE_KIND: project.db.ents(FILE_KIND),
            ROUTINE_KINDS: project.db.ents(ROUTINE_KINDS),
            CLASS_KINDS: project.db.ents(CLASS_KINDS),
            worker.MODULE_VARIABLE_KIND: variables,
        },
    )


# --- the load itself (requirement 9.4) --------------------------------------------------


def test_a_run_that_asks_for_no_lean_fact_never_loads_the_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole of requirement 9.4 in one line: a rule that is off costs nothing."""
    seen = loads(monkeypatch)

    a_document(monkeypatch)

    assert seen == []


def test_a_run_that_asks_for_references_loads_the_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = loads(monkeypatch)

    a_document(monkeypatch, **REFERENCES)

    assert seen == [worker.LEAN_PATH]


def test_a_run_that_asks_only_for_tokens_loads_the_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second half of the load condition, and it is a whole arc of its own.

    ``SnapshotExtractor.request`` sets the two keys from two properties, so a configuration
    with only a duplication rule on arrives here with ``lean_tokens`` alone: a load condition
    that read the reference key would leave the token pass with nothing to call, and every
    other test in this file would still pass.

    The two negative assertions are the other half. Loading the sibling is not measuring with
    it, and a run that asked only for tokens must carry neither the per-entity facts nor the
    declaring-class tally -- both of which cost a walk that nothing asked for (req 9.4).
    """
    seen = loads(monkeypatch)

    document = a_document(monkeypatch, **TOKENS)

    assert seen == [worker.LEAN_PATH]
    assert "lean" not in records(document)["app.build_parser"]
    assert "method_declarations" not in document


def test_the_sibling_is_executed_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading it per extraction would re-execute the module for every side of every run.

    Proven by breaking the path after the first load: a second execution would raise, so the
    module coming back unchanged is the cache answering rather than a second load agreeing.
    """
    monkeypatch.setattr(worker, "_LEAN", {})
    first = worker._lean_module()

    monkeypatch.setattr(worker, "LEAN_PATH", "/nowhere/worker_lean.py")

    assert worker._lean_module() is first


def test_the_sibling_is_loaded_by_path_rather_than_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load leaves no entry in ``sys.modules``, which is why the file may hold no dataclass.

    ``@dataclass`` reads ``sys.modules[cls.__module__].__dict__`` and raises for a module
    loaded this way -- measured, and recorded on ``worker_lean.LeanContext``. The assertion
    that the executed module is a *different object* from the imported one is what makes that
    consequence visible here: this test file imports the sibling the ordinary way, and the
    worker's copy is not it.
    """
    monkeypatch.setattr(worker, "_LEAN", {})

    module = worker._lean_module()

    assert module.__name__ == "worker_lean"
    assert module is not sys.modules.get("scitools_hook.understand.worker_lean")
    assert module.routine_facts is not worker_lean.routine_facts


# --- the facts on the records (requirements 1.6, 2.5) ------------------------------------


def test_no_record_carries_a_lean_key_when_references_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent rather than ``null``: the document a run with no lean rule produces is today's."""
    document = a_document(monkeypatch)

    assert [record for record in records(document).values() if "lean" in record] == []


def test_a_routine_record_carries_the_callers_and_callees_the_sibling_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One caller in another project file, one callee in a third: the pass-through shape.

    The numbers are non-trivial on purpose. A record carrying zeros would agree with a worker
    that handed the measurement the wrong entity, and with one that handed it nothing at all.
    """
    project = fake_project()
    project.build_parser.refs_extra = [
        FakeRef(project.wrap_lines, None, "python Callby", False, project.text),
        FakeRef(project.clamp, None, "python Call", True, project.app),
    ]

    document = a_document(monkeypatch, project, **REFERENCES)

    assert records(document)["app.build_parser"]["lean"] == {
        "callers": 1,
        "callees": 1,
        "forwards_to": "clamp",
        "overrides": False,
        "unused_parameters": ["argv"],
    }


def test_a_class_record_carries_the_facts_the_sibling_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A base with exactly one derived class and no other referrer: requirement 3.1's shape."""
    project = fake_project()
    project.runner.refs_extra = [
        FakeRef(project.helper, None, "python Inheritby", False, project.text)
    ]

    document = a_document(monkeypatch, project, **REFERENCES)

    assert records(document)["app.Runner"]["lean"] == {
        "referenced": True,
        "derived": ["text.Helper"],
        "referrers": 0,
    }


def test_a_file_record_carries_no_facts_even_when_references_are_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither dead-code rule nor pass-through has a question about a file entity."""
    document = a_document(monkeypatch, **REFERENCES)

    assert "lean" not in records(document)["cli/app.py"]


# --- the module-level bindings (requirement 1.1) -----------------------------------------


def a_project_with_bindings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> list[Any]:
    """Four bindings of which two are module-level, through the definitions walk.

    The other two are what the walk's guard is for and neither is a scattered definition: a
    field of a class, whose ``Definein`` names the class rather than the file it is written
    in, and a binding of the standard library Understand injects into every Python project.
    Both are reached and refused, so the walk's two refusals are measured rather than assumed.
    """
    project = fake_project()
    read = a_variable("TIMEOUT", project.app, kind="python Global Object")
    read.refs_extra = [FakeRef(project.wrap_lines, None, "python Useby", False, project.text)]
    dead = a_variable("UNUSED", project.app, kind="python Global Object")
    field = a_variable("RETRIES", project.app, kind="python Global Object")
    field.container = None
    field.refs_extra = [FakeRef(project.runner, 5, "python Definein", False, project.app)]
    injected = a_variable("MAXSIZE", project.injected, kind="python Global Object")
    install(
        monkeypatch,
        FakeUnderstand(db=a_db_with_variables(project, [read, dead, field, injected])),
    )
    document: dict[str, Any] = worker.dispatch(
        "snapshot", snapshot_request(**DEFINITIONS, **overrides)
    )
    assert "error" not in document, document
    return listing(document, "definitions")


def test_a_module_binding_carries_whether_the_project_reads_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definitions = a_project_with_bindings(monkeypatch, **REFERENCES)

    assert {row["name"]: row["referenced"] for row in definitions} == {
        "TIMEOUT": True,
        "UNUSED": False,
    }


def test_a_module_binding_of_a_run_that_did_not_ask_says_nothing_rather_than_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` is the answer the unused-variable rule reports as unavailable (req 1.6)."""
    definitions = a_project_with_bindings(monkeypatch)

    assert {row["name"]: row["referenced"] for row in definitions} == {
        "TIMEOUT": None,
        "UNUSED": None,
    }


# --- the declaring-class tally (requirement 1.9) -----------------------------------------


def a_project_with_methods() -> FakeProject:
    """Two classes declaring ``run``, one of them in a file this run did not ask about."""
    project = fake_project()
    project.runner.members = [a_routine("app.Runner.run", project.app)]
    project.helper.members = [
        a_routine("text.Helper.run", project.text),
        a_routine("text.Helper.only", project.text),
    ]
    return project


def test_the_declaring_class_tally_counts_the_whole_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``util/text.py`` is not in ``files``, and its class still counts.

    That is the whole reason the tally is a project-wide snapshot field rather than a fact on
    a record: requirement 1.9 asks how many classes in the *project* declare a name, and a
    count taken over the changed files alone would call an interface method dead the moment
    its other implementations sat in files the commit did not touch.
    """
    document = a_document(monkeypatch, a_project_with_methods(), **REFERENCES)

    assert mapping(document, "method_declarations") == {"run": 2, "only": 1}


def test_a_run_that_did_not_ask_for_references_records_no_tally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = a_document(monkeypatch, a_project_with_methods())

    assert "method_declarations" not in document


# --- the token index (requirements 5.8, 9.4) ---------------------------------------------


A_BODY: Final = [
    ("Keyword", "def", 9),
    ("Whitespace", " ", 9),
    ("Identifier", "run", 9),
    ("Punctuation", "(", 9),
    ("Punctuation", ")", 9),
    ("Punctuation", ":", 9),
    ("Newline", "\n", 9),
    ("Indent", "    ", 10),
    ("Keyword", "return", 10),
    ("Whitespace", " ", 10),
    ("Literal", "1", 10),
]
"""``def run():`` and ``return 1`` on lines 9 and 10, the lines ``a_routine`` defines over."""

A_BODY_SHAPE: Final = ["def", "ID", "(", ")", ":", "return", "LIT"]
"""What :data:`A_BODY` normalises to, and the reason this module asserts a shape at all.

The measurement itself belongs to ``test_worker_lean_tokens.py``. What is asserted here is
that the extractor handed the pass the *file entities* and the *context* -- an index built
from an empty stream, from the wrong map, or against a context whose root refuses every path
would still be a document with the right keys in it, and every other assertion below would
agree with it.
"""


def a_project_with_tokens() -> FakeProject:
    """The fake project with a lexable stream on each of its three files.

    ``build_parser`` and ``wrap_lines`` carry the ``End`` reference the index clips a shape
    to; ``clamp`` deliberately does **not**, so "every recorded routine with an end
    reference" is a set the walk's own routines are wider than. A fixture where every
    recorded routine were indexed could not tell that condition from "every recorded
    routine".
    """
    project = fake_project()
    project.app.tokens = project.text.tokens = project.native.tokens = A_BODY
    project.build_parser.end_ref = FakeRef(project.app, 10)
    project.wrap_lines.end_ref = FakeRef(project.text, 10)
    return project


def an_index(document: dict[str, Any]) -> TokenIndex:
    """The document's index, read back through the model the extractor validates it with.

    Through the **whole** ``ProjectSnapshot`` rather than ``TokenIndex`` alone, because the
    pass's own document is already bound to ``TokenIndex`` in ``test_worker_lean_tokens.py``
    and repeating that here would measure nothing new. What is only measurable at this site
    is the name the worker writes it under: an index recorded as ``token_index`` would parse
    as a ``TokenIndex`` perfectly well and arrive at the extractor as ``None``.
    """
    snapshot = ProjectSnapshot.model_validate(document)
    assert snapshot.tokens is not None
    return snapshot.tokens


def a_routine_token(path: str, longname: str) -> str:
    """The key token the walk gives one of the fixture's routines.

    Built through :class:`EntityKey` rather than written out as JSON, because the token is
    that model's own spelling and a literal here would be a second copy of it. ``argv`` is
    the parameter list ``worker_projects.a_routine`` gives every routine it builds.
    """
    return EntityKey(scope="routine", path=path, longname=longname, parameters="argv").token


def test_a_run_that_does_not_ask_for_tokens_records_no_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 9.4 over a project the pass *could* have read: nothing asked, nothing run.

    The fixture is the lexable one so the pass has something to find; the assertions that it
    was never asked for are ``token_passes``'. An earlier draft claimed the fixture alone
    made this case refuse a worker that ran the pass and discarded the answer, and it does
    not: such a worker passed all 4967 tests. Only the spy refuses it.
    """
    seen = token_passes(monkeypatch)

    document = a_document(monkeypatch, a_project_with_tokens())

    assert "tokens" not in document
    assert ProjectSnapshot.model_validate(document).tokens is None
    assert seen == []


def test_a_run_that_asks_only_for_references_records_no_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two request keys are two questions, and the sibling is loaded for either.

    Without this case the token pass could be written on ``lean_references`` -- the key that
    is already set in production -- and every other test in this module would agree with it.

    This is the run the sibling *is* loaded for, so the absence of an index says nothing on
    its own about whether the whole-project lexer pass ran. ``token_passes`` is what says it.
    """
    seen = token_passes(monkeypatch)

    document = a_document(monkeypatch, a_project_with_tokens(), **REFERENCES)

    assert "tokens" not in document
    assert ProjectSnapshot.model_validate(document).tokens is None
    assert seen == []


def test_a_run_that_asks_for_tokens_records_every_routine_with_an_end_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task's own condition, and it is also where the pass's *position* is bound.

    ``routine_ents`` is filled by the entity walk, so an index measured before it would carry
    files and no routines at all. Measured: moving the *statement* above the scope loop only
    raises, because ``document`` does not exist there yet; taking the measurement early into
    a local and assigning it at the same place is the ordering mutant, and the routine set
    below is what refuses it.

    ``clamp`` is in the walk's map and absent from the index while **its file is present**,
    which is the half that separates "every recorded routine with an end reference" from
    "every recorded routine": a routine left out because its file was never read would
    satisfy the first assertion and say nothing about the ``End`` reference.
    """
    index = an_index(a_document(monkeypatch, a_project_with_tokens(), **TOKENS))

    assert set(index.routines) == {
        a_routine_token("cli/app.py", "app.build_parser"),
        a_routine_token("util/text.py", "text.wrap_lines"),
    }
    assert a_routine_token("native/util.c", "clamp") not in index.routines
    assert "native/util.c" in index.files
    shape = index.routines[a_routine_token("cli/app.py", "app.build_parser")]
    assert (shape.path, shape.start, shape.end) == ("cli/app.py", 9, 10)
    assert [index.vocabulary[number] for number in shape.shape] == A_BODY_SHAPE


def test_the_index_covers_the_whole_project_rather_than_the_files_the_run_asked_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request names ``cli/app.py`` alone; a duplicate has two ends and one may be anywhere.

    ``file_ents`` and ``routine_ents`` are whole-project for the same reason the
    declaring-class tally is, so the pass takes the extractor's own maps rather than
    ``plan.files``: the document records three entities, all of them in ``cli/app.py``, and
    the index carries all three files and a routine of ``util/text.py`` the document has no
    record of at all. The library file, the vendored file and the out-of-root header are not
    project code and contribute neither lines nor a name under ``unreadable``.
    """
    document = a_document(monkeypatch, a_project_with_tokens(), **TOKENS)

    index = an_index(document)
    assert set(records(document)) == {"cli/app.py", "app.Runner", "app.build_parser"}
    assert set(index.files) == {"cli/app.py", "util/text.py", "native/util.c"}
    assert a_routine_token("util/text.py", "text.wrap_lines") in index.routines
    assert index.files["util/text.py"][0][0] == 9
    assert index.unreadable == []


def test_a_file_whose_lexer_refuses_is_named_rather_than_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 5.8 reaching the document: not lexed and nothing to say are two answers."""
    project = a_project_with_tokens()
    project.text.tokens = None

    index = an_index(a_document(monkeypatch, project, **TOKENS))

    assert index.unreadable == ["util/text.py"]
    assert "util/text.py" not in index.files


# --- the request keys --------------------------------------------------------------------


@pytest.mark.parametrize("key", ["lean_references", "lean_tokens"])
def test_a_lean_request_key_that_is_not_a_boolean_is_refused(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """Refused before the database is opened, as every other request key is."""
    install(monkeypatch, FakeUnderstand(db=fake_project().db))

    answer: dict[str, Any] = worker.dispatch("snapshot", snapshot_request(**{key: "yes"}))

    assert answer["error"]["type"] == "BadRequest"
    assert key in answer["error"]["message"]
