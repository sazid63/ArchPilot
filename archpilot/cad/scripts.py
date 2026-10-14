"""Turn a validated BuildingSpec into a geometry *plan* (pure data, no FreeCAD).

The plan is plain JSON-able data, so it can be unit-tested without FreeCAD installed.
The FreeCAD driver script consumes the plan and produces real objects.

Key geometric decisions (documented so quantities are traceable):
  * A wall is a box swept along its plan line, centred on that line.
  * Walls start at the TOP of the storey's slab so wall and slab volumes do not overlap
    (no double counting). Where a storey has no slab, walls start at the storey base.
  * Door/window openings are cut with boxes that are 400 mm wider than the wall, so the
    resulting wall volume is genuinely net of openings.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from archpilot.spec.schema import BuildingSpec, Slab, Wall

CUTTER_MARGIN_MM = 400.0


def literal(value: object) -> str:
    """A Python string literal for ``value``, ASCII-only.

    Using ``ascii`` rather than ``repr`` keeps the generated script pure ASCII, so a path or a
    name containing non-ASCII characters cannot depend on the script's source encoding. The
    result is always a valid, fully escaped Python literal.
    """
    return ascii(str(value))


def fill_template(template: str, values: dict[str, str]) -> str:
    """Substitute ``__PLACEHOLDER__`` tokens in a single pass.

    One pass matters: substituted content (a JSON blob, a file path) must never be re-scanned for
    further placeholders. Every value is expected to be an already-escaped Python literal, so
    nothing that arrives here can break out of the generated script. An unknown placeholder is a
    programming error and is raised rather than silently left in the output.
    """
    pattern = re.compile(r"__[A-Z0-9_]+__")

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        try:
            return values[token]
        except KeyError:
            raise KeyError(f"unfilled script placeholder: {token}") from None

    return pattern.sub(replace, template)


def spec_to_plan(spec: BuildingSpec, doc_name: str = "ArchPilot") -> dict[str, Any]:
    elevations = spec.storey_elevations()
    objects: list[dict[str, Any]] = []
    for st in sorted(spec.storeys, key=lambda s: s.level):
        base = elevations[st.id]
        slab_thickness = max((s.thickness_m for s in st.slabs), default=0.0)
        for slab in st.slabs:
            objects.append(_slab_entry(slab, st.id, base))
        for wall in st.walls:
            objects.append(_wall_entry(wall, st.id, base + slab_thickness))
    return {"doc_name": doc_name, "objects": objects}


def _slab_entry(slab: Slab, storey_id: str, z_m: float) -> dict[str, Any]:
    return {
        "ap_id": slab.id,
        "ap_kind": "slab",
        "type": "slab",
        "storey": storey_id,
        "z_m": round(z_m, 6),
        "outline": [[float(x), float(y)] for x, y in slab.outline],
        "thickness_m": slab.thickness_m,
        "slab_kind": slab.kind,
        "material": slab.material,
        "plan_area_m2": round(slab.area_m2, 6),
        "perimeter_m": round(slab.perimeter_m, 6),
    }


def _wall_entry(wall: Wall, storey_id: str, z_m: float) -> dict[str, Any]:
    return {
        "ap_id": wall.id,
        "ap_kind": "wall",
        "type": "wall",
        "storey": storey_id,
        "z_m": round(z_m, 6),
        "start": [float(wall.start[0]), float(wall.start[1])],
        "angle_deg": round(wall.angle_deg, 6),
        "length_m": round(wall.length_m, 6),
        "thickness_m": wall.thickness_m,
        "height_m": wall.height_m,
        "material": wall.material,
        "openings": [
            {
                "id": o.id,
                "kind": o.kind,
                "offset_m": o.offset_m,
                "width_m": o.width_m,
                "height_m": o.height_m,
                "sill_m": o.sill_height_m,
            }
            for o in wall.openings
        ],
    }


DRIVER_TEMPLATE = r'''
import json, math, os, sys, traceback
import FreeCAD as App
import Part

PLAN = json.loads(__PLAN_JSON__)
OUT_FCSTD = __OUT_FCSTD__
OUT_MANIFEST = __OUT_MANIFEST__


def nurse(message):
    # flush is essential: freecadcmd discards buffered stdout when a script exits via
    # sys.exit, which would otherwise leave the user staring at an empty error message.
    print("ARCHPILOT_ERROR " + str(message), flush=True)
    sys.exit(1)


def place_along(start_mm, angle_deg, along_mm, across_mm, z_mm):
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    nx, ny = -math.sin(a), math.cos(a)
    return App.Vector(start_mm[0] + dx * along_mm + nx * across_mm,
                      start_mm[1] + dy * along_mm + ny * across_mm,
                      z_mm)


def tag(obj, ap_id, ap_kind, storey, role):
    for prop in ("AP_ID", "AP_KIND", "AP_STOREY", "AP_ROLE"):
        if not hasattr(obj, prop):
            obj.addProperty("App::PropertyString", prop)
    obj.AP_ID = ap_id
    obj.AP_KIND = ap_kind
    obj.AP_STOREY = storey
    obj.AP_ROLE = role


def tag_meta(obj, key, value):
    if not hasattr(obj, "AP_" + key.upper()):
        obj.addProperty("App::PropertyString", "AP_" + key.upper())
    setattr(obj, "AP_" + key.upper(), str(value))


def build_slab(doc, ob):
    pts = [App.Vector(p[0] * 1000.0, p[1] * 1000.0, 0.0) for p in ob["outline"]]
    pts.append(pts[0])
    wire = Part.makePolygon(pts)
    face = Part.Face(wire)
    solid = face.extrude(App.Vector(0.0, 0.0, ob["thickness_m"] * 1000.0))
    obj = doc.addObject("Part::Feature", ob["ap_id"])
    obj.Shape = solid
    obj.Placement.Base = App.Vector(0.0, 0.0, ob["z_m"] * 1000.0)
    tag(obj, ob["ap_id"], "slab", ob["storey"], "result")
    tag_meta(obj, "plan_area_m2", ob["plan_area_m2"])
    tag_meta(obj, "thickness_m", ob["thickness_m"])
    tag_meta(obj, "slab_kind", ob["slab_kind"])
    return obj


def build_wall(doc, ob):
    start_mm = [ob["start"][0] * 1000.0, ob["start"][1] * 1000.0]
    angle = ob["angle_deg"]
    t_mm = ob["thickness_m"] * 1000.0
    z_mm = ob["z_m"] * 1000.0

    solid = doc.addObject("Part::Box", ob["ap_id"] + "_solid")
    solid.Length = ob["length_m"] * 1000.0
    solid.Width = t_mm
    solid.Height = ob["height_m"] * 1000.0
    solid.Placement = App.Placement(
        place_along(start_mm, angle, 0.0, -t_mm / 2.0, z_mm),
        App.Rotation(App.Vector(0.0, 0.0, 1.0), angle))
    tag(solid, "", "wall", ob["storey"], "internal")

    final = solid
    for i, op in enumerate(ob.get("openings", [])):
        cw = t_mm + __CUTTER_MARGIN__
        cutter = doc.addObject("Part::Box", "%s_cutter_%d" % (ob["ap_id"], i))
        cutter.Length = op["width_m"] * 1000.0
        cutter.Width = cw
        cutter.Height = op["height_m"] * 1000.0
        cutter.Placement = App.Placement(
            place_along(start_mm, angle, op["offset_m"] * 1000.0, -cw / 2.0,
                        z_mm + op["sill_m"] * 1000.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), angle))
        tag(cutter, "", "wall", ob["storey"], "cutter")

        cut = doc.addObject("Part::Cut", "%s_cut_%d" % (ob["ap_id"], i))
        cut.Base = final
        cut.Tool = cutter
        tag(cut, "", "wall", ob["storey"], "internal")
        final = cut

    tag(final, ob["ap_id"], "wall", ob["storey"], "result")
    tag_meta(final, "length_m", ob["length_m"])
    tag_meta(final, "height_m", ob["height_m"])
    tag_meta(final, "thickness_m", ob["thickness_m"])
    tag_meta(final, "opening_count", len(ob.get("openings", [])))
    tag_meta(final, "opening_area_m2",
             round(sum(o["width_m"] * o["height_m"] for o in ob.get("openings", [])), 6))
    return final


def main():
    doc_name = PLAN["doc_name"]
    if doc_name in App.listDocuments():
        App.closeDocument(doc_name)
    doc = App.newDocument(doc_name)

    for ob in PLAN["objects"]:
        if ob["type"] == "slab":
            build_slab(doc, ob)
        elif ob["type"] == "wall":
            build_wall(doc, ob)
        else:
            nurse("unsupported plan object type: %s" % ob.get("type"))

    doc.recompute()

    bad = [o.Name for o in doc.Objects
           if ("Invalid" in list(o.State) or "Error" in list(o.State))]
    if bad:
        nurse("recompute failed for: %s" % bad)

    manifest = []
    for o in doc.Objects:
        if getattr(o, "AP_ROLE", "") != "result":
            continue
        shape = o.Shape
        bb = shape.BoundBox
        manifest.append({
            "ap_id": o.AP_ID,
            "ap_kind": o.AP_KIND,
            "storey": o.AP_STOREY,
            "label": o.Label,
            "role": o.AP_ROLE,
            "volume_m3": shape.Volume / 1e9,
            "surface_area_m2": shape.Area / 1e6,
            "bbox_m": [bb.XLength / 1000.0, bb.YLength / 1000.0, bb.ZLength / 1000.0],
            "bbox_min_m": [bb.XMin / 1000.0, bb.YMin / 1000.0, bb.ZMin / 1000.0],
            "solids": len(shape.Solids),
            "valid": bool(shape.isValid()),
            "meta": {k[3:].lower(): getattr(o, k) for k in
                     ("AP_PLAN_AREA_M2", "AP_THICKNESS_M", "AP_SLAB_KIND",
                      "AP_LENGTH_M", "AP_HEIGHT_M", "AP_OPENING_COUNT",
                      "AP_OPENING_AREA_M2") if hasattr(o, k)},
        })

    doc.saveAs(OUT_FCSTD)
    with open(OUT_MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print("ARCHPILOT_BUILD_OK %d" % len(manifest), flush=True)


try:
    main()
except SystemExit:
    raise
except Exception:
    traceback.print_exc()
    sys.stderr.flush()
    nurse("unhandled exception — see log")
'''


def render_driver(plan: dict[str, Any], out_fcstd: str, out_manifest: str) -> str:
    return fill_template(DRIVER_TEMPLATE, {
        "__PLAN_JSON__": literal(json.dumps(plan)),
        "__OUT_FCSTD__": literal(out_fcstd),
        "__OUT_MANIFEST__": literal(out_manifest),
        "__CUTTER_MARGIN__": repr(CUTTER_MARGIN_MM),
    })


def wall_cutter_count(wall: Wall) -> int:
    return len(wall.openings)


def plan_object_count(plan: dict[str, Any]) -> int:
    return len(plan["objects"])


def plan_expected_volume_m3(plan: dict[str, Any]) -> float:
    """Analytic net volume of the plan — used as an independent test oracle."""
    total = 0.0
    for ob in plan["objects"]:
        if ob["type"] == "slab":
            total += ob["plan_area_m2"] * ob["thickness_m"]
        elif ob["type"] == "wall":
            gross = ob["length_m"] * ob["height_m"]
            openings = sum(o["width_m"] * o["height_m"] for o in ob.get("openings", []))
            total += max(0.0, gross - openings) * ob["thickness_m"]
    return total


def angle_between(ax: float, ay: float, bx: float, by: float) -> float:
    return math.degrees(math.atan2(by - ay, bx - ax))
