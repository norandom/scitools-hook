"""The analysis fingerprint: which settings changes make a cached analysis worthless.

Two caches in this specification are keyed on it -- the commit-built before database
(requirement 3.5) and the before snapshot (requirement 8.6) -- and they must agree, or one
would serve a stale answer the other had already discarded. So there is one function and
these tests, rather than a hash written twice.

The line it draws is the point. A setting that changes **what Understand analyses or how**
invalidates: the languages, the file selection, the architecture the structural rules read,
the acknowledged parse errors. A setting that changes only **how the result is judged** does
not: a threshold, a severity, a ratchet lever. Getting that backwards in the cheap direction
costs a rebuild nobody needed; getting it backwards in the expensive direction serves a
document that describes a different project, which is the failure this whole feature must
not introduce (requirement 8.7).
"""

from __future__ import annotations

import pytest

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.fingerprint import analysis_fingerprint
from scitools_hook.config.models import ParseAcknowledgement, Settings


def changed(**overrides: object) -> Settings:
    """The shipped settings with one branch replaced, as a configuration file would."""
    return default_settings().model_copy(update=overrides, deep=True)


def test_the_same_settings_always_hash_the_same() -> None:
    """A cache key that moved between two runs of one configuration would never hit."""
    assert analysis_fingerprint(default_settings()) == analysis_fingerprint(default_settings())


def test_the_fingerprint_is_a_short_stable_hex_string() -> None:
    """It goes in a file name and in `doctor`'s output, so it has to be both."""
    digest = analysis_fingerprint(default_settings())

    assert len(digest) == 16
    assert all(character in "0123456789abcdef" for character in digest)


def test_a_threshold_does_not_change_the_fingerprint() -> None:
    """The explicit criterion of task 1.3: judgement is not analysis.

    Two settings that differ only in a limit describe the same databases and the same
    snapshots; re-analysing on a threshold edit would make every experiment with a limit cost
    a full run, which is the opposite of what the ratchet is for.
    """
    loosened = default_settings()
    for spec in loosened.thresholds:
        if spec.limit is not None and spec.limit.max is not None:
            spec.limit.max += 1.0
            break
    else:  # pragma: no cover - the shipped defaults always carry a maximum
        pytest.fail("the shipped defaults carry no maximum to change")

    assert analysis_fingerprint(loosened) == analysis_fingerprint(default_settings())


def test_neither_do_the_severities_the_ratchet_or_the_baseline() -> None:
    """Everything downstream of the snapshot is outside the key, by the same argument."""
    base = default_settings()
    tightened = changed(
        ratchet=base.ratchet.model_copy(update={"strict": True}),
        baseline=base.baseline.model_copy(update={"adaptive": True}),
    )

    assert analysis_fingerprint(tightened) == analysis_fingerprint(base)


@pytest.mark.parametrize(
    "overrides",
    [
        {"languages": ["Python"]},
        {"include": ["src/**"]},
        {"exclude": ["vendor/**"]},
    ],
    ids=["languages", "include", "exclude"],
)
def test_what_enters_the_database_changes_the_fingerprint(overrides: dict[str, object]) -> None:
    """The file set and the language set decide what the database holds (requirement 2.4)."""
    base = default_settings()
    moved = changed(project=base.project.model_copy(update=overrides))

    assert analysis_fingerprint(moved) != analysis_fingerprint(base)


@pytest.mark.parametrize(
    "overrides",
    [{"architecture": "Git Stability"}, {"depth": 3}],
    ids=["architecture", "depth"],
)
def test_the_architecture_the_rules_read_changes_the_fingerprint(
    overrides: dict[str, object],
) -> None:
    """Nodes and their depth are recorded in the snapshot itself, so a cached one is stale."""
    base = default_settings()
    moved = changed(structure=base.structure.model_copy(update=overrides))

    assert analysis_fingerprint(moved) != analysis_fingerprint(base)


def test_an_acknowledged_parse_error_changes_the_fingerprint() -> None:
    """An acknowledgement changes which findings a snapshot can produce for a file."""
    base = default_settings()
    acknowledged = changed(
        parse=base.parse.model_copy(
            update={
                "acknowledged": [
                    ParseAcknowledgement(paths=["pkg/generic.py"], reason="measured elsewhere")
                ]
            }
        )
    )

    assert analysis_fingerprint(acknowledged) != analysis_fingerprint(base)


def test_the_ignore_rules_change_the_fingerprint() -> None:
    """The worker applies them while it records, so they are baked into the document."""
    base = default_settings()
    ignoring = changed(ignore=base.ignore.model_copy(update={"routines": ["^tests\\."]}))

    assert analysis_fingerprint(ignoring) != analysis_fingerprint(base)
