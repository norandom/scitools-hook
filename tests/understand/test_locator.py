"""Installation discovery and probe-driven verification (task 6.4, requirements 1.1-1.3).

Two halves, deliberately separated so neither needs the other's machinery:

* **Discovery** walks the precedence list of requirement 1.1 — command line, environment,
  configuration, ``und`` on the search path, per-OS well-known directories — and answers
  with the first location whose *layout* looks like an Understand installation. It touches
  the filesystem, so every test builds its own fake installations under ``tmp_path`` and
  passes an explicit environment mapping; nothing reads the developer's real machine.
* **Verification** decides how the Python API will be reached. It runs no subprocess and
  stats no file: the three probes are injected (requirement 1.2), so these tests are stubs
  answering yes or no, and they can assert *which* probes ran — the ordering is the whole
  point. ``auto`` must try ``upython`` first: the in-process import itself works, but
``Ent.draw`` aborts in-process (``Perl_xs_handshake``, rc 127) and succeeds under
``upython``, and the shipped ``graphs`` operation needs draw.

The ``contract``-marked test at the end resolves the real installation from
``SCITOOLS_HOME`` with probes that really run ``und`` and ``upython``; the gate in
``tests/conftest.py`` skips it when no licensed Understand is installed.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pytest

from scitools_hook.config.models import ApiMode
from scitools_hook.errors import UnderstandNotFoundError
from scitools_hook.exit_codes import ExitCode
from scitools_hook.models.understand import UnderstandEnv
from scitools_hook.understand.locator import (
    WORKER_PATH,
    InstallLayout,
    Locator,
    candidates,
    discover,
    layout,
    platform_bin,
    verify,
    well_known_homes,
)

LINUX: Final = "linux"
MACOS: Final = "darwin"
WINDOWS: Final = "win32"
SUBPROCESS_TIMEOUT_S: Final = 300


# --- fake installations and stub probes -----------------------------------------


def _make_executable(path: Path) -> Path:
    """Write a file and give it the execute bit, as an installed tool would have."""
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def make_install(
    home: Path,
    plat: str = "linux64",
    upython: bool = True,
    api: bool = True,
    executable: bool = True,
) -> Path:
    """Build an Understand installation layout under ``home`` and return ``home``."""
    suffix = ".exe" if plat == "pc-win64" else ""
    bin_dir = home / "bin" / plat
    bin_dir.mkdir(parents=True, exist_ok=True)
    und = bin_dir / f"und{suffix}"
    if executable:
        _make_executable(und)
    else:
        und.write_text("a file that is not executable\n", encoding="utf-8")
    if upython:
        _make_executable(bin_dir / f"upython{suffix}")
    if api:
        (bin_dir / "Python").mkdir(exist_ok=True)
    return home


@dataclass
class StubProbes:
    """The three injected probes, recording every call and answering as configured.

    ``version`` is what ``und`` reports; ``inprocess`` and ``upython`` are the API versions
    the two API probes return, or ``None`` for a probe that failed. ``errors`` raises the
    given exception from the named probe instead. It is typed ``Exception`` rather than
    ``OSError`` on purpose: production raises ``OSError`` for a probe that cannot start, but
    the guard under test catches *only* ``OSError``, so a test proving the guard is narrow
    must be able to inject something else -- a ``subprocess.TimeoutExpired``, which is not an
    ``OSError``. Narrowing this annotation would make that test unwritable.
    """

    version: str = "6.5.1204"
    inprocess: str | None = None
    upython: str | None = None
    errors: dict[str, Exception] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def und_version(self, und: Path) -> str:
        self.calls.append(f"und_version|{und}")
        self._maybe_raise("und_version")
        return self.version

    def inprocess_import(self, api_dir: Path, bin_dir: Path) -> str | None:
        self.calls.append(f"inprocess_import|{api_dir}|{bin_dir}")
        self._maybe_raise("inprocess_import")
        return self.inprocess

    def upython_ping(self, upython: Path, worker: Path) -> str | None:
        self.calls.append(f"upython_ping|{upython}|{worker}")
        self._maybe_raise("upython_ping")
        return self.upython

    def _maybe_raise(self, name: str) -> None:
        error = self.errors.get(name)
        if error is not None:
            raise error

    @property
    def probed(self) -> list[str]:
        """The names of the probes that ran, in order."""
        return [call.split("|", 1)[0] for call in self.calls]


def fake_env(home: Path, upython: bool = True) -> UnderstandEnv:
    """An unverified environment pointing into ``home``; the paths need not exist."""
    bin_dir = home / "bin" / "linux64"
    return UnderstandEnv(
        home=home,
        und=bin_dir / "und",
        upython=(bin_dir / "upython") if upython else None,
        python_api_dir=bin_dir / "Python",
        version="",
        source="cli",
        api_mode="upython" if upython else "inprocess",
    )


# --- the platform table ---------------------------------------------------------


def test_platform_bin_names_the_subdirectory_of_each_supported_os() -> None:
    """The bin subdirectory is what every path in an installation hangs off."""
    assert platform_bin(LINUX) == "linux64"
    assert platform_bin(MACOS) == "macosx"
    assert platform_bin(WINDOWS) == "pc-win64"


def test_platform_bin_falls_back_to_the_linux_layout_on_an_unknown_platform() -> None:
    """An OS Understand does not ship for still gets a candidate list, not a crash."""
    assert platform_bin("freebsd14") == "linux64"


def test_well_known_directories_are_the_documented_ones_per_os(tmp_path: Path) -> None:
    """Requirement 1.1's fixed list, and only it, per operating system."""
    env = {"HOME": str(tmp_path)}
    assert well_known_homes(env, LINUX) == [
        tmp_path / "scitools",
        Path("/opt/scitools"),
        Path("/usr/local/scitools"),
    ]
    assert well_known_homes(env, MACOS) == [Path("/Applications/Understand.app/Contents/MacOS")]
    assert well_known_homes(env, WINDOWS) == [Path("C:\\Program Files\\SciTools")]


