"""Validated structured operations — the only thing the AI is allowed to say.

Security rule: AI output is **data, never code**. Every operation crosses a strict Pydantic
schema that rejects unknown names, wrong types and out-of-range values before any of it is
applied. Nothing here imports, evaluates or executes a string.

Rejected operations are reported with a reason rather than being silently ignored, so the
user can see exactly what the model got wrong and correct it.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from archpilot.spec.schema import BuildingSpec, Storey
from archpilot.spec.shape import rectangle_slab, rectangle_walls
from archpilot.spec.template import apply_structural_notes

MAX_STOREYS = 12
MAX_OPENINGS_PER_WALL = 6

AssessmentRegion = Literal["BD", "US", "IN"]
RoofKind = Literal["flat", "sloped"]
DisplayUnits = Literal["m", "mm", "cm", "ft"]


class DesignRectangularBuilding(BaseModel):
    name: Literal["design_rectangular_building"] = "design_rectangular_building"
    width_m: float = Field(gt=0, le=300, description="Overall footprint width.")
    depth_m: float = Field(gt=0, le=300, description="Overall footprint depth.")
    storeys: int = Field(ge=1, le=MAX_STOREYS)
    storey_height_m: float = Field(gt=0, le=6)
    wall_thickness_m: float = Field(gt=0, le=1.0, default=0.24)
    slab_thickness_m: float = Field(gt=0, le=1.0, default=0.15)
    doors_south: int = Field(ge=0, le=4, default=1)
    windows_per_wall: int = Field(ge=0, le=MAX_OPENINGS_PER_WALL, default=1)
    roof_type: RoofKind = "flat"
    units: DisplayUnits = "m"
    design_region: AssessmentRegion = "BD"
    project_name: str = Field(default="", max_length=160)


class SetFootprint(BaseModel):
    name: Literal["set_footprint"] = "set_footprint"
    width_m: float = Field(gt=0, le=300)
    depth_m: float = Field(gt=0, le=300)


class SetStoreys(BaseModel):
    name: Literal["set_storeys"] = "set_storeys"
    storeys: int = Field(ge=1, le=MAX_STOREYS)


class SetStoreyHeight(BaseModel):
    name: Literal["set_storey_height"] = "set_storey_height"
    storey_height_m: float = Field(gt=0, le=6)


class SetWallThickness(BaseModel):
    name: Literal["set_wall_thickness"] = "set_wall_thickness"
    wall_thickness_m: float = Field(gt=0, le=1.0)


class SetSlabThickness(BaseModel):
    name: Literal["set_slab_thickness"] = "set_slab_thickness"
    slab_thickness_m: float = Field(gt=0, le=1.0)


class SetRoofType(BaseModel):
    name: Literal["set_roof_type"] = "set_roof_type"
    roof_type: RoofKind


class SetUnits(BaseModel):
    name: Literal["set_units"] = "set_units"
    units: DisplayUnits


class SetDesignRegion(BaseModel):
    name: Literal["set_design_region"] = "set_design_region"
    design_region: AssessmentRegion


class SetProjectName(BaseModel):
    name: Literal["set_project_name"] = "set_project_name"
    project_name: str = Field(min_length=1, max_length=160)


class AddAssumption(BaseModel):
    name: Literal["add_assumption"] = "add_assumption"
    text: str = Field(min_length=3, max_length=400)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class AddOpenQuestion(BaseModel):
    name: Literal["add_open_question"] = "add_open_question"
    text: str = Field(min_length=3, max_length=400)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


#: The complete allow-list. A model asking for anything else is refused.
ALLOWED_OPS: dict[str, type[BaseModel]] = {
    DesignRectangularBuilding.model_fields["name"].default: DesignRectangularBuilding,
    SetFootprint.model_fields["name"].default: SetFootprint,
    SetStoreys.model_fields["name"].default: SetStoreys,
    SetStoreyHeight.model_fields["name"].default: SetStoreyHeight,
    SetWallThickness.model_fields["name"].default: SetWallThickness,
    SetSlabThickness.model_fields["name"].default: SetSlabThickness,
    SetRoofType.model_fields["name"].default: SetRoofType,
    SetUnits.model_fields["name"].default: SetUnits,
    SetDesignRegion.model_fields["name"].default: SetDesignRegion,
    SetProjectName.model_fields["name"].default: SetProjectName,
    AddAssumption.model_fields["name"].default: AddAssumption,
    AddOpenQuestion.model_fields["name"].default: AddOpenQuestion,
}


@dataclass
class Operation:
    name: str
    raw: dict[str, Any]
    validated: BaseModel | None = None
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        return self.validated is not None


@dataclass
class OpResult:
    spec: BuildingSpec
    applied: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    parse_error: str = ""

    @property
    def ok(self) -> bool:
        return not self.rejected and not self.parse_error

    @property
    def changed(self) -> bool:
        return bool(self.applied)


# ---------------------------------------------------------------------------
# Parsing — tolerant of surrounding prose and ```json fences
# ---------------------------------------------------------------------------
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def _extract_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for match in _FENCE.finditer(text):
        candidates.append(match.group(1).strip())
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    # Last resort: the outermost {...} or [...] in the text.
    for opening, closing in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opening), stripped.rfind(closing)
        if start != -1 and end > start:
            candidates.append(stripped[start:end + 1])
    return candidates


def extract_operations(text: str) -> tuple[list[dict[str, Any]], str]:
    """Pull operation objects out of a model reply.

    Returns (operations, parse_error). parse_error is only set when nothing usable was found.
    """
    if not isinstance(text, str) or not text.strip():
        return [], "the AI response was empty"
    for candidate in _extract_json_candidates(text):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("ops"), list):
            return [x for x in payload["ops"] if isinstance(x, dict)], ""
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)], ""
        if isinstance(payload, dict) and "name" in payload:
            return [payload], ""
    return [], "no JSON operations found in the AI response"


def validate_operations(candidates: list[dict[str, Any]]) -> list[Operation]:
    ops: list[Operation] = []
    for raw in candidates:
        name = str(raw.get("name", "")).strip()
        if not name:
            ops.append(Operation("", raw, None, "operation has no name"))
            continue
        model_cls = ALLOWED_OPS.get(name)
        if model_cls is None:
            ops.append(Operation(name, raw, None,
                                 f"operation '{name}' is not permitted"))
            continue
        try:
            ops.append(Operation(name, raw, model_cls.model_validate(raw), ""))
        except ValidationError as e:
            detail = "; ".join(
                f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                for err in e.errors())[:300]
            ops.append(Operation(name, raw, None, detail or "invalid values"))
    return ops


# ---------------------------------------------------------------------------
# Applying — each rule only produces data the schema already proved valid
# ---------------------------------------------------------------------------
def _footprint(spec: BuildingSpec) -> tuple[float, float]:
    """Recover (width, depth) from the first slab polygon, falling back to the walls."""
    xs: list[float] = []
    ys: list[float] = []
    for st in spec.storeys:
        for slab in st.slabs:
            xs += [p[0] for p in slab.outline]
            ys += [p[1] for p in slab.outline]
    if not xs:
        for st in spec.storeys:
            for w in st.walls:
                xs += [w.start[0], w.end[0]]
                ys += [w.start[1], w.end[1]]
    if not xs:
        return 8.0, 6.0
    return max(xs) - min(xs), max(ys) - min(ys)


def _openings_count(spec: BuildingSpec) -> tuple[int, int]:
    """(doors on the ground south wall, windows per wall) as the user last set them."""
    if not spec.storeys:
        return 1, 1
    ground = next((s for s in spec.storeys if s.level == 0), spec.storeys[0])
    south = next((w for w in ground.walls if w.id.endswith("w-south")), None)
    doors = windows = 0
    if south is not None:
        doors = sum(1 for o in south.openings if o.kind == "door")
        windows = sum(1 for o in south.openings if o.kind == "window")
    east = next((w for w in ground.walls if w.id.endswith("w-east")), None)
    if east is not None and east.openings:
        windows = max(windows, len(east.openings))
    return doors, windows


def _thickness(spec: BuildingSpec) -> float:
    for st in spec.storeys:
        for w in st.walls:
            if w.thickness_m > 0:
                return w.thickness_m
    return 0.24


def _slab_thickness(spec: BuildingSpec) -> float:
    for st in spec.storeys:
        for s in st.slabs:
            return s.thickness_m
    return 0.15


def _storey_height(spec: BuildingSpec) -> float:
    if spec.storeys:
        return max(s.floor_height_m for s in spec.storeys)
    return 3.0


def rebuild_rectangle(spec: BuildingSpec, *, width_m: float, depth_m: float,
                      storeys: int | None = None,
                      storey_height_m: float | None = None,
                      wall_thickness_m: float | None = None,
                      slab_thickness_m: float | None = None,
                      doors_south: int | None = None,
                      windows_per_wall: int | None = None,
                      roof_type: RoofKind | None = None,
                      units: DisplayUnits | None = None,
                      design_region: str | None = None,
                      project_name: str | None = None) -> BuildingSpec:
    """Rebuild the whole design as a rectangle, preserving anything not specified.

    This is the only geometry-generating path the AI can reach, and it can only produce
    rectangular footprints. Anything else has to be edited by hand in the design view.
    """
    n_storeys = storeys if storeys is not None else len(spec.storeys)
    n_storeys = max(1, min(MAX_STOREYS, n_storeys))
    height = storey_height_m or _storey_height(spec)
    thick = wall_thickness_m or _thickness(spec)
    slab_t = slab_thickness_m or _slab_thickness(spec)
    doors, windows = _openings_count(spec)
    if doors_south is not None:
        doors = max(0, min(4, doors_south))
    if windows_per_wall is not None:
        windows = max(0, min(MAX_OPENINGS_PER_WALL, windows_per_wall))

    storeys_out: list[Storey] = []
    for level in range(n_storeys):
        lid = f"L{level}"
        # A slab sits at its storey's base elevation, so level 0's slab is the ground slab
        # and every upper storey's slab is the suspended slab carrying that floor. The roof
        # is deliberately not modelled yet and is reported as such by the measurement engine.
        slab_kind = "ground" if level == 0 else "floor"
        storeys_out.append(Storey(
            id=lid, level=level, floor_height_m=height,
            walls=rectangle_walls(lid, width_m, depth_m, thick, height,
                                  doors_south=doors, windows_per_wall=windows),
            slabs=[rectangle_slab(f"{lid}-slab-{slab_kind}", slab_kind,
                                  width_m, depth_m, slab_t)],
        ))

    rebuilt = BuildingSpec(
        spec_version=spec.spec_version,
        name=(project_name.strip() if project_name else spec.name),
        units=units or spec.units,
        site=spec.site,
        storeys=storeys_out,
        roof_type=roof_type or spec.roof_type,
        design_region=design_region or spec.design_region,
        assumptions=list(spec.assumptions),
        open_questions=list(spec.open_questions),
        warnings=list(spec.warnings),
    )
    # Rebuilding may have added storeys, which always changes the structural caveats. One
    # shared helper keeps a rebuilt design's warnings identical to a templated one's.
    return apply_structural_notes(rebuilt, n_storeys)


def apply_operations(spec: BuildingSpec, candidates: list[dict[str, Any]],
                     base_name: str | None = None) -> OpResult:
    """Validate then apply operations in order, stopping per-op on failure.

    A failing operation never aborts the others: each one is applied independently and its
    rejection reason is collected.
    """
    result = OpResult(spec=spec if base_name is None else spec.model_copy(
        update={"name": base_name or spec.name}))
    current = result.spec

    for op in validate_operations(candidates):
        if not op.is_valid:
            result.rejected.append((op.name or str(op.raw)[:40], op.reason))
            continue
        try:
            applied, description = _apply_one(current, op.validated)
        except (ValueError, KeyError, TypeError, ArithmeticError) as e:
            result.rejected.append((op.name, str(e)[:300]))
            continue
        current = applied
        result.applied.append(description or op.name)
    result.spec = current
    return result


def _apply_one(spec: BuildingSpec, op: BaseModel) -> tuple[BuildingSpec, str]:
    if isinstance(op, DesignRectangularBuilding):
        new = rebuild_rectangle(
            spec, width_m=op.width_m, depth_m=op.depth_m, storeys=op.storeys,
            storey_height_m=op.storey_height_m, wall_thickness_m=op.wall_thickness_m,
            slab_thickness_m=op.slab_thickness_m, doors_south=op.doors_south,
            windows_per_wall=op.windows_per_wall, roof_type=op.roof_type,
            units=op.units, design_region=op.design_region,
            project_name=op.project_name or None)
        return new, (f"designed a {op.width_m:g} × {op.depth_m:g} m rectangular building "
                     f"with {op.storeys} storey(s)")
    if isinstance(op, SetFootprint):
        new = rebuild_rectangle(spec, width_m=op.width_m, depth_m=op.depth_m)
        return new, f"set the footprint to {op.width_m:g} × {op.depth_m:g} m"
    if isinstance(op, SetStoreys):
        w, d = _footprint(spec)
        new = rebuild_rectangle(spec, width_m=w, depth_m=d, storeys=op.storeys)
        return new, f"set the building to {op.storeys} storey(s)"
    if isinstance(op, SetStoreyHeight):
        w, d = _footprint(spec)
        new = rebuild_rectangle(spec, width_m=w, depth_m=d,
                                storey_height_m=op.storey_height_m)
        return new, f"set the storey height to {op.storey_height_m:g} m"
    if isinstance(op, SetWallThickness):
        w, d = _footprint(spec)
        new = rebuild_rectangle(spec, width_m=w, depth_m=d,
                                wall_thickness_m=op.wall_thickness_m)
        return new, f"set the wall thickness to {op.wall_thickness_m:g} m"
    if isinstance(op, SetSlabThickness):
        w, d = _footprint(spec)
        new = rebuild_rectangle(spec, width_m=w, depth_m=d,
                                slab_thickness_m=op.slab_thickness_m)
        return new, f"set the slab thickness to {op.slab_thickness_m:g} m"
    if isinstance(op, SetRoofType):
        return spec.model_copy(update={"roof_type": op.roof_type}), \
            f"set the roof type to {op.roof_type}"
    if isinstance(op, SetUnits):
        return spec.model_copy(update={"units": op.units}), \
            f"set display units to {op.units}"
    if isinstance(op, SetDesignRegion):
        return spec.model_copy(update={"design_region": op.design_region}), \
            f"set the rate region to {op.design_region}"
    if isinstance(op, SetProjectName):
        return spec.model_copy(update={"name": op.project_name}), \
            f"renamed the project to {op.project_name}"
    if isinstance(op, AddAssumption):
        if op.text in spec.assumptions:
            return spec, f"assumption already recorded: {op.text[:60]}"
        return spec.model_copy(update={"assumptions": [*spec.assumptions, op.text]}), \
            f"recorded the assumption: {op.text[:80]}"
    if isinstance(op, AddOpenQuestion):
        if op.text in spec.open_questions:
            return spec, f"open question already recorded: {op.text[:60]}"
        return spec.model_copy(
            update={"open_questions": [*spec.open_questions, op.text]}), \
            f"raised an open question: {op.text[:80]}"
    raise ValueError(f"validated operation {type(op).__name__} has no rule")


# ---------------------------------------------------------------------------
# Describing the design back to the model (data only)
# ---------------------------------------------------------------------------
def describe_spec(spec: BuildingSpec) -> str:
    w, d = _footprint(spec)
    doors, windows = _openings_count(spec)
    lines = [
        f"Project: {spec.name}",
        f"Footprint: {w:g} m × {d:g} m (area {w * d:g} m²)",
        f"Storeys: {len(spec.storeys)} × {_storey_height(spec):g} m clear "
        f"(building height {spec.overall_height_m():g} m including slabs)",
        f"Wall thickness: {_thickness(spec):g} m; slab thickness: {_slab_thickness(spec):g} m",
        f"Openings on the ground-floor south wall: {doors} door(s), "
        f"approximately {windows} window(s) per wall",
        f"Roof type: {spec.roof_type}",
        f"Rate region: {spec.design_region or 'unset'}",
        f"Display units: {spec.units}",
        f"Total gross floor area: {spec.gross_floor_area_m2():g} m²",
        f"Walls: {spec.wall_count}, slabs: {spec.slab_count}, openings: {spec.opening_count}",
    ]
    if spec.assumptions:
        lines.append("Known assumptions:")
        lines += [f"  - {a}" for a in spec.assumptions]
    if spec.open_questions:
        lines.append("Open questions the user has not answered:")
        lines += [f"  - {q}" for q in spec.open_questions]
    if spec.warnings:
        lines.append("Warnings:")
        lines += [f"  - {w}" for w in spec.warnings]
    return "\n".join(lines)


def allowed_op_names() -> list[str]:
    return sorted(ALLOWED_OPS)


def estimate_feasibility(spec: BuildingSpec) -> tuple[bool, str]:
    """Rough sanity envelope for a single-user tool. Not engineering — just guard rails."""
    if not spec.storeys:
        return False, "no storeys defined"
    footprint_area = max((s.area_m2 for st in spec.storeys for s in st.slabs), default=0.0)
    if footprint_area <= 0:
        return False, "no floor slab — cannot estimate a buildable footprint"
    for st in spec.storeys:
        for wall in st.walls:
            if wall.height_m > 6:
                return False, f"wall {wall.id} is {wall.height_m:g} m tall — " \
                              "check the storey height"
            if wall.thickness_m > wall.height_m:
                return False, f"wall {wall.id} is thicker than it is tall"
    wall_total = sum(w.net_volume_m3 for st in spec.storeys for w in st.walls)
    ratio = wall_total / footprint_area if footprint_area else math.inf
    if ratio > 4.0:
        return True, (f"unusually high wall-to-floor ratio ({ratio:.1f}) — check for "
                      "overlapping or duplicate walls")
    return True, "within normal bounds"
