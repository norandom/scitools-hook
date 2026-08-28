# mypy: ignore-errors
"""Small text helpers, used by the cli layer."""


def wrap_lines(lines, width=72):
    return [line[:width] for line in lines]
