"""A hand-declared architecture, measured against the installed Understand (req 6.3, 6.7).

``structure.architecture`` defaults to ``"Directory Structure"``, and that architecture is
**derived from the directory layout**: it can only ever say what the folder tree already
says. A repository whose layers cut across its folders -- one directory holding two layers,
one layer spread over two directories -- cannot express its own rules through it at all.
This module measures the capability that fixes that: ``und import -arch`` puts an
architecture the repository *declares* into the database, and ``structure.layers`` then
judges declared layers instead of folders.

**The deliverable is the pair of tests under "the finding only a declared architecture can
produce".** One shows a real ``domain -> shells`` violation that ``Directory Structure``
cannot report *whatever layer rules are written*, because both files sit in the same folder
and a layer rule is silent inside one node. The other shows the mirror image: a legitimate
``domain -> domain`` dependency that ``Directory Structure`` reports as a violation, because
the two files sit in different folders. Between them they are the whole argument for the
feature.

Everything here runs the production adapters -- :class:`~scitools_hook.understand.und_cli.UndCli`
for the import and :class:`~scitools_hook.understand.snapshot.SnapshotExtractor` plus
:func:`~scitools_hook.analysis.structure.layers.evaluate_layers` for the verdict -- over a
real database built with plain ``und`` calls, because what is under test is whether
Understand and this code agree.

Four measurements are pinned here because each one is a way the feature can pass its tests
and fail in use:

* an imported architecture **survives** ``und analyze``, on the incremental path and the
  whole-project path alike, so a gate that rebuilds and re-syncs on every run does not lose
  it between commits;
* it does **not** survive being imported *before* the analysis: nodes imported into a
  database that has been ``add``-ed but not analysed come out empty and stay empty;
* a document naming files the project does not hold imports with status 0 and drops them
  silently, so an import that is not read back proves nothing;
* a malformed document exits 1 and changes nothing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pytest
from contract_project import contract_settings, real_env, run_und, und

from scitools_hook.analysis.structure.layers import evaluate_layers
from scitools_hook.config.models import LayerRule, Settings
from scitools_hook.errors import AnalysisFailedError
from scitools_hook.git.repo import GitRepo
from scitools_hook.git.shadow import ShadowSync
from scitools_hook.models.cache import CachePaths
from scitools_hook.models.findings import Finding
from scitools_hook.models.git import IndexTarget
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import ProjectSnapshot
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.database import ARCH_FILE, DatabaseManager
from scitools_hook.understand.snapshot import SnapshotExtractor, SnapshotTarget
from scitools_hook.understand.und_arch import (
    ArchNode,
    read_architecture,
    write_architecture,
)
from scitools_hook.understand.und_cli import (
    UndCli,
    _import_arch,
    _list_arches,
    _remove_arch,
)

pytestmark = pytest.mark.contract

DIRECTORY_STRUCTURE = "Directory Structure"
"""The folder-derived architecture every database has, and the one this feature is not."""

LAYERS = "Layers"
"""The architecture the repository declares, which no folder layout could produce."""

SOURCES: dict[str, str] = {
    # `api.py` and `store.py` are the top and the bottom of the layering and they share a
    # directory, so `Directory Structure` puts them in ONE node. `support/model.py` is in the
    # same layer as `store.py` and a different directory, so `Directory Structure` puts those
    # two in DIFFERENT nodes. Every claim this module makes rests on that arrangement.
    "src/core/api.py": '''"""The shells layer, in the same directory as the domain."""

from core.engine import Engine


def serve():
    return Engine().run()
''',
    "src/core/engine.py": '''"""The engine layer, between the shells and the domain."""

from core.store import Store


class Engine:
    def run(self):
        return Store().read()
''',
    "src/core/store.py": '''"""The domain layer, reaching back up into the shells: the leak."""

from core.api import serve


class Store:
    def read(self):
        return 1

    def leak(self):
        return serve()
''',
    "src/support/model.py": '''"""The domain layer again, elsewhere: one layer, two folders."""

from core.store import Store


class Model:
    def __init__(self):
        self.store = Store()
''',
}
"""A repository whose layers and whose folders deliberately disagree."""

FILES = tuple(sorted(SOURCES))
"""Every source file, repository-relative -- the snapshot request's ``files``."""

API = "src/core/api.py"
STORE = "src/core/store.py"
MODEL = "src/support/model.py"

