"""Specification schema and geometry-helper tests (no FreeCAD needed)."""
from __future__ import annotations

import math
from itertools import pairwise

import pytest
from pydantic import ValidationError

from archpilot.spec.sample import sample_single_storey, sample_two_storey
from archpilot.spec.schema import (
    MAX_NOTE_LEN,
    MAX_NOTES,
    MAX_OPENINGS_PER_WALL,
    MAX_OUTLINE_POINTS,
    MAX_STOREYS,
    MAX_WALLS_PER_STOREY,
    SPEC_VERSION,
    BuildingSpec,
    Opening,
    Slab,
    Storey,
    Wall,
)
from archpilot.spec.shape import layout, rectangle_slab, rectangle_walls


def test_slab_area_uses_shoelace_on_a_triangle() -> None:
    slab = Slab(id="s", outline=[(0, 0), (4, 0), (0, 3)], thickness_m=0.1)
    assert slab.area_m2 == pytest.approx(6.0)


def test_slab_area_rectangle_and_volume() -> None:
    slab = rectangle_slab("s", "ground", 8.0, 6.0, 0.15)
    assert slab.area_m2 == pytest.approx(48.0)
    assert slab.perimeter_m == pytest.approx(28.0)
    assert slab.volume_m3 == pytest.approx(7.2)


def test_slab_area_is_orientation_independent() -> None:
    ccw = Slab(id="a", outline=[(0, 0), (4, 0), (4, 3), (0, 3)], thickness_m=0.1)
    cw = Slab(id="b", outline=[(0, 3), (4, 3), (4, 0), (0, 0)], thickness_m=0.1)
    assert ccw.area_m2 == pytest.approx(cw.area_m2) == pytest.approx(12.0)


def test_slab_rejects_degenerate_outline() -> None:
    with pytest.raises(ValidationError):
        Slab(id="s", outline=[(0, 0), (1, 1), (2, 2)], thickness_m=0.1)


def test_wall_length_is_measured_not_supplied() -> None:
    wall = Wall(id="w", start=(0, 0), end=(3, 4), thickness_m=0.2, height_m=3.0)
    assert wall.length_m == pytest.approx(5.0)


def test_wall_net_volume_deducts_openings_once() -> None:
    wall = Wall(id="w", start=(0, 0), end=(8, 0), thickness_m=0.25, height_m=3.0,
                openings=[Opening(id="o1", kind="door", width_m=2.0, height_m=2.0)])
    gross = 8 * 3 * 0.25
    assert wall.gross_volume_m3 == pytest.approx(gross)
    assert wall.net_volume_m3 == pytest.approx(gross - 2.0 * 2.0 * 0.25)


def test_opening_running_past_wall_end_is_rejected() -> None:
    with pytest.raises(ValidationError) as info:
        Wall(id="w", start=(0, 0), end=(2, 0), thickness_m=0.2, height_m=3.0,
             openings=[Opening(id="o", kind="window", width_m=1.5, height_m=1.0,
                               offset_m=1.0)])
    assert "past the wall end" in str(info.value)


def test_opening_taller_than_wall_is_rejected() -> None:
    with pytest.raises(ValidationError) as info:
        Wall(id="w", start=(0, 0), end=(6, 0), thickness_m=0.2, height_m=2.0,
             openings=[Opening(id="o", kind="door", width_m=1.0, height_m=2.5)])
    assert "taller than the wall" in str(info.value)


def test_overlapping_openings_are_rejected() -> None:
    with pytest.raises(ValidationError) as info:
        Wall(id="w", start=(0, 0), end=(6, 0), thickness_m=0.2, height_m=3.0,
             openings=[
                 Opening(id="a", kind="window", width_m=1.5, height_m=1.2, offset_m=1.0),
                 Opening(id="b", kind="window", width_m=1.5, height_m=1.2, offset_m=2.0),
             ])
    assert "overlap" in str(info.value)


def test_zero_length_wall_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Wall(id="w", start=(1, 1), end=(1, 1), thickness_m=0.2, height_m=3.0)


def test_spec_version_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BuildingSpec(name="x", spec_version=SPEC_VERSION + 1,
                     storeys=[Storey(id="L0", level=0, floor_height_m=3.0)])


