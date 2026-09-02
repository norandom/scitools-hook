"""What the database manager assumes about ``und``, measured against the real one (task 8.1).

:mod:`tests.understand.test_database` proves the logic; this proves the four measurements the
logic is built on, so that an Understand whose behaviour differs says so here rather than
somewhere downstream where the symptom is a database that is quietly out of date.

The extension table is the reason this file exists. ``LANGUAGE_BY_SUFFIX`` decides which
paths may be named in a ``-files`` list, and being wrong about it costs either a broken run
(a path Understand does not hold makes the whole command exit 1) or a stale database (a path
it does hold, never named). Both directions are therefore measured, over the **complete** set
of extensions the installed build knows -- read out of its own ``FileTypes`` table, so an
extension added by a later build is caught instead of missed.

Every ``und`` call here is a plain subprocess: a contract test that went through the wrapper
would be testing the wrapper.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import understand_probe

from scitools_hook.understand.database import LANGUAGE_BY_SUFFIX

pytestmark = pytest.mark.contract

TIMEOUT_S = 600
"""Generous ceiling: a contract run must fail loudly rather than hang the suite."""

LANGUAGES = [
    "Ada",
    "Assembly",
    "Basic",
    "C++",
    "C#",
    "Fortran",
    "Java",
    "Jovial",
    "Pascal",
    "Python",
    "VHDL",
    "Web",
]
"""Every language this build accepts, as ``und list settings`` spells them."""

FILE_TYPES_HEADER = "FileTypes:"
"""The block of ``und list settings`` that lists every extension the build recognises."""


def und() -> Path:
    """The licensed ``und`` the session probe found; the marker skips the test without one."""
    probe = understand_probe()
    assert probe.und is not None, probe.reason
    return probe.und


def run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``und`` directly, never through the adapter this test exists to justify."""
    return subprocess.run(
        [str(und()), *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )


def build(db: Path, tree: Path, languages: list[str]) -> None:
    """Create a database over ``tree`` and analyse it whole, failing loudly if it cannot."""
    for argv in (
        ["-quiet", "create", "-db", str(db), "-languages", *languages, "-local"],
        ["-quiet", "-db", str(db), "add", str(tree)],
        ["-db", str(db), "analyze", "-all", "-errors", "-warnings"],
    ):
        done = run(*argv)
        if done.returncode != 0:
            pytest.fail(f"und {' '.join(argv)} exited {done.returncode}: {done.stderr.strip()}")


def project_files(db: Path) -> list[str]:
    """The files the database holds, as ``und list files`` reports them."""
    listing = run("-db", str(db), "list", "files")
    assert listing.returncode == 0, listing.stderr
    return [line.strip() for line in listing.stdout.splitlines()[1:] if line.strip()]


def known_extensions(db: Path) -> set[str]:
    """Every extension in the build's own ``FileTypes`` table.

    Only the extensions are read, by taking the tokens that start with a dot: the language
    column holds names with spaces in them (``MSDos Batch``), so a positional parse of the
    three-column table would be the fragile way to ask.
    """
    settings = run("-db", str(db), "list", "settings")
    assert settings.returncode == 0, settings.stderr
    lines = settings.stdout.splitlines()
    start = next(at for at, line in enumerate(lines) if line.startswith(FILE_TYPES_HEADER))
    found: set[str] = set()
    for line in lines[start + 1 :]:
        if not line.strip():
            break
        found.update(token for token in line.split() if token.startswith("."))
    return found


@pytest.fixture(scope="module")
def extension_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A database over one file per extension the build knows, with every language enabled."""
    workdir = tmp_path_factory.mktemp("extension-map")
    tree = workdir / "tree"
    tree.mkdir()
    probe = workdir / "probe.und"
    build(probe, tree, ["Python"])
    for number, suffix in enumerate(sorted(known_extensions(probe) | set(LANGUAGE_BY_SUFFIX))):
        (tree / f"f{number}{suffix}").write_text("x = 1\n", encoding="utf-8")
    db = workdir / "all.und"
    build(db, tree, LANGUAGES)
    return db


def test_the_language_table_names_exactly_the_files_understand_enrols(
    extension_project: Path,
) -> None:
    """Both directions at once, which is what makes this test worth its runtime.

    An extension here that Understand does not enrol would be named in a ``-files`` list it
    cannot appear in, and the whole command would exit 1; one Understand enrols that is
    missing here would be dropped from every incremental analysis and go stale in silence.
    """
    enrolled = {Path(path).suffix for path in project_files(extension_project)}

    assert enrolled == set(LANGUAGE_BY_SUFFIX)


def test_every_language_the_table_names_is_one_und_create_accepts(tmp_path: Path) -> None:
    """A language that does not exist makes ``create`` exit 1, and the map must name none."""
    for language in sorted(set(LANGUAGE_BY_SUFFIX.values())):
        db = tmp_path / f"{language.replace('+', 'p').replace('#', 's')}.und"
        done = run("-quiet", "create", "-db", str(db), "-languages", language, "-local")
        assert done.returncode == 0, f"{language}: {done.stdout.strip()} {done.stderr.strip()}"


def test_a_file_understand_does_not_hold_makes_the_whole_analysis_fail(tmp_path: Path) -> None:
    """The measurement the ``-files`` list is filtered for: one stray path fails the command.

    The valid file in the same list *is* analysed -- the marker function appears in the
    metrics afterwards -- which is exactly why the status is the only honest signal, and why
    a ``README.md`` may never reach the list.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "main.py").write_text("def before_marker():\n    return 1\n", encoding="utf-8")
    (tree / "README.md").write_text("# not a source file\n", encoding="utf-8")
    db = tmp_path / "mixed.und"
    build(db, tree, ["Python"])
    (tree / "main.py").write_text("def after_marker():\n    return 1\n", encoding="utf-8")
    listing = tmp_path / "files.txt"
    listing.write_text(f"{tree / 'main.py'}\n{tree / 'README.md'}\n", encoding="utf-8")

    refused = run("-db", str(db), "analyze", "-files", f"@{listing}", "-errors", "-warnings")

    assert refused.returncode != 0
    assert "README.md" in refused.stdout + refused.stderr
    assert "after_marker" in metric_names(db)


