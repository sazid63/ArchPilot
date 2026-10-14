"""Pricing and reporting tests — mostly about *honesty* when data is missing."""
from __future__ import annotations

import csv
import io

import pytest

from archpilot.cost import reports
from archpilot.cost.boq import (
    STATUS_PRICED,
    STATUS_UNIT_MISMATCH,
    WastePolicy,
    generate_boq,
)
from archpilot.cost.catalogue import (
    ILLUSTRATIVE_NOTE,
    RateCatalogue,
    blank_catalogue,
    default_catalogue,
    export_rates_csv,
    from_payload,
    import_rates_csv,
    to_payload,
)
from archpilot.measure.engine import MeasurementResult, QuantityLine


def _measurement() -> MeasurementResult:
    return MeasurementResult(lines=[
        QuantityLine(code="CONC.SLAB.FLOOR", description="Suspended floor slab",
                     unit="m3", quantity=7.2, category="concrete", element_ids=("a",)),
        QuantityLine(code="CONC.SLAB.FLOOR", description="Suspended floor slab",
                     unit="m3", quantity=7.2, category="concrete", element_ids=("b",)),
        QuantityLine(code="FINISH.FLOOR", description="Floor finish", unit="m2",
                     quantity=96.0, category="finishes", element_ids=("a", "b")),
        QuantityLine(code="OPENING.DOOR", description="Doors", unit="nos", quantity=1.0,
                     category="openings"),
    ])


# ---------------------------------------------------------------------------
# Missing rates must never become zero cost
# ---------------------------------------------------------------------------
def test_unpriced_items_are_none_not_zero() -> None:
    boq = generate_boq(_measurement(), blank_catalogue("BD"))
    assert not boq.complete
    for item in boq.items:
        assert item.rate is None
        assert item.amount is None, "an unpriced item must not carry a 0.00 amount"
        assert not item.is_priced
    assert boq.grand_total == pytest.approx(0.0)
    assert len(boq.unpriced_items) == len(boq.items)


def test_unpriced_items_are_listed_in_warnings() -> None:
    boq = generate_boq(_measurement(), blank_catalogue("BD"))
    assert len(boq.warnings) == len(boq.unpriced_items)
    assert all("unpriced" in w for w in boq.warnings)


def test_totals_label_an_incomplete_bill_as_partial() -> None:
    cat = blank_catalogue("BD")
    cat.add("CONC.SLAB.FLOOR", "m3", 1000.0, "concrete", "test")
    boq = generate_boq(_measurement(), cat)
    assert not boq.complete
    # only the priced item is counted
    assert boq.measured_subtotal == pytest.approx(14.4 * 1.05 * 1000.0)
    assert len(boq.unpriced_items) == 2


def test_unit_mismatch_is_reported_and_not_priced() -> None:
    cat = RateCatalogue(region="X", name="X", currency="XXX")
    cat.add("FINISH.FLOOR", "m3", 500.0, "wrong unit", "test")   # catalogue says m3
    boq = generate_boq(_measurement(), cat)
    item = boq.by_code("FINISH.FLOOR")
    assert item is not None
    assert item.status == STATUS_UNIT_MISMATCH
    assert item.rate is None
    assert item.amount is None
    notes = " ".join(item.notes)
    assert "is per m3" in notes and "per m2" in notes


def test_unknown_code_is_unpriced_not_free() -> None:
    boq = generate_boq(_measurement(), default_catalogue("BD"))
    item = boq.by_code("OPENING.DOOR")
    assert item is not None and item.status == STATUS_PRICED
    assert item.amount is not None and item.amount > 0


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------
def test_lines_with_the_same_code_are_aggregated() -> None:
    boq = generate_boq(_measurement(), default_catalogue("BD"))
    item = boq.by_code("CONC.SLAB.FLOOR")
    assert item is not None
    assert item.quantity == pytest.approx(14.4)
    assert item.element_ids == ("a", "b")
    assert "2 elements combined" in " ".join(item.notes)


def test_waste_is_applied_only_to_priced_quantities() -> None:
    boq = generate_boq(_measurement(), default_catalogue("BD"))
    item = boq.by_code("CONC.SLAB.FLOOR")
    assert item is not None
    assert item.waste_pct == pytest.approx(5.0)
    assert item.quantity_with_waste == pytest.approx(14.4 * 1.05)
    assert item.amount == pytest.approx(14.4 * 1.05 * item.rate)

    unpriced = generate_boq(_measurement(), blank_catalogue("BD")).by_code(
        "CONC.SLAB.FLOOR")
    assert unpriced is not None
    assert unpriced.priced_quantity == pytest.approx(unpriced.quantity)


