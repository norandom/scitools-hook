"""The API runner: two execution modes, one envelope contract, one command log (task 6.6).

``ApiRunner`` is the only place in the project that decides *where* an Understand operation
runs and *what* a refusal means, so three properties carry the whole task:

* **The two modes must be interchangeable.** The worker's ``dispatch`` is the single
  implementation both use, so the same request must produce the same document whether it ran
  in this interpreter or under ``upython``. The ``contract``-marked test at the end proves it
  on a real database; everything above it proves the plumbing around ``dispatch``.
* **``graphs`` never runs in this process.** Measured on the licensed machine (2026-08-30):
  in-process, ``Ent.draw`` dies with ``symbol lookup error: …/Perl/auto/Fcntl/Fcntl.so:
  undefined symbol: Perl_xs_handshake`` and takes the *host* down with status 127 — the Gate
  itself, not one operation. Open, iterate, ``metric()`` and ``close()`` are all fine
  in-process, so the mode stays useful; only drawing is routed out. The contract test for
  that runs an in-process runner and would kill pytest if the routing were dropped.
* **Every refusal is data, and every kind of data has one typed error.** The worker answers
  a foreseeable failure with ``{"error": {"type": …}}`` and exit status 0, so a mapping table
  — not an exception type — decides the exit code the operator sees. The envelope texts here
  are transcripts of the real worker (see ``RECORDED_ENVELOPES``), not invented shapes.

The unit tests drive a **stubbed ``upython``**: a small Python script written into
``tmp_path`` that records its argv and standard input and replays a scripted answer. That
exercises the real subprocess plumbing — argv, stdin, both output streams, the exit status
and a command that never returns — on a machine with no license.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pytest
from conftest import FakeCommandLog, SampleDatabases, Side, understand_probe
from fixtures.constants import SHELL_COMMAND_NOT_FOUND_STATUS, TIMEOUT_KILLED_STATUS

from scitools_hook.config.metric_names import SCOPE_KINDS
from scitools_hook.errors import (
    AnalysisFailedError,
    ArchitectureNotFoundError,
    ConfigError,
    GateError,
    LicenseError,
    UnderstandNotFoundError,
)
from scitools_hook.exit_codes import ExitCode
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import ProjectSnapshot
from scitools_hook.models.understand import UnderstandEnv
from scitools_hook.understand import worker
from scitools_hook.understand.api_runner import (
    IN_PROCESS,
    MISSING_RC,
    OPERATIONS,
    TIMEOUT_RC,
    UPYTHON_ONLY_OPS,
    WORKER_RC,
    ApiRunner,
    Operation,
)
from scitools_hook.understand.locator import WORKER_PATH

# --- the two recorded statuses, and the timing they are recorded with -------------


PYTHON_TRACEBACK_STATUS: Final = 1
"""CPython's exit status when an unhandled exception reaches the top of a script.

Measured: ``python3 -c "raise RuntimeError('boom')"`` exits 1. That is what makes 1 the right
number to record for a worker that raised *in this process*, where there is no child status to
report -- the in-process mode has to log what the subprocess mode would have seen.

The literal, for the reason the two statuses above give: this file compared the log against
``WORKER_RC`` imported from the module under test, and ``1 -> 0`` was measured surviving all
48 tests in this file under that spelling. Recording 0 would report a worker that blew up
mid-operation as a clean run, and the two modes would stop agreeing about what a broken worker
is -- which is the property the test making this assertion is named for.
"""

SLOW_ANSWER_SLEEP_S: Final = 0.3
"""How long a stand-in sleeps when a test needs a duration it knows independently.

Used by both modes: the stubbed ``upython`` sleeps it before answering, and
:class:`FakeDispatch` sleeps it inside the host process. Measured here: 0.31 s recorded for a
0.30 s sleep, against 0.02 s for a stub that does not sleep and 0.0004 s for an interpreter
that never started. A duration has to be pinned against a quantity known *without* asking the
module under test, because ``seconds >= 0.0`` -- what this file asserted before task 11.2 --
cannot fail: a :func:`time.monotonic` delta is non-negative by construction.
"""

SLOW_ANSWER_FLOOR_S: Final = 0.25
"""The floor asserted against :data:`SLOW_ANSWER_SLEEP_S`, leaving room for a coarse clock."""

KILLED_FLOOR_S: Final = 0.5
"""The floor for an operation killed at a one-second limit: it cannot have taken less.

Half the limit rather than the limit itself, so a slow machine's rounding cannot fail a
correct implementation; still far above the ``0.0`` a constant would record.
"""

CLOCK_READING_CEILING_S: Final = 30.0
"""The ceiling every recorded duration is held under, which fails a clock *reading*.

