"""What Understand does and does not enrol, and what it does when parsing fails (task 10.1).

The gate's whole coverage rests on two assumptions nothing in the code can check: that every
file the change touched is *in* the database, and that a file which failed to parse costs
only itself. Both are wrong in ways that are invisible from above -- a file Understand never
enrolled has no entities, so no threshold fires on it and the run is green; a file that failed
to parse loses every routine after the failure, so almost nothing fires on it and the run is
green again.

So the tests here measure the boundary of enrolment (symlinks), the signal that says a
selection was refused (the exit status, **not** the ``Errors:0`` banner), and the real cost of
a parse error. The CodeCheck test is the third silent-empty shape: an output directory with no
CSV in it must never be read as "no violations".
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from contract_project import (
    a_symlink,
    build_database,
    extract,
    real_env,
    run_und,
    write_tree,
)

from scitools_hook.errors import LicenseError
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.understand.codecheck import CodeCheckRunner
from scitools_hook.understand.und_cli import UndCli

pytestmark = pytest.mark.contract

COMPLETED = "Analyze Completed (Errors:0 Warnings:0)"
"""The banner ``und`` prints on a run that refused a file and still exited non-zero."""

UNPACK_SOURCES: dict[str, str] = {
    # Understand's Python parser fails on a star inside a LIST literal -- and the failure
    # cascades to the end of the file, taking every routine after it out of the database.
    "pkg/broken.py": '''"""One routine before the construct Understand cannot parse, one after."""


def before_marker(xs):
    return ["k", *xs]


def after_marker():
    return 2
''',
    "pkg/intact.py": '''"""A second file, to show the loss is confined to the file that failed."""


def intact_marker():
    return 3
''',
}

UNPACK_FILES: tuple[str, ...] = tuple(sorted(UNPACK_SOURCES))

CODECHECK_CONFIG = "Sandbox"
"""A configuration name shipped with Understand; the licence, not the name, is the question."""


def analysed(db: Path) -> list[str]:
    """The absolute paths ``und list files`` reports for a database."""
    listing = run_und("-db", str(db), "list", "files")
    assert listing.returncode == 0, listing.stderr
    return [line.strip() for line in listing.stdout.splitlines()[1:] if line.strip()]


# --- what ``und add`` enrols ------------------------------------------------------


def test_a_symlinked_source_file_is_never_enrolled(tmp_path: Path) -> None:
    """Measured: ``und add <dir>`` walks the tree and skips every symlink it meets.

    Two symlinks and a symlinked directory, none of them enrolled. This matters because git
    tracks symlinks: a repository that keeps a shared module as a link, or a checkout that
    materialises one into the shadow tree, hands the gate a file with no entities and
    therefore no findings -- indistinguishable from a file that is simply clean.
    """
    root = write_tree(
        tmp_path / "tree",
        {"pkg/real.py": "def real_marker():\n    return 1\n", "pkg/target.py": "x = 1\n"},
    )
    outside = write_tree(tmp_path / "outside", {"ext.py": "def ext_marker():\n    return 3\n"})
    a_symlink(Path("target.py"), root / "pkg" / "inside_link.py")
    a_symlink(outside / "ext.py", root / "pkg" / "outside_link.py")
    a_symlink(outside, root / "linked_dir")
    db = tmp_path / "symlinks.und"
    build_database(db, root, ("python",))

    enrolled = analysed(db)

    # Basenames only: the temporary directory pytest makes is itself named after this test,
    # so a substring test over the whole path would match the directory rather than the file.
    assert sorted(Path(path).name for path in enrolled) == ["real.py", "target.py"]
    assert all(not Path(path).is_symlink() for path in enrolled)


def test_a_files_list_naming_an_unenrolled_path_fails_the_whole_run(tmp_path: Path) -> None:
    """The exit status is the signal, and the banner is the trap.

    ``und`` prints ``Analyze Completed (Errors:0 Warnings:0)`` on **standard output** for a
    run that refused one of the paths it was given, analysed the rest, and exited **1**. A
    caller that read the banner, or the error count, would call this a clean run. Only the
    status says otherwise, which is why the file list the gate builds may never name a path
    the database does not hold.

    The symlink resolving *inside* the tree is the discriminator: it is not enrolled either,
    yet it is accepted, because ``und`` resolves the link before matching. So the rule is not
    "never name a symlink" but "never name a path that resolves outside the project".
    """
    root = write_tree(tmp_path / "tree", {"pkg/real.py": "def real_marker():\n    return 1\n"})
    write_tree(root, {"pkg/target.py": "x = 1\n"})
    outside = write_tree(tmp_path / "outside", {"ext.py": "y = 2\n"})
    a_symlink(Path("target.py"), root / "pkg" / "inside_link.py")
    a_symlink(outside / "ext.py", root / "pkg" / "outside_link.py")
    db = tmp_path / "selection.und"
    build_database(db, root, ("python",))
    real = root / "pkg" / "real.py"

    accepted = _analyse_list(tmp_path, db, [real, root / "pkg" / "inside_link.py"], "inside")
    refused = _analyse_list(tmp_path, db, [real, root / "pkg" / "outside_link.py"], "outside")

    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert refused.returncode != 0
    assert "was not found in project" in refused.stdout + refused.stderr
    assert COMPLETED in refused.stdout, "the banner a status-blind caller would have believed"


def _analyse_list(
    workdir: Path, db: Path, files: list[Path], name: str
) -> subprocess.CompletedProcess[str]:
    """Run ``und analyze -files @list`` over ``files`` and return the completed process."""
    listing = workdir / f"{name}.txt"
    listing.write_text("".join(f"{path}\n" for path in files), encoding="utf-8")
    return run_und("-db", str(db), "analyze", "-files", f"@{listing}", "-errors", "-warnings")


# --- what a parse error costs -----------------------------------------------------


def interpreter_dir(base: Path, name: str) -> Path:
    """A directory holding one link to this interpreter, under the given executable name.

    The two names matter: Understand looks for a bare ``python``, and the ``python3`` case is
    the control that shows the directory itself changes nothing.
    """
    base.mkdir(parents=True, exist_ok=True)
    a_symlink(Path(sys.executable), base / name)
    return base


def routines_of(db: Path, root: Path) -> dict[str, float]:
    """Every routine of a database as ``long name -> lines of code``."""
    snapshot = extract(db, root, UNPACK_FILES)
    return {
        key.longname: snapshot.entities[key].metrics["CountLineCode"]
        for key in snapshot.entities
        if key.scope == "routine"
    }


def test_whether_python_parses_at_all_depends_on_the_path_und_inherits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The largest surprise this task found, measured in both directions on one database.

    Understand 6.5.1204 runs a bare ``python`` from ``PATH`` while analysing Python sources.
    With one present, ``["k", *xs]`` parses and both routines are recorded. With only
    ``python3`` present -- which is every modern distribution's default, and what a git hook
    inherits from a login shell -- the same file, the same database and the same command
    report **eight parse errors**, ``after_marker`` disappears from the database entirely, and
    ``before_marker`` swallows the rest of the file (four code lines instead of two).

    Neither run is an error. Both exit 0, and the failing one prints its errors, so
    requirement 2.6's report is implementable either way. What is not implementable from
    above is noticing the difference: two machines, one commit, two entity sets, and nothing
    in the output that names ``PATH``.

    The measurement is deliberately made on one database, analysed twice, so that nothing but
    the environment differs -- and the recovery direction is asserted too, which is what rules
    out "the first analysis simply did less work".

    ``python3`` alone is the control. The link is to :data:`sys.executable`, so both
    directories hold the same real interpreter and only the *name* differs.
    """
    root = write_tree(tmp_path / "tree", UNPACK_SOURCES)
    db = tmp_path / "unpack.und"
    for argv in (
        ["-quiet", "create", "-db", str(db), "-languages", "python", "-local"],
        ["-quiet", "-db", str(db), "add", str(root)],
    ):
        assert run_und(*argv).returncode == 0
    cli = UndCli(real_env("upython"), NullCommandLog())

    monkeypatch.setenv("PATH", str(interpreter_dir(tmp_path / "py3", "python3")))
    without = cli.analyze(db, None, all=True)
    lost = routines_of(db, root)

    monkeypatch.setenv("PATH", str(interpreter_dir(tmp_path / "py", "python")))
    with_python = cli.analyze(db, None, all=True)
    recovered = routines_of(db, root)

    assert without.parse_errors, "only python3 on PATH: the parser must say it failed"
    assert {error.path.name for error in without.parse_errors} == {"broken.py"}
    assert any("expected token ']'" in error.message for error in without.parse_errors)
    assert "broken.after_marker" not in lost, "the routine after the error must be gone"
    assert lost["broken.before_marker"] == 4.0, "it must have swallowed the rest of the file"
    assert "intact.intact_marker" in lost, "another file must be unaffected"

    assert with_python.parse_errors == [], "a bare python on PATH: no error at all"
    assert recovered["broken.after_marker"] == 2.0
    assert recovered["broken.before_marker"] == 2.0
    assert set(recovered) == set(lost) | {"broken.after_marker"}


