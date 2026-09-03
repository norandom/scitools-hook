"""Fixtures for the end-to-end suite; the harness itself is :mod:`e2e.harness`.

This file is ``e2e.conftest`` rather than ``conftest`` because ``tests/e2e/__init__.py``
exists -- see that file for why that matters.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from e2e.harness import VIOLATING, Workspace, make_workspace, real_user_hook_paths, stamp
from scitools_hook.understand.fake import FAKE_VAR


@pytest.fixture(autouse=True)
def real_user_hooks_untouched() -> Iterator[None]:
    """Fail the test that writes a pre-commit hook into the developer's own configuration.

    The guard inside :func:`e2e.harness.isolated_env` is meant to make this unreachable; this
    is the measurement that says so rather than the belief. It runs around **every** test in
    this package, because the harm is done by the first child process that reaches
    ``install-hook --global`` with an inherited environment, whichever test starts it.
    """
    watched = real_user_hook_paths()
    before = {path: stamp(path) for path in watched}
    yield
    after = {path: stamp(path) for path in watched}
    assert after == before, (
        f"a test changed the developer's own global pre-commit hook: {before} -> {after}"
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    """A fresh repository, sandbox and environment, pointed at the violating fixtures."""
    return make_workspace(tmp_path, **{FAKE_VAR: str(VIOLATING)})
