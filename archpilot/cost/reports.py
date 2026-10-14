"""Export a Bill of Quantities (and quantities alone) to CSV, Excel and PDF.

Every exported document carries the preliminary-estimate stamp and, when the bill is
incomplete, a prominent statement of how many items are unpriced. The totals in the file are
the same numbers the app shows — no rounding differences between screen and paper.
"""
from __future__ import annotations

import csv
import io
from xml.sax.saxutils import escape as _xml_escape

from archpilot.branding import PRELIMINARY_STAMP, report_footer
from archpilot.cost.boq import BOQ, BOQItem
from archpilot.measure.engine import MeasurementResult

_BLANK = ""

#: Characters that make a spreadsheet treat a text cell as a formula, not as text.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _safe(text: object) -> str:
    """Escape text so reportlab shows it literally instead of parsing it as markup.

    reportlab's ``Paragraph`` understands a small XML-like language, including ``<img src=...>``,
    and will happily fetch a remote image while building a PDF. Anything that could have come from
    a user, an imported file or an AI response must therefore be escaped before it is rendered, so
    it appears as plain text and can never pull in an external image or abort the export.
    """
    return _xml_escape("" if text is None else str(text))


def _defuse(text: str) -> str:
    """Stop a text cell from being interpreted as a formula when a spreadsheet opens it.

    A leading ``=`` (or ``+ - @``) turns a harmless description into an executable formula in
    Excel or LibreOffice. Prefixing a single quote keeps the visible text the same while forcing
    it to be read as text. Applied only to genuinely textual cells, never to numbers.
    """
    if text.startswith(_FORMULA_LEADS):
        return "'" + text
    return text


def _money(value: float | None, currency: str) -> str:
    return _BLANK if value is None else f"{currency} {value:,.2f}"


def _money_plain(value: float | None) -> str:
    return _BLANK if value is None else f"{value:,.2f}"


def _qty(value: float) -> str:
    return f"{value:,.3f}"


def _incomplete_note(boq: BOQ) -> str:
    n = len(boq.unpriced_items)
    if n == 0:
        return "All items priced."
    return (f"INCOMPLETE — {n} item(s) have no usable rate and are NOT included in the "
            f"totals below. The total is a partial figure.")


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
def boq_to_csv(boq: BOQ) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_defuse(f"Bill of Quantities — {boq.title or 'Untitled project'}")])
    w.writerow([PRELIMINARY_STAMP])
    w.writerow([_incomplete_note(boq)])
    if boq.note:
        w.writerow([_defuse(boq.note)])
    w.writerow([])
    w.writerow(["Section", "Code", "Description", "Unit", "Quantity",
                "Waste %", "Qty incl. waste", "Rate", f"Amount ({boq.currency})"])

    last_section = None
    for item in boq.items:
        if item.section != last_section:
            w.writerow([_defuse(item.section)])
            last_section = item.section
        w.writerow([
            "", _defuse(item.code), _defuse(item.description), _defuse(item.unit),
            _qty(item.quantity),
            f"{item.waste_pct:g}", _qty(item.quantity_with_waste) if item.is_priced else "",
            _money_plain(item.rate), _money_plain(item.amount),
        ])

    w.writerow([])
    w.writerow(["", "", "Measured subtotal (priced items only)", "", "", "", "", "",
                _money_plain(boq.measured_subtotal)])
    w.writerow(["", "", f"Contingency @ {boq.contingency_pct:g}%", "", "", "", "", "",
                _money_plain(boq.contingency)])
    w.writerow(["", "", f"Preliminaries @ {boq.preliminaries_pct:g}%", "", "", "", "", "",
                _money_plain(boq.preliminaries)])
    w.writerow(["", "", "Net total", "", "", "", "", "", _money_plain(boq.net_total)])
    if boq.tax_pct:
        w.writerow(["", "", f"Tax @ {boq.tax_pct:g}%", "", "", "", "", "",
                    _money_plain(boq.tax)])
    w.writerow(["", "", f"TOTAL ({boq.currency})", "", "", "", "", "",
                _money_plain(boq.grand_total)])
    w.writerow([])
    w.writerow([report_footer()])
    return buf.getvalue()


