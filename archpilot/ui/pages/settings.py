"""Settings: FreeCAD, the optional AI assistant, and where the data lives.

This is the one screen that can change what the application can do, so it is written to be
honest about defaults rather than clever. FreeCAD falls back to an automatic search unless an
explicit path is given; the assistant is off unless deliberately configured; and the API key is
described for what it is — kept out of the project database and never shown back.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from archpilot import prefs
from archpilot.ai import providers
from archpilot.ai.providers import KIND_NONE, KIND_OPENAI_COMPATIBLE, ProviderConfig
from archpilot.branding import (
    APP_NAME,
    ESTIMATE_SCOPE_NOTE,
    FREECAD_ATTRIBUTION,
    PRELIMINARY_STAMP,
    TAGLINE,
)
from archpilot.cad.client import detect
from archpilot.core.paths import app_data_dir, open_in_explorer
from archpilot.ui import widgets
from archpilot.ui.page import Page

AI_KINDS = [
    (KIND_NONE, "Off — no AI (everything still works)"),
    (KIND_OPENAI_COMPATIBLE, "OpenAI-compatible server (local or online)"),
]


class SettingsPage(Page):
    key = "settings"
    nav_label = "Settings"
    title = "Settings"
    subtitle = "FreeCAD, the optional assistant, and where ArchPilot keeps your work."

    def __init__(self, shell, parent: QWidget | None = None):
        super().__init__(shell, parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        root.addWidget(widgets.PageHeader(self.title, self.subtitle))

        self.freecad_banner = widgets.Banner()
        root.addWidget(self.freecad_banner)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        root.addLayout(columns)

        # -- FreeCAD ----------------------------------------------------------
        left = QVBoxLayout()
        left.setSpacing(16)
        columns.addLayout(left, 1)

        freecad = widgets.Card("FreeCAD")
        self.freecad_status = widgets.plain_text(QLabel(""))
        self.freecad_status.setWordWrap(True)
        freecad.add(self.freecad_status)

        self.freecad_path = QLineEdit()
        self.freecad_path.setPlaceholderText(
            "Leave empty to search automatically, e.g. "
            r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe")
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(self.freecad_path, 1)
        path_row.addWidget(widgets.button("Browse…", on_click=self._browse_freecad))
        freecad.add(widgets.FieldRow(
            "Path to freecadcmd.exe", _wrap(path_row),
            "Only needed when FreeCAD is installed somewhere ArchPilot does not look."))
        freecad_row = QHBoxLayout()
        freecad_row.setSpacing(8)
        freecad_row.addWidget(widgets.button("Save and use this path", primary=True,
                                             on_click=self._save_freecad))
        freecad_row.addWidget(widgets.button("Test the connection",
                                             on_click=self._test_freecad))
        freecad_row.addStretch(1)
        freecad.add_layout(freecad_row)
        attribution = QLabel(FREECAD_ATTRIBUTION)
        attribution.setObjectName("Faint")
        attribution.setWordWrap(True)
        freecad.add(attribution)
        left.addWidget(freecad)

        # -- Data -------------------------------------------------------------
        data = widgets.Card("Your data")
        folder = QLabel(str(app_data_dir()))
        folder.setObjectName("Mono")
        folder.setWordWrap(True)
        data.add(folder)
        data.add(widgets.button("Open this folder",
                                on_click=lambda: open_in_explorer(app_data_dir())))
        data.add(QLabel(
            "Designs, saved rates and logs live here. Back this folder up to keep your work; "
            "copy it to another computer to move everything at once."))
        left.addWidget(data)
        left.addStretch(1)

        # -- AI ---------------------------------------------------------------
        right = QVBoxLayout()
        right.setSpacing(16)
        columns.addLayout(right, 1)

        self.ai_banner = widgets.Banner()
        right.addWidget(self.ai_banner)

        ai = widgets.Card("AI assistant (optional)")
        form = QFormLayout()
        form.setSpacing(10)
        self.kind_combo = QComboBox()
        for value, label in AI_KINDS:
            self.kind_combo.addItem(label, value)
        self.kind_combo.currentIndexChanged.connect(lambda _i: self._sync_ai_enabled())
        form.addRow("Provider", self.kind_combo)

        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText(providers.DEFAULT_BASE_URL)
        form.addRow("Server address", self.base_url)

        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("The model name the server expects")
        form.addRow("Model", self.model_name)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, providers.MAX_TIMEOUT_S)
        self.timeout_spin.setSuffix(" s")
        form.addRow("Timeout", self.timeout_spin)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(
            "Leave empty for a local server; type to replace a stored key")
        form.addRow("API key", self.api_key)
        ai.add_layout(form)

        ai_row = QHBoxLayout()
        ai_row.setSpacing(8)
        ai_row.addWidget(widgets.button("Save AI settings", primary=True,
                                        on_click=self._save_ai))
        ai_row.addWidget(widgets.button("Test the assistant", on_click=self._test_ai))
        ai_row.addWidget(widgets.button("Forget the stored key", danger=True,
                                        on_click=self._forget_key))
        ai_row.addStretch(1)
        ai.add_layout(ai_row)
        self.key_state = widgets.plain_text(QLabel(""))
        self.key_state.setObjectName("Hint")
        self.key_state.setWordWrap(True)
        ai.add(self.key_state)
        right.addWidget(ai)

        about = widgets.Card(f"About {APP_NAME}")
        about.add(QLabel(f"{APP_NAME} — {TAGLINE}"))
        scope = QLabel(ESTIMATE_SCOPE_NOTE)
        scope.setObjectName("Hint")
        scope.setWordWrap(True)
        about.add(scope)
        stamp = QLabel(PRELIMINARY_STAMP)
        stamp.setObjectName("BannerTitleWarn")
        stamp.setWordWrap(True)
        about.add(stamp)
        right.addWidget(about)
        right.addStretch(1)

    # -- state ---------------------------------------------------------------
    def refresh(self) -> None:
        db = self.shell.db

        saved = prefs.saved_freecad_path(db)
        self.freecad_path.setText(saved)
        install = detect()
        if install is not None:
            source = "the path you set" if saved else "automatic search"
            self.freecad_status.setText(f"Using {install.describe()}  (found by {source})")
            self.freecad_banner.update_message(
                "ok", "FreeCAD is available",
                "ArchPilot can build 3D models on this computer.")
        else:
            self.freecad_status.setText(
                "FreeCAD was not found. Install FreeCAD 1.x from freecad.org, then set its "
                "path below.")
            self.freecad_banner.update_message(
                "error", "FreeCAD is needed to build models",
                "Without FreeCAD you can still design, edit rates and export an empty bill, "
                "but nothing can be modelled or measured.")

        config = providers.load_config(db)
        index = self.kind_combo.findData(config.kind)
        self.kind_combo.setCurrentIndex(index if index >= 0 else 0)
        self.base_url.setText(config.base_url)
        self.model_name.setText(config.model)
        self.timeout_spin.setValue(config.timeout_s)
        self.api_key.clear()
        self._sync_ai_enabled()

        current = providers.status(config)
        if current.available:
            self.ai_banner.update_message("ok", "The assistant is ready", current.detail)
        else:
            self.ai_banner.update_message("info", "The assistant is not in use", current.detail)
        self._update_key_state(config)

    def _update_key_state(self, config: ProviderConfig) -> None:
        if config.with_key_present():
            self.key_state.setText(
                "A key is stored, encrypted for this Windows account. It is never written into "
                "project files and is never shown here.")
        else:
            self.key_state.setText(
                "No key is stored. Local servers such as LM Studio or llama.cpp usually need "
                "none.")

    def _sync_ai_enabled(self) -> None:
        on = self.kind_combo.currentData() == KIND_OPENAI_COMPATIBLE
        for widget in (self.base_url, self.model_name, self.timeout_spin, self.api_key):
            widget.setEnabled(on)

    # -- FreeCAD actions -----------------------------------------------------
    def _browse_freecad(self) -> None:
        path, _chosen = QFileDialog.getOpenFileName(
            self, "Choose freecadcmd.exe", "", "Programs (*.exe);;All files (*)")
        if path:
            self.freecad_path.setText(path)

    def _save_freecad(self) -> None:
        path = self.freecad_path.text().strip()
        if path and not prefs.looks_like_freecad(path) and not widgets.confirm(
                self, "That file does not exist",
                f"'{path}' was not found.\n\nSave it anyway? ArchPilot will fall back to "
                f"searching automatically if it cannot be used.",
                confirm_text="Save anyway"):
            return
        prefs.set_freecad_path(self.shell.db, path)
        self.refresh()
        self.shell.set_status("FreeCAD path saved." if path else "FreeCAD path cleared.")

    def _test_freecad(self) -> None:
        install = detect()
        if install is None:
            widgets.show_error(
                self, "No FreeCAD to test",
                "Set a path, or install FreeCAD, before testing the connection.")
            return

        def work(progress):
            progress("Starting FreeCAD…")
            from archpilot.cad.client import test_connection
            return test_connection(install.executable, app_data_dir() / "work")

        def done(result):
            ok, message = result
            (widgets.show_info if ok else widgets.show_error)(self, "Connection test", message)

        self.shell.jobs.run(work, on_success=done,
                            on_error=lambda m, d: widgets.show_error(self, "Connection test",
                                                                     m, d))

    # -- AI actions ----------------------------------------------------------
    def _collect_config(self) -> ProviderConfig | None:
        kind = self.kind_combo.currentData()
        config = ProviderConfig(kind=kind, base_url=self.base_url.text().strip(),
                                model=self.model_name.text().strip(),
                                timeout_s=self.timeout_spin.value())
        if kind == KIND_OPENAI_COMPATIBLE and not (config.base_url and config.model):
            widgets.show_error(
                self, "Not enough detail",
                "An OpenAI-compatible provider needs both a server address and a model name.")
            return None
        return config

    def _save_ai(self) -> None:
        config = self._collect_config()
        if config is None:
            return
        typed_key = self.api_key.text()
        api_key = typed_key if typed_key else None
        if config.kind == KIND_NONE:
            api_key = "" if typed_key == "" else None
        try:
            providers.save_config(self.shell.db, config, api_key=api_key)
        except ValueError as exc:
            widgets.show_error(self, "Could not save", str(exc))
            return
        self.refresh()
        self.shell.set_status("AI settings saved.")

    def _test_ai(self) -> None:
        config = self._collect_config()
        if config is None:
            return

        def work(progress):
            progress("Contacting the assistant…")
            return providers.complete(config, "Reply with the single word: ready.")

        def done(result):
            if result.ok:
                widgets.show_info(self, "Assistant test",
                                  "The assistant replied, so it is working.")
            else:
                widgets.show_error(self, "Assistant test", result.summary)

        self.shell.jobs.run(work, on_success=done,
                            on_error=lambda m, d: widgets.show_error(self, "Assistant test",
                                                                     m, d))

    def _forget_key(self) -> None:
        if not providers.load_config(self.shell.db).with_key_present():
            self.shell.set_status("There is no stored key to forget.")
        else:
            providers.forget_api_key()
            self.shell.set_status("The stored API key was removed.")
        self.refresh()


def _wrap(layout) -> QWidget:
    """Put a layout inside a widget so it can be used anywhere a widget is expected."""
    holder = QWidget()
    holder.setLayout(layout)
    return holder
