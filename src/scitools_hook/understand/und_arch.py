"""The architecture file ``und import -arch`` reads and ``und export -arch`` writes.

An architecture is a tree of ``<arch name=...>`` elements whose leaves hold one member path
per line; :class:`ArchNode` is that tree in memory, :func:`read_architecture` and
:func:`write_architecture` are the two directions, and the constants are the spellings the
installed ``und`` was measured to accept (the prefix is optional on import, whitespace is
stripped from a member line, the doctype is the first line of an export). The commands that
move such a file in and out of a database are :class:`~scitools_hook.understand.und_cli.UndCli`'s;
this module knows nothing about running ``und``, which is what keeps the dependency one way.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

from scitools_hook.errors import AnalysisFailedError

ARCH_LIST_HEADER: Final = "Architectures:"
"""``list arches`` prints this and then one indented architecture name per line."""

DIRECTORY_STRUCTURE: Final = "Directory Structure"
"""The architecture every database has whether or not anyone declared one.

Derived from the directory layout, so a rule written against it can only ever say what the
folder tree already says -- which is exactly why a repository that wants to gate on *layers*
has to declare one of its own. Measured: it is present in a database that has just been
created and holds no files, and ``und remove -arch "Directory Structure"`` exits 0 and resets
it to the folders rather than deleting it.
"""

ARCH_TAG: Final = "arch"
"""The only element ``und import -arch`` reads; an architecture is a tree of these."""

ARCH_NAME_ATTR: Final = "name"
"""The attribute naming an architecture or one of its nodes."""

ARCH_DOCTYPE: Final = "<!DOCTYPE arch>"
"""The first line ``und export -arch`` writes; reproduced so an emitted file matches it."""

ARCH_LONGNAME_PREFIX: Final = "@l"
"""What ``und export -arch`` puts in front of every member path.

Measured: the prefix is **optional on import** -- a member written without it resolves
identically -- so it is stripped on the way in and written on the way out, and a
hand-written file may leave it off.
"""

ARCH_INDENT: Final = "  "
"""One level of indentation in an emitted architecture file.

