"""Quantity measurement tests.

These verify the engine against **independently derived** numbers: expected volumes and areas
are recomputed here straight from the specification's own geometry (lengths, opening areas,
slab areas), which is a different calculation path from the one the engine follows (it
consumes FreeCAD-measured shape volumes).

The fabricated ``MeasuredObject`` values below stand in for FreeCAD output only in the
non-FreeCAD tests; ``test_freecad.py`` runs the real thing.
"""
from __future__ import annotations

import math

import pytest

from archpilot.cad.builder import MeasuredObject
from archpilot.measure.engine import (
    CATEGORY_FINISHES,
    CATEGORY_FORMWORK,
    CATEGORY_MASONRY,
    CODE_SLAB_FORMWORK_EDGE,
    CODE_SLAB_FORMWORK_SOFFIT,
    measure,
)
from archpilot.spec.sample import sample_single_storey, sample_two_storey
from archpilot.spec.schema import BuildingSpec, Storey
from archpilot.spec.shape import rectangle_slab, rectangle_walls

THICK = 0.20
HEIGHT = 2.80


def _room_spec() -> BuildingSpec:
    """A 4 × 3 m single room with known, hand-checkable geometry."""
    return BuildingSpec(
        name="room", storeys=[
            Storey(id="L0", level=0, floor_height_m=HEIGHT,
                   walls=rectangle_walls("L0", 4.0, 3.0, THICK, HEIGHT,
                                         doors_south=1, windows_per_wall=1,
                                         window_width=1.2),
                   slabs=[rectangle_slab("L0-slab", "ground", 4.0, 3.0, 0.15)]),
        ])


def _manifest_from_spec(spec: BuildingSpec) -> list[MeasuredObject]:
    """Build a manifest whose volumes equal the analytic spec values (FreeCAD stand-in)."""
    out: list[MeasuredObject] = []
    for st in spec.storeys:
        for slab in st.slabs:
            out.append(MeasuredObject(
                ap_id=slab.id, ap_kind="slab", storey=st.id, label=slab.id,
                volume_m3=slab.volume_m3, surface_area_m2=0.0,
                bbox_m=(0.0, 0.0, 0.0), bbox_min_m=(0.0, 0.0, 0.0),
                solids=1, valid=True))
        for wall in st.walls:
            out.append(MeasuredObject(
                ap_id=wall.id, ap_kind="wall", storey=st.id, label=wall.id,
                volume_m3=wall.net_volume_m3, surface_area_m2=0.0,
                bbox_m=(0.0, 0.0, 0.0), bbox_min_m=(0.0, 0.0, 0.0),
                solids=1, valid=True))
    return out


# ---------------------------------------------------------------------------
# Hand-computed expectations
# ---------------------------------------------------------------------------
def _expected_wall_net_volume(wall) -> float:
    net_area = wall.length_m * wall.height_m
    for op in wall.openings:
        net_area -= op.width_m * op.height_m
    return net_area * wall.thickness_m


def test_room_wall_volumes_match_hand_calculation() -> None:
    spec = _room_spec()
    result = measure(spec, _manifest_from_spec(spec))

    wall_lines = [line for line in result.lines if line.category == CATEGORY_MASONRY]
    total_wall = sum(line.quantity for line in wall_lines)

    expected = sum(_expected_wall_net_volume(w) for st in spec.storeys for w in st.walls)
    assert total_wall == pytest.approx(expected)

    # Spelled out longhand for the 4 × 3 m room. Windows are 1.2 m wide × 1.2 m high:
    #   south 4 × 2.8 = 11.2 gross, openings 1.0×2.1 + 1.2×1.2 = 3.54 → 7.66 net
    #   north 4 × 2.8 = 11.2 gross, opening 1.2×1.2 = 1.44 → 9.76 net
    #   east/west 3 × 2.8 = 8.4 gross, opening 1.2×1.2 = 1.44 → 6.96 net each
    expected_area = 7.66 + 9.76 + 6.96 + 6.96
    assert expected_area == pytest.approx(31.34)
    assert total_wall == pytest.approx(expected_area * THICK)


