"""The application window: a navigation rail, a stack of screens, and one shared job runner.

The window is the only place that owns a database connection and the only place that decides
which project is open. Pages ask it to open, create or close a project and then just read
``shell.session``; they never open a connection or a FreeCAD document themselves.

A single :class:`~archpilot.ui.workers.JobRunner` is shared, so a long FreeCAD build started on
one screen cannot be doubled up by clicking Build on another.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from archpilot.branding import APP_NAME, TAGLINE
from archpilot.core.errors import ArchPilotError, StorageError
from archpilot.project import ProjectSession
from archpilot.spec.schema import BuildingSpec
from archpilot.storage.db import Database
from archpilot.ui import theme, widgets
from archpilot.ui.page import Page
from archpilot.ui.pages.assistant import AssistantPage
from archpilot.ui.pages.cost import CostPage
from archpilot.ui.pages.design import DesignPage
from archpilot.ui.pages.exporting import ExportPage
from archpilot.ui.pages.model import ModelPage
from archpilot.ui.pages.projects import ProjectsPage
from archpilot.ui.pages.quantities import QuantitiesPage
from archpilot.ui.pages.settings import SettingsPage
from archpilot.ui.workers import JobRunner


class MainWindow(QMainWindow):
    sessionChanged = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.session: ProjectSession | None = None
        self.jobs = JobRunner(self)

        self.setWindowTitle(APP_NAME)
        self.resize(1180, 780)
        self.setMinimumSize(940, 620)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_rail())
        layout.addWidget(self._build_stack(), 1)

        self.setStatusBar(QStatusBar())
        self.set_status("Ready.")

        self.jobs.busy_changed.connect(self._on_busy_changed)
        self.jobs.progressed.connect(self.set_status)
        self.sessionChanged.connect(self._on_session_changed)

        self.refresh_pages()

    # -- construction --------------------------------------------------------
    def _build_rail(self) -> QWidget:
        rail = QWidget()
        rail.setFixedWidth(214)
        rail.setStyleSheet(f"background: {theme.PALETTE.primary};")
        column = QVBoxLayout(rail)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        brand = QWidget()
        brand_column = QVBoxLayout(brand)
        brand_column.setContentsMargins(18, 20, 18, 18)
        brand_column.setSpacing(2)
        name = QLabel(APP_NAME)
        name.setStyleSheet("color: #FFFFFF; font-size: 17pt; font-weight: 700;")
        tagline = QLabel(TAGLINE)
        tagline.setStyleSheet("color: #AFC4DD;")
        tagline.setWordWrap(True)
        brand_column.addWidget(name)
        brand_column.addWidget(tagline)
        column.addWidget(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        column.addWidget(self.nav, 1)

        self.project_label = widgets.plain_text(QLabel("No project open"))
        self.project_label.setWordWrap(True)
        self.project_label.setStyleSheet("color: #AFC4DD; padding: 12px 18px;")
        column.addWidget(self.project_label)
        return rail

    def _build_stack(self) -> QWidget:
        self.stack = QStackedWidget()
        self._pages: dict[str, Page] = {}
        self._order: list[str] = []

        page_classes = [ProjectsPage, DesignPage, AssistantPage, ModelPage, QuantitiesPage,
                        CostPage, ExportPage, SettingsPage]
        for page_class in page_classes:
            page = page_class(self)
            self._pages[page.key] = page
            self._order.append(page.key)
            self.stack.addWidget(page)
            item = QListWidgetItem(page.nav_label)
            item.setData(Qt.ItemDataRole.UserRole, page.key)
            if page.requires_project:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.nav.addItem(item)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)
        return self.stack

    # -- shell interface used by pages ---------------------------------------
    def go_to(self, key: str) -> None:
        if key not in self._pages:
            return
        row = self._order.index(key)
        item = self.nav.item(row)
        if item is not None and not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        self.nav.setCurrentRow(row)

    def set_status(self, text: str) -> None:
        self.statusBar().showMessage(text or "")

    def refresh_pages(self) -> None:
        for page in self._pages.values():
            page.refresh()

    def session_changed(self) -> None:
        self.sessionChanged.emit()

    def open_project(self, project_id: str) -> None:
        try:
            session = ProjectSession.open(self.db, project_id)
        except ArchPilotError as exc:
            widgets.show_error(self, "Could not open the project", exc.user_message())
            return
        except Exception as exc:
            widgets.show_error(self, "Could not open the project",
                               f"An unexpected problem occurred: {exc}")
            return
        self._adopt(session)

    def new_project(self, spec: BuildingSpec) -> None:
        session = ProjectSession.new(self.db, spec, region=spec.design_region or "BD")
        try:
            session.save(notes="Created from a template")
        except StorageError as exc:
            widgets.show_error(self, "Could not create the project", exc.user_message())
            return
        self._adopt(session)
        self.go_to("design")
        self.set_status(f"Created '{spec.name}'. Set the size, then build the model.")

    def close_project(self) -> None:
        """Forget the open project. Does not close the shared database connection."""
        self._drop_session()

    def _adopt(self, session: ProjectSession) -> None:
        self._drop_session()
        self.session = session
        self.sessionChanged.emit()
        self.set_status(f"Opened '{session.name}'.")

    def _drop_session(self) -> None:
        # Deliberately not calling ProjectSession.close(): the window owns the one database
        # connection, and a session closing it would break every later project.
        self.session = None
        self.sessionChanged.emit()

    # -- reactions -----------------------------------------------------------
    def _on_nav_changed(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        key = self._order[row]
        self._pages[key].on_shown()

    def _on_session_changed(self) -> None:
        has_project = self.session is not None
        for row, key in enumerate(self._order):
            page = self._pages[key]
            if not page.requires_project:
                continue
            item = self.nav.item(row)
            if item is None:
                continue
            flags = item.flags()
            if has_project:
                item.setFlags(flags | Qt.ItemFlag.ItemIsEnabled)
            else:
                item.setFlags(flags & ~Qt.ItemFlag.ItemIsEnabled)
        self.project_label.setText(
            f"Open project\n{self.session.name}" if has_project else "No project open")
        self.setWindowTitle(
            f"{self.session.name} — {APP_NAME}" if has_project else APP_NAME)
        # If the current screen now requires a project, fall back to the project list.
        current = self._order[self.stack.currentIndex()] if self.stack.currentIndex() >= 0 else ""
        if has_project is False and self._pages.get(current) and \
                self._pages[current].requires_project:
            self.go_to("projects")
        self.refresh_pages()

    def _on_busy_changed(self, busy: bool) -> None:
        self.nav.setEnabled(not busy)
        self.setCursor(Qt.CursorShape.BusyCursor if busy else Qt.CursorShape.ArrowCursor)

    # -- shutdown ------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.jobs.busy:
            if not widgets.confirm(
                    self, "A job is still running",
                    "ArchPilot is still working. Close anyway and stop the job?",
                    confirm_text="Close", destructive=True):
                event.ignore()
                return
            self.jobs.cancel()
            self.jobs.wait_for_idle(5000)
        self.session = None
        self.db.close()
        event.accept()