LEAK = (STORE, API)
"""``domain -> shells``: a real violation, invisible to the directory structure."""

WITHIN_DOMAIN = (MODEL, STORE)
"""``domain -> domain``: legitimate, and a violation as far as the directory structure knows."""


@dataclass(frozen=True)
class Declared:
    """A real database over the sources above, with ``Layers`` declared in it."""

    root: Path
    db: Path
    cli: UndCli

    def member(self, *names: str) -> tuple[str, ...]:
        """Those repository-relative names as the absolute paths ``und`` resolves."""
        return tuple(str(self.root / name) for name in names)

    def declaration(self) -> ArchNode:
        """``Layers``: shells, engine and a domain that spans two directories."""
        return ArchNode(
            name=LAYERS,
            children=(
                ArchNode(name="shells", members=self.member(API)),
                ArchNode(name="engine", members=self.member("src/core/engine.py")),
                ArchNode(name="domain", members=self.member(STORE, MODEL)),
            ),
        )

    def snapshot(self, architecture: str) -> ProjectSnapshot:
        """Read this database through the production extractor under one architecture."""
        settings = contract_settings().model_copy(
            update={
                "structure": contract_settings().structure.model_copy(
                    update={"architecture": architecture}
                )
            }
        )
        return extract_under(self.db, self.root, settings)

    def analyze(self, *argv: str) -> None:
        """Run one more ``und analyze`` over this database, failing loudly if it refuses."""
        done = run_und("-db", str(self.db), "analyze", *argv, "-errors", "-warnings")
        assert done.returncode == 0, done.stdout + done.stderr


def extract_under(db: Path, root: Path, settings: Settings) -> ProjectSnapshot:
    """One database as a snapshot, through the extractor the check pipeline uses."""
    extractor = SnapshotExtractor(ApiRunner(real_env("upython"), NullCommandLog()), settings)
    return extractor.extract(SnapshotTarget(db=db, root=root, side="after", files=frozenset(FILES)))


def node_of(snapshot: ProjectSnapshot) -> dict[str, str]:
    """File -> architecture node, built exactly as ``runner.check`` builds it."""
    return {member: node.path for node in snapshot.arch_nodes for member in node.members}


def build(work: Path) -> Path:
    """Write the sources under ``work`` and build a database beside them."""
    for name, text in SOURCES.items():
        target = work / "tree" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    db = work / "project.und"
    for argv in (
        ["-quiet", "create", "-db", str(db), "-languages", "python", "-local"],
        ["-quiet", "-db", str(db), "add", str(work / "tree")],
        ["-db", str(db), "analyze", "-all", "-errors", "-warnings"],
    ):
        done = run_und(*argv)
        assert done.returncode == 0, f"und {' '.join(argv)}: {done.stdout}{done.stderr}"
    return db


@pytest.fixture(scope="module")
def declared(tmp_path_factory: pytest.TempPathFactory) -> Declared:
    """A built database with ``Layers`` imported into it through the production wrapper."""
    work = tmp_path_factory.mktemp("architecture-contract")
    db = build(work)
    cli = UndCli(real_env("upython"), NullCommandLog())
    project = Declared(root=work / "tree", db=db, cli=cli)
    resolved = cli.declare_architecture(db, project.declaration())
    assert resolved == set(project.member(API, "src/core/engine.py", STORE, MODEL))
    return project


# --- the schema a hand-written file has to satisfy -------------------------------


def test_the_exported_directory_structure_is_the_schema(declared: Declared) -> None:
    """What ``und export -arch`` writes is what a hand-written file has to look like.

    Read through the production reader, because the export is the only documentation of this
    format that exists: an ``<arch>`` per node, nested, with member paths as text one per
    line. The paths come back absolute because the wrapper resolves them against the
    directory holding the database, which is the frame Understand writes them in.
    """
    exported = declared.cli.export_arch(
        declared.db, DIRECTORY_STRUCTURE, declared.db.parent / "ds.xml"
    )

    assert exported.name == DIRECTORY_STRUCTURE
    assert set(exported.paths()) == set(declared.member(*FILES))
    assert read_architecture(
        (declared.db.parent / "ds.xml").read_text(encoding="utf-8"), "the export"
    ) == exported.rebase(lambda member: member.replace(f"{declared.db.parent}/", "./"))


def test_a_declared_architecture_is_listed_beside_the_directory_structure(
    declared: Declared,
) -> None:
    assert set(_list_arches(declared.cli, declared.db)) >= {DIRECTORY_STRUCTURE, LAYERS}


