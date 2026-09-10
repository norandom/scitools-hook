"""The sample repository task 10.1 measures the installed Understand against.

Not a test module: it holds the fixture the contract tests share, so every test reads the same
measured project. The fixture is session-scoped, but importing it into several test modules
registers it once per module, so the databases are built once per module that asks for them --
about 1.3 s a pair, which is why this is left simple rather than cached across modules.

**Why the project is written here rather than reused from ``tests/fixtures/sample_project``.**
That fixture is a before/after pair whose two sides differ on purpose, and several other
tasks' contract tests assert against its exact contents. Requirement 4.4 needs the opposite:
two databases built from *different directories* over **token-identical** sources, so that any
difference between the two sides is a difference Understand introduced -- a root leaking into
an entity's identity -- and never a difference in the code. It also needs constructs that
fixture deliberately does not have: C++ overloads, a file sitting directly in the analysis
root, and a directory that holds both files and a subdirectory.

Everything here is built with plain ``und`` subprocess calls. A contract test that built its
database through :class:`~scitools_hook.understand.database.DatabaseManager` would be testing
the manager; the databases are the *given*, and the adapters are what is under test.

**The lean-code cases.** ``lean/`` and ``native/lean_*`` hold one instance each of every
shape the lean-code rules report -- a dead parameter, class and module variable, a
pass-through routine, an abstraction with one implementation, an over-exporting file, a
copied block and a renamed twin -- and one control beside each, so that a rule which reported
every routine, every class or every module variable fails here instead of passing. There is
deliberately exactly *one* instance of each **per language**, because a fixture holding two
of a shape can only be asserted by counting. Adding a routine, a class or an import to any of
these files can create a second instance somewhere else: ``app/entry.py`` carries a
module-level constant, and both it and ``core.Engine.run`` carry a statement of their own,
for no other reason.

Two of the shapes were already present in the C++ sources by accident before the C++ cases
were planted, and one file takes them back out. Measured on the installed build, ``Shape``
had no use, call or typed reference anywhere in the project -- its only inbound reference was
the ``Nameby`` from the file defining its methods -- which is the unused-class predicate, and
``native/shape.h`` had ``CountDeclClass`` 1, ``CountDeclFunction`` 0 and a single inbound
edge, which is the over-export predicate. The two ``scale`` overloads the header also declares
do not take it back out: ``CountDeclFunction`` counts *definitions*, and both are defined in
``native/shape.cpp``. Nor does the include guard, the only other module-level entity there,
because it is a Macro and the definitions walk records no Macro. ``native/measure.cpp``
constructs a ``Shape`` and includes the header, so the class is used and the header has two
inbound edges, and ``native/shape.h`` is left to answer only the overload contract it was
written for.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import understand_probe

from scitools_hook.config.models import Limit, Settings, StructureRules, ThresholdSpec
from scitools_hook.models.progress import NullCommandLog
from scitools_hook.models.snapshot import ProjectSnapshot, Side
from scitools_hook.models.understand import UnderstandEnv
from scitools_hook.understand.api_runner import ApiRunner
from scitools_hook.understand.snapshot import SnapshotExtractor, SnapshotTarget

TIMEOUT_S = 600
"""Generous ceiling: a contract run must fail loudly rather than hang the suite."""

LANGUAGES = ("python", "c++")
"""The two languages the sample repository is written in."""

ROOTS = ("alpha", "beta")
"""The two directory names the identical sources are analysed from (req 4.4)."""

SOURCES: dict[str, str] = {
    # A module sitting directly in the analysis root. Every real repository has one
    # (`setup.py`, `conftest.py`, `manage.py`) and it is the case that made `relname()`
    # return a path prefixed with the root's own name.
    "main.py": '''"""Entry module sitting directly in the analysis root."""

from app.entry import entry_point


def main():
    return entry_point()
''',
    # A sibling of `pkg/`, so that one architecture node really depends on another.
    # Two lines here are not decoration, and both keep a lean-code case unique. `START_VALUE`
    # is a module-level definition, without which this file defines one routine that exactly
    # one file imports -- the over-export rule's whole predicate (lean-code 4.1) -- and
    # `lean/exported.py` stops being the only Python instance of that case. The `started`
    # binding is a statement of `entry_point`'s own: without it the routine has one caller
    # (`main.main`), one callee that is a project routine (`core.Engine.run`; the sibling call
    # target `core.Engine` is a Class and is filtered out) and `CountStmt` 2, which is the
    # pass-through predicate (2.1) exactly, and `lean/layers.py`'s `display_name` stops being
    # the only instance. Neither line changes a reference count: `main.py -> app/entry.py`
    # stays at 3 and `app/entry.py -> pkg/core.py` at 4, because the call chain is untouched.
    "app/entry.py": '''"""A directory beside the package, so a sibling architecture edge exists."""

from pkg.core import Engine

START_VALUE = 1


def entry_point():
    started = START_VALUE
    return Engine().run(started)
''',
    # A directory that holds a file *and* a subdirectory: `pkg/core.py` beside `pkg/inner/`.
    # This is what decides which node holds `core.py` at depth 2.
    # `run` binds `widened` for the same reason `entry_point` binds `started`. With a single
    # forwarding expression it has one caller, `CountStmt` 2 and one call reference into a
    # project file -- `widen`, which Understand records as `python Unknown Ambiguous
    # Attribute` in `pkg/inner/leaf.py`. A pass-through rule that counts every call reference
    # rather than only the ones whose target is a routine would report it, so the fixture
    # keeps `display_name` the only instance under *both* readings rather than relying on the
    # implementation choosing the stricter one.
    "pkg/core.py": '''"""A class with a method, a classmethod and a staticmethod."""

from pkg.inner.leaf import Leaf


class Engine:
    def __init__(self):
        self.leaf = Leaf()

    def run(self, value):
        widened = self.leaf.widen(value)
        return widened

    @classmethod
    def build(cls):
        return cls()

    @staticmethod
    def label():
        return "engine"
''',
    "pkg/inner/leaf.py": '''"""The deepest package, two directory levels below the analysis root."""


class Leaf:
    def widen(self, value):
        return value + 1
''',
    # --- the lean-code cases (lean-code-rules 5.7, 9.1) ---------------------------------
    #
    # `lean/` holds one clean instance of each shape the lean-code rules report, because a
    # rule measured against a fixture that holds two of a shape can only be asserted by
    # counting, and a rule measured against none can be asserted at all. Every routine here
    # is either the case itself or the control beside it -- the routine with one caller and a
    # body of its own, the used module variable, the class the one implementation is derived
    # from -- so a rule that reported everything of a kind would fail rather than pass.
    #
    # "One instance" means one per language: `lean/` holds the Python instances and
    # `native/lean_*` the C++ ones, and each is unique within its own sources. Nothing here
    # relies on the C++ side holding none of a shape.
    #
    # Written to parse as Python 2: the installed Understand resolves no bare `python` on
    # this machine and falls back to its Python 2 grammar, so no f-string and no annotation
    # may appear in any of these files.
    #
    # The over-exporting file: one routine, no other definition, exactly one importer (4.1).
    "lean/exported.py": '''"""One routine for one importer: the over-exporting file (lean-code 4.1).

