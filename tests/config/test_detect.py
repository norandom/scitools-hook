"""Evidence-based scope detection: every detector, with a positive case proving it fires.

The shape of this file is set by a recorded hazard: **an assertion that something is ABSENT
passes just as happily when the search is broken.** Every "this does not fire" case below is
therefore paired with a sibling that *does* fire, built from a different input rather than a
different name -- the negative case for the generated-header detector is the real
``codegen/generator.py``/``client/gen.py`` pair, two files whose text contains the same
sentence and whose headers do not.

:func:`test_the_glob_language_agrees_with_the_shadow_filter` is the other structural test
here. ``config`` may not import ``git``, so ``config.models.compile_path_pattern`` is a second
implementation of the pattern language ``git.shadow`` already speaks. A test is the only thing
that can stop the two drifting into one name with two meanings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitools_hook.config.detect import (
    GENERATED_MARKERS,
    PARSE_REASONS,
    Detection,
    Evidence,
    Region,
    detect,
)
from scitools_hook.config.models import ProjectSettings, matching_pattern
from scitools_hook.git.shadow import PathFilter

# --- helpers ---------------------------------------------------------------------


def build(root: Path, files: dict[str, str]) -> list[str]:
    """Write ``files`` under ``root`` and answer them as a tracked-path list."""
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return sorted(files)


def patterns_for(found: Detection, role: str) -> list[str]:
    return [region.pattern for region in found.regions if region.role == role]


def region_named(found: Detection, pattern: str) -> Region:
    matching = [region for region in found.regions if region.pattern == pattern]
    assert matching, f"no region for {pattern!r}; got {[r.pattern for r in found.regions]}"
    return matching[0]


# --- .gitattributes: the declaration signal ---------------------------------------


def test_a_generated_declaration_in_a_subdirectory_is_anchored_to_that_directory(
    tmp_path: Path,
) -> None:
    """The exact shape measured in a real repository: ``/sdk/**`` inside ``.dagger/module``."""
    tracked = build(
        tmp_path,
        {
            ".dagger/module/.gitattributes": "/sdk/** linguist-generated\n",
            ".dagger/module/sdk/gen.py": "x = 1\n",
            "sdk/mine.py": "y = 2\n",
        },
    )
    found = detect(tmp_path, tracked)
    region = region_named(found, ".dagger/module/sdk/**")
    assert region.role == "generated"
    assert region.evidence.source == ".dagger/module/.gitattributes"
    assert region.evidence.detail == "/sdk/** linguist-generated"
    assert region.files == (".dagger/module/sdk/gen.py",)
    assert found.role_of("sdk/mine.py") == "product", "the top-level sdk/ is not the declared one"


def test_a_vendored_declaration_is_reported_as_vendored(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {".gitattributes": "third_party/** linguist-vendored\n", "third_party/lib.py": "x = 1\n"},
    )
    found = detect(tmp_path, tracked)
    assert patterns_for(found, "vendored") == ["third_party/**"]


@pytest.mark.parametrize(
    "line",
    [
        "# vendor/** linguist-generated",
        "vendor/** -linguist-generated",
        "vendor/** !linguist-generated",
        "vendor/** linguist-generated=false",
        "[attr]binary linguist-generated",
    ],
)
def test_the_four_ways_of_saying_no_are_not_a_declaration(tmp_path: Path, line: str) -> None:
    """The case a grep for the attribute name gets wrong, one input per spelling."""
    tracked = build(tmp_path, {".gitattributes": f"{line}\n", "vendor/lib.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "generated") == []


def test_the_same_file_with_the_attribute_set_does_declare_it(tmp_path: Path) -> None:
    """The positive sibling: the search above is not simply broken."""
    tracked = build(
        tmp_path, {".gitattributes": "vendor/** linguist-generated\n", "vendor/lib.py": "x = 1\n"}
    )
    assert patterns_for(detect(tmp_path, tracked), "generated") == ["vendor/**"]


def test_an_attribute_set_to_true_is_a_declaration(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {".gitattributes": "vendor/** linguist-vendored=true\n", "vendor/lib.py": "x = 1\n"},
    )
    assert patterns_for(detect(tmp_path, tracked), "vendored") == ["vendor/**"]


def test_a_quoted_pattern_keeps_the_space_inside_it(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {
            ".gitattributes": '"third party/**" linguist-vendored\n',
            "third party/lib.py": "x = 1\n",
        },
    )
    found = detect(tmp_path, tracked)
    assert region_named(found, "third party/**").files == ("third party/lib.py",)


def test_a_bare_name_in_a_subdirectory_matches_at_any_depth_below_it(tmp_path: Path) -> None:
    """git's own rule for a pattern with no slash, which decides what the written line means."""
    tracked = build(
        tmp_path,
        {
            "pkg/.gitattributes": "gen.py linguist-generated\n",
            "pkg/gen.py": "x = 1\n",
            "pkg/deep/gen.py": "x = 1\n",
            "gen.py": "x = 1\n",
        },
    )
    found = detect(tmp_path, tracked)
    region = region_named(found, "pkg/**/gen.py")
    assert region.files == ("pkg/deep/gen.py", "pkg/gen.py")
    assert found.role_of("gen.py") == "product", "the root gen.py is outside pkg/"


