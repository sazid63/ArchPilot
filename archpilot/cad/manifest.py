"""The measured-geometry manifest: what FreeCAD actually produced, and how to read it.

The manifest is the only channel by which model geometry reaches the rest of the app. It is
plain JSON so it can be inspected, diffed and tested without FreeCAD.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from archpilot.core.errors import FreeCADOperationFailed

#: FreeCAD object roles. Only ``result`` objects are part of the building; the others exist
#: to make booleans work and must never be measured or costed.
ROLE_RESULT = "result"
ROLE_INTERNAL = "internal"
ROLE_CUTTER = "cutter"


@dataclass
class MeasuredObject:
    """One FreeCAD object, measured. Geometry here is the source of truth for volumes."""

    ap_id: str
    ap_kind: str
    storey: str
    label: str
    volume_m3: float
    surface_area_m2: float
    bbox_m: tuple[float, float, float]
    bbox_min_m: tuple[float, float, float]
    solids: int
    valid: bool
    role: str = ROLE_RESULT
    meta: dict[str, str] = field(default_factory=dict)

    def meta_float(self, key: str, default: float = 0.0) -> float:
        return _as_float(self.meta.get(key), default)

    def meta_int(self, key: str, default: int = 0) -> int:
        return _as_int(self.meta.get(key), default)

    @property
    def is_result(self) -> bool:
        return self.role == ROLE_RESULT

    @property
    def height_m(self) -> float:
        return self.bbox_m[2]

    @property
    def footprint_m2(self) -> float:
        return self.bbox_m[0] * self.bbox_m[1]

    def as_row(self) -> dict:
        return {
            "ap_id": self.ap_id, "ap_kind": self.ap_kind, "storey": self.storey,
            "label": self.label, "volume_m3": self.volume_m3,
            "surface_area_m2": self.surface_area_m2, "bbox_m": list(self.bbox_m),
            "bbox_min_m": list(self.bbox_min_m), "solids": self.solids,
            "valid": self.valid, "role": self.role, "meta": dict(self.meta),
        }


@dataclass
class ObjectRecord:
    """A FreeCAD object that carries no ArchPilot tag — i.e. added outside ArchPilot."""

    name: str
    label: str
    type_id: str
    volume_m3: float
    visible: bool

    def describe(self) -> str:
        return f"{self.label or self.name} ({self.type_id})"


def _as_float(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default      # reject NaN


def _as_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float_triple(value, default: tuple[float, float, float]) -> tuple[float, float, float]:
    try:
        seq = list(value)
    except TypeError:
        return default
    if len(seq) != 3:
        return default
    return (_as_float(seq[0]), _as_float(seq[1]), _as_float(seq[2]))


def object_from_row(row: dict) -> MeasuredObject:
    return MeasuredObject(
        ap_id=str(row.get("ap_id", "")),
        ap_kind=str(row.get("ap_kind", "")),
        storey=str(row.get("storey", "")),
        label=str(row.get("label", "")),
        volume_m3=_as_float(row.get("volume_m3")),
        surface_area_m2=_as_float(row.get("surface_area_m2")),
        bbox_m=_as_float_triple(row.get("bbox_m"), (0.0, 0.0, 0.0)),
        bbox_min_m=_as_float_triple(row.get("bbox_min_m"), (0.0, 0.0, 0.0)),
        solids=_as_int(row.get("solids")),
        valid=bool(row.get("valid", False)),
        role=str(row.get("role", ROLE_RESULT)),
        meta={str(k): str(v) for k, v in (row.get("meta") or {}).items()},
    )


def read_manifest(path: Path | str) -> list[MeasuredObject]:
    """Read a manifest file written by a FreeCAD run.

    A missing file is a hard error: FreeCAD claimed success but produced nothing.
    """
    path = Path(path)
    if not path.is_file():
        raise FreeCADOperationFailed(
            "FreeCAD did not produce a measurement file.",
            detail=f"expected {path}")
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FreeCADOperationFailed(
            "The measurement file FreeCAD wrote is not valid JSON.",
            detail=f"{path}: {e}") from e
    if not isinstance(rows, list):
        raise FreeCADOperationFailed(
            "The measurement file FreeCAD wrote has an unexpected shape.",
            detail=f"{path}: expected a list, got {type(rows).__name__}")
    return [object_from_row(r) for r in rows if isinstance(r, dict)]


def read_external_objects(path: Path | str) -> list[ObjectRecord]:
    """Read the list of untagged (externally added) objects a sync run found."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(rows, list):
        return []
    out: list[ObjectRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(ObjectRecord(
            name=str(row.get("name", "")),
            label=str(row.get("label", "")),
            type_id=str(row.get("type_id", "")),
            volume_m3=_as_float(row.get("volume_m3")),
            visible=bool(row.get("visible", True)),
        ))
    return out


def results_only(objects: list[MeasuredObject]) -> list[MeasuredObject]:
    """Filter to the objects that form the building (excludes cutters and intermediates)."""
    return [o for o in objects if o.is_result]


def read_sync_document(path: Path | str) -> tuple[list[MeasuredObject], list[ObjectRecord]]:
    """Read a sync report: tagged results plus any objects added outside ArchPilot."""
    path = Path(path)
    if not path.is_file():
        raise FreeCADOperationFailed(
            "FreeCAD did not produce a sync report.",
            detail=f"expected {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FreeCADOperationFailed(
            "The sync report FreeCAD wrote is not valid JSON.",
            detail=f"{path}: {e}") from e
    if not isinstance(payload, dict):
        raise FreeCADOperationFailed(
            "The sync report FreeCAD wrote has an unexpected shape.",
            detail=f"{path}: expected an object")
    rows = payload.get("results") or []
    results = [object_from_row(r) for r in rows if isinstance(r, dict)]
    external: list[ObjectRecord] = []
    for row in payload.get("external") or []:
        if not isinstance(row, dict):
            continue
        external.append(ObjectRecord(
            name=str(row.get("name", "")),
            label=str(row.get("label", "")),
            type_id=str(row.get("type_id", "")),
            volume_m3=_as_float(row.get("volume_m3")),
            visible=bool(row.get("visible", True)),
        ))
    return results, external


def total_volume_m3(objects: list[MeasuredObject]) -> float:
    return sum(o.volume_m3 for o in results_only(objects))