Nothing else is defined here and ``lean/dead.py`` is the only file that imports it, which is
the whole of the rule's predicate. A second definition, or a second importer, and the Python
sources lose their only instance of this case. ``native/lean_exported.h`` is the C++ one.
"""


def only_export(value):
    return value * 3
''',
    # The three dead-code shapes requirement 1 adds to the routine rule, with their controls.
    "lean/dead.py": '''"""A module variable, a class and a parameter nothing uses (lean-code 1).

``RETRY_LIMIT`` and ``ForgottenReport`` are what nothing in the project names. ``DEFAULT_STEP``
and ``advance`` are the controls beside them, because a rule that reported every module
variable and every class would pass a fixture that held only the dead ones. ``advance``
carries the unused parameter, and its body is two statements of its own so that the
pass-through rule has to leave it alone (2.2).

``ForgottenReport`` is the only unused class in the Python sources;
``ForgottenNativeReport`` in ``native/lean_dead.cpp`` is the C++ one. ``Shape`` in
``native/shape.h`` used to be a second instance and is not one now, because
``native/measure.cpp`` constructs it.
"""

from lean.exported import only_export

RETRY_LIMIT = 3

DEFAULT_STEP = 2


class ForgottenReport(object):
    """A class nothing in the project references: the unused-class case (1.1)."""


def advance(value, verbose):
    """``verbose`` is never read: the unused-parameter case (1.2)."""
    stepped = value + DEFAULT_STEP
    return only_export(stepped)
''',
    # The pass-through routine (2.1) and the abstraction with one implementation (3.1).
    "lean/layers.py": '''"""A routine that forwards, and a base class with one implementation.

