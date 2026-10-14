"""Deterministic sample projects, so the full workflow works with no AI and no typing.

Both samples share :mod:`archpilot.spec.shape` with the AI operations, which means editing a
sample and asking the AI to produce the same building converge on identical geometry.
"""
from __future__ import annotations

from archpilot.spec.schema import BuildingSpec, SiteSpec, Storey
from archpilot.spec.shape import rectangle_slab, rectangle_walls

# 8 m x 6 m footprint, walls on the grid, 240 mm thick, 3 m floor-to-floor.
WIDTH, DEPTH = 8.0, 6.0
THICKNESS, FLOOR_HEIGHT, SLAB_THICKNESS = 0.24, 3.0, 0.15
FOOTNOTE_BASE = (
    "Foundation and reinforcement are excluded — no design inputs provided.",
)


def sample_single_storey() -> BuildingSpec:
    return BuildingSpec(
        name="Sample — Single Storey 8×6 m",
        units="m",
        site=SiteSpec(width_m=15.0, depth_m=12.0, note="Illustrative plot only."),
        storeys=[
            Storey(id="L0", level=0, floor_height_m=FLOOR_HEIGHT,
                   walls=rectangle_walls("L0", WIDTH, DEPTH, THICKNESS, FLOOR_HEIGHT,
                                         doors_south=1, windows_per_wall=1),
                   slabs=[rectangle_slab("L0-slab-ground", "ground", WIDTH, DEPTH,
                                         SLAB_THICKNESS)]),
        ],
        roof_type="flat",
        design_region="BD",
        assumptions=[
            "Walls are modelled as single-layer solid boxes on the plan grid.",
            f"Wall thickness assumed uniform at {THICKNESS * 1000:.0f} mm.",
            "Single storey — no upper-floor structural system to select.",
            FOOTNOTE_BASE[0],
        ],
        open_questions=[
            "Soil bearing capacity and foundation type not provided.",
            "Ceiling height and service zone allowance not confirmed.",
        ],
    )


def sample_two_storey() -> BuildingSpec:
    return BuildingSpec(
        name="Sample — Two Storey 8×6 m",
        units="m",
        site=SiteSpec(width_m=15.0, depth_m=12.0, note="Illustrative plot only."),
        storeys=[
            Storey(id="L0", level=0, floor_height_m=FLOOR_HEIGHT,
                   walls=rectangle_walls("L0", WIDTH, DEPTH, THICKNESS, FLOOR_HEIGHT,
                                         doors_south=1, windows_per_wall=1),
                   slabs=[rectangle_slab("L0-slab-ground", "ground", WIDTH, DEPTH,
                                         SLAB_THICKNESS)]),
            Storey(id="L1", level=1, floor_height_m=FLOOR_HEIGHT,
                   walls=rectangle_walls("L1", WIDTH, DEPTH, THICKNESS, FLOOR_HEIGHT,
                                         doors_south=0, windows_per_wall=2),
                   slabs=[rectangle_slab("L1-slab-floor", "floor", WIDTH, DEPTH,
                                         SLAB_THICKNESS)]),
        ],
        roof_type="flat",
        design_region="BD",
        assumptions=[
            "Second storey repeats the ground footprint; no setback modelled.",
            f"Intermediate floor slab assumed {SLAB_THICKNESS * 1000:.0f} mm reinforced concrete.",
            FOOTNOTE_BASE[0],
        ],
        open_questions=[
            "Structural system for the first floor not selected.",
            "Soil bearing capacity and foundation type not provided.",
        ],
        warnings=[
            "Adding a storey changes structural demand — structural checks and "
            "professional review may need to be repeated.",
        ],
    )


SAMPLES = {
    "single": sample_single_storey,
    "two-storey": sample_two_storey,
}


def sample_spec(kind: str = "two-storey") -> BuildingSpec:
    return SAMPLES.get(kind, sample_two_storey)()
