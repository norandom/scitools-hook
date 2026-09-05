"""The fixture-backed Understand seam: ``SCITOOLS_HOOK_FAKE_UNDERSTAND`` (task 8.2).

These two adapters are what an end-to-end test, or an agent developing the Gate without a
license, runs against. Their whole value is that they answer *from files*, so a test can
re-point the variable at a second directory and watch a violating project become a fixed one
without touching the Gate.

The property under test throughout is the one a fake is most likely to lose: **a fixture that
is not there must fail loudly.** An adapter that answered an empty document for a missing
``snapshot.after.json`` would produce a complete, green, entirely fictional run -- the exact
silent-green failure this project keeps finding -- so every absent fixture is an error naming
the file it wanted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.fake import (
    FAKE_VAR,
    FixtureApiRunner,
    FixtureUndCli,
    fake_directory,
    fixture_env,
    fixture_problem,
)
from scitools_hook.understand.und_cli import (
    ALL,
    UndCli,
)


def write(directory: Path, name: str, document: object) -> Path:
    """Write one fixture file into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _make(target: Path, shape: str) -> None:
    """Create ``target`` as a directory, a FIFO or a dangling symlink."""
    if shape == "directory":
        target.mkdir()
    elif shape == "fifo":
        os.mkfifo(target)
    else:
        target.symlink_to(target.parent / "nowhere")


def snapshot_document(side: str) -> dict[str, object]:
    """A minimal ``snapshot`` answer, identifiable by side."""
    return {"side": side, "entities": [], "edges": [], "arch_nodes": []}


# --- FixtureApiRunner: which file answers which request -------------------------


def _too_deep(*_args: object, **_kwargs: object) -> object:
    """Stand in for a document the parser cannot descend, so the threshold is not asserted."""
    raise RecursionError("maximum recursion depth exceeded")


def test_a_sided_request_is_answered_by_the_file_of_that_side(tmp_path: Path) -> None:
    """``<op>.<side>.json`` is the whole point: before and after must differ."""
    write(tmp_path, "snapshot.before.json", snapshot_document("before"))
    write(tmp_path, "snapshot.after.json", snapshot_document("after"))
    runner = FixtureApiRunner(tmp_path)
    assert runner.run("snapshot", {"db": "x.und", "side": "before"})["side"] == "before"
    assert runner.run("snapshot", {"db": "x.und", "side": "after"})["side"] == "after"


def test_a_request_without_a_side_is_answered_by_the_unsided_file(tmp_path: Path) -> None:
    """``catalogue``, ``impact`` and ``graphs`` carry no side; they read ``<op>.json``."""
    write(tmp_path, "catalogue.json", {"metrics": {"python function": ["CyclomaticStrict"]}})
    answer = FixtureApiRunner(tmp_path).run("catalogue", {"kinds": ["python function"]})
    assert answer == {"metrics": {"python function": ["CyclomaticStrict"]}}


def test_a_sided_request_falls_back_to_the_unsided_file(tmp_path: Path) -> None:
    """One file serves both sides when a fixture's two sides are the same."""
    write(tmp_path, "snapshot.json", snapshot_document("either"))
    runner = FixtureApiRunner(tmp_path)
    assert runner.run("snapshot", {"side": "before"})["side"] == "either"
    assert runner.run("snapshot", {"side": "after"})["side"] == "either"


def test_the_sided_file_wins_over_the_unsided_one(tmp_path: Path) -> None:
    """A directory holding both must not silently answer with the generic document."""
    write(tmp_path, "snapshot.json", snapshot_document("either"))
    write(tmp_path, "snapshot.after.json", snapshot_document("after"))
    assert FixtureApiRunner(tmp_path).run("snapshot", {"side": "after"})["side"] == "after"


