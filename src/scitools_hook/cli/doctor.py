"""The ``doctor`` subcommand: report the environment the Gate found (req 1.5, 12.5).

``runner.doctor.run_doctor`` produces the report and never raises; this module lays it out.
Four decisions shape the layout:

* **``doctor`` always exits 0.** It reports, it does not judge. Every exit code this tool
  documents already belongs to a failure of the command that *needed* the thing that is
  broken -- and the one code a "problems were found" verdict would naturally reach for,
  exit 1, is spent on "blocking violations found", so a CI job running ``doctor`` would be
  told a commit had violations that were never measured. Problems are printed, prominently
  and before the configuration dump; the exit code stays 0.
* **The API mode printed is the one that was *decided*.** ``UnderstandDiagnosis.api_mode`` is
  ``None`` until an installation verifies, while ``env.api_mode`` is a guess read off the
  directory layout. Printing the guess would tell an operator whose installation does not
  work which mode it is not working in (note 8.2), so an unverified installation says so and
  names no mode.
* **An after shadow synced from a commit is ordinary.** ``explain --range`` leaves
  ``SyncState.after_target == "commit"`` by design; the price is that the next
  ``check --staged`` re-syncs that shadow in full. That is worth one line of documentation
  next to the state -- "why was my next commit slow?" has a real answer -- and it is a note,
  not a problem, because nothing is broken (note 8.4).
* **The configuration is printed with its sources**, which is requirement 1.5's last clause,
  through the same renderer ``config`` uses. One layout for one thing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

import typer

from scitools_hook import __version__
from scitools_hook.cli import common
from scitools_hook.cli.config_cmd import render_settings
from scitools_hook.models.cache import CachePaths, SyncState
from scitools_hook.models.understand import UnderstandEnv
from scitools_hook.runner.doctor import (
    ApiProbe,
    DoctorReport,
    GitStatus,
    UnderstandDiagnosis,
    run_doctor,
)

HELP = (
    "Report the Understand installation, licence, repository and configuration in use. "
    "Always exits 0: it reports what it found rather than judging it."
)

NOT_VERIFIED: Final = "not verified"
"""What the API mode says while no mode has been decided; never the layout's guess."""

NOT_FOUND: Final = "not found"
NONE_FOUND: Final = "none"
NO_REPOSITORY: Final = "no repository, so there is no analysis cache"
NEVER_ANALYSED: Final = "nothing analysed yet"
NO_CONFIGURATION: Final = "none was loaded; see the problems above"
"""What the configuration section says when nothing merged.

Reachable, and reachable in exactly the situation this command exists for: a configuration
file the loader refuses leaves ``run_doctor`` reporting the problem and carrying on with an
empty ``Provenance``, so there is no leaf to print. Printing a bare heading there would read
as "no settings", which is a different and untrue statement."""

RESYNC_NOTE: Final = (
    "the after shadow was last synced from a commit (`explain --range` does that), so the "
    "next `check --staged` re-syncs it in full -- expected, not damage"
)
"""Requirement 4.11's cost, stated where an operator meets it (note 8.4)."""

LABEL_WIDTH: Final = 18
"""Label column; wide enough for the longest label here, so values line up in one block."""


def register(app: typer.Typer) -> None:
    """Add ``doctor`` to ``app``."""
    app.command(name="doctor", help=HELP)(doctor)


def doctor(ctx: typer.Context) -> None:
    """Diagnose the environment this run would use (req 1.5, 12.5)."""
    options = common.global_options(ctx)
    common.emit_findings(render_report(run_doctor(options.context_options())), None)


def render_report(report: DoctorReport) -> str:
    """The whole diagnosis as text: environment, then problems, then the configuration."""
    blocks = [
        _section("scitools-hook", [("version", __version__), ("python", report.python)]),
        _section("Understand", _understand_rows(report.understand)),
        _section("Repository", _git_rows(report.git)),
        _section("Analysis cache", _cache_rows(report.cache, report.state)),
        _listed("Problems", report.problems or [NONE_FOUND]),
        _listed(
            "Configuration",
            render_settings(report.settings, report.settings_provenance) or [NO_CONFIGURATION],
        ),
    ]
    return "\n\n".join(blocks)