def quantities_to_csv(measurement: MeasurementResult, *,
                      title: str = "Measured quantities") -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_defuse(title)])
    w.writerow([PRELIMINARY_STAMP])
    w.writerow([])
    w.writerow(["Category", "Code", "Description", "Unit", "Quantity", "Elements", "Source"])
    for line in measurement.lines:
        w.writerow([_defuse(line.category), _defuse(line.code), _defuse(line.description),
                    _defuse(line.unit), _qty(line.quantity), len(line.element_ids),
                    _defuse(line.source)])
    w.writerow([])
    for unit, total in measurement.unit_totals.items():
        w.writerow(["", "", "TOTAL", unit, _qty(total)])
    if measurement.warnings:
        w.writerow([])
        w.writerow(["Warnings"])
        for note in measurement.warnings:
            w.writerow([_defuse(note)])
    w.writerow([])
    w.writerow([report_footer()])
    return buf.getvalue()


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------
def boq_to_xlsx_bytes(boq: BOQ) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "BOQ"

    bold = Font(bold=True)
    title_font = Font(bold=True, size=14)
    head_fill = PatternFill("solid", fgColor="1F4E4A")
    head_font = Font(bold=True, color="FFFFFF")
    section_font = Font(bold=True, color="1F4E4A")

    ws.append([_defuse(f"Bill of Quantities — {boq.title or 'Untitled project'}")])
    ws["A1"].font = title_font
    ws.append([PRELIMINARY_STAMP])
    ws.append([_incomplete_note(boq)])
    if boq.note:
        ws.append([_defuse(boq.note)])
    ws.append([])

    headers = ["Section", "Code", "Description", "Unit", "Quantity", "Waste %",
               "Qty incl. waste", "Rate", f"Amount ({boq.currency})"]
    ws.append(headers)
    header_row = ws.max_row
    for cell in ws[header_row]:
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center")

    last_section = None
    for item in boq.items:
        if item.section != last_section:
            ws.append([_defuse(item.section)])
            ws.cell(row=ws.max_row, column=1).font = section_font
            last_section = item.section
        ws.append(["", _defuse(item.code), _defuse(item.description), _defuse(item.unit),
                   item.quantity, item.waste_pct,
                   item.quantity_with_waste if item.is_priced else None,
                   item.rate, item.amount])

    ws.append([])
    summary_start = ws.max_row
    rows = [
        ("Measured subtotal (priced items only)", boq.measured_subtotal),
        (f"Contingency @ {boq.contingency_pct:g}%", boq.contingency),
        (f"Preliminaries @ {boq.preliminaries_pct:g}%", boq.preliminaries),
        ("Net total", boq.net_total),
    ]
    if boq.tax_pct:
        rows.append((f"Tax @ {boq.tax_pct:g}%", boq.tax))
    rows.append((f"TOTAL ({boq.currency})", boq.grand_total))
    for label, value in rows:
        ws.append(["", "", label, "", None, None, None, None, value])
    for row in range(summary_start, ws.max_row + 1):
        ws.cell(row=row, column=3).font = bold
        ws.cell(row=row, column=9).font = bold
    ws.cell(row=ws.max_row, column=9).font = Font(bold=True, size=12)

    for column, width in zip("ABCDEFGHI", (16, 20, 46, 7, 12, 9, 15, 14, 18), strict=True):
        ws.column_dimensions[column].width = width
    for row in range(summary_start, ws.max_row + 1):
        ws.cell(row=row, column=9).number_format = "#,##0.00"
    for row in range(header_row + 1, summary_start):
        ws.cell(row=row, column=5).number_format = "#,##0.000"
        ws.cell(row=row, column=9).number_format = "#,##0.00"

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def quantities_to_xlsx_bytes(measurement: MeasurementResult, boq: BOQ | None = None,
                             title: str = "Quantities") -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Quantities"
    ws.append([_defuse(title)])
    ws.append([PRELIMINARY_STAMP])
    ws.append([])
    ws.append(["Category", "Code", "Description", "Unit", "Quantity",
               "Elements", "Source", "Notes"])
    for line in measurement.lines:
        ws.append([_defuse(line.category), _defuse(line.code), _defuse(line.description),
                   _defuse(line.unit), line.quantity, len(line.element_ids),
                   _defuse(line.source), _defuse("; ".join(line.notes))])
    ws.append([])
    ws.append(["", "", "TOTALS BY UNIT"])
    for unit, total in measurement.unit_totals.items():
        ws.append(["", "", "", unit, total])
    for column, width in zip("ABCDEFGH", (14, 22, 46, 8, 12, 10, 10, 40), strict=True):
        ws.column_dimensions[column].width = width
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def boq_to_pdf_bytes(boq: BOQ) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
    warning = ParagraphStyle("warning", parent=small, textColor=colors.HexColor("#8a2b06"))
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=15, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=10, spaceBefore=6,
                        spaceAfter=2, textColor=colors.HexColor("#1F4E4A"))

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4),
                            leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=13 * mm, bottomMargin=13 * mm,
                            title=f"Bill of Quantities — {boq.title}")
    flow = []
    flow.append(Paragraph(_safe(f"Bill of Quantities — {boq.title or 'Untitled project'}"), h1))
    flow.append(Paragraph(_safe(PRELIMINARY_STAMP), warning))
    flow.append(Paragraph(_safe(_incomplete_note(boq)), warning))
    if boq.note:
        flow.append(Paragraph(_safe(boq.note), small))
    flow.append(Spacer(1, 6))

    header = ["Code", "Description", "Unit", "Qty", "Waste %", "Qty+", "Rate",
              f"Amount ({boq.currency})"]
    table_data = [header]
    styles_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E4A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9bb3b0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
    ]
    row = 1
    last_section = None
    for item in boq.items:
        if item.section != last_section:
            table_data.append([item.section, "", "", "", "", "", "", ""])
            styles_cmds.append(("SPAN", (0, row), (-1, row)))
            styles_cmds.append(("BACKGROUND", (0, row), (-1, row),
                                colors.HexColor("#dce9e7")))
            styles_cmds.append(("FONTNAME", (0, row), (-1, row), "Helvetica-Bold"))
            row += 1
            last_section = item.section
        table_data.append([
            item.code, Paragraph(_safe(item.description), small), item.unit,
            _qty(item.quantity), f"{item.waste_pct:g}",
            _qty(item.quantity_with_waste) if item.is_priced else "—",
            _money_plain(item.rate), _money_plain(item.amount),
        ])
        row += 1

    summary_rows = [
        ("", "Measured subtotal (priced items only)", _money_plain(boq.measured_subtotal)),
        ("", f"Contingency @ {boq.contingency_pct:g}%", _money_plain(boq.contingency)),
        ("", f"Preliminaries @ {boq.preliminaries_pct:g}%", _money_plain(boq.preliminaries)),
        ("", "Net total", _money_plain(boq.net_total)),
    ]
    if boq.tax_pct:
        summary_rows.append(("", f"Tax @ {boq.tax_pct:g}%", _money_plain(boq.tax)))
    summary_rows.append(("", f"TOTAL ({boq.currency})", _money_plain(boq.grand_total)))
    for _a, label, value in summary_rows:
        table_data.append(["", label, "", "", "", "", "", value])
        styles_cmds.append(("SPAN", (1, row), (6, row)))
        styles_cmds.append(("FONTNAME", (0, row), (-1, row), "Helvetica-Bold"))
        row += 1

    widths = [58, 250, 32, 52, 40, 52, 62, 72]
    table = Table(table_data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(styles_cmds))
    flow.append(table)

    if boq.warnings:
        flow.append(Spacer(1, 8))
        flow.append(Paragraph("Unpriced or incomplete items", h3))
        for note in boq.warnings:
            flow.append(Paragraph(_safe(f"• {note}"), warning))

    if boq.assumptions:
        flow.append(Spacer(1, 8))
        flow.append(Paragraph("Assumptions and limitations", h3))
        for note in boq.assumptions:
            flow.append(Paragraph(_safe(f"• {note}"), small))

    flow.append(Spacer(1, 10))
    flow.append(Paragraph(_safe(report_footer()), small))
    doc.build(flow)
    return out.getvalue()