def test_ping_is_answered_without_any_fixture_file(tmp_path: Path) -> None:
    """The probe asks what version is there; a fixture directory answers for itself."""
    answer = FixtureApiRunner(tmp_path).run("ping", {})
    assert answer["version"]
    assert answer["python"]


def test_a_missing_fixture_names_every_file_it_looked_for(tmp_path: Path) -> None:
    """An absent snapshot must fail, never answer an empty project that passes every rule."""
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureApiRunner(tmp_path).run("snapshot", {"side": "after"})
    message = raised.value.message
    assert "snapshot.after.json" in message
    assert "snapshot.json" in message
    assert str(tmp_path) in message


def test_a_fixture_that_is_not_json_is_an_error_naming_the_file(tmp_path: Path) -> None:
    """A half-edited fixture is a broken test, and it says which file to look at."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "catalogue.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureApiRunner(tmp_path).run("catalogue", {"kinds": []})
    assert "catalogue.json" in raised.value.message


def test_a_fixture_that_is_not_an_object_is_an_error(tmp_path: Path) -> None:
    """Every worker answer is a JSON object; a list would fail much later and much worse."""
    write(tmp_path, "catalogue.json", ["not", "an", "object"])
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureApiRunner(tmp_path).run("catalogue", {"kinds": []})
    assert "catalogue.json" in raised.value.message


def test_every_request_is_recorded_for_the_pipeline_tests_that_follow(tmp_path: Path) -> None:
    """8.3 asserts which sides were extracted; the seam has to be able to say."""
    write(tmp_path, "snapshot.json", snapshot_document("either"))
    runner = FixtureApiRunner(tmp_path)
    runner.run("snapshot", {"side": "before"})
    runner.run("snapshot", {"side": "after"})
    assert [call.op for call in runner.calls] == ["snapshot", "snapshot"]
    assert [call.request.get("side") for call in runner.calls] == ["before", "after"]


# --- FixtureUndCli: analyze, and the commands a run still makes ------------------


def test_analyze_answers_with_the_parse_errors_the_fixture_records(tmp_path: Path) -> None:
    """Requirement 2.6 is exercised end to end only if the seam can report a parse error."""
    write(
        tmp_path,
        "analyze.json",
        {
            "parse_errors": [{"path": "src/broken.py", "line": 3, "message": "expected ']'"}],
            "warnings": 1,
            "seconds": 0.5,
        },
    )
    result = FixtureUndCli(tmp_path).analyze(tmp_path / "after.und", ALL)
    assert [str(error.path) for error in result.parse_errors] == ["src/broken.py"]
    assert result.warnings == 1


def test_analyze_without_a_fixture_reports_a_clean_analysis(tmp_path: Path) -> None:
    """A fixture project that parses is the common case and needs no file to say so."""
    result = FixtureUndCli(tmp_path).analyze(tmp_path / "after.und", [])
    assert result.parse_errors == []
    assert result.warnings == 0


def test_analyze_refuses_a_fixture_it_cannot_read_as_a_result(tmp_path: Path) -> None:
    """A malformed ``analyze.json`` is a broken fixture, not an analysis with no errors."""
    write(tmp_path, "analyze.json", {"parse_errors": "not a list", "seconds": 0.0})
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(tmp_path).analyze(tmp_path / "after.und", ALL)
    assert "analyze.json" in raised.value.message


def test_creating_a_database_makes_the_directory_a_real_one_would(tmp_path: Path) -> None:
    """A ``.und`` is a directory; the manager checks for it, so the seam must create it."""
    db = tmp_path / "cache" / "after.und"
    FixtureUndCli(tmp_path).create(db, ["python"])
    assert db.is_dir()


def test_the_seam_reports_a_version_and_a_valid_license(tmp_path: Path) -> None:
    """``doctor`` prints both, and a run must not stop on a license the seam cannot have."""
    cli = FixtureUndCli(tmp_path)
    assert cli.version()
    assert cli.license_status().ok


def test_codecheck_answers_with_the_fixture_csv_when_there_is_one(tmp_path: Path) -> None:
    """Requirement 6.9 through the seam: the violations file is a fixture like any other."""
    csv = tmp_path / "codecheck.csv"
    tmp_path.mkdir(parents=True, exist_ok=True)
    csv.write_text("Violation,File\n", encoding="utf-8")
    found = FixtureUndCli(tmp_path).codecheck(tmp_path / "a.und", "Sandbox", [], tmp_path / "out")
    assert found == csv


def test_codecheck_without_a_fixture_fails_rather_than_reporting_no_violations(
    tmp_path: Path,
) -> None:
    """A missing CSV must never read as "no violations found"."""
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(tmp_path).codecheck(tmp_path / "a.und", "Sandbox", [], tmp_path / "out")
    assert "codecheck.csv" in raised.value.message


# --- the environment the seam presents ------------------------------------------


def test_the_fixture_environment_names_the_directory_it_came_from(tmp_path: Path) -> None:
    """``doctor`` prints the install directory; under the seam that is the fixture directory."""
    env = fixture_env(tmp_path)
    assert env.home == tmp_path
    assert env.version
    assert FAKE_VAR in env.source


def test_the_variable_is_read_only_when_it_names_a_directory() -> None:
    """An unset or blank variable is off; a set one is the seam, whatever else is configured."""
    assert fake_directory({}) is None
    assert fake_directory({FAKE_VAR: "   "}) is None
    assert fake_directory({FAKE_VAR: "/fixtures/violating"}) == Path("/fixtures/violating")


# --- the near-miss trap, the side whitelist, and the base adapters --------------


def test_an_unrelated_file_does_not_block_a_clean_analysis(tmp_path: Path) -> None:
    """The near-miss rule must not fire on the fixtures a directory legitimately holds."""
    write(tmp_path, "snapshot.after.json", snapshot_document("after"))
    write(tmp_path, "catalogue.json", {"metrics": {}})
    assert FixtureUndCli(tmp_path).analyze(tmp_path / "after.und", ALL).warnings == 0


def test_a_side_the_models_do_not_know_is_not_used_to_build_a_filename(tmp_path: Path) -> None:
    """Only ``before`` and ``after`` name a file; anything else falls back to the plain one."""
    write(tmp_path, "snapshot.json", snapshot_document("either"))
    write(tmp_path, "snapshot.sideways.json", snapshot_document("sideways"))
    assert FixtureApiRunner(tmp_path).run("snapshot", {"side": "sideways"})["side"] == "either"


def test_the_fixture_adapters_carry_everything_their_real_bases_establish(
    tmp_path: Path,
) -> None:
    """A fixture adapter stands in for the real one *everywhere*, inherited members included.

    Both classes are dataclasses over a hand-written base, so the generated ``__init__``
    replaces the base's. Without initialising the base, every attribute it sets is missing and
    any member these classes do not override fails with ``AttributeError``. Both bases are
    fully overridden today, so this asserts the invariant rather than a live bug: the next
    member added to either one is what it protects, and a member added during this task's own
    review is what revealed the gap.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    real_cli = UndCli(fixture_env(tmp_path), NullCommandLog())
    real_api = ApiRunner(fixture_env(tmp_path), NullCommandLog())
    assert set(vars(real_cli)) <= set(vars(FixtureUndCli(tmp_path)))
    assert set(vars(real_api)) <= set(vars(FixtureApiRunner(tmp_path)))


