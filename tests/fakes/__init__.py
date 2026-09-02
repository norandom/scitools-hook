"""Test doubles for the adapters, added by the task that owns each real implementation.

Each fake **subclasses** the class it stands in for. That is not decoration: mypy checks an
override against the base method, so a signature that drifts from the real adapter fails the
type check instead of failing a test months later. A ``Protocol`` would not do it —
``runtime_checkable`` only checks that an attribute exists, never what it accepts.

The fixture-backed adapters are the exception, and deliberately so: ``FixtureUndCli`` and
``FixtureApiRunner`` are *shipped* in :mod:`scitools_hook.understand.fake` because the
``SCITOOLS_HOOK_FAKE_UNDERSTAND`` seam is a documented product feature, not a test aid. They
are re-exported here so a test reaches for one fake package and cannot end up writing a
second, divergent implementation of the same fixture directory.
"""

from fakes.und_cli import FakeCall, FakeUndCli
from scitools_hook.understand.fake import FixtureApiRunner, FixtureUndCli

__all__ = ["FakeCall", "FakeUndCli", "FixtureApiRunner", "FixtureUndCli"]