``display_name`` is the pass-through: one project caller, one project callee, no body of its
own. ``canonical_name`` beside it has one caller too and a body, which is the decomposition
requirement 2.2 says must never be reported. ``BaseChannel`` is named by nothing in the
project except the class derived from it, and ``OnlyChannel`` is used by ``open_channel``, so
that the Python sources' one unused class is in ``lean/dead.py`` and not here.
"""


class BaseChannel(object):
    """One derived class and no other user: the single-implementation case (3.1)."""

    def send(self, message):
        return message


class OnlyChannel(BaseChannel):
    """The one implementation, and the override the parameter and layering rules exempt."""

    def send(self, message):
        labelled = display_name(message)
        return labelled + "!"


def canonical_name(raw):
    """One caller and a body of its own, so the pass-through rule must stay silent (2.2)."""
    trimmed = raw.strip()
    return trimmed.lower()


def display_name(raw):
    """The pass-through: forwards to ``canonical_name`` and does nothing else (2.1)."""
    return canonical_name(raw)


def open_channel(message):
    """Gives ``OnlyChannel`` the project reference that keeps it out of the dead-code rule."""
    channel = OnlyChannel()
    return channel.send(message)
''',
    # The renamed twin (5.2), one half in each file. Nothing imports either: a single-routine
    # file with one importer would be an over-export finding as well.
    "lean/twin_left.py": '''"""One half of the renamed twin pair (lean-code 5.2).

Every difference between this routine and ``lean/twin_right.py``'s is an identifier or a
literal, which is exactly what requirement 5.4 says similarity normalises away, and both are
well past the six-statement floor the rule ships with.
"""


def summarise_orders(orders):
    total = 0
    count = 0
    for order in orders:
        total = total + order["total"]
        count = count + 1
    if count == 0:
        return 0
    return total / count
''',
    "lean/twin_right.py": '''"""The other half of the renamed twin pair (lean-code 5.2).

The same routine as ``lean/twin_left.py``'s under other names. It is deliberately not a
copied *block*: no twelve consecutive lines are identical between the two files, so the
duplicate-block rule has nothing to say about this pair and the similarity rule has.
"""


def summarise_invoices(invoices):
    amount = 0
    seen = 0
    for invoice in invoices:
        amount = amount + invoice["amount"]
        seen = seen + 1
    if seen == 0:
        return 0
    return amount / seen
''',
    # The copied block (5.1): the fourteen lines from `return {` to `}` are identical in both
    # files, past the twelve-line minimum the rule ships with.
    "lean/table_left.py": '''"""One half of the copied block (lean-code 5.1).

The copy is a literal rather than a run of statements on purpose. It has to survive the
duplication rule's twelve-line minimum once whitespace and comments are dropped, and it has
to stay *below* the similar-routine rule's six-statement floor, so that the twin pair in
``lean/twin_left.py`` and ``lean/twin_right.py`` stays the fixture's only similarity finding.
"""


def order_columns():
    return {
        "identifier": "order_id",
        "customer": "customer_id",
        "created": "created_at",
        "updated": "updated_at",
        "status": "status",
        "currency": "currency",
        "subtotal": "subtotal",
        "discount": "discount",
        "shipping": "shipping",
        "tax": "tax",
        "total": "total",
        "notes": "notes",
    }
''',
    "lean/table_right.py": '''"""The other half of the copied block (lean-code 5.1).

