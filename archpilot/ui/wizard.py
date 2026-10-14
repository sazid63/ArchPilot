"""First-run setup: get FreeCAD found and, optionally, the assistant set up.

Shown only on a genuinely fresh installation. Nothing here is mandatory — the wizard can be
skipped at any point and the same choices live on the Settings screen afterwards. Its job is
simply to make sure a new user is not confronted by "FreeCAD not found" without knowing what to
do about it.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from archpilot import prefs
from archpilot.ai.providers import (
    DEFAULT_BASE_URL,
    KIND_NONE,
    KIND_OPENAI_COMPATIBLE,
    ProviderConfig,
    save_config,
)
from archpilot.branding import APP_NAME, FREECAD_ATTRIBUTION, PRELIMINARY_STAMP, TAGLINE
from archpilot.cad.client import detect, test_connection
from archpilot.core.paths import app_data_dir
from archpilot.storage.db import Database
from archpilot.ui import widgets


class SetupWizard(QDialog):
    """A short, skippable setup dialog. Returns whether the user finished it."""

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(f"{APP_NAME} setup")
        self.setMinimumSize(640, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_welcome())
        self.stack.addWidget(self._page_freecad())
        self.stack.addWidget(self._page_ai())
        self.stack.addWidget(self._page_done())
        root.addWidget(self.stack, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.back_button = widgets.button("Back", on_click=self._back)
        self.skip_button = widgets.button("Skip setup", on_click=self.reject)
        self.next_button = widgets.button("Continue", primary=True, on_click=self._next)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        buttons.addWidget(self.next_button)
        root.addLayout(buttons)

        self._sync()

    # -- pages ---------------------------------------------------------------
    def _page_welcome(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        title = QLabel(f"Welcome to {APP_NAME}")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(QLabel(TAGLINE))
        for line in (
            "Turn an idea into a 3D building, measure what it contains, and price it.",
            "FreeCAD does the modelling on your own computer.",
            "The AI assistant is optional — ArchPilot works fully without it.",
            "Nothing is sent anywhere unless you deliberately set up an assistant.",
        ):
            label = QLabel(f"•  {line}")
            label.setWordWrap(True)
            layout.addWidget(label)
        layout.addStretch(1)
        layout.addWidget(widgets.Banner("warning", "A preliminary tool", PRELIMINARY_STAMP))
        return page

    def _page_freecad(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        title = QLabel("FreeCAD")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(QLabel("ArchPilot uses FreeCAD to build 3D models. It is free."))

        self.freecad_banner = widgets.Banner()
        layout.addWidget(self.freecad_banner)

        self.freecad_path = QLineEdit()
        self.freecad_path.setPlaceholderText(
            "Leave empty to search automatically")
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(self.freecad_path, 1)
        path_row.addWidget(widgets.button("Browse…", on_click=self._browse_freecad))
        holder = QWidget()
        holder.setLayout(path_row)
        layout.addWidget(widgets.FieldRow("Path to freecadcmd.exe", holder))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(widgets.button("Check again", on_click=self._recheck_freecad))
        row.addWidget(widgets.button("Test it", on_click=self._test_freecad))
        row.addStretch(1)
        layout.addLayout(row)
        note = QLabel(FREECAD_ATTRIBUTION)
        note.setObjectName("Faint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        self._refresh_freecad()
        return page

    def _page_ai(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        title = QLabel("AI assistant (optional)")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        layout.addWidget(widgets.Banner(
            "info", "You can skip this",
            "Everything ArchPilot does — designing, modelling, measuring, pricing and "
            "exporting — works without an assistant. This only lets you describe changes in "
            "plain words."))

        self.ai_choice = QComboBox()
        self.ai_choice.addItem("Not now", KIND_NONE)
        self.ai_choice.addItem("Set up an OpenAI-compatible server", KIND_OPENAI_COMPATIBLE)
        self.ai_choice.currentIndexChanged.connect(self._sync_ai)
        layout.addWidget(widgets.FieldRow("Assistant", self.ai_choice))

        self.base_url = QLineEdit(DEFAULT_BASE_URL)
        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("Model name, e.g. a local model you have loaded")
        layout.addWidget(widgets.FieldRow("Server address", self.base_url))
        layout.addWidget(widgets.FieldRow("Model", self.model_name))
        layout.addStretch(1)
        self._sync_ai()
        return page

    def _page_done(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        title = QLabel("You are ready")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        self.done_summary = widgets.plain_text(QLabel(""))
        self.done_summary.setWordWrap(True)
        layout.addWidget(self.done_summary)
        layout.addWidget(QLabel(
            "Next: create a project on the Projects screen. Start from a ready-made building "
            "and change anything you like."))
        layout.addWidget(widgets.Banner(
            "info", "Everything is saved on this computer",
            f"Your work is kept in {app_data_dir()}."))
        layout.addStretch(1)
        return page

    # -- navigation ----------------------------------------------------------
    def _index(self) -> int:
        return self.stack.currentIndex()

    def _sync(self) -> None:
        index = self._index()
        self.back_button.setEnabled(index > 0)
        self.next_button.setText("Finish" if index == self.stack.count() - 1 else "Continue")
        if index == self.stack.count() - 1:
            self.done_summary.setText(self._summary())

    def _back(self) -> None:
        if self._index() > 0:
            self.stack.setCurrentIndex(self._index() - 1)
            self._sync()

    def _next(self) -> None:
        if self._index() == self.stack.count() - 1:
            self.accept()
            return
        self._commit_current_page()
        self.stack.setCurrentIndex(self._index() + 1)
        self._sync()

    def _commit_current_page(self) -> None:
        """Persist the choices on the page being left, without blocking progress."""
        if self._index() == 1:
            prefs.set_freecad_path(self.db, self.freecad_path.text().strip())
        elif self._index() == 2:
            kind = self.ai_choice.currentData()
            if kind == KIND_OPENAI_COMPATIBLE and self.model_name.text().strip():
                save_config(self.db, ProviderConfig(
                    kind=KIND_OPENAI_COMPATIBLE,
                    base_url=self.base_url.text().strip() or DEFAULT_BASE_URL,
                    model=self.model_name.text().strip()))

    def _summary(self) -> str:
        install = detect()
        freecad = (f"FreeCAD: {install.describe()}" if install
                   else "FreeCAD: not found — set its path on the Settings screen.")
        config = prefs.saved_freecad_path(self.db)
        chosen = "a saved path" if config else "automatic search"
        return f"{freecad}  (using {chosen})\nAssistant: set on the Settings screen if wanted."

    # -- FreeCAD helpers -----------------------------------------------------
    def _refresh_freecad(self) -> None:
        install = detect()
        if install is not None:
            self.freecad_banner.update_message(
                "ok", "FreeCAD found", install.describe())
        else:
            self.freecad_banner.update_message(
                "warning", "FreeCAD not found yet",
                "Install FreeCAD 1.x from freecad.org, or point at freecadcmd.exe below if it "
                "is already installed.")

    def _browse_freecad(self) -> None:
        path, _chosen = QFileDialog.getOpenFileName(
            self, "Choose freecadcmd.exe", "", "Programs (*.exe);;All files (*)")
        if path:
            self.freecad_path.setText(path)
            prefs.set_freecad_path(self.db, path)
            self._refresh_freecad()

    def _recheck_freecad(self) -> None:
        prefs.set_freecad_path(self.db, self.freecad_path.text().strip())
        self._refresh_freecad()

    def _test_freecad(self) -> None:
        prefs.set_freecad_path(self.db, self.freecad_path.text().strip())
        install = detect()
        if install is None:
            widgets.show_error(self, "Nothing to test",
                               "FreeCAD is still not found. Install it, or set the path.")
            return
        ok, message = test_connection(install.executable, app_data_dir() / "work")
        (widgets.show_info if ok else widgets.show_error)(self, "Connection test", message)
        self._refresh_freecad()

    # -- AI helpers ----------------------------------------------------------
    def _sync_ai(self) -> None:
        on = self.ai_choice.currentData() == KIND_OPENAI_COMPATIBLE
        self.base_url.setEnabled(on)
        self.model_name.setEnabled(on)