def test_duplicate_ids_across_storeys_are_rejected() -> None:
    with pytest.raises(ValidationError) as info:
        BuildingSpec(name="x", storeys=[
            Storey(id="L0", level=0, floor_height_m=3.0,
                   walls=[Wall(id="dup", start=(0, 0), end=(4, 0), thickness_m=0.2,
                               height_m=3.0)]),
            Storey(id="L1", level=1, floor_height_m=3.0,
                   walls=[Wall(id="dup", start=(0, 0), end=(4, 0), thickness_m=0.2,
                               height_m=3.0)]),
        ])
    assert "duplicate" in str(info.value)


def test_duplicate_storey_levels_are_rejected() -> None:
    with pytest.raises(ValidationError) as info:
        BuildingSpec(name="x", storeys=[
            Storey(id="A", level=0, floor_height_m=3.0),
            Storey(id="B", level=0, floor_height_m=3.0),
        ])
    assert "duplicate storey level" in str(info.value)


def test_storey_elevations_account_for_slab_thickness() -> None:
    """A storey occupies its slab *and* its clear height, so elevations must include slabs."""
    spec = sample_two_storey()
    assert spec.storey_elevations() == {"L0": 0.0, "L1": 3.15}
    assert spec.total_height_m() == pytest.approx(6.0)          # clear heights only
    assert spec.overall_height_m() == pytest.approx(6.3)        # including both slabs


def test_a_storey_without_a_slab_adds_no_height() -> None:
    spec = BuildingSpec(name="x", storeys=[
        Storey(id="L0", level=0, floor_height_m=3.0),
        Storey(id="L1", level=1, floor_height_m=3.0,
               slabs=[rectangle_slab("L1-slab-floor", "floor", 5.0, 4.0, 0.2)]),
    ])
    assert spec.storey_elevations() == {"L0": 0.0, "L1": 3.0}
    assert spec.overall_height_m() == pytest.approx(6.2)


def test_out_of_order_levels_still_stack_correctly() -> None:
    spec = BuildingSpec(name="x", storeys=[
        Storey(id="top", level=2, floor_height_m=2.5),
        Storey(id="ground", level=0, floor_height_m=3.0),
        Storey(id="mid", level=1, floor_height_m=2.8),
    ])
    assert spec.storey_elevations() == {"ground": 0.0, "mid": 3.0, "top": 5.8}


# ---------------------------------------------------------------------------
# Opening layout
# ---------------------------------------------------------------------------
def test_layout_centres_openings_and_keeps_clear_of_the_ends() -> None:
    offsets = layout(8.0, [1.5, 1.5], gap=0.35)
    assert offsets == pytest.approx([2.325, 4.175])
    assert offsets[0] >= 0.30
    assert offsets[-1] + 1.5 <= 8.0 - 0.30


def test_layout_drops_openings_that_cannot_fit() -> None:
    assert layout(1.0, [1.5, 1.5]) == []
    assert len(layout(4.0, [1.5] * 6)) == 2


def test_layout_never_overlaps() -> None:
    widths = [1.0, 1.5, 1.5]
    offsets = layout(9.0, widths, gap=0.35)
    pairs = sorted(zip(offsets, widths, strict=True))
    for (a_off, a_w), (b_off, _b_w) in pairwise(pairs):
        assert a_off + a_w + 0.35 <= b_off + 1e-9


def test_layout_returns_nothing_for_impossible_wall() -> None:
    assert layout(0.0, [1.0]) == []
    assert layout(-3.0, [1.0]) == []
    assert layout(5.0, [0.0]) == []


# ---------------------------------------------------------------------------
# Sample projects
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("factory", [sample_single_storey, sample_two_storey])
def test_sample_projects_are_valid_and_self_consistent(factory) -> None:
    spec = factory()
    assert spec.spec_version == SPEC_VERSION
    assert spec.wall_count == len(spec.storeys) * 4
    assert spec.slab_count == len(spec.storeys)
    for st in spec.storeys:
        for wall in st.walls:
            for op in wall.openings:
                assert op.offset_m + op.width_m <= wall.length_m + 1e-9
                assert op.top_m <= wall.height_m + 1e-9


