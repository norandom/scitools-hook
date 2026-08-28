# mypy: ignore-errors
"""Drives the rules over one request."""

from analysis.rules import apply_rules


class Engine:
    def __init__(self):
        self.seen = 0

    def run(self, options):
        self.seen += 1
        return apply_rules(sorted(options))