# --- the finding only a declared architecture can produce ------------------------


def domain_rule(node: str, allowed: list[str]) -> LayerRule:
    """One layer rule constraining ``node``."""
    return LayerRule(name="domain-may-not-reach-the-shells", node=node, may_depend_on=allowed)


def edges_of(snapshot: ProjectSnapshot) -> set[tuple[str, str]]:
    """The file dependency pairs the snapshot reports."""
    return {(edge.src, edge.dst) for edge in snapshot.file_edges}


def reported(findings: list[Finding], edge: tuple[str, str]) -> list[Finding]:
    """The findings that name one edge."""
    return [found for found in findings if (found.details["src"], found.details["dst"]) == edge]


def test_understand_sees_the_dependency_both_architectures_are_judged_on(
    declared: Declared,
) -> None:
    """The premise. Without this edge in the database neither test below means anything."""
    assert LEAK in edges_of(declared.snapshot(LAYERS))
    assert WITHIN_DOMAIN in edges_of(declared.snapshot(LAYERS))


def test_the_directory_structure_cannot_tell_the_two_layers_apart(
    declared: Declared,
) -> None:
    """Both ends of the violation sit in one folder, so one folder-derived node holds them."""
    nodes = node_of(declared.snapshot(DIRECTORY_STRUCTURE))

    assert nodes[STORE] == nodes[API]
    assert nodes[MODEL] != nodes[STORE]


def test_the_declared_architecture_tells_them_apart(declared: Declared) -> None:
    """The same two files, in the layers the repository declared them to be in."""
    nodes = node_of(declared.snapshot(LAYERS))

    assert nodes[STORE] == f"{LAYERS}/domain"
    assert nodes[API] == f"{LAYERS}/shells"
    assert nodes[MODEL] == f"{LAYERS}/domain"


def test_a_declared_architecture_reports_a_layer_violation(declared: Declared) -> None:
    """**The deliverable.** ``domain -> shells``, named as such, from a declared architecture."""
    snapshot = declared.snapshot(LAYERS)

    findings = evaluate_layers(
        snapshot.file_edges,
        None,
        node_of(snapshot).get,
        [domain_rule(f"{LAYERS}/domain", [f"{LAYERS}/engine"])],
    )

    assert [found.details for found in reported(findings, LEAK)] == [
        {
            "rule_name": "domain-may-not-reach-the-shells",
            "from_node": f"{LAYERS}/domain",
            "to_node": f"{LAYERS}/shells",
            "src": STORE,
            "dst": API,
        }
    ]


def test_no_directory_structure_rule_whatsoever_can_report_that_violation(
    declared: Declared,
) -> None:
    """The other half of the deliverable, proved by exhaustion rather than asserted.

    A layer rule can only name a node and the nodes it may depend on, and
    :func:`evaluate_layers` is silent on an edge whose two ends are in the *same* node -- a
    node always depends on itself. So the strictest rule set the directory structure admits
    is "every node may depend on nothing", and even that cannot see this edge. Written this
    way because the weaker form -- one rule, no finding -- would pass just as well if the
    edge had simply gone missing, which the test above rules out independently.
    """
    snapshot = declared.snapshot(DIRECTORY_STRUCTURE)
    every_node = sorted({node.path for node in snapshot.arch_nodes})

    findings = evaluate_layers(
        snapshot.file_edges,
        None,
        node_of(snapshot).get,
        [domain_rule(node, []) for node in every_node],
    )

    assert reported(findings, LEAK) == []
    assert findings, "the strictest folder rules found nothing at all, so nothing was proved"


def test_the_directory_structure_reports_a_violation_that_is_not_one(
    declared: Declared,
) -> None:
    """The mirror image: one layer in two folders reads as a forbidden crossing.

    ``support/model.py`` and ``core/store.py`` are the same layer, so the dependency between
    them is legitimate and the declared architecture is silent about it. The directory
    structure has them in two nodes and reports it -- a false positive an operator could only
    silence by weakening the rule for everything else in those folders.
    """
    folders = declared.snapshot(DIRECTORY_STRUCTURE)
    layers = declared.snapshot(LAYERS)

    by_folder = evaluate_layers(
        folders.file_edges,
        None,
        node_of(folders).get,
        [domain_rule(node_of(folders)[MODEL], [])],
    )
    by_layer = evaluate_layers(
        layers.file_edges,
        None,
        node_of(layers).get,
        [domain_rule(f"{LAYERS}/domain", [f"{LAYERS}/engine"])],
    )

    assert reported(by_folder, WITHIN_DOMAIN)
    assert reported(by_layer, WITHIN_DOMAIN) == []


