"""Architectures through the wrapper: ``import -arch``, ``export -arch``, ``list arches``,
``remove -arch``, and the declaration that reads back what actually resolved (task 6.5).

``und import -arch`` cannot say whether it worked -- a document naming files the project
does not hold imports with status 0 and the unresolved members are simply gone -- so every
caller goes through ``declare_architecture``, which removes a same-named architecture,
imports, exports the result and answers the member paths that survived. The stubbed ``und``
and the transcripts come from ``und_stub``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from und_stub import (
    RecordingLog,
    UndStub,
    cli,
    db_path,
    write_stub,
)

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.understand.und_arch import (
    ArchNode,
    read_architecture,
    write_architecture,
)
from scitools_hook.understand.und_cli import _import_arch, _list_arches, _remove_arch


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log (requirement 12.8)."""
    return RecordingLog(entries=[])


# --- architectures: import -arch, export -arch, list arches, remove -arch ----------

DIRECTORY_STRUCTURE_XML = (
    "<!DOCTYPE arch>\n"
    '<arch name="Directory Structure"><arch name="src">@l./src/main.py'
    '<arch name="domain">@l./src/domain/leak.py\n'
    "@l./src/domain/model.py</arch>\n"
    '  <arch name="engine">@l./src/engine/core.py</arch>\n'
    " </arch>\n"
    "</arch>\n"
)
"""``und export -arch "Directory Structure"`` on build 1204, transcribed verbatim.

Three properties of the real document are load-bearing and all three are here: the paths are
relative to the directory holding the ``.und`` database (``./src/...`` for a database beside
``src/``), each carries an ``@l`` prefix, and ``src``'s own member sits in the element's
*text* while the nested nodes follow it -- so a reader that only looked at ``text`` would
drop ``domain`` and one that only looked at children would drop ``main.py``.
"""

ARCHES_OUTPUT = "Architectures:\n  Directory Structure\n  Layers\n  "
"""``und -db X list arches``, transcribed: no trailing newline, and a final line of blanks."""

IMPORT_OK = "Architecture imported.\n"
"""What a successful ``import -arch`` prints -- and what a wholly unresolved one prints too."""

IMPORT_MALFORMED = (
    "Error: unable to import architecture - malformed XML.\nError: could not import architecture.\n"
)
"""``import -arch`` on a file that is not well-formed: this, on stdout, and status 1."""

IMPORT_DUPLICATE = (
    "Error: unable to import architecture - duplicate name.\n"
    "Error: could not import architecture.\n"
)
"""``import -arch`` naming an architecture the database already holds: status 1.

This is why :meth:`UndCli.declare_architecture` removes before it imports. A warm database
keeps its architectures across every ``analyze``, so the second run would fail outright.
"""

REMOVE_UNKNOWN = "Error: Layers is not a valid architecture. Architecture skipped.\n"
"""``remove -arch`` naming one the database does not hold: status 1, ``-quiet`` or not."""

EXPORT_UNKNOWN = "Error: Layers is not a valid architecture. Stopping export.\n"
"""``export -arch`` naming one the database does not hold: status 1, and no file written."""


def a_tree(*members: str) -> ArchNode:
    """A one-node architecture called ``Layers`` holding ``members``."""
    return ArchNode(name="Layers", children=(ArchNode(name="shells", members=members),))


def test_read_architecture_reads_understands_own_export() -> None:
    """The whole document, nesting and both member positions included."""
    root = read_architecture(DIRECTORY_STRUCTURE_XML, "an export")
    assert root == ArchNode(
        name="Directory Structure",
        children=(
            ArchNode(
                name="src",
                members=("./src/main.py",),
                children=(
                    ArchNode(
                        name="domain",
                        members=("./src/domain/leak.py", "./src/domain/model.py"),
                    ),
                    ArchNode(name="engine", members=("./src/engine/core.py",)),
                ),
            ),
        ),
    )


def test_read_architecture_takes_a_member_that_follows_a_child_element() -> None:
    """A member written after a nested node lands in that node's ``tail``, not the parent's text."""
    document = '<arch name="Layers"><arch name="shells">a.py</arch>\nb.py</arch>'
    root = read_architecture(document, "a declaration")
    assert root.members == ("b.py",)
    assert list(root.paths()) == ["b.py", "a.py"]


def test_read_architecture_accepts_a_member_written_without_the_prefix() -> None:
    """``@l`` is optional on import (measured), so the reader must not require it."""
    with_prefix = read_architecture('<arch name="L">@l./a.py</arch>', "x")
    without = read_architecture('<arch name="L">./a.py</arch>', "x")
    assert with_prefix == without == ArchNode(name="L", members=("./a.py",))


def test_read_architecture_names_the_source_of_a_malformed_document() -> None:
    with pytest.raises(AnalysisFailedError) as caught:
        read_architecture("not xml at all", "/repo/scitools-hook.arch.xml")
    assert "/repo/scitools-hook.arch.xml" in str(caught.value)
    assert "well-formed" in str(caught.value)


