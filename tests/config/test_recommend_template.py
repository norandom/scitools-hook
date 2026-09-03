"""The pasteable half of ``recommend``: deviations only, each carrying its measurement.

A separate module from ``test_template.py`` on purpose -- that file is the detection
renderer's, and two tasks editing one test module is how a merge loses an assertion.

The contract asserted here is narrow and it is the whole reason this rendering lives in
``config.template`` rather than in ``analysis``: the block an operator pastes has to be a
*valid* fragment of this project's configuration file, quoted and keyed the way the rest of
the file is, and it has to say on its face that it is not a baseline.
"""

from __future__ import annotations

import tomllib

from scitools_hook.config.template import (
    RECOMMEND_NOTHING,
    RecommendedThreshold,
    render_recommendation,
)

MEASURED = "7397 routines, 1345 classes, 756 files"


EVIDENCE = "measured 7397 routines: p95 7"


def line(scope: str, metric: str, limit: float, evidence: str = EVIDENCE) -> RecommendedThreshold:
    """One proposed threshold with its measurement."""
    return RecommendedThreshold(scope=scope, metric=metric, limit=limit, evidence=evidence)


def parsed(document: str) -> dict[str, object]:
    """The rendered block as TOML, which is the only thing an operator will do with it."""
    return tomllib.loads(document)


# --- what a proposal renders as ---------------------------------------------------


def test_the_block_parses_as_the_configuration_it_claims_to_be() -> None:
    """A block that does not parse is a block that breaks the file it is pasted into."""
    document = render_recommendation(
        [line("routine", "CyclomaticStrict", 15), line("file", "CountDeclClass", 8)],
        MEASURED,
        0.95,
    )

    assert parsed(document) == {
        "thresholds": {
            "routine": {"CyclomaticStrict": 15},
            "file": {"CountDeclClass": 8},
        }
    }


def test_only_the_proposals_appear_and_nothing_else_does() -> None:
    """ "Only deviations" is the house style: a restated default decided nothing.

    Asserted against the *parsed* document rather than against the text, so a metric that
    appeared only inside a comment cannot pass this.
    """
    document = render_recommendation([line("routine", "CyclomaticStrict", 15)], MEASURED, 0.95)

    tables = parsed(document)["thresholds"]
    assert isinstance(tables, dict)
    assert list(tables) == ["routine"]
    assert tables["routine"] == {"CyclomaticStrict": 15}


def test_every_line_carries_the_measurement_that_produced_it() -> None:
    """The evidence sits directly above the value it justifies, as a comment."""
    evidence = "measured 756 files: p50 1, p95 7, max 48; 124 outside (16.4%) at 3"
    document = render_recommendation([line("file", "CountDeclClass", 8, evidence)], MEASURED, 0.95)

    body = document.splitlines()
    at = body.index("CountDeclClass = 8")
    assert body[at - 1] == f"# {evidence}"


def test_a_long_measurement_is_wrapped_rather_than_overrunning_the_line() -> None:
    """The generated file has to stay inside this project's own line limit."""
    evidence = "measured " + "very long evidence indeed, " * 12
    document = render_recommendation([line("file", "CountLineCode", 600, evidence)], MEASURED, 0.95)

    assert max(len(text) for text in document.splitlines()) <= 100
    assert parsed(document) == {"thresholds": {"file": {"CountLineCode": 600}}}


def test_the_scopes_come_out_in_the_order_the_configuration_file_uses() -> None:
    """Same order as every other table this renderer writes, whatever order the caller had."""
    document = render_recommendation(
        [line("file", "CountDeclClass", 8), line("routine", "CyclomaticStrict", 15)],
        MEASURED,
        0.95,
    )

    assert document.index("[thresholds.routine]") < document.index("[thresholds.file]")


# --- what the block says about itself ---------------------------------------------


def test_the_header_states_that_this_is_not_a_baseline() -> None:
    """The confusion this feature can cause, forestalled in the artefact and not only in help.

    A pasted block outlives the command that produced it, so the distinction has to travel
    with it.
    """
    document = render_recommendation([line("routine", "CyclomaticStrict", 15)], MEASURED, 0.95)

    assert "THIS IS NOT A BASELINE" in document
    assert "WHERE YOU ARE" in document
    assert "WHERE TO AIM" in document


def test_the_header_records_what_was_measured_and_against_what_target() -> None:
    """A block read six months later still says what it was derived from."""
    document = render_recommendation([line("routine", "CyclomaticStrict", 15)], MEASURED, 0.95)

    assert f"Measured {MEASURED}" in document
    assert "contains 95% of its population" in document


def test_nothing_to_paste_is_written_out_as_an_answer() -> None:
    """An empty block would be indistinguishable from a run that failed to measure anything."""
    document = render_recommendation([], MEASURED, 0.95)

    assert parsed(document) == {}
    assert RECOMMEND_NOTHING[0] in document
    assert "always proposes a change is a tool nobody trusts" in document
    assert f"Measured {MEASURED}" in document


def test_every_line_of_the_empty_block_is_a_comment() -> None:
    """It is pasted into a real file too, and must change nothing when it is."""
    document = render_recommendation([], MEASURED, 0.95)

    assert all(text.startswith("#") for text in document.splitlines() if text)


def test_a_metric_name_needing_quotes_is_quoted() -> None:
    """The same escaping the rest of the file uses; a bare ``AVG:X`` would not parse."""
    document = render_recommendation([line("project", "AVG:CountLineCode", 30)], MEASURED, 0.95)

    assert parsed(document) == {"thresholds": {"project": {"AVG:CountLineCode": 30}}}
