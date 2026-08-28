# mypy: ignore-errors
"""Stands in for the analysis backend the cli layer may not depend on."""


class Adapter:
    def extract(self, name):
        return {"name": name, "kind": "file"}
