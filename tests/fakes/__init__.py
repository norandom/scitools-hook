"""Test doubles for the adapters, added by the task that owns each real implementation.

Each fake **subclasses** the class it stands in for. That is not decoration: mypy checks an
override against the base method, so a signature that drifts from the real adapter fails the
type check instead of failing a test months later. A ``Protocol`` would not do it —
``runtime_checkable`` only checks that an attribute exists, never what it accepts.
"""

from fakes.und_cli import FakeCall, FakeUndCli

__all__ = ["FakeCall", "FakeUndCli"]
