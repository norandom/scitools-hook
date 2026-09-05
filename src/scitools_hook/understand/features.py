"""What the installed Understand offers, measured by asking it (requirements 1.1, 1.4).

Requirement 1.4 says availability is *measured*, not inferred from a version number, and the
reason is that 6.5 and 8.0 already differ in three of these while a build number tells you
nothing about a feature backported into it. So every answer here comes from running the thing
and reading what came back.

The probe runs inside ``doctor``'s existing scratch project -- one file, created, added and
analysed in a temporary directory -- and adds about one and a half seconds to a command an
operator runs when something is already wrong. It never runs against the repository's own
databases, and it **never runs a licence command**: `und license`, `-isundlicensed` and every
`-*license*` switch are the user's and the vendor's business
(``.kiro/steering/licensing.md``), and `doctor` asks `-isundlicensed` once elsewhere.

Three answers, and the third is the one that matters. ``available`` and ``not on this build``
are what they say; ``unverified`` is a probe that could not run -- no git on the machine, a
scratch directory that could not be written -- and saying so is not the same as saying the
build lacks the feature. A configuration that asks for something ``unverified`` fails closed
rather than being quietly ignored (requirement 1.2, task 2.3).

The report is stored beside the analysis databases with the build string it was measured on,
so a check can validate its configuration without paying for a probe, and so a report from
another build reads as stale rather than as an answer.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

from scitools_hook.config.models import Settings
from scitools_hook.errors import ConfigError, GateError
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.understand import Availability, Feature, FeatureReport
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.und_arch import DIRECTORY_STRUCTURE
from scitools_hook.understand.und_cli import (
    ALL,
    GitSource,
    UndCli,
    create_from_commit,
    list_generated,
)

FEATURES_FILE: Final = "features.json"
"""Where the report is stored, beside the databases it describes."""

PROBE_METRIC: Final = "CountGlobalsModified"
"""One plugin metric, asked for by lookup: the build either knows it or it does not."""

GIT_TIMEOUT_S: Final = 60.0
"""A git command in a one-file scratch repository takes milliseconds; this is a hang guard."""

_UNVERIFIED_NO_GIT: Final = "git could not run here, so a commit-built database was not tried"


def probe_features(cli: UndCli, api: ApiRunner | None, scratch: Path, build: str) -> FeatureReport:
    """Ask the installed build for each feature of this specification (req 1.1, 1.4).

    ``scratch`` is the directory ``doctor``'s analysis probe just built and analysed a
    one-file project in; ``scratch / "probe.und"`` is that database. Nothing here writes
    outside that directory.
    """
    db = scratch / "probe.und"
    sarif, accuracy = _probe_reports(cli, db, scratch)
    return FeatureReport(
        build=build,
        features={
            Feature.UNDERSTAND_SARIF: sarif,
            Feature.ACCURACY: accuracy,
            Feature.GENERATED_ARCHS: _probe_generated(cli, db),
            Feature.COMMIT_BEFORE: _probe_commit(cli, scratch, db),
            Feature.PLUGIN_METRICS: _probe_plugin_metrics(api),
            Feature.UNUSED_RULE: Availability(
                state="available",
                detail="reference-based; every build reports what calls what",
            ),
        },
    )


def _probe_reports(cli: UndCli, db: Path, scratch: Path) -> tuple[Availability, Availability]:
    """The two switches ``und analyze`` gained, asked for in one analysis.

    The SARIF answer is the *file*, not the exit status: a build that ignored an unknown
    switch would otherwise read as offering the feature and write nothing.
    """
    target = scratch / "probe.sarif"
    try:
        result = cli.analyze(db, ALL, accuracy=True, sarif=target)
    except GateError as refused:
        return (_refused(refused), _refused(refused))
    wrote = target.exists()
    return (
        Availability(
            state="available" if wrote else "not on this build",
            detail="" if wrote else f"und analyze -sarif wrote no file at {target.name}",
        ),
        Availability(
            state="available" if result.accuracy is not None else "not on this build",
            detail="" if result.accuracy is not None else "und analyze printed no accuracy line",
        ),
    )


def _probe_generated(cli: UndCli, db: Path) -> Availability:
    """Which automatic architectures this build can generate, by name (requirement 4.2)."""
    try:
        offered = list_generated(cli, db)
    except GateError as refused:
        return _refused(refused)
    return Availability(state="available", generated=[arch.name for arch in offered])


def _probe_commit(cli: UndCli, scratch: Path, db: Path) -> Availability:
    """Whether a database can be built from a commit, tried on a repository made here.

    The scratch project is turned into a one-commit repository for this, because
    ``-gitcommit`` needs a commit to pin to and the operator's own repository is not this
    probe's to touch. Without git on the machine the answer is ``unverified``: the build may
    well offer the feature, and this probe simply could not ask.
    """
    commit = _one_commit(scratch)
    if commit is None:
        return Availability(state="unverified", detail=_UNVERIFIED_NO_GIT)
    try:
        create_from_commit(
            cli,
            scratch / "probe-commit.und",
            ["Python"],
            GitSource(repo=scratch, commit=commit, refdb=db),
        )
    except GateError as refused:
        return _refused(refused)
    return Availability(state="available")


def _probe_plugin_metrics(api: ApiRunner | None) -> Availability:
    """Whether ``Metric.lookup`` finds a metric ``Metric.list`` never names (requirement 5.1)."""
    if api is None:
        return Availability(
            state="unverified", detail="no working API mode, so the catalogue was not asked"
        )
    try:
        answer = api.run("catalogue", {"kinds": [], "lookup": [PROBE_METRIC]})
    except GateError as refused:
        return _refused(refused)
    found = answer.get("lookup")
    known = isinstance(found, dict) and found.get(PROBE_METRIC) is not None
    return Availability(
        state="available" if known else "not on this build",
        detail="" if known else f"Metric.lookup does not know {PROBE_METRIC}",
    )


def _refused(failed: GateError) -> Availability:
    """A build that refused a command does not offer it, in the build's own words.

    ``und``'s own sentence first and the wrapper's message only as a fallback: the message
    leads with the whole command line, which on a temporary directory is long enough to fill
    the detail on its own and push the one useful line off the end.
    """
    said = getattr(failed, "und_output", "") or getattr(failed, "stderr", "") or ""
    return Availability(
        state="not on this build", detail=" ".join((said or str(failed)).split())[:300]
    )


def _one_commit(scratch: Path) -> str | None:
    """Make the scratch project a repository with one commit, and answer its hash.

    ``None`` when git is not there or refused, which is an ``unverified`` answer rather than
    a missing feature. The developer's global configuration, hooks and template directory are
    kept out, exactly as the test fixtures keep them out, and the databases in the scratch
    directory are ignored rather than committed -- a ``.und`` is a directory of binary files
    and none of it belongs in a commit made to ask one question.
    """
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "scitools-hook doctor",
        "GIT_AUTHOR_EMAIL": "doctor@example.invalid",
        "GIT_COMMITTER_NAME": "scitools-hook doctor",
        "GIT_COMMITTER_EMAIL": "doctor@example.invalid",
    }
    try:
        (scratch / ".gitignore").write_text("*.und\n", encoding="utf-8")
    except OSError:
        return None
    steps = (
        ["init", "--quiet", "--initial-branch=main"],
        ["add", "--all"],
        ["commit", "--quiet", "--no-verify", "--message", "doctor probe"],
        ["rev-parse", "HEAD"],
    )
    answer = ""
    for step in steps:
        try:
            done = subprocess.run(
                ["git", "-C", str(scratch), *step],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_S,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None
        answer = done.stdout.strip()
    return answer or None


ASKED_BY: Final[dict[str, Feature]] = {
    "understand.sarif": Feature.UNDERSTAND_SARIF,
    "understand.before_side": Feature.COMMIT_BEFORE,
    "analysis.accuracy_floor": Feature.ACCURACY,
    "structure.unused_routines": Feature.UNUSED_RULE,
}
"""Configuration key -> the feature it needs the build to offer (requirement 1.2).