def test_an_unknown_platform_has_no_well_known_directories(tmp_path: Path) -> None:
    """Guessing a location for an OS Understand does not ship for would be a lie."""
    assert well_known_homes({"HOME": str(tmp_path)}, "freebsd14") == []


# --- what counts as an installation ---------------------------------------------


def test_layout_reports_und_upython_and_the_api_directory(tmp_path: Path) -> None:
    """The four paths every later component needs, all under ``bin/<platform>``."""
    home = make_install(tmp_path / "scitools")
    found = layout(home, LINUX)
    assert found == InstallLayout(
        home=home,
        und=home / "bin" / "linux64" / "und",
        upython=home / "bin" / "linux64" / "upython",
        python_api_dir=home / "bin" / "linux64" / "Python",
    )


def test_layout_uses_the_windows_executable_names(tmp_path: Path) -> None:
    """``und.exe``/``upython.exe`` under ``bin/pc-win64`` is the Windows layout."""
    home = make_install(tmp_path / "SciTools", plat="pc-win64")
    found = layout(home, WINDOWS)
    assert found is not None
    assert found.und.name == "und.exe"
    assert found.upython is not None and found.upython.name == "upython.exe"


def test_a_directory_without_und_is_not_an_installation(tmp_path: Path) -> None:
    """A directory that merely exists proves nothing."""
    (tmp_path / "empty" / "bin" / "linux64").mkdir(parents=True)
    assert layout(tmp_path / "empty", LINUX) is None
    assert layout(tmp_path / "missing", LINUX) is None


def test_a_non_executable_und_is_not_an_installation(tmp_path: Path) -> None:
    """A file named ``und`` that cannot be run is not the tool."""
    home = make_install(tmp_path / "scitools", executable=False)
    assert layout(home, LINUX) is None


def test_an_installation_without_upython_still_resolves(tmp_path: Path) -> None:
    """``upython`` is optional: in-process mode may still work (requirement 1.2)."""
    home = make_install(tmp_path / "scitools", upython=False)
    found = layout(home, LINUX)
    assert found is not None
    assert found.upython is None


def test_a_missing_api_directory_does_not_reject_the_installation(tmp_path: Path) -> None:
    """The probes, not the directory listing, decide whether the API is usable."""
    home = make_install(tmp_path / "scitools", api=False)
    found = layout(home, LINUX)
    assert found is not None
    assert found.python_api_dir == home / "bin" / "linux64" / "Python"