:func:`time.monotonic` is the machine's uptime on Linux (hundreds of thousands of seconds
when this was measured), so recording it in place of the delta clears every floor and fails
this. No stubbed operation here is allowed anywhere near the runner's 30 s test timeout.
"""


# --- the stubbed upython ---------------------------------------------------------

STUB_SOURCE: Final = '''#!/usr/bin/env python3
"""Stand-in for ``upython``: record the call and replay one scripted answer."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "plan.json")) as handle:
    plan = json.load(handle)
body = sys.stdin.read()
with open(os.path.join(HERE, "calls.jsonl"), "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "stdin": body}) + "\\n")
answer = plan.get(sys.argv[2] if len(sys.argv) > 2 else "", {})
time.sleep(answer.get("sleep", 0))
sys.stdout.write(answer.get("stdout", "{}"))
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer.get("rc", 0))
'''


@dataclass(frozen=True)
class StubUpython:
    """A fake bundled interpreter: what it answers, and what it was asked."""

    path: Path

    def script(self, op: str, **answer: object) -> None:
        """Script one operation: ``stdout``, ``stderr``, ``rc`` and ``sleep`` seconds."""
        plan_file = self.path.parent / "plan.json"
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        plan[op] = answer
        plan_file.write_text(json.dumps(plan), encoding="utf-8")

    def answers(self, op: str, document: Mapping[str, object]) -> None:
        """Script one operation to answer with ``document`` and exit 0, as the worker does."""
        self.script(op, stdout=json.dumps(document))

    @property
    def calls(self) -> list[dict[str, Any]]:
        """Every invocation: its argv (without the interpreter) and its standard input."""
        log = self.path.parent / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def stub_upython(tmp_path: Path) -> StubUpython:
    """A stubbed ``upython`` with an empty plan, ready to be scripted."""
    home = tmp_path / "scitools" / "bin" / "linux64"
    home.mkdir(parents=True)
    (home / "plan.json").write_text("{}", encoding="utf-8")
    stub = home / "upython"
    stub.write_text(STUB_SOURCE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return StubUpython(stub)


def an_env(upython: Path | None, mode: str = "upython") -> UnderstandEnv:
    """An installation as the locator would have verified it, pointing at the stub."""
    home = (
        upython.parent.parent.parent if upython is not None else Path("/opt/scitools")
    ).resolve()
    bin_dir = home / "bin" / "linux64"
    return UnderstandEnv(
        home=home,
        und=bin_dir / "und",
        upython=upython,
        python_api_dir=bin_dir / "Python",
        version="6.5.1204",
        source="cli",
        api_mode="upython" if mode == "upython" else "inprocess",
    )


def a_runner(
    stub: StubUpython | None, log: FakeCommandLog, mode: str = "upython", timeout_s: int = 30
) -> ApiRunner:
    """A runner for the stubbed installation, in the requested mode."""
    return ApiRunner(an_env(None if stub is None else stub.path, mode), log, timeout_s=timeout_s)


# --- the operations this runner knows --------------------------------------------


def test_the_operation_names_are_exactly_the_ones_the_worker_implements() -> None:
    # A name the runner accepts and the worker does not would be an UnknownOperation
    # envelope at run time, on a machine that has already paid for a database.
    assert OPERATIONS == worker.OPS


def test_only_graphs_is_kept_out_of_this_process() -> None:
    # The measured abort is `Ent.draw`, nothing else: open, iterate, metric and close all
    # work in-process, so routing more than `graphs` out would cost a mode for no reason.
    assert UPYTHON_ONLY_OPS == frozenset({"graphs"})


# --- the upython mode ------------------------------------------------------------


def test_the_worker_runs_under_upython_with_the_operation_as_its_argument(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    stub_upython.answers("ping", {"version": "6.5.1204"})

    a_runner(stub_upython, command_log).run("ping", {})

    assert stub_upython.calls[0]["argv"] == [str(WORKER_PATH), "ping"]


def test_the_worker_script_the_runner_names_is_the_one_that_ships() -> None:
    # A path that does not exist would only fail once a real Understand is installed.
    assert WORKER_PATH.is_file()
    assert WORKER_PATH.name == "worker.py"


def test_the_request_reaches_the_worker_as_json_on_standard_input(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    stub_upython.answers("catalogue", {"metrics": {}})
    request = {"kinds": ["python file ~unknown ~unresolved"], "describe": ["CountLineCode"]}

    a_runner(stub_upython, command_log).run("catalogue", request)

    assert json.loads(stub_upython.calls[0]["stdin"]) == request


def test_the_answer_document_is_returned_verbatim(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    document = {"metrics": {"python file": ["CountLineCode"]}, "descriptions": {}}
    stub_upython.answers("catalogue", document)

    assert a_runner(stub_upython, command_log).run("catalogue", {"kinds": []}) == document


def test_every_subprocess_run_is_recorded_with_its_argv_and_status(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    stub_upython.answers("ping", {"version": "6.5.1204"})

    a_runner(stub_upython, command_log).run("ping", {})

    argv, seconds, rc = command_log.calls[0]
    assert argv == [str(stub_upython.path), str(WORKER_PATH), "ping"]
    assert rc == 0
    # `> 0.0`, not `>= 0.0`: a `time.monotonic()` delta is non-negative by construction, so
    # the comparison this replaces could not fail and `record(argv, 0.0, rc)` survived all
    # 3067 tests. The quantity itself is pinned by the sleeping stub in the next test.
    assert seconds > 0.0
    assert seconds < CLOCK_READING_CEILING_S


def test_the_recorded_duration_is_the_length_of_the_operation_it_timed(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    """Requirement 12.8: the seconds in the log are *this operation's*, not a plausible number.

    The stub sleeps :data:`SLOW_ANSWER_SLEEP_S` before answering, which is a duration this
    test knows independently of the runner, and the recorded number is held between a floor
    and a ceiling that fail different mistakes: the floor fails any constant below 0.25
    (``0.0`` included), the ceiling fails a clock *reading* recorded in place of a delta.
    """
    stub_upython.script(
        "ping", stdout=json.dumps({"version": "6.5.1204"}), sleep=SLOW_ANSWER_SLEEP_S
    )

    a_runner(stub_upython, command_log).run("ping", {})

    assert len(command_log.calls) == 1, "one operation in, one line out"
    _, seconds, rc = command_log.calls[-1]
    assert rc == 0
    assert seconds >= SLOW_ANSWER_FLOOR_S, (
        f"a call that slept {SLOW_ANSWER_SLEEP_S}s logged {seconds}s"
    )
    assert seconds < CLOCK_READING_CEILING_S, f"{seconds}s is a clock reading, not a duration"


def test_the_two_recorded_statuses_are_the_conventional_numbers() -> None:
    """The statuses this runner logs for an operation that was killed or never started.

    Pinned as literals here because the names are imported from
    :mod:`scitools_hook.exit_codes` rather than defined in the runner (task 11.2): ``git``,
    the ``und`` wrapper and the installation probes write the same two numbers into the same
    ``--verbose`` stream, and an operator who has learnt that 124 means "killed" and 127 means
    "never started" must not have to learn a different pair per adapter. The behavioural tests
    assert the same two literals where they are recorded; this one is about the values the
    module exports.
    """
    assert TIMEOUT_RC == TIMEOUT_KILLED_STATUS
    assert MISSING_RC == SHELL_COMMAND_NOT_FOUND_STATUS
    # The third status is this module's own -- the in-process mode has no child to report --
    # but it is pinned by the same rule, and by the same measurement: `1 -> 0` survived.
    assert WORKER_RC == PYTHON_TRACEBACK_STATUS


def test_a_worker_that_exits_non_zero_is_a_broken_worker_not_an_answer(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # The worker exits 0 for every answer it can express, envelopes included, so a non-zero
    # status means the worker itself broke — never a refusal the caller could act on.
    stub_upython.script("snapshot", rc=1, stdout="", stderr="Traceback (most recent call last)")

    with pytest.raises(AnalysisFailedError) as failure:
        a_runner(stub_upython, command_log).run("snapshot", {})

    assert failure.value.command == [str(stub_upython.path), str(WORKER_PATH), "snapshot"]
    assert "Traceback" in failure.value.stderr
    assert command_log.calls[0][2] == 1


def test_output_that_is_not_json_is_reported_with_the_command_and_the_stderr(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    stub_upython.script("ping", stdout="Segmentation fault\n", stderr="qt.qpa.plugin: failed")

    with pytest.raises(AnalysisFailedError) as failure:
        a_runner(stub_upython, command_log).run("ping", {})

    assert "qt.qpa.plugin: failed" in failure.value.stderr
    assert failure.value.command[-1] == "ping"


def test_output_that_is_json_but_not_an_object_is_refused(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # `json.loads` accepts a bare list or number; the contract is one JSON *object*.
    stub_upython.script("ping", stdout="[1, 2, 3]\n")

    with pytest.raises(AnalysisFailedError):
        a_runner(stub_upython, command_log).run("ping", {})


def test_a_worker_that_never_returns_is_killed_and_reported(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # `subprocess.TimeoutExpired` is not an `OSError`: catching only `OSError` hangs the Gate
    # for as long as Understand hangs.
    stub_upython.script("snapshot", sleep=10, stdout="{}")

    with pytest.raises(AnalysisFailedError) as failure:
        a_runner(stub_upython, command_log, timeout_s=1).run("snapshot", {})

    assert "timed out" in str(failure.value)
    # The literal 124, not `TIMEOUT_RC` read back out of the module under test: that
    # comparison is a tautology and `124 -> 0` survived all 3067 tests under it. The duration
    # is asserted against the one-second limit this test set, because `record(argv, 0.0,
    # TIMEOUT_RC)` survived too -- `--verbose` could have claimed the hang took no time.
    _, seconds, rc = command_log.calls[0]
    assert rc == TIMEOUT_KILLED_STATUS
    assert seconds >= KILLED_FLOOR_S, f"an operation killed at a 1s limit logged {seconds}s"
    assert seconds < CLOCK_READING_CEILING_S, f"{seconds}s is a clock reading, not a duration"


def test_an_interpreter_that_cannot_be_run_is_recorded_and_reported(
    tmp_path: Path, command_log: FakeCommandLog
) -> None:
    missing = StubUpython(tmp_path / "gone" / "bin" / "linux64" / "upython")

    with pytest.raises(AnalysisFailedError) as failure:
        a_runner(missing, command_log).run("ping", {})

    # The literal 127, for the reason given on the timeout above. The duration is asserted
    # because failing to start still takes measurable time (measured: 0.0004 s) and
    # `--verbose` prints that line like any other.
    _, seconds, rc = command_log.calls[0]
    assert rc == SHELL_COMMAND_NOT_FOUND_STATUS
    assert seconds > 0.0, "an interpreter that never started still took time to fail"
    assert seconds < CLOCK_READING_CEILING_S, f"{seconds}s is a clock reading, not a duration"
    assert failure.value.command[0] == str(missing.path)


def test_an_installation_without_upython_cannot_run_the_upython_mode(
    command_log: FakeCommandLog,
) -> None:
    with pytest.raises(AnalysisFailedError) as failure:
        a_runner(None, command_log).run("ping", {})

    assert "upython" in str(failure.value)


# --- the in-process mode ---------------------------------------------------------


@dataclass
class FakeDispatch:
    """Stands in for ``worker.dispatch``: records the call and answers, or raises.

    ``sleep_s`` is what lets the in-process mode's recorded duration be pinned against a
    quantity the test knows on its own. The subprocess mode gets that from its stub's
    ``sleep``; without it here the in-process branch could only ever be compared against
    zero, which is the assertion task 11.2 exists to remove.
    """

    answer: dict[str, object]
    raises: Exception | None = None
    sleep_s: float = 0.0
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def __call__(self, op: str, request: Mapping[str, object]) -> dict[str, object]:
        self.calls.append((op, dict(request)))
        os.environ["LC_NUMERIC"] = "C"  # what `worker._import_api` does on every call
        time.sleep(self.sleep_s)
        if self.raises is not None:
            raise self.raises
        return self.answer


def install_dispatch(monkeypatch: pytest.MonkeyPatch, fake: FakeDispatch) -> FakeDispatch:
    """Replace the worker's dispatch; the worker's own behaviour is task 6.1's subject."""
    monkeypatch.setattr(worker, "dispatch", fake)
    return fake


def test_the_in_process_mode_calls_the_worker_in_this_interpreter(
    stub_upython: StubUpython, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install_dispatch(monkeypatch, FakeDispatch({"version": "6.5.1204"}))

    answer = a_runner(stub_upython, command_log, mode="inprocess").run("ping", {"a": 1})

    assert answer == {"version": "6.5.1204"}
    assert fake.calls == [("ping", {"a": 1})]
    assert stub_upython.calls == []


def test_the_in_process_mode_records_the_interpreter_it_ran_in(
    stub_upython: StubUpython, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dispatch(monkeypatch, FakeDispatch({}))

    a_runner(stub_upython, command_log, mode="inprocess").run("snapshot", {})

    argv, seconds, rc = command_log.calls[0]
    assert argv == [IN_PROCESS, sys.executable, str(WORKER_PATH), "snapshot"]
    assert rc == 0
    assert seconds > 0.0
    assert seconds < CLOCK_READING_CEILING_S


def test_the_in_process_mode_records_the_length_of_the_call_it_timed(
    stub_upython: StubUpython, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-process branch needs its own pinned duration, not only the subprocess one.

    ``--verbose`` prints an in-process line exactly like a subprocess line, and the two are
    separate ``record`` calls: ``record(argv, 0.0, 0)`` in this branch survived all 3067 tests
    while the subprocess branch was the only one with a duration assertion on it.
    """
    install_dispatch(monkeypatch, FakeDispatch({}, sleep_s=SLOW_ANSWER_SLEEP_S))

    a_runner(stub_upython, command_log, mode="inprocess").run("snapshot", {})

    assert len(command_log.calls) == 1, "one operation in, one line out"
    _, seconds, rc = command_log.calls[-1]
    assert rc == 0
    assert seconds >= SLOW_ANSWER_FLOOR_S, (
        f"a call that slept {SLOW_ANSWER_SLEEP_S}s logged {seconds}s"
    )
    assert seconds < CLOCK_READING_CEILING_S, f"{seconds}s is a clock reading, not a duration"


def test_the_in_process_mode_puts_the_api_directory_on_the_path_exactly_once(
    stub_upython: StubUpython, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dispatch(monkeypatch, FakeDispatch({}))
    monkeypatch.setattr(sys, "path", list(sys.path))
    runner = a_runner(stub_upython, command_log, mode="inprocess")
    api_dir = str(an_env(stub_upython.path, "inprocess").python_api_dir)

    runner.run("ping", {})
    runner.run("ping", {})

    assert sys.path.count(api_dir) == 1
    # Appended, never inserted: an Understand directory ahead of the project's own would
    # shadow whatever it happens to hold.
    assert sys.path[-1] == api_dir


def test_the_in_process_mode_leaves_the_hosts_numeric_locale_alone(
    stub_upython: StubUpython, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `worker._import_api` forces LC_NUMERIC=C so Understand writes `0.5` and not `0,5`.
    # In a subprocess that is invisible; in this process it would rewrite the operator's
    # environment for everything that runs after the Gate's first API call.
    install_dispatch(monkeypatch, FakeDispatch({}))
    monkeypatch.setenv("LC_NUMERIC", "de_DE.UTF-8")

    a_runner(stub_upython, command_log, mode="inprocess").run("ping", {})

    assert os.environ["LC_NUMERIC"] == "de_DE.UTF-8"


def test_the_in_process_mode_leaves_an_unset_numeric_locale_unset(
    stub_upython: StubUpython, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dispatch(monkeypatch, FakeDispatch({}))
    monkeypatch.delenv("LC_NUMERIC", raising=False)

    a_runner(stub_upython, command_log, mode="inprocess").run("ping", {})

    assert "LC_NUMERIC" not in os.environ


def test_a_worker_that_raises_in_process_is_reported_like_a_non_zero_exit(
    stub_upython: StubUpython, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The subprocess mode turns a traceback into an AnalysisFailedError; the modes must not
    # disagree about what a broken worker is.
    install_dispatch(monkeypatch, FakeDispatch({}, raises=RuntimeError("the API blew up")))

    with pytest.raises(AnalysisFailedError) as failure:
        a_runner(stub_upython, command_log, mode="inprocess").run("snapshot", {})

    assert "the API blew up" in failure.value.stderr
    _, seconds, rc = command_log.calls[0]
    assert rc == PYTHON_TRACEBACK_STATUS
    # A worker that raised still ran for a measurable time, and the line is printed like any
    # other: `record(argv, 0.0, WORKER_RC)` survived all 3067 tests before this assertion.
    assert seconds > 0.0
    assert seconds < CLOCK_READING_CEILING_S


def test_a_worker_that_raises_in_process_still_restores_the_locale(
    stub_upython: StubUpython, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dispatch(monkeypatch, FakeDispatch({}, raises=RuntimeError("boom")))
    monkeypatch.setenv("LC_NUMERIC", "de_DE.UTF-8")

    with pytest.raises(AnalysisFailedError):
        a_runner(stub_upython, command_log, mode="inprocess").run("ping", {})

    assert os.environ["LC_NUMERIC"] == "de_DE.UTF-8"


def test_graphs_runs_under_upython_even_when_the_mode_is_in_process(
    stub_upython: StubUpython, command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Measured: an in-process `Ent.draw` aborts the host process with status 127. Routing it
    # out is the difference between one failed review aid and a dead Gate.
    fake = install_dispatch(monkeypatch, FakeDispatch({}))
    stub_upython.answers("graphs", {"graphs": [], "warnings": []})

    a_runner(stub_upython, command_log, mode="inprocess").run("graphs", {})

    assert fake.calls == []
    assert stub_upython.calls[0]["argv"] == [str(WORKER_PATH), "graphs"]


def test_graphs_without_a_bundled_interpreter_is_refused_rather_than_drawn_here(
    command_log: FakeCommandLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install_dispatch(monkeypatch, FakeDispatch({"graphs": [], "warnings": []}))

    with pytest.raises(AnalysisFailedError) as failure:
        a_runner(None, command_log, mode="inprocess").run("graphs", {})

    assert fake.calls == []
    assert "upython" in str(failure.value)


# --- the envelope contract -------------------------------------------------------

RECORDED_ENVELOPES: Final[dict[str, dict[str, Any]]] = {
    "ApiUnavailable": {
        "type": "ApiUnavailable",
        "message": "the understand Python API is not importable by /usr/bin/python3.14: "
        "No module named 'understand'",
    },
    "DBUnableOpen": {"type": "DBUnableOpen", "message": "DBUnableOpen: unable to open database"},
    "DBEmpty": {"type": "UnderstandError", "message": "DBEmpty: database is empty"},
    "ArchitectureNotFound": {
        "type": "ArchitectureNotFound",
        "message": "architecture 'No Such Arch' does not exist in /cache/after.und",
        "available": ["Directory Structure"],
    },
    "AnalysisRootMismatch": {
        "type": "AnalysisRootMismatch",
        "message": "no file of /cache/after.und is under the analysis root '/completely/wrong'",
        "found": ["/ws/after/analysis/engine.py", "/ws/after/cli/app.py"],
    },
    "BadRequest": {"type": "BadRequest", "message": "'root' must be a non-empty string, got None"},
    "UnknownOperation": {
        "type": "UnknownOperation",
        "message": "unknown operation 'frobnicate'; known operations: ping, catalogue, archs",
    },
}
"""Envelopes copied verbatim from the real worker on the licensed machine (2026-08-30)."""


def refusal(stub: StubUpython, log: FakeCommandLog, error: Mapping[str, object]) -> GateError:
    """Run one operation that answers with ``error`` and return the typed error it became."""
    stub.answers("snapshot", {"error": dict(error)})
    with pytest.raises(GateError) as raised:
        a_runner(stub, log).run("snapshot", {})
    return raised.value


def test_no_api_license_is_a_license_failure_carrying_what_understand_said(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    error = refusal(
        stub_upython,
        command_log,
        {"type": "NoApiLicense", "message": "NoApiLicense: no valid license"},
    )

    assert isinstance(error, LicenseError)
    assert error.exit_code == ExitCode.LICENSE_UNAVAILABLE
    assert error.und_output == "NoApiLicense: no valid license"


def test_an_interpreter_without_the_api_is_an_installation_problem_not_a_license_one(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # The distinction the operator acts on: `ApiUnavailable` means *this interpreter* cannot
    # import the module, which no license can fix.
    error = refusal(stub_upython, command_log, RECORDED_ENVELOPES["ApiUnavailable"])

    assert isinstance(error, UnderstandNotFoundError)
    assert not isinstance(error, LicenseError)
    assert error.exit_code == ExitCode.UNDERSTAND_NOT_FOUND
    assert error.tried


def test_a_missing_architecture_is_a_configuration_error_listing_the_ones_that_exist(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    error = refusal(stub_upython, command_log, RECORDED_ENVELOPES["ArchitectureNotFound"])

    assert isinstance(error, ArchitectureNotFoundError)
    assert error.available == ["Directory Structure"]
    assert error.exit_code == ExitCode.CONFIG_ERROR
    assert error.key == "structure.architecture"


def test_a_wrong_analysis_root_is_a_configuration_error_naming_what_was_found(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # The caller pointed at the wrong directory; the alternative reading — an analysis
    # failure — sends the operator to rebuild a database that is perfectly fine.
    error = refusal(stub_upython, command_log, RECORDED_ENVELOPES["AnalysisRootMismatch"])

    assert isinstance(error, ConfigError)
    assert not isinstance(error, AnalysisFailedError)
    assert error.exit_code == ExitCode.CONFIG_ERROR
    assert "/ws/after/cli/app.py" in (error.hint or "")


def test_a_half_built_database_asks_for_a_rebuild(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # Measured: `understand.open` on a partially built `.und` raises `DBEmpty: database is
    # empty`, which the worker classifies as the generic `UnderstandError` — so the type
    # alone cannot carry this one and the message has to be read.
    error = refusal(stub_upython, command_log, RECORDED_ENVELOPES["DBEmpty"])

    assert isinstance(error, AnalysisFailedError)
    assert "db rebuild" in (error.hint or "")


def test_an_explicit_db_empty_envelope_is_mapped_the_same_way(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # The worker may learn to classify it; the runner must not then lose the hint.
    error = refusal(
        stub_upython, command_log, {"type": "DBEmpty", "message": "DBEmpty: database is empty"}
    )

    assert isinstance(error, AnalysisFailedError)
    assert "db rebuild" in (error.hint or "")


def test_a_corrupt_or_outdated_database_asks_for_a_rebuild(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    for kind in ("DBCorrupt", "DBOldVersion", "DBUnknownVersion"):
        error = refusal(stub_upython, command_log, {"type": kind, "message": f"{kind}: bad"})
        assert isinstance(error, AnalysisFailedError)
        assert "db rebuild" in (error.hint or ""), kind


@pytest.mark.parametrize(
    "kind",
    ["DBAlreadyOpen", "DBUnableOpen", "UnderstandError", "BadRequest", "UnknownOperation"],
)
def test_every_other_refusal_is_an_analysis_failure(
    stub_upython: StubUpython, command_log: FakeCommandLog, kind: str
) -> None:
    recorded = RECORDED_ENVELOPES.get(kind, {"type": kind, "message": f"{kind}: something"})
    error = refusal(stub_upython, command_log, recorded)

    assert isinstance(error, AnalysisFailedError)
    assert error.exit_code == ExitCode.ANALYSIS_FAILED
    assert str(error) == recorded["message"]


def test_every_error_type_the_worker_can_answer_with_has_a_typed_error(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # A new envelope type must not fall through as a success document: the answer would be
    # validated as a snapshot and fail somewhere far from the cause.
    known = (
        *worker.ERROR_TYPES,
        "UnderstandError",
        "ApiUnavailable",
        "ArchitectureNotFound",
        "AnalysisRootMismatch",
        "DBEmpty",
        "BadRequest",
        "UnknownOperation",
    )
    for kind in known:
        error = refusal(stub_upython, command_log, {"type": kind, "message": f"{kind}: text"})
        assert isinstance(error, GateError), kind
        assert error.exit_code != ExitCode.UNEXPECTED, kind


@pytest.mark.parametrize(
    ("kind", "own_hint"),
    [("DBUnableOpen", "may read it"), ("BadRequest", "defect in the Gate")],
)
def test_a_type_the_worker_named_is_never_re_read_from_its_message(
    stub_upython: StubUpython, command_log: FakeCommandLog, kind: str, own_hint: str
) -> None:
    # `DBEmpty` is recovered from the message for the catch-all `UnderstandError` and for
    # nothing else. The envelopes below are deliberately contradictory — a type the worker
    # classified, over a message whose prose spells the text the catch-all is recognised by —
    # because that is the only way to say which of the two wins. Reading the message over a
    # named type would make every hint hostage to whatever the message quotes (a database
    # path, the request, a chained exception) and would answer a database that cannot be
    # opened at all, or a request the Gate itself built wrong, with "rebuild the analysis".
    error = refusal(
        stub_upython,
        command_log,
        {"type": kind, "message": f"{kind}: DBEmpty is not what went wrong here"},
    )

    assert isinstance(error, AnalysisFailedError)
    assert own_hint in (error.hint or "")
    assert "db rebuild" not in (error.hint or "")


def test_an_unknown_error_type_is_still_typed(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    error = refusal(stub_upython, command_log, {"type": "SomethingNew", "message": "who knows"})

    assert isinstance(error, AnalysisFailedError)


def test_an_answer_without_an_error_key_is_not_a_refusal(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # `impact` answers with a `warnings` list; nothing but `error` may raise.
    document = {"impact": {}, "warnings": ["the routine 'nope' is not in this database"]}
    stub_upython.answers("impact", document)

    assert a_runner(stub_upython, command_log).run("impact", {}) == document


def test_an_error_key_that_is_not_an_envelope_is_not_a_refusal(
    stub_upython: StubUpython, command_log: FakeCommandLog
) -> None:
    # A refusal is an envelope — a mapping carrying a type — and the guard that says so is
    # what keeps a document from being read as one. A bare string has no `type` and no
    # `message` to read: taking it for an envelope calls `.get` on a `str` and ends the run
    # with an `AttributeError` no caller can map to an exit code, in the one module whose
    # whole purpose is to turn every foreseeable failure into a typed error.
    document = {"error": "the routine 'nope' is not in this database"}
    stub_upython.answers("impact", document)

    assert a_runner(stub_upython, command_log).run("impact", {}) == document


# --- against the real Understand -------------------------------------------------


def upython_or_skip() -> Path:
    """The interpreter Understand ships next to ``und``; skip when this build has none."""
    probe = understand_probe()
    assert probe.und is not None, "the contract gate only lets this run with a usable probe"
    upython = probe.und.parent / "upython"
    if not upython.exists():
        pytest.skip(f"no upython next to {probe.und}")
    return upython


def real_env(mode: str) -> UnderstandEnv:
    """The installation this machine has, in the requested execution mode."""
    upython = upython_or_skip()
    bin_dir = upython.parent
    return UnderstandEnv(
        home=bin_dir.parent.parent,
        und=bin_dir / "und",
        upython=upython,
        python_api_dir=bin_dir / "Python",
        version="6.5.1204",
        source="env:SCITOOLS_HOME",
        api_mode="upython" if mode == "upython" else "inprocess",
    )


def snapshot_request(databases: SampleDatabases, side: Side) -> dict[str, object]:
    """The request ``SnapshotExtractor`` builds, spelled out so this test needs no wrapper."""
    root = databases.root(side)
    files = sorted(Path(name).relative_to(root).as_posix() for name in databases.list_files(side))
    return {
        "db": str(databases.db(side)),
        "side": side,
        "root": str(root),
        "files": files,
        "kinds_by_scope": dict(SCOPE_KINDS),
        "metrics_by_scope": {
            "routine": ["CyclomaticStrict", "CountLineCode", "CountParams"],
            "class": ["CountDeclMethod", "PercentLackOfCohesion"],
            "file": ["CountLineCode", "RatioCommentToCode"],
        },
        "synthetic": ["CountParams"],
        "population_metrics": {"routine": ["AVG:CyclomaticStrict"]},
        "ignore": {},
        "architecture": "Directory Structure",
        "depth": 2,
        "include_edges": True,
        "parse_errors": [],
    }


def run_in(mode: str, op: Operation, request: Mapping[str, object]) -> dict[str, object]:
    """Run one operation against the real installation in one mode."""
    return ApiRunner(real_env(mode), NullCommandLog()).run(op, request)


@pytest.mark.contract
def test_both_modes_answer_with_the_same_snapshot_for_the_same_database(
    sample_databases: SampleDatabases,
) -> None:
    """The done criterion: ``dispatch`` is one implementation, so the modes cannot disagree.

    Anything that made them disagree — a request the subprocess mangles, a document the
    in-process path reads differently, a locale that reaches only one of them — would show
    up as a different snapshot for the same database, which is exactly the failure a ratchet
    cannot see.
    """
    request = snapshot_request(sample_databases, "after")

    under_upython = ProjectSnapshot.model_validate(run_in("upython", "snapshot", request))
    in_process = ProjectSnapshot.model_validate(run_in("inprocess", "snapshot", request))

    assert under_upython.entities, "the sample project has entities in every scope"
    assert in_process == under_upython
    assert in_process.model_dump_json() == under_upython.model_dump_json()


@pytest.mark.contract
def test_both_modes_report_a_missing_architecture_the_same_way(
    sample_databases: SampleDatabases,
) -> None:
    request = dict(snapshot_request(sample_databases, "after"), architecture="No Such Arch")

    for mode in ("upython", "inprocess"):
        with pytest.raises(ArchitectureNotFoundError) as failure:
            run_in(mode, "snapshot", request)
        assert failure.value.available == ["Directory Structure"], mode


@pytest.mark.contract
def test_a_database_that_cannot_be_opened_is_an_analysis_failure(tmp_path: Path) -> None:
    with pytest.raises(AnalysisFailedError) as failure:
        run_in("upython", "archs", {"db": str(tmp_path / "gone.und"), "architecture": "x"})

    assert "DBUnableOpen" in str(failure.value)


@pytest.mark.contract
def test_a_half_built_database_is_recognised_on_the_real_installation(tmp_path: Path) -> None:
    """The measured ``DBEmpty`` text, from the real ``understand.open``."""
    half_built = tmp_path / "half.und"
    half_built.mkdir()

    with pytest.raises(AnalysisFailedError) as failure:
        run_in("upython", "archs", {"db": str(half_built), "architecture": "Directory Structure"})

    assert "db rebuild" in (failure.value.hint or "")


@pytest.mark.contract
def test_a_wrong_analysis_root_is_a_configuration_error_on_the_real_installation(
    sample_databases: SampleDatabases,
) -> None:
    request = dict(snapshot_request(sample_databases, "after"), root="/completely/wrong")

    with pytest.raises(ConfigError) as failure:
        run_in("upython", "snapshot", request)

    assert not isinstance(failure.value, ArchitectureNotFoundError)
    assert "engine.py" in (failure.value.hint or "")