def test_waste_policy_is_configurable_per_category() -> None:
    policy = WastePolicy(by_category={"concrete": 0.0, "finishes": 20.0})
    boq = generate_boq(_measurement(), default_catalogue("BD"), waste=policy)
    assert boq.by_code("CONC.SLAB.FLOOR").waste_pct == pytest.approx(0.0)
    assert boq.by_code("FINISH.FLOOR").waste_pct == pytest.approx(20.0)


def test_totals_arithmetic() -> None:
    boq = generate_boq(_measurement(), default_catalogue("BD"),
                       contingency_pct=5.0, preliminaries_pct=3.0, tax_pct=10.0)
    subtotal = boq.measured_subtotal
    assert subtotal > 0
    assert boq.contingency == pytest.approx(subtotal * 0.05)
    assert boq.preliminaries == pytest.approx(subtotal * 0.03)
    assert boq.net_total == pytest.approx(subtotal * 1.08)
    assert boq.tax == pytest.approx(boq.net_total * 0.10)
    assert boq.grand_total == pytest.approx(boq.net_total * 1.10)


def test_sections_group_in_a_stable_order() -> None:
    boq = generate_boq(_measurement(), default_catalogue("BD"))
    sections = list(boq.sections())
    assert sections == ["A. Concrete", "D. Doors and windows", "E. Finishes"]
    # items themselves are ordered by section, not by code
    assert [i.section for i in boq.items] == ["A. Concrete", "D. Doors and windows",
                                             "E. Finishes"]


def test_currency_comes_from_the_catalogue() -> None:
    for region, currency in (("BD", "BDT"), ("US", "USD"), ("IN", "INR")):
        boq = generate_boq(_measurement(), default_catalogue(region))
        assert boq.currency == currency
        assert currency in boq.note or boq.note == ILLUSTRATIVE_NOTE


# ---------------------------------------------------------------------------
# Catalogue exchange
# ---------------------------------------------------------------------------
def test_csv_export_then_import_round_trips() -> None:
    source = default_catalogue("BD")
    text = export_rates_csv(source)
    target = blank_catalogue("BD")
    report = import_rates_csv(text, target)
    assert report.ok
    assert not report.skipped
    for code, item in source.items.items():
        assert target.rate_for(code, item.unit) == pytest.approx(item.rate)


def test_import_rejects_unit_mismatch_and_junk_rows() -> None:
    cat = default_catalogue("BD")
    csv_text = ("code,unit,rate\n"
                "CONC.SLAB.FLOOR,m2,999\n"      # wrong unit
                "CONC.SLAB.FLOOR,m3,not-a-number\n"
                "CONC.SLAB.FLOOR,m3,-5\n"
                ",,,\n"
                "CONC.SLAB.FLOOR,m3,\n")         # blank rate
    report = import_rates_csv(csv_text, cat)
    assert report.skipped
    assert not report.applied
    reasons = " ".join(reason for _code, reason in report.skipped)
    assert "does not match" in reasons
    assert "not a number" in reasons
    assert "negative" in reasons
    assert "blank rate" in reasons
    # original rate untouched
    assert cat.rate_for("CONC.SLAB.FLOOR", "m3") == pytest.approx(13_500)


def test_import_can_add_new_codes_but_not_without_a_unit() -> None:
    cat = blank_catalogue("BD")
    report = import_rates_csv("code,unit,rate\nNEW.ITEM,m2,42\nNO.UNIT,,7\n", cat)
    assert "NEW.ITEM" in report.added
    assert cat.rate_for("NEW.ITEM", "m2") == pytest.approx(42.0)
    assert report.skipped and "needs a unit" in report.skipped[0][1]


def test_import_requires_code_and_rate_columns() -> None:
    report = import_rates_csv("thing,value\nfoo,1\n", blank_catalogue("BD"))
    assert report.skipped and "code" in report.skipped[0][1]


def test_blank_catalogue_has_no_rates_but_knows_the_codes() -> None:
    cat = blank_catalogue("US")
    assert cat.currency == "USD"
    assert cat.item_for("CONC.SLAB.GROUND") is not None
    assert cat.rate_for("CONC.SLAB.GROUND", "m3") is None


def test_catalogue_payload_round_trip_keeps_none_rates() -> None:
    cat = blank_catalogue("BD")
    restored = from_payload(to_payload(cat))
    assert restored.rate_for("WALL.BRICK", "m3") is None
    for code, item in cat.items.items():
        assert (restored.item_for(code) is not None) == (item is not None)
    assert restored.currency == cat.currency