def test_sample_two_storey_area_matches_footprint_times_levels() -> None:
    spec = sample_two_storey()
    assert spec.gross_floor_area_m2() == pytest.approx(8.0 * 6.0 * 2)


def test_sample_runs_of_openings_do_not_overlap() -> None:
    for factory in (sample_single_storey, sample_two_storey):
        spec = factory()
        for st in spec.storeys:
            for wall in st.walls:
                opens = sorted(wall.openings, key=lambda o: o.offset_m)
                for a, b in pairwise(opens):
                    assert a.offset_m + a.width_m <= b.offset_m + 1e-9


def test_unresolved_assumptions_lists_open_questions() -> None:
    spec = sample_single_storey()
    combined = spec.unresolved_assumptions()
    assert any("Unresolved:" in item for item in combined)
    assert len(combined) == len(spec.assumptions) + len(spec.open_questions)


def test_rectangle_walls_geometry_is_closed() -> None:
    walls = rectangle_walls("L0", 8.0, 6.0, 0.24, 3.0, doors_south=1, windows_per_wall=1)
    assert len(walls) == 4
    lengths = sorted(round(w.length_m, 6) for w in walls)
    assert lengths == [6.0, 6.0, 8.0, 8.0]
    # the four walls form one closed loop
    ends = {w.start: w.end for w in walls}
    start = walls[0].start
    cursor = start
    for _ in range(len(walls)):
        cursor = ends[cursor]
    assert cursor == start


def test_wall_angle_is_used_for_placement() -> None:
    wall = Wall(id="w", start=(0, 0), end=(0, 5), thickness_m=0.2, height_m=3.0)
    assert wall.angle_deg == pytest.approx(90.0)
    assert math.isclose(wall.length_m, 5.0)


# ---------------------------------------------------------------------------
# A design cannot be made arbitrarily large or full of arbitrary text
# ---------------------------------------------------------------------------
def _minimal_storey(level: int) -> dict:
    return {"id": f"L{level}", "level": level, "floor_height_m": 3.0,
            "walls": [], "slabs": []}


def test_a_reasonable_design_is_still_accepted_at_the_limits() -> None:
    # One below each bound must still validate, so the limits are not accidentally too tight.
    spec = BuildingSpec(name="Ok", storeys=[_minimal_storey(i) for i in range(3)])
    assert len(spec.storeys) == 3


def test_a_design_with_too_many_storeys_is_refused() -> None:
    with pytest.raises(ValidationError):
        BuildingSpec(name="Too tall",
                     storeys=[_minimal_storey(i) for i in range(MAX_STOREYS + 1)])


def test_a_storey_with_too_many_walls_is_refused() -> None:
    walls = [Wall(id=f"w{i}", start=(0, 0), end=(1, 0), thickness_m=0.2, height_m=3.0)
             for i in range(MAX_WALLS_PER_STOREY + 1)]
    with pytest.raises(ValidationError):
        Storey(id="L0", level=0, floor_height_m=3.0, walls=walls)


def test_a_wall_with_too_many_openings_is_refused() -> None:
    openings = [Opening(id=f"o{i}", kind="window", width_m=0.5, height_m=0.5,
                        offset_m=float(i))
                for i in range(MAX_OPENINGS_PER_WALL + 1)]
    with pytest.raises(ValidationError):
        Wall(id="w", start=(0, 0), end=(100, 0), thickness_m=0.2, height_m=3.0,
             openings=openings)


def test_an_overlong_assumption_is_refused() -> None:
    with pytest.raises(ValidationError):
        BuildingSpec(name="N", storeys=[_minimal_storey(0)],
                     assumptions=["x" * (MAX_NOTE_LEN + 1)])


def test_too_many_assumptions_are_refused() -> None:
    with pytest.raises(ValidationError):
        BuildingSpec(name="N", storeys=[_minimal_storey(0)],
                     assumptions=["note"] * (MAX_NOTES + 1))


def test_a_slab_outline_with_absurdly_many_points_is_refused() -> None:
    points = [(math.cos(i), math.sin(i)) for i in range(MAX_OUTLINE_POINTS + 1)]
    with pytest.raises(ValidationError):
        Slab(id="s", outline=points, thickness_m=0.15)