# --- the candidate list ---------------------------------------------------------


def _five_homes(tmp_path: Path) -> dict[str, Path]:
    """One installation per precedence step, plus the environment that finds them all."""
    homes = {name: make_install(tmp_path / name) for name in ("cli", "env", "config", "path")}
    homes["wellknown"] = make_install(tmp_path / "userhome" / "scitools")
    return homes


def _full_env(homes: dict[str, Path], tmp_path: Path) -> dict[str, str]:
    """An environment naming the ``env`` home and putting the ``path`` home on PATH."""
    return {
        "SCITOOLS_HOME": str(homes["env"]),
        "PATH": str(homes["path"] / "bin" / "linux64"),
        "HOME": str(tmp_path / "userhome"),
    }


def test_candidates_are_ordered_by_the_precedence_of_requirement_1_1(tmp_path: Path) -> None:
    """Command line, environment, configuration, PATH, then the well-known list."""
    homes = _five_homes(tmp_path)
    found = candidates(_full_env(homes, tmp_path), homes["cli"], homes["config"], LINUX)
    assert [source for source, _ in found] == [
        "cli",
        "env:SCITOOLS_HOME",
        "config",
        "path",
        f"wellknown:{tmp_path / 'userhome' / 'scitools'}",
        "wellknown:/opt/scitools",
        "wellknown:/usr/local/scitools",
    ]
    assert [path for _, path in found][:4] == [
        homes["cli"],
        homes["env"],
        homes["config"],
        homes["path"],
    ]


def test_absent_sources_drop_out_of_the_candidate_list(tmp_path: Path) -> None:
    """No command-line option, no variable, no configuration: only PATH and well-known."""
    found = candidates({"HOME": str(tmp_path)}, None, None, LINUX)
    assert [source for source, _ in found] == [
        f"wellknown:{tmp_path / 'scitools'}",
        "wellknown:/opt/scitools",
        "wellknown:/usr/local/scitools",
    ]


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_environment_variable_is_not_a_candidate(tmp_path: Path, value: str) -> None:
    """``SCITOOLS_HOME=`` means unset, not "the current directory"."""
    found = candidates({"SCITOOLS_HOME": value, "HOME": str(tmp_path)}, None, None, LINUX)
    assert all(source != "env:SCITOOLS_HOME" for source, _ in found)


def test_a_tilde_in_a_candidate_is_expanded_against_the_given_environment(
    tmp_path: Path,
) -> None:
    """The environment mapping is the authority on ``~``, never the developer's own HOME."""
    env = {"SCITOOLS_HOME": "~/elsewhere", "HOME": str(tmp_path)}
    found = dict((source, path) for source, path in candidates(env, None, None, LINUX))
    assert found["env:SCITOOLS_HOME"] == tmp_path / "elsewhere"


def test_candidates_follow_the_well_known_list_of_the_given_platform(tmp_path: Path) -> None:
    """The platform reaches the well-known list; it is not read from the running one."""
    found = candidates({"HOME": str(tmp_path)}, None, None, MACOS)
    assert [source for source, _ in found] == [
        "wellknown:/Applications/Understand.app/Contents/MacOS"
    ]


def test_the_path_candidate_is_the_installation_root_holding_und(tmp_path: Path) -> None:
    """``und`` on PATH names ``<home>/bin/<platform>/und``; the candidate is ``<home>``."""
    home = make_install(tmp_path / "scitools")
    env = {"PATH": str(home / "bin" / "linux64"), "HOME": str(tmp_path / "nowhere")}
    found = dict(candidates(env, None, None, LINUX))
    assert found["path"] == home


def test_the_path_candidate_uses_the_executable_name_of_the_given_platform(
    tmp_path: Path,
) -> None:
    """On Windows the search is for ``und.exe``; the platform decides that too."""
    home = make_install(tmp_path / "SciTools", plat="pc-win64")
    env = {"PATH": str(home / "bin" / "pc-win64"), "HOME": str(tmp_path / "nowhere")}
    found = dict(candidates(env, None, None, WINDOWS))
    assert found["path"] == home


