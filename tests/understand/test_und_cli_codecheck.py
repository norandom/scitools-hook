"""``und codecheck`` through the wrapper: the argv, and which CSV comes back (task 6.5).

``codecheck`` writes several CSVs and they are not interchangeable, so the export is chosen
by name and never by luck: the per-violation export when it is there, a lone export that is
still one of CodeCheck's own, and a refusal -- listing what it found, in sorted order --
for anything else. The output directory must be empty, because ``codecheck`` can exit 0
having written nothing and a reused directory would hand back the previous run. The stubbed
``und`` and the transcripts come from ``und_stub``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from und_stub import (
    RecordingLog,
    UndStub,
    assume_unsorted_readdir,
    cli,
    db_path,
    write_stub,
)

from scitools_hook.errors import AnalysisFailedError
from scitools_hook.understand.codecheck_sarif import RESULTS_SARIF
from scitools_hook.understand.und_cli import (
    CONFIG_HINT,
    EXPORT_PREFIX,
    VIOLATIONS_EXPORT,
    _list_file,
)


@pytest.fixture
def stub(tmp_path: Path) -> UndStub:
    """A stubbed ``und`` executable with an empty plan, ready to be scripted."""
    return write_stub(tmp_path)


@pytest.fixture
def log() -> RecordingLog:
    """A fresh recording command log (requirement 12.8)."""
    return RecordingLog(entries=[])


# --- codecheck --------------------------------------------------------------------


def test_codecheck_switches_precede_the_two_positional_arguments(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``und codecheck [-switches] <configuration> <output directory>``."""
    database = db_path(tmp_path)
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.csv": "Check ID,File\n"}}})
    cli(stub, log).codecheck(database, "Quick Check", [tmp_path / "a.py"], out_dir)
    assert stub.argv[:4] == ["-db", str(database), "codecheck", "-files"]
    assert stub.argv[-2:] == ["Quick Check", str(out_dir)]


def test_codecheck_file_list_is_not_at_prefixed(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-files`` documents a list file that "does not have to start with @"."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.csv": "Check ID,File\n"}}})
    cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    listing = stub.argv[stub.argv.index("-files") + 1]
    assert not listing.startswith("@")
    assert stub.lists[Path(listing).name] == f"{tmp_path / 'a.py'}\n"


def test_codecheck_returns_the_csv_it_found(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.csv": "Check ID,File\n"}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / f"{VIOLATIONS_EXPORT}.csv"


def test_codecheck_returns_the_sarif_when_the_build_wrote_one(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """8.0 writes ``results.sarif`` every run; the CSV the 6.5 reader wants is gone."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {RESULTS_SARIF: '{"runs": []}'}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / RESULTS_SARIF


def test_codecheck_prefers_the_sarif_over_the_plugin_csv_beside_it(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """8.0's default install writes both, and the plugin's CSV is not the 6.5 export.

    ``CodeCheckResultsByTable.csv`` begins with :data:`EXPORT_PREFIX`, so as the only CSV in
    the directory it would be taken for one of CodeCheck's own exports and read with columns
    it does not have. The SARIF is the documented report and wins before that can happen.
    """
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    RESULTS_SARIF: '{"runs": []}',
                    "CodeCheckResultsByTable.csv": "File,Violation\n",
                }
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / RESULTS_SARIF


def test_codecheck_still_refuses_an_output_directory_holding_neither(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A run that wrote no report at all is a failed run, not an inspection that found none."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"notes.txt": "nothing was checked\n"}}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "no csv file" in str(caught.value)


def test_codecheck_picks_the_per_violation_export_not_the_alphabetically_first(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``codecheck`` writes three exports; sorted() would hand back the files-tree one."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "CodeCheckResultByFile.csv": "File\n",
                    "CodeCheckResultByTable.csv": "Table\n",
                    f"{VIOLATIONS_EXPORT}.csv": "Violation\n",
                }
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / f"{VIOLATIONS_EXPORT}.csv"
    assert found != sorted(out_dir.glob("*.csv"))[0]


def test_codecheck_refuses_to_guess_between_several_exports(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Without the per-violation export, picking one of several would hand back a schema."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "CodeCheckResultByFile.csv": "File\n",
                    "CodeCheckResultByTable.csv": "Table\n",
                }
            }
        }
    )
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    complaint = str(caught.value)
    assert "CodeCheckResultByFile.csv" in complaint
    assert "CodeCheckResultByTable.csv" in complaint


def test_the_export_names_are_the_ones_und_is_built_with() -> None:
    """Pinned to the literal, not to itself: a drifted value would take the wrong file.

    ``test_contract_the_fixture_headers_and_export_names_are_compiled_into_und`` in
    ``test_codecheck_runner.py`` checks the same strings against the executable's own bytes,
    but that test needs an Understand install; this one holds on any machine.
    """
    assert VIOLATIONS_EXPORT == "CodeCheckResultByViolation"
    assert EXPORT_PREFIX == "CodeCheckResult"


def test_codecheck_finds_an_export_whose_extension_is_upper_case(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``glob("*.csv")`` is case-sensitive on Linux, so a ``.CSV`` export would be invisible."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.CSV": "Check ID,File\n"}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / f"{VIOLATIONS_EXPORT}.CSV"


def test_codecheck_ignores_the_reports_that_are_not_csv(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``-html`` and the compliance reports drop other files beside the CSV exports."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "CodeCheckResultByFile.csv": "File\n",
                    "index.html": "<html></html>",
                    "summary.pdf": "%PDF",
                }
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "CodeCheckResultByFile.csv"


def test_codecheck_recognises_the_per_violation_export_whatever_its_case(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Understand also runs on a case-insensitive filesystem, where the name may arrive
    lower-cased. Written beside another export, a stem compared case-sensitively would not
    match at all and the wrapper would refuse to choose."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "codecheckresultbyviolation.csv": "Violation\n",
                    "CodeCheckResultByFile.csv": "File\n",
                }
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "codecheckresultbyviolation.csv"


def test_codecheck_recognises_a_lone_export_whatever_its_case(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The same for the fallback's scope check: the prefix is a name, not a byte sequence."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"codecheckresultbytable.csv": "Table\n"}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "codecheckresultbytable.csv"


def test_codecheck_accepts_a_lone_export_that_is_still_one_of_codechecks_own(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"CodeCheckResultByTable.csv": "Table\n"}}})
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "CodeCheckResultByTable.csv"


def test_codecheck_requires_an_export_name_to_start_with_the_prefix(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``startswith``, not ``in``: a name that merely contains the prefix is somebody else's."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"stale-CodeCheckResultByTable.csv": "T\n"}}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "stale-CodeCheckResultByTable.csv" in str(caught.value)


def test_codecheck_refuses_a_lone_csv_that_is_not_a_codecheck_export(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """One file is not one *right* file; an unrelated CSV would be read as violations."""
    out_dir = tmp_path / "cc"
    stub.plan({"codecheck": {"write": {"results.csv": "whatever\n"}}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "results.csv" in str(caught.value)


def test_codecheck_does_not_read_an_unexaminable_entry_as_absent(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``Path.is_file`` answers False for every ``OSError``, turning "cannot tell" into "no".

    Measured: a symlink loop named ``CodeCheckResultByViolation.csv`` makes ``is_file()``
    answer False while ``stat`` raises ELOOP. The per-violation export then vanishes from
    the listing, the lone-export fallback hands back the by-table schema instead, and
    nothing says a word — which is precisely what that fallback's docstring promises it
    prevents.
    """
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "symlink_loop": [f"{VIOLATIONS_EXPORT}.csv"],
                "write": {"CodeCheckResultByTable.csv": "Table\n"},
            }
        }
    )
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert f"{VIOLATIONS_EXPORT}.csv" in str(caught.value)


