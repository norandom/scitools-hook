"""The baseline file on disk: missing, unreadable, round-tripped (task 8.2; req 8.1, 8.6).

``analysis.baseline`` already decides what a *document* means; everything tested here is
about the file it comes from. Requirement 8.6 says an unreadable baseline is reported and the
run continues on configured limits, so every failure below must come back as a
:class:`BaselineIssue` and never as an exception -- with one exception of its own: a baseline
the Gate cannot *write* is not a degraded run, it is an operator instruction that failed, and
it raises.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from scitools_hook.config.models import Limit, Settings, ThresholdSpec
from scitools_hook.errors import ConfigError
from scitools_hook.models.baseline import Baseline
from scitools_hook.runner.baseline_store import BaselineStore, baseline_path

STAMP = "2026-08-28T10:00:00+00:00"
"""Fixed capture timestamp, so a written file is byte-comparable."""


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


def _no_space(*args: object, **kwargs: object) -> None:
    """A write that fails the way a full disk does."""
    raise OSError(28, "No space left on device")


def specs() -> list[ThresholdSpec]:
    """Two configured thresholds, the keys a stored baseline may name."""
    return [
        ThresholdSpec(scope="routine", metric="CyclomaticStrict", limit=Limit(max=10)),
        ThresholdSpec(scope="file", metric="CountLineCode", limit=Limit(max=500)),
    ]


def stored(tmp_path: Path, document: object) -> BaselineStore:
    """A store over a file already holding ``document``."""
    path = tmp_path / "scitools-hook.baseline.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return BaselineStore(path)


# --- load: the three states of the file (req 8.6) --------------------------------


def _too_deep(*_args: object, **_kwargs: object) -> object:
    """Stand in for a document the parser cannot descend."""
    raise RecursionError("maximum recursion depth exceeded")


def test_a_missing_baseline_file_is_no_baseline_and_no_problem(tmp_path: Path) -> None:
    """A repository that never captured a baseline is the normal case, not a fault."""
    baseline, issues = BaselineStore(tmp_path / "absent.json").load(specs())
    assert baseline is None
    assert issues == []


def test_a_stored_baseline_is_read_into_its_values(tmp_path: Path) -> None:
    """The values reach the caller keyed by rule name, ready for ``analysis.baseline.apply``."""
    store = stored(
        tmp_path,
        {"version": 1, "captured_at": STAMP, "values": {"routine.CyclomaticStrict": 9.0}},
    )
    baseline, issues = store.load(specs())
    assert baseline is not None
    assert baseline.values == {"routine.CyclomaticStrict": 9.0}
    assert baseline.captured_at == STAMP
    assert issues == []


def test_a_file_that_is_not_json_is_reported_rather_than_raised(tmp_path: Path) -> None:
    """Requirement 8.6: an unreadable baseline is a problem the run reports and survives."""
    path = tmp_path / "baseline.json"
    path.write_text("{not json at all", encoding="utf-8")
    baseline, issues = BaselineStore(path).load(specs())
    assert baseline is None
    assert len(issues) == 1
    assert str(path) in issues[0].message
    assert "not valid JSON" in issues[0].message


def test_a_directory_where_the_baseline_should_be_is_reported(tmp_path: Path) -> None:
    """An unreadable path is an operating-system failure, and it is still only a problem."""
    path = tmp_path / "baseline.json"
    path.mkdir()
    baseline, issues = BaselineStore(path).load(specs())
    assert baseline is None
    assert len(issues) == 1
    assert str(path) in issues[0].message
    assert "not a regular file" in issues[0].message


def test_a_key_no_configured_threshold_owns_is_reported_and_the_rest_still_load(
    tmp_path: Path,
) -> None:
    """Requirement 8.6's second half: the good entries survive an unknown one."""
    store = stored(
        tmp_path,
        {
            "version": 1,
            "captured_at": STAMP,
            "values": {"routine.CyclomaticStrict": 9.0, "routine.Gone": 3.0},
        },
    )
    baseline, issues = store.load(specs())
    assert baseline is not None
    assert baseline.values == {"routine.CyclomaticStrict": 9.0}
    assert [issue.key for issue in issues] == ["routine.Gone"]


# --- save and round-trip (req 8.1) -----------------------------------------------


