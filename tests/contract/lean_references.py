"""The three measured databases the lean-reference contract tests share (task 6.1).

Not a test module: it holds the probe and the fixtures that ``test_lean_references_contract``
and ``test_lean_reference_kinds_contract`` both read, so that the two modules ask one
question each and the databases are described once. It is the same arrangement as
``contract_project``, and the split follows the dependency rule the way task 4.2 of
understand-8-features did: one module over the budget became two by subject, with what they
share in a module of its own.

Every fact the five reference rules read comes from a kind string in ``worker_lean``, and a
kind string is a claim about what Understand records: it matches or it matches nothing, and
matching nothing raises no error. Nothing in the unit tests can say which claim is true on a
real database, so these fixtures ask the real one, on three databases:

**The contract project** (``contract_project.py``), extracted with every reference rule on
and probed directly, which is where the rules are measured against their planted cases.

**A scratch project with defaulted parameters**, for the question task 3.1's review left
open: whether Understand records a ``Set Init`` against a defaulted parameter's own
declaration. If it did, ``setby`` in ``PARAMETER_USE`` would make every defaulted parameter
read as used and requirement 1.2 would under-report in silence.

**A scratch project in the six languages the contract project does not build**, for the
``derive`` direction the design could only read off the documentation.

The probe runs under ``upython`` against each database directly rather than through the
worker, because the question is what Understand *records*, and the worker's kind filters are
what is being checked against it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
from contract_project import (
    FILES,
    TIMEOUT_S,
    SampleProject,
    build_database,
    contract_settings,
    extract_with,
    real_env,
    write_tree,
)

from scitools_hook.analysis.lean.layering import STATEMENT_METRIC
from scitools_hook.config.models import REFERENCE_RULES, LeanRules, Limit, Settings, ThresholdSpec
from scitools_hook.models.snapshot import EntityKey, LeanFacts, ProjectSnapshot
from scitools_hook.understand import worker_lean

# --- what the contract project plants, by the names the snapshot records ---------------

BASES: Final = {"Python": "layers.BaseChannel", "C++": "BaseNativeChannel"}
DERIVED: Final = {"Python": "layers.OnlyChannel", "C++": "OnlyNativeChannel"}
OVERRIDES: Final = {"Python": "layers.OnlyChannel.send", "C++": "OnlyNativeChannel::send"}
BASE_METHODS: Final = {"Python": "layers.BaseChannel.send", "C++": "BaseNativeChannel::send"}
"""The single-implementation pair and the override in each language of the fixture."""

# --- the defaulted-parameter question (task 3.1's review) ------------------------------

DEFAULTED_SOURCES: Final[dict[str, str]] = {
    # Python 2 grammar, as the contract project is: no annotation, no f-string.
    "defaults.py": '''"""Three shapes of a parameter, to ask what its own declaration records."""


def defaulted(value, verbose=False):
    """``verbose`` has a default and is never read: the shape the question is about."""
    stepped = value + 1
    return stepped


def defaulted_read(value, verbose=False):
    """The control: a defaulted parameter the body reads."""
    if verbose:
        return value
    return value + 1


def reassigned(value, verbose):
    """The reason ``setby`` is in the set: the body writes it and never reads it."""
    verbose = True
    return value
''',
    "native/defaults.cpp": """// The same three shapes in C++.
int defaulted(int value, bool verbose = false) {
    int stepped = value + 1;
    return stepped;
}

int defaulted_read(int value, bool verbose = false) {
    if (verbose) {
        return value;
    }
    return value + 1;
}

int reassigned(int value, bool verbose) {
    verbose = true;
    return value;
}
""",
}
DEFAULTED_FILES: Final = tuple(sorted(DEFAULTED_SOURCES))

NEVER_READ: Final = "defaulted"
READ: Final = "defaulted_read"
REASSIGNED: Final = "reassigned"
PARAMETER: Final = "verbose"

# --- the languages the contract project does not build ---------------------------------

LANGUAGE_SOURCES: Final[dict[str, str]] = {
    "Shapes.java": """class JavaBase {
    int send(int message) { return message; }
}
class JavaOnly extends JavaBase {
    int send(int message) { return message + 1; }
}
interface JavaShape { int area(); }
class JavaSquare implements JavaShape {
    public int area() { return 4; }
}
""",
    "Shapes.cs": """class SharpBase {
    public virtual int Send(int message) { return message; }
}
class SharpOnly : SharpBase {
    public override int Send(int message) { return message + 1; }
}
interface ISharpShape { int Area(); }
class SharpSquare : ISharpShape {
    public int Area() { return 4; }
}
""",
    "shapes.ads": """package Shapes is
   type Ada_Base is tagged null record;
   function Send (Self : Ada_Base; Message : Integer) return Integer;
   type Ada_Only is new Ada_Base with null record;
   overriding function Send (Self : Ada_Only; Message : Integer) return Integer;
