"""Architectures Understand generates by itself: listing them, and asking for one (4.1-4.3).

Build 1262 ships 21 automatic architectures, three of them derived from git history. The Gate
needs two things from them: which ones this build offers, so a configuration naming an absent
one can be refused at configuration time, and one generated into a database so the layer and
coupling rules can read its nodes.

**The exit status is not the answer.** Measured on Build 1262: `arch -generate "Git Stability"`
printed `Git Stability: generated` and exited **1** on one database and **0** on another, with
87 members exported either way. So success is decided by exporting the architecture and
reading it back, and failure by the `Error:` line `und` prints for the three refusals it has --
an unknown name, a name already in use without `-force`, and an unknown option.

Everything here is a module function taking the wrapper rather than a method on it: `UndCli`
is five over its coupling limit, so a new type named inside the class is refused by the gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from und_stub import RecordingLog, UndStub, cli, db_path, write_stub

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.understand.und_arch import ARCH_DOCTYPE
from scitools_hook.understand.und_cli import (
    GeneratedArch,
    generate_arch,
    list_generated,
)

LIST_OUTPUT = """\
Directory Structure   active
Calendar              available
Language              available
Visual Studio         available
AI Namespace          available
CMake                 available
Copyright and License  available
Create From File      available
Cycles                available
File Categorizer Architecture  available
File Namespace        available
Long Name             available
Visual Studio Structure ( C/C++ )  available
POSIX Thread Entry Points  available
Qt Thread Entry Points  available
Git Author            available
Git Date              available
Git Owner             available
Git Stability         available
Visibility Matrix     available
Visibility Matrix Cores  available
"""
"""``und -db X arch -list`` on Build 1262, verbatim: 21 architectures, one of them active."""

GENERATED_OK = "Git Stability: generated\n"
"""What a successful generation prints. The exit status beside it may be 0 or 1 (measured)."""

GENERATED_WITH_OPTIONS = "Churn: generated (Date Relative to:=Most Recent Commit)\n"

UNKNOWN_NAME = (
    "Error: architecture not found: No Such Arch\n"
    "Use 'und arch -list' to see available architectures\n"
)

ALREADY_IN_USE = (
    "Error: architecture name already in use: Git Owner\n"
    "Use -force to overwrite it, or -name to choose a different name\n"
)

BAD_OPTION = (
    "Error: invalid -options: unknown option: Nonsense\n"
    "Options for Git Stability:\n"
    "    Date Relative to: [Today | Most Recent Commit] (default: Today)\n"
)

EXPORTED = f"""{ARCH_DOCTYPE}
<arch name="Git Stability">
  <arch name="Active">
    @lmain.py
  </arch>
  <arch name="Stable">
    @lpkg/core.py
  </arch>
</arch>
"""
"""What ``export -arch`` writes for a populated generated architecture."""

EMPTY_EXPORT = f'{ARCH_DOCTYPE}\n<arch name="Git Stability"></arch>\n'
"""What it writes when the database knows no repository: the node with no members at all."""


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log."""
    return RecordingLog(entries=[])


def generating(stub: UndStub, printed: str, exported: str, status: int = 0) -> None:
    """Script a generation and the export that decides whether it worked."""
    stub.plan(
        {
            "arch": {"stdout": printed, "rc": status},
            "export": {"write_argv": exported},
        }
    )


# --- what this build offers ---------------------------------------------------------


