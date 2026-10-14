"""Deterministic quantity measurement.

Rules of this module:

* Geometry produced by FreeCAD is the source of truth for solid volumes. The specification
  supplies structure that geometry does not carry (floor finish, formwork intent, materials).
* Everything is derived and returned as :class:`QuantityLine` records — no rounding until
  display, so totals never drift.
* Anything present in the design but missing from the model is reported as a warning. Nothing
  is silently dropped, and nothing is invented.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from archpilot.cad.manifest import MeasuredObject
from archpilot.spec.schema import BuildingSpec

# Item codes are stable identifiers used to look up rates. Changing one is a breaking change
# for saved rate catalogues.
CODE_SLAB_CONCRETE = "CONC.SLAB.{kind}"
CODE_SLAB_FORMWORK_SOFFIT = "FORM.SLAB.SOFFIT"
CODE_SLAB_FORMWORK_EDGE = "FORM.SLAB.EDGE"
CODE_SLAB_FINISH = "FINISH.FLOOR"
CODE_WALL_VOLUME = "WALL.{material}"
CODE_WALL_PLASTER = "FINISH.WALL.PLASTER"
CODE_DOOR = "OPENING.DOOR"
CODE_WINDOW = "OPENING.WINDOW"

CATEGORY_CONCRETE = "concrete"
CATEGORY_MASONRY = "masonry"
CATEGORY_FINISHES = "finishes"
CATEGORY_FORMWORK = "formwork"
CATEGORY_OPENINGS = "openings"

_SLAB_LABEL = {"ground": "Ground slab", "floor": "Suspended floor slab", "roof": "Roof slab"}
_WALL_LABEL = {
    "brick": "Brick wall", "block": "Block wall", "concrete": "Concrete wall",
    "timber": "Timber wall", "steel": "Steel wall", "glass": "Glazed wall",
    "other": "Wall",
}


@dataclass(frozen=True)
class QuantityLine:
    code: str
    description: str
    unit: str
    quantity: float
    category: str
    element_ids: tuple[str, ...] = ()
    source: str = "model"
    notes: tuple[str, ...] = ()

    def rounded(self, dp: int = 3) -> float:
        return round(self.quantity, dp)

    @property
    def is_zero(self) -> bool:
        return abs(self.quantity) < 1e-9


@dataclass
class MeasurementResult:
    lines: list[QuantityLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    coverage: dict[str, int] = field(default_factory=dict)

    def by_code(self, code: str) -> QuantityLine | None:
        return next((line for line in self.lines if line.code == code), None)

    def total(self, unit: str) -> float:
        return sum(line.quantity for line in self.lines if line.unit == unit)

    def category_totals(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for line in self.lines:
            out[line.category] = out.get(line.category, 0.0) + line.quantity
        return out

    @property
    def unit_totals(self) -> dict[str, float]:
        units = sorted({line.unit for line in self.lines})
        return {u: self.total(u) for u in units}


def measure(spec: BuildingSpec, manifest: Iterable[MeasuredObject]) -> MeasurementResult:
    measured = {m.ap_id: m for m in manifest}
    result = MeasurementResult()

    design_ids = {obj.id for _, _, obj in spec.objects()}
    for missing in sorted(design_ids - set(measured)):
        result.warnings.append(
            f"{missing}: in the design but not found in the 3D model — not measured.")
    for extra in sorted(set(measured) - design_ids):
        result.warnings.append(
            f"{extra}: exists in the 3D model but not in the design — excluded from quantities.")

    slab_lines: list[QuantityLine] = []
    wall_lines: list[QuantityLine] = []
    opening_lines: list[QuantityLine] = []

    for st in sorted(spec.storeys, key=lambda s: s.level):
        for slab in sorted(st.slabs, key=lambda s: s.id):
            got = measured.get(slab.id)
            if got is None:
                continue
            slab_lines.extend(_slab_lines(slab, got, st.id))
        for wall in sorted(st.walls, key=lambda w: w.id):
            got = measured.get(wall.id)
            if got is None:
                continue
            line, warn = _wall_lines(wall, got, st.id)
            wall_lines.append(line)
            result.warnings.extend(warn)
            for o in wall.openings:
                opening_lines.append(_opening_line(o, wall, st.id))

    result.lines = slab_lines + wall_lines + opening_lines
    result.lines.append(plaster_areas(spec, measured))

    roof_slabs = [s for st in spec.storeys for s in st.slabs if s.kind == "roof"]
    if not roof_slabs:
        result.warnings.append(
            "No roof slab is modelled, so roof concrete, formwork and finishes are "
            "excluded from these quantities.")
    if not any(line.category == CATEGORY_MASONRY for line in result.lines):
        result.warnings.append("No walls were measured — masonry is excluded.")

    slabs_designed = sum(len(st.slabs) for st in spec.storeys)
    walls_designed = sum(len(st.walls) for st in spec.storeys)
    slabs_measured = sum(1 for st in spec.storeys for s in st.slabs if s.id in measured)
    walls_measured = sum(1 for st in spec.storeys for w in st.walls if w.id in measured)
    openings_on_measured_walls = sum(
        len(w.openings) for st in spec.storeys for w in st.walls if w.id in measured)
    result.coverage = {
        "slabs_designed": slabs_designed,
        "slabs_measured": slabs_measured,
        "walls_designed": walls_designed,
        "walls_measured": walls_measured,
        "openings_designed": spec.opening_count,
        "openings_measured": openings_on_measured_walls,
    }
    return result


def _slab_lines(slab, got: MeasuredObject, storey_id: str) -> list[QuantityLine]:
    lines: list[QuantityLine] = []
    kind_label = _SLAB_LABEL.get(slab.kind, "Slab")
    lines.append(QuantityLine(
        code=CODE_SLAB_CONCRETE.format(kind=slab.kind.upper()),
        description=f"{kind_label} — {slab.material}, {slab.thickness_m * 1000:.0f} mm",
        unit="m3", quantity=got.volume_m3, category=CATEGORY_CONCRETE,
        element_ids=(slab.id,), source="model",
        notes=(f"storey {storey_id}",),
    ))
    lines.append(QuantityLine(
        code=CODE_SLAB_FINISH,
        description="Floor finish to slab (plan area)",
        unit="m2", quantity=slab.area_m2, category=CATEGORY_FINISHES,
        element_ids=(slab.id,), source="spec",
    ))
    if slab.kind == "ground":
        # Slab cast on grade: only its edge needs formwork.
        lines.append(QuantityLine(
            code=CODE_SLAB_FORMWORK_EDGE,
            description="Edge formwork to ground slab",
            unit="m2", quantity=slab.perimeter_m * slab.thickness_m,
            category=CATEGORY_FORMWORK, element_ids=(slab.id,), source="derived",
        ))
    else:
        # Suspended slab: soffit is struck formwork.
        lines.append(QuantityLine(
            code=CODE_SLAB_FORMWORK_SOFFIT,
            description="Soffit formwork to suspended slab",
            unit="m2", quantity=slab.area_m2, category=CATEGORY_FORMWORK,
            element_ids=(slab.id,), source="derived",
        ))
    return lines


def _wall_lines(wall, got: MeasuredObject, storey_id: str) -> tuple[QuantityLine, list[str]]:
    warnings: list[str] = []
    net_volume = got.volume_m3
    net_area = net_volume / wall.thickness_m if wall.thickness_m else 0.0
    material_label = _WALL_LABEL.get(wall.material, "Wall")

    expected_net = wall.net_volume_m3
    if expected_net > 1e-9 and abs(net_volume - expected_net) / expected_net > 0.02:
        warnings.append(
            f"{wall.id}: model volume {net_volume:.3f} m³ differs from the design's net "
            f"{expected_net:.3f} m³ by more than 2% — check the opening cutouts.")

    line = QuantityLine(
        code=CODE_WALL_VOLUME.format(material=wall.material.upper()),
        description=f"{material_label}, {wall.thickness_m * 1000:.0f} mm thick, "
                    f"net of openings",
        unit="m3", quantity=net_volume, category=CATEGORY_MASONRY,
        element_ids=(wall.id,), source="model",
        notes=(f"storey {storey_id}", f"net area {net_area:.3f} m²",
               f"{len(wall.openings)} opening(s) deducted"),
    )
    return line, warnings


def _opening_line(opening, wall, storey_id: str) -> QuantityLine:
    if opening.kind == "door":
        code, label = CODE_DOOR, "Doors — supply and fix"
    else:
        code, label = CODE_WINDOW, "Windows — supply and fix"
    return QuantityLine(
        code=code, description=label, unit="nos", quantity=1.0,
        category=CATEGORY_OPENINGS, element_ids=(opening.id,), source="spec",
        notes=(f"in {wall.id} (storey {storey_id})",
               f"{opening.width_m:.2f} × {opening.height_m:.2f} m",
               f"area {opening.area_m2:.3f} m²"),
    )


def plaster_areas(spec: BuildingSpec, measured: dict[str, MeasuredObject]) -> QuantityLine:
    """Wall plaster/finish counted on BOTH faces of every wall — kept separate so it is
    obvious this is a derived allowance rather than measured geometry."""
    total = 0.0
    ids: list[str] = []
    for st in spec.storeys:
        for wall in st.walls:
            got = measured.get(wall.id)
            if got is None or wall.thickness_m <= 0:
                continue
            total += 2.0 * (got.volume_m3 / wall.thickness_m)
            ids.append(wall.id)
    return QuantityLine(
        code=CODE_WALL_PLASTER,
        description="Wall plaster / finish — both faces (derived allowance)",
        unit="m2", quantity=total, category=CATEGORY_FINISHES,
        element_ids=tuple(ids), source="derived",
        notes=("both faces assumed finished", "deductions for openings not applied"),
    )


def summary(result: MeasurementResult) -> dict[str, float]:
    out = dict(result.unit_totals)
    out["line_count"] = float(len(result.lines))
    return out