def test_the_seam_refuses_a_path_that_is_not_a_directory_of_fixtures(tmp_path: Path) -> None:
    """``fixture_problem`` is what stops a mistyped variable presenting as a healthy install."""
    plain = tmp_path / "file"
    plain.write_text("", encoding="utf-8")
    assert "no such directory" in fixture_problem(tmp_path / "absent")
    assert "not a directory" in fixture_problem(plain)
    assert fixture_problem(tmp_path) == ""


# --- every fixture failure leaves as an AnalysisFailedError, never untyped -------


@pytest.mark.parametrize(
    ("mode", "phrase"),
    [(0o111, "cannot be read"), (0o000, "cannot be reached")],
)
def test_a_fixture_directory_that_cannot_be_read_fails_in_the_module_s_own_terms(
    tmp_path: Path, mode: int, phrase: str
) -> None:
    """``iterdir`` raises ``PermissionError`` on an unreadable directory (measured).

    That would leave this module by a door its contract does not have -- every fixture failure
    is an ``AnalysisFailedError`` carrying the seam hint -- and the near-miss check introduced
    it on a path that previously could not raise at all.

    The two modes reach the fault by different routes and both are pinned, because ``0o000``
    stopped exercising the listing once absence began to be classified: with no search
    permission the ``analyze.json`` name itself cannot even be stat-ed, so it is refused as
    unreachable before any listing happens; only ``0o111`` -- search but not read -- gets far
    enough to call ``iterdir``. Asserting the *reason* rather than the exception type is what
    keeps the handler distinguishable from the module-wide outcome guard, which would also
    produce an ``AnalysisFailedError`` and a far vaguer message.
    """
    directory = tmp_path / f"sealed-{mode:o}"
    directory.mkdir()
    directory.chmod(mode)
    try:
        with pytest.raises(AnalysisFailedError) as raised:
            FixtureUndCli(directory).analyze(directory / "after.und", ALL)
    finally:
        directory.chmod(0o755)
    assert str(directory) in raised.value.message
    assert FAKE_VAR in (raised.value.hint or "")
    assert phrase in raised.value.message