The routine is named differently and the module says something else, so the duplicate run is
the literal itself: fourteen consecutive lines this file shares with ``lean/table_left.py``
and with nothing else in the project.
"""


def invoice_columns():
    return {
        "identifier": "order_id",
        "customer": "customer_id",
        "created": "created_at",
        "updated": "updated_at",
        "status": "status",
        "currency": "currency",
        "subtotal": "subtotal",
        "discount": "discount",
        "shipping": "shipping",
        "tax": "tax",
        "total": "total",
        "notes": "notes",
    }
''',
    # Two overload pairs: one member function and one free function. `EntityKey` must tell
    # each pair apart, and the header/source split must not produce two entities per routine.
    "native/shape.h": """#ifndef SAMPLE_SHAPE_H
#define SAMPLE_SHAPE_H

class Shape {
public:
    explicit Shape(int side);
    int area(int width) const;
    int area(int width, int height) const;
    int side() const;

private:
    int side_;
};

int scale(int value);
int scale(int value, int factor);

#endif
""",
    "native/shape.cpp": """#include "shape.h"

Shape::Shape(int side) : side_(side) {}

int Shape::area(int width) const { return width * side_; }

int Shape::area(int width, int height) const { return width * height; }

int Shape::side() const { return side_; }

int scale(int value) { return value * 2; }

int scale(int value, int factor) { return value * factor; }
""",
    # --- the C++ half of the lean-code cases (lean-code-rules 5.7, 9.1) ------------------
    #
    # The same eight shapes again, one instance of each in the C++ sources, because a rule
    # measured on Python alone has not been measured on a language with headers, free
    # functions and overloads. They sit flat in `native/` rather than in a `native/lean/`
    # subdirectory on purpose: a subdirectory would give `native/` a child, and at depth 2
    # that moves `native/shape.cpp` and `native/shape.h` out of their own architecture node --
    # a change to what the structure contract measures and nothing to do with lean code.
    #
    # Written to compile as C++98: `override`, `nullptr` and `auto` are not used, so nothing
    # here depends on which standard the installed clang defaults to.
    #
    # `analyze` reports one error per C++ translation unit on this machine -- clang's note
    # that a future release would prefer a different libstdc++ include directory -- so the
    # banner reads `Errors:8 Warnings:0` here where it read `Errors:1` with one source file.
    # It is the same note eight times and not eight problems; nothing asserts the count.
    #
    # Two shapes are planted rather than adopted. `Shape` and `native/shape.h` satisfied the
    # unused-class and over-export predicates exactly, measured, before these files existed,
    # and `native/measure.cpp` is what takes them back out again. Adopting them instead would
    # have made one header answer both the overload contract and two lean-code cases, so an
    # edit made for one would silently break the other, and neither would have a control
    # beside it.
    #
    # The C++ over-exporting file: one definition, no other recorded one, one includer (4.1).
    "native/lean_exported.h": """#ifndef SAMPLE_LEAN_EXPORTED_H
#define SAMPLE_LEAN_EXPORTED_H

// One definition for one includer: the C++ over-exporting file (lean-code 4.1).
//
// `native/lean_dead.cpp` is the only file that includes this header, and the only other
// entity it declares is the include guard, which is a Macro and which the definitions walk
// does not record. A second definition here, or a second includer, and the C++ sources lose
// their only instance of this case.
inline int only_native_export(int value) { return value * 3; }

#endif
""",
    # The three dead-code shapes requirement 1 adds to the routine rule, with their controls.
    "native/lean_dead.cpp": """// A constant, a class and a parameter nothing uses (lean-code 1).
//
// `kRetryLimit` and `ForgottenNativeReport` are what nothing in the project names.
// `kDefaultStep` and `native_advance` are the controls beside them, because a rule that
// reported every file-scope constant and every class would pass a fixture that held only the
// dead ones. `native_advance` carries the unused parameter.
//
// `ForgottenNativeReport` is the only unused class in the C++ sources. `Shape` in
// `native/shape.h` was the other one until `native/measure.cpp` gave it a user.
#include "lean_exported.h"

static const int kRetryLimit = 3;

static const int kDefaultStep = 2;

// A class nothing in the project references: the C++ unused-class case (1.1).
class ForgottenNativeReport {
public:
    int total;
};

