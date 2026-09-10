"""One worked before-and-after example per lean-code rule (lean-code req 8.2).

A hint says what is wrong. An example shows the shorter form, and those are different
products: requirement 8.2 asks for the second because an agent learns a pattern from the
finding in front of it and not from documentation it will not open. So every rule in this
family carries one, in ponytail's own shape -- a location, a tag, what to cut and what
replaces it on the first line, then the code in two to five lines.

**Seven of the nine are code, not prose.** An example that describes the shorter form is a
second hint and buys nothing, so the body shows the before side and the after side and lets
a reader see the difference rather than take it on trust. Two of them cannot: the after side
of ``unused_class`` is an absence -- a deleted file has no code to print -- and
``net_growth`` is a whole-change finding with no location, so its two sides are what the
change did and what it should have done instead. Both are admitted here rather than dressed
up as listings, because an example that pretends to be code an agent could paste is worse
than a sentence that says what happened.

**They are not all Python.** This gate covers twelve languages, and an example in one of them
teaches the rule as a rule of that language. The nine below are spread over Java, TypeScript,
Python, C++ and C#, chosen so that each reads without knowing the language it is written in --
the rule is structural, so the example has to be too.

The keys ride the catalogue's existing variant namespace: ``structure.pass_through/example``
sits beside ``analysis.parse_error/type_params`` in one flat dict, which is what makes an
operator's ``[hints]`` table override an example exactly as it overrides a hint, with no
second configuration key and no second lookup (8.6). The cost of that is a **reserved variant
name**: no rule may ever set ``details["construct"] = "example"``, because the hint lookup
would then answer with the worked example. :mod:`scitools_hook.report.hints` records the
same reservation at the lookup, and ``tests/report/test_lean_hints.py`` holds it.

:data:`LEAN_RULES` is derived from the table rather than written beside it, so a rule added
to the family without an example is not in the tuple at all -- and the test that pins the
tuple to the tail of ``STRUCTURE_RULES`` fails, which is the earliest place the omission can
be caught. The family list lives here rather than in :mod:`scitools_hook.report.hints`
because this is the module that must name every rule once; the report layer's other reader
of the family, the agent-rules snippet, imports it from here.
"""

from __future__ import annotations

from typing import Final

from scitools_hook.models.findings import StructureRuleName, structure_rule

EXAMPLE_SUFFIX: Final = "/example"
"""What turns a rule name into its example key; ``/`` is the catalogue's variant separator.

Written out rather than composed from
:data:`~scitools_hook.report.hints.VARIANT_SEPARATOR`, because :mod:`scitools_hook.report.
hints` imports this module and the dependency may only run that way. The test asserts the
two agree, which is cheaper than a cycle.
"""

_BY_RULE: Final[dict[StructureRuleName, str]] = {
    "unused_parameter": (
        "Order.java:L44: delete: nothing reads `currency`; drop it and the 3 arguments.\n"
        "\n"
        "  before  double total(List<Item> items, String currency) { return sum(items); }\n"
        "  after   double total(List<Item> items)                  { return sum(items); }\n"
        "          the 3 call sites each lose an argument"
    ),
    "unused_class": (
        "src/legacy/CsvExporter.ts:L1-58: delete: the class is the file, and nothing names it.\n"
        "\n"
        "  before  export class CsvExporter { ... }   // 58 lines, 0 references\n"
        "  after   the file goes with the class; no import pointed at either"
    ),
    "unused_variable": (
        "settings.py:L12: delete: `DEFAULT_RETRIES` is written once and read nowhere.\n"
        "\n"
        "  before  DEFAULT_RETRIES = 3    # nothing reads it\n"
        "          MAX_WORKERS = 8\n"
        "  after   MAX_WORKERS = 8"
    ),
    "pass_through": (
        "service.py:L20-22: yagni: `save_user` only forwards, and it has one caller.\n"
        "\n"
        "  before  def save_user(u):        # service.py, the whole routine\n"
        "              return repo.insert(u)\n"
        "          save_user(user)          # api.py, the only caller\n"
        "  after   repo.insert(user)        # api.py; service.py loses the routine"
    ),
    "single_implementation": (
        "Notifier.java:L1-9: yagni: one implementer, so the interface varies nothing.\n"
        "\n"
        "  before  interface Notifier { void send(Message m); }\n"
        "          class SmtpNotifier implements Notifier { public void send(Message m) {...} }\n"
        "  after   class SmtpNotifier { public void send(Message m) {...} }   // interface gone"
    ),
    "over_export": (
        "src/formatCurrency.ts:L1-6: yagni: one definition, one importer -- a name, not a "
        "boundary.\n"
        "\n"
        "  before  formatCurrency.ts  export function formatCurrency(n) { ... }\n"
        "          invoice.ts         import { formatCurrency } from './formatCurrency';\n"
        "  after   invoice.ts         function formatCurrency(n) { ... }   // one file fewer"
    ),
    "duplicate_block": (
        "parser.cpp:L88-101: delete: the same 14 lines stand in reader.cpp:L40-53.\n"
        "\n"
        "  before  parser.cpp   open(); read(); decode(); close();   // 14 lines\n"
        "          reader.cpp   open(); read(); decode(); close();   // the same 14\n"
        "  after   stream.cpp   decode_stream(path)                  // one copy, two callers"
    ),
    "similar_routine": (
        "UserRepo.cs:L30-48: delete: `LoadUsers` is `LoadOrders` (OrderRepo.cs:L12) renamed.\n"
        "\n"
        '  before  List<User>  LoadUsers()  { return Query<User>("users"); }\n'
        '          List<Order> LoadOrders() { return Query<Order>("orders"); }\n'
        "  after   List<T> Load<T>(string table) { return Query<T>(table); }\n"
        '          callers  Load<User>("users"), Load<Order>("orders")'
    ),
    "net_growth": (
        "whole change, +180 lloc over 6 routines: shrink: the path it replaces is still there.\n"
        "\n"
        "  before  export_v2() added, +180 lloc; export() kept 'until the migration'\n"
        "  after   export() and its tests deleted, 3 callers moved   net: -40 lloc, same feature"
    ),
}
"""The nine examples, keyed by bare rule name; :data:`EXAMPLES` gives them their catalogue key."""

LEAN_RULES: Final[tuple[StructureRuleName, ...]] = tuple(_BY_RULE)
"""The lean-code family, in the design's order, derived from the table that must cover it.

It is the tail of :data:`~scitools_hook.models.findings.STRUCTURE_RULES`, and the test says
so: the grammar is owned by ``models``, and this is the report layer's view of which of its
names belong to this family.
"""

EXAMPLES: Final[dict[str, str]] = {
    f"{structure_rule(name)}{EXAMPLE_SUFFIX}": text for name, text in _BY_RULE.items()
}
"""Every example under the key the catalogue and an operator's ``[hints]`` table both use."""
