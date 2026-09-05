"""Stand-in Understand installations for the ``doctor`` tests, built from shell scripts.

The installations the doctor tests diagnose are directories of shell scripts laid out like
``<SCITOOLS_HOME>/bin/<platform>/``: an ``und`` that answers the commands ``doctor`` runs and
refuses the rest, a ``upython`` that answers the worker ping one of four ways, and optionally
an importable ``understand`` module for the in-process probe. That is deliberate: it exercises
:class:`scitools_hook.understand.locator.RealProbes` running real subprocesses -- the wiring
between the locator's injected probes and the processes that answer them -- on a machine with
no Understand at all. Shared by ``test_doctor.py`` and ``test_doctor_licence.py``.
"""

from __future__ import annotations

import contextlib
import signal
import stat
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from conftest import FakeCommandLog

from scitools_hook.models.cache import CachePaths
from scitools_hook.models.understand import Availability, Feature, FeatureReport
from scitools_hook.runner.context import ContextOptions, cache_dir
from scitools_hook.runner.doctor import DoctorReport
from scitools_hook.understand.fake import FAKE_VAR
from scitools_hook.understand.features import FEATURES_FILE
from scitools_hook.understand.locator import platform_bin

UND_SCRIPT = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    -gitcommit)
      if [ -n "{commit_create}" ]; then exit 0; fi
      echo "Error: Unrecognized arguments." >&2; exit 1 ;;
  esac
done
for arg in "$@"; do
  case "$arg" in
    create|add) exit 0 ;;
    arch)
      if [ -n "{arch_listing}" ]; then printf '%s\n' "{arch_listing}"; exit 0; fi
      echo "Error: No valid command found." >&2; exit 1 ;;
    analyze)
      prev=""
      for a in "$@"; do
        if [ "$prev" = "-sarif" ] && [ -n "{writes_sarif}" ]; then
          printf '{{"version": "2.1.0", "runs": []}}' > "$a"
        fi
        prev="$a"
      done
      [ -n "{analysis_text}" ] && echo "{analysis_text}" >&2
      [ -n "{accuracy_line}" ] && echo "{accuracy_line}"
      exit {analysis_rc} ;;
  esac
done
case "$1" in
  version) echo "{version}" ;;
  -isundlicensed) echo "{licensed}" ;;
  *) echo "Error: No valid command found." >&2; exit 1 ;;
esac
"""
"""A stand-in ``und`` answering the commands ``doctor`` runs, and refusing the rest.

The subcommand of a database command comes after ``-db <path>`` (and ``-quiet``), so those
are found by walking the arguments; the three bare probes still sit in ``$1``. ``analyze``
answers with whatever the test planned, which is how "No Server Response" is staged.
"""

UPYTHON_SCRIPT = """#!/bin/sh
echo '{{"version": "{version}", "python": "3.12.0"}}'
"""
"""A stand-in ``upython`` answering the worker ping the way a healthy installation does."""

BROKEN_UPYTHON = """#!/bin/sh
echo '{{"version": "{version}", "python": "3.12.0"}}'
echo "the interpreter died after answering" >&2
exit 1
"""
"""A bundled interpreter that prints a perfectly good answer and then dies.

Deliberately this shape rather than a silent failure: it is the one that would slip through
a probe reading only standard output. The worker goes to some length (``worker._leave``) to
exit 0 after ``Ent.draw`` leaves a subinterpreter behind, so a non-zero status means the
interpreter is broken whatever it managed to print first.
"""

REFUSING_UPYTHON = """#!/bin/sh
echo '{{"error": {{"type": "NoApiLicense", "message": "no license"}}, "version": "{version}"}}'
"""
"""A bundled interpreter that runs, exits 0, and answers with a refusal.

The worker's contract is that a foreseeable failure is *data*: an ``{{"error": ...}}``
envelope on standard output with exit status 0. A probe that read the document for a version
and ignored the envelope would certify a mode that cannot open a database, so the envelope --
not the absence of a version -- is what decides. The version is present here on purpose: it
is the input that tells the two rules apart.
"""

STUB_API = '''"""A stand-in for Understand's own ``understand`` module, importable by any Python."""