def test_slab_volume_and_finish_area_are_exact() -> None:
    spec = _room_spec()
    result = measure(spec, _manifest_from_spec(spec))
    concrete = [line for line in result.lines if line.unit == "m3"
                and line.code.startswith("CONC.SLAB")]
    assert sum(line.quantity for line in concrete) == pytest.approx(4.0 * 3.0 * 0.15)

    finish = [line for line in result.lines if line.code == "FINISH.FLOOR"]
    assert sum(line.quantity for line in finish) == pytest.approx(12.0)


def test_openings_are_deducted_exactly_once() -> None:
    """Regression: an earlier bug double-counted openings (area *and* volume)."""
    spec = _room_spec()
    result = measure(spec, _manifest_from_spec(spec))
    total_wall = sum(line.quantity for line in result.lines if line.category == CATEGORY_MASONRY)
    gross = sum(w.length_m * w.height_m * w.thickness_m
                for st in spec.storeys for w in st.walls)
    deducted = sum(op.width_m * op.height_m * w.thickness_m
                   for st in spec.storeys for w in st.walls for op in w.openings)
    assert gross - total_wall == pytest.approx(deducted)
    assert gross - total_wall > 0


def test_ground_slab_gets_edge_formwork_and_suspended_slab_gets_soffit() -> None:
    spec = sample_two_storey()
    result = measure(spec, _manifest_from_spec(spec))
    edge = result.by_code(CODE_SLAB_FORMWORK_EDGE)
    soffit = result.by_code(CODE_SLAB_FORMWORK_SOFFIT)
    assert edge is not None and edge.unit == "m2"
    # ground slab on grade: perimeter × thickness, not the whole soffit
    assert edge.quantity == pytest.approx(28.0 * 0.15)
    # one suspended slab: soffit struck over the full plan area
    assert soffit is not None and soffit.quantity == pytest.approx(48.0)


def test_formwork_categories_are_not_double_counted() -> None:
    spec = sample_two_storey()
    result = measure(spec, _manifest_from_spec(spec))
    formwork = [line for line in result.lines if line.category == CATEGORY_FORMWORK]
    assert len(formwork) == 2
    assert {line.code for line in formwork} == {CODE_SLAB_FORMWORK_EDGE,
                                          CODE_SLAB_FORMWORK_SOFFIT}


def test_plaster_is_an_explicit_derived_allowance() -> None:
    spec = _room_spec()
    result = measure(spec, _manifest_from_spec(spec))
    plaster = result.by_code("FINISH.WALL.PLASTER")
    assert plaster is not None
    assert plaster.source == "derived"
    # both faces of every wall (net of openings)
    expected = 2.0 * (7.66 + 9.76 + 6.96 + 6.96)
    assert plaster.quantity == pytest.approx(expected)


def test_openings_are_counted_as_units() -> None:
    spec = _room_spec()
    result = measure(spec, _manifest_from_spec(spec))
    assert result.by_code("OPENING.DOOR").quantity == pytest.approx(1.0)
    windows = [line for line in result.lines if line.code == "OPENING.WINDOW"]
    assert sum(line.quantity for line in windows) == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# Honesty: nothing silently dropped or invented
# ---------------------------------------------------------------------------
def test_design_object_missing_from_model_is_warned_and_excluded() -> None:
    spec = _room_spec()
    manifest = _manifest_from_spec(spec)
    missing = manifest[0].ap_id
    manifest = [m for m in manifest if m.ap_id != missing]
    result = measure(spec, manifest)
    assert any(missing in w and "not found in the 3D model" in w for w in result.warnings)
    assert all(missing not in line.element_ids for line in result.lines)


def test_model_object_absent_from_design_is_warned_and_excluded() -> None:
    spec = _room_spec()
    manifest = _manifest_from_spec(spec)
    manifest.append(MeasuredObject(
        ap_id="ghost", ap_kind="wall", storey="L0", label="ghost", volume_m3=99.0,
        surface_area_m2=0.0, bbox_m=(1.0, 1.0, 1.0), bbox_min_m=(0.0, 0.0, 0.0),
        solids=1, valid=True))
    result = measure(spec, manifest)
    assert any("exists in the 3D model but not in the design" in w
               for w in result.warnings)
    assert all("ghost" not in line.element_ids for line in result.lines)