// `verbose` is never read: the C++ unused-parameter case (1.2).
//
// The body is three statements of its own, as the Python `advance` next door is, so that the
// pass-through rule has to leave this routine alone (2.2) for a reason of its own rather than
// only because nothing calls it.
int native_advance(int value, bool verbose) {
    int stepped = value + kDefaultStep;
    int scaled = only_native_export(stepped);
    return scaled;
}
""",
    # The pass-through routine (2.1) and the abstraction with one implementation (3.1).
    "native/lean_layers.cpp": """// A routine that forwards, and a base with one implementation.
//
// `display_native_name` is the pass-through: one project caller, one project callee, no body
// of its own. `canonical_native_name` beside it has one caller too and a body, which is the
// decomposition requirement 2.2 says must never be reported. `BaseNativeChannel` is named by
// nothing in the project except the class derived from it, and `OnlyNativeChannel` is used by
// `open_native_channel`, so that the C++ sources' one unused class is in
// `native/lean_dead.cpp` and not here.

// One caller and a body of its own, so the pass-through rule must stay silent (2.2).
//
// Three statements, as the Python `canonical_name` next door has: at two it sat exactly on
// the default budget and was excluded only by having no project callee, which is one reason
// where the Python control has two.
int canonical_native_name(int raw) {
    int trimmed = raw - 1;
    int scaled = trimmed * 2;
    return scaled;
}

// The pass-through: forwards to `canonical_native_name` and does nothing else (2.1).
int display_native_name(int raw) { return canonical_native_name(raw); }

// One derived class and no other user: the C++ single-implementation case (3.1).
class BaseNativeChannel {
public:
    virtual int send(int message) const { return message; }
};

// The one implementation. Its body is three statements of its own, so the pass-through rule
// has to leave it alone whether or not this build records an `overrides` reference for a C++
// virtual member -- the Python override next door is excused by `overrides` and this one is
// excused by requirement 2.2, and between them both readings are covered.
class OnlyNativeChannel : public BaseNativeChannel {
public:
    virtual int send(int message) const {
        int labelled = display_native_name(message);
        int shouted = labelled + 1;
        return shouted;
    }
};

// Gives `OnlyNativeChannel` the project reference that keeps it out of the dead-code rule.
int open_native_channel(int message) {
    OnlyNativeChannel channel;
    return channel.send(message);
}
""",
    # The renamed twin (5.2), one half in each file. Neither is included by anything: a
    # single-definition file with one includer would be an over-export finding as well.
    "native/lean_twin_left.cpp": """// One half of the C++ renamed twin pair (lean-code 5.2).
//
// Every difference between this routine and `native/lean_twin_right.cpp`'s is an identifier,
// which is exactly what requirement 5.4 says similarity normalises away, and both are well
// past the six-statement floor the rule ships with. At most two consecutive lines are
// identical between the two files, so the duplicate-block rule has nothing to say about this
// pair and the similarity rule has.
//
// Measured on the installed build with the normalised shape design.md describes: this pair
// agrees at 1.00 and the Python pair in `lean/twin_left.py` agrees at 1.00, while a C++
// routine against a Python one agrees at only 0.63 -- 35 matched of 41 + 70 tokens. The two
// pairs are two findings at any threshold the rule would ship, never one four-way finding.
int summarise_native_orders(const int* totals, int count) {
    int total = 0;
    int seen = 0;
    for (int index = 0; index < count; ++index) {
        total = total + totals[index];
        seen = seen + 1;
    }
    if (seen == 0) {
        return 0;
    }
    return total / seen;
}
""",
    "native/lean_twin_right.cpp": """// The other half of the C++ renamed twin pair (lean-code 5.2).
//
// The same routine as `native/lean_twin_left.cpp`'s under other names.
int summarise_native_invoices(const int* amounts, int count) {
    int amount = 0;
    int found = 0;
    for (int index = 0; index < count; ++index) {
        amount = amount + amounts[index];
        found = found + 1;
    }
    if (found == 0) {
        return 0;
    }
    return amount / found;
}
""",
    # The copied block (5.1): the sixteen lines from `static const char* const columns[] = {`
    # to the closing brace of the routine are identical in both files, past the twelve-line
    # minimum the rule ships with.
    "native/lean_table_left.cpp": """// One half of the C++ copied block (lean-code 5.1).