def quantities_to_pdf_bytes(measurement: MeasurementResult, *,
                            title: str = "Measured quantities") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=14 * mm,
                            rightMargin=14 * mm, topMargin=13 * mm, bottomMargin=13 * mm,
                            title=title)
    flow = [Paragraph(_safe(title), styles["Heading1"]),
            Paragraph(_safe(PRELIMINARY_STAMP), styles["Normal"]), Spacer(1, 6)]
    data = [["Category", "Code", "Description", "Unit", "Quantity", "Elements", "Source"]]
    for line in measurement.lines:
        data.append([line.category, line.code,
                     Paragraph(_safe(line.description), styles["Normal"]),
                     line.unit, _qty(line.quantity), str(len(line.element_ids)), line.source])
    for unit, total in measurement.unit_totals.items():
        data.append(["", "", "TOTAL", unit, _qty(total), "", ""])
    table = Table(data, colWidths=[70, 130, 280, 40, 70, 55, 60], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E4A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9bb3b0")),
        ("ALIGN", (4, 1), (4, -1), "RIGHT"),
    ]))
    flow.append(table)
    if measurement.warnings:
        flow.append(Spacer(1, 8))
        flow.append(Paragraph("Warnings", styles["Heading3"]))
        for note in measurement.warnings:
            flow.append(Paragraph(_safe(f"• {note}"), styles["Normal"]))
    flow.append(Spacer(1, 10))
    flow.append(Paragraph(_safe(report_footer()), styles["Normal"]))
    doc.build(flow)
    return out.getvalue()


def write_bytes(path, data: bytes) -> str:
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return str(p)


def write_text(path, text: str) -> str:
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8-sig")
    return str(p)


def boq_item_rows(boq: BOQ) -> list[dict[str, object]]:
    """Flat rows for pre-existing spreadsheet tooling."""
    return [
        {
            "section": i.section, "code": i.code, "description": i.description,
            "unit": i.unit, "quantity": i.quantity, "waste_pct": i.waste_pct,
            "quantity_with_waste": i.quantity_with_waste if i.is_priced else None,
            "rate": i.rate, "amount": i.amount, "status": i.status,
        }
        for i in boq.items
    ]


def item_is_priced(item: BOQItem) -> bool:
    return item.is_priced
