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
report in it -- no CSV on 6.5, no ``results.sarif`` on 8.0 -- must never be read as "no
violations".
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from contract_project import (
    TIMEOUT_S,
    a_symlink,
    build_database,
    extract,
    real_env,
    run_und,
    upython,
    write_tree,
)

from scitools_hook.errors import LicenseError
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.understand.codecheck import CodeCheckRunner
from scitools_hook.understand.codecheck_sarif import RESULTS_SARIF
from scitools_hook.understand.und_cli import (
    ALL,
    UndCli,
)

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

CODECHECK_CONFIG = "Hersteller Initiative Software (HIS) Metrics"
"""A configuration 8.0 ships (under Published Standards). 6.5's "Sandbox" no longer exists:
7.0 rebuilt CodeCheck around plugins, and an unknown name fails with `Unable to find config`,
which is neither a refusal nor a result. On this machine the licence excludes CodeCheck, and
8.0 refuses it as "No checks in this configuration are licensed to run" (measured)."""
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


PY2_ONLY: tuple[str, ...] = ("has_key", "iteritems", "raw_input")
"""Builtins that exist only in Python 2, and so name the language model of a database.

**The discriminator is the model, not the error count.** ``Errors:0`` from ``und`` has been
read as proof of a clean analysis four separate times on this project and was wrong every
time; these three names are in the database or they are not, and nothing about the exit
status or the banner can fake them.
"""

MODEL_SCRIPT = """
import json
import sys

import understand

db = understand.open(sys.argv[1])
names = {ent.name() for ent in db.ents()}
print(json.dumps(sorted(names.intersection(sys.argv[2:]))))
db.close()
"""
"""Read straight from the database through Understand's own interpreter, not from output."""

PRETEND_PYTHON_TWO = "#!/bin/sh\necho 'Python 2.7.18'\n"
"""A ``python`` that answers like a Python 2. Measured: ``und`` falls back on this one."""