end Shapes;
""",
    "shapes.adb": """package body Shapes is
   function Send (Self : Ada_Base; Message : Integer) return Integer is
   begin
      return Message;
   end Send;
   function Send (Self : Ada_Only; Message : Integer) return Integer is
   begin
      return Message + 1;
   end Send;
end Shapes;
""",
    "shapes.pas": """unit shapes;
interface
type
  TPasBase = class
    function Send(Message: Integer): Integer; virtual;
  end;
  TPasOnly = class(TPasBase)
    function Send(Message: Integer): Integer; override;
  end;
implementation
function TPasBase.Send(Message: Integer): Integer;
begin
  Result := Message;
end;
function TPasOnly.Send(Message: Integer): Integer;
begin
  Result := Message + 1;
end;
end.
""",
    "shapes.ts": """interface TsShape { area(): number; }
class TsSquare implements TsShape {
    area(): number { return 4; }
}
class TsBase { send(message: number): number { return message; } }
class TsOnly extends TsBase { send(message: number): number { return message + 1; } }
""",
    "shapes.f90": """module shapes
  implicit none
  type :: fortran_base
    integer :: side
  end type fortran_base
  type, extends(fortran_base) :: fortran_only
    integer :: extra
  end type fortran_only
end module shapes
""",
}
"""One base with one derived type per language, and an interface where the language has one.

Ada and Pascal are the two the design flagged; Java is the one whose inverse spelling the
worker's docstring calls unverified; C#, Fortran and TypeScript are the remaining languages
the kind documentation names for the four inheritance members.
"""

LANGUAGES: Final = ("ada", "c#", "fortran", "java", "pascal", "web")
"""What ``und create -languages`` is given for the scratch project above."""

EXTENDS: Final[dict[str, tuple[str, str]]] = {
    "Ada": ("Ada_Base", "Ada_Only"),
    "C#": ("SharpBase", "SharpOnly"),
    "Fortran": ("fortran_base", "fortran_only"),
    "Java": ("JavaBase", "JavaOnly"),
    "Pascal": ("TPasBase", "TPasOnly"),
    "Web": ("TsBase", "TsOnly"),
}
IMPLEMENTS: Final[dict[str, tuple[str, str]]] = {
    "C#": ("ISharpShape", "SharpSquare"),
    "Java": ("JavaShape", "JavaSquare"),
    "Web": ("TsShape", "TsSquare"),
}
"""``(base, derived)`` by entity name, per language, as the sources above plant them."""

OVERRIDDEN_METHOD: Final = "send"
"""The method every derived class overrides, compared case-insensitively for Ada and C#."""

# --- the probe ---------------------------------------------------------------------------

PROBE: Final = """
import json
import sys

import understand

db_path, root, routine_kinds, callby = sys.argv[1:5]
db = understand.open(db_path)


def project_file(ent):
    "The file an entity is written in when that file is under the root, else None."
    ref = ent.ref("definein, declarein")
    if ref is None:
        return None
    name = ref.file().longname()
    return name if name.startswith(root) else None


rows = []
for ent in db.ents("~unknown ~unresolved"):
    path = project_file(ent)
    if path is None:
        continue
    row = {
        "language": str(ent.language()),
        "kind": str(ent.kind().longname()),
        "name": str(ent.name()),
        "longname": str(ent.longname()),
        "path": path[len(root):].lstrip("/"),
        "refs": [
            [str(r.kind().longname()), str(r.ent().name()), str(r.ent().longname()),
             str(r.ent().kind().longname()), r.file().name(), r.line()]
            for r in ent.refs()
        ],
    }
    if ent.kind().check(routine_kinds):
        metrics = ent.metric(["CountCallby", "CountCallbyUnique"])
        row["callby"] = metrics["CountCallby"]
        row["callby_unique"] = metrics["CountCallbyUnique"]
        row["callby_kinds"] = sorted(str(r.kind().longname()) for r in ent.refs(callby))
    rows.append(row)
print(json.dumps(rows))
"""
"""Every entity written in a project file with every reference it carries, as JSON.

Run under ``upython`` against the database directly rather than through the worker, because
the question is what Understand *records*, and the worker's kind filters are what is being
checked against it. The plugin caller metrics are read exactly as ``worker._metric_values``
reads one -- ``ent.metric([name])`` -- so the comparison is against the number a threshold on
``CountCallbyUnique`` would see.
"""


