"""Versioned building specification (spec v1).

Independent of AI providers and of FreeCAD. Walls are plan lines, slabs are plan polygons,
openings carry a position along the wall and a sill height — enough for the CAD layer to
produce genuinely net wall geometry and for measurement to be deterministic.
"""
from __future__ import annotations

import math
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SPEC_VERSION = 1

WallMaterial = Literal["brick", "block", "concrete", "timber", "steel", "glass", "other"]
SlabKind = Literal["ground", "floor", "roof"]
OpeningKind = Literal["door", "window"]
DisplayUnits = Literal["m", "mm", "cm", "ft"]

MAX_DIM_M = 500.0
MAX_WALL_THICKNESS_M = 2.0
MAX_OPENING_W_M = 8.0
MAX_OPENING_H_M = 5.0

# Upper bounds on how much one design may contain, so that a hostile or broken producer (an AI
# response, an imported file) cannot hand the app a design big enough to exhaust memory or stall
# the model build. Every limit is far above any real preliminary design.
MAX_STOREYS = 40
MAX_WALLS_PER_STOREY = 200
MAX_OPENINGS_PER_WALL = 50
MAX_SLABS_PER_STOREY = 50
MAX_OUTLINE_POINTS = 2_000
MAX_NOTES = 200
MAX_NOTE_LEN = 1_000

#: Free text attached to a design — an assumption, an open question or a warning.
NoteText = Annotated[str, Field(max_length=MAX_NOTE_LEN)]


class Opening(BaseModel):
    """A door or window cut into a wall, positioned along it and above the floor."""

    id: str = Field(min_length=1, max_length=64)
    kind: OpeningKind
    width_m: float = Field(gt=0, le=MAX_OPENING_W_M)
    height_m: float = Field(gt=0, le=MAX_OPENING_H_M)
    sill_height_m: float = Field(default=0.0, ge=0)
    offset_m: float = Field(default=0.0, ge=0,
                            description="Distance from the wall start to the opening's left edge.")

    @property
    def area_m2(self) -> float:
        return self.width_m * self.height_m

    @property
    def top_m(self) -> float:
        return self.sill_height_m + self.height_m


class Wall(BaseModel):
    """A straight wall segment in plan, extruded vertically."""

    id: str = Field(min_length=1, max_length=64)
    start: tuple[float, float]
    end: tuple[float, float]
    thickness_m: float = Field(gt=0, le=MAX_WALL_THICKNESS_M)
    height_m: float = Field(gt=0, le=20)
    material: WallMaterial = "brick"
    openings: list[Opening] = Field(default_factory=list, max_length=MAX_OPENINGS_PER_WALL)

    @property
    def length_m(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def angle_deg(self) -> float:
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]
        return math.degrees(math.atan2(dy, dx))

    @property
    def gross_area_m2(self) -> float:
        return self.length_m * self.height_m

    @property
    def opening_area_m2(self) -> float:
        return sum(o.area_m2 for o in self.openings)

    @property
    def net_area_m2(self) -> float:
        return max(0.0, self.gross_area_m2 - self.opening_area_m2)

    @property
    def net_volume_m3(self) -> float:
        return self.net_area_m2 * self.thickness_m

    @property
    def gross_volume_m3(self) -> float:
        return self.gross_area_m2 * self.thickness_m

    @model_validator(mode="after")
    def _geometry_sane(self) -> "Wall":
        if self.length_m <= 1e-6:
            raise ValueError(f"wall {self.id!r}: start and end coincide (zero length)")
        for o in self.openings:
            if o.offset_m + o.width_m > self.length_m + 1e-9:
                raise ValueError(
                    f"wall {self.id!r}: opening {o.id!r} runs past the wall end "
                    f"({o.offset_m + o.width_m:.3f} m > {self.length_m:.3f} m)")
            if o.top_m > self.height_m + 1e-9:
                raise ValueError(
                    f"wall {self.id!r}: opening {o.id!r} is taller than the wall "
                    f"({o.top_m:.3f} m > {self.height_m:.3f} m)")
        # openings must not overlap each other along the wall
        ordered = sorted(self.openings, key=lambda o: o.offset_m)
        for a, b in pairwise(ordered):
            if a.offset_m + a.width_m > b.offset_m + 1e-9:
                raise ValueError(
                    f"wall {self.id!r}: openings {a.id!r} and {b.id!r} overlap")
        return self


class Slab(BaseModel):
    """A horizontal slab defined by a closed plan polygon (metres)."""

    id: str = Field(min_length=1, max_length=64)
    outline: list[tuple[float, float]] = Field(min_length=3, max_length=MAX_OUTLINE_POINTS)
    thickness_m: float = Field(gt=0, le=3)
    kind: SlabKind = "ground"
    material: str = Field(default="concrete", max_length=64)

    @field_validator("outline")
    @classmethod
    def _distinct_points(cls, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        reduced: list[tuple[float, float]] = []
        for p in pts:
            if not reduced or math.dist(p, reduced[-1]) > 1e-9:
                reduced.append((float(p[0]), float(p[1])))
        if len(reduced) >= 2 and math.dist(reduced[0], reduced[-1]) <= 1e-9:
            reduced.pop()
        if len(reduced) < 3:
            raise ValueError("slab outline needs at least 3 distinct points")
        return reduced

    @property
    def area_m2(self) -> float:
        """Shoelace polygon area (unsigned)."""
        pts = self.outline
        s = 0.0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1], strict=False):
            s += x1 * y2 - x2 * y1
        return abs(s) / 2.0

    @property
    def perimeter_m(self) -> float:
        pts = self.outline
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:] + pts[:1], strict=False))

    @property
    def volume_m3(self) -> float:
        return self.area_m2 * self.thickness_m

    @model_validator(mode="after")
    def _area_positive(self) -> "Slab":
        if self.area_m2 <= 1e-9:
            raise ValueError(f"slab {self.id!r}: outline encloses no area")
        return self


