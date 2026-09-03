"""Harness for the end-to-end suite: a real repository, a real environment, a real process.

Everything here exists so that task 10.2's tests can drive the **installed console script**
the way a developer or an agent does -- through ``git commit``, through the pre-commit
framework's argv, through ``check --worktree`` -- rather than through a ``CliRunner``. Four
properties of the harness are load-bearing, and each one is a defect this project already
paid for once.

* **The environment is built, never inherited.** :func:`isolated_env` returns a complete
  environment for a child process, and :func:`_guarded` refuses to hand back one that leaves
  ``HOME``, ``XDG_CONFIG_HOME``, ``GIT_CONFIG_GLOBAL`` or ``GIT_CONFIG_NOSYSTEM`` unset or
  pointing outside the test's own directory. That is not tidiness: ``install-hook --global``
  resolves through ``git config --global core.hooksPath`` and falls back to
  ``$XDG_CONFIG_HOME/git/hooks`` read from the **ambient** environment, so a child process
  missing those four would install a pre-commit hook into the developer's real
  ``~/.config/git/hooks`` during a test run.

* **``PATH`` is narrowed, never emptied.** The Gate runs ``git`` itself, and a run with no
  ``PATH`` disables the very probe the test is watching -- this project has hit that five
  times. :func:`isolated_env` therefore builds a ``PATH`` of exactly two directories: the one
  holding the installed ``scitools-hook`` and the one holding ``git``.
  :func:`missing_tool_path` is the deliberate opposite, a ``PATH`` with git and nothing else,
  which is how requirement 11.4's "the tool is not installed" is produced on purpose.

* **Every child runs under an external timeout.** ``subprocess.run(timeout=...)`` kills the
  process; an in-process alarm could not, and a hook that hangs would hang the suite.

* **Every child is told where to run.** ``cwd`` is passed to every call rather than relying
  on the process's own directory: a probe whose ``cd`` did not persist once reported a clean
  pass against the wrong repository, which is the false green this harness is shaped to make
  impossible.

The fixture directories under ``tests/fixtures/e2e`` are the ``SCITOOLS_HOOK_FAKE_UNDERSTAND``
seam's input. ``violating/`` describes a change that breaks ``routine.MaxNesting`` in two
files and carries an ``analyze.json`` with one parse error; ``fixed/`` describes the same two
routines back at the values ``HEAD`` holds. The parse error is the tests' **marker**: it
proves which fixture directory answered, so a run that read the wrong one -- or none, which
the seam reports as "this project parsed cleanly" -- cannot pass as the right one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scitools_hook.understand.fake import FAKE_VAR

E2E_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "e2e"
VIOLATING = E2E_FIXTURES / "violating"
FIXED = E2E_FIXTURES / "fixed"
"""The two fixture directories the seam is pointed at, in that order, by the developer path."""

PARSE_ERROR_PATH = "pkg/unparsable.py"
"""The marker inside ``violating/analyze.json``; see the module docstring."""

DEEP = "pkg/deep.py"
OTHER = "pkg/other.py"
"""The two source files the fixtures describe entities in; the repository holds both."""

SKIP_VAR = "SCITOOLS_HOOK_SKIP"
SOFT_FAIL_VAR = "SCITOOLS_HOOK_SOFT_FAIL"
"""The shim's two documented variables (req 11.4, 11.5). ``test_hook_workflow`` pins both
spellings against the shipped template, so a rename there cannot leave a test setting a
variable nothing reads."""

TIMEOUT_S = 180
"""External ceiling for one child process driven by the fixture seam."""

GUARDED_VARS = ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")
"""What must be set before any child that can reach ``install-hook --global`` is started."""

CLI_NAME = "scitools-hook"

_BASELINE = {
    DEEP: "def walk(rows):\n    return rows\n",
    OTHER: "def scan(items):\n    return items\n",
}
"""The committed state: two one-line routines, matching the ``before`` fixture snapshot."""

NESTED = (
    "def walk(rows):\n"
    "    for row in rows:\n"
    "        if row:\n"
    "            for cell in row:\n"
    "                if cell:\n"
    "                    print(cell)\n"
)
"""A working-tree edit that a reader can see is over the nesting limit.