def test_the_path_candidate_follows_a_symlink_into_the_installation(tmp_path: Path) -> None:
    """A ``/usr/local/bin/und`` symlink is the usual way ``und`` reaches PATH."""
    home = make_install(tmp_path / "scitools")
    link_dir = tmp_path / "usr" / "bin"
    link_dir.mkdir(parents=True)
    (link_dir / "und").symlink_to(home / "bin" / "linux64" / "und")
    env = {"PATH": str(link_dir), "HOME": str(tmp_path / "nowhere")}
    found = dict(candidates(env, None, None, LINUX))
    assert found["path"] == home


def test_the_path_candidate_falls_back_to_the_parents_parent(tmp_path: Path) -> None:
    """An ``und`` in no recognizable layout is still reported as a location tried."""
    loose = tmp_path / "opt" / "bin"
    loose.mkdir(parents=True)
    _make_executable(loose / "und")
    env = {"PATH": str(loose), "HOME": str(tmp_path / "nowhere")}
    found = dict(candidates(env, None, None, LINUX))
    assert found["path"] == tmp_path / "opt"


def test_no_path_in_the_environment_yields_no_path_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The environment mapping is the only search path; the process's own is never read."""
    home = make_install(tmp_path / "scitools")
    monkeypatch.setenv("PATH", str(home / "bin" / "linux64"))
    found = candidates({"HOME": str(tmp_path / "nowhere")}, None, None, LINUX)
    assert all(source != "path" for source, _ in found)


def test_a_repeated_location_keeps_the_highest_precedence_source(tmp_path: Path) -> None:
    """The same directory named twice is one location, attributed to the stronger source."""
    home = make_install(tmp_path / "scitools")
    env = {"SCITOOLS_HOME": str(home), "HOME": str(tmp_path / "nowhere")}
    found = candidates(env, home, home, LINUX)
    assert [source for source, path in found if path == home] == ["cli"]


def test_a_well_known_location_named_by_a_stronger_source_is_listed_once(
    tmp_path: Path,
) -> None:
    """Installing into ``~/scitools`` and pointing every source at it is one location."""
    home = make_install(tmp_path / "scitools")
    env = {
        "SCITOOLS_HOME": str(home),
        "PATH": str(home / "bin" / "linux64"),
        "HOME": str(tmp_path),
    }
    found = candidates(env, home, home, LINUX)
    assert [source for source, path in found if path == home] == ["cli"]
    assert len(found) == 3  # the one location, plus /opt/scitools and /usr/local/scitools


# --- discovery ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dropped", "winner", "expected_source"),
    [
        ((), "cli", "cli"),
        (("cli",), "env", "env:SCITOOLS_HOME"),
        (("cli", "env"), "config", "config"),
        (("cli", "env", "config"), "path", "path"),
        (("cli", "env", "config", "path"), "wellknown", "wellknown:"),
    ],
)
def test_discovery_walks_the_precedence_list_in_order(
    tmp_path: Path, dropped: tuple[str, ...], winner: str, expected_source: str
) -> None:
    """Each step wins once every stronger step is unavailable (requirement 1.1)."""
    homes = _five_homes(tmp_path)
    env = _full_env(homes, tmp_path)
    cli = None if "cli" in dropped else homes["cli"]
    config = None if "config" in dropped else homes["config"]
    for name in dropped:
        env.pop({"env": "SCITOOLS_HOME", "path": "PATH"}.get(name, name), None)
    found = discover(env, cli, config, LINUX)
    assert found.home == homes[winner]
    assert found.source.startswith(expected_source)


def test_discovery_records_the_source_and_the_paths_of_the_winner(tmp_path: Path) -> None:
    """The resolved environment says where it came from and where everything is."""
    home = make_install(tmp_path / "scitools")
    found = discover({"HOME": str(tmp_path / "nowhere")}, home, None, LINUX)
    assert found.source == "cli"
    assert found.home == home
    assert found.und == home / "bin" / "linux64" / "und"
    assert found.upython == home / "bin" / "linux64" / "upython"
    assert found.python_api_dir == home / "bin" / "linux64" / "Python"