# --- CodeCheck: a licence, a CSV, or a loud refusal -------------------------------


def test_codecheck_reports_violations_or_refuses_but_never_answers_silently_nothing(
    tmp_path: Path,
) -> None:
    """Requirement 6.9's failure mode, on whatever licence this machine has.

    ``und codecheck`` writes its violations as CSV into a directory it is given, and on a
    licence without CodeCheck it writes **nothing at all** and exits 1. An empty directory
    read as an empty violation list would report a clean run for a check that never ran, so
    the wrapper treats it as a failure.

    Both outcomes are asserted because both are real: this machine's licence excludes
    CodeCheck, and a machine whose licence includes it must not fail here. What is refused in
    either case is the third outcome -- an empty answer with no error.
    """
    root = write_tree(tmp_path / "tree", {"pkg/one.py": "def one():\n    return 1\n"})
    db = tmp_path / "codecheck.und"
    build_database(db, root, ("python",))
    out_dir = tmp_path / "violations"
    out_dir.mkdir()
    runner = CodeCheckRunner(UndCli(real_env("upython"), NullCommandLog()))
    files = analysed(db)
    assert files, "the fixture must give CodeCheck something to check"

    try:
        violations = runner.run(db, CODECHECK_CONFIG, files, out_dir)
    except LicenseError as unlicensed:
        assert "CodeCheck" in unlicensed.und_output, unlicensed.und_output
        assert list(out_dir.glob("*.csv")) == [], (
            "an unlicensed run wrote a CSV, so the refusal is not what it looks like"
        )
        return

    assert list(out_dir.glob("*.csv")), "a licensed run must have written the export"
    for violation in violations:
        assert violation.check_id
        assert violation.path in files
