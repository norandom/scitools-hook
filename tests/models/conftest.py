"""Make ``tests/`` importable so test modules can ``from fixtures import snapshot_fixture``.

The snapshot fixtures live in ``tests/fixtures/`` as a package (loader in its ``__init__``)
so every later task can load them. Pytest only inserts the directory of the collected test
module into ``sys.path``; task 3.2 adds ``tests/conftest.py``, after which pytest inserts
``tests/`` itself and this shim becomes redundant.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