def test_catalogue_payload_round_trip_keeps_real_rates() -> None:
    cat = default_catalogue("IN")
    cat.add("CUSTOM.X", "m3", 123.5, "custom", "imported")
    restored = from_payload(to_payload(cat))
    assert restored.rate_for("CUSTOM.X", "m3") == pytest.approx(123.5)
    for code, item in cat.items.items():
        assert restored.rate_for(code, item.unit) == pytest.approx(item.rate)


def test_catalogue_payload_skips_unreadable_rows() -> None:
    restored = from_payload({"currency": "XXX", "items": [
        {"code": "A", "unit": "m3", "rate": "not a number"},
        {"code": "", "unit": "m3", "rate": 1},
        "junk",
        {"code": "B", "unit": "m2", "rate": 5},
    ]})
    assert restored.item_for("A") is None
    assert restored.rate_for("B", "m2") == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("region", ["BD", "US", "IN"])
def test_boq_exports_produce_real_files(region: str) -> None:
    boq = generate_boq(_measurement(), default_catalogue(region), title="Test")
    csv_text = reports.boq_to_csv(boq)
    assert "Bill of Quantities" in csv_text
    assert "PRELIMINARY" in csv_text
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert any("CONC.SLAB.FLOOR" in r for r in rows)

    xlsx = reports.boq_to_xlsx_bytes(boq)
    assert xlsx[:2] == b"PK" and len(xlsx) > 3000

    pdf = reports.boq_to_pdf_bytes(boq)
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000


def test_export_states_when_the_bill_is_incomplete() -> None:
    boq = generate_boq(_measurement(), blank_catalogue("BD"), title="Partial")
    assert "INCOMPLETE" in reports.boq_to_csv(boq)
    assert "PRELIMINARY" in reports.boq_to_csv(boq)


def test_quantities_export_includes_warnings() -> None:
    m = _measurement()
    m.warnings.append("something was not measured")
    text = reports.quantities_to_csv(m, title="Q")
    assert "something was not measured" in text
    assert text.startswith("Q")


def test_exported_totals_match_in_memory_totals() -> None:
    boq = generate_boq(_measurement(), default_catalogue("BD"), title="Match")
    text = reports.boq_to_csv(boq)
    rows = list(csv.reader(io.StringIO(text)))
    total_row = next(r for r in rows if len(r) > 2 and r[2].startswith("TOTAL"))
    assert float(total_row[8].replace(",", "")) == pytest.approx(boq.grand_total, abs=0.01)


# ---------------------------------------------------------------------------
# Untrusted text must never be interpreted, in a PDF or in a spreadsheet
# ---------------------------------------------------------------------------
def test_markup_in_untrusted_text_is_escaped_for_the_pdf() -> None:
    """reportlab's Paragraph parses markup and would fetch <img src>. Escaped text cannot."""
    assert reports._safe('<img src="http://example.invalid/x"/>') == \
        '&lt;img src="http://example.invalid/x"/&gt;'
    assert reports._safe("a & b") == "a &amp; b"


def test_a_pdf_with_hostile_assumption_text_is_still_produced() -> None:
    """An AI-written assumption is shown as literal text, never as live markup."""
    boq = generate_boq(_measurement(), default_catalogue("BD"), title="Safe",
                       assumptions=['<img src="http://example.invalid/beacon.png"/>'])
    pdf = reports.boq_to_pdf_bytes(boq)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_text_cells_that_look_like_formulas_are_defused() -> None:
    """A description starting with '=' must not run as a formula when the export is opened."""
    measurement = MeasurementResult(lines=[
        QuantityLine(code="EVIL.1", description="=1+1", unit="m3", quantity=1.0,
                     category="concrete", element_ids=("a",)),
    ])
    cat = blank_catalogue("BD")
    cat.add("EVIL.1", "m3", 10.0, "concrete", "test")
    cat.note = "=HYPERLINK(\"http://example.invalid\")"
    boq = generate_boq(measurement, cat, title="T")

    rows = list(csv.reader(io.StringIO(reports.boq_to_csv(boq))))
    cells = [c for row in rows for c in row if c]
    assert any(c == "'=1+1" for c in cells), "the description was not defused"
    assert any(c.startswith("'=HYPERLINK") for c in cells), "the note was not defused"
    # And nothing that survives as a cell begins with a formula lead.
    assert not any(c[0] in "=+@" for c in cells)


def test_the_defuse_helper_leaves_ordinary_text_alone() -> None:
    assert reports._defuse("Suspended floor slab") == "Suspended floor slab"
    assert reports._defuse("WALL.C240") == "WALL.C240"
    assert reports._defuse("=1+1") == "'=1+1"
    assert reports._defuse("+A1") == "'+A1"
    assert reports._defuse("@cmd") == "'@cmd"
