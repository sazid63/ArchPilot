---
name: freecad
description: 'Remote-control FreeCAD (the open-source parametric CAD app) by Python — build and edit 3D models: box/cylinder primitives and booleans, filleted/chamfered solids, constrained Sketcher profiles, parametric PartDesign features (pad, pocket, revolution, patterns), 2D Draft geometry, meshes, and TechDraw drawings, then measure them and export STEP/IGES/BREP/STL/OBJ. Use whenever the task involves creating or editing a CAD model, a 3D part, an .FCStd file, converting/exporting a model to STEP/STL/IGES/OBJ, or measures geometry. Also use for architectural/BIM modelling in FreeCAD (walls, slabs, openings as boolean cutouts, storeys) and headless batch builds via freecadcmd.'
summary: "model and measure in FreeCAD via Python; primitives, booleans, Sketcher, PartDesign, draft/mesh, metrics, export; headless freecadcmd batch builds"
category: cad
risk: medium
tags:
    - freecad
    - cad
    - parametric-modeling
    - 3d
    - scripting
    - bim
metadata:
  version: "1.0.0-vendored"
---

<!-- Vendored 2026-10-14 from https://github.com/jablonkai/skills (skills/freecad, MIT).
Knowledge and API reference only. The upstream freecad-bridge.py / freecad-send.sh
scripts are a LOCAL CODE-EXECUTION SERVER (binds 127.0.0.1:8735, NO authentication) and
were deliberately NOT installed. ArchPilot uses a headless `freecadcmd <script>` subprocess
instead — never a network code-execution endpoint. -->

# FreeCAD Control

FreeCAD is scriptable in **Python** against the `FreeCAD` / `FreeCADGui` / `Part` /
`Sketcher` / `PartDesign` / `Draft` / `Mesh` / `TechDraw` modules. Units are **mm** and
**degrees**; you must call `doc.recompute()` before measuring or exporting.

**ArchPilot context:** drive FreeCAD headlessly via `freecadcmd <script.py>` (a subprocess),
NOT via a network bridge. Under `freecadcmd`, `Gui` is undefined — guard with
`globals().get("Gui")`. An uncaught exception still exits 0, so wrap the script in
`try/except` and call `sys.exit(1)` so failures surface to the caller.

## The control loop

1. Write a build script (Python against the API — see
   [references/api-reference.md](references/api-reference.md)).
2. Run it: `freecadcmd build.py` (capture stdout; check the exit code).
3. **Check `State` before trusting anything** — a failed feature does NOT raise; it is
   marked `Invalid`. Scan the document, then measure.
4. Emit metrics as JSON (bbox, volume, `isValid()`, solid count) and read them back.
5. Export STEP/STL/FCStd for the deliverable.

## Modeling cheatsheet (proven patterns)

### Solid via primitives + boolean (plate with a bored hole)

```python
import FreeCAD as App, Part
doc = App.newDocument("thing")
box = doc.addObject("Part::Box", "Plate"); box.Length, box.Width, box.Height = 40, 20, 10
hole = doc.addObject("Part::Cylinder", "Hole"); hole.Radius, hole.Height = 5, 10
hole.Placement.Base = App.Vector(20, 10, 0)
cut = doc.addObject("Part::Cut", "Result"); cut.Base, cut.Tool = box, hole
doc.recompute()
```

### Architectural wall with real opening cutouts

Model a wall as a `Part::Box`, then cut each door/window out with a `Part::Cut` chain so the
resulting `.Shape.Volume` is genuinely **net** of openings.

```python
from functools import reduce
wall = doc.addObject("Part::Box", "wall-1"); wall.Length, wall.Width, wall.Height = 5000, 250, 3000
cutters = []
for i, (x, z, w, h) in enumerate([(1500, 900, 1500, 1200)]):   # sill height, opening size
    c = doc.addObject("Part::Box", f"cut-{i}")
    c.Length, c.Width, c.Height = w, 400, h          # deeper than the wall so it cuts through
    c.Placement.Base = App.Vector(x, -75, z)
    cutters.append(c)
if cutters:
    result = wall
    for c in cutters:
        cut = doc.addObject("Part::Cut", f"wall-1-cut-{c.Name}")
        cut.Base, cut.Tool = result, c
        result = cut
    result.addProperty("App::PropertyString", "AP_ID"); result.AP_ID = "wall-1"
```

### Check, then measure

```python
bad = [(o.Name, o.State) for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]
if bad:
    raise RuntimeError("recompute failed: %s" % bad)
shp = result.Shape; bb = shp.BoundBox
m = {"bbox": [bb.XLength, bb.YLength, bb.ZLength], "volume": shp.Volume,
     "valid": shp.isValid(), "solids": len(shp.Solids)}
```

### Export

```python
Part.export([result], out + "/x.step")   # B-rep: .step/.stp .iges/.igs .brep
Mesh.export([result], out + "/x.stl")    # mesh: .stl .obj .ply .3mf
doc.saveAs(out + "/x.FCStd")             # native, re-editable
```

See [references/api-reference.md](references/api-reference.md) for Sketcher, PartDesign,
Draft, Mesh, TechDraw, and the full export matrix.

## Verification

- **Returned stdout** — `print(...)` comes straight back.
- **`Invalid`-state check** turns a silent recompute failure into an error.
- **metrics.json** (bbox / volume / `isValid()` / solid count) — confirm geometry without
  eyeballing. Compare against values computed by hand (e.g. a wall's expected net volume).

## Gotchas

- **`doc.recompute()` before every measure/export** — attributes don't reach `.Shape` until then.
- **Failed features don't raise** — scan `o.State` for `"Invalid"`/`"Error"`.
- **`newDocument` never fails on a duplicate name** — it silently creates `name1`; close or
  reuse by name to stay re-runnable.
- **Booleans hide their inputs**, they don't consume them — `Base`/`Tool` still exist in the
  tree; export the *result* feature and hide inputs so they don't show through.
- **`Part.export` vs `Mesh.export`** are different modules for solid (B-rep) vs mesh output.
- **Units are mm/deg**; `App.Rotation(axis, angle)` takes degrees.
- **Pick edges/faces by geometry, not guessed indices** (`Edge1` is often a seam).

## Security

Upstream ships a bridge that is a **code-execution server** on `127.0.0.1:8735` with **no
authentication**. ArchPilot does not use it: it invokes `freecadcmd` with a script file it
generated itself from a validated specification. Never pipe untrusted/AI-generated code into
a FreeCAD session.