def test_an_untracked_attributes_file_is_not_a_declaration(tmp_path: Path) -> None:
    """A declaration is what the repository committed, not what one working copy holds."""
    build(tmp_path, {"vendor/lib.py": "x = 1\n"})
    (tmp_path / ".gitattributes").write_text("vendor/** linguist-vendored\n", encoding="utf-8")
    assert patterns_for(detect(tmp_path, ["vendor/lib.py"]), "vendored") == []
    listed = detect(tmp_path, ["vendor/lib.py", ".gitattributes"])
    assert patterns_for(listed, "vendored") == ["vendor/**"], "and it fires once it is tracked"


def test_a_declaration_covering_no_tracked_file_is_still_reported(tmp_path: Path) -> None:
    """Measured on a real repository: ``/sdk/**`` is declared and ``.gitignore``d as well."""
    tracked = build(tmp_path, {".gitattributes": "sdk/** linguist-generated\n"})
    region = region_named(detect(tmp_path, tracked), "sdk/**")
    assert region.covered == 0


# --- tool configuration ------------------------------------------------------------


def test_pytest_testpaths_names_the_test_tree(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests", "itests"]\n',
            "tests/test_a.py": "def test_a():\n    pass\n",
        },
    )
    found = detect(tmp_path, tracked)
    assert patterns_for(found, "tests") == ["itests/**", "tests/**"]
    assert region_named(found, "tests/**").evidence.source == "pyproject.toml"


def test_a_pyproject_without_pytest_settings_names_no_test_tree(tmp_path: Path) -> None:
    files = {"pyproject.toml": '[project]\nname = "x"\n', "tests/a.py": "x = 1\n"}
    tracked = build(tmp_path, files)
    assert patterns_for(detect(tmp_path, tracked), "tests") == []


def test_testpaths_in_a_subproject_are_relative_to_that_subproject(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {
            "packages/lib/pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "packages/lib/tests/test_a.py": "x = 1\n",
        },
    )
    assert patterns_for(detect(tmp_path, tracked), "tests") == ["packages/lib/tests/**"]


@pytest.mark.parametrize("name", ["setup.cfg", "tox.ini"])
def test_testpaths_in_an_ini_file_name_the_test_tree(tmp_path: Path, name: str) -> None:
    tracked = build(
        tmp_path, {name: "[tool:pytest]\ntestpaths = tests suite\n", "tests/a.py": "x = 1\n"}
    )
    assert patterns_for(detect(tmp_path, tracked), "tests") == ["suite/**", "tests/**"]


