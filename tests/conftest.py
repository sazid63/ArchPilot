"""Shared pytest fixtures.

Anything that needs the real FreeCAD binary is marked ``freecad`` and skipped (not faked)
when FreeCAD is absent — so a green run on a machine without FreeCAD never implies FreeCAD
integration was verified.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from archpilot.cad.client import FreeCADInstall, detect


@pytest.fixture(scope="session")
def freecad_install() -> FreeCADInstall:
    if os.environ.get("ARCHPILOT_SKIP_FREECAD") == "1":
        pytest.skip("ARCHPILOT_SKIP_FREECAD=1")
    install = detect()
    if install is None:
        pytest.skip("FreeCAD (freecadcmd) is not installed on this machine")
    return install


@pytest.fixture(scope="session")
def freecad_workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("freecad")


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ArchPilot's user-data directory at a temp folder."""
    monkeypatch.setenv("ARCHPILOT_HOME", str(tmp_path / "ArchPilotHome"))
    return tmp_path / "ArchPilotHome"
