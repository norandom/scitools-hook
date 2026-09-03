"""End-to-end tests (task 10.2): the installed console script, driven the way a user drives it.

This directory is a **package** on purpose. A ``conftest.py`` in a directory with no
``__init__.py`` is imported under the bare module name ``conftest``, which is the name
``tests/conftest.py`` already holds and a dozen modules import from; the first version of this
suite shadowed it and broke sixteen of them. With this file present the local conftest becomes
``e2e.conftest`` and both live side by side.
"""
