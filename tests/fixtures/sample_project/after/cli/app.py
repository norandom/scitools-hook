# mypy: ignore-errors
"""Command line entry point of the sample project (analysis fixture, never executed)."""

from analysis.engine import Engine
from understand.adapter import Adapter
from util.text import wrap_lines


def build_parser(argv):
    """Modified by the change: deeper nesting and more branches than before."""
    options = {}
    for item in argv:
        if item.startswith("--"):
            key = item[2:]
            if "=" in key:
                name, value = key.split("=", 1)
                if value.isdigit():
                    options[name] = int(value)
                else:
                    options[name] = value
            else:
                options[key] = True
        elif item.startswith("-"):
            for flag in item[1:]:
                options[flag] = True
    return options


def check_command(args):
    """Added by the change: the new-routine case."""
    return main(args)


def main(argv):
    options = build_parser(argv)
    details = Adapter().extract(str(sorted(options)))
    return wrap_lines(Engine().run(options) + [details["kind"]])
