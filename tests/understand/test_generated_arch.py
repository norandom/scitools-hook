"""The route that generates a git-derived architecture, driven by the stubbed ``und`` (4.3).

What is asserted here is the *shape* of the route -- which commands run, in which order, and
what comes back -- because the reason it exists at all is a measurement no unit test can make:
the Gate's own shadow-rooted database generates an architecture with zero members, and a
repository-rooted commit-built one generates 99. That comparison is
``tests/contract/test_generated_arch_contract.py``; this module holds the parts that must
still be right when the build is not there.

The order of the four commands is not arrangement. ``-gitrepo`` at *create* time is what the
git plugin reads; ``add`` decides the file set, because there is no reference database to copy
one from; and an architecture generated over a database that was added but not analysed holds
empty nodes that the analysis afterwards does not fill in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from und_stub import RecordingLog, UndStub, cli, write_stub

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.understand.generated_arch import (
    GIT_ARCHITECTURES,
    NO_COMMIT,
    Generation,
    generate_for_commit,
)

COMMIT = "3ca0a97"
STABILITY = "Git Stability"

EXPORT = (
    "<!DOCTYPE arch>\n"
    '<arch name="Git Stability"><arch name="Active">@l{repo}/pkg/core.py\n'
    "@l{repo}/pkg/other.py</arch>"
    '<arch name="Stable">@l{repo}/pkg/deep/inner.py</arch></arch>\n'
)
"""What ``und export -arch`` writes, in the shape Build 1262 writes it (absolute members)."""


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log."""
    return RecordingLog(entries=[])


def a_request(tmp_path: Path, **changed: object) -> Generation:
    """One generation request over a repository that exists on disk."""
    repo = tmp_path / "repo"
    (repo / "pkg" / "deep").mkdir(parents=True, exist_ok=True)
    for name in ("pkg/core.py", "pkg/other.py", "pkg/deep/inner.py"):
        (repo / name).write_text("def one():\n    return 1\n", encoding="utf-8")
    fields: dict[str, object] = {
        "db": tmp_path / "generate.und",
        "repo": repo,
        "commit": COMMIT,
        "languages": ("Python",),
    }
    fields.update(changed)
    return Generation(**fields)  # type: ignore[arg-type]


def exporting(stub: UndStub, request: Generation, document: str | None = None) -> None:
    """Script the stub so ``export -arch`` writes the document the route reads back."""
    text = EXPORT.format(repo=request.repo) if document is None else document
    stub.plan({"export": {"write_argv": text}})


# --- the four commands, in the order the measurement requires ---------------------------


def test_the_database_is_created_from_the_commit_with_the_repository_named(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-gitrepo`` at create time is what the git plugin reads (measured on Build 1262)."""
    request = a_request(tmp_path)
    exporting(stub, request)

    generate_for_commit(cli(stub, log), request, STABILITY)

    created = stub.calls[0]
    assert created[created.index("-db") + 1] == str(request.db)
    assert created[created.index("-gitrepo") + 1] == str(request.repo)
    assert created[created.index("-gitcommit") + 1] == COMMIT


def test_the_repository_is_added_and_then_analysed_before_anything_is_generated(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """An architecture generated over an unanalysed database holds empty nodes for good."""
    request = a_request(tmp_path)
    exporting(stub, request)

    generate_for_commit(cli(stub, log), request, STABILITY)

    order = [next((word for word in call if word in _STEPS), None) for call in stub.calls]
    steps = [word for word in order if word is not None]
    assert steps[: len(_STEPS)] == ["create", "add", "settings", "analyze", "arch"]


_STEPS = ("create", "add", "settings", "analyze", "arch")
"""The five subcommands whose relative order the measurement fixes."""


def test_the_configured_exclusions_decide_the_file_set(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """There is no reference database here, so ``und add`` is the only thing that decides it."""
    request = a_request(tmp_path, exclude=("build/**", "vendor"))
    exporting(stub, request)

    generate_for_commit(cli(stub, log), request, STABILITY)

    added = [call for call in stub.calls if "add" in call][0]
    assert added[added.index("-exclude") + 1].split(",") == ["build", "vendor"]


# --- what comes back ----------------------------------------------------------------------


def test_the_members_come_back_relative_to_the_repository(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The declared-architecture step places repository-relative members into a shadow tree."""
    request = a_request(tmp_path)
    exporting(stub, request)

    generated = generate_for_commit(cli(stub, log), request, STABILITY)

    assert generated.name == STABILITY
    assert sorted(generated.paths()) == [
        "pkg/core.py",
        "pkg/deep/inner.py",
        "pkg/other.py",
    ]


def test_the_shape_of_the_generated_tree_survives(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The buckets are the architecture; flattening them would lose what it is for."""
    request = a_request(tmp_path)
    exporting(stub, request)

    generated = generate_for_commit(cli(stub, log), request, STABILITY)

    assert [child.name for child in generated.children] == ["Active", "Stable"]
    assert sorted(generated.children[0].members) == ["pkg/core.py", "pkg/other.py"]


def test_a_member_outside_the_repository_is_dropped_rather_than_refused(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The interpreter's own standard library is not this repository's architecture."""
    request = a_request(tmp_path)
    elsewhere = tmp_path / "elsewhere.py"
    elsewhere.write_text("x = 1\n", encoding="utf-8")
    document = (
        "<!DOCTYPE arch>\n"
        f'<arch name="Git Stability">@l{request.repo}/pkg/core.py\n@l{elsewhere}</arch>\n'
    )
    exporting(stub, request, document)

    generated = generate_for_commit(cli(stub, log), request, STABILITY)

    assert sorted(generated.paths()) == ["pkg/core.py"]


def test_an_architecture_holding_no_file_of_the_repository_is_refused(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Every member outside the repository means the database was built over something else.

    Reported as a defect in the Gate rather than in the configuration, because no
    configuration an operator can write produces it.
    """
    request = a_request(tmp_path)
    elsewhere = tmp_path / "elsewhere.py"
    elsewhere.write_text("x = 1\n", encoding="utf-8")
    exporting(
        stub,
        request,
        f'<!DOCTYPE arch>\n<arch name="Git Stability">@l{elsewhere}</arch>\n',
    )

    with pytest.raises(AnalysisFailedError) as caught:
        generate_for_commit(cli(stub, log), request, STABILITY)

    assert "holds no file of" in str(caught.value)


# --- what the route cannot do -------------------------------------------------------------


def test_the_history_architectures_are_named_so_a_caller_can_recognise_one() -> None:
    """``und arch -list`` on 1262 offers 21; these four read a repository's history."""
    assert GIT_ARCHITECTURES == {"Git Author", "Git Date", "Git Owner", "Git Stability"}


def test_the_reason_a_staged_run_cannot_have_one_names_what_to_do_instead() -> None:
    """Requirement 4.3: said out loud, because an empty architecture reads as a clean run."""
    assert "--range" in NO_COMMIT
    assert "declares" in NO_COMMIT
