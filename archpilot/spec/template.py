"""Ready-made rectangular designs, for starting a project without typing or an AI.

This is the spec layer's own geometry builder. It shares :mod:`archpilot.spec.shape` with the
AI's ``design_rectangular_building`` operation, so a project started from a template and the
same project created by the assistant produce identical walls, slabs and openings.

Only rectangles are offered, deliberately: the spec can express any plan polygon, but a
template that quietly guessed an L-shape would be inventing design decisions. Anything beyond a
rectangle is drawn by hand in the design view, which says so out loud.
"""
from __future__ import annotations

from archpilot.spec.schema import BuildingSpec, SiteSpec, Storey
from archpilot.spec.shape import rectangle_slab, rectangle_walls

#: Wall/slab/opening defaults shared by every template.
DEFAULT_STOREY_HEIGHT_M = 3.0
DEFAULT_WALL_THICKNESS_M = 0.24
DEFAULT_SLAB_THICKNESS_M = 0.15

FOUNDATION_ASSUMPTION = "Foundation and reinforcement are excluded — no design inputs provided."
WALL_ASSUMPTION = "Walls are modelled as single-layer solid boxes on the plan grid."
ROOF_QUESTION = "Roof slab is not modelled, so roof concrete and finishes are excluded."
UPPER_STOREY_ASSUMPTION = "Upper storeys repeat the ground footprint; no setback modelled."
STRUCTURAL_WARNING = (
    "Adding storeys changes structural demand — structural checks and professional review "
    "may need to be repeated.")


def apply_structural_notes(spec: BuildingSpec, storeys: int) -> BuildingSpec:
    """Attach the assumption and warning that a multi-storey building always needs.

    Called from both the template builder and the AI operation that rebuilds a rectangle, so a
    building made from a template and one reached by asking 'make it two storeys' carry the
    same caveats. Idempotent: applying it twice does not duplicate the text.
    """
    if storeys <= 1:
        return spec
    assumptions = list(spec.assumptions)
    if UPPER_STOREY_ASSUMPTION not in assumptions:
        assumptions.append(UPPER_STOREY_ASSUMPTION)
    warnings = list(spec.warnings)
    if STRUCTURAL_WARNING not in warnings:
        warnings.append(STRUCTURAL_WARNING)
    return spec.model_copy(update={"assumptions": assumptions, "warnings": warnings})


def rectangular_project(
    name: str,
    width_m: float,
    depth_m: float,
    *,
    storeys: int = 1,
    storey_height_m: float = DEFAULT_STOREY_HEIGHT_M,
    wall_thickness_m: float = DEFAULT_WALL_THICKNESS_M,
    slab_thickness_m: float = DEFAULT_SLAB_THICKNESS_M,
    doors_south: int = 1,
    windows_per_wall: int = 1,
    region: str = "BD",
    units: str = "m",
) -> BuildingSpec:
    """Build a valid rectangular design with honest, template-specific assumptions.

    Raises ValueError for anything the schema would refuse, so the caller gets one clear
    message rather than a Pydantic error tree.
    """
    storeys = max(1, int(storeys))
    if width_m <= 0 or depth_m <= 0:
        raise ValueError("A building needs a width and a depth greater than zero.")

    levels: list[Storey] = []
    for level in range(storeys):
        lid = f"L{level}"
        slab_kind = "ground" if level == 0 else "floor"
        levels.append(Storey(
            id=lid, level=level, floor_height_m=storey_height_m,
            walls=rectangle_walls(lid, width_m, depth_m, wall_thickness_m, storey_height_m,
                                  doors_south=doors_south, windows_per_wall=windows_per_wall),
            slabs=[rectangle_slab(f"{lid}-slab-{slab_kind}", slab_kind,
                                  width_m, depth_m, slab_thickness_m)],
        ))

    assumptions = [
        WALL_ASSUMPTION,
        f"Wall thickness assumed uniform at {wall_thickness_m * 1000:.0f} mm.",
        f"Slab thickness assumed {slab_thickness_m * 1000:.0f} mm reinforced concrete.",
        FOUNDATION_ASSUMPTION,
    ]

    spec = BuildingSpec(
        name=name,
        units=units,                                    # type: ignore[arg-type]
        site=SiteSpec(),
        storeys=levels,
        roof_type="flat",
        design_region=region,
        assumptions=assumptions,
        open_questions=[ROOF_QUESTION],
    )
    return apply_structural_notes(spec, storeys)


#: Starting points offered on the Projects screen. Labels are what the user sees.
TEMPLATES: list[tuple[str, str, dict]] = [
    ("Small room", "A single room, 4 × 3 m.",
     {"width_m": 4.0, "depth_m": 3.0, "storeys": 1, "windows_per_wall": 1}),
    ("House, single storey", "8 × 6 m, one floor, four walls with doors and windows.",
     {"width_m": 8.0, "depth_m": 6.0, "storeys": 1, "windows_per_wall": 1}),
    ("House, two storeys", "8 × 6 m footprint, two floors.",
     {"width_m": 8.0, "depth_m": 6.0, "storeys": 2, "windows_per_wall": 1}),
    ("Small office", "12 × 9 m, one floor, more glazing.",
     {"width_m": 12.0, "depth_m": 9.0, "storeys": 1, "windows_per_wall": 3, "doors_south": 2}),
    ("Empty rectangle", "6 × 6 m shell with a single door — a clean slate.",
     {"width_m": 6.0, "depth_m": 6.0, "storeys": 1, "windows_per_wall": 0}),
]


def from_template(label: str, name: str, region: str = "BD") -> BuildingSpec:
    """Create a project from a named template."""
    for template_label, _description, params in TEMPLATES:
        if template_label == label:
            return rectangular_project(name, region=region, **params)
    raise ValueError(f"no such template: {label!r}")