Measured: ``und import -arch`` strips leading and trailing whitespace from a member line, so
an indented, human-readable file resolves exactly as Understand's own single-line export does.
"""

ARCH_HINT: Final = (
    "Export a starting point with `scitools-hook db export-arch`, edit it, and commit it: "
    "the file `und import -arch` reads is one <arch> element per node, each holding one "
    "repository-relative file path per line."
)
"""The one thing to do about a file ``und`` would not take: start from a real export."""


@dataclass(frozen=True)
class ArchNode:
    """One node of an architecture: a name, the files it holds and the nodes under it.

    The root node's :attr:`name` is the architecture's own name, which is what
    ``structure.architecture`` has to be set to for the layer and arch-cycle rules to read it.
    Members are file paths in whatever frame the holder is working in -- repository-relative
    in the checked-in file, absolute while ``und`` is being spoken to -- and :meth:`rebase`
    is how one frame becomes the other.
    """

    name: str
    members: tuple[str, ...] = ()
    children: tuple[ArchNode, ...] = ()

    def paths(self) -> Iterator[str]:
        """Every member of this node and of everything below it, in document order."""
        yield from self.members
        for child in self.children:
            yield from child.paths()

    def rebase(self, move: Callable[[str], str | None]) -> ArchNode:
        """The same tree with every member path put through ``move``.

        A member ``move`` answers ``None`` for is **dropped**, node and shape kept. That is
        the shape the before side needs: a file added by the change under review is not in
        the before shadow, so its declaration is not something to fail on -- it is simply not
        part of that side's architecture.
        """
        return ArchNode(
            name=self.name,
            members=tuple(moved for member in self.members if (moved := move(member)) is not None),
            children=tuple(child.rebase(move) for child in self.children),
        )


def read_architecture(text: str, source: str) -> ArchNode:
    """Parse one ``und`` architecture document, naming ``source`` in every refusal.

    Measured against build 1204's own export: the document is ``<!DOCTYPE arch>`` followed by
    a single ``<arch name="...">`` element, nested ``<arch>`` elements for the nodes, and the
    member paths as **text**, one per line, each prefixed ``@l``. A member written after a
    child element lands in that child's ``tail`` rather than in the parent's ``text``, so both
    are read.

    ``xml.etree`` is used rather than a regular expression because the failure this has to
    produce is a *typed* one: a malformed file must be refused here, in the operator's own
    words, rather than handed to ``und`` -- which answers ``Error: unable to import
    architecture - malformed XML.`` and exits 1 (measured), a fine outcome but one that names
    neither the line nor the file. It also declines external entities on its own (measured:
    ``ParseError: undefined entity``), so a checked-in file cannot reach outside itself.
    """
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as broken:
        raise AnalysisFailedError(
            f"{source} is not well-formed XML: {broken}", hint=ARCH_HINT
        ) from broken
    if root.tag != ARCH_TAG:
        raise AnalysisFailedError(
            f"{source} has <{root.tag}> at its root, and und import -arch reads <{ARCH_TAG}> there",
            hint=ARCH_HINT,
        )
    return _read_arch_node(root, source)


def write_architecture(node: ArchNode) -> str:
    """Serialise ``node`` into the document ``und import -arch`` reads.

    Indented and one member per line, which Understand's own export is not: measured, an
    import strips the whitespace around a member, so the readable form and the single-line
    form resolve to the same architecture and the readable one is what an operator has to
    keep in version control.
    """
    return "\n".join([ARCH_DOCTYPE, *_arch_lines(node, 0), ""])


def _read_arch_node(element: ElementTree.Element, source: str) -> ArchNode:
    """One ``<arch>`` element and everything under it."""
    name = element.get(ARCH_NAME_ATTR)
    if not name:
        raise AnalysisFailedError(
            f"{source} holds an <{ARCH_TAG}> element with no {ARCH_NAME_ATTR} attribute",
            hint=ARCH_HINT,
        )
    members = _arch_members(element.text)
    children: list[ArchNode] = []
    for child in element:
        if child.tag != ARCH_TAG:
            raise AnalysisFailedError(
                f"{source} holds a <{child.tag}> element inside {name!r}, and und "
                f"import -arch reads only <{ARCH_TAG}>",
                hint=ARCH_HINT,
            )
        children.append(_read_arch_node(child, source))
        members += _arch_members(child.tail)
    return ArchNode(name=name, members=tuple(members), children=tuple(children))


def _arch_members(text: str | None) -> list[str]:
    """The member paths in one run of element text: one per line, ``@l`` optional."""
    if not text:
        return []
    found: list[str] = []
    for line in text.splitlines():
        path = line.strip()
        if path.startswith(ARCH_LONGNAME_PREFIX):
            path = path[len(ARCH_LONGNAME_PREFIX) :].strip()
        if path:
            found.append(path)
    return found


def _arch_lines(node: ArchNode, depth: int) -> Iterator[str]:
    """One node as indented lines, members before children, exactly as ``und`` writes them."""
    pad = ARCH_INDENT * depth
    opening = f"{pad}<{ARCH_TAG} {ARCH_NAME_ATTR}={quoteattr(node.name)}>"
    if not node.members and not node.children:
        yield f"{opening}</{ARCH_TAG}>"
        return
    yield opening
    for member in node.members:
        yield f"{pad}{ARCH_INDENT}{ARCH_LONGNAME_PREFIX}{escape(member)}"
    for child in node.children:
        yield from _arch_lines(child, depth + 1)
    yield f"{pad}</{ARCH_TAG}>"


def read_arch_names(text: str) -> list[str]:
    """The architecture names under ``Architectures:``, one per indented line.

    Measured: the listing ends with a line of two spaces and no newline, so blank lines are
    skipped rather than trusted to be absent, and a name is taken stripped -- ``Directory
    Structure`` has a space in it, so splitting on whitespace would produce two names.
    """
    names: list[str] = []
    listing = False
    for line in text.splitlines():
        if not listing:
            listing = line.strip() == ARCH_LIST_HEADER
            continue
        name = line.strip()
        if name:
            names.append(name)
    return names
