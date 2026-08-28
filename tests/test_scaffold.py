"""Scaffold checks for task 1.1: package skeleton, version and placeholder CLI."""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest
from typer.testing import CliRunner

LAYER_PACKAGES = [
    "scitools_hook",
    "scitools_hook.config",
    "scitools_hook.models",
    "scitools_hook.understand",
    "scitools_hook.git",
    "scitools_hook.analysis",
    "scitools_hook.analysis.structure",
    "scitools_hook.report",
    "scitools_hook.runner",
    "scitools_hook.cli",
]


def test_version_is_non_empty_string() -> None:
    package = importlib.import_module("scitools_hook")
    assert isinstance(package.__version__, str)
    assert package.__version__


@pytest.mark.parametrize("name", LAYER_PACKAGES)
def test_layer_package_is_importable(name: str) -> None:
    assert importlib.import_module(name).__name__ == name


def test_cli_help_via_runner_prints_usage() -> None:
    app_module = importlib.import_module("scitools_hook.cli.app")
    result = CliRunner().invoke(app_module.app, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_cli_help_via_module_entry_prints_usage() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "scitools_hook.cli.app", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Usage" in completed.stdout
