"""Application entry point.

Start-up order matters and is deliberate:

1. Create the data folders and open the database. If this fails, there is nothing to run, and
   the user gets a plain message rather than a traceback.
2. Apply the saved FreeCAD path, so the whole application agrees on which FreeCAD to use before
   any screen reads it.
3. Decide whether this is a first run (no projects at all) and, if so, offer the setup wizard.
4. Build the window and enter the event loop.

The default GUI entry point is :func:`main`, referenced by ``archpilot = archpilot.main:main``
in ``pyproject.toml``.
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from archpilot import prefs
from archpilot.branding import APP_NAME
from archpilot.core.paths import configure_logging, db_path, ensure_dirs
from archpilot.storage.db import Database
from archpilot.ui import theme
from archpilot.ui.main_window import MainWindow
from archpilot.ui.wizard import SetupWizard


def build_application(argv: list[str] | None = None) -> QApplication:
    """Create the QApplication with the app identity and stylesheet applied."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyleSheet(theme.stylesheet())
    return app


def run(argv: list[str] | None = None) -> int:
    """Run the application. Returns the process exit code."""
    app = build_application(argv)

    try:
        ensure_dirs()
        configure_logging()
        db = Database(db_path())
    except Exception as exc:
        QMessageBox.critical(
            None, f"{APP_NAME} could not start",
            f"{APP_NAME} could not open its data folder or database.\n\n{exc}")
        return 1

    prefs.apply_freecad_path(db)

    first_run = not db.list_projects(include_archived=True)
    window = MainWindow(db)
    if first_run:
        wizard = SetupWizard(db, window)
        wizard.exec()
        window.refresh_pages()

    window.show()
    return app.exec()


def main() -> None:
    """Console-script / GUI-script entry point."""
    sys.exit(run())


if __name__ == "__main__":
    main()
