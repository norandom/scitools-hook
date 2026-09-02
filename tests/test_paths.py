"""The path classifier: absence, kind and reach, told apart (task 8.2).

``Path.exists()``, ``.is_file()`` and ``.is_dir()`` answer ``False`` both for a path that was
never created and for one that cannot be reached at all -- and on 3.12/3.13 they do not even
do that consistently, raising ``EACCES`` instead (see the module under test).
Every consumer in this package treats absence as an *answer* -- no baseline captured, this
repository has never been analysed, no repository configuration -- so confusing the two turns
a broken installation into a healthy one that simply has nothing yet.

This module had no tests of its own for a round: it was covered only through four consumers,
which is how the distinctions below stayed unverified while the callers' own assertions passed.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path

import pytest

import scitools_hook.paths as paths_module
from scitools_hook.paths import PathVerdict, classify_directory, classify_file


def sealed(tmp_path: Path, name: str) -> Path:
    """A file inside a directory this user cannot search, restored by the caller."""
    parent = tmp_path / name
    parent.mkdir()
    target = parent / "target"
    target.write_text("{}", encoding="utf-8")
    parent.chmod(0o000)
    return target


# --- absence is the one answer, and only genuine absence earns it ----------------


def test_a_path_that_was_never_created_is_absent(tmp_path: Path) -> None:
    """The state every consumer reads as "nothing has happened yet"."""
    verdict = classify_file(tmp_path / "nothing.json")
    assert verdict.absent
    assert not verdict.usable
    assert verdict.reason == ""


@pytest.mark.parametrize("shape", ["dangling", "loop", "unsearchable", "nul", "under-a-file"])
def test_a_path_that_cannot_be_reached_is_never_reported_as_absent(
    tmp_path: Path, shape: str
) -> None:
    """Each of these answers ``exists()`` False while being nothing like "not created".

    ``under-a-file`` is the commonest ``baseline.file`` typo -- a path whose parent is a
    regular file -- and ``ENOTDIR`` reading as absence is the silent-green shape.
    """
    parent = tmp_path / shape
    if shape == "dangling":
        path = tmp_path / "link"
        path.symlink_to(tmp_path / "nowhere")
    elif shape == "loop":
        path = tmp_path / "loop"
        path.symlink_to(path)
    elif shape == "unsearchable":
        path = sealed(tmp_path, shape)
    elif shape == "nul":
        path = Path(f"{tmp_path}/bad\x00name")
    else:
        parent.write_text("not a directory", encoding="utf-8")
        path = parent / "baseline.json"
    try:
        verdict = classify_file(path)
    finally:
        if shape == "unsearchable":
            (tmp_path / shape).chmod(0o755)
    assert not verdict.absent
    assert not verdict.usable
    assert verdict.reason


# --- the reason has to be true, because operators act on it ----------------------


@pytest.mark.parametrize(
    ("shape", "phrase"),
    [
        ("regular-elsewhere", "is a symbolic link that is not"),
        ("dangling", "leads nowhere"),
        ("loop", "cannot be reached"),
        ("unreachable-target", "cannot be reached"),
    ],
)
def test_the_four_link_faults_are_told_apart(tmp_path: Path, shape: str, phrase: str) -> None:
    """A link to a directory, a link to nothing, a loop and a link into a sealed directory.

    Collapsing them into "leads nowhere" sends an operator looking for a missing file whose
    target is sitting there; that is the whole reason this distinction is drawn.
    """
    path = tmp_path / "link"
    if shape == "regular-elsewhere":
        path.symlink_to(tmp_path)
    elif shape == "dangling":
        path.symlink_to(tmp_path / "nowhere")
    elif shape == "loop":
        path.symlink_to(path)
    else:
        path.symlink_to(sealed(tmp_path, "sealed"))
    try:
        reason = classify_file(path).reason
    finally:
        if (tmp_path / "sealed").exists():
            (tmp_path / "sealed").chmod(0o755)
    assert phrase in reason


@pytest.mark.parametrize("shape", ["fifo", "directory"])
def test_a_wrong_kind_is_named_as_one(tmp_path: Path, shape: str) -> None:
    """Not a link, just not a file; the message must not blame a link."""
    path = tmp_path / "thing"
    os.mkfifo(path) if shape == "fifo" else path.mkdir()
    verdict = classify_file(path)
    assert verdict.reason == "is not a regular file"
    assert "symbolic" not in verdict.reason


def test_a_readable_regular_file_is_usable(tmp_path: Path) -> None:
    """The affirmative case, so the classifier cannot pass by refusing everything."""
    path = tmp_path / "ok.json"
    path.write_text("{}", encoding="utf-8")
    verdict = classify_file(path)
    assert verdict.usable
    assert not verdict.absent
    assert verdict.reason == ""


def test_a_symbolic_link_to_a_regular_file_is_usable(tmp_path: Path) -> None:
    """A shared baseline is configured exactly this way, so it must not be refused."""
    target = tmp_path / "shared.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "baseline.json"
    link.symlink_to(target)
    assert classify_file(link).usable


# --- directories, where reachability is two separate permission bits -------------


@pytest.mark.parametrize(("mode", "usable"), [(0o755, True), (0o444, False), (0o111, False)])
def test_a_directory_needs_both_permission_bits(tmp_path: Path, mode: int, usable: bool) -> None:
    """Measured: ``0o444`` grants read but not search and ``0o111`` search but not read.

    Either alone makes the contents unreachable while ``exists()`` and ``is_dir()`` both still
    answer yes, and ``chmod 000`` -- what every earlier test used -- removes both and so
    cannot tell the two bits apart.
    """
    path = tmp_path / f"d{mode:o}"
    path.mkdir()
    path.chmod(mode)
    try:
        assert classify_directory(path).usable is usable
    finally:
        path.chmod(0o755)


def test_a_file_where_a_directory_was_expected_is_not_called_absent(tmp_path: Path) -> None:
    """The seam variable pointed at a file is a typo, and it is reported as one."""
    path = tmp_path / "plain"
    path.write_text("", encoding="utf-8")
    verdict = classify_directory(path)
    assert not verdict.absent
    assert verdict.reason == "is not a directory"


def test_a_verdict_is_usable_only_when_it_is_neither_absent_nor_faulty() -> None:
    """Both terms carry weight: one consumer reads ``usable`` before asking about absence."""
    assert PathVerdict().usable
    assert not PathVerdict(absent=True).usable
    assert not PathVerdict(reason="is not a regular file").usable


VERSION_SENSITIVE = frozenset({"is_file", "is_dir", "is_symlink", "exists"})


def _predicates_called(source: str) -> list[str]:
    """The version-sensitive predicate names actually *called* in ``source``.

    Parsed rather than grepped. A regex over the text reports every one of these names,
    because the module's own docstrings name all three while explaining why it does not use
    them -- the first version of this helper did exactly that and failed on prose. Comments do
    not survive ``ast.parse`` at all and a docstring is a ``Constant``, so only real calls
    remain.
    """
    found = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in VERSION_SENSITIVE
    }
    return sorted(found)


def test_the_classifier_calls_no_pathlib_predicate_that_changed_behaviour_in_3_14() -> None:
    """A source assertion, because the behaviour it guards cannot be observed on 3.14.

    ``Path.is_file()``, ``.is_dir()``, ``.is_symlink()`` and ``.exists()`` swallow every
    ``OSError`` on 3.14, but on 3.12 and 3.13 ``pathlib`` filters through ``_ignore_error``
    (``_IGNORED_ERRNOS = ENOENT, ENOTDIR, EBADF, ELOOP``) and re-raises the rest -- ``EACCES``
    among them, which is precisely the fault this module exists to report. Measured, one probe
    on three interpreters against a symlink whose target sits under a ``chmod 000`` directory:
    3.12.14 and 3.13.13 raise ``PermissionError``, 3.14.4 returns ``False``. With
    ``requires-python = ">=3.12"`` that is an untyped escape from a function contracted to
    return a verdict, and it cost this task a round.

    ``test_the_four_link_faults_are_told_apart`` covers the behaviour and is the better test --
    but only on an interpreter where the fault exists, and this suite runs on one. Until a
    version matrix lands (task 10.3), a regression here would be invisible on 3.14 and break
    on the declared floor. So this asserts the *source*, which is checkable anywhere.

    It is a **proxy** and is written down as one: it cannot tell a correct use of these
    predicates from an incorrect one, only that none is present. The project has been bitten
    before by a proxy whose limits were not stated (the ``documents()`` helper in the CLI
    tests), so the limit is stated here.
    """
    used = _predicates_called(inspect.getsource(paths_module))
    assert used == [], (
        f"scitools_hook.paths calls {used} -- these raise EACCES on Python 3.12/3.13 while "
        f"swallowing it on 3.14. Use os.stat/os.lstat with a stat.S_IS* predicate instead."
    )


def test_the_source_guard_can_actually_fail() -> None:
    """The guard above asserts an absence, so its own detector needs a positive case.

    An assertion that something is missing passes just as happily when the search is broken --
    a detector that matches nothing reports a clean module forever. This drives the same
    search over source that does call them, and over prose that only mentions them, because
    telling those two apart is the whole reason it parses instead of grepping.
    """
    calls = "def f(path):\n    if not path.is_file():\n        return path.exists()\n"
    assert _predicates_called(calls) == ["exists", "is_file"]

    prose = '''"""Why ``Path.exists()`` and ``Path.is_file()`` are not used here."""'''
    assert _predicates_called(prose) == []