def test_discovery_leaves_the_environment_unverified(tmp_path: Path) -> None:
    """``version`` stays empty until ``verify`` asks ``und``; the mode is only a guess."""
    home = make_install(tmp_path / "scitools")
    found = discover({"HOME": str(tmp_path / "nowhere")}, home, None, LINUX)
    assert found.version == ""


def test_discovery_looks_only_under_the_bin_directory_of_the_given_platform(
    tmp_path: Path,
) -> None:
    """A Linux installation is not a macOS one: the platform decides the layout."""
    home = make_install(tmp_path / "scitools")
    env = {"SCITOOLS_HOME": str(home), "HOME": str(tmp_path / "nowhere")}
    with pytest.raises(UnderstandNotFoundError):
        discover(env, None, None, MACOS)
    make_install(home, plat="macosx")
    found = discover(env, None, None, MACOS)
    assert found.und == home / "bin" / "macosx" / "und"


def test_a_candidate_that_is_not_an_installation_is_skipped(tmp_path: Path) -> None:
    """An existing directory with no ``und`` never shadows a real installation."""
    empty = tmp_path / "empty"
    empty.mkdir()
    home = make_install(tmp_path / "scitools")
    env = {"SCITOOLS_HOME": str(home), "HOME": str(tmp_path / "nowhere")}
    found = discover(env, empty, None, LINUX)
    assert found.source == "env:SCITOOLS_HOME"
    assert found.home == home


def test_an_installation_without_upython_discovers_with_none(tmp_path: Path) -> None:
    """``upython`` missing is data for ``verify``, not a discovery failure."""
    home = make_install(tmp_path / "scitools", upython=False)
    found = discover({"HOME": str(tmp_path / "nowhere")}, home, None, LINUX)
    assert found.upython is None


def test_not_found_lists_every_location_that_was_tried(tmp_path: Path) -> None:
    """Requirement 1.3: every candidate, with the source that produced it."""
    homes = {name: tmp_path / name for name in ("cli", "env", "config")}
    env = {
        "SCITOOLS_HOME": str(homes["env"]),
        "PATH": str(tmp_path / "no-und"),
        "HOME": str(tmp_path / "userhome"),
    }
    with pytest.raises(UnderstandNotFoundError) as raised:
        discover(env, homes["cli"], homes["config"], LINUX)
    tried = raised.value.tried
    assert [line.split(": ", 1)[0] for line in tried] == [
        "cli",
        "env:SCITOOLS_HOME",
        "config",
        f"wellknown:{tmp_path / 'userhome' / 'scitools'}",
        "wellknown:/opt/scitools",
        "wellknown:/usr/local/scitools",
    ]
    assert str(homes["cli"]) in tried[0]
    assert str(homes["config"]) in tried[2]


def test_not_found_names_the_option_and_the_variable_to_set(tmp_path: Path) -> None:
    """Requirement 1.3: the message must say what to do about it."""
    with pytest.raises(UnderstandNotFoundError) as raised:
        discover({"HOME": str(tmp_path)}, None, None, LINUX)
    told = f"{raised.value.message} {raised.value.hint}"
    assert "--scitools-home" in told
    assert "SCITOOLS_HOME" in told
    assert "understand.home" in told
    assert raised.value.exit_code is ExitCode.UNDERSTAND_NOT_FOUND


# --- verification ---------------------------------------------------------------


def test_verify_reports_the_version_und_gave_and_keeps_the_source(tmp_path: Path) -> None:
    """``und_version`` is the only source of the version (requirement 1.2)."""
    probes = StubProbes(version="6.5.1204", upython="6.5.1204")
    verified = verify(fake_env(tmp_path), "auto", probes)
    assert verified.version == "6.5.1204"
    assert verified.source == "cli"
    assert verified.home == tmp_path
    assert probes.calls[0] == f"und_version|{tmp_path / 'bin' / 'linux64' / 'und'}"


def test_auto_prefers_upython_when_it_answers(tmp_path: Path) -> None:
    """The default mode: the bundled interpreter is the one proven to work."""
    probes = StubProbes(upython="6.5.1204", inprocess="6.5.1204")
    verified = verify(fake_env(tmp_path), "auto", probes)
    assert verified.api_mode == "upython"
    assert probes.probed == ["und_version", "upython_ping"]