def test_a_new_file_cannot_be_analysed_before_it_is_added(tmp_path: Path) -> None:
    """Why ``und add`` runs whenever the shadow gained anything, and not only at creation."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "main.py").write_text("def main_marker():\n    return 1\n", encoding="utf-8")
    db = tmp_path / "fresh.und"
    build(db, tree, ["Python"])
    (tree / "later.py").write_text("def later_marker():\n    return 1\n", encoding="utf-8")
    listing = tmp_path / "files.txt"
    listing.write_text(f"{tree / 'later.py'}\n", encoding="utf-8")

    refused = run("-db", str(db), "analyze", "-files", f"@{listing}", "-errors", "-warnings")
    assert refused.returncode != 0
    assert "later_marker" not in metric_names(db)

    added = run("-quiet", "-db", str(db), "add", str(tree))
    assert added.returncode == 0
    accepted = run("-db", str(db), "analyze", "-files", f"@{listing}", "-errors", "-warnings")

    assert accepted.returncode == 0, accepted.stderr
    assert "later_marker" in metric_names(db)


def test_a_full_analysis_drops_a_file_that_has_left_the_disk(tmp_path: Path) -> None:
    """What makes ``analyze -all`` the correct answer to a deletion this module cannot name."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "kept.py").write_text("def kept_marker():\n    return 1\n", encoding="utf-8")
    (tree / "gone.py").write_text("def gone_marker():\n    return 1\n", encoding="utf-8")
    db = tmp_path / "deleting.und"
    build(db, tree, ["Python"])
    assert "gone_marker" in metric_names(db)

    (tree / "gone.py").unlink()
    swept = run("-db", str(db), "analyze", "-all", "-errors", "-warnings")

    assert swept.returncode == 0, swept.stderr
    names = metric_names(db)
    assert "gone_marker" not in names
    assert "kept_marker" in names
    assert not any(path.endswith("gone.py") for path in project_files(db))


def test_a_full_analysis_ignores_the_timestamps_the_changed_switch_trusts(
    tmp_path: Path,
) -> None:
    """The correctness argument for the whole fallback path, and for not using ``-changed``.

    The file's mtime is forced back to what the previous analysis recorded while its content
    and size change. ``-changed`` answers ``Errors:0`` and leaves the old entities in place;
    ``-all`` re-parses it. A gate whose incremental primitive can silently skip a changed
    file is a gate that reports on code nobody wrote, so this is the difference that decides
    which switch this module uses.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    source = tree / "main.py"
    source.write_text("def before_marker(x):\n    return x\n", encoding="utf-8")
    db = tmp_path / "frozen.und"
    build(db, tree, ["Python"])
    recorded = source.stat()
    source.write_text("def after_marker(x):\n    return x + 1\n", encoding="utf-8")
    os.utime(source, ns=(recorded.st_atime_ns, recorded.st_mtime_ns))
    assert source.stat().st_mtime_ns == recorded.st_mtime_ns
    assert source.stat().st_size != recorded.st_size

    missed = run("-db", str(db), "analyze", "-changed", "-errors", "-warnings")
    assert missed.returncode == 0, missed.stderr
    assert "after_marker" not in metric_names(db), "und -changed noticed a frozen mtime"

    swept = run("-db", str(db), "analyze", "-all", "-errors", "-warnings")

    assert swept.returncode == 0, swept.stderr
    assert "after_marker" in metric_names(db)


def metric_names(db: Path) -> set[str]:
    """Every entity name in the database, from ``und metrics``.

    A marker entity read back out of the database is the discriminator these tests need. An
    error count cannot fail in the interesting direction: ``Errors:0`` is what a run that
    silently skipped the file prints too.

    ``metrics`` takes no output path in project mode -- passing one is "an unused argument
    and ignored", with status 1 -- and writes ``<database stem>.csv`` beside the database.
    """
    export = db.with_suffix(".csv")
    export.unlink(missing_ok=True)
    done = run("-db", str(db), "metrics")
    assert done.returncode == 0, done.stderr
    rows = export.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
    return {row.split(",")[1].strip('"').split(".")[-1] for row in rows if "," in row}
