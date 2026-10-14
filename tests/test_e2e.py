"""End-to-end: idea -> real FreeCAD model -> quantities -> priced BOQ -> saved project.

This is the only test that exercises the whole product the way a user does, and the only one
that can catch a break *between* layers (a change in how the manifest is read that the
measurement engine silently tolerates, a catalogue that loses its region on save, and so on).

It runs against the real FreeCAD binary and is skipped, never faked, when FreeCAD is absent.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from archpilot.ai import ops
from archpilot.branding import PRELIMINARY_STAMP
from archpilot.cad.builder import build_model, verify_against_spec
from archpilot.cad.reader import compare, sync_model
from archpilot.cost.boq import STATUS_NO_RATE, generate_boq
from archpilot.cost.catalogue import blank_catalogue, default_catalogue, from_payload, to_payload
from archpilot.cost.reports import (
    boq_to_csv,
    boq_to_pdf_bytes,
    boq_to_xlsx_bytes,
    quantities_to_csv,
)
from archpilot.measure.engine import measure
from archpilot.spec.sample import sample_single_storey, sample_two_storey
from archpilot.spec.schema import BuildingSpec
from archpilot.storage.db import KIND_RATES, KIND_SPEC, Database

pytestmark = pytest.mark.e2e


def _set_storeys(spec: BuildingSpec, storeys: int) -> BuildingSpec:
    """Add storeys the way the AI does, keeping the footprint."""
    result = ops.apply_operations(spec, [{"name": "set_storeys", "storeys": storeys}])
    assert result.applied, result.rejected
    return result.spec


@pytest.fixture()
def db(tmp_path: Path):
    database = Database(tmp_path / "projects.db")
    yield database
    database.close()


def _pipeline(spec: BuildingSpec, install, workdir: Path, catalogue=None):
    """Build -> verify -> measure -> price, the way the app does it."""
    built = build_model(spec, install, workdir / "model")
    warnings = verify_against_spec(built, spec)
    measurement = measure(spec, built.manifest)
    rates = catalogue or default_catalogue(spec.design_region or "BD")
    boq = generate_boq(measurement, rates, title=spec.name,
                       assumptions=spec.unresolved_assumptions())
    return built, warnings, measurement, boq


# ---------------------------------------------------------------------------
# The whole pipeline
# ---------------------------------------------------------------------------
def test_idea_to_priced_bill_of_quantities(freecad_install, tmp_path: Path) -> None:
    spec = sample_two_storey()
    built, warnings, measurement, boq = _pipeline(spec, freecad_install, tmp_path)

    # 1. The model really was built and matches the design.
    assert built.fcstd_path.is_file()
    assert warnings == []
    assert len(built.results) == len({obj.id for _k, _s, obj in spec.objects()})

    # 2. Quantities exist, are positive, and are traceable to model elements.
    assert measurement.lines
    concrete = measurement.by_code("CONC.SLAB.FLOOR")
    assert concrete is not None and concrete.quantity > 0
    assert concrete.element_ids

    # 3. Every quantity line either got a rate or is visibly unpriced.
    assert boq.items
    for item in boq.items:
        if item.is_priced:
            assert item.amount is not None and item.amount > 0
        else:
            assert item.status in (STATUS_NO_RATE, "rate exists but in a different unit")
            assert item.amount is None

    # 4. A seeded catalogue prices the structural work, so the total is real money.
    assert boq.priced_items
    assert boq.grand_total > 0
    assert boq.grand_total == pytest.approx(
        boq.measured_subtotal + boq.contingency + boq.preliminaries + boq.tax)


def test_the_measured_total_equals_the_design_total(freecad_install, tmp_path: Path) -> None:
    """The number that must never drift: concrete volume in the model vs in the design."""
    spec = sample_two_storey()
    built, _w, measurement, _b = _pipeline(spec, freecad_install, tmp_path)

    design_total = sum(obj.volume_m3 if kind == "slab" else obj.net_volume_m3
                       for kind, _s, obj in spec.objects())
    assert built.total_volume_m3 == pytest.approx(design_total, rel=0.01)
    assert measurement.total("m3") == pytest.approx(design_total, rel=0.01)


def test_an_ai_edit_flows_all_the_way_through(freecad_install, tmp_path: Path) -> None:
    """The AI path must reach the same quality of model as the sample path."""
    spec = sample_single_storey()
    edited = ops.rebuild_rectangle(spec, width_m=10.0, depth_m=7.0, storeys=2)

    built, warnings, _m, _b = _pipeline(edited, freecad_install, tmp_path)
    assert warnings == []
    assert built.by_id("L1-slab-floor") is not None
    assert built.by_id("L0-slab-ground").bbox_m[0] == pytest.approx(10.0)


def test_unpriced_items_never_become_free(freecad_install, tmp_path: Path) -> None:
    """The core honesty rule: no rate must never quietly mean zero cost."""
    spec = sample_single_storey()
    built = build_model(spec, freecad_install, tmp_path / "model")
    measurement = measure(spec, built.manifest)

    boq = generate_boq(measurement, blank_catalogue("BD"))
    assert boq.items
    assert boq.unpriced_items, "a blank catalogue must leave everything unpriced"
    assert boq.priced_items == []
    assert boq.measured_subtotal == 0.0
    assert boq.complete is False
    for item in boq.items:
        assert item.amount is None, f"{item.code} was silently priced at {item.amount}"

    # And that incompleteness is visible in every export, not just in memory.
    csv_text = boq_to_csv(boq).upper()
    assert "INCOMPLETE" in csv_text or "NOT PRICED" in csv_text or "UNPRICED" in csv_text


def test_partially_priced_bill_reports_only_what_it_can(freecad_install,
                                                        tmp_path: Path) -> None:
    spec = sample_single_storey()
    built = build_model(spec, freecad_install, tmp_path / "model")
    measurement = measure(spec, built.manifest)

    catalogue = blank_catalogue("BD")
    catalogue.add("CONC.SLAB.GROUND", "m3", 200.0)

    boq = generate_boq(measurement, catalogue)
    assert boq.complete is False
    ground = next(i for i in boq.items if i.code == "CONC.SLAB.GROUND")
    assert ground.is_priced and ground.amount and ground.amount > 0
    assert boq.measured_subtotal == pytest.approx(ground.amount)
    # the subtotal covers only the priced item, and the bill says so
    assert boq.unpriced_items


# ---------------------------------------------------------------------------
# Save, reopen, and prove nothing changed
# ---------------------------------------------------------------------------
def test_a_saved_project_reopens_to_the_same_numbers(freecad_install, tmp_path: Path,
                                                     db: Database) -> None:
    spec = sample_two_storey()
    built, _warnings, measurement, boq = _pipeline(spec, freecad_install, tmp_path)
    catalogue = default_catalogue("BD")

    project_id = db.create_project("Two-storey house", "BD", "BDT")
    revision = db.save_bundle(project_id, spec.model_dump(), to_payload(catalogue))

    # Reopen exactly as the app does on startup.
    spec_json, rates_json, reopened_revision = db.load_bundle(project_id)
    assert reopened_revision == revision
    reopened = BuildingSpec.model_validate(spec_json)
    reopened_rates = from_payload(rates_json)

    assert reopened == spec
    assert to_payload(reopened_rates) == to_payload(catalogue)

    # Quantities computed from the saved design are identical, element for element.
    again = measure(reopened, built.manifest)
    assert [(line.code, line.unit, round(line.quantity, 9)) for line in again.lines] == \
           [(line.code, line.unit, round(line.quantity, 9)) for line in measurement.lines]

    reboq = generate_boq(again, reopened_rates, title=reopened.name)
    assert reboq.grand_total == pytest.approx(boq.grand_total)
    assert reboq.complete == boq.complete


def test_the_stored_rates_are_the_ones_the_bill_used(freecad_install, tmp_path: Path,
                                                     db: Database) -> None:
    """A bill must never be reproduced against a different rate set than it was built with."""
    spec = sample_single_storey()
    built = build_model(spec, freecad_install, tmp_path / "model")
    measurement = measure(spec, built.manifest)

    cheap = blank_catalogue("BD")
    dear = blank_catalogue("BD")
    cheap.add("CONC.SLAB.GROUND", "m3", 100.0)
    dear.add("CONC.SLAB.GROUND", "m3", 900.0)

    pid = db.create_project("Rates")
    db.save_bundle(pid, spec.model_dump(), to_payload(cheap))
    db.save_bundle(pid, spec.model_dump(), to_payload(dear))

    stored = from_payload(db.load_bundle(pid)[1])
    assert stored.rate_for("CONC.SLAB.GROUND", "m3") == 900.0
    assert generate_boq(measurement, stored).grand_total > \
           generate_boq(measurement, cheap).grand_total


def test_revision_history_keeps_every_version_readable(freecad_install,
                                                       tmp_path: Path, db: Database) -> None:
    pid = db.create_project("Evolving")
    for storeys in (1, 2, 3):
        spec = _set_storeys(sample_single_storey(), storeys)
        db.save_bundle(pid, spec.model_dump(), to_payload(default_catalogue("BD")),
                       notes=f"{storeys} storey")

    specs = db.list_revisions(pid, KIND_SPEC)
    assert sorted(r.revision for r in specs) == [1, 2, 3]
    assert len(db.list_revisions(pid, KIND_RATES)) == 3
    assert len(db.read_revision(pid, KIND_SPEC, 1)["storeys"]) == 1
    assert len(db.read_revision(pid, KIND_SPEC, 3)["storeys"]) == 3


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def test_every_export_carries_its_warnings(freecad_install, tmp_path: Path) -> None:
    spec = sample_single_storey()
    _built, _w, measurement, boq = _pipeline(spec, freecad_install, tmp_path)

    text = boq_to_csv(boq)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows and any(PRELIMINARY_STAMP in cell for row in rows for cell in row), \
        "the CSV must carry the preliminary-estimate stamp"
    assert boq.currency in text or "Total" in text

    xlsx = boq_to_xlsx_bytes(boq)
    assert xlsx[:2] == b"PK" and len(xlsx) > 5_000      # a real xlsx zip archive

    pdf = boq_to_pdf_bytes(boq)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 1_000

    quantities = quantities_to_csv(measurement)
    assert "CONC.SLAB" in quantities


def test_exports_survive_an_unpriced_bill(freecad_install, tmp_path: Path) -> None:
    """Exports must not crash or print a fake zero total when nothing is priced."""
    spec = sample_single_storey()
    built = build_model(spec, freecad_install, tmp_path / "model")
    measurement = measure(spec, built.manifest)
    boq = generate_boq(measurement, blank_catalogue("BD"))

    assert boq_to_csv(boq)
    assert boq_to_xlsx_bytes(boq)[:2] == b"PK"
    assert boq_to_pdf_bytes(boq)[:5] == b"%PDF-"
    for item in boq.items:
        assert item.amount is None


# ---------------------------------------------------------------------------
# Sync after the fact, and reopening a model
# ---------------------------------------------------------------------------
def test_a_rebuilt_model_matches_its_saved_quantities(freecad_install,
                                                      tmp_path: Path) -> None:
    spec = sample_two_storey()
    built = build_model(spec, freecad_install, tmp_path / "model")
    first = measure(spec, built.manifest)

    # Reading the model back must give the same quantities as building it.
    synced = sync_model(freecad_install, built.fcstd_path, tmp_path / "model")
    assert compare(spec, synced) == []
    second = measure(spec, synced.manifest)

    assert [(line.code, round(line.quantity, 6)) for line in second.lines] == \
           [(line.code, round(line.quantity, 6)) for line in first.lines]


def test_adding_a_storey_through_ai_changes_the_cost(freecad_install,
                                                     tmp_path: Path) -> None:
    one = ops.rebuild_rectangle(sample_single_storey(), width_m=8.0, depth_m=6.0, storeys=1)
    two = ops.rebuild_rectangle(sample_single_storey(), width_m=8.0, depth_m=6.0, storeys=2)

    _b1, _w1, _m1, boq1 = _pipeline(one, freecad_install, tmp_path / "one")
    _b2, _w2, _m2, boq2 = _pipeline(two, freecad_install, tmp_path / "two")

    assert boq2.grand_total > boq1.grand_total
    assert boq2.grand_total > boq1.grand_total * 1.3      # a whole extra storey


# ---------------------------------------------------------------------------
# The limitations must be stated, not hidden
# ---------------------------------------------------------------------------
def test_the_bill_states_what_is_not_included(freecad_install, tmp_path: Path) -> None:
    spec = sample_single_storey()
    _b, _w, measurement, boq = _pipeline(spec, freecad_install, tmp_path)

    joined = " ".join(boq.warnings + boq.assumptions + measurement.warnings).lower()
    assert "roof" in joined, "the missing roof must be declared"
    assert "reinforcement" in joined or "rebar" in joined, \
        "reinforcement is never inferred and must be declared"
    # The stamp that says the numbers are not construction documents must reach the output.
    assert PRELIMINARY_STAMP in boq_to_csv(boq)
    assert "NOT FOR CONSTRUCTION" in PRELIMINARY_STAMP