def test_auto_passes_the_worker_script_to_the_upython_probe(tmp_path: Path) -> None:
    """The ping runs this project's worker, so the probe must be given its path."""
    probes = StubProbes(upython="6.5.1204")
    verify(fake_env(tmp_path), "auto", probes)
    assert probes.calls[1].endswith(f"|{WORKER_PATH}")
    assert WORKER_PATH.name == "worker.py"


def test_auto_falls_back_to_in_process_when_there_is_no_upython(tmp_path: Path) -> None:
    """An installation without the bundled interpreter can still work in-process."""
    probes = StubProbes(inprocess="6.5.1204")
    verified = verify(fake_env(tmp_path, upython=False), "auto", probes)
    assert verified.api_mode == "inprocess"
    assert probes.probed == ["und_version", "inprocess_import"]


def test_auto_falls_back_to_in_process_when_the_ping_fails(tmp_path: Path) -> None:
    """A present but unusable ``upython`` is not the end of the road."""
    probes = StubProbes(upython=None, inprocess="6.5.1204")
    verified = verify(fake_env(tmp_path), "auto", probes)
    assert verified.api_mode == "inprocess"
    assert probes.probed == ["und_version", "upython_ping", "inprocess_import"]


def test_the_in_process_probe_receives_the_api_directory_and_the_bin_directory(
    tmp_path: Path,
) -> None:
    """The import needs both: the module lives in one, its libraries in the other."""
    probes = StubProbes(inprocess="6.5.1204")
    env = fake_env(tmp_path, upython=False)
    verify(env, "auto", probes)
    assert probes.calls[1] == f"inprocess_import|{env.python_api_dir}|{env.python_api_dir.parent}"


def test_auto_fails_with_both_probe_reasons(tmp_path: Path) -> None:
    """Requirement 1.3: neither mode works, so the message carries both failures."""
    probes = StubProbes(upython=None, inprocess=None)
    env = fake_env(tmp_path)
    with pytest.raises(UnderstandNotFoundError) as raised:
        verify(env, "auto", probes)
    tried = raised.value.tried
    assert len(tried) == 2
    assert str(env.upython) in tried[0]
    assert str(env.python_api_dir) in tried[1]
    assert raised.value.exit_code is ExitCode.UNDERSTAND_NOT_FOUND
    assert str(env.home) in raised.value.message


def test_auto_reports_a_missing_upython_as_one_of_the_two_reasons(tmp_path: Path) -> None:
    """A missing ``upython`` is a reason an operator can act on."""
    probes = StubProbes(upython=None, inprocess=None)
    env = fake_env(tmp_path, upython=False)
    with pytest.raises(UnderstandNotFoundError) as raised:
        verify(env, "auto", probes)
    assert str(env.python_api_dir.parent) in raised.value.tried[0]
    assert "upython" in raised.value.tried[0]
    assert probes.probed == ["und_version", "inprocess_import"]


def test_forced_in_process_never_asks_upython(tmp_path: Path) -> None:
    """``api_mode = "inprocess"`` is an operator decision, not a preference."""
    probes = StubProbes(upython="6.5.1204", inprocess="6.5.1204")
    verified = verify(fake_env(tmp_path), "inprocess", probes)
    assert verified.api_mode == "inprocess"
    assert probes.probed == ["und_version", "inprocess_import"]


def test_forced_in_process_fails_with_only_the_in_process_reason(tmp_path: Path) -> None:
    """Forcing a mode means the other one's reason is not part of the answer."""
    probes = StubProbes(upython="6.5.1204", inprocess=None)
    env = fake_env(tmp_path)
    with pytest.raises(UnderstandNotFoundError) as raised:
        verify(env, "inprocess", probes)
    assert len(raised.value.tried) == 1
    assert str(env.python_api_dir) in raised.value.tried[0]
    assert probes.probed == ["und_version", "inprocess_import"]


def test_forced_upython_never_asks_the_in_process_probe(tmp_path: Path) -> None:
    """In-process drawing can abort the process; a forced mode must not risk it behind a back."""
    probes = StubProbes(upython="6.5.1204", inprocess="6.5.1204")
    verified = verify(fake_env(tmp_path), "upython", probes)
    assert verified.api_mode == "upython"
    assert probes.probed == ["und_version", "upython_ping"]


