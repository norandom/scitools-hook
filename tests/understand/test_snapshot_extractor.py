"""The snapshot extractor: one request built from settings, one validated document (6.6).

The worker runs under an interpreter that cannot import this project, so the whole
configuration has to travel in the request. Everything that can go wrong on the way there is
silent: a kind string the worker never receives makes a scope disappear, a stats prefix
stripped too early turns a population threshold into an entity threshold, an analysis root
that names nothing yields a perfectly valid, entirely empty, entirely green document. So the
tests below assert the *request*, key by key, as hard as they assert the answer.

The unit tests drive :class:`fakes.api.FakeApiRunner` and the recorded snapshot fixture from
``tests/fixtures``; the ``contract``-marked ones run the real worker against the sample
databases.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest
from conftest import SampleDatabases, Side
from fakes.api import FakeApiRunner, FakeSnapshotExtractor
from fixtures import snapshot_fixture, snapshot_path
from pydantic import ValidationError
from test_api_runner import real_env

from scitools_hook.config.defaults import default_settings
from scitools_hook.config.fingerprint import analysis_fingerprint
from scitools_hook.config.metric_names import SCOPE_KINDS, Scope
from scitools_hook.config.models import (
    IgnoreRules,
    LeanRules,
    Limit,
    Settings,
    StructureRules,
    ThresholdSpec,
)
from scitools_hook.errors import AnalysisFailedError, ArchitectureNotFoundError, ConfigError
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import ParseError
from scitools_hook.models.understand import ExtractRequest
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.snapshot import SnapshotExtractor, SnapshotTarget

WIRE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "db",
        "side",
        "root",
        "files",
        "kinds_by_scope",
        "metrics_by_scope",
        "synthetic",
        "population_metrics",
        "plugin_metrics",
        "ignore",
        "architecture",
        "depth",
        "include_edges",
        "include_definitions",
        "record_referenced",
        "neighbourhood_rings",
        "lean_references",
        "lean_tokens",
        "parse_errors",
    }
)
"""Every key ``worker._plan`` reads. A missing one is refused; an extra one is ignored.

The two lean keys travel from the moment ``ExtractRequest`` declares them, which is one task
before ``_plan`` reads them: an extra key is ignored, so the request is valid on both sides of
that gap, and a key the plan reads before the request carries it would not be.
"""

MODEL_CANNOT_CARRY: Final[frozenset[str]] = frozenset({"db", "root", "side", "parse_errors"})
"""The keys ``ExtractRequest`` has no field for (task 6.2 handoff); the rest come from it."""


def a_target(**overrides: object) -> SnapshotTarget:
    """A snapshot target for a shadow tree that no test actually reads."""
    fields: dict[str, Any] = {
        "db": Path("/cache/after.und"),
        "root": Path("/cache/after"),
        "side": "after",
        "files": frozenset({"cli/app.py", "analysis/engine.py"}),
    }
    fields.update(overrides)
    return SnapshotTarget(**fields)


def an_extractor(answer: dict[str, object], settings: Settings | None = None) -> SnapshotExtractor:
    """An extractor whose runner answers ``snapshot`` with ``answer``."""
    runner = FakeApiRunner(answers={"snapshot": answer})
    return SnapshotExtractor(runner, settings or default_settings())


def fake_runner(extractor: SnapshotExtractor) -> FakeApiRunner:
    """The fake runner behind an extractor built by :func:`an_extractor`."""
    runner = extractor.runner
    assert isinstance(runner, FakeApiRunner)
    return runner


def fixture_document() -> dict[str, Any]:
    """The recorded ``after`` snapshot in the wire form the worker answers with."""
    text = snapshot_path("after").read_text(encoding="utf-8")
    document: dict[str, Any] = json.loads(text)
    return document


def only(scope: Scope, metric: str, limit: float = 10) -> Settings:
    """Settings with exactly one threshold, so a request key can be read unambiguously."""
    return Settings(thresholds=[ThresholdSpec(scope=scope, metric=metric, limit=Limit(max=limit))])


# --- the request built from settings ---------------------------------------------


def test_the_kind_strings_are_the_ones_config_declares_for_scopes_that_have_entities() -> None:
    # Iterating SCOPES instead of SCOPE_KINDS sends the worker `project` and `arch`, which
    # have no entity kind at all: the worker would call `db.ents("")`.
    request = an_extractor({}).request()

    assert request.kinds_by_scope == SCOPE_KINDS
    assert "project" not in request.kinds_by_scope
    assert "arch" not in request.kinds_by_scope


def test_element_thresholds_travel_as_plain_metric_names_per_scope() -> None:
    request = an_extractor({}).request()

    assert "CyclomaticStrict" in request.metrics_by_scope["routine"]
    assert "RatioCommentToCode" in request.metrics_by_scope["file"]
    assert request.metrics_by_scope["routine"] == sorted(request.metrics_by_scope["routine"])


def test_the_synthetic_metrics_are_declared_so_the_worker_computes_them() -> None:
    # Understand's own CountParams is unset for Python; without this list the worker reads
    # the native metric, finds nothing, and every parameter-count threshold stops firing.
    request = an_extractor({}).request()

    assert request.synthetic == ["CountDeclMethodNonStub", "CountParams", "LinesPerStatement"]
    assert "CountParams" in request.metrics_by_scope["routine"]


def test_a_configuration_that_names_no_synthetic_metric_asks_for_none() -> None:
    request = an_extractor({}, only("routine", "CyclomaticStrict")).request()

    assert request.synthetic == []


def test_population_metrics_keep_their_stats_prefixes() -> None:
    # The worker splits the prefix itself and needs it to tell a project metric read from
    # `db.metric` (plain) from one reduced over a scope's population (prefixed).
    request = an_extractor({}).request()

    assert "AVG:CyclomaticStrict" in request.population_metrics["project"]
    assert "MaxCyclomaticStrict" in request.population_metrics["project"]
    assert request.population_metrics["project"] == sorted(request.population_metrics["project"])


DELTA_COUNTS: Final = ["CountLineCode", "CountStmt"]
"""The two routine counts the net delta is summed over, in the request's sorted order."""