def test_a_saved_baseline_loads_back_unchanged(tmp_path: Path) -> None:
    """The round trip requirement 8.1 rests on: capture, save, load, same values."""
    path = tmp_path / "nested" / "scitools-hook.baseline.json"
    store = BaselineStore(path)
    written = Baseline(
        captured_at=STAMP, values={"file.CountLineCode": 210.0, "routine.CyclomaticStrict": 12.0}
    )
    store.save(written)
    read, issues = store.load(specs())
    assert read == written
    assert issues == []


def test_saving_writes_a_readable_document_with_sorted_keys(tmp_path: Path) -> None:
    """The file is committed to a repository, so two identical baselines are byte-identical."""
    path = tmp_path / "baseline.json"
    BaselineStore(path).save(
        Baseline(
            captured_at=STAMP,
            values={"routine.CyclomaticStrict": 12.0, "file.CountLineCode": 210.0},
        )
    )
    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == {
        "version": 1,
        "captured_at": STAMP,
        "values": {"file.CountLineCode": 210.0, "routine.CyclomaticStrict": 12.0},
    }
    assert text.index('"file.CountLineCode"') < text.index('"routine.CyclomaticStrict"')
    assert text.endswith("\n")


def test_saving_over_an_existing_baseline_replaces_it_entirely(tmp_path: Path) -> None:
    """A tightened baseline must not leave an old, higher entry behind (req 8.3, 8.4)."""
    path = tmp_path / "baseline.json"
    store = BaselineStore(path)
    store.save(Baseline(captured_at=STAMP, values={"routine.CyclomaticStrict": 12.0}))
    store.save(Baseline(captured_at=STAMP, values={"file.CountLineCode": 210.0}))
    read, _ = store.load(specs())
    assert read is not None
    assert read.values == {"file.CountLineCode": 210.0}


def test_a_baseline_that_cannot_be_written_raises_naming_the_file(tmp_path: Path) -> None:
    """A failed capture must never look like a successful one; it names the path it tried."""
    blocked = tmp_path / "occupied"
    blocked.write_text("not a directory", encoding="utf-8")
    store = BaselineStore(blocked / "baseline.json")
    with pytest.raises(ConfigError) as raised:
        store.save(Baseline(captured_at=STAMP, values={}))
    assert raised.value.file == blocked / "baseline.json"
    assert "baseline" in raised.value.message


# --- where the file lives (req 8.1: "a repository-level file" by default) --------


def test_a_relative_configured_path_is_resolved_against_the_repository_root() -> None:
    """The default ``scitools-hook.baseline.json`` belongs to the repository, not the cwd."""
    settings = Settings()
    assert baseline_path(settings, Path("/repo")) == Path("/repo/scitools-hook.baseline.json")


def test_an_absolute_configured_path_is_left_alone() -> None:
    """An operator who names a path outside the repository gets exactly that path."""
    settings = Settings.model_validate({"baseline": {"file": "/shared/team.baseline.json"}})
    assert baseline_path(settings, Path("/repo")) == Path("/shared/team.baseline.json")


def test_without_a_repository_a_relative_path_stays_relative_to_the_working_directory() -> None:
    """``check`` needs a repository, but ``baseline --file`` outside one still has a meaning."""
    settings = Settings()
    assert baseline_path(settings, None) == Path("scitools-hook.baseline.json")


def test_a_baseline_file_that_is_not_utf8_is_reported_rather_than_raised(tmp_path: Path) -> None:
    """A ``UnicodeDecodeError`` is a ``ValueError``, not an ``OSError``, and requirement 8.6
    asks for a report either way: the run continues on configured limits."""
    path = tmp_path / "baseline.json"
    path.write_bytes(b'{"version": 1, "values": {"routine.CyclomaticStrict": \xff}}')
    baseline, issues = BaselineStore(path).load(specs())
    assert baseline is None
    assert len(issues) == 1
    assert str(path) in issues[0].message
    assert "utf-8" in issues[0].message


def test_a_baseline_that_is_a_fifo_is_reported_rather_than_read(tmp_path: Path) -> None:
    """Opening a FIFO with no writer blocks forever, withholding the run's report entirely.

    Requirement 8.6 wants an unreadable baseline reported; a read that never returns reports
    nothing at all, so the kind is settled with ``stat``, which does not block.
    """
    path = tmp_path / "baseline.json"
    os.mkfifo(path)
    with time_limit(10):
        baseline, issues = BaselineStore(path).load(specs())
    assert baseline is None
    assert len(issues) == 1
    assert "not a regular file" in issues[0].message