def test_forced_upython_fails_when_the_ping_fails(tmp_path: Path) -> None:
    """One reason, the one the operator asked for."""
    probes = StubProbes(upython=None, inprocess="6.5.1204")
    env = fake_env(tmp_path)
    with pytest.raises(UnderstandNotFoundError) as raised:
        verify(env, "upython", probes)
    assert len(raised.value.tried) == 1
    assert str(env.upython) in raised.value.tried[0]
    assert probes.probed == ["und_version", "upython_ping"]


def test_forced_upython_fails_when_the_installation_has_none(tmp_path: Path) -> None:
    """Nothing to ping, and the in-process probe is not a substitute here."""
    probes = StubProbes(inprocess="6.5.1204")
    env = fake_env(tmp_path, upython=False)
    with pytest.raises(UnderstandNotFoundError) as raised:
        verify(env, "upython", probes)
    assert len(raised.value.tried) == 1
    assert str(env.python_api_dir.parent) in raised.value.tried[0]
    assert probes.probed == ["und_version"]


def test_verify_names_the_api_mode_setting_in_its_hint(tmp_path: Path) -> None:
    """The operator can force the other mode; the message says how."""
    probes = StubProbes()
    with pytest.raises(UnderstandNotFoundError) as raised:
        verify(fake_env(tmp_path), "auto", probes)
    assert "--api-mode" in f"{raised.value.message} {raised.value.hint}"


@pytest.mark.parametrize(
    ("failing", "forced"), [("upython_ping", "upython"), ("inprocess_import", "inprocess")]
)
def test_a_probe_that_cannot_run_is_a_failed_probe_carrying_its_error(
    tmp_path: Path, failing: str, forced: ApiMode
) -> None:
    """A probe raising ``OSError`` is a probe answering no, with the reason attached."""
    probes = StubProbes(
        upython="6.5.1204", inprocess="6.5.1204", errors={failing: OSError("no such file")}
    )
    with pytest.raises(UnderstandNotFoundError) as raised:
        verify(fake_env(tmp_path), forced, probes)
    assert any("no such file" in line for line in raised.value.tried)


def test_auto_falls_back_when_the_upython_probe_cannot_run_at_all(tmp_path: Path) -> None:
    """An unrunnable ping is a failed ping: in-process still gets its turn."""
    probes = StubProbes(inprocess="6.5.1204", errors={"upython_ping": OSError("permission")})
    verified = verify(fake_env(tmp_path), "auto", probes)
    assert verified.api_mode == "inprocess"


def test_verify_does_not_swallow_what_und_itself_reported(tmp_path: Path) -> None:
    """A failing ``und`` is the ``und`` wrapper's error to report, not a locator answer."""
    probes = StubProbes(upython="6.5.1204", errors={"und_version": OSError("und is broken")})
    with pytest.raises(OSError, match="und is broken"):
        verify(fake_env(tmp_path), "auto", probes)


def test_a_probe_failing_in_any_other_way_reaches_the_caller(tmp_path: Path) -> None:
    """Only ``OSError`` means "this mode does not work here"; anything else is a defect.

    A hung command raises ``subprocess.TimeoutExpired``, which is NOT an ``OSError``. If the
    guard were widened, a broken or hanging probe would be recorded as a probe *reason* and
    the operator would be told the mode is unavailable rather than that something failed.
    """
    probes = StubProbes(
        errors={"upython_ping": subprocess.TimeoutExpired(cmd=["upython"], timeout=5.0)}
    )

    with pytest.raises(subprocess.TimeoutExpired):
        verify(fake_env(tmp_path), "auto", probes)


def test_a_probe_raising_an_os_error_is_recorded_as_a_reason(tmp_path: Path) -> None:
    """A probe that cannot start has answered no, and the system's words are the reason."""
    probes = StubProbes(errors={"upython_ping": OSError("no such file")})

    with pytest.raises(UnderstandNotFoundError) as caught:
        verify(fake_env(tmp_path), "auto", probes)

    assert any("no such file" in reason for reason in caught.value.tried)


