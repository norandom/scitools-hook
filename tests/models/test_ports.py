"""The shadow port: as wide as its caller, and satisfied by the real synchroniser (11.7).

:class:`~scitools_hook.models.ports.ShadowPort` exists so ``understand/database.py`` can use a
shadow synchroniser without importing ``git/shadow.py``: the two adapters are siblings and the
import-direction gate refuses an edge between them. A port like that has two ways to rot.

* **It can grow.** Nothing stops a later change from copying another ``ShadowSync`` member
  into the protocol until the port is a mirror of the class and the coupling is back, spelled
  differently. :func:`test_the_port_is_exactly_as_wide_as_its_one_caller` measures the width
  against ``database.py`` itself rather than against a list written here, so the two cannot
  drift apart in either direction without failing.
* **It can stop describing the real class.** ``mypy`` does check that, but only at
  ``cli/pipelines.py:106``, where the concrete ``ShadowSync`` is handed to
  ``DatabaseManager`` -- measured: widening ``ShadowPort.sync`` by one parameter reports
  ``Argument 3 to "DatabaseManager" has incompatible type "ShadowSync"`` there. That proof
  lives in one call site the port does not control, so the same question is asked here.

The conformance check is deliberately **not** ``isinstance``. Both protocols are
``runtime_checkable``, and a runtime protocol check tests that an attribute is *present*, not
that it has the right signature. The gap is measured below rather than asserted: two stand-ins
that ``isinstance`` accepts would raise ``TypeError`` or answer ``None`` on the manager's first
call, which is why the signature comparison exists at all.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from scitools_hook.config.models import ProjectSettings
from scitools_hook.git.repo import GitRepo
from scitools_hook.git.shadow import ShadowSync
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.git import SyncDelta, SyncTarget
from scitools_hook.models.ports import RepositoryRoot, ShadowPort
from scitools_hook.models.snapshot import Side

DATABASE_SOURCE = (
    Path(__file__).resolve().parents[2] / "src" / "scitools_hook" / "understand" / "database.py"
)
HELD_AS = "_shadow"
"""The attribute ``DatabaseManager`` keeps its shadow synchroniser in."""


def members_reached_on(source: str, receiver: str) -> set[str]:
    """Every attribute name read off ``self.<receiver>`` anywhere in ``source``.

    An AST walk rather than a search for text, for the reason ``tests/test_import_direction``
    records against its own first attempt: a regex over ``database.py`` would match the
    module's prose about ``.sync`` and ``.repo`` and report a width nobody wrote.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Attribute):
            continue
        inner = node.value
        if (
            isinstance(inner, ast.Attribute)
            and inner.attr == receiver
            and isinstance(inner.value, ast.Name)
            and inner.value.id == "self"
        ):
            found.add(node.attr)
    return found


def sync_signature(owner: Any) -> inspect.Signature:
    """``sync``'s parameters and types, resolved rather than compared as text.

    ``eval_str=True`` turns the string annotations both modules write under ``from __future__
    import annotations`` back into the objects they name, so a port and a class that spell the
    same union differently still compare equal, and two different types never do.
    """
    return inspect.signature(owner.sync, eval_str=True)


def a_shadow_synchroniser() -> ShadowSync:
    """A real ``ShadowSync`` over paths that need not exist; nothing here runs ``git``."""
    repo = GitRepo(root=Path("/repo"), git_dir=Path("/repo/.git"), common_dir=Path("/repo/.git"))
    paths = CachePaths.for_repo(repo.common_dir, "gitdir")
    return ShadowSync(repo, paths, ProjectSettings())


# --- the width of the port ----------------------------------------------------------


def test_the_port_is_exactly_as_wide_as_its_one_caller() -> None:
    """Measured against ``database.py``, so the port cannot quietly become a second class."""
    reached = members_reached_on(DATABASE_SOURCE.read_text(encoding="utf-8"), HELD_AS)
    assert reached, f"no `self.{HELD_AS}.<member>` access found in {DATABASE_SOURCE}"
    assert reached == set(ShadowPort.__protocol_attrs__), (
        f"DatabaseManager reaches for {sorted(reached)}; the port declares "
        f"{sorted(ShadowPort.__protocol_attrs__)}"
    )
    assert set(RepositoryRoot.__protocol_attrs__) == {"root"}


