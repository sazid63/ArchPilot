"""Model: build the 3D model with FreeCAD, open it, and re-read it after hand edits.

FreeCAD runs as a separate program driven through generated scripts, never imported into this
process. Everything here therefore reports two things distinctly: what ArchPilot *asked* for
and what FreeCAD actually *produced*. When they disagree the differences are listed, not
hidden. A failed build leaves no model and no quantities behind.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from archpilot.branding import FREECAD_ATTRIBUTION
from archpilot.cad.client import detect, test_connection
from archpilot.cad.manifest import total_volume_m3
from archpilot.cad.reader import external_object_summary
from archpilot.core.paths import launch_file, open_in_explorer, work_dir
from archpilot.ui import widgets
from archpilot.ui.page import Page


class ModelPage(Page):
    key = "model"
    nav_label = "3D Model"
    title = "3D Model"
    subtitle = "Build the model in FreeCAD, then measure and price what it actually contains."
    requires_project = True

    def __init__(self, shell, parent: QWidget | None = None):
        super().__init__(shell, parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        header = widgets.PageHeader(self.title, self.subtitle)
        self.build_button = widgets.button("Build the 3D model", primary=True,
                                           on_click=self._build)
        self.open_button = widgets.button("Open in FreeCAD", on_click=self._open_in_freecad)
        self.resync_button = widgets.button("Re-read from FreeCAD", on_click=self._resync,
                                            tooltip="Pick up any edits you made in FreeCAD")
        header.add_action(self.build_button)
        header.add_action(self.open_button)
        header.add_action(self.resync_button)
        root.addWidget(header)

        self.banner = widgets.Banner()
        root.addWidget(self.banner)

        self.progress = QProgressBar()
        self.progress.setObjectName("Busy")
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self.step_label = widgets.plain_text(QLabel(""))
        self.step_label.setObjectName("Hint")
        self.step_label.setVisible(False)
        root.addWidget(self.step_label)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        root.addLayout(columns, 1)

        # -- left: connection and model facts --------------------------------
        left = QVBoxLayout()
        left.setSpacing(16)
        columns.addLayout(left, 1)

        connection = widgets.Card("FreeCAD")
        self.connection_label = widgets.plain_text(QLabel("Checking…"))
        self.connection_label.setWordWrap(True)
        connection.add(self.connection_label)
        connection_row = QHBoxLayout()
        connection_row.setSpacing(8)
        connection_row.addWidget(widgets.button("Check again", on_click=self.refresh))
        connection_row.addWidget(widgets.button(
            "Test the connection", on_click=self._test_connection))
        connection_row.addStretch(1)
        connection.add_layout(connection_row)
        attribution = QLabel(FREECAD_ATTRIBUTION)
        attribution.setObjectName("Faint")
        attribution.setWordWrap(True)
        connection.add(attribution)
        left.addWidget(connection)

        facts = widgets.Card("This model")
        self.facts_grid = QGridLayout()
        self.facts_grid.setHorizontalSpacing(12)
        self.facts_grid.setVerticalSpacing(12)
        self.tile_objects = widgets.StatTile("Objects measured", "—")
        self.tile_volume = widgets.StatTile("Concrete + masonry volume", "—")
        self.tile_duration = widgets.StatTile("Build time", "—")
        self.tile_external = widgets.StatTile("Added by hand", "—")
        self.facts_grid.addWidget(self.tile_objects, 0, 0)
        self.facts_grid.addWidget(self.tile_volume, 0, 1)
        self.facts_grid.addWidget(self.tile_duration, 1, 0)
        self.facts_grid.addWidget(self.tile_external, 1, 1)
        facts.add_layout(self.facts_grid)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(widgets.button("Show the model file", on_click=self._reveal))
        row.addWidget(widgets.button("Copy the path", on_click=self._copy_path))
        row.addStretch(1)
        facts.add_layout(row)
        self.model_path_label = widgets.plain_text(QLabel("—"))
        self.model_path_label.setObjectName("Mono")
        self.model_path_label.setWordWrap(True)
        self.model_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        facts.add(self.model_path_label)
        left.addWidget(facts)
        left.addStretch(1)

        # -- right: the build log --------------------------------------------
        right = QVBoxLayout()
        right.setSpacing(16)
        columns.addLayout(right, 1)

        checks = widgets.Card("Checks and differences")
        self.checks = QPlainTextEdit()
        self.checks.setReadOnly(True)
        self.checks.setPlaceholderText(
            "After a build, anything that differs between the design and the model is listed "
            "here — including objects changed or added by hand in FreeCAD.")
        checks.add(self.checks)
        right.addWidget(checks, 2)

        log_card = widgets.Card("What FreeCAD reported")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setObjectName("Mono")
        self.log.setPlaceholderText("The build log appears here.")
        log_card.add(self.log)
        right.addWidget(log_card, 1)

    # -- state ---------------------------------------------------------------
    def refresh(self) -> None:
        install = detect()
        if install is None:
            self.connection_label.setText(
                "FreeCAD was not found on this computer. ArchPilot cannot build a 3D model "
                "without it. Install FreeCAD 1.x from freecad.org, then set the path on the "
                "Settings screen.")
            self._set_connection_ok(False)
        else:
            self.connection_label.setText(f"Found: {install.describe()}")
            self._set_connection_ok(True)

        session = self.session
        if session is None:
            self.banner.update_message(
                "info", "No project open",
                "Open or create a project on the Projects screen to build a model.")
            self._set_enabled(False)
            return
        self._set_enabled(install is not None)
        self._update_facts()
        self._update_banner()
        self._update_checks()

    def _set_connection_ok(self, ok: bool) -> None:
        self.connection_label.setObjectName("Hint" if ok else "BannerTitleError")
        widgets.restyle(self.connection_label)

    def _set_enabled(self, freecad_found: bool) -> None:
        """Only offer the model buttons when they can actually do something."""
        session = self.session
        if session is None:
            for b in (self.build_button, self.open_button, self.resync_button):
                b.setEnabled(False)
            return
        self.build_button.setEnabled(freecad_found)
        self.open_button.setEnabled(session.has_model)
        self.resync_button.setEnabled(freecad_found and session.has_model)

    def _update_facts(self) -> None:
        session = self.session
        if session is None:
            return
        if session.build is None:
            self.tile_objects.set_value("—")
            self.tile_volume.set_value("—")
            self.tile_duration.set_value("—")
            self.tile_external.set_value("—")
            self.model_path_label.setText("—")
            return
        manifest = session.build.manifest
        self.tile_objects.set_value(str(len(manifest)))
        self.tile_volume.set_value(f"{total_volume_m3(manifest):,.3f} m³")
        self.tile_duration.set_value(f"{session.build.duration_s:.1f} s")
        hand_added = _external_count(session)
        self.tile_external.set_value(
            str(hand_added) if hand_added is not None else "—",
            "not in the design" if hand_added else "")
        self.model_path_label.setText(str(session.build.fcstd_path))

    def _update_banner(self) -> None:
        session = self.session
        if session is None:
            return
        if session.is_stale:
            self.banner.update_message(
                "warning", "Rebuild needed",
                "The design changed after this model was built. Press Build the 3D model to "
                "bring the quantities and costs up to date.")
        elif session.has_model:
            self.banner.update_message(
                "ok", "Model built",
                "The model matches the current design. Quantities and costs are up to date.")
        else:
            self.banner.update_message(
                "info", "Not built yet",
                "Press Build the 3D model. ArchPilot writes a script, runs FreeCAD on it, and "
                "reads back the measured geometry.")

    def _update_checks(self) -> None:
        session = self.session
        if session is None:
            return
        lines: list[str] = []
        if session.sync is not None:
            summary = external_object_summary(session.sync)
            if summary:
                lines.append(summary)
                lines.append("")
        lines.extend(f"•  {w}" for w in session.warnings)
        if not lines:
            lines.append("No differences found between the design and the model.")
        self.checks.setPlainText("\n".join(lines))

    # -- actions -------------------------------------------------------------
    def _build(self) -> None:
        session = self.session
        if session is None:
            return

        def work(progress):
            progress("Checking FreeCAD…")
            install = session.find_freecad()
            progress(f"Building with {install.executable.name}…")
            result = session.build_3d()
            progress("Measuring quantities…")
            session.measure()
            progress("Pricing…")
            session.price()
            return result

        started = self.shell.jobs.run(
            work,
            on_success=self._on_build_ok,
            on_error=self._on_error,
            on_progress=self._on_progress,
            on_finished=lambda: self._set_busy(False))
        if started:
            self._set_busy(True)
            self.banner.update_message("info", "Building…",
                                       "FreeCAD is building the model. This usually takes a "
                                       "few seconds.")

    def _resync(self) -> None:
        session = self.session
        if session is None:
            return

        def work(progress):
            progress("Re-reading the model from FreeCAD…")
            result = session.resync()
            progress("Re-measuring from the model on disk…")
            return result

        if self.shell.jobs.run(work, on_success=self._on_sync_ok, on_error=self._on_error,
                               on_progress=self._on_progress,
                               on_finished=lambda: self._set_busy(False)):
            self._set_busy(True)

    def _on_build_ok(self, result) -> None:
        session = self.session
        count = len(result.manifest)
        self.shell.set_status(f"Model built: {count} objects measured.")
        if session is not None and session.warnings:
            self.banner.update_message(
                "warning", "Built, with items to review",
                f"The model is built and the quantities are current, but {len(session.warnings)} "
                f"item(s) need attention — see the list on the right.")
        else:
            self.banner.update_message("ok", "Model built",
                                       "Quantities and costs are now up to date.")
        self._update_facts()
        self._update_checks()
        self.shell.refresh_pages()

    def _on_sync_ok(self, result) -> None:
        session = self.session
        external = len(session.sync.external) if session is not None and session.sync else 0
        if external:
            note = f"Re-read the model: {external} object(s) were not created by ArchPilot."
            self.banner.update_message(
                "warning", "Model re-read, with hand-made additions",
                f"{external} object(s) in the file are not part of the design and are excluded "
                f"from the quantities. They are listed on the right.")
        else:
            note = "Re-read the model."
            self.banner.update_message(
                "info", "Model re-read",
                "Quantities and costs have been recalculated from what is actually in the file.")
        self.shell.set_status(note)
        self._update_facts()
        self._update_checks()
        self.shell.refresh_pages()

    def _on_progress(self, text: str) -> None:
        self.step_label.setText(text)

    def _on_error(self, message: str, detail: str) -> None:
        self.banner.update_message("error", "That did not work", message)
        if detail:
            self.log.setPlainText(detail)
        self.shell.set_status("The last step failed.")
        widgets.show_error(self, "Could not finish", message, detail)
        self._update_facts()
        self._update_checks()

    def _set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.step_label.setVisible(busy)
        if not busy:
            self.step_label.clear()
        self.build_button.setEnabled(not busy)
        self._set_enabled(detect() is not None and not busy)

    def _test_connection(self) -> None:
        install = detect()
        if install is None:
            widgets.show_error(
                self, "FreeCAD not found",
                "Install FreeCAD 1.x from freecad.org, then set the path on the Settings "
                "screen.")
            return
        folder = work_dir(self.session.project_id if self.session else None)

        def work(progress):
            progress("Starting FreeCAD…")
            return test_connection(install.executable, folder)

        def done(result):
            ok, message = result
            (widgets.show_info if ok else widgets.show_error)(self, "Connection test", message)

        self.shell.jobs.run(work, on_success=done,
                            on_error=lambda m, d: widgets.show_error(self, "Connection test",
                                                                     m, d),
                            on_progress=self._on_progress,
                            on_finished=lambda: self._set_busy(False))
        self._set_busy(True)

    def _open_in_freecad(self) -> None:
        session = self.session
        if session is None or session.model_path is None:
            return
        if not launch_file(session.model_path):
            widgets.show_error(
                self, "Could not open the model",
                f"FreeCAD does not appear to be associated with .FCStd files.\n\n"
                f"Open this file yourself:\n{session.model_path}")

    def _reveal(self) -> None:
        session = self.session
        if session is not None and session.model_path is not None:
            open_in_explorer(session.model_path)

    def _copy_path(self) -> None:
        session = self.session
        if session is None or session.model_path is None:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(str(session.model_path))
        self.shell.set_status("Model path copied.")

    def busy(self, is_busy: bool) -> None:
        if is_busy:
            self._set_busy(True)


def _external_count(session) -> int | None:
    """How many objects in the file were not created by ArchPilot, if we have read it back."""
    if session.sync is None:
        return None
    return len(session.sync.external)