def test_a_stats_prefixed_element_threshold_asks_for_a_population_not_for_entities() -> None:
    request = an_extractor({}, only("routine", "AVG:CyclomaticStrict")).request()

    assert request.population_metrics["routine"] == ["AVG:CyclomaticStrict"]
    assert request.metrics_by_scope["routine"] == DELTA_COUNTS


def test_a_plain_project_threshold_asks_for_the_project_population() -> None:
    # `project` has no entities: the worker reads `db.metric` for a plain name and reduces
    # the routine population for a prefixed one. Either way it arrives as a population.
    request = an_extractor({}, only("project", "MaxCyclomaticStrict")).request()

    assert request.population_metrics["project"] == ["MaxCyclomaticStrict"]
    assert request.metrics_by_scope == {"routine": DELTA_COUNTS}


def test_an_architecture_scope_threshold_asks_for_neither() -> None:
    # `arch` values come from the architecture nodes and their edges, not from the snapshot's
    # entity or population vectors; asking for them would only produce an empty vector.
    request = an_extractor({}, only("arch", "CountLineCode")).request()

    assert request.metrics_by_scope == {"routine": DELTA_COUNTS}
    assert request.population_metrics == {}


def test_the_ignore_regexes_travel_under_the_scope_they_belong_to() -> None:
    settings = Settings(
        ignore=IgnoreRules(files=[r"^tests/"], classes=[r"Fake"], routines=[r"^_", r"test_"])
    )

    request = an_extractor({}, settings).request()

    assert request.ignore == {
        "file": [r"^tests/"],
        "class": [r"Fake"],
        "routine": [r"^_", r"test_"],
    }


def test_the_architecture_and_its_depth_come_from_the_structure_rules() -> None:
    settings = Settings(structure=StructureRules(architecture="Custom Arch", depth=3))

    request = an_extractor({}, settings).request()

    assert request.architecture == "Custom Arch"
    assert request.depth == 3


def test_the_files_of_interest_are_the_targets_own() -> None:
    request = an_extractor({}).request(["b.py", "a.py"])

    assert request.files == {"a.py", "b.py"}


def test_the_request_is_a_validated_extract_request() -> None:
    # The model is what forbids an unknown scope and an empty kind string; building the wire
    # dict by hand would drop those checks.
    assert isinstance(an_extractor({}).request(), ExtractRequest)


# --- the wire request ------------------------------------------------------------