def test_alembic_script_location_names_the_migration_tree(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {
            "alembic.ini": "[alembic]\nscript_location = migrations\n",
            "migrations/versions/0001.py": "x = 1\n",
        },
    )
    region = region_named(detect(tmp_path, tracked), "migrations/**")
    assert region.role == "generated"
    assert region.evidence.detail == "[alembic] script_location = migrations"


def test_an_alembic_file_with_logging_interpolation_still_reads(tmp_path: Path) -> None:
    """``ConfigParser`` raises on ``%(levelname)s``; a stock ``alembic.ini`` carries one."""
    text = (
        "[alembic]\nscript_location = %(here)s/migrations\n\n"
        "[formatter_generic]\nformat = %(levelname)-5.5s [%(name)s] %(message)s\n"
    )
    tracked = build(tmp_path, {"alembic.ini": text, "migrations/env.py": "x = 1\n"})
    region = region_named(detect(tmp_path, tracked), "migrations/**")
    assert region.files == ("migrations/env.py",)


def test_an_unparsable_manifest_yields_no_region_and_does_not_raise(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"pyproject.toml": "this is not = = toml\n"})
    assert detect(tmp_path, tracked).regions == ()


# --- generated-code banners --------------------------------------------------------

GENERATOR = """\
import enum


class Generator:
    def render(self):
        return "# Code generated by dagger. DO NOT EDIT."
"""
"""A real shape: the *generator*, whose job is to emit the banner. Its own header has none."""

GENERATED = """\
# Code generated by dagger. DO NOT EDIT.

import warnings
"""

DOCUMENTED = '''\
"""Code generated by a tool: DO NOT EDIT is the banner this module searches for."""

VALUE = 1
'''
"""The recorded false-green: prose about the marker, in the module documenting the marker."""


def test_a_banner_in_the_leading_comment_is_a_generated_file(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"pkg/gen.py": GENERATED, "pkg/hand.py": "x = 1\n"})
    region = region_named(detect(tmp_path, tracked), "pkg/gen.py")
    assert region.role == "generated"
    assert region.evidence.detail == "# Code generated by dagger. DO NOT EDIT."


