# FreeCAD Python API reference (condensed)

Verified against **FreeCAD 1.1.3**; ArchPilot runs **1.1.4**. Units are **millimetres** and
**degrees** by default. Authoritative source for anything beyond this digest:
<https://wiki.freecad.org/Python_scripting_tutorial> and the per-workbench "Scripting" pages
(`Part_scripting`, `Sketcher_scripting`, `PartDesign_Scripting`, `Draft_API`, `TechDraw_API`,
`Mesh_Scripting`).

## App / document model

```python
import FreeCAD as App
if "thing" in App.listDocuments():     # re-runnable: newDocument on a taken name
    App.closeDocument("thing")         # silently creates "thing1" instead of failing
doc = App.newDocument("thing")
App.setActiveDocument(doc.Name)
obj = doc.addObject("Part::Box", "MyBox")   # type string, then object Name
doc.getObject("MyBox")
doc.Objects
doc.removeObject("MyBox")
doc.recompute()                        # REQUIRED before measuring / exporting
doc.saveAs("/abs/path/thing.FCStd")
App.openDocument("/abs/path/x.FCStd")
App.Vector(x, y, z)
```

Placement: `obj.Placement = App.Placement(App.Vector(10,0,0), App.Rotation(App.Vector(0,0,1), 45))`.
`obj.Placement.Base` is the translation.

## Part workbench — direct solids (`import Part`)

```python
box = doc.addObject("Part::Box", "Box");  box.Length, box.Width, box.Height = 40, 20, 10
cyl = doc.addObject("Part::Cylinder", "Cyl"); cyl.Radius, cyl.Height = 5, 10
sph = doc.addObject("Part::Sphere", "Sph"); sph.Radius = 8
con = doc.addObject("Part::Cone", "Cone"); con.Radius1, con.Radius2, con.Height = 6, 2, 12
tor = doc.addObject("Part::Torus", "Tor"); tor.Radius1, tor.Radius2 = 10, 3
```

Booleans take `Base`/`Tool`; `Part::MultiFuse` / `Part::MultiCommon` take `Shapes = [...]`:

```python
cut  = doc.addObject("Part::Cut", "Cut");     cut.Base, cut.Tool = box, cyl
fuse = doc.addObject("Part::Fuse", "Fuse");   fuse.Base, fuse.Tool = box, cyl
comm = doc.addObject("Part::Common", "Comm"); comm.Base, comm.Tool = box, cyl
doc.recompute()
```

`Part::Cut`/`Fuse`/`Common` hide their inputs automatically; `Part::Fillet`/`Chamfer` do **not**.

```python
fil = doc.addObject("Part::Fillet", "Fillet"); fil.Base = box
fil.Edges = [(i + 1, 2.0, 2.0) for i in range(len(box.Shape.Edges))]
box.Visibility = False
doc.recompute()
```

Scripted geometry without doc objects (wrap in a `Part::Feature`):

```python
wire  = Part.makePolygon([App.Vector(0,0,0), App.Vector(10,0,0), App.Vector(10,10,0), App.Vector(0,0,0)])
face  = Part.Face(wire)
solid = face.extrude(App.Vector(0,0,5))
feat  = doc.addObject("Part::Feature", "Extruded"); feat.Shape = solid
```

## Measuring

```python
shp = obj.Shape          # recompute() first
bb  = shp.BoundBox       # bb.XLength/YLength/ZLength, bb.XMin.., bb.Center
shp.Volume               # mm^3
shp.Area                 # mm^2
shp.CenterOfMass
shp.isValid()
shp.check()              # None if ok
len(shp.Solids), len(shp.Faces), len(shp.Edges), len(shp.Vertexes)
```

## Sketcher — constrained 2D profiles (`import Sketcher`)

```python
sk = body.newObject("Sketcher::SketchObject", "Sketch")   # or doc.addObject(...)
V = App.Vector
sk.addGeometry(Part.LineSegment(V(0,0,0), V(30,0,0)), False)   # False = not construction
sk.addGeometry(Part.Circle(V(0,0,0), V(0,0,1), 5), False)
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i+1) % 4, 1))
sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
sk.addConstraint(Sketcher.Constraint("Distance", 0, 30.0))
doc.recompute()
```

Constraint kinds: `Coincident, Horizontal, Vertical, Parallel, Perpendicular, Equal, Tangent,
PointOnObject, Distance, DistanceX, DistanceY, Radius, Diameter, Angle, Symmetric`.
Endpoints are `(geoId, pointPos)`; pointPos `1`=start, `2`=end, `3`=centre; geoId `-1` = H axis
(its start `(-1, 1)` is the origin), `-2` = V axis.

```python
sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 30.0))
sk.addConstraint(Sketcher.Constraint("PointOnObject", 0, 1, -1))
sk.FullyConstrained      # True once no DOF remain
sk.solve()               # 0 = solved
```

Placing a sketch: an unattached sketch (`MapMode == "Deactivated"`) lies on XY.

```python
plane = [f for f in body.Origin.OriginFeatures if f.Role == "XZ_Plane"][0]  # XY_/XZ_/YZ_Plane
sk.AttachmentSupport = [(plane, "")]; sk.MapMode = "FlatFace"
sk2.AttachmentSupport = [(pad, "Face6")]; sk2.MapMode = "FlatFace"  # top of a Box = Face6
```

Look up origin features by `Role`, not name. On an XZ-attached sketch, sketch X is world X and
sketch Y is world Z.

## PartDesign — parametric feature modelling

```python
body = doc.addObject("PartDesign::Body", "Body")
sk   = body.newObject("Sketcher::SketchObject", "Sketch")
doc.recompute()
pad  = body.newObject("PartDesign::Pad", "Pad");   pad.Profile = sk; pad.Length = 10
doc.recompute()
```