def test_verify_touches_no_filesystem(tmp_path: Path) -> None:
    """Nothing under this environment exists: only the probes decide (requirement 1.2)."""
    env = fake_env(tmp_path / "does-not-exist")
    verified = verify(env, "auto", StubProbes(upython="6.5.1204"))
    assert verified.api_mode == "upython"
    assert not verified.home.exists()


# --- the locator as one component -----------------------------------------------


def test_locator_resolves_and_verifies_in_one_call(tmp_path: Path) -> None:
    """What the runner uses: a location and a decided mode, or an error."""
    home = make_install(tmp_path / "scitools")
    probes = StubProbes(upython="6.5.1204")
    located = Locator(probes=probes, platform=LINUX)
    found = located.resolve(None, {"SCITOOLS_HOME": str(home), "HOME": str(tmp_path)}, None)
    assert found.source == "env:SCITOOLS_HOME"
    assert found.home == home
    assert found.version == "6.5.1204"
    assert found.api_mode == "upython"
    assert probes.calls[0] == f"und_version|{home / 'bin' / 'linux64' / 'und'}"


def test_locator_honours_the_configured_preference(tmp_path: Path) -> None:
    """``understand.api_mode`` reaches the locator as ``preferred``."""
    home = make_install(tmp_path / "scitools")
    probes = StubProbes(upython="6.5.1204", inprocess="6.5.1204")
    located = Locator(probes=probes, preferred="inprocess", platform=LINUX)
    found = located.resolve(home, {"HOME": str(tmp_path)}, None)
    assert found.api_mode == "inprocess"
    assert probes.probed == ["und_version", "inprocess_import"]


def test_locator_reports_a_missing_installation_before_probing(tmp_path: Path) -> None:
    """Nothing to probe: the failure is about locations, not about modes."""
    probes = StubProbes()
    located = Locator(probes=probes, platform=LINUX)
    with pytest.raises(UnderstandNotFoundError) as raised:
        located.resolve(None, {"HOME": str(tmp_path)}, None)
    assert probes.calls == []
    assert len(raised.value.tried) == 3


# --- contract: the real installation --------------------------------------------


@dataclass
class RealProbes:
    """The probes the runner will wire in task 6.6, in their simplest honest form."""

    calls: list[str] = field(default_factory=list)

    def und_version(self, und: Path) -> str:
        done = subprocess.run(
            [str(und), "version"], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S
        )
        self.calls.append(f"und_version|rc={done.returncode}")
        return done.stdout.strip()

    def inprocess_import(self, api_dir: Path, bin_dir: Path) -> str | None:
        """Import the API in a *child* interpreter, so any abort cannot take the Gate with it."""
        script = "import understand, json; print(json.dumps(understand.version()))"
        env = dict(os.environ, PYTHONPATH=str(api_dir))
        done = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
        self.calls.append(f"inprocess_import|rc={done.returncode}|{done.stderr.strip()[:200]}")
        return str(json.loads(done.stdout)) if done.returncode == 0 else None

    def upython_ping(self, upython: Path, worker: Path) -> str | None:
        done = subprocess.run(
            [str(upython), str(worker), "ping"],
            input="{}",
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
        self.calls.append(f"upython_ping|rc={done.returncode}")
        if done.returncode != 0:
            return None
        answer = json.loads(done.stdout)
        return None if "error" in answer else str(answer["version"])


@pytest.mark.contract
def test_the_real_installation_resolves_and_chooses_upython() -> None:
    """The real installation, resolved from ``SCITOOLS_HOME`` and verified by real probes.

    ``auto`` must settle on ``upython``: the ping answers, so the in-process probe — the one
    whose drawing can abort an interpreter — is never even reached, which is what ``probes.calls``
    pins.
    """
    home = os.environ.get("SCITOOLS_HOME")
    if not home:
        pytest.skip("SCITOOLS_HOME is not set: nothing to resolve against")
    probes = RealProbes()
    found = Locator(probes=probes).resolve(None, dict(os.environ), None)
    assert found.home == Path(home)
    assert found.source == "env:SCITOOLS_HOME"
    assert found.version != ""
    assert found.api_mode == "upython"
    assert found.upython is not None and found.upython.is_file()
    assert probes.calls == ["und_version|rc=0", "upython_ping|rc=0"]