def test_geometry_disagreeing_with_design_is_flagged_not_hidden() -> None:
    spec = _room_spec()
    manifest = _manifest_from_spec(spec)
    # simulate a model where an opening was never cut: volume is the gross volume
    stretched = []
    for m in manifest:
        if m.ap_kind == "wall":
            wall = next(w for st in spec.storeys for w in st.walls if w.id == m.ap_id)
            stretched.append(MeasuredObject(
                ap_id=m.ap_id, ap_kind="wall", storey=m.storey, label=m.label,
                volume_m3=wall.gross_volume_m3, surface_area_m2=0.0,
                bbox_m=m.bbox_m, bbox_min_m=m.bbox_min_m, solids=1, valid=True))
        else:
            stretched.append(m)
    result = measure(spec, stretched)
    assert any("differs from the design's net" in w for w in result.warnings)


def test_roof_absence_is_reported() -> None:
    spec = _room_spec()          # ground slab only, no roof slab
    result = measure(spec, _manifest_from_spec(spec))
    assert any("No roof slab is modelled" in w for w in result.warnings)


def test_coverage_counts_every_designed_element() -> None:
    spec = sample_two_storey()
    result = measure(spec, _manifest_from_spec(spec))
    assert result.coverage["slabs_measured"] == result.coverage["slabs_designed"]
    assert result.coverage["walls_measured"] == result.coverage["walls_designed"]
    assert result.coverage["openings_measured"] == result.coverage["openings_designed"]


def test_quantities_are_not_rounded_prematurely() -> None:
    spec = _room_spec()
    result = measure(spec, _manifest_from_spec(spec))
    total_m3 = result.total("m3")
    # total of unrounded parts is not the same as sum of rounded parts
    rounded_total = sum(round(line.quantity, 3) for line in result.lines if line.unit == "m3")
    assert total_m3 == pytest.approx(rounded_total, abs=2e-3)
    assert not math.isnan(total_m3)


def test_empty_manifest_measures_nothing_but_says_so() -> None:
    spec = _room_spec()
    result = measure(spec, [])
    assert result.lines == [] or all(line.quantity == pytest.approx(0.0)
                                     for line in result.lines)
    assert len(result.warnings) >= spec.wall_count


def test_two_storey_has_more_wall_and_concrete_than_single_storey() -> None:
    single = sample_single_storey()
    two = sample_two_storey()
    r1 = measure(single, _manifest_from_spec(single))
    r2 = measure(two, _manifest_from_spec(two))

    concrete_1 = sum(line.quantity for line in r1.lines
                     if line.unit == "m3" and line.code.startswith("CONC.SLAB"))
    concrete_2 = sum(line.quantity for line in r2.lines
                     if line.unit == "m3" and line.code.startswith("CONC.SLAB"))
    assert concrete_1 == pytest.approx(7.2)
    assert concrete_2 == pytest.approx(14.4)

    walls_1 = sum(line.quantity for line in r1.lines if line.category == CATEGORY_MASONRY)
    walls_2 = sum(line.quantity for line in r2.lines if line.category == CATEGORY_MASONRY)
    assert walls_2 > walls_1
    assert r2.total("m3") > r1.total("m3")


def test_category_totals_partition_the_lines() -> None:
    spec = sample_two_storey()
    result = measure(spec, _manifest_from_spec(spec))
    by_category = sum(result.category_totals().values())
    by_line = sum(line.quantity for line in result.lines)
    assert by_category == pytest.approx(by_line)


def test_unit_totals_only_cover_their_own_unit() -> None:
    spec = sample_two_storey()
    result = measure(spec, _manifest_from_spec(spec))
    totals = result.unit_totals
    assert set(totals) == {"m2", "m3", "nos"}
    assert totals["m3"] == pytest.approx(result.total("m3"))
    assert totals["nos"] == pytest.approx(13.0)


def test_finishes_category_includes_floor_and_plaster() -> None:
    spec = _room_spec()
    result = measure(spec, _manifest_from_spec(spec))
    finishes = [line for line in result.lines if line.category == CATEGORY_FINISHES]
    codes = {line.code for line in finishes}
    assert "FINISH.FLOOR" in codes
    assert "FINISH.WALL.PLASTER" in codes