# --- survival across the analysis paths this gate actually runs ------------------


def test_an_imported_architecture_survives_a_whole_project_analysis(
    declared: Declared,
) -> None:
    """``analyze -all`` is what every cold run and every fallback does."""
    declared.analyze("-all")

    assert LAYERS in _list_arches(declared.cli, declared.db)
    assert node_of(declared.snapshot(LAYERS))[STORE] == f"{LAYERS}/domain"


def test_an_imported_architecture_survives_a_selective_analysis(declared: Declared) -> None:
    """``analyze -files`` is the incremental path a warm commit hook takes.

    Re-measured through the whole stack rather than by listing the architecture: a node that
    survived by name and lost its members would list identically and gate on nothing.
    """
    listing = declared.db.parent / "changed.txt"
    listing.write_text(f"{declared.root / STORE}\n", encoding="utf-8")
    declared.analyze("-files", f"@{listing}")

    snapshot = declared.snapshot(LAYERS)
    assert node_of(snapshot)[STORE] == f"{LAYERS}/domain"
    assert LEAK in edges_of(snapshot)


def test_an_imported_architecture_survives_a_file_leaving_and_coming_back(
    declared: Declared,
) -> None:
    """``und remove -file`` then ``und add`` is what a modified file costs on the warm path."""
    target = declared.root / MODEL
    removed = run_und("-quiet", "-db", str(declared.db), "remove", "-file", str(target))
    assert removed.returncode == 0, removed.stdout + removed.stderr
    added = run_und("-quiet", "-db", str(declared.db), "add", str(target))
    assert added.returncode == 0, added.stdout + added.stderr
    declared.analyze("-all")

    assert node_of(declared.snapshot(LAYERS))[MODEL] == f"{LAYERS}/domain"


def test_an_architecture_imported_before_the_analysis_is_empty_and_stays_empty(
    tmp_path: Path,
) -> None:
    """The measurement that decides where the import goes, and the worst false green here.

    A database that has been created and ``add``-ed but not analysed takes the import with
    status 0 and produces nodes holding **nothing**, and the analysis that follows does not
    fill them in. An architecture in that state lists normally, so only reading the members
    back tells the two apart -- and every layer rule reads an empty node set as "no finding".
    """
    for name, text in SOURCES.items():
        target = tmp_path / "tree" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    db = tmp_path / "early.und"
    created = run_und("-quiet", "create", "-db", str(db), "-languages", "python", "-local")
    assert created.returncode == 0, created.stdout + created.stderr
    added = run_und("-quiet", "-db", str(db), "add", str(tmp_path / "tree"))
    assert added.returncode == 0, added.stdout + added.stderr
    cli = UndCli(real_env("upython"), NullCommandLog())
    early = ArchNode(
        name=LAYERS,
        children=(ArchNode(name="shells", members=(str(tmp_path / "tree" / API),)),),
    )

    assert cli.declare_architecture(db, early) == frozenset()

    assert run_und("-db", str(db), "analyze", "-all", "-errors", "-warnings").returncode == 0
    assert LAYERS in _list_arches(cli, db)
    assert set(cli.export_arch(db, LAYERS, tmp_path / "after.xml").paths()) == set()


# --- the whole gate, over a real repository (req 2.1, 6.3) -----------------------


class MakeRepo(Protocol):
    """The ``git_repo`` fixture from ``tests/conftest.py``, declared rather than imported.

    ``from conftest import …`` is what task 6.1 recorded as breaking under
    ``--import-mode=importlib``, so the fixture arrives by injection and only its shape is
    written down here.
    """

    def __call__(self, name: str = "repo") -> Any: ...


