"""``doctor`` against a real, licensed Understand (task 8.2; req 1.2, 1.4, 1.5).

Every other test of this pipeline stands in a shell script for ``und`` and ``upython``. This
one runs the real thing, which is the only way to prove the part that cannot be faked: that
:class:`~scitools_hook.runner.context.RealProbes` speaks the protocol the installed
interpreters actually answer with.

It also pins the correction the project made to a core finding on 2026-08-30: **the
in-process API import is not broken -- only ``Ent.draw`` is.** Measured here, both probes
report the same API version, and ``auto`` still chooses ``upython`` because drawing resolves
only there. A regression to "in-process cannot import" would show up as a failing probe, and
the reason for preferring ``upython`` would silently change from "draw needs it" back to the
generalisation ``research.md`` had to correct.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from scitools_hook.runner.context import ContextOptions
from scitools_hook.runner.doctor import run_doctor

pytestmark = pytest.mark.contract

API_VERSION = re.compile(r"^\d+\.\d+")
"""The Python API reports a product version (``6.5.1204``); ``und version`` does not."""


def test_doctor_diagnoses_the_installed_understand(tmp_path: Path) -> None:
    """The whole report, from a real installation, with nothing to complain about."""
    report = run_doctor(ContextOptions(cwd=tmp_path, env=dict(os.environ)))
    understand = report.understand
    assert understand.env is not None
    assert understand.env.home == Path(os.environ["SCITOOLS_HOME"])
    assert "Build" in (understand.und_version or "")
    assert understand.license is not None and understand.license.ok
    assert report.problems == []


def test_both_api_probes_answer_and_upython_is_the_one_chosen(tmp_path: Path) -> None:
    """The measured truth: in-process imports fine, and ``upython`` is still preferred."""
    report = run_doctor(ContextOptions(cwd=tmp_path, env=dict(os.environ)))
    probes = {probe.mode: probe for probe in report.understand.probes}
    assert probes["upython"].ok, probes["upython"].detail
    assert probes["inprocess"].ok, probes["inprocess"].detail
    assert API_VERSION.match(probes["upython"].version)
    assert probes["inprocess"].version == probes["upython"].version
    assert report.understand.verified
    assert report.understand.api_mode == "upython"


def test_doctor_outside_a_repository_reports_no_repository_and_no_cache(tmp_path: Path) -> None:
    """Requirement 12.5 on a real machine: no working tree, no cache, still a full report."""
    report = run_doctor(ContextOptions(cwd=tmp_path, env=dict(os.environ)))
    assert not report.git.inside_repository
    assert report.cache is None
    assert report.state is None
    assert report.python
