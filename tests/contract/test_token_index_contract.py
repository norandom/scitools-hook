"""The token index's coverage against the installed build (lean-code-rules 5.4).

One thing about the index only the real lexer can answer, measured on the contract project on
Build 1262 rather than assumed from the fake. Its siblings measure the rest:
``test_token_rules_contract`` the exact finding set (5.7) and ``test_docstring_lines_contract``
the docstring accounting (6.5); ``token_index_project`` holds what the three share.

``worker_lean.token_index`` records a shape for a routine only when the database gives both
ends of it -- ``ref("definein")`` and ``ref("end")`` in one project file -- and records
nothing for a routine it has only a declaration of. Nothing in the unit tests can say whether
that pair of kind strings answers on a real database; a build that answered ``end`` for
nothing would index nothing, and the family rule would report nothing on every project with
every unit test green. The test reads the same pair off the raw API through ``upython`` and
holds the index to exactly that set: every routine the API gives both ends of, and no routine
it does not. Measured: the fixture records 36 routines, the API spans all 36 and the index
holds all 36, out of 741 routine entities the database carries, the rest being the library
routines Understand injects outside the analysis root.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from contract_project import (
    TIMEOUT_S,
    SampleProject,
    real_env,
    sample_project,  # noqa: F401 -- imported so the session fixture is registered here
)
from token_index_project import index_of, keys_of, token_snapshot

from scitools_hook.config.metric_names import SCOPE_KINDS

pytestmark = pytest.mark.contract

SPAN_PROBE = """
import json
import sys

import understand

db = understand.open(sys.argv[1])
rows = []
for ent in db.ents(sys.argv[2]):
    start, end = ent.ref("definein"), ent.ref("end")
    rows.append({
        "longname": str(ent.longname()),
        "parameters": str(ent.parameters()),
        "start": None if start is None else [str(start.file().longname()), start.line()],
        "end": None if end is None else [str(end.file().longname()), end.line()],
    })
print(json.dumps(rows))
"""
"""Every routine of the database with the two references the index is built from.

The two kind strings ``worker_lean._routine_span`` asks for, **spelled here rather than
imported** from ``START_REFS`` and ``END_REFS`` and read off the raw API in Understand's own
interpreter rather than through the worker, so that the test is a second reading of the same
question and not the worker checking itself: a constant misspelled in the worker empties the
index and leaves this probe's answer intact, and the two then disagree.
"""


def spans_from_api(project: SampleProject, tmp_path: Path) -> dict[tuple[str, str], bool]:
    """``(longname, parameters) -> whether the API gives both ends in one project file``."""
    script = tmp_path / "spans.py"
    script.write_text(SPAN_PROBE, encoding="utf-8")
    upython = real_env("upython").upython
    assert upython is not None, "this build ships no upython, so the API cannot be read"
    done = subprocess.run(
        [str(upython), str(script), str(project.db("alpha")), SCOPE_KINDS["routine"]],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    root = f"{project.root('alpha')}/"
    found: dict[tuple[str, str], bool] = {}
    for row in json.loads(done.stdout.strip().splitlines()[-1]):
        start, end = row["start"], row["end"]
        spanned = (
            start is not None
            and end is not None
            and start[0] == end[0]
            and start[0].startswith(root)
        )
        found[(row["longname"], row["parameters"])] = spanned
    return found


def test_contract_the_index_holds_exactly_the_routines_the_api_gives_both_ends_of(
    sample_project: SampleProject,  # noqa: F811
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Requirement 5.4's index, held to the raw API's answer on ``definein`` and ``end``.

    Equality in both directions: a routine the API spans and the index lacks is a routine the
    family rule can never see, and a routine the index holds without a span is a shape clipped
    from nowhere. The three counts are printed so the record carries the sample.
    """
    snapshot = token_snapshot(sample_project)
    spanned = spans_from_api(sample_project, tmp_path)
    recorded = keys_of(snapshot, "routine")
    assert recorded, "the fixture recorded no routine at all"
    expected = {key.token for key in recorded if spanned[(key.longname, key.parameters or "")]}
    indexed = set(index_of(snapshot).routines)
    with capsys.disabled():
        print(
            f"\n  routines: {len(recorded)} recorded, {len(expected)} spanned by the API, "
            f"{len(indexed)} indexed; {len(spanned)} routine entities in the database"
        )

    assert indexed == expected, {
        "indexed but unspanned": sorted(indexed - expected),
        "spanned but not indexed": sorted(expected - indexed),
    }
    assert expected == {key.token for key in recorded}, (
        "some recorded routine has no end reference on this build; the coverage claim in "
        "worker_lean is then a weaker one than the fixture shows"
    )