# --- the sections ------------------------------------------------------------------


def _understand_rows(diagnosis: UnderstandDiagnosis) -> list[tuple[str, str]]:
    """Where Understand is, what it answers, and what each way into its API said (req 1.5)."""
    rows: list[tuple[str, str]] = []
    rows.extend(_installation_rows(diagnosis.env))
    rows.append(("und version", diagnosis.und_version or NOT_FOUND))
    rows.append(("license", _license(diagnosis)))
    rows.append(("api mode", diagnosis.api_mode or NOT_VERIFIED))
    rows.extend((f"probe {probe.mode}", _probe(probe)) for probe in diagnosis.probes)
    return rows


def _installation_rows(env: UnderstandEnv | None) -> list[tuple[str, str]]:
    """The installation that was found, verified or not; one row saying so when there is none."""
    if env is None:
        return [("installation", NOT_FOUND)]
    rows = [("installation", str(env.home)), ("found by", env.source), ("und", str(env.und))]
    if env.upython is not None:
        rows.append(("upython", str(env.upython)))
    return [*rows, ("python api", str(env.python_api_dir))]


def _license(diagnosis: UnderstandDiagnosis) -> str:
    """What ``und`` said about licensing, quoted when it refused one (req 1.4)."""
    status = diagnosis.license
    if status is None:
        return NOT_FOUND
    return "ok" if status.ok else f"unavailable: {status.text}".strip()


def _probe(probe: ApiProbe) -> str:
    """One probe's answer: the API version it reported, or why it reported none."""
    return f"ok ({probe.version})" if probe.ok else f"no ({probe.detail})"


def _git_rows(git: GitStatus) -> list[tuple[str, str]]:
    """The repository, or git's own words about why there is none (req 1.5, 12.5)."""
    if not git.inside_repository:
        return [("inside a repository", "no"), ("detail", git.detail)]
    return [
        ("inside a repository", "yes"),
        ("root", str(git.root)),
        ("git directory", str(git.git_dir)),
        ("common directory", str(git.common_dir)),
        ("HEAD", git.head or "unborn branch (no commit yet)"),
    ]


def _cache_rows(paths: CachePaths | None, state: SyncState | None) -> list[tuple[str, str]]:
    """Where the databases live and what the shadows currently hold (req 2.8)."""
    if paths is None:
        return [("cache", NO_REPOSITORY)]
    rows = [
        ("cache root", str(paths.root)),
        ("after database", str(paths.after_db)),
        ("before database", str(paths.before_db)),
        ("sync state", str(paths.state)),
    ]
    return rows + _state_rows(state)


def _state_rows(state: SyncState | None) -> list[tuple[str, str]]:
    """What the recorded sync state says, and the one consequence it can carry."""
    if state is None:
        return [("state", NEVER_ANALYSED)]
    rows = [
        ("after target", f"{state.after_target or 'none'} ({state.after_tree_id or 'no id'})"),
        ("before commit", state.before_commit or "none"),
        ("languages", ", ".join(state.languages) or "none recorded"),
        ("built with", state.created_with or "unknown"),
    ]
    if state.after_target == "commit":
        rows.append(("note", RESYNC_NOTE))
    return rows


# --- layout --------------------------------------------------------------------------


def _section(title: str, rows: Sequence[tuple[str, str]]) -> str:
    """A titled block of ``label: value`` rows, the labels in one column."""
    lines = [title]
    lines.extend(f"  {label + ':':<{LABEL_WIDTH}} {value}" for label, value in rows)
    return "\n".join(lines)


def _listed(title: str, items: Iterable[str]) -> str:
    """A titled block of indented lines, for anything that is a list rather than a table."""
    return "\n".join([title, *(f"  {item}" for item in items)])