def test_the_gate_declares_the_repositorys_architecture_and_gates_on_it(
    git_repo: MakeRepo, tmp_path: Path
) -> None:
    """**The feature, end to end, with nothing stubbed.**

    A real repository holding ``scitools-hook.arch.xml``, the production
    :class:`~scitools_hook.understand.database.DatabaseManager` synchronising a shadow and
    building a database with the real ``und``, and the layer rule reporting a violation the
    ``Directory Structure`` of that same database cannot see.

    This is the test that covers the rewrite the unit tests can only fake: the declaration
    holds **repository-relative** paths, and measured, ``src/core/store.py`` written literally
    into an architecture document resolves to *nothing* -- import status 0, an empty node, and
    a layer rule with nothing to judge. The manager rewrites every member against the side's
    own shadow tree, and only a real ``und`` can say whether that rewrite is right.
    """
    builder = git_repo("declared")
    for name, text in SOURCES.items():
        builder.write(name, text)
    builder.write(ARCH_FILE, write_architecture(repository_declaration()))
    builder.stage()
    builder.commit("declare the layers")
    manager, paths = gate_over(builder.path, tmp_path / "cache")

    manager.ensure_side("after", IndexTarget())

    snapshot = extract_under(paths.after_db, paths.after_tree, settings_for(LAYERS))
    findings = evaluate_layers(
        snapshot.file_edges,
        None,
        node_of(snapshot).get,
        [domain_rule(f"{LAYERS}/domain", [f"{LAYERS}/engine"])],
    )
    assert [found.message for found in reported(findings, LEAK)] == [
        f"{STORE} now depends on {API}, but layer rule "
        f"'domain-may-not-reach-the-shells' does not allow "
        f"{LAYERS}/domain to depend on {LAYERS}/shells"
    ]


def test_the_gate_survives_the_second_run_over_the_same_cache(
    git_repo: MakeRepo, tmp_path: Path
) -> None:
    """The warm path: the same manager, a second commit, the architecture still gating.

    This is the question that decides whether the feature works in use rather than in a test.
    The gate re-syncs its shadows and analyses incrementally on every run, and a declaration
    that only survived the cold run would pass every test written against a fresh cache and
    stop gating on the second commit of the day.
    """
    builder = git_repo("warm")
    for name, text in SOURCES.items():
        builder.write(name, text)
    builder.write(ARCH_FILE, write_architecture(repository_declaration()))
    builder.stage()
    builder.commit("declare the layers")
    manager, paths = gate_over(builder.path, tmp_path / "cache")
    manager.ensure_side("after", IndexTarget())

    builder.write("src/core/store.py", f"{SOURCES['src/core/store.py']}\n\nEXTRA = 1\n")
    builder.stage()
    manager.ensure_side("after", IndexTarget())

    snapshot = extract_under(paths.after_db, paths.after_tree, settings_for(LAYERS))
    assert node_of(snapshot)[STORE] == f"{LAYERS}/domain"
    assert reported(
        evaluate_layers(
            snapshot.file_edges,
            None,
            node_of(snapshot).get,
            [domain_rule(f"{LAYERS}/domain", [f"{LAYERS}/engine"])],
        ),
        LEAK,
    )


def test_the_gate_exports_a_declaration_the_repository_could_commit(
    git_repo: MakeRepo, tmp_path: Path
) -> None:
    """The other half: nobody can write this XML from nothing, so it has to be exportable.

    The document that comes back names files of the *repository*, not of this machine's
    cache, and is readable by the same reader the import path uses -- which is what makes
    "export the directory structure, edit it into the layers you meant, commit it" a real
    workflow rather than a suggestion.
    """
    builder = git_repo("exporting")
    for name, text in SOURCES.items():
        builder.write(name, text)
    builder.stage()
    builder.commit("sources")
    manager, paths = gate_over(builder.path, tmp_path / "cache")
    manager.ensure_side("after", IndexTarget())

    document = manager.export_architecture("after")

    assert str(paths.root) not in document
    assert set(read_architecture(document, "the export").paths()) == set(FILES)


def repository_declaration() -> ArchNode:
    """``Layers`` with repository-relative members, exactly as a committed file holds them."""
    return ArchNode(
        name=LAYERS,
        children=(
            ArchNode(name="shells", members=(API,)),
            ArchNode(name="engine", members=("src/core/engine.py",)),
            ArchNode(name="domain", members=(STORE, MODEL)),
        ),
    )


def settings_for(architecture: str) -> Settings:
    """The contract settings with one architecture named."""
    base = contract_settings()
    return base.model_copy(
        update={"structure": base.structure.model_copy(update={"architecture": architecture})}
    )


