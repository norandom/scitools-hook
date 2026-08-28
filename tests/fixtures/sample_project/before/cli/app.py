# mypy: ignore-errors
"""Command line entry point of the sample project (analysis fixture, never executed)."""

from analysis.engine import Engine
from util.text import wrap_lines


def build_parser(argv):
    options = {}
    for item in argv:
        if item.startswith("--"):
            options[item[2:]] = True
    return options


def legacy_entry(argv):
    """Removed by the change: the deleted-routine case."""
    return main(argv)


def main(argv):
    return wrap_lines(Engine().run(build_parser(argv)))