``understand.before_side`` is here for ``"commit"`` only: ``"auto"`` asks for the route
*if the build has it* and falls back to the shadow tree otherwise, which is the whole point
of the value and must not be refused (requirement 3.3).
"""

RUN_DOCTOR: Final = (
    "Run `scitools-hook doctor` once: it measures what this build offers and records it "
    "beside the analysis databases."
)
"""What to do about a missing or stale record. The check never probes; ``doctor`` does."""


def asked_features(settings: Settings) -> dict[str, Feature]:
    """The features this configuration asks the build for, by the key that asked (req 1.2)."""
    enabled = {
        "understand.sarif": settings.understand.sarif,
        "understand.before_side": settings.understand.before_side == "commit",
        "analysis.accuracy_floor": settings.analysis.accuracy_floor is not None,
        "structure.unused_routines": settings.structure.unused_routines is not None,
    }
    return {key: ASKED_BY[key] for key, on in enabled.items() if on}


def generated_name(settings: Settings, declared: bool) -> str | None:
    """The architecture name that can only come from the build generating it, if any (req 4.2).

    ``Directory Structure`` is built into every database and a declared architecture comes
    from the repository's own file, so neither is this question. Anything else has to be a
    name the build can generate -- and a name that is none of the three is a misspelling,
    which is worth catching at configuration time rather than as ``und``'s "architecture not
    found" after two analyses have run.
    """
    name = settings.structure.architecture
    if name == DIRECTORY_STRUCTURE or declared:
        return None
    return name


def refuse_unavailable(
    settings: Settings, report: FeatureReport | None, build: str, declared: bool
) -> None:
    """Stop a run whose configuration needs something this build does not offer (req 1.2).

    Fails **closed**: a missing record, or one measured on another build, is not permission.
    A configuration that asks for nothing new needs no record at all, which is what keeps
    requirement 1.3's promise that an untouched repository behaves as it always did.
    """
    asked = asked_features(settings)
    wanted = generated_name(settings, declared)
    if not asked and wanted is None:
        return
    if report is None or report.build != build:
        keys = ", ".join(sorted(asked)) or "structure.architecture"
        raise ConfigError(
            f"this configuration asks what {build or 'this Understand'} offers, and no "
            f"measurement of it was found; keys asking: {keys}",
            hint=RUN_DOCTOR,
        )
    for key, feature in sorted(asked.items()):
        _reject_feature(report, feature, key, build)
    if wanted is not None:
        _reject_architecture(report, wanted)


def _reject_feature(report: FeatureReport, feature: Feature, key: str, build: str) -> None:
    """One key, one feature, and the build's own reason for not having it."""
    found = report.features.get(feature)
    if found is not None and found.state == "available":
        return
    said = f": {found.detail}" if found is not None and found.detail else ""
    state = "unverified" if found is None else found.state
    raise ConfigError(
        f"{key} needs {feature.value.replace('_', ' ')}, which {build} does not offer "
        f"({state}{said})",
        key=key,
        hint=RUN_DOCTOR if state == "unverified" else "Remove the key, or use a build that has it.",
    )


