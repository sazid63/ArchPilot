"""The page base class and the thin interface between a page and the main window.

Pages never import :mod:`archpilot.ui.main_window`; they depend only on the :class:`Shell`
protocol and on the project session, which keeps the import graph acyclic and lets a single
page be constructed in a test with a stub shell.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from archpilot.project import ProjectSession
    from archpilot.spec.schema import BuildingSpec
    from archpilot.storage.db import Database
    from archpilot.ui.workers import JobRunner


class Shell(Protocol):
    """What the main window offers a page. Implemented by ``MainWindow``."""

    db: "Database"
    session: "ProjectSession | None"
    jobs: "JobRunner"

    def go_to(self, key: str) -> None: ...
    def set_status(self, text: str) -> None: ...
    def refresh_pages(self) -> None: ...
    def open_project(self, project_id: str) -> None: ...
    def new_project(self, spec: "BuildingSpec") -> None: ...
    def close_project(self) -> None: ...
    def session_changed(self) -> None: ...


class Page(QWidget):
    """One screen. Subclasses build their widgets in ``__init__`` and implement ``refresh``."""

    key: str = ""
    nav_label: str = ""
    title: str = ""
    subtitle: str = ""
    #: When True the navigation entry is disabled until a project is open.
    requires_project: bool = False

    def __init__(self, shell: Shell, parent: QWidget | None = None):
        super().__init__(parent)
        self.shell = shell

    # -- lifecycle -----------------------------------------------------------
    def refresh(self) -> None:
        """Re-read state. Called on show and whenever the open project changes."""

    def on_shown(self) -> None:
        """Called each time the page becomes the visible one."""
        self.refresh()

    def busy(self, is_busy: bool) -> None:
        """Called when background work starts or stops, so controls can be locked."""

    # -- convenience ---------------------------------------------------------
    @property
    def session(self) -> "ProjectSession | None":
        return self.shell.session

    def has_project(self) -> bool:
        return self.shell.session is not None