def test_the_wire_request_carries_every_key_the_worker_reads() -> None:
    wire = an_extractor({}).wire_request(a_target())

    assert set(wire) == WIRE_KEYS


def test_only_the_four_keys_the_model_cannot_hold_are_added_by_hand() -> None:
    # `ExtractRequest` has no `db`, `root`, `side` or `parse_errors` field and forbids
    # extras (task 6.2 handoff), so those four are the documented deviation and nothing else.
    extractor = an_extractor({})

    from_model = set(extractor.request().model_dump(mode="json"))

    assert WIRE_KEYS - from_model == MODEL_CANNOT_CARRY


def test_the_database_and_the_side_come_from_the_target() -> None:
    wire = an_extractor({}).wire_request(a_target(db=Path("/cache/before.und"), side="before"))

    assert wire["db"] == "/cache/before.und"
    assert wire["side"] == "before"


def test_the_analysis_root_travels_exactly_as_the_caller_named_it(tmp_path: Path) -> None:
    # It has to be the directory `und add` was pointed at, character for character: the
    # worker makes every entity's long name relative to it. A user cache reached through a
    # symlink is ordinary, and resolving it here would produce a root the database never saw
    # — every entity key would carry the other path, nothing would match, and the run would
    # come back green with no entities at all.
    real = tmp_path / "cache" / "after"
    real.mkdir(parents=True)
    linked = tmp_path / "link"
    linked.symlink_to(tmp_path / "cache")

    wire = an_extractor({}).wire_request(a_target(root=linked / "after"))

    assert wire["root"] == str(linked / "after")
    assert wire["root"] != str(real)


def test_the_files_travel_sorted_so_two_runs_send_the_same_request() -> None:
    wire = an_extractor({}).wire_request(a_target(files=frozenset({"b.py", "a.py", "c.py"})))

    assert wire["files"] == ["a.py", "b.py", "c.py"]


_WIRE_SCRIPT: Final = """\
import json
from pathlib import Path

from fakes.api import FakeApiRunner

from scitools_hook.config.defaults import default_settings
from scitools_hook.understand.snapshot import SnapshotExtractor, SnapshotTarget

extractor = SnapshotExtractor(FakeApiRunner(), default_settings())
target = SnapshotTarget(
    db=Path("/cache/after.und"),
    root=Path("/cache/after"),
    side="after",
    files=frozenset({"cli/app.py", "analysis/engine.py"}),
)
print(json.dumps(extractor.wire_request(target)))
"""
"""The default wire request, built by a fresh interpreter and printed as the worker gets it."""