def _reject_architecture(report: FeatureReport, wanted: str) -> None:
    """An architecture name nothing can supply, with the names the build can (req 4.2)."""
    offered = report.features[Feature.GENERATED_ARCHS].generated
    if wanted in offered:
        return
    raise ConfigError(
        f"structure.architecture names {wanted!r}, which is neither {DIRECTORY_STRUCTURE!r}, "
        f"nor declared in this repository, nor one this build can generate",
        key="structure.architecture",
        hint=f"Architectures this build generates: {', '.join(offered) or '(none)'}.",
    )


def store_features(paths: CachePaths, report: FeatureReport) -> Path | None:
    """Write the report beside the databases, or answer ``None`` when it cannot be written.

    A report that cannot be stored costs the next check a "run doctor" message and nothing
    else, so a failure here is reported by its absence rather than by stopping the command
    that was diagnosing a broken installation in the first place.

    **A report in which nothing was measured is not stored at all**, and the test seam is the
    case that matters: it answers from fixtures with no Understand behind it, so every entry
    is ``unverified``, and writing that would have ``doctor`` create an analysis cache for a
    repository nothing has ever analysed. A diagnostic reports; it does not manufacture the
    thing it is diagnosing.
    """
    if all(found.state == "unverified" for found in report.features.values()):
        return None
    target = paths.root / FEATURES_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    except OSError:
        return None
    return target


def load_features(paths: CachePaths) -> FeatureReport | None:
    """The last report stored for this repository, or ``None`` when there is none to read.

    Unreadable and absent answer alike: the caller's question is "may I trust a feature on
    this build", and anything but a report that says so is a no.
    """
    source = paths.root / FEATURES_FILE
    try:
        return FeatureReport.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