class Storey(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    level: int = Field(ge=0, le=100)
    floor_height_m: float = Field(gt=0, le=15)
    walls: list[Wall] = Field(default_factory=list, max_length=MAX_WALLS_PER_STOREY)
    slabs: list[Slab] = Field(default_factory=list, max_length=MAX_SLABS_PER_STOREY)


class SiteSpec(BaseModel):
    width_m: float | None = Field(default=None, gt=0, le=MAX_DIM_M)
    depth_m: float | None = Field(default=None, gt=0, le=MAX_DIM_M)
    note: str = Field(default="", max_length=MAX_NOTE_LEN)


class BuildingSpec(BaseModel):
    spec_version: int = SPEC_VERSION
    name: str = Field(min_length=1, max_length=160)
    units: DisplayUnits = "m"
    site: SiteSpec = Field(default_factory=SiteSpec)
    storeys: list[Storey] = Field(min_length=1, max_length=MAX_STOREYS)
    roof_type: Literal["flat", "sloped"] = "flat"
    design_region: str = Field(default="", max_length=200)
    assumptions: list[NoteText] = Field(default_factory=list, max_length=MAX_NOTES)
    open_questions: list[NoteText] = Field(default_factory=list, max_length=MAX_NOTES)
    warnings: list[NoteText] = Field(default_factory=list, max_length=MAX_NOTES)

    @field_validator("spec_version")
    @classmethod
    def _supported_version(cls, v: int) -> int:
        if v != SPEC_VERSION:
            raise ValueError(
                f"unsupported spec_version {v}; this build reads version {SPEC_VERSION}")
        return v

    @model_validator(mode="after")
    def _unique_ids(self) -> "BuildingSpec":
        seen: set[str] = set()
        for st in self.storeys:
            for coll in (st.walls, st.slabs):
                for item in coll:
                    if item.id in seen:
                        raise ValueError(f"duplicate id {item.id!r}")
                    seen.add(item.id)
            for w in st.walls:
                for o in w.openings:
                    if o.id in seen:
                        raise ValueError(f"duplicate opening id {o.id!r}")
                    seen.add(o.id)
        levels = [s.level for s in self.storeys]
        if len(levels) != len(set(levels)):
            raise ValueError("duplicate storey level numbers")
        return self

    # ---- derived helpers -------------------------------------------------
    def storey_rise_m(self, storey: Storey) -> float:
        """How much taller a storey makes the building: its slab plus its clear height."""
        slab = max((s.thickness_m for s in storey.slabs), default=0.0)
        return storey.floor_height_m + slab

    def storey_elevations(self) -> dict[str, float]:
        """Base elevation of each storey, stacking slabs *and* wall heights.

        ``floor_height_m`` is the **clear** wall height, so a storey physically occupies
        ``base + slab_thickness + floor_height_m``: its slab sits at the base and its walls
        stand on that slab (see ``cad/scripts.py``). Stacking only the floor heights would put
        each upper storey's floor slab inside the top of the walls below it, and the shared
        band would then be counted twice in the total concrete volume.
        """
        out: dict[str, float] = {}
        z = 0.0
        for st in sorted(self.storeys, key=lambda s: s.level):
            out[st.id] = z
            z += self.storey_rise_m(st)
        return out

    def total_height_m(self) -> float:
        """Sum of clear storey heights. Excludes slab thicknesses — see overall_height_m."""
        return sum(s.floor_height_m for s in self.storeys)

    def overall_height_m(self) -> float:
        """Top of the building as actually modelled, including every storey's slab."""
        return sum(self.storey_rise_m(s) for s in self.storeys)

    @property
    def wall_count(self) -> int:
        return sum(len(s.walls) for s in self.storeys)

    @property
    def slab_count(self) -> int:
        return sum(len(s.slabs) for s in self.storeys)

    @property
    def opening_count(self) -> int:
        return sum(len(w.openings) for s in self.storeys for w in s.walls)

    def gross_floor_area_m2(self) -> float:
        """Sum of the largest slab area per storey (their footprint)."""
        total = 0.0
        for st in self.storeys:
            if st.slabs:
                total += max(s.area_m2 for s in st.slabs)
        return total

    def objects(self):
        """Yield (kind, storey, object) for every model object, level order."""
        for st in sorted(self.storeys, key=lambda s: s.level):
            for sl in st.slabs:
                yield "slab", st, sl
            for w in st.walls:
                yield "wall", st, w

    def unresolved_assumptions(self) -> list[str]:
        items = list(self.assumptions)
        if not self.open_questions:
            return items
        return items + [f"Unresolved: {q}" for q in self.open_questions]


def build_spec(payload: dict) -> BuildingSpec:
    return BuildingSpec.model_validate(payload)


def spec_from_json(raw: str) -> BuildingSpec:
    return BuildingSpec.model_validate_json(raw)
