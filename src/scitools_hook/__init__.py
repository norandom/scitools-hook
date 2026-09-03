"""scitools-hook: a maintainability gate backed by SciTools Understand.

``__version__`` is read from the installed distribution metadata rather than written here,
so ``pyproject.toml`` is the single place a version exists. It was two places until the
release work found them disagreeing: ``pyproject.toml`` said ``0.1.0a1`` while this module
still said ``0.1.0``, and the wheel installed, ran, and reported the older number. That string
is not cosmetic -- it travels into ``RunResult.tool_version`` and therefore into every SARIF
report the gate writes, so a stale copy misattributes findings to a version that never
produced them.

The fallback covers running from a source tree with nothing installed, which is the one case
where the metadata is genuinely absent. It is deliberately not a plausible-looking number: a
version that cannot be established should be obvious in a report, not quietly wrong.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("scitools-hook")
except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
    __version__ = "0+unknown"
