"""Export: write the bill and the quantities out as files you can send or open elsewhere.

Three formats are offered, and the page is plain about what each is for: CSV to feed a
spreadsheet, XLSX for a formatted workbook, PDF for a document to print or email. Every export
carries the preliminary-estimate stamp, so a figure can never be lifted out of context and
mistaken for a final price. If nothing has been built yet, the buttons explain why rather than
failing silently.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from archpilot.branding import PRELIMINARY_STAMP
from archpilot.core.errors import ArchPilotError
from archpilot.core.paths import exports_dir, open_in_explorer
from archpilot.ui import widgets
from archpilot.ui.page import Page

#: (key, label, extension, description)
DOCUMENTS = [
    ("boq", "Bill of quantities", "Measurements with rates, waste, contingency and totals."),
    ("quantities", "Quantity schedule", "Measured quantities only, without prices."),
]
FORMATS = [
    ("csv", "CSV", ".csv", "Opens in any spreadsheet."),
    ("xlsx", "Excel workbook", ".xlsx", "Formatted, ready to send."),
    ("pdf", "PDF document", ".pdf", "Print or email as a finished page."),
]


class ExportPage(Page):
    key = "export"
    nav_label = "Export"
    title = "Export"
    subtitle = "Save the bill and the quantities as files. Estimates are stamped as preliminary."
    requires_project = True

    def __init__(self, shell, parent: QWidget | None = None):
        super().__init__(shell, parent)
        self._buttons: list = []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        header = widgets.PageHeader(self.title, self.subtitle)
        header.add_action(widgets.button("Open the exports folder",
                                         on_click=lambda: open_in_explorer(exports_dir())))
        root.addWidget(header)

        self.banner = widgets.Banner()
        root.addWidget(self.banner)

        self.grid_card = widgets.Card("Choose what to export")
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(12)
        self.grid_card.add_layout(self.grid)
        root.addWidget(self.grid_card)

        self.preview_card = widgets.Card("Where the files are saved")
        self.folder_label = widgets.plain_text(QLabel(str(exports_dir())))
        self.folder_label.setObjectName("Mono")
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview_card.add(self.folder_label)
        self.rename_edit = QLineEdit()
        self.rename_edit.setPlaceholderText("File name (without extension)")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("File name"))
        row.addWidget(self.rename_edit, 1)
        self.preview_card.add_layout(row)
        self.preview_card.add(widgets.Banner(
            "warning", "Every export says what it is",
            f"Each file carries the line \"{PRELIMINARY_STAMP}\", the project name, and the "
            f"assumptions behind it, so a reader can never mistake an early estimate for a "
            f"final price."))
        root.addWidget(self.preview_card)

        self.last_card = widgets.Card("Recently exported")
        self.last_label = widgets.plain_text(QLabel("Nothing exported yet."))
        self.last_label.setObjectName("Hint")
        self.last_label.setWordWrap(True)
        self.last_card.add(self.last_label)
        root.addWidget(self.last_card)
        root.addStretch(1)

        self._build_buttons()

    def _build_buttons(self) -> None:
        for column, (key, label, description) in enumerate(DOCUMENTS):
            heading = QLabel(label)
            heading.setObjectName("CardTitle")
            self.grid.addWidget(heading, 0, column)
            note = QLabel(description)
            note.setObjectName("Hint")
            note.setWordWrap(True)
            self.grid.addWidget(note, 1, column)
            for offset, (fmt, fmt_label, _ext, hint) in enumerate(FORMATS):
                b = widgets.button(f"Export as {fmt_label}",
                                   on_click=lambda _k=key, _f=fmt: self._export(_k, _f))
                b.setToolTip(hint)
                self.grid.addWidget(b, 2 + offset, column)
                self._buttons.append(b)

    # -- state ---------------------------------------------------------------
    def refresh(self) -> None:
        session = self.session
        self.folder_label.setText(str(exports_dir()))
        ready = session is not None and session.boq is not None
        for b in self._buttons:
            b.setEnabled(ready)
        if session is None:
            self.banner.update_message(
                "info", "No project open",
                "Open or create a project and build its model, then export from here.")
            return
        if session.boq is None:
            self.banner.update_message(
                "warning", "Nothing to export yet",
                "Build the 3D model first — the files are produced from measured quantities.")
            return
        if session.is_stale:
            self.banner.update_message(
                "warning", "What you export is out of date",
                "The design changed after the model was built, so any file you export now will "
                "describe the previous design. Rebuild the model first to be safe.")
        elif not session.boq.complete:
            self.banner.update_message(
                "warning", "The bill is incomplete",
                "Some items have no rate. They will appear as 'unpriced' in the file, and the "
                "total will be labelled as covering priced items only.")
        else:
            self.banner.update_message(
                "ok", "Ready to export",
                "The bill is complete and matches the current model.")

    # -- actions -------------------------------------------------------------
    def _default_name(self, document: str, extension: str) -> str:
        session = self.session
        base = (session.spec.name if session else "archpilot").strip() or "archpilot"
        safe = "".join(c if c.isalnum() or c in " -_" else "-" for c in base).strip()
        typed = self.rename_edit.text().strip()
        if typed:
            safe = "".join(c if c.isalnum() or c in " -_" else "-" for c in typed).strip()
        suffix = "bill-of-quantities" if document == "boq" else "quantities"
        return f"{safe} - {suffix}{extension}"

    def _export(self, document: str, fmt: str) -> None:
        session = self.session
        if session is None or session.boq is None:
            return
        extension = next(ext for key, _l, ext, _h in FORMATS if key == fmt)
        suggested = exports_dir() / self._default_name(document, extension)
        path, _chosen = QFileDialog.getSaveFileName(
            self, "Save as", str(suggested),
            f"{fmt.upper()} files (*{extension})")
        if not path:
            return
        try:
            written = _write(session, document, fmt, path)
        except ArchPilotError as exc:
            widgets.show_error(self, "Nothing to export", exc.user_message())
            return
        except Exception as exc:
            widgets.show_error(self, "Could not write the file", str(exc))
            return
        self.last_label.setText(f"Saved: {written}")
        self.shell.set_status(f"Exported {Path(written).name}")
        open_in_explorer(written)


def _write(session, document: str, fmt: str, path: str) -> str:
    methods = {
        ("boq", "csv"): session.export_boq_csv,
        ("boq", "xlsx"): session.export_boq_xlsx,
        ("boq", "pdf"): session.export_boq_pdf,
        ("quantities", "csv"): session.export_quantities_csv,
        ("quantities", "xlsx"): session.export_quantities_xlsx,
        ("quantities", "pdf"): session.export_quantities_pdf,
    }
    method = methods[(document, fmt)]
    return method(path)