//
// The copy is an initialiser rather than a run of statements, for the same reason the Python
// half is a literal: it has to survive the duplication rule's twelve-line minimum once
// whitespace and comments are dropped, and it has to stay below the similar-routine rule's
// six-statement floor, so that the twin pair stays the C++ sources' only similarity finding.
// `columns` is a local, so the duplicate-definition rule that already ships does not see it.
//
// Measured: sixteen consecutive normalised lines are shared with `native/lean_table_right.cpp`
// and no window of twelve is shared with the Python pair, whose surrounding syntax differs on
// every line.
const char* const* native_order_columns() {
    static const char* const columns[] = {
        "order_id",
        "customer_id",
        "created_at",
        "updated_at",
        "status",
        "currency",
        "subtotal",
        "discount",
        "shipping",
        "tax",
        "total",
        "notes",
    };
    return columns;
}
""",
    "native/lean_table_right.cpp": """// The other half of the C++ copied block (lean-code 5.1).
//
// The routine is named differently and the comment says something else, so the duplicate run
// is the initialiser itself: sixteen consecutive lines this file shares with
// `native/lean_table_left.cpp` and with nothing else in the project.
const char* const* native_invoice_columns() {
    static const char* const columns[] = {
        "order_id",
        "customer_id",
        "created_at",
        "updated_at",
        "status",
        "currency",
        "subtotal",
        "discount",
        "shipping",
        "tax",
        "total",
        "notes",
    };
    return columns;
}
""",
    # Not a case: what keeps two of them unique. See the block comment above.
    "native/measure.cpp": """// What takes `Shape` and `native/shape.h` out of two lean-code rules.
//
// Measured before this file existed: `Shape` had no use, call or typed reference anywhere in
// the project -- the unused-class predicate (1.1) exactly -- and `native/shape.h` defined one
// class, no function and had exactly one includer, which is the over-export predicate (4.1).
// Constructing a `Shape` answers the first and including the header answers the second, and
// `native/shape.h` is left to the overload contract it was written for.
//
// It is also the fixture's only caller of a routine in `native/shape.cpp`, so the C++
// overloads are reachable rather than merely present.
#include "shape.h"

int measure(int side, int width) {
    Shape shape(side);
    return shape.area(width) + scale(side);
}
""",
}
"""The sample repository, one entry per file, written verbatim under both roots."""

BASE_SOURCES: dict[str, str] = {
    **SOURCES,
    "main.py": "def main():\n    return 1\n",
}
"""The same files one commit earlier, with ``main`` shorter and importing nothing.

The contract project needs a history, not just a tree, because three things in the
understand-8-features specification are about a repository rather than a directory: a before
database built from a commit (requirement 3.2), a git-derived architecture generated from
commit dates and authors (requirement 4.3), and the comparison pair the two register with
each other (requirement 5.5). One commit would give a history with nothing before it, so
there are two, and the difference is deliberately the smallest thing that still moves both a
metric and the dependency graph: at the base commit ``main`` returns a literal and imports
nothing, so ``main.py`` is two code lines with no edge to ``app/entry.py``, and the head
commit adds both. A docstring alone would not do -- ``CountLineCode`` does not count one, so
the two sides would measure identically and every before/after test over this project would
pass without comparing anything. Every existing expectation about the *working tree* is
untouched, because the working tree is the head commit.
"""

FILES: tuple[str, ...] = tuple(sorted(SOURCES))
"""Every source file, root-relative with forward slashes -- the request's ``files``."""


def und() -> Path:
    """The licensed ``und`` the session probe found; the marker skips the test without one."""
    probe = understand_probe()
    assert probe.und is not None, probe.reason
    return probe.und


def upython() -> Path:
    """The interpreter Understand ships next to ``und``; skip when this build has none."""
    found = und().parent / "upython"
    if not found.exists():
        pytest.skip(f"no upython next to {und()}")
    return found