def test_a_database_that_cannot_be_created_fails_in_the_module_s_own_terms(
    tmp_path: Path,
) -> None:
    """``mkdir`` raises ``OSError`` sub-types; the seam must not leak one either."""
    blocked = tmp_path / "occupied"
    blocked.write_text("not a directory", encoding="utf-8")
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(tmp_path).create(blocked / "after.und", ["python"])
    assert "creating the fixture database" in raised.value.message
    assert "NotADirectoryError" in raised.value.message
    assert FAKE_VAR in (raised.value.hint or "")


# --- the near-miss rule, measured in both directions ----------------------------


@pytest.mark.parametrize(
    "name",
    ["analyse.json", "anaylze.json", "analyz.json", "analze.json", "Analyze.JSON", "ANALYSE.JSON"],
)
def test_a_misspelt_analysis_fixture_is_refused_whatever_the_typo(
    tmp_path: Path, name: str
) -> None:
    """A misspelling of the word, carrying the right extension: unambiguously the fixture.

    ``anaylze`` is the transposition, ``analyse`` the British spelling, ``Analyze.JSON`` the
    case error that a case-sensitive filesystem turns into a missing file. All of them mean
    "an analysis fixture that will never be read", which is the silent green this refuses.
    """
    directory = tmp_path / name.replace(".", "-")
    write(directory, name, {"parse_errors": [], "seconds": 0.0})
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(directory).analyze(directory / "after.und", ALL)
    assert name in raised.value.message
    assert "analyze.json" in raised.value.message


ALLOWED_ALONGSIDE = [
    "analyze.jsonl",
    "analyze.json5",
    "analyze.json~",
    ".analyze.json",
    "analyze.csv",
    "analyze.log",
    "analyze.md",
    "analyze.py",
    "analyze.txt",
    "analyze.yaml",
    "analyze.json.bak",
    "analyze-old.json",
    "analysis.json",
    "catalogue.json",
    "analyzed_notes.md",
]
"""Names a fixture directory may hold that are *not* misspellings of ``analyze.json``.

A different extension means a different file on purpose (``.jsonl`` is JSON Lines, ``.json~``
an editor backup, ``.json.bak`` a copy); a leading dot means a hidden file; ``analysis.json``
is a different word, three edits away. An earlier whole-filename rule at distance 3 refused
nine of these.
"""