def test_read_architecture_refuses_a_document_rooted_at_something_else() -> None:
    with pytest.raises(AnalysisFailedError) as caught:
        read_architecture('<layers name="x"/>', "a declaration")
    assert "<layers>" in str(caught.value)


def test_read_architecture_refuses_a_node_with_no_name() -> None:
    with pytest.raises(AnalysisFailedError) as caught:
        read_architecture('<arch name="L"><arch>a.py</arch></arch>', "a declaration")
    assert "name" in str(caught.value)


def test_read_architecture_refuses_a_foreign_element_inside_a_node() -> None:
    with pytest.raises(AnalysisFailedError) as caught:
        read_architecture('<arch name="L"><file>a.py</file></arch>', "a declaration")
    assert "<file>" in str(caught.value)


def test_read_architecture_does_not_expand_an_external_entity(tmp_path: Path) -> None:
    """A committed file may not reach outside itself; the parser refuses the entity outright."""
    secret = tmp_path / "secret"
    secret.write_text("token", encoding="utf-8")
    document = (
        f'<!DOCTYPE arch [<!ENTITY leak SYSTEM "file://{secret}">]>'
        f'<arch name="L"><arch name="a">&leak;</arch></arch>'
    )
    with pytest.raises(AnalysisFailedError) as caught:
        read_architecture(document, "a declaration")
    assert "token" not in str(caught.value)


def test_write_architecture_round_trips_through_the_reader() -> None:
    tree = ArchNode(
        name="Layers",
        members=("root.py",),
        children=(
            ArchNode(name="shells", members=("a.py", "b.py")),
            ArchNode(name="empty"),
        ),
    )
    assert read_architecture(write_architecture(tree), "written") == tree


def test_write_architecture_escapes_a_path_xml_would_otherwise_break_on() -> None:
    document = write_architecture(a_tree("a&b<c>.py"))
    assert "a&amp;b&lt;c&gt;.py" in document
    assert read_architecture(document, "written") == a_tree("a&b<c>.py")


def test_write_architecture_starts_with_the_doctype_understand_writes() -> None:
    assert write_architecture(a_tree("a.py")).startswith("<!DOCTYPE arch>\n")


def test_list_arches_reads_names_that_contain_spaces(stub: UndStub, log: RecordingLog) -> None:
    """``Directory Structure`` is one name; splitting the line on whitespace makes it two."""
    stub.plan({"list": {"stdout": ARCHES_OUTPUT}})
    assert _list_arches(cli(stub, log), db_path(stub.root)) == ["Directory Structure", "Layers"]


def test_list_arches_never_passes_quiet(stub: UndStub, log: RecordingLog) -> None:
    """``und -quiet list arches`` prints nothing at all and still exits 0 (measured)."""
    stub.plan({"list": {"stdout": ARCHES_OUTPUT}})
    _list_arches(cli(stub, log), db_path(stub.root))
    assert "-quiet" not in stub.argv
    assert stub.argv[-2:] == ["list", "arches"]


def test_list_arches_refuses_an_empty_answer(stub: UndStub, log: RecordingLog) -> None:
    """Every database holds ``Directory Structure``, so silence is a broken install."""
    stub.plan({"list": {"stdout": "", "rc": 0}})
    with pytest.raises(AnalysisFailedError) as caught:
        _list_arches(cli(stub, log), db_path(stub.root))
    assert "Directory Structure" in str(caught.value)


def test_import_arch_names_the_document(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"import": {"stdout": IMPORT_OK}})
    document = stub.root / "arch.xml"
    document.write_text(write_architecture(a_tree("a.py")), encoding="utf-8")
    _import_arch(cli(stub, log), db_path(stub.root), document)
    assert stub.argv[-3:] == ["import", "-arch", str(document)]


def test_import_arch_maps_a_malformed_document_to_a_typed_failure(
    stub: UndStub, log: RecordingLog
) -> None:
    stub.plan({"import": {"stdout": IMPORT_MALFORMED, "rc": 1}})
    with pytest.raises(AnalysisFailedError) as caught:
        _import_arch(cli(stub, log), db_path(stub.root), stub.root / "arch.xml")
    assert "exit status 1" in str(caught.value)


def test_import_arch_refuses_an_error_reported_with_a_zero_status(
    stub: UndStub, log: RecordingLog
) -> None:
    """The status is the signal, but an ``Error:`` line at status 0 is still not a success."""
    stub.plan({"import": {"stdout": IMPORT_DUPLICATE, "rc": 0}})
    with pytest.raises(AnalysisFailedError) as caught:
        _import_arch(cli(stub, log), db_path(stub.root), stub.root / "arch.xml")
    assert "duplicate name" in str(caught.value)


