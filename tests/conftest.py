"""Shared test infrastructure: temporary git repositories, port fakes, the contract gate.

Three things live here, in the order the test suite needs them (task 3.2):

* :class:`GitRepoBuilder` and the ``git_repo`` factory fixture — a throwaway repository per
  test, isolated from the developer's own git configuration, able to produce the states the
  gate reasons about: a commit, a staged change whose content differs from the working tree
  (requirement 4.1), a staged or unstaged rename, a staged deletion, and the ``.git/hooks``
  directory the hook installer writes into (requirement 11.1).
* :class:`FakeCommandLog` and :class:`FakeProgress` — recording implementations of the
  ``CommandLog`` and ``Progress`` protocols from :mod:`scitools_hook.models.progress`, so
  adapter tests can assert on the external commands a component ran.
* The contract gate — :func:`understand_probe` resolves ``und`` from ``SCITOOLS_HOME``
  (preferred, because it is the variable the Gate itself documents) or from ``PATH`` and
  asks it whether a license is available; :func:`pytest_collection_modifyitems` skips every
  ``contract``-marked test with the reason the probe gave. :func:`sample_databases` then
  builds the ``before`` and ``after`` Understand databases from
  ``tests/fixtures/sample_project/`` with plain ``und`` subprocess calls — no adapter code,
  so contract tests keep testing Understand rather than the code under test.

Understand's global switches must precede the subcommand (``und -quiet create …``); the
verified command form is ``und -quiet create -db <db> -languages python c++ -local
add <root> analyze``. ``-local`` keeps analysis data inside the ``.und`` directory instead
of the user profile.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import pytest

TESTS_DIR = Path(__file__).resolve().parent
"""Directory holding the test suite; also the import root for the ``fixtures`` package."""

SAMPLE_PROJECT = TESTS_DIR / "fixtures" / "sample_project"
"""Tiny Python + C/C++ project in two versions, the input of every contract test."""

Side = Literal["before", "after"]

_UND_TIMEOUT_S = 600
"""Generous ceiling: a contract run must fail loudly rather than hang the suite."""


# --- temporary git repositories -------------------------------------------------


@dataclass
class GitRepoBuilder:
    """A throwaway git repository built one plumbing call at a time.

    Every call runs with the developer's global and system git configuration switched off,
    so a repository behaves the same on any machine; identity and signing are configured
    locally instead, otherwise ``commit`` fails wherever ``user.email`` is unset.
    """

    path: Path

    def init(self) -> GitRepoBuilder:
        """Create the directory and initialize an empty repository on branch ``main``."""
        self.path.mkdir(parents=True, exist_ok=True)
        self.run("-c", "init.defaultBranch=main", "init", "--quiet")
        self.run("config", "user.name", "Gate Test")
        self.run("config", "user.email", "gate@example.invalid")
        self.run("config", "commit.gpgsign", "false")
        return self

    def run(self, *args: str) -> str:
        """Run ``git <args>`` in the repository and return its stripped standard output."""
        return self.capture(*args).strip()

    def capture(self, *args: str) -> str:
        """Run ``git <args>`` and return its standard output verbatim, newlines included."""
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        # Drop any inherited repository/identity pointers so the builder is unaffected by an
        # outer git invocation (a hook runs with GIT_DIR and GIT_INDEX_FILE already set).
        for leaked in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ):
            env.pop(leaked, None)
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed in {self.path} (rc={proc.returncode}):\n"
                f"{proc.stderr.strip()}"
            )
        return proc.stdout

    def write(self, rel: str, text: str) -> Path:
        """Write ``text`` to ``rel`` in the working tree, creating parents; do not stage."""
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def unstaged_edit(self, rel: str, text: str) -> Path:
        """Change ``rel`` in the working tree only, so the index keeps its own content."""
        return self.write(rel, text)

    def stage(self, *rels: str) -> None:
        """Stage the named paths, or every change in the working tree when none is given."""
        if rels:
            self.run("add", "--", *rels)
        else:
            self.run("add", "-A")

    def commit(self, message: str) -> str:
        """Commit whatever is staged and return the new commit hash."""
        self.run("commit", "--quiet", "-m", message)
        return self.run("rev-parse", "HEAD")

    def rename(self, old: str, new: str, staged: bool = True) -> None:
        """Rename ``old`` to ``new``, staging the rename unless ``staged`` is false."""
        target = self.path / new
        target.parent.mkdir(parents=True, exist_ok=True)
        if staged:
            self.run("mv", "--", old, new)
        else:
            (self.path / old).rename(target)

    def delete(self, rel: str, staged: bool = True) -> None:
        """Delete ``rel``, staging the deletion unless ``staged`` is false."""
        if staged:
            self.run("rm", "--quiet", "--", rel)
        else:
            (self.path / rel).unlink()

    def staged_content(self, rel: str) -> str:
        """Return the content of ``rel`` as it sits in the index (requirement 4.1)."""
        return self.capture("show", f":{rel}")


class MakeGitRepo(Protocol):
    """The ``git_repo`` fixture: call it once per repository a test needs."""

    def __call__(self, name: str = "repo") -> GitRepoBuilder: ...


@pytest.fixture
def git_repo(tmp_path: Path) -> MakeGitRepo:
    """Factory for initialized temporary git repositories, one directory under ``tmp_path``."""

    def make(name: str = "repo") -> GitRepoBuilder:
        return GitRepoBuilder(tmp_path / name).init()

    return make


# --- fakes for the Progress and CommandLog ports --------------------------------


@dataclass
class FakeCommandLog:
    """``CommandLog`` that keeps every recorded command (requirement 12.8)."""

    calls: list[tuple[list[str], float, int]] = field(default_factory=list)

    def record(self, argv: list[str], seconds: float, rc: int) -> None:
        """Record one finished command, copying ``argv`` so later mutation cannot rewrite it."""
        self.calls.append((list(argv), seconds, rc))

    @property
    def commands(self) -> list[str]:
        """The recorded command lines, joined, for readable assertions."""
        return [" ".join(argv) for argv, _, _ in self.calls]


@dataclass
class FakeProgress:
    """``Progress`` that keeps every phase and note instead of writing to a terminal."""

    started: list[str] = field(default_factory=list)
    finished: list[tuple[str, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def start(self, phase: str) -> None:
        """Record the start of a phase."""
        self.started.append(phase)

    def finish(self, phase: str, seconds: float) -> None:
        """Record the end of a phase and how long it took."""
        self.finished.append((phase, seconds))

    def note(self, message: str) -> None:
        """Record a one-line diagnostic."""
        self.notes.append(message)


@pytest.fixture
def command_log() -> FakeCommandLog:
    """A fresh recording command log."""
    return FakeCommandLog()


@pytest.fixture
def progress() -> FakeProgress:
    """A fresh recording progress reporter."""
    return FakeProgress()


# --- the contract gate ----------------------------------------------------------


@dataclass(frozen=True)
class UnderstandProbe:
    """The result of looking for a licensed Understand, computed once per session."""

    und: Path | None
    reason: str | None

    @property
    def usable(self) -> bool:
        """True when ``und`` was found and reports a valid license."""
        return self.reason is None


_UND_GLOBS = ("bin/*/und", "bin/und", "und", "bin/*/und.exe", "bin/und.exe", "und.exe")
_NO_LICENSE = re.compile(
    r"no .{0,20}licen[cs]e|not licensed|licen[cs]e .{0,20}(expired|invalid)", re.I
)


def _find_und(home: Path) -> Path | None:
    """Find the ``und`` executable inside an Understand installation directory."""
    for pattern in _UND_GLOBS:
        for candidate in sorted(home.glob(pattern)):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def _run_und(und: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``und`` as a plain subprocess, never through the code under test."""
    return subprocess.run(
        [str(und), *args],
        capture_output=True,
        text=True,
        timeout=_UND_TIMEOUT_S,
        check=False,
    )


