"""A file the before side could not read: its violations are measured, not written.

Separate from ``test_classify.py`` because it is a separate question. That file asks what the
severity map and the ratchet do to a finding; this one asks what happens when the comparison
had nothing to compare against -- the case that decides whether anybody can afford to make a
file readable.

Blocking there was measured to make the fix impossible: converting one file so Understand can
parse it surfaces every routine in it at once, all blocking, for code the commit did not
write. Nobody pays that twice, so the file stays unmeasured forever.
"""

from __future__ import annotations

from scitools_hook.analysis.classify import classify
from scitools_hook.config.models import RatchetSettings
from scitools_hook.models.findings import PARSE_ERROR_RULE, Finding


def measured_finding(path: str = "src/pkg/mod.py", **extra: object) -> Finding:
    """One threshold violation with no before value, as an unparsed before side leaves it."""
    return Finding.model_validate(
        {
            "kind": "threshold",
            "rule": "routine.CyclomaticStrict",
            "scope": "routine",
            "path": path,
            "line": 12,
            "value": 14.0,
            "limit": 10.0,
            "limit_source": "rule",
            "severity": "error",
            "blocking": True,
            "message": "routine pkg.mod.walk CyclomaticStrict is 14, which exceeds the maximum 10",
        }
        | extra
    )


def test_a_violation_in_a_file_the_before_side_could_not_read_does_not_block() -> None:
    """Fixing a parse error must not cost one blocking finding per routine it revealed.

    The code was there; only the measurement is new. Blocking here is what made the fix
    impossible, so the file stayed unmeasured -- the outcome the rule exists to prevent.
    """
    (classified,) = classify([measured_finding()], RatchetSettings(), {}, None, ["src/pkg/mod.py"])

    assert classified.preexisting is True
    assert classified.blocking is False
    assert classified.severity == "error", "still an error, still reported, still counted"
    assert "measured here for the first time" in classified.message


def test_a_violation_in_a_file_that_parsed_before_still_blocks() -> None:
    """The exemption is per file, so an ordinary regression is untouched."""
    (classified,) = classify(
        [measured_finding()], RatchetSettings(), {}, None, ["src/other/thing.py"]
    )

    assert classified.preexisting is False
    assert classified.blocking is True


def test_the_exemption_reaches_no_further_than_threshold_findings() -> None:
    """A parse finding keeps its own judgement; acknowledgement is the lever for that one."""
    unreadable = Finding.model_validate(
        {
            "kind": "parse",
            "rule": PARSE_ERROR_RULE,
            "scope": "file",
            "path": "src/pkg/mod.py",
            "limit_source": "rule",
            "message": "Understand could not read src/pkg/mod.py",
        }
    )

    (classified,) = classify([unreadable], RatchetSettings(), {}, None, ["src/pkg/mod.py"])

    assert classified.blocking is True
    assert "measured here for the first time" not in classified.message


def test_naming_no_unparsed_file_leaves_every_finding_alone() -> None:
    """The shipped default: nothing is exempt unless a before side actually failed."""
    (classified,) = classify([measured_finding()], RatchetSettings(), {})

    assert classified.blocking is True


def test_a_finding_already_pre_existing_is_not_relabelled() -> None:
    """Its message stays the one the evaluator wrote; the exemption adds nothing to it."""
    (classified,) = classify(
        [measured_finding(preexisting=True, blocking=False)],
        RatchetSettings(),
        {},
        None,
        ["src/pkg/mod.py"],
    )

    assert "measured here for the first time" not in classified.message