def test_codecheck_does_not_mistake_a_directory_for_an_export(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A directory named ``CodeCheckResultByViolation.csv`` is not the per-violation export."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "mkdir": [f"{VIOLATIONS_EXPORT}.csv"],
                "write": {"CodeCheckResultByTable.csv": "Table\n"},
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "CodeCheckResultByTable.csv"


def test_codecheck_ignores_a_hidden_file(stub: UndStub, log: RecordingLog, tmp_path: Path) -> None:
    """``und`` writes no dotfiles, so one in the output directory belongs to something else."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    f".{VIOLATIONS_EXPORT}.csv": "hidden\n",
                    "CodeCheckResultByTable.csv": "Table\n",
                }
            }
        }
    )
    found = cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert found == out_dir / "CodeCheckResultByTable.csv"


def test_codecheck_lists_the_exports_it_refused_in_sorted_order(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """The message states an order, so two runs over one directory must read the same."""
    out_dir = tmp_path / "cc"
    written = ["Zulu", "Alpha", "Mike", "Bravo"]
    stub.plan({"codecheck": {"write": {f"CodeCheckResultBy{name}.csv": "x\n" for name in written}}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assume_unsorted_readdir(out_dir)
    expected = ", ".join(sorted(f"CodeCheckResultBy{name}.csv" for name in written))
    assert expected in str(caught.value)


def test_codecheck_lists_a_stale_directorys_contents_in_sorted_order(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "cc"
    out_dir.mkdir()
    stale = ["zulu.txt", "alpha.txt", "mike.txt", "bravo.txt"]
    for name in stale:
        (out_dir / name).write_text("stale\n", encoding="utf-8")
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assume_unsorted_readdir(out_dir)
    assert ", ".join(sorted(stale)) in str(caught.value)


def test_codecheck_refuses_an_output_directory_holding_anything_at_all(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """An HTML report from an earlier run is the same evidence as a stale CSV."""
    out_dir = tmp_path / "cc"
    out_dir.mkdir()
    (out_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "index.html" in str(caught.value)
    assert stub.calls == []


def test_codecheck_reports_an_output_directory_it_cannot_create_as_an_analysis_failure(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A file where the directory should be raises ``FileExistsError`` out of ``mkdir``.

    That is an ``OSError``, which no caught-error tuple in the package expects, so it would
    leave the typed envelope every other failure here travels in.
    """
    blocked = tmp_path / "cc"
    blocked.write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], blocked)
    assert str(blocked) in str(caught.value)


