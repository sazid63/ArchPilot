"""Canonical rectangular-building geometry.

One place decides how a rectangle becomes walls, slabs and openings. The sample projects and
the AI's structured operations both call this, so "change the footprint to 9 × 5 m" and "give
me a 9 × 5 m sample" produce exactly the same model.

Openings are laid out as one centred group along each wall, so they never overlap and always
keep a clear distance from the wall ends. When the openings do not fit, the surplus is
dropped rather than pushed out of the wall.
"""
from __future__ import annotations

from archpilot.spec.schema import Opening, Slab, Wall

WALL_MARGIN_M = 0.30     # minimum clear distance from a wall end to an opening edge
OPENING_GAP_M = 0.35     # minimum clear distance between adjacent openings

DEFAULT_DOOR = (1.0, 2.1, 0.0)    # width, height, sill
DEFAULT_WINDOW = (1.5, 1.2, 0.9)


def layout(length: float, widths: list[float], gap: float = OPENING_GAP_M) -> list[float]:
    """Offsets for `widths` laid out as one centred group along a wall of `length`.

    Items that cannot fit are dropped from the end, so the returned list is never shorter
    than what the wall holds without overlap.
    """
    if length <= 0:
        return []
    kept: list[float] = []
    for w in widths:
        if w <= 0:
            continue
        span = sum(kept) + gap * max(0, len(kept)) + w
        if span > length - 2 * WALL_MARGIN_M + 1e-9:
            break
        kept.append(w)
    if not kept:
        return []
    span = sum(kept) + gap * (len(kept) - 1)
    cursor = (length - span) / 2.0
    offsets: list[float] = []
    for w in kept:
        offsets.append(round(cursor, 4))
        cursor += w + gap
    return offsets


def distribute(length: float, count: int, opening_width: float) -> list[float]:
    """Offsets for `count` equal openings — a thin wrapper over :func:`layout`."""
    if count <= 0 or opening_width <= 0:
        return []
    return layout(length, [opening_width] * int(count))


def rectangle_outline(width: float, depth: float, x0: float = 0.0,
                      y0: float = 0.0) -> list[tuple[float, float]]:
    return [(x0, y0), (x0 + width, y0), (x0 + width, y0 + depth), (x0, y0 + depth)]


def rectangle_walls(level: str, width: float, depth: float, thickness: float,
                    height: float, *, x0: float = 0.0, y0: float = 0.0,
                    doors_south: int = 1, windows_per_wall: int = 1,
                    window_width: float = DEFAULT_WINDOW[0]) -> list[Wall]:
    """Four perimeter walls on a rectangle, with distributed door/window openings."""
    x1, y1 = x0, y0
    x2, y2 = x0 + width, y0 + depth
    door_w, door_h, door_sill = DEFAULT_DOOR
    _window_w, win_h, win_sill = DEFAULT_WINDOW
    n_doors = max(0, doors_south)
    n_wins = max(0, windows_per_wall)

    # South: doors first, then glazing, as one centred group.
    south_offsets = layout(width, [door_w] * n_doors + [window_width] * n_wins)
    south: list[Opening] = []
    for i in range(n_doors):
        if i >= len(south_offsets):
            break
        south.append(Opening(id=f"{level}-d-south-{i}", kind="door", width_m=door_w,
                             height_m=door_h, sill_height_m=door_sill,
                             offset_m=south_offsets[i]))
    for i in range(n_wins):
        idx = n_doors + i
        if idx >= len(south_offsets):
            break
        south.append(Opening(id=f"{level}-win-south-{i}", kind="window",
                             width_m=window_width, height_m=win_h,
                             sill_height_m=win_sill, offset_m=south_offsets[idx]))

    def side(side_name: str, wall_length: float) -> list[Opening]:
        """Glazing sized to the shorter side so it keeps its edge clearances."""
        usable = min(window_width, wall_length - 2 * WALL_MARGIN_M)
        if usable <= 0 or n_wins == 0:
            return []
        return [Opening(id=f"{level}-win-{side_name}-{i}", kind="window", width_m=usable,
                        height_m=win_h, sill_height_m=win_sill, offset_m=o)
                for i, o in enumerate(layout(wall_length, [usable] * n_wins))]

    north_offsets = layout(width, [window_width] * n_wins)

    return [
        Wall(id=f"{level}-w-south", start=(x1, y1), end=(x2, y1), thickness_m=thickness,
             height_m=height, material="brick", openings=south),
        Wall(id=f"{level}-w-east", start=(x2, y1), end=(x2, y2), thickness_m=thickness,
             height_m=height, material="brick", openings=side("east", depth)),
        Wall(id=f"{level}-w-north", start=(x2, y2), end=(x1, y2), thickness_m=thickness,
             height_m=height, material="brick",
             openings=[Opening(id=f"{level}-win-north-{i}", kind="window",
                               width_m=window_width, height_m=win_h,
                               sill_height_m=win_sill, offset_m=o)
                       for i, o in enumerate(north_offsets)]),
        Wall(id=f"{level}-w-west", start=(x1, y2), end=(x1, y1), thickness_m=thickness,
             height_m=height, material="brick", openings=side("west", depth)),
    ]


def rectangle_slab(slab_id: str, kind: str, width: float, depth: float,
                   thickness: float, x0: float = 0.0, y0: float = 0.0,
                   material: str = "concrete") -> Slab:
    return Slab(id=slab_id, outline=rectangle_outline(width, depth, x0, y0),
                thickness_m=thickness, kind=kind, material=material)
