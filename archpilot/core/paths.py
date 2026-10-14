"""Where ArchPilot keeps its files.

User data lives in ``Documents/ArchPilot`` (database, logs, generated FreeCAD models) so it
survives app updates and can be backed up with the rest of a project. Nothing is written
outside the user's own folders.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from archpilot.branding import APP_NAME, DB_FILENAME, LOG_FILENAME, WORK_SUBDIR

APP_DATA_DIR_ENV = "ARCHPILOT_HOME"


def _documents_dir() -> Path:
    home = Path.home()
    docs = home / "Documents"
    if docs.is_dir():
        return docs
    return home


def app_data_dir() -> Path:
    override = os.environ.get(APP_DATA_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return _documents_dir() / APP_NAME


def ensure_dirs() -> Path:
    root = app_data_dir()
    (root / WORK_SUBDIR).mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    ensure_dirs()
    return app_data_dir() / DB_FILENAME


def log_path() -> Path:
    ensure_dirs()
    return app_data_dir() / LOG_FILENAME


def work_dir(project_id: str | None = None) -> Path:
    base = ensure_dirs() / WORK_SUBDIR
    if project_id:
        base = base / project_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def exports_dir() -> Path:
    path = app_data_dir() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging(verbose: bool = False) -> Path:
    root = logging.getLogger("archpilot")
    if getattr(root, "_archpilot_configured", False):
        return log_path()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.FileHandler(log_path(), encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    # A packaged windowed build has no console, so sys.stderr is None; only attach a stream
    # handler when there is somewhere to write.
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler())
    root._archpilot_configured = True  # type: ignore[attr-defined]
    return log_path()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"archpilot.{name}")


def open_in_explorer(path: Path) -> bool:
    """Reveal in Explorer (a folder opens, a file is selected)."""
    import subprocess
    from pathlib import Path as P
    p = P(path).resolve()
    if not p.exists():
        return False
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(p)])
        return True
    return False


def launch_file(path: Path) -> bool:
    """Open a file with the OS default application."""
    import sys as _sys
    from pathlib import Path as P
    p = P(path).resolve()
    if not p.exists():
        return False
    if _sys.platform == "win32":
        import os
        os.startfile(str(p))
        return True
    if _sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["open", str(p)])
        return True
    import subprocess
    subprocess.Popen(["xdg-open", str(p)])
    return True
