"""Cost: the bill of quantities, and the rates behind it.

The rule this screen exists to enforce is honesty about pricing. An item with no rate is shown
as **unpriced** and its amount is left blank — never treated as zero — and the total is labelled
as covering only the priced items until every item has a rate. The illustrative nature of the
seeded rates is stated on the screen, not buried in a footnote.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from archpilot.branding import PRELIMINARY_STAMP
from archpilot.ui import theme, widgets
from archpilot.ui.page import Page

UNPRICED_TEXT = "unpriced — set a rate"


class RateDialog(QDialog):
    """Set or clear one rate. An empty field clears the rate (a real, supported action)."""

    def __init__(self, code: str, unit: str, description: str, current: float | None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Rate for {code}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(QLabel(description or code))

        form = QFormLayout()
        form.setSpacing(10)
        self.rate_spin = QDoubleSpinBox()
        self.rate_spin.setDecimals(2)
        self.rate_spin.setRange(0.0, 1e9)
        self.rate_spin.setGroupSeparatorShown(True)
        self.rate_spin.setSpecialValueText("no rate (leave unpriced)")
        if current is not None:
            self.rate_spin.setValue(current)
        else:
            self.rate_spin.setValue(0.0)
        form.addRow(f"Rate per {theme.unit_label(unit)}", self.rate_spin)
        layout.addLayout(form)

        hint = widgets.Banner(
            "info", "Leaving this unpriced is allowed",
            "If you do not have a rate, leave it at 'no rate'. The item stays in the bill and "
            "is counted as unpriced, so the total is never overstated by treating it as zero.")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        clear = buttons.addButton("Clear rate", QDialogButtonBox.ButtonRole.DestructiveRole)
        clear.clicked.connect(self._clear)
        layout.addWidget(buttons)
        self._cleared = False

    def _clear(self) -> None:
        self._cleared = True
        self.accept()

    def chosen_rate(self) -> float | None:
        if self._cleared:
            return None
        value = self.rate_spin.value()
        return value if value > 0 else None


class CostPage(Page):
    key = "cost"
    nav_label = "Cost & BOQ"
    title = "Cost and bill of quantities"
    subtitle = "Priced from the measured quantities and the rates you set."
    requires_project = True

    def __init__(self, shell, parent: QWidget | None = None):
        super().__init__(shell, parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        header = widgets.PageHeader(self.title, self.subtitle)
        self.recalculate_button = widgets.button("Recalculate", on_click=self._recalculate)
        self.save_button = widgets.button("Save rates", on_click=self._save_rates)
        header.add_action(self.recalculate_button)
        header.add_action(self.save_button)
        root.addWidget(header)

        self.banner = widgets.Banner()
        root.addWidget(self.banner)

        self.splitters = QSplitter(Qt.Orientation.Vertical)

        # -- the bill ---------------------------------------------------------
        bill_card = widgets.Card("Bill of quantities")
        self.bill_table = QTableWidget(0, 7)
        self.bill_table.setHorizontalHeaderLabels(
            ["Item", "Description", "Unit", "Qty", "Waste %", "Rate", "Amount"])
        self.bill_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bill_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.bill_table.setAlternatingRowColors(True)
        self.bill_table.verticalHeader().setVisible(False)
        header_view = self.bill_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5, 6):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.bill_table.doubleClicked.connect(self._edit_rate_from_bill)
        bill_card.add(self.bill_table)
        self.totals_label = widgets.plain_text(QLabel(""))
        self.totals_label.setObjectName("Mono")
        self.totals_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        bill_card.add(self.totals_label)
        self.splitters.addWidget(bill_card)

        # -- the rates --------------------------------------------------------
        rates_card = widgets.Card(
            "Rates", "Double-click a rate to change it. Setting 0 or clearing removes it, so "
                     "the item becomes 'unpriced' rather than free.")
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("Show everything", "all")
        self.filter_combo.addItem("Only items still to price", "unpriced")
        self.filter_combo.currentIndexChanged.connect(lambda _i: self._fill_rates())
        rates_card.add_header_widget(self.filter_combo)
        self.rates_table = QTableWidget(0, 4)
        self.rates_table.setHorizontalHeaderLabels(["Item", "Unit", "Rate", "Description"])
        self.rates_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rates_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rates_table.setAlternatingRowColors(True)
        self.rates_table.verticalHeader().setVisible(False)
        rates_header = self.rates_table.horizontalHeader()
        rates_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        rates_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        rates_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        rates_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.rates_table.doubleClicked.connect(self._edit_rate_from_rates)
        rates_card.add(self.rates_table)
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addWidget(widgets.button("Set the selected rate…",
                                            on_click=self._edit_rate_from_rates))
        button_row.addWidget(widgets.button("Load the region's illustrative rates",
                                            on_click=self._load_region_rates))
        button_row.addStretch(1)
        rates_card.add_layout(button_row)
        self.splitters.addWidget(rates_card)
        root.addWidget(self.splitters, 1)

        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(90)
        root.addWidget(self.notes)

    # -- state ---------------------------------------------------------------
    def refresh(self) -> None:
        session = self.session
        if session is None:
            self.bill_table.setRowCount(0)
            self.rates_table.setRowCount(0)
            self.totals_label.setText("")
            self.notes.setPlainText("")
            self.banner.update_message(
                "info", "No project open",
                "Open or create a project, build its model, and cost it here.")
            return

        self._fill_rates()
        if session.boq is None:
            self.bill_table.setRowCount(0)
            self.totals_label.setText("")
            self.banner.update_message(
                "warning", "Nothing to price yet",
                "Build the 3D model first — the bill is built from measured quantities.")
            return
        self._fill_bill()
        self._update_banner()

    def _update_banner(self) -> None:
        session = self.session
        if session is None or session.boq is None:
            return
        boq = session.boq
        if session.is_stale:
            self.banner.update_message(
                "warning", "These costs are out of date",
                "The design changed after the model was built. Rebuild the model to update the "
                "quantities and the bill.")
        elif not boq.complete:
            count = len(boq.unpriced_items)
            self.banner.update_message(
                "warning", f"{count} item(s) still to price",
                "The total below covers only the items that have a rate. Set the remaining "
                "rates to get a complete figure — unpriced items are never counted as zero.")
        else:
            self.banner.update_message(
                "ok", "Every item is priced",
                "This total covers the whole measured bill.")

    def _fill_bill(self) -> None:
        session = self.session
        if session is None or session.boq is None:
            return
        boq = session.boq
        self.bill_table.setRowCount(0)
        current_section = None
        for _section, items in boq.sections().items():
            for item in items:
                if item.section != current_section:
                    current_section = item.section
                    self._section_row(current_section)
                self._bill_row(item)
        self._fill_totals()
        notes: list[str] = [PRELIMINARY_STAMP, boq.note]
        if boq.assumptions:
            notes.append("")
            notes.append("Assumptions carried into this estimate:")
            notes.extend(f"•  {a}" for a in boq.assumptions)
        if boq.warnings:
            notes.append("")
            notes.extend(f"!  {w}" for w in boq.warnings)
        self.notes.setPlainText("\n".join(notes))

    def _section_row(self, title: str) -> None:
        row = self.bill_table.rowCount()
        self.bill_table.insertRow(row)
        cell = QTableWidgetItem(title)
        font = cell.font()
        font.setBold(True)
        cell.setFont(font)
        self.bill_table.setItem(row, 0, cell)
        self.bill_table.setSpan(row, 0, 1, 7)

    def _bill_row(self, item) -> None:
        row = self.bill_table.rowCount()
        self.bill_table.insertRow(row)
        self.bill_table.setItem(row, 0, _cell(item.code, mono=True))
        description = item.description
        if item.notes:
            description = f"{description}  ({'; '.join(item.notes)})"
        self.bill_table.setItem(row, 1, _cell(description))
        self.bill_table.setItem(row, 2, _cell(theme.unit_label(item.unit)))
        self.bill_table.setItem(row, 3, _cell(f"{item.quantity:,.3f}", right=True))
        self.bill_table.setItem(row, 4, _cell(f"{item.waste_pct:g}", right=True))
        if item.is_priced:
            self.bill_table.setItem(row, 5, _cell(f"{item.rate:,.2f}", right=True))
            self.bill_table.setItem(row, 6, _cell(f"{item.amount:,.2f}", right=True))
        else:
            unpriced = _cell(UNPRICED_TEXT)
            unpriced.setForeground(Qt.GlobalColor.darkYellow)
            self.bill_table.setItem(row, 5, unpriced)
            self.bill_table.setItem(row, 6, _cell("—", right=True))

    def _fill_totals(self) -> None:
        session = self.session
        if session is None or session.boq is None:
            return
        boq = session.boq
        currency = boq.currency
        lines = [
            f"Measured subtotal (priced items only): {currency} {boq.measured_subtotal:,.2f}",
            f"Contingency @ {boq.contingency_pct:g}%: {currency} {boq.contingency:,.2f}",
            f"Preliminaries @ {boq.preliminaries_pct:g}%: {currency} {boq.preliminaries:,.2f}",
            f"Net total: {currency} {boq.net_total:,.2f}",
        ]
        if boq.tax_pct:
            lines.append(f"Tax @ {boq.tax_pct:g}%: {currency} {boq.tax:,.2f}")
        if boq.complete:
            lines.append(f"TOTAL: {currency} {boq.grand_total:,.2f}")
        else:
            lines.append(
                f"TOTAL so far: {currency} {boq.grand_total:,.2f}  "
                f"(incomplete — {len(boq.unpriced_items)} item(s) unpriced)")
        self.totals_label.setText("\n".join(lines))

    def _fill_rates(self) -> None:
        session = self.session
        if session is None:
            return
        catalogue = session.rate_catalogue()
        only_unpriced = self.filter_combo.currentData() == "unpriced"
        self.rates_table.setRowCount(0)
        for code, item in sorted(catalogue.items.items()):
            if only_unpriced and item.rate is not None:
                continue
            row = self.rates_table.rowCount()
            self.rates_table.insertRow(row)
            self.rates_table.setItem(row, 0, _cell(code, mono=True))
            self.rates_table.setItem(row, 1, _cell(theme.unit_label(item.unit)))
            rate_cell = _cell("not set" if item.rate is None else f"{item.rate:,.2f}",
                              right=True)
            if item.rate is None:
                rate_cell.setForeground(Qt.GlobalColor.darkYellow)
            self.rates_table.setItem(row, 2, rate_cell)
            self.rates_table.setItem(row, 3, _cell(item.description))

    # -- actions -------------------------------------------------------------
    def _edit_rate_from_bill(self, index) -> None:
        code_item = self.bill_table.item(index.row(), 0)
        if code_item is None:
            return
        self._edit_rate(code_item.text())

    def _edit_rate_from_rates(self, *_args) -> None:
        rows = self.rates_table.selectionModel().selectedRows()
        if not rows:
            return
        code_item = self.rates_table.item(rows[0].row(), 0)
        if code_item is None:
            return
        self._edit_rate(code_item.text())

    def _edit_rate(self, code: str) -> None:
        session = self.session
        if session is None:
            return
        catalogue = session.rate_catalogue()
        item = catalogue.item_for(code)
        if item is None:
            return
        dialog = RateDialog(code, item.unit, item.description, item.rate, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        session.set_rate(code, item.unit, dialog.chosen_rate(), item.description)
        self.refresh()

    def _load_region_rates(self) -> None:
        session = self.session
        if session is None:
            return
        region = session.spec.design_region or "BD"
        if not widgets.confirm(
                self, "Load illustrative rates",
                f"Replace every rate with ArchPilot's illustrative rates for {region}?\n\n"
                f"These are published as examples only and must be checked against real quotes "
                f"before any use in a tender.",
                confirm_text="Load them", destructive=True):
            return
        from archpilot.cost.catalogue import default_catalogue
        session.catalogue = default_catalogue(region)
        session.price()
        self.refresh()

    def _recalculate(self) -> None:
        session = self.session
        if session is None:
            return
        if session.measurement is None:
            widgets.show_error(self, "Nothing to recalculate",
                               "Build the 3D model first.")
            return
        session.price()
        self.refresh()
        self.shell.set_status("Bill recalculated.")

    def _save_rates(self) -> None:
        session = self.session
        if session is None:
            return
        try:
            if session.project_id is None:
                session.save(notes="Saved rates from the Cost screen")
                self.shell.set_status("Project saved, with its rates.")
            else:
                revision = session.save_rates(notes="Saved rates from the Cost screen")
                self.shell.set_status(f"Rates saved — revision {revision}.")
        except Exception as exc:
            widgets.show_error(self, "Could not save the rates", str(exc))


def _cell(text: str, *, right: bool = False, mono: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if mono:
        from PySide6.QtGui import QFont
        font = QFont("Cascadia Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
        item.setFont(font)
    return item