def test_codecheck_reports_an_unreadable_output_directory_as_an_analysis_failure(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """Listing an existing directory raises ``PermissionError``, an ``OSError`` like any other."""
    out_dir = tmp_path / "cc"
    out_dir.mkdir(mode=0o000)
    try:
        with pytest.raises(AnalysisFailedError) as caught:
            cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    finally:
        out_dir.chmod(0o755)
    assert str(out_dir) in str(caught.value)


@pytest.mark.parametrize(
    "call",
    ["analyze", "remove_files"],
    ids=["analyze", "remove-files"],
)
def test_a_name_that_cannot_be_encoded_is_typed_for_every_list_file_caller(
    stub: UndStub, log: RecordingLog, tmp_path: Path, call: str
) -> None:
    """``git`` decodes names with ``surrogateescape``; ``write_text`` will not take one.

    ``UnicodeEncodeError`` is a ``ValueError`` — neither ``GateError`` nor ``OSError`` — so
    it is caught nowhere. The guard belongs in ``_list_file`` rather than in one caller,
    because ``analyze`` and ``remove_files`` write the same list file from the same kind of
    name.
    """
    stub.plan({call.split("_")[0]: {}})
    wrapper = cli(stub, log)
    unencodable = [Path("/src/caf\udce9.c")]
    with pytest.raises(AnalysisFailedError) as caught:
        if call == "analyze":
            wrapper.analyze(db_path(tmp_path), unencodable)
        else:
            wrapper.remove_files(db_path(tmp_path), unencodable)
    assert "list file" in str(caught.value)
    assert stub.calls == []


def test_the_list_file_never_lands_in_the_working_tree(tmp_path: Path) -> None:
    """Requirement 2.2, asserted rather than only claimed in a docstring.

    ``_list_file`` writing into the process cwd would put ``files.txt`` in the repository
    root for a pre-commit hook, which runs there — the same shape as the ``_prefix``
    ``.resolve()`` defect the project record already carries. Nothing pinned it: swapping
    ``TemporaryDirectory()`` for ``TemporaryDirectory(dir=".")`` left the suite green.
    """
    with _list_file([Path("/src/a.py")]) as listing:
        assert listing.is_file()
        assert listing.is_relative_to(Path(tempfile.gettempdir())), listing
        assert not listing.is_relative_to(Path.cwd()), listing
        assert listing.read_text(encoding="utf-8") == "/src/a.py\n"


def test_the_list_file_is_gone_once_the_command_has_run(tmp_path: Path) -> None:
    """It exists only while ``und`` is reading it, which is the other half of that claim."""
    with _list_file([Path("/src/a.py")]) as listing:
        kept = listing
    assert not kept.exists()


def test_a_refusal_names_the_lever_an_operator_actually_has(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """``CONFIG_HINT`` is a constant whose whole value is its wording, and nothing read it."""
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {"CodeCheckResultByFile.csv": "F\n", "CodeCheckResultByTable.csv": "T\n"}
            }
        }
    )
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert caught.value.hint == CONFIG_HINT
    assert "codecheck.config" in CONFIG_HINT


def test_codecheck_refuses_an_output_directory_that_is_not_empty(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A reused directory would hand back the previous run's export as this run's results."""
    out_dir = tmp_path / "cc"
    out_dir.mkdir()
    (out_dir / f"{VIOLATIONS_EXPORT}.csv").write_text("stale\n", encoding="utf-8")
    stub.plan({"codecheck": {"write": {}}})
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert str(out_dir) in str(caught.value)
    assert stub.calls == []


def test_codecheck_names_the_output_directory_when_it_refuses_to_choose(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "cc"
    stub.plan(
        {
            "codecheck": {
                "write": {
                    "CodeCheckResultByFile.csv": "File\n",
                    "CodeCheckResultByTable.csv": "T\n",
                }
            }
        }
    )
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert str(out_dir) in str(caught.value)


def test_codecheck_creates_the_output_directory(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "deep" / "cc"
    stub.plan({"codecheck": {"write": {f"{VIOLATIONS_EXPORT}.csv": "Check ID,File\n"}}})
    cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert out_dir.is_dir()


def test_codecheck_without_a_csv_fails_loudly(
    stub: UndStub, log: RecordingLog, tmp_path: Path
) -> None:
    """A silent empty directory would read as "no violations"; it means "no results".

    The assertion is on a phrase, not the word "csv": ``tmp_path`` is named after the test
    that asked for it, so ``"csv" in str(exc)`` was satisfied by the directory
    ``test_codecheck_without_a_csv_f0`` whatever the code actually said.
    """
    out_dir = tmp_path / "cc"
    with pytest.raises(AnalysisFailedError) as caught:
        cli(stub, log).codecheck(db_path(tmp_path), "Quick", [tmp_path / "a.py"], out_dir)
    assert "wrote no csv file" in str(caught.value).lower()