def test_legitimate_fixture_content_is_never_mistaken_for_a_typo(tmp_path: Path) -> None:
    """A directory holding notes, subdirectories, backups and near-named data analyses cleanly.

    Asserted over the adversarial list rather than over names chosen to pass: the previous
    rule was justified by a corpus its author picked, which is a guess about the population
    dressed as a measurement.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    assert len(ALLOWED_ALONGSIDE) >= 15, "an empty or shrunken corpus would pass vacuously"
    for name in ALLOWED_ALONGSIDE:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    for directory in ("analyzer", "analysis", "analyze"):
        (tmp_path / directory).mkdir()
    result = FixtureUndCli(tmp_path).analyze(tmp_path / "after.und", ALL)
    assert result.parse_errors == []


def test_a_different_word_is_not_treated_as_a_misspelling(tmp_path: Path) -> None:
    """Pins the far edge of the threshold: ``analysis`` is three edits from ``analyze``.

    Widening the rule by one would swallow it, so this is what stops the constant drifting
    upwards and taking the fifteen names above with it.
    """
    write(tmp_path, "analysis.json", {"parse_errors": [], "seconds": 0.0})
    assert FixtureUndCli(tmp_path).analyze(tmp_path / "after.und", None).warnings == 0


def test_a_different_extension_is_a_different_file_on_purpose(tmp_path: Path) -> None:
    """Pins the other half of the rule: the stem alone must never be enough to refuse."""
    write(tmp_path, "analyze.jsonl", {"parse_errors": [], "seconds": 0.0})
    assert FixtureUndCli(tmp_path).analyze(tmp_path / "after.und", None).parse_errors == []


# --- a fixture must be a file, not merely a name that exists --------------------


@pytest.mark.parametrize("shape", ["directory", "fifo", "dangling"])
def test_a_fixture_name_that_is_taken_but_unusable_is_refused_by_kind(
    tmp_path: Path, shape: str
) -> None:
    """ "no fixture answers" is an absence claim, and the name is plainly occupied.

    It is also refused rather than skipped, which is the half that matters: with a usable
    ``snapshot.json`` beside it, a broken ``snapshot.before.json`` would otherwise fall through
    and the generic file would answer for the wrong side without saying so.
    """
    _make(tmp_path / "snapshot.after.json", shape)
    write(tmp_path, "snapshot.json", snapshot_document("either"))
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureApiRunner(tmp_path).run("snapshot", {"side": "after"})
    assert "snapshot.after.json" in raised.value.message
    assert "no fixture answers" not in raised.value.message


def test_a_directory_named_like_the_violations_csv_is_not_one(tmp_path: Path) -> None:
    """The same rule for CodeCheck: a name that exists is not a file that can be parsed."""
    (tmp_path / "codecheck.csv").mkdir(parents=True)
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(tmp_path).codecheck(tmp_path / "a.und", "Sandbox", [], tmp_path / "out")
    assert "codecheck.csv" in raised.value.message


def test_a_fixture_directory_this_user_cannot_enter_is_refused_by_name(tmp_path: Path) -> None:
    """``exists()`` and ``is_dir()`` both answer True for a ``chmod 000`` directory (measured).

    The sibling check on the cache root documents this exact trap; without it here the seam
    presents an unreadable directory as a healthy installation.
    """
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    sealed.chmod(0o000)
    try:
        assert "cannot be read" in fixture_problem(sealed)
    finally:
        sealed.chmod(0o755)


def test_the_seam_reports_the_value_the_operator_actually_set(tmp_path: Path) -> None:
    """Judging a sanitised value and echoing it hides the whitespace that caused the fault."""
    padded = f"  {tmp_path / 'nowhere'}  "
    found = fake_directory({FAKE_VAR: padded})
    assert found is not None
    assert str(found) == padded
    assert padded in fixture_problem(found)


@pytest.mark.parametrize("shape", ["directory", "fifo", "dangling"])
def test_a_misspelt_fixture_is_refused_whatever_kind_of_thing_it_is(
    tmp_path: Path, shape: str
) -> None:
    """The guard must not inspect the entry's kind, because all four shapes are equally unread.

    ``is_file()`` is ``False`` for a directory, a FIFO **and** a dangling symlink, so filtering
    the listing on it let three of the four hostile shapes walk straight past the check and
    produce the silent green it exists to prevent. Measured, all three, before this test.
    """
    directory = tmp_path / shape
    directory.mkdir()
    target = directory / "analyse.json"
    _make(target, shape)
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(directory).analyze(directory / "after.und", ALL)
    assert "analyse.json" in raised.value.message


@pytest.mark.parametrize(
    ("shape", "phrase"),
    [
        ("directory", "is not a regular file"),
        ("fifo", "is not a regular file"),
        ("dangling", "is a symbolic link that leads nowhere"),
    ],
)
def test_an_analysis_fixture_that_is_not_a_readable_file_is_refused(
    tmp_path: Path, shape: str, phrase: str
) -> None:
    """A name that is taken but unusable must never read as "absent, therefore clean".

    A dangling ``analyze.json`` is the sharpest case: ``exists()`` follows the link and
    answers ``False``, so the one absence this module treats as an answer was produced by a
    file that is very much there.
    """
    directory = tmp_path / shape
    directory.mkdir()
    _make(directory / "analyze.json", shape)
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(directory).analyze(directory / "after.und", ALL)
    assert "analyze.json" in raised.value.message
    # The message is the point, not merely the raise: without the explicit kind check the
    # generic reader still refuses, but it reports "no fixture answers the 'analyze'
    # operation: looked for .../analyze.json" about a file that is plainly sitting there.
    # The reason comes from the classifier, so the unusable shapes stay distinguishable.
    assert phrase in raised.value.message


def test_a_fixture_nested_too_deeply_is_refused_rather_than_reported_as_a_gate_defect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``json.loads`` raises ``RecursionError``, which is not a ``ValueError``.

    Untyped it left this module and reached ``doctor`` as an internal defect (exit 70) instead
    of as the broken fixture it is -- the same fault class already mapped for ``tomllib``.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "catalogue.json").write_text("[[[]]]", encoding="utf-8")
    monkeypatch.setattr(json, "loads", _too_deep)
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureApiRunner(tmp_path).run("catalogue", {"kinds": []})
    assert "too deeply" in raised.value.message


@pytest.mark.parametrize(("mode", "missing"), [(0o444, "search"), (0o111, "read")])
def test_a_fixture_directory_missing_either_permission_bit_is_refused(
    tmp_path: Path, mode: int, missing: str
) -> None:
    """Both bits are the question, and ``chmod 000`` alone cannot tell them apart.

    Measured: ``0o444`` grants read but not search, ``0o111`` search but not read, and either
    one on its own makes the fixtures unreachable. Every earlier test used ``0o000``, which
    removes both, so dropping either bit from the check survived.
    """
    sealed = tmp_path / f"mode-{mode:o}"
    sealed.mkdir()
    sealed.chmod(mode)
    try:
        assert "cannot be read" in fixture_problem(sealed), missing
    finally:
        sealed.chmod(0o755)


def test_no_fixture_read_escapes_by_a_type_nobody_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This module promises one exception type, so it is guarded on that outcome.

    ``MemoryError`` from a file larger than available memory is neither an ``OSError``, a
    ``ValueError`` nor a ``RecursionError``; it left the module untyped and reached the CLI as
    an internal defect for what was plainly a broken fixture. Injected, so the guard is pinned
    rather than the one exception that exposed it.
    """
    write(tmp_path, "catalogue.json", {"metrics": {}})

    def exhausted(*args: object, **kwargs: object) -> str:
        raise MemoryError("cannot allocate")

    monkeypatch.setattr(Path, "read_text", exhausted)
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureApiRunner(tmp_path).run("catalogue", {"kinds": []})
    assert "MemoryError" in raised.value.message
    assert FAKE_VAR in (raised.value.hint or "")