def real_env(mode: str) -> UnderstandEnv:
    """The installation this machine has, in the requested execution mode."""
    interpreter = upython()
    bin_dir = interpreter.parent
    return UnderstandEnv(
        home=bin_dir.parent.parent,
        und=bin_dir / "und",
        upython=interpreter,
        python_api_dir=bin_dir / "Python",
        version="6.5.1204",
        source="env:SCITOOLS_HOME",
        api_mode="upython" if mode == "upython" else "inprocess",
    )


def run_und(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``und`` directly, never through the adapters these tests exist to justify."""
    return subprocess.run(
        [str(und()), *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )


def write_tree(root: Path, sources: dict[str, str] | None = None) -> Path:
    """Write one copy of the sample repository under ``root`` and return it."""
    for name, text in (sources or SOURCES).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


@dataclass(frozen=True)
class History:
    """The two commits written over the sample sources, newest last."""

    base: str
    head: str


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in ``root`` with the developer's configuration and hooks kept out of it."""
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Gate Contract",
        "GIT_AUTHOR_EMAIL": "gate@example.invalid",
        "GIT_COMMITTER_NAME": "Gate Contract",
        "GIT_COMMITTER_EMAIL": "gate@example.invalid",
    }
    done = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        env=environment,
    )
    if done.returncode != 0:
        pytest.fail(f"git {' '.join(args)} exited {done.returncode}: {done.stderr.strip()}")
    return done


def init_history(root: Path) -> History:
    """Write the sample sources as two commits, leaving the tree at the second.

    The working tree ends holding exactly :data:`SOURCES`, which is what every contract test
    written before this fixture had a history expects to find there. What the history adds is
    a *base commit* that holds something else.
    """
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--quiet", "--initial-branch=main")
    commits = []
    for sources, message in ((BASE_SOURCES, "the base commit"), (SOURCES, "the head commit")):
        write_tree(root, sources)
        _git(root, "add", "--all")
        _git(root, "commit", "--quiet", "--no-verify", "--message", message)
        commits.append(_git(root, "rev-parse", "HEAD").stdout.strip())
    return History(base=commits[0], head=commits[1])


def build_database(db: Path, root: Path, languages: tuple[str, ...] = LANGUAGES) -> None:
    """Create a database over ``root`` and analyse it whole, failing loudly if it cannot.

    ``-quiet`` is used for ``create``/``add`` only: it silences the *answer* as well as the
    noise, and ``analyze`` needs its parse errors (measured, tasks.md 6.5).
    """
    for argv in (
        ["-quiet", "create", "-db", str(db), "-languages", *languages, "-local"],
        ["-quiet", "-db", str(db), "add", str(root)],
        ["-db", str(db), "analyze", "-all", "-errors", "-warnings"],
    ):
        done = run_und(*argv)
        if done.returncode != 0:
            pytest.fail(f"und {' '.join(argv)} exited {done.returncode}: {done.stderr.strip()}")


@dataclass(frozen=True)
class SampleProject:
    """The same sources analysed from two different directories (requirement 4.4)."""

    workdir: Path
    history: History

    @property
    def repo(self) -> Path:
        """The analysis root that is also a git repository, for the tests that need one."""
        return self.root(ROOTS[0])

    @property
    def base_commit(self) -> str:
        """The commit a before side of this project represents (requirements 3.2, 4.3)."""
        return self.history.base

    def root(self, name: str) -> Path:
        """The analysis root of one side, exactly as ``und add`` received it."""
        return self.workdir / name

    def db(self, name: str) -> Path:
        """The database built from one side."""
        return self.workdir / f"{name}.und"

    def analysed(self, name: str) -> list[str]:
        """The files the database holds, root-relative, as ``und list files`` reports them."""
        listing = run_und("-db", str(self.db(name)), "list", "files")
        assert listing.returncode == 0, listing.stderr
        root = f"{self.root(name)}/"
        return sorted(
            line.strip()[len(root) :] for line in listing.stdout.splitlines() if root in line
        )