The fixture seam, not this text, decides the metrics -- so the file is written to make the
test readable, and the ``violating`` snapshot is what actually reports ``MaxNesting``.
"""


def git_executable() -> str:
    """The ``git`` the harness drives; without one none of this suite means anything."""
    found = shutil.which("git")
    if found is None:  # pragma: no cover - a machine with no git cannot run this suite
        raise RuntimeError("the end-to-end suite needs git on PATH")
    return found


def cli_executable() -> Path:
    """The installed ``scitools-hook`` console script.

    Task 10.2 is about the command a user types, so the script itself is what is run -- not
    ``python -m``, which would prove nothing about the entry point the hook shim looks for.
    A missing script is an error rather than a skip: the documented gate is ``uv run pytest``,
    where the script is always beside the interpreter, and a suite that quietly skipped its
    whole premise would be the false green this project keeps meeting.
    """
    found = shutil.which(CLI_NAME)
    if found is not None:
        return Path(found)
    beside = Path(sys.executable).parent / CLI_NAME
    if beside.is_file():
        return beside
    raise RuntimeError(
        f"the end-to-end suite needs the installed {CLI_NAME!r} console script; "
        f"it is neither on PATH nor at {beside}. Run the suite with `uv run pytest`."
    )


def _guarded(env: dict[str, str], sandbox: Path) -> dict[str, str]:
    """Refuse an environment that could let a child reach the developer's own home.

    The check is on the *values* as well as the names, because a variable set to the real
    home is exactly as dangerous as one left unset.
    """
    for name in GUARDED_VARS:
        value = env.get(name)
        assert value, f"{name} must be set in every child environment (see the module docstring)"
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "GIT_CONFIG_GLOBAL"):
        assert Path(env[name]).is_relative_to(sandbox), (
            f"{name}={env[name]} points outside the test's own directory {sandbox}"
        )
    return env


def isolated_env(sandbox: Path, *, path: str | None = None, **extra: str) -> dict[str, str]:
    """A complete environment for a child process, anchored under ``sandbox``.

    Nothing is inherited: the returned mapping is the whole environment the child gets, so a
    variable the developer happens to export cannot change an answer. ``extra`` adds or
    replaces entries after the guard has run on the ones that matter.
    """
    home = sandbox / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(sandbox / "xdg"),
        "XDG_CACHE_HOME": str(sandbox / "cache"),
        "GIT_CONFIG_GLOBAL": str(sandbox / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PATH": path if path is not None else default_path(),
        "LC_ALL": "C.UTF-8",
    }
    _guarded(env, sandbox)
    env.update(extra)
    return env


def default_path() -> str:
    """``PATH`` with the installed CLI and git on it, and nothing else."""
    return os.pathsep.join(
        dict.fromkeys([str(cli_executable().parent), str(Path(git_executable()).parent)])
    )


def missing_tool_path(sandbox: Path) -> str:
    """``PATH`` holding one symlink to git: requirement 11.4's "the tool is not installed".

    Narrowed rather than emptied, deliberately. An empty ``PATH`` stops ``git`` itself from
    being found by anything the hook runs, so the run would fail for a reason the test is not
    about -- the trap this project has walked into five times.
    """
    bin_dir = sandbox / "no-gate-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "git"
    if not link.exists():
        link.symlink_to(git_executable())
    return str(bin_dir)


@dataclass(frozen=True)
class Workspace:
    """One temporary repository, its sandbox and the environment its children run in."""

    root: Path
    sandbox: Path
    env: dict[str, str]

    # --- running things ----------------------------------------------------------

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = TIMEOUT_S,
    ) -> subprocess.CompletedProcess[str]:
        """Run one child process under an external timeout, in a directory named explicitly."""
        return subprocess.run(
            list(argv),
            cwd=str(cwd if cwd is not None else self.root),
            env=dict(env if env is not None else self.env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def cli(
        self,
        *argv: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the installed console script by name, resolved through the child's own PATH."""
        return self.run([CLI_NAME, *argv], cwd=cwd, env=env)

    def git(
        self,
        *argv: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run git in the repository; the hook, when one is installed, runs with it."""
        return self.run([git_executable(), *argv], cwd=cwd, env=env)

    def git_ok(self, *argv: str, env: Mapping[str, str] | None = None) -> str:
        """Run git and insist it succeeded, so a broken step cannot look like an empty answer."""
        done = self.git(*argv, env=env)
        assert done.returncode == 0, f"git {' '.join(argv)} failed:\n{done.stderr}"
        return done.stdout

    def commit(
        self, message: str, *, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """A real ``git commit``, which is what runs the hook."""
        return self.git("commit", "-m", message, env=env)

    # --- looking at the result ----------------------------------------------------

    def log(self) -> list[str]:
        """The commit subjects, newest first; empty on a repository with no commit."""
        done = self.git("log", "--format=%s")
        return [] if done.returncode != 0 else done.stdout.split("\n")[:-1]

    def head(self) -> str:
        """The resolved commit hash of ``HEAD``."""
        return self.git_ok("rev-parse", "HEAD").strip()

    def write(self, rel: str, text: str) -> Path:
        """Write a file in the working tree without staging it."""
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def stage(self, *rels: str) -> None:
        """Stage the named paths."""
        self.git_ok("add", "--", *rels)

    def with_env(self, **extra: str) -> dict[str, str]:
        """This workspace's environment with a few entries added or replaced."""
        return {**self.env, **extra}

    def sync_state(self) -> dict[str, object]:
        """The analysis cache's recorded sync state, which names the commit the before side
        was built from.

        Read through ``db path`` rather than by rebuilding the cache key here, so the test
        cannot agree with itself about a location the tool does not use.
        """
        answer = self.cli("db", "path")
        assert answer.returncode == 0, answer.stderr
        state = Path(answer.stdout.strip()).parent / "state.json"
        return dict(json.loads(state.read_text(encoding="utf-8")))

    def shadow(self, side: str, rel: str) -> str:
        """The content of one file in a synced shadow tree."""
        answer = self.cli("db", "path")
        assert answer.returncode == 0, answer.stderr
        return (Path(answer.stdout.strip()).parent / side / rel).read_text(encoding="utf-8")


def make_workspace(sandbox: Path, name: str = "repo", **extra_env: str) -> Workspace:
    """A repository holding the two committed routines the fixture snapshots describe."""
    root = sandbox / name
    root.mkdir(parents=True, exist_ok=True)
    space = Workspace(root=root, sandbox=sandbox, env=isolated_env(sandbox, **extra_env))
    space.git_ok("-c", "init.defaultBranch=main", "init", "--quiet")
    space.git_ok("config", "user.name", "Gate End To End")
    space.git_ok("config", "user.email", "gate@example.invalid")
    space.git_ok("config", "commit.gpgsign", "false")
    for rel, text in _BASELINE.items():
        space.write(rel, text)
    space.stage(*_BASELINE)
    space.git_ok("commit", "--quiet", "-m", "baseline")
    return space


# --- the canary -------------------------------------------------------------------


def real_user_hook_paths() -> tuple[Path, ...]:
    """Where a child with an unguarded environment would install a *global* shim.

    Both spellings are watched, because which one is used depends on whether the developer
    exports ``XDG_CONFIG_HOME``, and a test must not depend on that to be safe.
    """
    ambient = os.environ.get("XDG_CONFIG_HOME", "").strip()
    homes = [Path.home() / ".config"]
    if ambient:
        homes.append(Path(ambient))
    return tuple(dict.fromkeys(base / "git" / "hooks" / "pre-commit" for base in homes))


def stamp(path: Path) -> tuple[bool, int | None, int | None]:
    """Enough of a file to notice it appearing, changing or being replaced."""
    try:
        info = path.stat()
    except OSError:
        return (False, None, None)
    return (True, info.st_mtime_ns, info.st_size)


def report(done: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """The JSON document a ``--format json`` run printed, and nothing else (req 7.4).

    ``json.loads`` over the whole stream is the assertion: a diagnostic that leaked onto
    standard output makes it fail, which is the property requirement 7.4 asks for.
    """
    return dict(json.loads(done.stdout))


def finding_paths(document: Mapping[str, object]) -> set[str]:
    """The repository-relative paths the findings name."""
    findings = document["findings"]
    assert isinstance(findings, list)
    return {str(finding["path"]) for finding in findings}


def rules(document: Mapping[str, object]) -> set[str]:
    """The rule names the findings name."""
    findings = document["findings"]
    assert isinstance(findings, list)
    return {str(finding["rule"]) for finding in findings}


# --- the licensed half: real adapters, a real installation ------------------------


SCITOOLS_CONFIG_DIRNAME = "SciTools"
"""Understand keeps its licence under ``$XDG_CONFIG_HOME/SciTools`` (measured on 6.5.1204).

That is the one thing an otherwise sealed environment has to be able to reach: with
``XDG_CONFIG_HOME`` pointed at a sandbox, ``und -isundlicensed`` answers ``0`` and every
licensed test would fail for a reason none of them is about. :func:`licensed_env` therefore
links *that directory alone* through into the sandbox, which keeps the Gate's own user
configuration -- ``$XDG_CONFIG_HOME/scitools-hook/config.toml`` -- isolated, so a developer's
own thresholds still cannot reach a test run.
"""

LICENSED_TIMEOUT_S = 600
"""External ceiling for a child that builds and analyses a real Understand database."""


def understand_home() -> Path | None:
    """The installation directory to hand a licensed run, or ``None`` when it cannot be told.

    ``SCITOOLS_HOME`` is preferred because it is the variable the Gate itself documents; when
    it is unset the directory is derived from the ``und`` the session probe found, whose path
    is ``<home>/bin/<platform>/und`` on this build. The derived answer is only used to set the
    same variable, so a wrong guess fails loudly at discovery rather than silently.
    """
    named = os.environ.get("SCITOOLS_HOME", "").strip()
    if named:
        return Path(named)
    from conftest import understand_probe

    und = understand_probe().und
    if und is None:
        return None
    for parent in und.parents:
        if parent.name == "bin":
            return parent.parent
    return None


def _license_directories() -> list[Path]:
    """The real ``SciTools`` configuration directories, whichever spelling this machine uses."""
    ambient = os.environ.get("XDG_CONFIG_HOME", "").strip()
    bases = [Path(ambient)] if ambient else []
    bases.append(Path.home() / ".config")
    found = [base / SCITOOLS_CONFIG_DIRNAME for base in bases]
    return [path for path in dict.fromkeys(found) if path.is_dir()]


def licensed_env(sandbox: Path, **extra: str) -> dict[str, str]:
    """An isolated environment a real, licensed Understand can still be reached from.

    The fixture seam is deliberately absent: this is the half of task 10.2 that runs against
    the real adapters, and a ``SCITOOLS_HOOK_FAKE_UNDERSTAND`` left set would make it a second
    copy of the fake-seam suite while looking like the licensed one.
    """
    home = understand_home()
    path = default_path()
    if home is not None:
        path = os.pathsep.join([path, str(home / "bin")])
    env = isolated_env(sandbox, path=path, **extra)
    config = Path(env["XDG_CONFIG_HOME"])
    config.mkdir(parents=True, exist_ok=True)
    for source in _license_directories():
        link = config / source.name
        if not link.exists():
            link.symlink_to(source, target_is_directory=True)
    if home is not None:
        env["SCITOOLS_HOME"] = str(home)
    assert FAKE_VAR not in env, "the licensed half must not run behind the fixture seam"
    return env


def license_problem(env: Mapping[str, str]) -> str:
    """Why a licensed run cannot happen *in this environment*, or ``""`` when it can.

    Asked with the environment the tests will really use, not the ambient one: the session
    probe in ``tests/conftest.py`` answers for the developer's own environment, and an
    isolated environment that cannot reach the licence is a different question with a
    different answer. Measured: with ``XDG_CONFIG_HOME`` pointed at an empty sandbox,
    ``und -isundlicensed`` printed ``0`` on a machine whose ambient probe printed ``1``.
    """
    from conftest import understand_probe

    probe = understand_probe()
    if not probe.usable or probe.und is None:
        return probe.reason or "no licensed SciTools Understand was found"
    try:
        answer = subprocess.run(
            [str(probe.und), "-isundlicensed"],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=LICENSED_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as broken:  # pragma: no cover - machine-specific
        return f"{probe.und} could not be run in the isolated environment: {broken!r}"
    if answer.stdout.strip() == "1":
        return ""
    return (
        f"{probe.und} -isundlicensed printed {answer.stdout.strip()!r} in the isolated "
        f"environment: its licence is not reachable with XDG_CONFIG_HOME={env['XDG_CONFIG_HOME']}"
    )