class UnderstandError(Exception):
    """The exception type the worker catches by attribute, so the stub must define it."""


def version() -> str:
    """The API version the worker's ``ping`` operation reports."""
    return "{version}"
'''
"""Put in ``bin/<platform>/Python`` so the in-process probe has something real to import."""

ENV_REPORTING_API = '''"""A stand-in that answers with the environment the probe gave its child."""

import os

_WATCHED = ("PYTHONPATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "PATH")


class UnderstandError(Exception):
    """The exception type the worker catches by attribute."""


def version() -> str:
    """The variables the probe set, then the PYTHONPATH it built, separated by a bar."""
    named = ",".join(name for name in _WATCHED if os.environ.get(name))
    return named + "|" + os.environ.get("PYTHONPATH", "")
'''
"""Reports its own environment, so the probe's promise about it can be asserted on."""

API_STUBS = {"stub": STUB_API, "env": ENV_REPORTING_API}
"""Which stand-in module ``install(api=...)`` writes into the API directory."""

DEEP_UPYTHON = """#!/bin/sh
exec /bin/cat '{deep}'
"""
"""A bundled interpreter whose answer is a document no parser will accept.

``json.loads`` answers this with ``RecursionError``, not ``ValueError`` -- the parser-depth
fault class -- so a probe guarding only ``ValueError`` would take the Gate down while merely
asking whether a mode works.

``/bin/cat`` by absolute path, reading a file written at install time: the probe runs the stub
with the isolated environment, whose ``PATH`` is empty, so anything resolved through ``PATH``
exits 127 and the probe answers "no" on the *status* without ever parsing the output. An
earlier version of this stub invoked ``python3`` and did exactly that -- the test passed while
proving nothing, which the surviving mutant is what exposed.
"""

API_UNLICENSED_UPYTHON = """#!/bin/sh
case "$2" in
  ping) echo '{{"version": "{version}", "python": "3.12.0"}}' ;;
  *) echo '{{"error": {{"type": "NoApiLicense", "message": "NoApiLicense: no API licence"}}}}' ;;
esac
"""
"""A bundled interpreter licensed for everything but the API: the 2026-09-05 morning.

``understand.version()`` answers, so the ping probe says ``ok``; ``understand.open`` refuses,
so the first operation on a database answers the ``NoApiLicense`` envelope. The worker is run
as ``upython worker.py <op>``, hence ``$2``.
"""

UNDERSTAND_8_UPYTHON = """#!/bin/sh
case "$2" in
  catalogue)
    found='{{"targets": ["Functions"], "languages": ["Python"]}}'
    echo '{{"metrics": {{}}, "lookup": {{"CountGlobalsModified": '"$found"'}}}}' ;;
  *) echo '{{"version": "{version}", "python": "3.12.0"}}' ;;
esac
"""
"""A bundled interpreter whose catalogue knows a plugin metric, as Build 1262's does.

Answers the ping document for every other operation, so the analysis probe's ``archs`` call
still succeeds and only the feature probe sees a difference.
"""

UPYTHON_SCRIPTS = {
    "ok": UPYTHON_SCRIPT,
    "broken": BROKEN_UPYTHON,
    "refusing": REFUSING_UPYTHON,
    "deep": DEEP_UPYTHON,
    "api_unlicensed": API_UNLICENSED_UPYTHON,
    "understand8": UNDERSTAND_8_UPYTHON,
}
"""The three answers a bundled interpreter can give, selected by ``install(mode=...)``."""

API_VERSION = "6.5.1204"
"""What the stub ``upython`` reports; the version the API returns, not the ``und`` build."""

IN_PROCESS_VERSION = "9.9.9-stub"
"""What the importable stub module reports, so the two probes cannot be confused."""


@contextlib.contextmanager
def time_limit(seconds: int) -> Iterator[None]:
    """Fail rather than hang: a blocking read must not take the whole suite down with it."""

    def ring(signum: int, frame: object) -> None:
        raise AssertionError(f"the call blocked for more than {seconds}s")

    previous = signal.signal(signal.SIGALRM, ring)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def executable(path: Path, body: str) -> Path:
    """Write ``body`` to ``path`` and make it runnable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return path


@dataclass(frozen=True)
class UndAnswers:
    """What the stand-in ``und`` says: whether it is licensed, and how an analysis ends."""

    licensed: bool = True
    analysis_rc: int = 0
    analysis_text: str = ""
    arch_listing: str = ""
    """``und arch -list``'s answer; empty makes the stub refuse the command, as 6.5 does."""

    accuracy_line: str = ""
    """The line ``-accuracy`` adds after the summary; empty is a build that knows no such switch."""

    writes_sarif: bool = False
    """Whether ``-sarif <file>`` actually writes one. A build that ignored the switch would
    otherwise read as offering the feature while writing nothing."""

    commit_create: bool = False
    """Whether ``create -gitcommit`` is accepted; off makes it an unrecognised argument."""


def install(
    root: Path,
    upython: bool = True,
    api: str = "",
    mode: str = "ok",
    und: UndAnswers | None = None,
) -> Path:
    """A directory laid out like an Understand installation, answering like a healthy one.

    ``api`` writes an importable stand-in module into the directory the in-process mode adds
    to ``sys.path``, so that probe has something to succeed at; without it the probe answers
    the ``ApiUnavailable`` envelope a real interpreter without Understand answers. ``und``
    scripts the licence and analysis answers; the default is a licensed installation whose
    analyses succeed.
    """
    answers = und or UndAnswers()
    bin_dir = root / "bin" / platform_bin(sys.platform)
    executable(
        bin_dir / "und",
        UND_SCRIPT.format(
            version="(Build 1204)",
            licensed="1" if answers.licensed else "0",
            analysis_rc=answers.analysis_rc,
            analysis_text=answers.analysis_text,
            arch_listing=answers.arch_listing,
            accuracy_line=answers.accuracy_line,
            writes_sarif="yes" if answers.writes_sarif else "",
            commit_create="yes" if answers.commit_create else "",
        ),
    )
    if upython:
        deep = bin_dir / "deep.json"
        deep.parent.mkdir(parents=True, exist_ok=True)
        deep.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
        script = UPYTHON_SCRIPTS[mode].format(version=API_VERSION, deep=deep)
        executable(bin_dir / "upython", script)
    if api:
        (bin_dir / "Python").mkdir(parents=True, exist_ok=True)
        (bin_dir / "Python" / "understand.py").write_text(
            API_STUBS[api].format(version=IN_PROCESS_VERSION), encoding="utf-8"
        )
    return root


def isolated_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """An environment that cannot reach this machine's real Understand or user config."""
    return {
        "HOME": str(tmp_path / "home"),
        "PATH": "",
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        **extra,
    }


def options(cwd: Path, env: Mapping[str, str], log: FakeCommandLog) -> ContextOptions:
    """The inputs the ``doctor`` command hands the pipeline."""
    return ContextOptions(cwd=cwd, env=dict(env), log=log)


def seam(tmp_path: Path, **extra: str) -> tuple[Path, dict[str, str]]:
    """A fixture directory and an environment pointing the test seam at it."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    return fixtures, isolated_env(tmp_path, **{FAKE_VAR: str(fixtures), **extra})


def seed_features(repo: Path, env: Mapping[str, str], build: str, **states: str) -> Path:
    """Write a feature record beside a repository's databases, as ``doctor`` would.

    Every feature reads ``available`` unless a keyword names it otherwise, so a test that
    needs one feature missing says only that. Without this, any test enabling a key from the
    understand-8-features specification would fail closed on a missing record -- which is the
    correct behaviour and a useless thing to re-prove in every test.
    """
    paths = CachePaths.for_repo(repo.resolve() / ".git", "cache", cache_dir(env))
    report = FeatureReport(
        build=build,
        features={
            feature: Availability(state=states.get(feature.value, "available"), detail="seeded")
            for feature in Feature
        },
    )
    paths.root.mkdir(parents=True, exist_ok=True)
    target = paths.root / FEATURES_FILE
    target.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return target


def problem_about(report: DoctorReport, needle: str) -> str:
    """The one problem mentioning ``needle``; fails the test when there is none."""
    found = [problem for problem in report.problems if needle in problem]
    assert found, f"no problem mentions {needle!r}; problems were {report.problems}"
    return found[0]