def test_remove_arch_is_quiet_and_names_the_architecture(stub: UndStub, log: RecordingLog) -> None:
    stub.plan({"remove": {}})
    _remove_arch(cli(stub, log), db_path(stub.root), "Layers")
    assert stub.argv[0] == "-quiet"
    assert stub.argv[-3:] == ["remove", "-arch", "Layers"]


def test_remove_arch_fails_on_an_architecture_the_database_does_not_hold(
    stub: UndStub, log: RecordingLog
) -> None:
    """Measured: status 1. This is why ``declare_architecture`` asks ``list arches`` first."""
    stub.plan({"remove": {"stdout": REMOVE_UNKNOWN, "rc": 1}})
    with pytest.raises(AnalysisFailedError):
        _remove_arch(cli(stub, log), db_path(stub.root), "Layers")


def test_export_arch_resolves_members_against_the_directory_holding_the_database(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The measured frame: ``./after/pkg/core.py`` beside a database at ``<root>/after.und``."""
    cache = tmp_path / "cache"
    (cache / "after" / "pkg").mkdir(parents=True)
    stub.plan(
        {"export": {"write_argv": '<!DOCTYPE arch>\n<arch name="L">@l./after/pkg/core.py</arch>\n'}}
    )
    exported = cli(stub, log).export_arch(cache / "after.und", "L", tmp_path / "out.xml")
    assert list(exported.paths()) == [str(cache / "after" / "pkg" / "core.py")]


def test_export_arch_fails_when_und_wrote_no_file(stub: UndStub, log: RecordingLog) -> None:
    """Measured: an unknown architecture exits 1, but a status-0 silence must not pass either."""
    stub.plan({"export": {"rc": 0}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).export_arch(db_path(stub.root), "L", stub.root / "missing.xml")
    assert "no readable file" in str(caught.value)


def test_export_arch_maps_an_unknown_architecture_to_a_typed_failure(
    stub: UndStub, log: RecordingLog
) -> None:
    stub.plan({"export": {"stdout": EXPORT_UNKNOWN, "rc": 1}})
    with pytest.raises(AnalysisFailedError):
        cli(stub, log).export_arch(db_path(stub.root), "Layers", stub.root / "out.xml")


def declaring_stub(stub: UndStub, exported: str, arches: str = ARCHES_OUTPUT) -> None:
    """Script the four commands one declaration makes."""
    stub.plan(
        {
            "list": {"stdout": arches},
            "remove": {},
            "import": {"stdout": IMPORT_OK},
            "export": {"write_argv": exported},
        }
    )


def test_declare_architecture_removes_the_old_one_before_importing(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A warm database still holds the architecture, and a second import would exit 1."""
    cache = tmp_path / "cache"
    cache.mkdir()
    declaring_stub(stub, '<arch name="Layers"><arch name="shells">@l./a.py</arch></arch>')
    cli(stub, log).declare_architecture(cache / "after.und", a_tree(str(cache / "a.py")))
    subcommands = [argv[argv.index("-db") + 2] for argv in stub.calls]
    assert subcommands == ["list", "remove", "import", "export"]


def test_declare_architecture_does_not_remove_one_the_database_does_not_hold(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``remove -arch`` on an absent name exits 1, so a cold database must not be asked."""
    cache = tmp_path / "cache"
    cache.mkdir()
    declaring_stub(
        stub,
        '<arch name="Layers"><arch name="shells">@l./a.py</arch></arch>',
        arches="Architectures:\n  Directory Structure\n",
    )
    cli(stub, log).declare_architecture(cache / "after.und", a_tree(str(cache / "a.py")))
    subcommands = [argv[argv.index("-db") + 2] for argv in stub.calls]
    assert subcommands == ["list", "import", "export"]


def test_declare_architecture_answers_only_the_members_und_really_took(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The measurement this method exists for.

    ``und import -arch`` answers ``Architecture imported.`` with status 0 for a document
    naming files the project does not hold, and silently drops them -- a document whose every
    path is wrong produces an architecture of empty nodes. So the answer here is read back
    out of the database, and a member that did not survive is simply not in it.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    declaring_stub(stub, '<arch name="Layers"><arch name="shells">@l./kept.py</arch></arch>')
    resolved = cli(stub, log).declare_architecture(
        cache / "after.und", a_tree(str(cache / "kept.py"), str(cache / "dropped.py"))
    )
    assert resolved == frozenset({str(cache / "kept.py")})


def test_declare_architecture_writes_the_document_it_imports(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """What ``und`` was actually handed, snapshotted while the temporary file still existed."""
    cache = tmp_path / "cache"
    cache.mkdir()
    declaring_stub(stub, '<arch name="Layers"><arch name="shells">@l./a.py</arch></arch>')
    cli(stub, log).declare_architecture(cache / "after.und", a_tree(str(cache / "a.py")))
    written = stub.lists.get("architecture.xml")
    assert written is not None, f"und was handed no architecture document: {stub.lists}"
    assert read_architecture(written, "handed to und") == a_tree(str(cache / "a.py"))