@dataclass(frozen=True)
class Entity:
    """One row of :data:`PROBE`'s answer."""

    language: str
    kind: str
    name: str
    longname: str
    path: str
    refs: tuple[tuple[str, str, str, str, str, int], ...]
    callby: int | None = None
    callby_unique: int | None = None
    callby_kinds: tuple[str, ...] = ()

    def kinds(self, kinds: str | None = None) -> list[str]:
        """The reference kinds this entity carries, all of them or only those matching."""
        if kinds is None:
            return [ref[0] for ref in self.refs]
        wanted = {word.strip().lower() for word in kinds.split(",")}
        return [ref[0] for ref in self.refs if _matches(ref[0], wanted)]

    def targets(self, kinds: str) -> list[str]:
        """The names the references of ``kinds`` point at, in the order recorded."""
        wanted = {word.strip().lower() for word in kinds.split(",")}
        return [ref[1] for ref in self.refs if _matches(ref[0], wanted)]


def _matches(kind: str, wanted: set[str]) -> bool:
    """Whether a kind longname carries one of the words a ``refs`` filter would match.

    A filter word matches a whole word of the longname, case-insensitively: measured on this
    build, ``derive`` matches ``Ada Derive`` and ``c# csharp Derive`` and does **not** match
    ``Ada Derivefrom``, and ``extendby`` matches ``Java Extendby Coupleby``. The probe above
    also asks the API directly for the caller kinds, which is the check on this reading.
    """
    return any(word.lower() in wanted for word in kind.split())


@dataclass(frozen=True)
class Probed:
    """One database as :data:`PROBE` saw it, with the entities looked up by name."""

    entities: tuple[Entity, ...]

    def named(self, language: str, name: str) -> Entity:
        """The one entity of that language and short name; the sources plant each once."""
        found = [e for e in self.entities if e.language == language and e.name == name]
        assert len(found) == 1, f"{language} {name}: {[e.longname for e in found]}"
        return found[0]

    def by_longname(self, language: str, longname: str) -> list[Entity]:
        """Every entity of that language and qualified name -- overloads share one."""
        return [e for e in self.entities if e.language == language and e.longname == longname]

    def routines(self) -> list[Entity]:
        """Every entity the probe read the caller metrics for."""
        return [e for e in self.entities if e.callby is not None]


