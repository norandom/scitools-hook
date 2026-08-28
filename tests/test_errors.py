"""Construction and context fields of the typed error hierarchy."""

from __future__ import annotations

from pathlib import Path

import pytest

from scitools_hook.errors import (
    AnalysisFailedError,
    ArchitectureNotFoundError,
    ConfigError,
    GateError,
    LicenseError,
    NotAGitRepositoryError,
    UnderstandNotFoundError,
)


def test_str_is_the_message_and_hint_defaults_to_none() -> None:
    err = GateError("something broke")
    assert str(err) == "something broke"
    assert err.message == "something broke"
    assert err.args == ("something broke",)
    assert err.hint is None


def test_hint_round_trips() -> None:
    err = GateError("no", hint="set --scitools-home")
    assert err.hint == "set --scitools-home"
    assert str(err) == "no"


def test_config_error_context_round_trips() -> None:
    err = ConfigError("unknown key", file=Path("/tmp/x.toml"), key="thresholds.bogus", hint="h")
    assert err.file == Path("/tmp/x.toml")
    assert err.key == "thresholds.bogus"
    assert err.hint == "h"
    assert str(err) == "unknown key"


def test_config_error_context_defaults_to_none() -> None:
    err = ConfigError("bad")
    assert err.file is None
    assert err.key is None
    assert err.hint is None


def test_understand_not_found_keeps_tried_locations_as_a_list() -> None:
    err = UnderstandNotFoundError("not found", tried=("/opt/scitools", "~/scitools"))
    assert err.tried == ["/opt/scitools", "~/scitools"]
    assert isinstance(err.tried, list)
    assert UnderstandNotFoundError("not found").tried == []


def test_license_error_carries_und_output() -> None:
    err = LicenseError("no license", und_output="License check failed: ...")
    assert err.und_output == "License check failed: ..."
    assert LicenseError("no license").und_output == ""


def test_analysis_failed_carries_command_and_stderr() -> None:
    err = AnalysisFailedError("und failed", command=("und", "analyze"), stderr="boom")
    assert err.command == ["und", "analyze"]
    assert isinstance(err.command, list)
    assert err.stderr == "boom"
    bare = AnalysisFailedError("und failed")
    assert bare.command == []
    assert bare.stderr == ""


def test_not_a_git_repository_error_is_a_plain_gate_error() -> None:
    err = NotAGitRepositoryError("not a git repo", hint="run inside a repository")
    assert str(err) == "not a git repo"
    assert err.hint == "run inside a repository"


def test_architecture_not_found_carries_available_and_config_context() -> None:
    err = ArchitectureNotFoundError(
        "architecture 'Foo' not found",
        available=("Bar", "Baz"),
        file=Path("cfg.toml"),
        key="structure.architectures",
        hint="pick one of the available architectures",
    )
    assert err.available == ["Bar", "Baz"]
    assert isinstance(err.available, list)
    assert err.file == Path("cfg.toml")
    assert err.key == "structure.architectures"
    assert err.hint == "pick one of the available architectures"
    assert ArchitectureNotFoundError("x").available == []


def test_context_lists_are_copies_of_the_input() -> None:
    tried = ["/a"]
    err = UnderstandNotFoundError("nf", tried=tried)
    tried.append("/b")
    assert err.tried == ["/a"]


@pytest.mark.parametrize(
    "err",
    [
        ConfigError("c"),
        UnderstandNotFoundError("u"),
        LicenseError("l"),
        AnalysisFailedError("a"),
        NotAGitRepositoryError("g"),
        ArchitectureNotFoundError("r"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_every_error_can_be_raised_and_caught_as_gate_error(err: GateError) -> None:
    with pytest.raises(GateError) as caught:
        raise err
    assert caught.value is err
    assert caught.value.exit_code is type(err).exit_code