def test_a_near_miss_is_found_even_when_other_fixtures_sort_before_it(tmp_path: Path) -> None:
    """The scan must examine every entry, not stop at the first name that is not a typo.

    ``catalogue.json`` sorts before ``reanalyze.json``, so an early return on the first
    non-match would walk straight past the misspelt fixture in any realistic directory.
    """
    write(tmp_path, "catalogue.json", {"metrics": {}})
    write(tmp_path, "reanalyze.json", {"parse_errors": [], "seconds": 0.0})
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(tmp_path).analyze(tmp_path / "after.und", ALL)
    assert "reanalyze.json" in raised.value.message


@pytest.mark.parametrize(
    ("shape", "phrase"),
    [
        ("directory", "is not a regular file"),
        ("fifo", "is not a regular file"),
        ("dangling", "is a symbolic link that leads nowhere"),
    ],
)
def test_the_violations_export_is_refused_by_kind_with_a_reason_that_is_true(
    tmp_path: Path, shape: str, phrase: str
) -> None:
    """A missing-file claim about a FIFO under exactly that name is misleading.

    The unusable shapes are told apart rather than collapsed, which is why ``_link_or_kind``
    distinguishes them at all: a dangling link and a wrong kind send the operator elsewhere.
    """
    directory = tmp_path / shape
    directory.mkdir()
    _make(directory / "codecheck.csv", shape)
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(directory).codecheck(directory / "a.und", "Sandbox", [], directory / "out")
    assert phrase in raised.value.message