def test_the_same_sentence_inside_the_code_is_not(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"pkg/generator.py": GENERATOR, "pkg/hand.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "generated") == []


def test_the_same_sentence_inside_a_docstring_is_not(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"pkg/about.py": DOCUMENTED, "pkg/hand.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "generated") == []


def test_the_generator_and_the_generated_file_side_by_side(tmp_path: Path) -> None:
    """Both inputs at once: exactly one of the two is reported, and it is the right one."""
    tracked = build(tmp_path, {"pkg/generator.py": GENERATOR, "pkg/gen.py": GENERATED})
    assert patterns_for(detect(tmp_path, tracked), "generated") == ["pkg/gen.py"]


def test_a_go_style_banner_is_read_from_a_slash_comment(tmp_path: Path) -> None:
    text = "// Code generated by protoc-gen-go. DO NOT EDIT.\n\npackage main\n"
    tracked = build(tmp_path, {"pkg/api.pb.go": text, "pkg/main.go": "package main\n"})
    assert patterns_for(detect(tmp_path, tracked), "generated") == ["pkg/api.pb.go"]


def test_a_banner_below_the_first_statement_is_not_a_header(tmp_path: Path) -> None:
    text = "package main\n\n// Code generated by hand. DO NOT EDIT.\n"
    tracked = build(tmp_path, {"pkg/main.go": text})
    assert patterns_for(detect(tmp_path, tracked), "generated") == []


def test_a_directory_whose_source_files_are_all_generated_becomes_one_region(
    tmp_path: Path,
) -> None:
    tracked = build(
        tmp_path,
        {"pkg/a.py": GENERATED, "pkg/b.py": GENERATED, "pkg/README.md": "not source\n"},
    )
    found = detect(tmp_path, tracked)
    assert patterns_for(found, "generated") == ["pkg/**"]
    assert region_named(found, "pkg/**").evidence.source == "pkg/a.py"


def test_one_hand_written_file_keeps_the_report_per_file(tmp_path: Path) -> None:
    """The sibling of the case above, differing in its input rather than in its name."""
    tracked = build(tmp_path, {"pkg/a.py": GENERATED, "pkg/b.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "generated") == ["pkg/a.py"]


@pytest.mark.parametrize("marker", GENERATED_MARKERS)
def test_every_published_marker_fires(tmp_path: Path, marker: str) -> None:
    """Each phrase in the published tuple is reachable; a dead entry is a lie in the docs."""
    tracked = build(tmp_path, {"pkg/a.py": f"# {marker} v1\n\nx = 1\n", "pkg/b.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "generated") == ["pkg/a.py"]


def test_a_file_with_no_source_suffix_is_never_opened_for_a_banner(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"notes.md": "@generated\n", "pkg/a.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "generated") == []


def test_a_tracked_path_missing_from_the_working_tree_is_not_evidence(tmp_path: Path) -> None:
    assert detect(tmp_path, ["gone.py", ".gitattributes"]).regions == ()


def test_a_truncated_header_still_yields_its_comments(tmp_path: Path) -> None:
    """The header read stops at 4096 bytes, which can cut a literal in half mid-token."""
    text = "# @generated\nTEXT = '''" + ("x" * 8000) + "'''\n"
    tracked = build(tmp_path, {"pkg/big.py": text, "pkg/hand.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "generated") == ["pkg/big.py"]


# --- sub-projects ------------------------------------------------------------------


def test_a_manifest_below_the_root_marks_a_subproject(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "root"\n',
            "packages/client/pyproject.toml": '[project]\nname = "client"\n',
            "packages/client/src/a.py": "x = 1\n",
        },
    )
    found = detect(tmp_path, tracked)
    subprojects = [r for r in found.regions if r.evidence.signal == "subproject"]
    assert [region.pattern for region in subprojects] == ["packages/client/**"]
    assert subprojects[0].role == "product", "a manifest says a project starts here, no more"


def test_the_root_manifest_is_not_a_subproject(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"pyproject.toml": '[project]\nname = "root"\n'})
    assert [r for r in detect(tmp_path, tracked).regions if r.evidence.signal == "subproject"] == []


def test_two_manifests_in_one_directory_yield_one_region(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {"pkg/pyproject.toml": "", "pkg/setup.py": "", "pkg/a.py": "x = 1\n"},
    )
    found = [r for r in detect(tmp_path, tracked).regions if r.evidence.signal == "subproject"]
    assert [region.pattern for region in found] == ["pkg/**"]


# --- what the configuration already drops ------------------------------------------


def test_the_configured_excludes_are_reported_with_the_pattern_that_decided(
    tmp_path: Path,
) -> None:
    tracked = build(tmp_path, {"build/out.py": "x = 1\n", "src/a.py": "x = 1\n"})
    project = ProjectSettings(include=["**"], exclude=["build/**"])
    found = detect(tmp_path, tracked, project)
    region = region_named(found, "build/**")
    assert region.role == "not-analysed"
    assert region.evidence.detail == "[project] exclude = 'build/**'"
    assert found.role_of("src/a.py") == "product"


def test_a_path_no_include_pattern_selects_is_reported_too(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"src/a.py": "x = 1\n", "docs/b.py": "x = 1\n"})
    project = ProjectSettings(include=["src/**"], exclude=[])
    found = detect(tmp_path, tracked, project)
    assert region_named(found, "docs/b.py").role == "not-analysed"
    assert "include" in region_named(found, "docs/b.py").evidence.detail


def test_without_a_project_nothing_is_reported_as_not_analysed(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"build/out.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "not-analysed") == []


# --- the report -------------------------------------------------------------------


def test_the_strongest_role_wins_when_two_regions_cover_one_path(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {
            ".gitattributes": "vendor/** linguist-vendored\n",
            "vendor/lib.py": "x = 1\n",
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["vendor"]\n',
        },
    )
    found = detect(tmp_path, tracked)
    assert {region.role for region in found.covering("vendor/lib.py")} == {"vendored", "tests"}
    assert found.role_of("vendor/lib.py") == "vendored", "vendored outranks tests"


def test_evidence_describes_itself_with_the_signal_and_the_file() -> None:
    evidence = Evidence(signal="gitattributes", source=".gitattributes", detail="a/** x")
    assert evidence.describe() == "gitattributes in .gitattributes: a/** x"
    assert Evidence(signal="excluded", source="", detail="d").describe() == "excluded: d"


def test_detection_is_deterministic(tmp_path: Path) -> None:
    tracked = build(
        tmp_path,
        {
            ".gitattributes": "vendor/** linguist-vendored\ngen/** linguist-generated\n",
            "vendor/a.py": "x = 1\n",
            "gen/b.py": "x = 1\n",
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "tests/c.py": "x = 1\n",
        },
    )
    assert detect(tmp_path, tracked) == detect(tmp_path, list(reversed(tracked)))


def test_a_repository_that_declares_nothing_yields_no_regions(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"src/a.py": "x = 1\n", "README.md": "hello\n"})
    assert detect(tmp_path, tracked).regions == ()
    assert detect(tmp_path, tracked).role_of("src/a.py") == "product"


# --- the pattern language ----------------------------------------------------------

PATTERN_CORPUS = [
    "**",
    "tests/**",
    "/tests/**",
    "node_modules/**",
    "*.min.js",
    "*.generated.*",
    "build",
    "a/b?c.py",
    "**/gen.py",
    "vendor/*/dist/**",
    "",
]

PATH_CORPUS = [
    "tests/a/b.py",
    "src/tests/a.py",
    "node_modules/x/y.js",
    "src/vendor/x/dist/a.js",
    "vendor/x/dist/a.js",
    "app.min.js",
    "a/bxc.py",
    "a/b/gen.py",
    "gen.py",
    "build/out.o",
    "src/a.py",
]


@pytest.mark.parametrize("pattern", PATTERN_CORPUS)
def test_the_glob_language_agrees_with_the_shadow_filter(pattern: str) -> None:
    """``config`` may not import ``git``, so the two implementations are compared instead.

    Without this, ``config.models.compile_path_pattern`` and ``git.shadow._translate`` are one
    documented language with two implementations and nothing holding them together -- and a
    generated ``exclude`` line would then mean one thing to ``init`` and another to the run.
    """
    shadow = PathFilter(include=[pattern], exclude=[])
    for path in PATH_CORPUS:
        mine = matching_pattern([pattern], path) is not None
        assert mine == shadow.allows(path), f"{pattern!r} vs {path!r}"


def test_the_pattern_comparison_can_fail() -> None:
    """The corpus above is asserted to be discriminating, not merely all-True or all-False."""
    verdicts = {
        (pattern, path): matching_pattern([pattern], path) is not None
        for pattern in PATTERN_CORPUS
        for path in PATH_CORPUS
    }
    assert any(verdicts.values()) and not all(verdicts.values())


def test_a_blank_pattern_selects_nothing() -> None:
    assert matching_pattern(["", "src/**"], "src/a.py") == "src/**"
    assert matching_pattern([""], "src/a.py") is None


def test_the_first_matching_pattern_is_the_one_reported() -> None:
    assert matching_pattern(["a/**", "**"], "a/b.py") == "a/**"
    assert matching_pattern(["**", "a/**"], "a/b.py") == "**"


# --- files Understand cannot read --------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
HINTS_MODULE = REPO_ROOT / "src" / "scitools_hook" / "report" / "hints.py"


@pytest.mark.parametrize(
    ("source", "construct"),
    [
        ("def gen[T](v: T) -> T:\n    return v\n", "line 1: def gen[T]"),
        ("async def agen[T](v: T) -> T:\n    return v\n", "line 1: def agen[T]"),
        ("def ps[**P](v: int) -> int:\n    return v\n", "line 1: def ps[P]"),
        ("class Box[T]:\n    pass\n", "line 1: class Box[T]"),
        ("def two[A, B](v: int) -> int:\n    return v\n", "line 1: def two[A, B]"),
    ],
)
def test_a_type_parameter_list_is_reported_as_truncating(
    tmp_path: Path, source: str, construct: str
) -> None:
    """Measured on Understand 6.5.1204: everything after such a declaration is lost."""
    tracked = build(tmp_path, {"src/a.py": source})
    (evidence,) = detect(tmp_path, tracked).limitations
    assert evidence.signal == "pep695"
    assert evidence.source == "src/a.py"
    assert evidence.detail == construct


@pytest.mark.parametrize(
    "source",
    ["type A = int\n", "type A = int | str\n", "type A[T] = list[T]\n"],
)
def test_a_type_statement_is_reported_as_the_cheaper_kind(tmp_path: Path, source: str) -> None:
    """The other half of the same measurement: the parse error is real, the truncation is not.

    All three raise ``Error: expected newline at token A`` on 6.5.1204 and all three leave the
    routines after them in the database. Reporting them under the truncating signal would put
    a false sentence -- "nothing after it is measured" -- into a generated configuration.
    """
    tracked = build(tmp_path, {"src/a.py": f"def before() -> int:\n    return 1\n\n\n{source}"})
    (evidence,) = detect(tmp_path, tracked).limitations
    assert evidence.signal == "pep695-alias"
    assert evidence.detail.startswith("line 5: type A = ...")


def test_the_two_signals_carry_different_reasons() -> None:
    assert set(PARSE_REASONS) == {"pep695", "pep695-alias"}
    assert PARSE_REASONS["pep695"] != PARSE_REASONS["pep695-alias"]
    assert "nothing after" in PARSE_REASONS["pep695"]
    assert "still measured" in PARSE_REASONS["pep695-alias"]


def test_a_file_with_both_kinds_is_reported_as_the_truncating_one(tmp_path: Path) -> None:
    """Even when the alias comes first: what the acknowledgement must say is the worse cost."""
    source = "type A = int\n\n\ndef gen[T](v: T) -> T:\n    return v\n"
    tracked = build(tmp_path, {"src/a.py": source})
    (evidence,) = detect(tmp_path, tracked).limitations
    assert evidence.signal == "pep695"
    assert evidence.detail == "line 4: def gen[T]"


def test_ordinary_generics_are_not_reported(tmp_path: Path) -> None:
    """The spelling this project itself uses, and which Understand reads to the end."""
    source = (
        'from typing import TypeVar\n\nT = TypeVar("T")\n\n\ndef gen(v: T) -> T:\n    return v\n'
    )
    tracked = build(tmp_path, {"src/a.py": source})
    assert detect(tmp_path, tracked).limitations == ()


def test_a_subscript_that_is_not_a_type_parameter_list_is_not_reported(tmp_path: Path) -> None:
    """``def`` and ``[`` on one line without being a declaration; the shape a regex confuses."""
    source = "def gen(values: list[int]) -> int:\n    return values[0]\n"
    tracked = build(tmp_path, {"src/a.py": source})
    assert detect(tmp_path, tracked).limitations == ()


def test_the_construct_written_inside_a_string_is_not_reported(tmp_path: Path) -> None:
    """The recorded false-green, in its own words: prose about the construct is not it."""
    source = (
        'HINT = "rewrite def f[T](x) as an explicit TypeVar"\n\n\ndef ok() -> int:\n    return 1\n'
    )
    tracked = build(tmp_path, {"src/a.py": source})
    assert detect(tmp_path, tracked).limitations == ()


def test_this_repositorys_own_hint_catalogue_is_the_real_negative_case(tmp_path: Path) -> None:
    """``report/hints.py`` documents the construct in hint text; a grep for it fires there.

    The file is copied into the fixture rather than detected in place, so the case is about
    the *content* and not about which repository happens to be on disk. The grep half is
    asserted too: without it, "the detector stayed quiet" would prove nothing.
    """
    text = HINTS_MODULE.read_text(encoding="utf-8")
    assert "[T]" in text, "the catalogue no longer quotes the construct; pick another witness"
    tracked = build(tmp_path, {"src/hints.py": text})
    assert detect(tmp_path, tracked).limitations == ()


def test_a_python_file_the_interpreter_cannot_parse_reports_nothing(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"src/a.py": "def broken(:\n"})
    assert detect(tmp_path, tracked).limitations == ()


def test_a_non_python_file_is_never_parsed_for_type_parameters(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"src/a.txt": "def gen[T](v):\n    return v\n"})
    assert detect(tmp_path, tracked).limitations == ()


def test_the_limitations_are_ordered_by_path(tmp_path: Path) -> None:
    source = "def gen[T](v: T) -> T:\n    return v\n"
    tracked = build(tmp_path, {"src/b.py": source, "src/a.py": source})
    found = detect(tmp_path, tracked)
    assert [item.source for item in found.limitations] == ["src/a.py", "src/b.py"]


# --- the shapes the readers have to survive ----------------------------------------


def test_an_unterminated_quoted_pattern_is_skipped(tmp_path: Path) -> None:
    tracked = build(
        tmp_path, {".gitattributes": '"vendor/** linguist-vendored\n', "vendor/lib.py": "x = 1\n"}
    )
    assert detect(tmp_path, tracked).regions == ()


def test_an_anchored_file_pattern_in_a_subdirectory_resolves_to_that_file(tmp_path: Path) -> None:
    """The second shape measured in the real repository: ``/dagger.gen.go`` in a nested file."""
    tracked = build(
        tmp_path,
        {
            "sdk/runtime/.gitattributes": "/dagger.gen.go linguist-generated\n",
            "sdk/runtime/dagger.gen.go": "package main\n",
            "dagger.gen.go": "package main\n",
        },
    )
    found = detect(tmp_path, tracked)
    region = region_named(found, "sdk/runtime/dagger.gen.go")
    assert region.files == ("sdk/runtime/dagger.gen.go",)
    assert found.role_of("dagger.gen.go") == "product"


def test_an_ini_without_pytest_or_alembic_settings_yields_nothing(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"tox.ini": "[tox]\nenvlist = py312\n", "alembic.ini": "[alembic]\n"})
    assert detect(tmp_path, tracked).regions == ()


def test_an_alembic_script_location_of_the_ini_directory_itself_yields_nothing(
    tmp_path: Path,
) -> None:
    tracked = build(tmp_path, {"alembic.ini": "[alembic]\nscript_location = %(here)s\n"})
    assert detect(tmp_path, tracked).regions == ()


def test_a_malformed_ini_is_read_as_no_evidence(tmp_path: Path) -> None:
    tracked = build(tmp_path, {"alembic.ini": "script_location = migrations\n"})
    assert detect(tmp_path, tracked).regions == ()


def test_a_pyproject_whose_tool_table_is_not_a_table_is_read_as_no_evidence(
    tmp_path: Path,
) -> None:
    tracked = build(tmp_path, {"pyproject.toml": "tool = 1\n", "tests/a.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "tests") == []


def test_a_header_that_cannot_be_tokenised_keeps_the_comments_before_the_break(
    tmp_path: Path,
) -> None:
    """The read stops at 4096 bytes and can leave a literal open; the header is still a header."""
    text = "# @generated\n'''" + ("x" * 8000)
    tracked = build(tmp_path, {"pkg/a.py": text, "pkg/b.py": "x = 1\n"})
    assert patterns_for(detect(tmp_path, tracked), "generated") == ["pkg/a.py"]