def wire_request_in_subprocess(seed: str) -> str:
    """The wire request built by an interpreter whose strings hash under ``seed``."""
    completed = subprocess.run(
        [sys.executable, "-c", _WIRE_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            "PYTHONHASHSEED": seed,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    return completed.stdout.strip()


def test_the_wire_request_is_the_same_bytes_in_every_process() -> None:
    # Every list in the request is reduced from a set, and a set of strings iterates in an
    # order that changes with the interpreter's hash seed. An order that is stable in *this*
    # process is exactly the bug: two Gate runs would send the worker different bytes for the
    # same configuration, so the same request would no longer be diffable, cacheable or
    # reproducible from a bug report. The seeds are fixed rather than left to chance because
    # a test that only sometimes disagrees with itself pins nothing.
    built = {wire_request_in_subprocess(seed) for seed in ("0", "1", "2", "random")}

    assert len(built) == 1
    assert built == {json.dumps(an_extractor({}).wire_request(a_target()))}


def test_the_parse_errors_of_the_analysis_travel_into_the_snapshot() -> None:
    # Requirement 2.6's errors belong on the snapshot, and the worker only echoes what it is
    # given: it never re-parses anything.
    errors = (ParseError(path=Path("src/bad.py"), line=41, message="unexpected token"),)

    wire = an_extractor({}).wire_request(a_target(parse_errors=errors))

    assert wire["parse_errors"] == [
        {"path": "src/bad.py", "line": 41, "message": "unexpected token"}
    ]


def test_edges_are_requested_by_default_and_can_be_switched_off() -> None:
    settings = default_settings()
    runner = FakeApiRunner(answers={"snapshot": {}})

    assert SnapshotExtractor(runner, settings).wire_request(a_target())["include_edges"] is True
    cheap = SnapshotExtractor(runner, settings, include_edges=False)
    assert cheap.wire_request(a_target())["include_edges"] is False


# --- the answer ------------------------------------------------------------------


def test_a_recorded_worker_document_becomes_the_project_snapshot_it_describes() -> None:
    """The done criterion: a fixture document, validated through the adapter."""
    extractor = an_extractor(fixture_document())

    snapshot = extractor.extract(a_target())

    assert snapshot == snapshot_fixture("after")
    assert snapshot.side == "after"
    assert snapshot.entities
    assert fake_runner(extractor).ops == ["snapshot"]


def test_the_document_is_validated_rather_than_trusted() -> None:
    # `extra="forbid"` on the models is what turns a drifting worker contract into a
    # failure here instead of a missing metric three layers away.
    extractor = an_extractor({"side": "after", "surprise": []})

    with pytest.raises(AnalysisFailedError) as failure:
        extractor.extract(a_target())

    assert "/cache/after.und" in str(failure.value)


def test_a_document_that_is_not_a_snapshot_at_all_is_reported_not_raised_as_pydantic() -> None:
    extractor = an_extractor({"entities": "not a list"})

    with pytest.raises(AnalysisFailedError) as failure:
        extractor.extract(a_target())

    assert not isinstance(failure.value, ValidationError)
    assert failure.value.stderr


def test_a_typed_refusal_from_the_runner_reaches_the_caller_unchanged() -> None:
    refusal = ArchitectureNotFoundError("no such architecture", available=["Directory Structure"])
    runner = FakeApiRunner(answers={"snapshot": refusal})
    extractor = SnapshotExtractor(runner, default_settings())

    with pytest.raises(ArchitectureNotFoundError) as failure:
        extractor.extract(a_target())

    assert failure.value.available == ["Directory Structure"]


# --- the fakes the tasks above this one build on ---------------------------------


def test_the_fake_runner_answers_and_records_what_it_was_asked() -> None:
    runner = FakeApiRunner(answers={"ping": {"version": "6.5.1204"}})

    assert runner.run("ping", {"a": 1}) == {"version": "6.5.1204"}
    assert runner.request_for("ping") == {"a": 1}
    assert runner.ops == ["ping"]


def test_the_fake_runner_raises_the_typed_errors_a_real_one_maps() -> None:
    runner = FakeApiRunner(answers={"snapshot": ArchitectureNotFoundError("gone")})

    with pytest.raises(ArchitectureNotFoundError):
        runner.run("snapshot", {})


def test_the_fake_runner_refuses_an_operation_nobody_scripted() -> None:
    with pytest.raises(AssertionError):
        FakeApiRunner().run("archs", {})


def test_the_fake_extractor_answers_per_side_and_records_its_targets() -> None:
    before, after = snapshot_fixture("before"), snapshot_fixture("after")
    extractor = FakeSnapshotExtractor(snapshots={"before": before, "after": after})
    target = a_target(side="before")

    assert extractor.extract(target) == before
    assert extractor.targets == [target]


def test_the_fake_extractor_refuses_a_side_nobody_scripted() -> None:
    with pytest.raises(AssertionError):
        FakeSnapshotExtractor().extract(a_target())


# --- against the real Understand -------------------------------------------------


def real_extractor(settings: Settings | None = None) -> SnapshotExtractor:
    """An extractor wired to the installed Understand through a real ``ApiRunner``."""
    runner = ApiRunner(real_env("upython"), NullCommandLog())
    return SnapshotExtractor(runner, settings or default_settings())


def real_target(databases: SampleDatabases, side: Side) -> SnapshotTarget:
    """Every analysed file of one sample database, named relative to its analysis root."""
    root = databases.root(side)
    files = {Path(name).relative_to(root).as_posix() for name in databases.list_files(side)}
    return SnapshotTarget(db=databases.db(side), root=root, side=side, files=frozenset(files))


@pytest.mark.contract
def test_the_request_the_extractor_builds_is_one_the_real_worker_accepts(
    sample_databases: SampleDatabases,
) -> None:
    """The whole point of building the request here: the worker must not refuse it."""
    snapshot = real_extractor().extract(real_target(sample_databases, "after"))

    assert snapshot.side == "after"
    assert {"Python", "C++"} <= set(snapshot.languages)
    assert {"cli/app.py", "analysis/engine.py"} <= {
        key.path for key in snapshot.entities if key.scope == "file"
    }
    assert [node.path for node in snapshot.arch_nodes] == sorted(
        node.path for node in snapshot.arch_nodes
    )


@pytest.mark.contract
def test_the_default_thresholds_come_back_as_metrics_and_populations(
    sample_databases: SampleDatabases,
) -> None:
    # The defaults name synthetic metrics, plain project metrics and prefixed ones at once,
    # so this is the request shape every real run sends.
    snapshot = real_extractor().extract(real_target(sample_databases, "after"))

    routines = [record for key, record in snapshot.entities.items() if key.scope == "routine"]
    assert any("CountParams" in record.metrics for record in routines)
    assert snapshot.populations["project"]["CyclomaticStrict"]
    assert snapshot.populations["project"]["MaxCyclomaticStrict"]
    assert snapshot.unavailable == {"Python": ["PercentLackOfCohesion"]}


@pytest.mark.contract
def test_the_ignore_regexes_reach_the_worker(sample_databases: SampleDatabases) -> None:
    settings = Settings(ignore=IgnoreRules(files=[r"engine\.py$"]), thresholds=[])
    target = real_target(sample_databases, "after")

    kept = real_extractor().extract(target)
    filtered = real_extractor(settings).extract(target)

    paths = {key.path for key in kept.entities if key.scope == "file"}
    assert "analysis/engine.py" in paths
    left = {key.path for key in filtered.entities if key.scope == "file"}
    assert "analysis/engine.py" not in left


@pytest.mark.contract
def test_a_root_that_names_no_file_of_the_database_is_a_configuration_error(
    sample_databases: SampleDatabases,
) -> None:
    """A wrong root is the failure that looks like success: zero entities, exit 0, all green."""
    target = real_target(sample_databases, "after")
    wrong = SnapshotTarget(
        db=target.db, root=Path("/completely/wrong"), side="after", files=target.files
    )

    with pytest.raises(ConfigError):
        real_extractor().extract(wrong)


# --- the definitions walk, asked for by three rules -------------------------------


def test_the_definitions_walk_is_asked_for_by_the_duplicate_definition_rule() -> None:
    settings = Settings(structure=StructureRules(duplicate_definitions=3))

    assert an_extractor({}, settings).request().include_definitions is True


def test_the_over_export_rule_alone_asks_for_the_definitions_walk() -> None:
    # The live gap task 2.1's review found. `structure.over_export` reads
    # `snapshot.definitions` to tell "one routine and nothing else" from "one routine beside
    # a module constant", and the fingerprint already treats the rule as a cache key -- so a
    # request built from `duplicate_definitions` alone serves the rule a FRESH snapshot with
    # no definitions in it, its module-level-binding guard sees nothing, and every candidate
    # file with a constant beside its one routine becomes a finding.
    settings = Settings(lean=LeanRules(over_export="warning"))

    assert settings.structure.duplicate_definitions is None
    assert an_extractor({}, settings).request().include_definitions is True


def test_the_unused_variable_rule_alone_asks_for_the_definitions_walk() -> None:
    """The third rule that reads the walk, found missing by task 6.1's contract test.

    ``lean.unused_variables`` judges ``Definition.referenced``, which only the definitions
    walk writes. Being one of ``REFERENCE_RULES`` turned on the per-entity reference walk and
    nothing else, so a configuration enabling this rule alone got a snapshot whose
    ``definitions`` list was empty -- and an empty list is a project with no module bindings
    as far as the rule can tell, so it reported nothing and raised no unavailable note. On the
    contract project that lost both planted cases in silence.
    """
    settings = Settings(lean=LeanRules(unused_variables="warning"))

    assert settings.structure.duplicate_definitions is None
    assert settings.lean.over_export is None
    assert an_extractor({}, settings).request().include_definitions is True


def test_a_configuration_that_needs_no_definitions_does_not_walk_them() -> None:
    settings = Settings()

    assert settings.structure.duplicate_definitions is None
    assert settings.lean.over_export is None
    assert settings.lean.unused_variables is None
    assert an_extractor({}, settings).request().include_definitions is False


DEFINITIONS_ASKED_BY: Final[tuple[Settings, ...]] = (
    Settings(structure=StructureRules(duplicate_definitions=3)),
    Settings(lean=LeanRules(over_export="warning")),
    Settings(lean=LeanRules(unused_variables="warning")),
)
"""One configuration per rule that needs the definitions walk, each on **alone**.

Alone is the point: it is the configuration where one site can disagree with the other and
still look right from either end.
"""


@pytest.mark.parametrize("settings", [Settings(), *DEFINITIONS_ASKED_BY])
def test_the_request_asks_for_the_definitions_walk_exactly_when_the_settings_want_it(
    settings: Settings,
) -> None:
    """The extractor reads ``Settings.wants_definitions`` rather than spelling it again.

    Without this the request and the property can drift apart silently: the two tests above
    pin the answer for the two rules that exist today, and a seventh rule added to the
    property alone would leave both of them green.
    """
    assert an_extractor({}, settings).request().include_definitions is settings.wants_definitions


@pytest.mark.parametrize("settings", DEFINITIONS_ASKED_BY)
def test_the_fingerprint_reads_the_same_property_the_request_does(settings: Settings) -> None:
    """Either rule alone changes the analysis fingerprint, and both change it identically.

    The fingerprint hashes its payload, so the ``definitions`` key cannot be read back; what
    can be read is that these two configurations differ from the default in *nothing else* --
    neither rule appears in the payload under its own name, and ``over_export`` is not one of
    the rules ``wants_references`` covers -- so equal fingerprints here mean both rules turned
    the same key on, and a fingerprint equal to the default's would mean one of them turned
    nothing on and a snapshot recorded without the walk would be served to a rule that needs
    it. That is the cache half of the defect the property exists to prevent.
    """
    asked = [analysis_fingerprint(each) for each in DEFINITIONS_ASKED_BY]

    assert analysis_fingerprint(settings) != analysis_fingerprint(Settings())
    assert asked[0] == asked[1]


# --- the reference walk, asked for by five rules (lean-code requirement 9.4) ------


REFERENCES_ASKED_BY: Final[tuple[Settings, ...]] = (
    Settings(lean=LeanRules(unused_parameters="warning")),
    Settings(lean=LeanRules(unused_classes="warning")),
    Settings(lean=LeanRules(unused_variables="warning")),
    Settings(lean=LeanRules(pass_through="warning")),
    Settings(lean=LeanRules(single_implementation="warning")),
)
"""One configuration per rule that needs the per-entity reference walk, each on **alone**.

Alone is the point, as it is for the definitions walk above: it is the configuration where
one site can disagree with the other and still look right from either end. Five rules and
not six -- `over_export` is answered from file metrics, `file_edges` and the definitions
walk, and paying for a `refs` call on every recorded entity to read none of the answers is
exactly the cost requirement 9.4 forbids.
"""


TOKENS_ASKED_BY: Final[tuple[Settings, ...]] = (
    Settings(lean=LeanRules(duplicates="warning")),
    Settings(lean=LeanRules(similar_routines="warning")),
)
"""One configuration per rule that needs the whole-project lexer pass, each on **alone**."""


@pytest.mark.parametrize("settings", REFERENCES_ASKED_BY)
def test_each_reference_rule_alone_asks_the_worker_for_the_reference_walk(
    settings: Settings,
) -> None:
    assert an_extractor({}, settings).request().lean_references is True


def test_an_over_export_only_configuration_asks_for_no_reference_walk() -> None:
    """The sixth lean rule, and the one that must not turn the walk on."""
    settings = Settings(lean=LeanRules(over_export="warning"))

    assert an_extractor({}, settings).request().lean_references is False


@pytest.mark.parametrize("settings", [Settings(), *REFERENCES_ASKED_BY, *TOKENS_ASKED_BY])
def test_the_request_asks_for_references_exactly_when_the_settings_want_them(
    settings: Settings,
) -> None:
    """The extractor reads ``LeanRules.wants_references`` rather than spelling it again.

    The fingerprint reads the same property (``config/fingerprint.py``), so a request built
    from a second reading of the five rules could serve a cached snapshot with no facts in it
    to a run whose configuration asks for them -- and every affected record would answer
    ``None``, which the rules report as unavailable. One property, asked twice, cannot drift.

    The token configurations are in the table for the mirror case, and it is the one this
    reads for: a request that wrote ``wants_references or wants_tokens`` would make a
    duplicates-only run pay a ``refs`` call on every recorded entity and read none of the
    answers. Without them the two keys are guarded asymmetrically -- the tokens twin below
    spans both tables and this one did not.
    """
    request = an_extractor({}, settings).request()

    assert request.lean_references is settings.lean.wants_references


# --- the token pass, asked for by two rules (lean-code requirement 9.4) -----------


@pytest.mark.parametrize("settings", TOKENS_ASKED_BY)
def test_each_token_rule_alone_asks_the_worker_for_the_token_pass(settings: Settings) -> None:
    assert an_extractor({}, settings).request().lean_tokens is True


@pytest.mark.parametrize("settings", REFERENCES_ASKED_BY)
def test_a_reference_rule_alone_asks_for_no_token_pass(settings: Settings) -> None:
    """The two keys are two costs. A lexer pass over every file of the project is not what a
    dead-parameter rule asked for, and a request that turned both on together would pay for
    it on every check that enables any lean rule at all.
    """
    assert an_extractor({}, settings).request().lean_tokens is False


EVERY_LEAN_RULE: Final = LeanRules(
    unused_parameters="warning",
    unused_classes="warning",
    unused_variables="warning",
    pass_through="warning",
    single_implementation="warning",
    over_export="warning",
    duplicates="warning",
    similar_routines="warning",
    max_net_growth=0,
)
"""Every switch of the ``[lean]`` section on at once, the far end from ``LeanRules()``."""


@pytest.mark.parametrize(
    "lean",
    [LeanRules(), LeanRules(pass_through="warning"), EVERY_LEAN_RULE],
    ids=["shipped", "pass_through", "every_rule"],
)
def test_the_delta_asks_for_both_of_its_counts_whatever_the_lean_section_says(
    lean: LeanRules,
) -> None:
    """Follow-ups 11 and 14 of the lean-code family, one request. The net delta sums
    ``CountStmt`` and ``CountLineCode`` off every routine record on every check that has a
    before side, whatever ``[lean]`` says (requirement 7.1), and the pass-through rule judges
    a routine by the first of the two (requirement 2.2). Until follow-up 11 the pass-through
    count reached the record only because the shipped ``routine.CountStmt`` threshold asked
    for it, and until follow-up 14 the delta's two counts reached it only because the shipped
    thresholds asked for both: drop those thresholds and the delta summed zeros without a
    word. So the extractor asks for both counts on the routine scope itself.

    Written with **no thresholds at all**, so the counts can only have come from that request,
    and parametrised from the shipped ``[lean]`` section to every switch on, so the request
    is shown not to depend on the section.

    No fingerprint key answers for this. ``config.fingerprint`` keys the snapshot cache on
    what a configuration makes the request ask for, and a request that does not vary by
    configuration has nothing for a key to tell apart.
    """
    request = an_extractor({}, Settings(thresholds=[], lean=lean)).request()

    assert request.metrics_by_scope == {"routine": DELTA_COUNTS}


def test_the_delta_counts_join_the_thresholds_metrics_once_and_sorted() -> None:
    """With the shipped thresholds, which ask for both counts, the request is exactly what
    it was before the extractor asked for them itself."""
    settings = default_settings()
    asked = settings.model_copy(update={"lean": LeanRules(pass_through="warning")})

    before = an_extractor({}, settings).request().metrics_by_scope
    after = an_extractor({}, asked).request().metrics_by_scope

    assert set(DELTA_COUNTS) <= set(before["routine"])
    assert before["routine"] == sorted(set(before["routine"]))
    assert after == before


@pytest.mark.parametrize("settings", [Settings(), *TOKENS_ASKED_BY, *REFERENCES_ASKED_BY])
def test_the_request_asks_for_tokens_exactly_when_the_settings_want_them(
    settings: Settings,
) -> None:
    """Read from ``LeanRules.wants_tokens``, for the reason the reference key is read from
    ``wants_references``: ``config/fingerprint`` decides from the same property whether a
    cached snapshot still describes this configuration, so a snapshot recorded with no index
    in it cannot be served to a run that asks for one -- and the two sites agreeing is what
    makes that true. Spelling the two rules again here is how they would drift.
    """
    request = an_extractor({}, settings).request()

    assert request.lean_tokens is settings.lean.wants_tokens
