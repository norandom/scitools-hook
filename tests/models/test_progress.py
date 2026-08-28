"""Progress and command-log ports plus their no-op implementations (4.11, 12.8)."""

from __future__ import annotations

from scitools_hook.models.progress import (
    CommandLog,
    NullCommandLog,
    NullProgress,
    Progress,
)


class _RecordingLog:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], float, int]] = []

    def record(self, argv: list[str], seconds: float, rc: int) -> None:
        self.calls.append((argv, seconds, rc))


def test_null_progress_satisfies_the_progress_port() -> None:
    progress: Progress = NullProgress()
    assert isinstance(progress, Progress)


def test_null_progress_does_nothing() -> None:
    progress: Progress = NullProgress()
    progress.start("analyze")
    progress.note("2 files re-analyzed")
    progress.finish("analyze", 6.5)


def test_null_command_log_satisfies_the_command_log_port() -> None:
    log: CommandLog = NullCommandLog()
    assert isinstance(log, CommandLog)


def test_null_command_log_does_nothing() -> None:
    log: CommandLog = NullCommandLog()
    log.record(["und", "analyze"], 3.0, 0)


def test_any_object_with_record_satisfies_the_command_log_port() -> None:
    recorder = _RecordingLog()
    log: CommandLog = recorder
    log.record(["git", "diff", "--cached"], 0.02, 0)
    assert isinstance(log, CommandLog)
    assert recorder.calls == [(["git", "diff", "--cached"], 0.02, 0)]