def probe(db: Path, root: Path, tmp_path_factory: pytest.TempPathFactory) -> Probed:
    """Run :data:`PROBE` over one database and read its answer back."""
    script = tmp_path_factory.mktemp("lean-probe") / "probe.py"
    script.write_text(PROBE, encoding="utf-8")
    upython = real_env("upython").upython
    assert upython is not None, "this build ships no upython, so the kinds cannot be read"
    done = subprocess.run(
        [
            str(upython),
            str(script),
            str(db),
            str(root),
            worker_lean.ROUTINE_KINDS,
            worker_lean.CALLER_KINDS,
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    rows = json.loads(done.stdout.strip().splitlines()[-1])
    return Probed(
        tuple(
            Entity(
                language=row["language"],
                kind=row["kind"],
                name=row["name"],
                longname=row["longname"],
                path=row["path"],
                refs=tuple(tuple(ref) for ref in row["refs"]),
                callby=row.get("callby"),
                callby_unique=row.get("callby_unique"),
                callby_kinds=tuple(row.get("callby_kinds", ())),
            )
            for row in rows
        )
    )


# --- the three databases, measured -------------------------------------------------------


def asking_every_reference_rule() -> Settings:
    """The contract settings with the five reference rules on, and nothing else of the family.

    Nothing else on purpose: the definitions walk the variable rule reads has to be switched
    on by one of these five, or a configuration enabling only ``unused_variables`` reports
    nothing for ever and never says why -- which is what this configuration measured before
    ``Settings.wants_definitions`` learned the third rule.

    One threshold is added: ``routine.CountStmt``, which the shipped defaults carry at 40 and
    the fixed contract list does not. The pass-through rule reads the statement count off the
    record and asks for nothing itself; a routine without it is a record the rule does not
    judge, by task 4.2's decision, so without the threshold the rule reports nothing here and
    raises no note. A real run has it because ``default_settings()`` does.
    """
    settings = contract_settings()
    settings.thresholds.append(
        ThresholdSpec(scope="routine", metric=STATEMENT_METRIC, limit=Limit(max=40))
    )
    for rule in REFERENCE_RULES:
        setattr(settings.lean, rule, "warning")
    return settings


@dataclass(frozen=True)
class Measured:
    """The contract project's snapshot with every reference fact, probed directly.

    ``settings`` is the configuration the snapshot was extracted under, so a rule test asks
    its rule with the ignore lists and budgets of the same configuration -- which are the
    shipped ``LeanRules`` defaults, since :func:`asking_every_reference_rule` touches only the
    five severities.
    """

    snapshot: ProjectSnapshot
    probed: Probed
    settings: Settings

    @property
    def rules(self) -> LeanRules:
        """The ``[lean]`` section the snapshot was extracted under."""
        return self.settings.lean

    def facts(self, longname: str) -> LeanFacts:
        """The lean facts of the one recorded entity with that qualified name."""
        found = [
            record.lean
            for key, record in self.snapshot.entities.items()
            if key.longname == longname and record.lean is not None
        ]
        assert len(found) == 1, f"{longname}: {len(found)} records"
        return found[0]

    @property
    def keys(self) -> set[EntityKey]:
        """Every recorded entity, which is what "affected" means for a whole-project run."""
        return set(self.snapshot.entities)


@dataclass(frozen=True)
class Defaulted:
    """The defaulted-parameter tree, read through the worker and probed directly."""

    snapshot: ProjectSnapshot
    probed: Probed

    def parameter(self, language: str, routine: str) -> Entity:
        """The ``verbose`` parameter of one routine of the tree."""
        found = [
            e
            for e in self.probed.entities
            if e.language == language
            and e.name == PARAMETER
            and any(ref[0].endswith("Definein") and ref[1] == routine for ref in e.refs)
        ]
        assert len(found) == 1, [(e.longname, e.refs) for e in found]
        return found[0]

    def unused_of(self, routine: str) -> dict[str, list[str]]:
        """``unused_parameters`` of each recorded routine with that short name, by language."""
        return {
            record.language: list(record.lean.unused_parameters or [])
            for key, record in self.snapshot.entities.items()
            if key.scope == "routine" and key.longname.split(".")[-1] == routine and record.lean
        }


@pytest.fixture(scope="module")
def measured(
    sample_project: SampleProject,
    tmp_path_factory: pytest.TempPathFactory,
) -> Measured:
    """The alpha side, extracted with every reference rule on and probed directly.

    A module that uses this fixture imports ``sample_project`` from ``contract_project``
    itself, because pytest resolves a fixture's arguments in the namespace of the module the
    test is in, not in the namespace this function was written in.
    """
    db, root = sample_project.db("alpha"), sample_project.root("alpha")
    settings = asking_every_reference_rule()
    snapshot = extract_with(db, root, FILES, settings)
    return Measured(snapshot, probe(db, root, tmp_path_factory), settings)


@pytest.fixture(scope="module")
def defaulted(tmp_path_factory: pytest.TempPathFactory) -> Defaulted:
    """The defaulted-parameter tree, read through the worker and probed directly."""
    workdir = tmp_path_factory.mktemp("defaulted")
    root = write_tree(workdir / "tree", DEFAULTED_SOURCES)
    db = workdir / "defaulted.und"
    build_database(db, root)
    snapshot = extract_with(db, root, DEFAULTED_FILES, asking_every_reference_rule())
    return Defaulted(snapshot, probe(db, root, tmp_path_factory))


@pytest.fixture(scope="module")
def languages(tmp_path_factory: pytest.TempPathFactory) -> Probed:
    """The six-language tree, probed directly: the worker records none of these languages."""
    workdir = tmp_path_factory.mktemp("languages")
    root = write_tree(workdir / "tree", LANGUAGE_SOURCES)
    db = workdir / "languages.und"
    build_database(db, root, LANGUAGES)
    return probe(db, root, tmp_path_factory)
