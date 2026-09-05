"""``FakeUndCli``: an ``UndCli`` that runs no process and reports what it was asked (6.5).

The tasks that depend on this one — the database manager (8.1) above all — need to assert
*which* ``und`` commands ran, in what order, with which arguments, and to decide what each
``analyze`` reports back. So every call is recorded as a :class:`FakeCall` and the analyze
answers are scripted in order.

It derives from :class:`~scitools_hook.understand.und_cli.UndCli` so that mypy compares each
override against the real signature, and so it can be passed anywhere the real wrapper is
expected. The base ``__init__`` is deliberately not called: there is no installation, no
command log and no subprocess behind this object.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from scitools_hook.models.understand import AnalyzeResult, LicenseStatus
from scitools_hook.understand.und_cli import (
    AnalysisSelection,
    UndCli,
)


@dataclass(frozen=True)
class FakeCall:
    """One recorded call: the method that was invoked and the arguments it was given."""

    command: str
    arguments: Mapping[str, object]


@dataclass
class FakeUndCli(UndCli):
    """An ``UndCli`` that answers from configuration instead of from a real Understand."""

    version_text: str = "(Build 1204)"
    license: LicenseStatus = field(default_factory=lambda: LicenseStatus(ok=True))
    analyze_results: list[AnalyzeResult] = field(default_factory=list)
    violations_csv: Path | None = None
    calls: list[FakeCall] = field(default_factory=list)

    @property
    def commands(self) -> list[str]:
        """The names of the commands that ran, in order, for readable assertions."""
        return [call.command for call in self.calls]

    def version(self) -> str:
        """The configured version string, as ``und version`` would have printed it."""
        self.calls.append(FakeCall("version", {}))
        return self.version_text

    def license_status(self) -> LicenseStatus:
        """The configured license status; ok unless a test says otherwise."""
        self.calls.append(FakeCall("license_status", {}))
        return self.license

    def create(self, db: Path, languages: list[str], local: bool = True) -> None:
        """Record a database creation."""
        self.calls.append(
            FakeCall("create", {"db": db, "languages": list(languages), "local": local})
        )

    def add(self, db: Path, root: Path, exclude: list[str]) -> None:
        """Record a source root being added."""
        self.calls.append(FakeCall("add", {"db": db, "root": root, "exclude": list(exclude)}))

    def remove_files(self, db: Path, files: list[Path]) -> None:
        """Record a removal, including the empty one the real wrapper skips."""
        self.calls.append(FakeCall("remove_files", {"db": db, "files": list(files)}))

    def analyze(
        self,
        db: Path,
        selection: AnalysisSelection,
        accuracy: bool = False,
        sarif: Path | None = None,
    ) -> AnalyzeResult:
        """Record the analysis and answer with the next scripted result.

        Once the script runs out the answer is an empty result, so a test that scripts one
        analysis and triggers two sees the second as "nothing happened" rather than as a
        silent repeat of the first.
        """
        selected = list(selection) if isinstance(selection, list) else selection
        self.calls.append(
            FakeCall(
                "analyze",
                {"db": db, "selection": selected, "accuracy": accuracy, "sarif": sarif},
            )
        )
        if not self.analyze_results:
            return AnalyzeResult(seconds=0.0)
        return self.analyze_results.pop(0)

    def codecheck(self, db: Path, config: str, files: list[Path], out_dir: Path) -> Path:
        """Record the run and answer with the configured violations CSV."""
        self.calls.append(
            FakeCall(
                "codecheck",
                {"db": db, "config": config, "files": list(files), "out_dir": out_dir},
            )
        )
        return self.violations_csv if self.violations_csv is not None else out_dir / "results.csv"