def _probe_license(und: Path) -> UnderstandProbe:
    """Ask ``und`` whether a license is available; ``-isundlicensed`` prints ``1`` or ``0``."""
    try:
        answer = _run_und(und, "-isundlicensed")
    except (OSError, subprocess.SubprocessError) as exc:
        return UnderstandProbe(und, f"{und} could not be run: {exc!r}")
    if answer.returncode == 0 and answer.stdout.strip() == "1":
        return UnderstandProbe(und, None)
    if answer.returncode == 0 and answer.stdout.strip() == "0":
        return UnderstandProbe(und, f"{und} -isundlicensed printed 0: no valid Understand license")
    try:
        status = _run_und(und, "license")
    except (OSError, subprocess.SubprocessError) as exc:
        return UnderstandProbe(und, f"{und} license could not be run: {exc!r}")
    text = f"{status.stdout}\n{status.stderr}".strip()
    if status.returncode == 0 and not _NO_LICENSE.search(text):
        return UnderstandProbe(und, None)
    first_line = text.splitlines()[0] if text else "(no output)"
    return UnderstandProbe(und, f"{und} license reports no valid license: {first_line}")


@functools.cache
def understand_probe() -> UnderstandProbe:
    """Locate a licensed Understand once per session and say precisely what is missing."""
    home = os.environ.get("SCITOOLS_HOME")
    if home:
        und = _find_und(Path(home).expanduser())
        if und is None:
            return UnderstandProbe(
                None,
                f"SCITOOLS_HOME={home!r} holds no 'und' executable "
                f"(looked for {', '.join(_UND_GLOBS)})",
            )
    else:
        found = shutil.which("und")
        if found is None:
            return UnderstandProbe(
                None, "SCITOOLS_HOME is not set and no 'und' executable is on PATH"
            )
        und = Path(found)
    return _probe_license(und)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip every ``contract``-marked test when no licensed Understand is available."""
    probe = understand_probe()
    if probe.usable:
        return
    skip = pytest.mark.skip(reason=f"needs a licensed SciTools Understand: {probe.reason}")
    for item in items:
        if "contract" in item.keywords:
            item.add_marker(skip)


# --- the sample project and the databases built from it -------------------------


@dataclass(frozen=True)
class SampleDatabases:
    """The two Understand databases built from the sample project, plus their sources."""

    und: Path
    before_db: Path
    after_db: Path
    before_root: Path
    after_root: Path

    def db(self, side: Side) -> Path:
        """The database of the ``before`` or ``after`` side."""
        return self.before_db if side == "before" else self.after_db

    def root(self, side: Side) -> Path:
        """The source root the database of ``side`` was built from."""
        return self.before_root if side == "before" else self.after_root

    def list_files(self, side: Side) -> list[str]:
        """Open the database with ``und list files`` and return the analyzed source files."""
        listing = _run_und(self.und, "-db", str(self.db(side)), "list", "files")
        if listing.returncode != 0:
            raise RuntimeError(
                f"und list files failed for {self.db(side)} (rc={listing.returncode}):\n"
                f"{listing.stderr.strip()}"
            )
        root = str(self.root(side))
        return [line.strip() for line in listing.stdout.splitlines() if line.startswith(root)]


@pytest.fixture(scope="session")
def sample_project() -> Path:
    """The ``before``/``after`` source trees the contract databases are built from."""
    return SAMPLE_PROJECT


@pytest.fixture(scope="session")
def sample_databases(tmp_path_factory: pytest.TempPathFactory) -> SampleDatabases:
    """Build ``before.und`` and ``after.und`` once per session with plain ``und`` calls.

    A machine that has a licensed Understand and still cannot build these databases has a
    real problem, so a failing ``und`` fails the run with its command and stderr instead of
    quietly skipping.
    """
    probe = understand_probe()
    if not probe.usable:
        pytest.skip(f"needs a licensed SciTools Understand: {probe.reason}")
    und = probe.und
    assert und is not None  # guaranteed by probe.usable
    workdir = tmp_path_factory.mktemp("sample-databases")
    databases: dict[Side, Path] = {}
    for side in ("before", "after"):
        db = workdir / f"{side}.und"
        root = SAMPLE_PROJECT / side
        argv = [
            "-quiet",
            "create",
            "-db",
            str(db),
            "-languages",
            "python",
            "c++",
            "-local",
            "add",
            str(root),
            "analyze",
        ]
        built = _run_und(und, *argv)
        if built.returncode != 0 or not db.exists():
            pytest.fail(
                f"building the {side} database failed (rc={built.returncode}):\n"
                f"  {und} {' '.join(argv)}\n"
                f"stdout: {built.stdout.strip()}\nstderr: {built.stderr.strip()}"
            )
        databases[side] = db
    return SampleDatabases(
        und=und,
        before_db=databases["before"],
        after_db=databases["after"],
        before_root=SAMPLE_PROJECT / "before",
        after_root=SAMPLE_PROJECT / "after",
    )
