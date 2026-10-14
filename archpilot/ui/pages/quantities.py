"""Quantities: what the model actually contains, in billed units.

Every number here comes from measured geometry, never from the design on paper. The screen is
explicit about two things a quantity schedule usually hides: which items were *derived* by rule
rather than measured, and every warning raised while measuring. If the design has changed since
the model was built, the page says the figures are from the previous design rather than
presenting them as current.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archpilot.measure.engine import (
    CATEGORY_CONCRETE,
    CATEGORY_FINISHES,
    CATEGORY_FORMWORK,
    CATEGORY_MASONRY,
    CATEGORY_OPENINGS,
)
from archpilot.ui import theme, widgets
from archpilot.ui.page import Page

CATEGORY_LABELS = {
    CATEGORY_CONCRETE: "Concrete",
    CATEGORY_MASONRY: "Walling",
    CATEGORY_FORMWORK: "Formwork",
    CATEGORY_FINISHES: "Finishes",
    CATEGORY_OPENINGS: "Doors and windows",
}

SOURCE_LABELS = {
    "model": "measured",
    "derived": "derived by rule",
}


class QuantitiesPage(Page):
    key = "quantities"
    nav_label = "Quantities"
    title = "Quantities"
    subtitle = "Measured from the 3D model. Derived allowances are labelled so they are not " \
               "mistaken for measurements."
    requires_project = True

    def __init__(self, shell, parent: QWidget | None = None):
        super().__init__(shell, parent)
        self._all_lines: list = []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        header = widgets.PageHeader(self.title, self.subtitle)
        self.rebuild_button = widgets.button("Rebuild the model", primary=True,
                                             on_click=lambda: self.shell.go_to("model"))
        header.add_action(self.rebuild_button)
        root.addWidget(header)

        self.banner = widgets.Banner()
        root.addWidget(self.banner)

        totals = QHBoxLayout()
        totals.setSpacing(12)
        self.tile_concrete = widgets.StatTile("Concrete", "—")
        self.tile_walling = widgets.StatTile("Walling", "—")
        self.tile_formwork = widgets.StatTile("Formwork", "—")
        self.tile_finishes = widgets.StatTile("Finishes", "—")
        for tile in (self.tile_concrete, self.tile_walling, self.tile_formwork,
                     self.tile_finishes):
            totals.addWidget(tile, 1)
        root.addLayout(totals)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        filters.addWidget(QLabel("Show"))
        self.category_filter = QComboBox()
        self.category_filter.addItem("Everything", None)
        for key, label in CATEGORY_LABELS.items():
            self.category_filter.addItem(label, key)
        self.category_filter.currentIndexChanged.connect(lambda _i: self._fill_table())
        filters.addWidget(self.category_filter)
        filter_note = QLabel(
            "Only elements that were actually found in the model are counted. Anything the "
            "model measured but the design does not describe is listed below and excluded.")
        filter_note.setObjectName("Hint")
        filter_note.setWordWrap(True)
        filters.addWidget(filter_note, 1)
        root.addLayout(filters)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Item", "Description", "Unit", "Quantity", "Source", "Elements"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_selected_detail)
        root.addWidget(self.table, 1)

        detail = QHBoxLayout()
        detail.setSpacing(16)
        notes_card = widgets.Card("Selected item")
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(120)
        self.detail.setPlaceholderText("Select a row to see which elements produced it.")
        notes_card.add(self.detail)
        detail.addWidget(notes_card, 1)

        warnings_card = widgets.Card("Warnings")
        self.warnings = QPlainTextEdit()
        self.warnings.setReadOnly(True)
        self.warnings.setMaximumHeight(120)
        warnings_card.add(self.warnings)
        detail.addWidget(warnings_card, 1)
        root.addLayout(detail)

    # -- state ---------------------------------------------------------------
    def refresh(self) -> None:
        session = self.session
        if session is None:
            self._all_lines = []
            self.table.setRowCount(0)
            self._set_tiles_empty()
            self.warnings.setPlainText("")
            self.banner.update_message(
                "info", "No project open",
                "Open or create a project and build its model first.")
            return

        if session.measurement is None:
            self._all_lines = []
            self.table.setRowCount(0)
            self._set_tiles_empty()
            self.warnings.setPlainText("")
            self.banner.update_message(
                "warning", "Nothing measured yet",
                "Quantities come from the 3D model. Build the model on the 3D Model screen, "
                "then come back here.")
            return

        self._all_lines = list(session.measurement.lines)
        self._fill_table()
        self._fill_tiles()
        self._fill_warnings()
        self._update_banner()

    def _update_banner(self) -> None:
        session = self.session
        if session is None:
            return
        if session.is_stale:
            self.banner.update_message(
                "warning", "These figures are out of date",
                "The design changed after the model was built, so what you see describes the "
                "previous design. Rebuild the model to update these numbers.")
        else:
            self.banner.update_message(
                "ok", "Up to date",
                "These quantities were measured from the current model.")

    def _set_tiles_empty(self) -> None:
        for tile in (self.tile_concrete, self.tile_walling, self.tile_formwork,
                     self.tile_finishes):
            tile.set_value("—")

    def _fill_tiles(self) -> None:
        session = self.session
        if session is None or session.measurement is None:
            return
        lines = session.measurement.lines

        def total(category: str, unit: str) -> float:
            return sum(line.quantity for line in lines
                       if line.category == category and line.unit == unit)

        self.tile_concrete.set_value(f"{total(CATEGORY_CONCRETE, 'm3'):,.2f} m³")
        self.tile_walling.set_value(f"{total(CATEGORY_MASONRY, 'm3'):,.2f} m³")
        self.tile_formwork.set_value(f"{total(CATEGORY_FORMWORK, 'm2'):,.2f} m²")
        finishes = sum(line.quantity for line in lines
                       if line.category == CATEGORY_FINISHES and line.unit == "m2")
        self.tile_finishes.set_value(f"{finishes:,.2f} m²")

    def _fill_table(self) -> None:
        chosen = self.category_filter.currentData()
        lines = [line for line in self._all_lines
                 if chosen is None or line.category == chosen]
        self.table.setRowCount(0)
        for line in lines:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, _cell(line.code, mono=True))
            self.table.setItem(row, 1, _cell(line.description))
            self.table.setItem(row, 2, _cell(theme.unit_label(line.unit)))
            self.table.setItem(row, 3, _cell(f"{line.quantity:,.3f}", right=True))
            self.table.setItem(row, 4, _cell(SOURCE_LABELS.get(line.source, line.source)))
            self.table.setItem(row, 5, _cell(str(len(line.element_ids)), right=True))
        self._fill_warnings()

    def _fill_warnings(self) -> None:
        session = self.session
        if session is None or session.measurement is None:
            self.warnings.setPlainText("")
            return
        items = session.measurement.warnings
        other = [w for w in session.warnings if w not in items]
        combined = [*other, *items]
        if not combined:
            self.warnings.setPlainText("No warnings. Every element in the design was found.")
        else:
            self.warnings.setPlainText(
                "\n\n".join(f"•  {w}" for w in combined))

    def _show_selected_detail(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.detail.setPlainText("")
            return
        index = rows[0].row()
        code = self.table.item(index, 0).text()
        line = next((item for item in self._all_lines if item.code == code), None)
        if line is None:
            self.detail.setPlainText("")
            return
        parts = [
            f"Billed item: {line.code}",
            f"Category: {CATEGORY_LABELS.get(line.category, line.category)}",
            f"Source: {SOURCE_LABELS.get(line.source, line.source)}",
            f"Elements: {', '.join(line.element_ids) if line.element_ids else 'none'}",
        ]
        if line.notes:
            parts.append("")
            parts.extend(f"•  {note}" for note in line.notes)
        self.detail.setPlainText("\n".join(parts))


def _cell(text: str, *, right: bool = False, mono: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if mono:
        item.setFont(_mono_font())
    return item


def _mono_font():
    from PySide6.QtGui import QFont
    font = QFont("Cascadia Mono")
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font