@pytest.fixture(scope="session")
def sample_project(tmp_path_factory: pytest.TempPathFactory) -> SampleProject:
    """Two databases over token-identical sources, built from two differently named roots."""
    workdir = tmp_path_factory.mktemp("contract-project")
    project = SampleProject(workdir, init_history(workdir / ROOTS[0]))
    for name in ROOTS[1:]:
        write_tree(project.root(name))
    for name in ROOTS:
        build_database(project.db(name), project.root(name))
    return project


def contract_settings(depth: int = 2) -> Settings:
    """Thresholds naming every metric these tests read, including both synthetic ones.

    The request the worker receives is built by the production
    :class:`~scitools_hook.understand.snapshot.SnapshotExtractor` from these settings, so a
    metric only reaches the worker because a threshold asks for it -- exactly as it would on
    a real run. ``depth`` is the architecture depth requirement 6.7 makes configurable.
    """
    return Settings(
        thresholds=[
            ThresholdSpec(scope="routine", metric="CyclomaticStrict", limit=Limit(max=10)),
            ThresholdSpec(scope="routine", metric="CountLineCode", limit=Limit(max=60)),
            ThresholdSpec(scope="routine", metric="MaxNesting", limit=Limit(max=4)),
            ThresholdSpec(scope="routine", metric="CountParams", limit=Limit(max=5)),
            ThresholdSpec(scope="class", metric="CountDeclMethod", limit=Limit(max=20)),
            ThresholdSpec(scope="class", metric="CountDeclMethodNonStub", limit=Limit(max=20)),
            ThresholdSpec(scope="class", metric="PercentLackOfCohesion", limit=Limit(max=80)),
            ThresholdSpec(scope="file", metric="CountLineCode", limit=Limit(max=500)),
            ThresholdSpec(scope="file", metric="CountDeclFunction", limit=Limit(max=25)),
            ThresholdSpec(scope="file", metric="RatioCommentToCode", limit=Limit(min=0.0)),
            ThresholdSpec(scope="routine", metric="AVG:CyclomaticStrict", limit=Limit(max=5)),
            ThresholdSpec(scope="project", metric="MaxCyclomaticStrict", limit=Limit(max=15)),
        ],
        structure=StructureRules(depth=depth),
    )


def extract(
    db: Path, root: Path, files: tuple[str, ...], side: Side = "after", depth: int = 2
) -> ProjectSnapshot:
    """Read one real database into a snapshot through the production extractor."""
    return extract_with(db, root, files, contract_settings(depth))


def extract_with(
    db: Path, root: Path, files: tuple[str, ...], settings: Settings
) -> ProjectSnapshot:
    """The same extraction under settings of the caller's own.

    Separate because a test about *which* metrics reach the worker has to be able to name
    them, and :func:`contract_settings` is deliberately one fixed list that every other
    contract test reads.
    """
    runner = ApiRunner(real_env("upython"), NullCommandLog())
    extractor = SnapshotExtractor(runner, settings)
    target = SnapshotTarget(db=db, root=root, side="after", files=frozenset(files))
    return extractor.extract(target)


def a_symlink(source: Path, link: Path) -> None:
    """Make ``link`` point at ``source``, skipping where the filesystem refuses symlinks."""
    try:
        link.symlink_to(source)
    except (OSError, NotImplementedError) as refused:  # pragma: no cover - platform specific
        pytest.skip(f"this filesystem does not support symlinks: {refused!r}")


def comma_decimal_locale() -> str:
    """An installed locale whose decimal separator is a comma, or skip.

    Setting ``LC_NUMERIC`` to a locale the system does not have is a no-op, which would make
    a test that measures the comma-decimal hazard pass while proving nothing.
    """
    listing = shutil.which("locale")
    if listing is None:  # pragma: no cover - measured on a machine that has `locale`
        pytest.skip("no `locale` command, so no installed comma-decimal locale can be named")
    done = subprocess.run(
        [listing, "-a"], capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    installed = {line.strip().lower() for line in done.stdout.splitlines()}
    for candidate in ("de_DE.UTF-8", "fr_FR.UTF-8", "es_ES.UTF-8", "pt_BR.UTF-8"):
        if candidate.lower() in installed or candidate.lower().replace("-", "") in installed:
            return candidate
    pytest.skip("no comma-decimal locale is installed, so the SVG hazard cannot be provoked")