def test_a_baseline_nested_too_deeply_is_reported_rather_than_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``json.loads`` answers a deeply nested document with ``RecursionError``, not ``ValueError``.

    The same fault class was already mapped twice for ``tomllib`` in ``config.loader``; it
    escaped here because that sweep went reader by reader instead of fault-class by reader.
    Requirement 8.6 and this module's own docstring both promise a ``BaselineIssue``.

    A depth of 100,000 used to be written here and CPython was trusted to refuse it. It does
    on some builds and not on others: in 3.14 the json C scanner is bounded by the C stack
    rather than by ``sys.getrecursionlimit()``, so the depth that trips it moves with the
    environment. Measured -- the release container parsed the same document this machine
    refuses, and `sys.setrecursionlimit(200)` does not help because the scanner never consults
    it. The test then failed on a message about the wrong thing entirely.

    What this actually promises is that a ``RecursionError`` from the parser is REPORTED, not
    that CPython raises one at any particular depth. So the error is injected. The claim is
    about our handler; the threshold is CPython's business and it is not stable enough to
    assert.
    """
    path = tmp_path / "baseline.json"
    path.write_text("[[[]]]", encoding="utf-8")
    monkeypatch.setattr(json, "loads", _too_deep)
    baseline, issues = BaselineStore(path).load(specs())
    assert baseline is None
    assert len(issues) == 1
    assert "too deeply" in issues[0].message


def test_a_byte_that_is_not_utf8_inside_a_key_is_reported_never_silently_replaced(
    tmp_path: Path,
) -> None:
    """A lenient decoder is a silent edit, and this one would edit a *threshold name*.

    With ``errors="replace"`` the 0xE9 below becomes U+FFFD, the document then parses, and the
    Gate accepts ``routine.Caf�Metric`` as a real metric key -- a baseline entry nobody
    wrote, silently attached to a threshold that does not exist. The strict decode is the
    behaviour; this is what pins it.
    """
    path = tmp_path / "baseline.json"
    path.write_bytes(b'{"version": 1, "captured_at": "x", "values": {"routine.Caf\xe9": 1.0}}')
    baseline, issues = BaselineStore(path).load(specs())
    assert baseline is None
    assert "utf-8" in issues[0].message


# --- the access and kind classes, which Path.exists() hides ---------------------


def test_a_baseline_behind_an_unsearchable_directory_is_reported_not_treated_as_absent(
    tmp_path: Path,
) -> None:
    """``Path.exists()`` swallows ``OSError``, so an unreachable file answered "no baseline".

    Requirement 8.6 says an unreadable baseline is *reported*; silently reading it as absent
    is worse than an exception, because the run then proceeds on configured limits believing
    no baseline was ever captured. ``os.lstat`` distinguishes genuine absence from every
    other reason the path cannot be reached.
    """
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    path = sealed / "baseline.json"
    path.write_text("{}", encoding="utf-8")
    sealed.chmod(0o000)
    try:
        baseline, issues = BaselineStore(path).load(specs())
    finally:
        sealed.chmod(0o755)
    assert baseline is None
    assert len(issues) == 1
    assert "cannot be reached" in issues[0].message


@pytest.mark.parametrize(
    ("shape", "phrase"),
    [
        ("dangling", "leads nowhere"),
        ("loop", "cannot be reached"),
        ("unreadable", "cannot be reached"),
    ],
)
def test_a_baseline_symlink_that_cannot_be_followed_is_reported(
    tmp_path: Path, shape: str, phrase: str
) -> None:
    """All three answer ``exists()`` False while the name is plainly taken.

    ``lstat`` does not follow the link, so "no baseline was captured" and "the baseline points
    at something I cannot use" stay distinct. The three are then told apart from each other as
    well: only a *dangling* link leads nowhere -- a loop and a target in an unsearchable
    directory both resolve to something, and reporting either as "nowhere" would send the
    operator looking for a missing file that exists.
    """
    path = tmp_path / "baseline.json"
    sealed = tmp_path / "sealed"
    if shape == "dangling":
        path.symlink_to(tmp_path / "nowhere")
    elif shape == "loop":
        path.symlink_to(path)
    else:
        sealed.mkdir()
        (sealed / "target.json").write_text("{}", encoding="utf-8")
        path.symlink_to(sealed / "target.json")
        sealed.chmod(0o000)
    try:
        baseline, issues = BaselineStore(path).load(specs())
    finally:
        if sealed.exists():
            sealed.chmod(0o755)
    assert baseline is None
    assert len(issues) == 1
    assert phrase in issues[0].message


def test_a_baseline_that_is_a_fifo_is_reported_by_kind_not_by_blocking(tmp_path: Path) -> None:
    """Settled by ``stat``, which does not block, before anything opens the path."""
    path = tmp_path / "baseline.json"
    os.mkfifo(path)
    with time_limit(10):
        baseline, issues = BaselineStore(path).load(specs())
    assert baseline is None
    assert "not a regular file" in issues[0].message


# --- writers are part of the matrix too ----------------------------------------


def test_saving_onto_a_fifo_is_refused_rather_than_blocking_forever(tmp_path: Path) -> None:
    """Opening a FIFO for *writing* blocks with no reader, exactly as reading does.

    Measured: still blocked at eight seconds. The reader in this module was guarded and the
    writer one method below it was not, because the sweep that found the class enumerated
    readers only.
    """
    path = tmp_path / "baseline.json"
    os.mkfifo(path)
    with time_limit(10), pytest.raises(ConfigError) as raised:
        BaselineStore(path).save(Baseline(captured_at=STAMP, values={}))
    assert raised.value.file == path
    assert "not a regular file" in raised.value.message


def test_saving_onto_a_device_is_refused_rather_than_reporting_a_capture_that_stored_nothing(
    tmp_path: Path,
) -> None:
    """Writing to ``/dev/null`` succeeds perfectly and stores nothing.

    A capture that failed then looks exactly like one that worked -- and the operator only
    finds out on the next run, when the limits the capture was meant to establish do not hold.
    """
    path = tmp_path / "baseline.json"
    path.symlink_to("/dev/null")
    with pytest.raises(ConfigError) as raised:
        BaselineStore(path).save(Baseline(captured_at=STAMP, values={"routine.X": 1.0}))
    assert "not a regular file" in raised.value.message


def test_a_baseline_key_outside_ascii_survives_the_round_trip(tmp_path: Path) -> None:
    """The encoding is written down as UTF-8 and must never be the platform default.

    Nothing on the default path produces a non-ASCII rule name, so this is what makes the
    ``encoding=`` argument load-bearing rather than decorative -- and it only became so once
    the writer stopped escaping to ASCII, which had made the declared encoding unable to
    change a single byte.
    """
    path = tmp_path / "baseline.json"
    store = BaselineStore(path)
    owned = [ThresholdSpec(scope="routine", metric="Café", limit=Limit(max=5))]
    written = Baseline(captured_at=STAMP, values={"routine.Café": 3.0})
    store.save(written)
    assert "Café" in path.read_text(encoding="utf-8")
    assert store.load(owned)[0] == written


def test_neither_reading_nor_writing_escapes_by_a_type_nobody_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 8.6 is an outcome, so it is guarded as one rather than by type list.

    ``MemoryError`` is the case that proved the point -- a regular file larger than available
    memory is neither an ``OSError``, a ``ValueError`` nor a ``RecursionError`` -- and it is
    injected here rather than reproduced, so the test pins the guard instead of the one
    exception that happened to expose it.
    """
    path = tmp_path / "baseline.json"
    path.write_text("{}", encoding="utf-8")

    def exhausted(*args: object, **kwargs: object) -> str:
        raise MemoryError("cannot allocate")

    monkeypatch.setattr(Path, "read_text", exhausted)
    baseline, issues = BaselineStore(path).load(specs())
    assert baseline is None
    assert "MemoryError" in issues[0].message

    monkeypatch.setattr(tempfile, "mkstemp", exhausted)
    with pytest.raises(ConfigError) as raised:
        BaselineStore(tmp_path / "out.json").save(Baseline(captured_at=STAMP, values={}))
    assert "MemoryError" in raised.value.message


