"""Application preferences that live in the database but apply process-wide.

Only one thing needs this today: an explicit path to FreeCAD, for the case where it is installed
somewhere ArchPilot's automatic search does not look. The chosen path is saved as a normal
setting (never a secret) and also pushed into the environment variable that
:func:`archpilot.cad.client.detect` consults first, so the whole application agrees on which
FreeCAD to use — not just the screen that set it.
"""
from __future__ import annotations

import os
from pathlib import Path

from archpilot.cad.client import ENV_OVERRIDE, forget_detection

#: Database setting holding an explicit ``freecadcmd.exe`` path. Empty means "search normally".
SETTING_FREECAD_PATH = "freecad.executable"


def saved_freecad_path(db) -> str:
    """The path the user chose, or an empty string when none was set."""
    return (db.get_setting(SETTING_FREECAD_PATH, "") or "").strip()


def apply_freecad_path(db) -> str:
    """Push the saved path (if any) into the environment. Returns what is now in effect.

    Called once at start-up so a restart does not silently fall back to auto-detection.
    """
    path = saved_freecad_path(db)
    if path:
        os.environ[ENV_OVERRIDE] = path
    else:
        os.environ.pop(ENV_OVERRIDE, None)
    forget_detection()
    return path


def set_freecad_path(db, path: str) -> None:
    """Save an explicit path, or clear it when given an empty string."""
    clean = (path or "").strip()
    db.set_setting(SETTING_FREECAD_PATH, clean)
    if clean:
        os.environ[ENV_OVERRIDE] = clean
    else:
        os.environ.pop(ENV_OVERRIDE, None)
    forget_detection()


def looks_like_freecad(path: str) -> bool:
    """A cheap sanity check for the Settings screen before a full connection test.

    Deliberately permissive: any existing file is accepted, because the definitive answer comes
    from actually running it, not from guessing at its name.
    """
    clean = (path or "").strip()
    if not clean:
        return False
    return Path(clean).is_file()