@pytest.mark.parametrize("shape", ["dangling", "loop"])
def test_a_seam_pointing_at_a_link_that_leads_nowhere_says_so(tmp_path: Path, shape: str) -> None:
    """ "names no such directory" is wrong about a link that is plainly there.

    ``exists()`` follows the link and answers ``False``, which is the same answer it gives for
    a name nobody ever created -- and the operator fixes those two faults differently.
    """
    link = tmp_path / f"seam-{shape}"
    link.symlink_to(tmp_path / "nowhere" if shape == "dangling" else link)
    reported = fixture_problem(link)
    assert "no such directory" not in reported
    assert "nowhere" in reported or "leads nowhere" in reported or "cannot be reached" in reported


def test_a_seam_pointing_at_a_fifo_is_not_called_a_file(tmp_path: Path) -> None:
    """A FIFO is not "a file, not a directory of fixtures"; the reason has to be true."""
    fifo = tmp_path / "seam.fifo"
    os.mkfifo(fifo)
    assert "is not a directory" in fixture_problem(fifo)


def test_the_first_near_miss_reported_does_not_depend_on_the_directory_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two misspellings in one directory must always name the same one.

    Repeating the call cannot show this: ``iterdir`` order is arbitrary but *stable* within a
    filesystem, so the same wrong answer would come back every time. The order is injected
    instead, which is the only way to tell a sorted scan from one that reports whatever the
    filesystem happened to hand back first.
    """
    for name in ("reanalyze.json", "analyse.json"):
        write(tmp_path, name, {"parse_errors": [], "seconds": 0.0})
    shuffled = sorted(tmp_path.iterdir(), reverse=True)
    monkeypatch.setattr(Path, "iterdir", lambda self: iter(shuffled))
    with pytest.raises(AnalysisFailedError) as raised:
        FixtureUndCli(tmp_path).analyze(tmp_path / "after.und", ALL)
    assert "analyse.json" in raised.value.message
    assert "reanalyze.json" not in raised.value.message