def test_the_written_encoding_does_not_depend_on_the_ambient_locale(tmp_path: Path) -> None:
    """``encoding=`` cannot be pinned in-process, so this pins it in a controlled one.

    A round trip is self-consistent whatever the platform default is, and under a UTF-8 locale
    the qualified and unqualified writes produce identical bytes -- so neither a round-trip nor
    a bytes assertion can tell them apart. The difference only exists where the ambient
    encoding is *not* UTF-8, which is a child process. Measured under
    ``LC_ALL=C PYTHONUTF8=0 PYTHONCOERCECLOCALE=0``: the unqualified write raises
    ``UnicodeEncodeError: 'ascii'`` while the qualified one succeeds.

    This is the second half of a lesson: ``ensure_ascii=False`` made the argument *able* to
    matter, and that was mistaken for making it tested. Making a line load-bearing and pinning
    it are different steps.
    """
    script = tmp_path / "write.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from scitools_hook.models.baseline import Baseline\n"
        "from scitools_hook.runner.baseline_store import BaselineStore\n"
        "store = BaselineStore(Path(sys.argv[1]))\n"
        "store.save(Baseline(captured_at='x', values={'routine.Café': 3.0}))\n"
        "print('wrote', Path(sys.argv[1]).read_bytes().decode('utf-8').count('Café'))\n",
        encoding="utf-8",
    )
    target = tmp_path / "baseline.json"
    ascii_only = dict(os.environ, LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
    ascii_only.pop("LANG", None)
    ascii_only.pop("LC_CTYPE", None)
    done = subprocess.run(
        [sys.executable, str(script), str(target)],
        capture_output=True,
        text=True,
        env=ascii_only,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "wrote 1" in done.stdout
    assert "Café" in target.read_text(encoding="utf-8")


def test_saving_creates_every_missing_directory_level(tmp_path: Path) -> None:
    """``parents=True`` earns its place only below the first level.

    The round-trip test uses exactly one missing directory, which a plain ``mkdir`` also
    creates, so the argument was decorative as far as any test could tell.
    """
    path = tmp_path / "one" / "two" / "three" / "scitools-hook.baseline.json"
    BaselineStore(path).save(Baseline(captured_at=STAMP, values={"routine.X": 1.0}))
    assert path.is_file()


def test_a_failed_write_leaves_the_previous_baseline_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture that fails must not also destroy the baseline it was replacing.

    The failure is injected at the *rename*, because that is the only step which touches the
    target at all: a writer that renames leaves the old content when it fails there, while one
    that writes the destination directly has already truncated it and never reaches a rename.
    An injection anywhere earlier passes under both and proves nothing -- which is what the
    first version of this test did.
    """
    path = tmp_path / "baseline.json"
    store = BaselineStore(path)
    store.save(Baseline(captured_at=STAMP, values={"routine.X": 12.0}))
    original = path.read_text(encoding="utf-8")
    monkeypatch.setattr(os, "replace", _no_space)
    with pytest.raises(ConfigError):
        store.save(Baseline(captured_at=STAMP, values={"routine.X": 3.0}))
    assert path.read_text(encoding="utf-8") == original


def test_a_failed_write_leaves_no_scratch_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 8.1 puts the baseline in the repository by default, so a leftover scratch
    file shows up in ``git status`` and can be committed. Half of an atomic write is the
    cleanup, and it shipped untested."""
    path = tmp_path / "baseline.json"
    store = BaselineStore(path)
    store.save(Baseline(captured_at=STAMP, values={"routine.X": 12.0}))
    monkeypatch.setattr(os, "replace", _no_space)
    with pytest.raises(ConfigError):
        store.save(Baseline(captured_at=STAMP, values={"routine.X": 3.0}))
    assert [entry.name for entry in tmp_path.iterdir()] == ["baseline.json"]


@pytest.mark.parametrize("shape", ["fifo", "symlink", "regular"])
def test_a_hostile_scratch_name_cannot_divert_or_block_the_write(
    tmp_path: Path, shape: str
) -> None:
    """The scratch path is opened too, so it has to be safe by construction.

    All three were measured against a hand-built ``<name>.<pid>.tmp``: a FIFO blocked the write
    forever, a symlink was written *through* and the rename then made the baseline itself a
    symlink with the content landing elsewhere, and a pre-existing regular file was destroyed.
    ``mkstemp``'s ``O_EXCL`` and unpredictable name refuse all three without a guard.
    """
    path = tmp_path / "baseline.json"
    decoy = tmp_path / f"baseline.json.{os.getpid()}.tmp"
    elsewhere = tmp_path / "elsewhere"
    if shape == "fifo":
        os.mkfifo(decoy)
    elif shape == "symlink":
        elsewhere.write_text("untouched", encoding="utf-8")
        decoy.symlink_to(elsewhere)
    else:
        decoy.write_text("PRECIOUS", encoding="utf-8")
    with time_limit(10):
        BaselineStore(path).save(Baseline(captured_at=STAMP, values={"routine.X": 1.0}))
    assert path.is_file() and not path.is_symlink()
    assert BaselineStore(path).load(specs())[0] is not None
    if shape == "symlink":
        assert elsewhere.read_text(encoding="utf-8") == "untouched"
    if shape == "regular":
        assert decoy.read_text(encoding="utf-8") == "PRECIOUS"


def test_a_new_baseline_is_readable_by_everyone_who_clones_the_repository(
    tmp_path: Path,
) -> None:
    """``mkstemp`` creates 0600; a committed baseline only its author can read is wrong."""
    path = tmp_path / "baseline.json"
    BaselineStore(path).save(Baseline(captured_at=STAMP, values={"routine.X": 1.0}))
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_replacing_a_baseline_keeps_the_permissions_it_already_had(tmp_path: Path) -> None:
    """An operator who tightened or loosened the file did so on purpose."""
    path = tmp_path / "baseline.json"
    store = BaselineStore(path)
    store.save(Baseline(captured_at=STAMP, values={"routine.X": 1.0}))
    path.chmod(0o640)
    store.save(Baseline(captured_at=STAMP, values={"routine.X": 2.0}))
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_a_path_holding_a_null_byte_is_reported_as_unreachable(tmp_path: Path) -> None:
    """``os.lstat`` raises ``ValueError`` for it, not ``OSError`` -- the reason that branch
    exists, and nothing exercised it despite the comment beside it saying "(measured)".

    Outcomes hold either way at every call site, but without it ``doctor`` relabels the fault
    from the operator's environment to a Gate defect.
    """
    baseline, issues = BaselineStore(Path(f"{tmp_path}/bad\x00name.json")).load(specs())
    assert baseline is None
    assert "cannot be reached" in issues[0].message


def test_the_written_document_is_indented_for_a_readable_diff(tmp_path: Path) -> None:
    """The baseline is committed, so a one-line document makes every capture an unreadable
    diff. Nothing pinned the indent, and a mutant dropping it changed no assertion."""
    path = tmp_path / "baseline.json"
    BaselineStore(path).save(Baseline(captured_at=STAMP, values={"routine.X": 1.0, "file.Y": 2.0}))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 4
    assert any(line.startswith("    ") for line in lines)


def test_a_shared_baseline_reached_through_a_symlink_is_written_through(tmp_path: Path) -> None:
    """One baseline, several repositories: a symlinked ``baseline.file`` is a working setup.

    Measured on the version before this one: ``os.replace`` replaced the *link* with a regular
    file, so the shared baseline was never updated and the link was gone -- and because the
    mode came from ``os.lstat``, which reports a symlink as ``0777`` on Linux, the new file
    landed **world-writable inside a repository**, where any local user could move the gate's
    limits.
    """
    shared = tmp_path / "elsewhere"
    shared.mkdir()
    real = shared / "team.baseline.json"
    real.write_text('{"version": 1, "captured_at": "old", "values": {}}\n', encoding="utf-8")
    real.chmod(0o600)
    link = tmp_path / "baseline.json"
    link.symlink_to(real)
    BaselineStore(link).save(Baseline(captured_at=STAMP, values={"routine.X": 4.0}))
    assert link.is_symlink()
    assert "routine.X" in real.read_text(encoding="utf-8")
    assert stat.S_IMODE(real.stat().st_mode) == 0o600
    assert not list(shared.glob("*.tmp"))


def test_a_real_write_failure_is_reported_with_the_error_the_system_gave(
    tmp_path: Path,
) -> None:
    """Closing the scratch descriptor twice replaced every diagnosis with ``EBADF``.

    ``RLIMIT_FSIZE`` produces a genuine ``OSError(27)`` with no mocking at all; the same shape
    covers the realistic failures -- ``ENOSPC`` and ``EDQUOT`` -- and all of them were being
    reported as "Bad file descriptor", which sends the operator to look for a Gate defect.
    """
    script = tmp_path / "write.py"
    script.write_text(
        "import resource, sys\n"
        "from pathlib import Path\n"
        "from scitools_hook.models.baseline import Baseline\n"
        "from scitools_hook.runner.baseline_store import BaselineStore\n"
        "from scitools_hook.errors import ConfigError\n"
        "resource.setrlimit(resource.RLIMIT_FSIZE, (200, 200))\n"
        "big = Baseline(captured_at='x', values={f'routine.M{i}': float(i) for i in range(400)})\n"
        "try:\n"
        "    BaselineStore(Path(sys.argv[1])).save(big)\n"
        "    print('no error')\n"
        "except ConfigError as exc:\n"
        "    print(exc.message)\n",
        encoding="utf-8",
    )
    done = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "baseline.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "File too large" in done.stdout
    assert "Bad file descriptor" not in done.stdout


def test_the_scratch_file_is_created_beside_its_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.replace`` is atomic only within one filesystem; across one it raises ``EXDEV``.

    A ``/tmp`` on tmpfs is the ordinary way to meet that boundary, so putting the scratch file
    anywhere but the destination's own directory turns the atomic write into a failure -- or,
    worse, a partial one on a system that falls back to copying.
    """
    seen: list[Path] = []
    real_replace = os.replace

    def watched(src: object, dst: object) -> None:
        seen.append(Path(str(src)))
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", watched)
    target = tmp_path / "nested" / "baseline.json"
    BaselineStore(target).save(Baseline(captured_at=STAMP, values={"routine.X": 1.0}))
    assert seen
    assert seen[0].parent == target.parent


def test_an_unreadable_regular_baseline_is_reported(tmp_path: Path) -> None:
    """ "unreadable -> issue" is the task's literal wording, and the plainest case of it."""
    path = tmp_path / "baseline.json"
    path.write_text('{"version": 1, "captured_at": "x", "values": {}}', encoding="utf-8")
    path.chmod(0o000)
    try:
        baseline, issues = BaselineStore(path).load(specs())
    finally:
        path.chmod(0o644)
    assert baseline is None
    assert "cannot be read" in issues[0].message


def test_a_baseline_under_a_path_that_is_a_file_is_reported(tmp_path: Path) -> None:
    """``ENOTDIR`` -- the commonest ``baseline.file`` typo -- must not read as absence."""
    parent = tmp_path / "not-a-directory"
    parent.write_text("", encoding="utf-8")
    baseline, issues = BaselineStore(parent / "baseline.json").load(specs())
    assert baseline is None
    assert "cannot be reached" in issues[0].message


def test_a_refusal_is_not_wrapped_a_second_time(tmp_path: Path) -> None:
    """The kind refusal already names the fault; re-wrapping it buries that under a generic
    "could not be written", which is what the outcome guard says for an *unforeseen* failure."""
    path = tmp_path / "baseline.json"
    os.mkfifo(path)
    with pytest.raises(ConfigError) as raised:
        BaselineStore(path).save(Baseline(captured_at=STAMP, values={}))
    assert "is not a regular file" in raised.value.message
    assert "could not be written" not in raised.value.message


def test_a_scratch_descriptor_is_not_leaked_when_the_stream_cannot_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mkstemp`` hands back a raw descriptor, and only the stream can close it afterwards.

    If wrapping it fails, nothing else owns it -- and a Gate that leaks one descriptor per
    failed capture eventually cannot open anything at all. Counted rather than asserted
    indirectly, because a leak has no other visible effect.
    """
    fds = Path("/proc/self/fd")
    if not fds.is_dir():
        pytest.skip("descriptor counting needs /proc")

    def refuses(*args: object, **kwargs: object) -> None:
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(os, "fdopen", refuses)
    before = len(list(fds.iterdir()))
    with pytest.raises(ConfigError):
        BaselineStore(tmp_path / "baseline.json").save(
            Baseline(captured_at=STAMP, values={"routine.X": 1.0})
        )
    assert len(list(fds.iterdir())) == before