def test_the_width_measurement_finds_the_members_that_are_really_there() -> None:
    """The floor under the test above: a scan that found nothing would agree with any port."""
    source = (
        "class M:\n"
        "    def f(self):\n"
        "        self._shadow.sync(1)\n"
        "        return self._shadow.repo.root\n"
        "    def g(self):\n"
        "        return self._other.rebuild\n"
    )
    assert members_reached_on(source, HELD_AS) == {"sync", "repo"}
    assert members_reached_on(source, "_other") == {"rebuild"}


def test_a_docstring_naming_a_member_is_not_a_member() -> None:
    """Distinct input from the case above: prose and a string constant, no attribute access."""
    source = '"""This one calls self._shadow.rebuild() nowhere."""\nX = "self._shadow.wipe"\n'
    assert members_reached_on(source, HELD_AS) == set()


# --- the real synchroniser satisfies it ---------------------------------------------


def test_the_real_shadow_synchroniser_carries_both_members_of_the_port() -> None:
    """Read through the port's own vocabulary, on a real ``ShadowSync``."""
    port: ShadowPort = a_shadow_synchroniser()
    assert port.repo.root == Path("/repo")
    assert callable(port.sync)


def test_the_sync_signature_is_the_one_the_manager_calls() -> None:
    """Named parameters, in order, resolved to the same types on both sides."""
    port = inspect.signature(ShadowPort.sync, eval_str=True)
    assert list(port.parameters) == ["self", "side", "target", "state"]
    assert sync_signature(ShadowSync) == port


def test_the_repository_root_the_port_promises_is_a_path() -> None:
    """The member exists so the manager can name the repository in a refusal it raises."""
    assert inspect.signature(RepositoryRoot.root.fget, eval_str=True).return_annotation is Path
    assert isinstance(a_shadow_synchroniser().repo.root, Path)


# --- what the check would miss if it were `isinstance` -------------------------------


class _NoSync:
    """A stand-in that never learned to sync at all."""

    def __init__(self) -> None:
        self.repo = GitRepo(root=Path("/x"), git_dir=Path("/x/.git"), common_dir=Path("/x/.git"))


class _WiderSync:
    """A stand-in whose ``sync`` demands one argument more than the manager passes."""

    def __init__(self) -> None:
        self.repo = GitRepo(root=Path("/x"), git_dir=Path("/x/.git"), common_dir=Path("/x/.git"))

    def sync(self, side: Side, target: SyncTarget, state: SyncState, force: bool) -> SyncDelta:
        return SyncDelta()


class _DifferentReturn:
    """A stand-in whose ``sync`` takes the right arguments and answers with nothing."""

    def __init__(self) -> None:
        self.repo = GitRepo(root=Path("/x"), git_dir=Path("/x/.git"), common_dir=Path("/x/.git"))

    def sync(self, side: Side, target: SyncTarget, state: SyncState) -> None:
        return None


def test_isinstance_alone_would_accept_a_stand_in_that_breaks_on_the_first_call() -> None:
    """Why the signature comparison exists, asserted rather than assumed.

    ``runtime_checkable`` checks attribute presence. Both stand-ins below have every member
    the port names, so ``isinstance`` says yes to each; the manager would then call ``sync``
    with three arguments and get a ``TypeError`` from one and ``None`` from the other.
    """
    for stand_in in (_WiderSync(), _DifferentReturn()):
        assert isinstance(stand_in, ShadowPort)


@pytest.mark.parametrize(
    ("stand_in", "why"),
    [
        pytest.param(_WiderSync, "one argument too many", id="a-wider-signature"),
        pytest.param(_DifferentReturn, "answers None instead of a delta", id="a-different-return"),
    ],
)
def test_a_stand_in_whose_sync_differs_is_refused_by_the_signature_comparison(
    stand_in: type, why: str
) -> None:
    assert sync_signature(stand_in) != inspect.signature(ShadowPort.sync, eval_str=True), why


def test_a_stand_in_with_no_sync_at_all_is_refused_by_isinstance_too() -> None:
    """The third distinct defect: a missing member, which presence-checking does catch."""
    assert not isinstance(_NoSync(), ShadowPort)
    assert isinstance(_NoSync().repo, RepositoryRoot)