def gate_over(repository: Path, cache: Path) -> tuple[DatabaseManager, CachePaths]:
    """The production manager over a real repository, with the real ``und`` behind it."""
    repo = GitRepo.discover(repository, NullCommandLog())
    settings = settings_for(LAYERS)
    paths = CachePaths.for_repo(repo.common_dir, settings.understand.db_location, cache)
    manager = DatabaseManager(
        paths,
        UndCli(real_env("upython"), NullCommandLog()),
        ShadowSync(repo, paths, settings.project),
        settings,
    )
    return manager, paths


# --- the operator mistakes that must not be tracebacks ---------------------------


def test_a_member_the_project_does_not_hold_is_dropped_without_a_word(
    declared: Declared, tmp_path: Path
) -> None:
    """Why the import is read back at all: ``und`` reports this as a success."""
    ghost = ArchNode(
        name="Ghosts",
        children=(ArchNode(name="nowhere", members=declared.member(API, "src/core/absent.py")),),
    )

    resolved = declared.cli.declare_architecture(declared.db, ghost)

    assert resolved == set(declared.member(API))


def test_an_architecture_of_nothing_but_ghosts_still_imports(
    declared: Declared,
) -> None:
    """Status 0, ``Architecture imported.``, and not one member -- measured, not feared."""
    ghost = ArchNode(
        name="AllGhosts",
        children=(ArchNode(name="nowhere", members=declared.member("src/core/absent.py")),),
    )

    assert declared.cli.declare_architecture(declared.db, ghost) == frozenset()
    assert "AllGhosts" in _list_arches(declared.cli, declared.db)


def test_a_malformed_document_is_refused_and_changes_nothing(
    declared: Declared, tmp_path: Path
) -> None:
    """``und import -arch`` exits 1 on malformed XML; the wrapper turns that into an error."""
    broken = tmp_path / "broken.xml"
    broken.write_text('<arch name="Broken"><arch name="x">\n', encoding="utf-8")
    before = set(_list_arches(declared.cli, declared.db))

    with pytest.raises(AnalysisFailedError):
        _import_arch(declared.cli, declared.db, broken)

    assert set(_list_arches(declared.cli, declared.db)) == before


def test_importing_under_the_built_in_name_merges_instead_of_replacing(
    declared: Declared, tmp_path: Path
) -> None:
    """Why a declaration may not be called ``Directory Structure``, measured rather than feared.

    Every other name is refused as a *duplicate* at status 1, which is what makes
    ``declare_architecture`` remove before it imports. This one is not refused at all: the
    import succeeds and the declared node is **added to** the folder-derived architecture,
    which then holds both -- so ``structure.architecture`` can no longer select between the
    layout and the declaration, because they are one architecture.

    ``und remove -arch "Directory Structure"`` does undo it -- it resets the architecture to
    the folders rather than deleting it -- so the damage is not permanent. That is measured
    here too, because the first version of this test asserted the opposite from an
    unmeasured inference and this is the assertion that caught it.
    """
    folders = declared.cli.export_arch(declared.db, DIRECTORY_STRUCTURE, tmp_path / "a.xml")
    before = set(folders.paths())
    intruder = ArchNode(
        name=DIRECTORY_STRUCTURE,
        children=(ArchNode(name="intruder", members=declared.member(API)),),
    )
    document = tmp_path / "intruder.xml"
    document.write_text(write_architecture(intruder), encoding="utf-8")

    _import_arch(declared.cli, declared.db, document)

    merged = declared.cli.export_arch(declared.db, DIRECTORY_STRUCTURE, tmp_path / "b.xml")
    assert "intruder" in {child.name for child in merged.children}
    assert set(merged.paths()) == before
    _remove_arch(declared.cli, declared.db, DIRECTORY_STRUCTURE)
    reset = declared.cli.export_arch(declared.db, DIRECTORY_STRUCTURE, tmp_path / "c.xml")
    assert "intruder" not in {child.name for child in reset.children}
    assert {child.name for child in reset.children} == {child.name for child in folders.children}


def test_an_architecture_the_database_does_not_hold_cannot_be_exported(
    declared: Declared, tmp_path: Path
) -> None:
    with pytest.raises(AnalysisFailedError):
        declared.cli.export_arch(declared.db, "NoSuchArchitecture", tmp_path / "out.xml")


def test_the_command_line_is_the_one_the_wrapper_documents(declared: Declared) -> None:
    """``und`` refuses a global switch after the subcommand, so the shape is not incidental."""
    refused = subprocess.run(
        [str(und()), "list", "arches", "-db", str(declared.db), "-quiet"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )

    assert refused.returncode != 0 or "Architectures" not in refused.stdout