```python
pad.Length = 10; pad.Reversed = True; pad.Midplane = True
pk = body.newObject("PartDesign::Pocket", "Pocket"); pk.Profile = sk2
pk.Type = "ThroughAll"            # enum: Length, ThroughAll, UpToFirst, UpToFace, TwoLengths, UpToShape
rev = body.newObject("PartDesign::Revolution", "Rev"); rev.Profile = sk
rev.ReferenceAxis = (sk, ["V_Axis"]); rev.Angle = 360
fil = body.newObject("PartDesign::Fillet", "Fillet")
fil.Base = (pk, ["Edge1", "Edge2"]); fil.Radius = 1     # Chamfer: .Size

lp = body.newObject("PartDesign::LinearPattern", "LP")
lp.Originals = [pk]; lp.Direction = (sk, ["H_Axis"]); lp.Length = 48; lp.Occurrences = 2
body.Tip = lp                     # newObject does NOT move the Tip onto a pattern
```

**Pick edges and faces by geometry, not guessed index** (`Edge1` of a cylinder is often a seam):

```python
edges = [i + 1 for i, e in enumerate(feat.Shape.Edges)
         if e.Curve.TypeId == "Part::GeomCircle" and abs(e.Curve.Radius - 20) < 1e-6]
top = [i + 1 for i, f in enumerate(feat.Shape.Faces)
       if f.Surface.TypeId == "Part::GeomPlane" and abs(f.CenterOfMass.z - 5) < 1e-6]
```

Also `Groove`, `Loft`, `Sweep`, `Chamfer` (`.Size`), `Mirrored`, `Hole`. `body.Tip` is the final
feature; `body.Shape` is the solid.

## Draft — 2D drafting (`import Draft`)

```python
import Draft
Draft.make_circle(radius, placement=None)
Draft.make_rectangle(length, height)
Draft.make_polygon(nsides, radius)
Draft.make_wire([App.Vector(0,0,0), App.Vector(10,0,0), App.Vector(10,10,0)], closed=True)
Draft.make_text(["line 1", "line 2"], App.Vector(0,0,0))
arr = Draft.make_array(base, App.Vector(10,0,0), App.Vector(0,10,0), 3, 2)
```

## Mesh (`import Mesh, MeshPart`)

```python
import Mesh, MeshPart
mesh = doc.addObject("Mesh::Feature", "Mesh")
mesh.Mesh = MeshPart.meshFromShape(Shape=solid, LinearDeflection=0.1, AngularDeflection=0.5)
Mesh.export([obj], "/abs/out.stl")
```

## Import / Export — verified format matrix

| Call | Formats confirmed working |
|------|---------------------------|
| `Part.export([objs], path)` | `.step` / `.stp`, `.iges` / `.igs`, `.brep` |
| `Mesh.export([objs], path)` | `.stl`, `.obj`, `.ply`, `.3mf`, `.amf`, `.off` |
| `Import.insert(path, docName)` | STEP / IGES into an existing document (verified) |
| `Part.read(path)` | STEP / IGES / BREP straight to a `TopoShape` |
| `Mesh.insert(path, docName)` | STL / OBJ / PLY into a document |

`Part.export`/`Mesh.export` pick the exporter from the file extension. For a finer STL, build
the mesh with `MeshPart.meshFromShape(Shape=..., LinearDeflection=0.05, AngularDeflection=0.2)`
then `mesh.write(path)`.

## GUI & screenshots (live GUI only — `Gui` undefined under `freecadcmd`)

```python
view = Gui.getDocument(doc.Name).activeView()
view.setAnimationEnabled(False)   # view changes animate; saveImage would catch it mid-turn
view.viewIsometric(); view.viewFront(); view.viewTop()
view.fitAll()
view.saveImage("/abs/shot.png", 1000, 750, "White")
```

## TechDraw — 2D technical drawings

```python
import os, time, TechDraw, TechDrawGui
page = doc.addObject("TechDraw::DrawPage", "Page")
tmpl = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
tmpl.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", "ISO",
                             "A4_Landscape_ISO5457_minimal.svg")
page.Template = tmpl
texts = {k: "" for k in tmpl.EditableTexts}
texts.update(title="Bracket", drawing_number="BR-001", scale="2:1")
tmpl.EditableTexts = texts
top = doc.addObject("TechDraw::DrawViewPart", "Top"); page.addView(top)
top.Source = [part]; top.Direction = App.Vector(0, 0, 1)
top.X, top.Y = 90, 140
top.ScaleType = "Custom"; top.Scale = 2   # ScaleType defaults to "Page": Scale is IGNORED otherwise
doc.recompute()
```

View geometry computes in a **background thread** — wait for `top.getVisibleEdges()` before
dimensioning, or every dimension measures `0.0`. Edge lengths come back **scaled**.

```python
dim = doc.addObject("TechDraw::DrawViewDimension", "DimW")
dim.Type = "DistanceX"   # DistanceX/DistanceY/Distance/Radius/Diameter/Angle
dim.References2D = [(top, "Edge1")]; page.addView(dim)
dim.X, dim.Y = 0, -40
doc.recompute()
print(dim.getRawValue())     # model units, independent of view Scale — 0.0 = wrong ref
```

The template (frame, title block) renders only once the page has been opened in the GUI.

## Gotchas

- **`doc.recompute()` before every measure or export.**
- **Failed features don't raise** — `State` contains `"Invalid"`.
- **TechDraw is asynchronous** — wait for `getVisibleEdges()`.
- **Sketcher indices**: geometry 0-based; external `-1`/`-2` are the sketch axes.
- **Units mm/deg.**
- **Booleans hide their inputs**, they don't consume them.
- **`Part.export` vs `Mesh.export`** are different modules for B-rep vs mesh output.