def test_the_listing_is_read_into_names_and_statuses(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """21 on Build 1262, and the first is the built-in one every database already has."""
    stub.plan({"arch": {"stdout": LIST_OUTPUT}})

    offered = list_generated(cli(stub, log), db_path(tmp_path))

    assert len(offered) == 21
    assert offered[0] == GeneratedArch(name="Directory Structure", status="active")
    assert GeneratedArch(name="Git Stability", status="available") in offered


def test_a_name_with_spaces_and_punctuation_survives_the_parse(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``Visual Studio Structure ( C/C++ )`` is one name; splitting on whitespace loses it."""
    stub.plan({"arch": {"stdout": LIST_OUTPUT}})

    names = [arch.name for arch in list_generated(cli(stub, log), db_path(tmp_path))]

    assert "Visual Studio Structure ( C/C++ )" in names
    assert "Copyright and License" in names


def test_the_listing_asks_the_database_it_was_given(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Measured: ``und -db X arch -list`` answers, so the wrapper's own db placement works."""
    db = db_path(tmp_path)
    stub.plan({"arch": {"stdout": LIST_OUTPUT}})

    list_generated(cli(stub, log), db)

    assert stub.argv == ["-db", str(db), "arch", "-list"]


def test_a_line_that_is_not_an_architecture_is_ignored(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A later build may print a heading; only the two known statuses make a row."""
    stub.plan({"arch": {"stdout": f"Automatic architectures:\n{LIST_OUTPUT}\n"}})

    offered = list_generated(cli(stub, log), db_path(tmp_path))

    assert len(offered) == 21


def test_a_listing_that_names_nothing_is_refused(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Every database has ``Directory Structure``, so an empty answer is a broken install."""
    stub.plan({"arch": {"stdout": "\n"}})

    with pytest.raises(AnalysisFailedError):
        list_generated(cli(stub, log), db_path(tmp_path))


# --- asking for one ------------------------------------------------------------------


def test_a_generation_answers_the_architecture_read_back(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The node every rule consumes, not a status: the export is what says it worked."""
    generating(stub, GENERATED_OK, EXPORTED)

    node = generate_arch(cli(stub, log), db_path(tmp_path), "Git Stability")

    assert node.name == "Git Stability"
    assert [child.name for child in node.children] == ["Active", "Stable"]


def test_a_generation_that_exited_one_but_wrote_an_architecture_worked(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Measured on Build 1262: the same command exits 0 on one database and 1 on another.

    Reading the status as the answer would make a populated architecture look like a failure
    on the very databases this feature is for.
    """
    generating(stub, GENERATED_OK, EXPORTED, status=1)

    node = generate_arch(cli(stub, log), db_path(tmp_path), "Git Stability")

    assert [member for child in node.children for member in child.members]


def test_the_argv_names_the_architecture_the_instance_and_forces(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-force`` always: a warm database already holds the instance from the last run."""
    generating(stub, GENERATED_WITH_OPTIONS, EXPORTED)

    generate_arch(cli(stub, log), db_path(tmp_path), "Git Stability", instance="Churn")

    argv = stub.calls[0]
    assert argv[argv.index("arch") + 1 :] == [
        "-generate",
        "Git Stability",
        "-name",
        "Churn",
        "-force",
    ]


def test_options_are_passed_as_one_semicolon_separated_argument(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``und help arch``: ``-options <name=value;...>``, using the GUI's own option names."""
    generating(stub, GENERATED_WITH_OPTIONS, EXPORTED)

    generate_arch(
        cli(stub, log),
        db_path(tmp_path),
        "Git Stability",
        options={"Date Relative to": "Most Recent Commit", "Other": "x"},
    )

    argv = stub.calls[0]
    assert argv[argv.index("-options") + 1] == "Date Relative to=Most Recent Commit;Other=x"


@pytest.mark.parametrize(
    ("printed", "needle"),
    [
        (UNKNOWN_NAME, "architecture not found"),
        (ALREADY_IN_USE, "already in use"),
        (BAD_OPTION, "unknown option"),
    ],
    ids=["unknown-name", "duplicate", "bad-option"],
)
def test_every_refusal_und_has_is_reported_in_its_own_words(
    stub: UndStub, log: RecordingLog, tmp_path: Path, printed: str, needle: str
) -> None:
    """The three refusals measured on Build 1262, each recognised by its ``Error:`` line."""
    stub.plan({"arch": {"stdout": printed, "rc": 1}})

    with pytest.raises(AnalysisFailedError) as caught:
        generate_arch(cli(stub, log), db_path(tmp_path), "Git Stability")

    assert needle in str(caught.value)


def test_an_architecture_that_generated_no_members_names_the_likely_cause(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The failure this feature will actually meet, measured: a database with no repository.

    ``Git Stability`` on a plain database exports ``<arch name="Git Stability"></arch>`` and
    says nothing about why. Evaluating the layer rules against that would report no violation
    on a project that has them, which is the silent-green shape the Gate exists to refuse.
    """
    generating(stub, GENERATED_OK, EMPTY_EXPORT)

    with pytest.raises(AnalysisFailedError) as caught:
        generate_arch(cli(stub, log), db_path(tmp_path), "Git Stability")

    assert "no members" in str(caught.value)
    assert "repository" in str(caught.value.hint or "")
