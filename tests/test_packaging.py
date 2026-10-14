"""The packaging contract: everything ArchPilot imports is declared in pyproject.toml.

A third-party import that resolves only because some other package in a shared environment
happens to provide it would pass every other test in this suite and still fail for anyone who
installs ArchPilot with pip. This checks the declared dependencies are complete.
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "archpilot"


def _normalise(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _declared_distributions() -> set[str]:
    """Names declared in pyproject.toml, without the version specifiers."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = list(data["project"]["dependencies"])
    specs += list(data["project"].get("optional-dependencies", {}).get("dev", []))
    return {_normalise(re.split(r"[<>=!~\[ (;]", spec, maxsplit=1)[0]) for spec in specs}


def _top_level_imports() -> set[str]:
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def test_every_third_party_import_is_a_declared_dependency() -> None:
    external = {
        module
        for module in _top_level_imports()
        if module not in sys.stdlib_module_names and module != "archpilot"
    }
    assert external, "expected ArchPilot to import its declared third-party packages"

    declared = _declared_distributions()
    owners = packages_distributions()
    undeclared = sorted(
        module
        for module in external
        if not {_normalise(dist) for dist in owners.get(module, [])} & declared
    )
    assert not undeclared, (
        f"these imported modules are not declared in pyproject.toml: {undeclared}. "
        "Add the dependency, or remove the import."
    )
