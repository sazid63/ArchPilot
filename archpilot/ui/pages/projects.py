"""Projects: the first screen. Create, open, rename, archive and delete projects."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archpilot.cost.catalogue import REGIONS
from archpilot.spec.template import TEMPLATES, from_template
from archpilot.ui import widgets
from archpilot.ui.page import Page


class NewProjectDialog(QDialog):
    """Ask for a name, a starting template and a pricing region."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("New project")
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Riverside house")
        self.name_edit.setText("Untitled project")
        form.addRow("Project name", self.name_edit)

        self.template_combo = QComboBox()
        for label, _description, _params in TEMPLATES:
            self.template_combo.addItem(label, label)
        form.addRow("Starting point", self.template_combo)

        self.description = self._description_label()
        form.addRow("", self.description)

        self.region_combo = QComboBox()
        for code, spec in REGIONS.items():
            self.region_combo.addItem(f"{spec['name']} ({spec['currency']})", code)
        form.addRow("Prices for", self.region_combo)
        layout.addLayout(form)

        self.template_combo.currentIndexChanged.connect(self._sync_description)
        self._sync_description()

        note = widgets.Banner(
            "info", "What you get",
            "A single-storey or two-storey rectangular building with doors and windows, "
            "ready to model and price. Openings and wall thickness can be changed afterwards "
            "on the Design screen.")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _description_label(self):
        label = widgets.plain_text(QLabel(""))
        label.setObjectName("Hint")
        label.setWordWrap(True)
        return label

    def _sync_description(self) -> None:
        label = self.template_combo.currentData()
        for name, description, _params in TEMPLATES:
            if name == label:
                self.description.setText(description)
                return

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            widgets.show_error(self, "Name needed", "Give the project a name.")
            return
        self.accept()

    # -- results -------------------------------------------------------------
    def chosen_name(self) -> str:
        return self.name_edit.text().strip() or "Untitled project"

    def chosen_template(self) -> str:
        return self.template_combo.currentData()

    def chosen_region(self) -> str:
        return self.region_combo.currentData()


class ProjectsPage(Page):
    key = "projects"
    nav_label = "Projects"
    title = "Projects"
    subtitle = "Every design ArchPilot has saved on this computer."

    def __init__(self, shell, parent: QWidget | None = None):
        super().__init__(shell, parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        header = widgets.PageHeader(self.title, self.subtitle)
        header.add_action(widgets.button("New project…", primary=True,
                                         on_click=self._new_project))
        root.addWidget(header)

        self.banner = widgets.Banner()
        root.addWidget(self.banner)

        card = widgets.Card("Saved projects")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Prices for", "Last saved", "Model"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        card.add(self.table)
        root.addWidget(card, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.open_button = widgets.button("Open", primary=True, on_click=self._open_selected)
        self.rename_button = widgets.button("Rename…", on_click=self._rename_selected)
        self.archive_button = widgets.button("Archive", on_click=self._archive_selected)
        self.delete_button = widgets.button("Delete…", danger=True,
                                            on_click=self._delete_selected)
        self.show_archived = QComboBox()
        self.show_archived.addItem("Active projects", False)
        self.show_archived.addItem("Including archived", True)
        self.show_archived.currentIndexChanged.connect(lambda _i: self.refresh())
        for b in (self.open_button, self.rename_button, self.archive_button,
                  self.delete_button):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(self.show_archived)
        root.addLayout(actions)

    # -- data ----------------------------------------------------------------
    def refresh(self) -> None:
        include_archived = bool(self.show_archived.currentData())
        rows = self.shell.db.list_projects(include_archived=include_archived)
        self.table.setRowCount(0)
        for row in rows:
            self._append_row(row)

        if not rows:
            self.banner.update_message(
                "info", "No projects yet",
                "Choose New project to create one. You can start from a ready-made building "
                "and change anything afterwards.")
        else:
            self.banner.update_message("", "", "")
        self._sync_buttons()

    def _append_row(self, row) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        name = QTableWidgetItem(row.display_name + ("  (archived)" if row.archived else ""))
        name.setData(Qt.ItemDataRole.UserRole, row.id)
        self.table.setItem(r, 0, name)

        region = REGIONS.get(row.region, {})
        region_label = f"{region.get('name', row.region)} ({row.currency})" if row.region else "—"
        self.table.setItem(r, 1, QTableWidgetItem(region_label))
        self.table.setItem(r, 2, QTableWidgetItem(_friendly_time(row.updated_at)))

        model = "saved" if self._has_model(row.id) else "not built"
        self.table.setItem(r, 3, QTableWidgetItem(model))

    def _has_model(self, project_id: str) -> bool:
        from archpilot.core.paths import work_dir
        folder = work_dir(project_id)
        return any(folder.glob("*.FCStd"))

    def _sync_buttons(self) -> None:
        has_selection = self._selected_id() is not None
        for b in (self.open_button, self.rename_button, self.archive_button,
                  self.delete_button):
            b.setEnabled(has_selection)

    def _selected_id(self) -> str | None:
        items = self.table.selectedItems()
        if not items:
            return None
        first = self.table.item(items[0].row(), 0)
        return first.data(Qt.ItemDataRole.UserRole) if first else None

    def _selected_name(self) -> str:
        items = self.table.selectedItems()
        if not items:
            return ""
        return self.table.item(items[0].row(), 0).text().replace("  (archived)", "")

    # -- actions -------------------------------------------------------------
    def _new_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            spec = from_template(dialog.chosen_template(), dialog.chosen_name(),
                                 dialog.chosen_region())
        except ValueError as exc:
            widgets.show_error(self, "Could not create the project", str(exc))
            return
        self.shell.new_project(spec)

    def _open_selected(self) -> None:
        project_id = self._selected_id()
        if project_id is None:
            return
        self.shell.open_project(project_id)

    def _rename_selected(self) -> None:
        project_id = self._selected_id()
        if project_id is None:
            return
        current = self._selected_name()
        dialog = QDialog(self)
        dialog.setWindowTitle("Rename project")
        dialog.setMinimumWidth(360)
        layout = QVBoxLayout(dialog)
        edit = QLineEdit(current)
        layout.addWidget(QLabel("Project name"))
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and edit.text().strip():
            self.shell.db.rename_project(project_id, edit.text().strip())
            if self.shell.session is not None and self.shell.session.project_id == project_id:
                self.shell.session.spec = self.shell.session.spec.model_copy(
                    update={"name": edit.text().strip()})
            self.refresh()
            self.shell.refresh_pages()

    def _archive_selected(self) -> None:
        project_id = self._selected_id()
        if project_id is None:
            return
        row = self.shell.db.get_project(project_id)
        if row is None:
            return
        self.shell.db.set_archived(project_id, not row.archived)
        self.refresh()

    def _delete_selected(self) -> None:
        project_id = self._selected_id()
        if project_id is None:
            return
        name = self._selected_name()
        if not widgets.confirm(
                self, "Delete project",
                f"Permanently delete '{name}' and all of its saved versions?\n\n"
                f"This cannot be undone.",
                confirm_text="Delete", destructive=True):
            return
        if self.shell.session is not None and self.shell.session.project_id == project_id:
            self.shell.close_project()
        self.shell.db.delete_project(project_id)
        self.refresh()


def _friendly_time(iso: str) -> str:
    """Show a readable local time, falling back to the raw value if it cannot be parsed."""
    from datetime import datetime
    try:
        stamp = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or "—"
    return stamp.astimezone().strftime("%d %b %Y, %H:%M")