def language_model_of(db: Path) -> list[str]:
    """The Python-2-only builtins the database holds; empty means it was analysed as Python 3."""
    done = subprocess.run(
        [str(upython()), "-c", MODEL_SCRIPT, str(db), *PY2_ONLY],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return list(json.loads(done.stdout))


def decoy_dir(base: Path) -> Path:
    """A directory holding a ``python`` that is not one, under the name ``und`` looks for."""
    base.mkdir(parents=True, exist_ok=True)
    decoy = base / "python"
    decoy.write_text(PRETEND_PYTHON_TWO, encoding="utf-8")
    decoy.chmod(decoy.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return base


def test_a_bare_und_analyses_python_2_when_the_path_carries_no_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect in the tool, measured on one database with nothing but ``PATH`` differing.

    Understand 6.5.1204 decides the Python dialect by **executing** a bare ``python`` from
    ``PATH``. With one present, ``["k", *xs]`` parses and both routines are recorded. With
    only ``python3`` -- which is every modern distribution's default, and what a git hook
    inherits from a login shell -- the same file, the same database and the same command
    report **eight parse errors**, ``after_marker`` disappears from the database entirely,
    and ``before_marker`` swallows the rest of the file (four code lines instead of two).

    Neither run is an error. Both exit 0, and the failing one prints its errors, so
    requirement 2.6's report is implementable either way. What is not implementable from
    above is noticing the difference: two machines, one commit, two entity sets, and nothing
    in the output that names ``PATH``.

    The measurement is deliberately made on one database, analysed twice, so that nothing but
    the environment differs -- and the recovery direction is asserted too, which is what rules
    out "the first analysis simply did less work".

    ``python3`` alone is the control. The link is to :data:`sys.executable`, so both
    directories hold the same real interpreter and only the *name* differs.

    This runs ``und`` **directly**. The Gate's own wrapper no longer inherits this ``PATH``
    at all -- see the test below -- so driving the defect through it would measure the fix
    instead of the tool, and the day Understand changes this behaviour is the day this test
    should say so.
    """
    root = write_tree(tmp_path / "tree", UNPACK_SOURCES)
    db = tmp_path / "unpack.und"
    for argv in (
        ["-quiet", "create", "-db", str(db), "-languages", "python", "-local"],
        ["-quiet", "-db", str(db), "add", str(root)],
    ):
        assert run_und(*argv).returncode == 0

    monkeypatch.setenv("PATH", str(interpreter_dir(tmp_path / "py3", "python3")))
    without = run_und("-db", str(db), "analyze", "-all", "-errors", "-warnings")
    lost = routines_of(db, root)
    lost_model = language_model_of(db)

    monkeypatch.setenv("PATH", str(interpreter_dir(tmp_path / "py", "python")))
    recovery = run_und("-db", str(db), "analyze", "-all", "-errors", "-warnings")
    recovered = routines_of(db, root)
    recovered_model = language_model_of(db)

    assert without.returncode == 0 and recovery.returncode == 0
    assert list(lost_model) == sorted(PY2_ONLY), "no python on PATH: a Python 2 model"
    assert "expected token ']'" in without.stdout
    assert "broken.after_marker" not in lost, "the routine after the error must be gone"
    assert lost["broken.before_marker"] == 4.0, "it must have swallowed the rest of the file"
    assert "intact.intact_marker" in lost, "another file must be unaffected"

    assert recovered_model == [], "a bare python on PATH: a Python 3 model"
    assert recovered["broken.after_marker"] == 2.0
    assert recovered["broken.before_marker"] == 2.0
    assert set(recovered) == set(lost) | {"broken.after_marker"}


@pytest.mark.parametrize("hostile", ["python3-only", "python-2-decoy", "nothing-at-all"])
def test_the_wrapper_pins_the_interpreter_whatever_the_path_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostile: str
) -> None:
    """The fix, measured against the same discriminator: the ``PATH`` no longer decides.

    Three environments that all produced a **Python 2** database before -- a distribution
    with only ``python3``, a developer whose first ``python`` is a Python 2, and an empty
    search path -- are driven through the production
    :class:`~scitools_hook.understand.und_cli.UndCli` for *every* step, ``create`` and ``add``
    included. All three must give the same Python 3 database: no Python-2-only builtin in it,
    both routines present, no parse error.

    This fails on the old behaviour. ``UndCli`` used to start ``und`` with no ``env`` at all,
    which is the ambient ``PATH``, so each of these three cases produced ``has_key``,
    ``iteritems`` and ``raw_input`` in the database and lost ``after_marker`` with it.

    The parse-error assertion is last and is the *weakest* of the three on purpose. What
    matters is the model and the entity set: a routine that is absent is not an error, it is
    an absence, and an absence breaks no threshold.
    """
    directories = {
        "python3-only": lambda: interpreter_dir(tmp_path / "py3", "python3"),
        "python-2-decoy": lambda: decoy_dir(tmp_path / "decoy"),
        "nothing-at-all": lambda: _empty_dir(tmp_path / "bare"),
    }
    monkeypatch.setenv("PATH", str(directories[hostile]()))
    root = write_tree(tmp_path / "tree", UNPACK_SOURCES)
    db = tmp_path / "pinned.und"
    cli = UndCli(real_env("upython"), NullCommandLog())

    cli.create(db, ["python"], local=True)
    cli.add(db, root, [])
    analysed_result = cli.analyze(db, ALL)

    assert language_model_of(db) == [], "the wrapper must never leave a Python 2 database"
    routines = routines_of(db, root)
    assert "broken.after_marker" in routines, "the routine after the construct must be there"
    assert routines["broken.before_marker"] == 2.0
    assert routines["broken.after_marker"] == 2.0
    assert analysed_result.parse_errors == []


def _empty_dir(base: Path) -> Path:
    """A search path with nothing on it at all: the strictest of the three hostile cases."""
    base.mkdir(parents=True, exist_ok=True)
    return base


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
        assert sorted(path.name for path in out_dir.iterdir()) == [], (
            "an unlicensed run wrote a report, so the refusal is not what it looks like"
        )
        return

    # Which report a licensed run leaves depends on the build: 6.5 writes the per-violation
    # CSV, 8.0 writes results.sarif and drops those exports (requirement 2.3). Either is an
    # answer; an empty directory is the third outcome this test exists to refuse.
    assert list(out_dir.glob("*.csv")) or (out_dir / RESULTS_SARIF).is_file(), (
        f"a licensed run must have written a report; it wrote "
        f"{sorted(path.name for path in out_dir.iterdir())}"
    )
    for violation in violations:
        assert violation.check_id
        assert violation.path in files
