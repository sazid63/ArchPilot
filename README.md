# ArchPilot

**From Idea to Building.**

ArchPilot is a Windows desktop application for the early stages of a building project: turn a
simple description into a 3D model, measure what the model actually contains, price it, and
produce a bill of quantities you can send or print.

It is a **preliminary** tool. Every estimate it produces is stamped
`PRELIMINARY ESTIMATE — NOT FOR CONSTRUCTION` because the numbers depend on assumptions and on
rates you supply, and because structural design and code compliance are explicitly out of scope.

## What it does

| Screen | Purpose |
| --- | --- |
| **Projects** | Create, open, rename, archive and delete projects. Start from a ready-made building. |
| **Design** | Set the size, storeys, wall thickness, openings and region. Every change is validated. |
| **Assistant** | *Optional.* Describe a change in plain words; review the proposed edits before applying. |
| **3D Model** | Build the model with FreeCAD, open it, and re-read it after hand edits. |
| **Quantities** | Measured quantities in billed units, with derived allowances labelled. |
| **Cost & BOQ** | Set rates, generate the bill of quantities, and see honestly incomplete totals. |
| **Export** | Write the bill and quantity schedule as CSV, XLSX or PDF. |
| **Settings** | Point ArchPilot at FreeCAD, configure the optional assistant, find your data. |

## Design principles

These are promises the code is written to keep, not aspirations:

- **Windows desktop, not a web app.** It runs on your machine. There is no server and no account.
- **FreeCAD is the only CAD engine.** ArchPilot drives an independent FreeCAD installation as a
  separate program; it never imports FreeCAD into its own process.
- **No paid service is required.** Designing, modelling, measuring, pricing, saving and exporting
  all work with the AI assistant switched off.
- **The AI never runs code.** The assistant may only propose changes drawn from a fixed list of
  validated building operations. ArchPilot checks each one and shows the effect before applying
  anything. Nothing the model produces is executed.
- **Quantities and costs are real calculations.** Areas, volumes and totals come from measured
  geometry, not from the design on paper.
- **A missing price is never a zero price.** An item with no rate is shown as *unpriced*, its
  amount is left blank, and the total is labelled as covering only the priced items.
- **Nothing is silently ignored.** The model is re-read after every build; anything in the file
  that ArchPilot did not create is surfaced to you and excluded from quantities. Unsupported
  geometry is flagged, never dropped.
- **Usable without a terminal.** Normal setup, modelling, costing and exporting are all done from
  the interface.

## Requirements

- **Windows 10 or 11**
- **[FreeCAD](https://www.freecad.org/) 1.x** (free and open source) — required to build and
  measure models. ArchPilot finds it automatically, or you can point at it in Settings.
- **Python 3.11 or newer** — only if you run from source.

The assistant is optional and needs an **OpenAI-compatible** server (`/chat/completions`). That
can be a free, local server such as LM Studio, llama.cpp or an Ollama-compatible shim, or an
online service you choose. Local servers usually need no API key.

## Install

### From the installer (recommended)

Run `ArchPilot-Setup.exe`, then launch ArchPilot from the Start menu. Install FreeCAD separately
from [freecad.org](https://www.freecad.org/) if you have not already.

### From source (development)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
archpilot
```

If FreeCAD is installed somewhere unusual, set an explicit path before launching:

```powershell
$env:ARCHPILOT_FREECADCMD = "C:\Path\To\FreeCAD\bin\freecadcmd.exe"
```

### Build a packaged executable or installer

From the repository root:

```powershell
powershell -File installer\build.ps1
```

(That works in Windows PowerShell 5.1 and in PowerShell 7. If you have PowerShell 7 installed you can use
`pwsh -File installer\build.ps1` instead.)

This runs PyInstaller (`installer\archpilot.spec`) to produce
`dist\ArchPilot\ArchPilot.exe`, and then — **only if Inno Setup 6 is installed** — compiles
`installer\archpilot.iss` into `installer\Output\ArchPilot-Setup-<version>.exe`. If Inno Setup is
not present, the script says so and stops after the executable; it does not pretend an installer
was produced. FreeCAD is never bundled: the installer checks for it and tells the user where to
get it if it is missing.

## How to use it

1. **Create a project.** On the **Projects** screen, pick a template closest to your idea.
2. **Set the design.** On the **Design** screen, set the width, depth, storeys, wall thickness,
   openings and region. Numbers that cannot work together are refused with a reason.
3. **Build the model.** On the **3D Model** screen, press **Build**. FreeCAD runs and produces the
   `.FCStd` file. If you edit the file in FreeCAD itself, press **Re-read** so ArchPilot learns
   what changed.
4. **Measure.** The **Quantities** screen lists what the model contains, with derived allowances
   labelled and every warning shown.
5. **Price it.** On **Cost & BOQ**, set a rate for each item. Items you have no rate for stay
   *unpriced*. Generate the bill.
6. **Export.** On **Export**, choose the bill or the quantities, and CSV, XLSX or PDF.

If you change the design after building, ArchPilot marks the quantities and costs as **out of
date** and asks you to rebuild rather than quietly showing stale figures.

## What ArchPilot does not do

Stated plainly, because the interface states them plainly too:

- It does **not** design structure, reinforcement or foundations. A slab and wall thickness are
  assumptions you can change; they are not calculated.
- It does **not** check code compliance or produce drawings for construction.
- It does **not** model the roof slab; the roof is reported as excluded from the quantities.
- Adding or removing a storey invalidates structural assumptions, and ArchPilot says so.
- The assistant produces **rectangular footprints only**, and cannot add structural detail.
- Plant, services, and any category with no measured data are excluded from the estimate.

## Where your work is kept

Everything lives under `Documents\ArchPilot` on this computer:

```
ArchPilot\
  archpilot.db      projects, designs, saved rates and revisions
  secrets.bin       the optional AI key, encrypted for your Windows account (DPAPI)
  archpilot.log     a log, for when something goes wrong
  work\             one folder per project: the FreeCAD file and Exchange JSON
  exports\          files you have exported
```

The AI key is **not** stored in the project database, and is never written into a project file.
Copy the whole folder to move your work to another machine.

## Testing

ArchPilot is tested at several levels, and the distinction matters:

- **Unit tests** cover the spec, geometry helpers, the measurement engine, the cost and BOQ
  arithmetic, storage and revisions, the secret store, the AI operation allow-list, and the
  assistant's provider layer.
- **Interface tests** build the whole window with Qt's `offscreen` platform and drive each screen
  against a real project session.
- **FreeCAD integration tests** drive the real `freecadcmd` executable to build models, then
  verify measured volumes against independently computed values. These are marked `freecad` and
  are **skipped** when FreeCAD is not installed — a green run on a machine without FreeCAD does
  not mean the FreeCAD integration was verified.
- **End-to-end tests** run the full design → build → measure → price → export path.

Run them with:

```powershell
python -m pytest tests/ -q          # everything available on this machine
python -m pytest tests/ -q -m freecad   # only the real-FreeCAD integration tests
python -m ruff check archpilot tests    # lint
```

### Packaging and dependencies

The installer path is exercised, not merely built. `installer\Output\ArchPilot-Setup-<version>.exe`
has been run through install → upgrade → uninstall on Windows, checking that each step exits 0,
that the program directory is gone after uninstall, and that the project database under
`Documents\ArchPilot` is left byte-for-byte unchanged (`archpilot.db` compares equal by SHA-256
across the upgrade and the uninstall).

Runtime dependencies are checked for known vulnerabilities with
[pip-audit](https://pypi.org/project/pip-audit/). On a development machine ArchPilot's
dependencies share a Python environment with unrelated packages, so the scan is scoped to
ArchPilot's own closure rather than the whole environment:

```powershell
pip-audit .        # the dependencies declared in pyproject.toml
```

Both the declared dependencies and the exact installed runtime closure (pydantic, PySide6,
openpyxl, reportlab and their transitive dependencies) reported no known vulnerabilities when
this was last checked. Advisories change over time, so treat this as a point-in-time result and
re-run it before a release.

The packaged build carries only what the application uses. Several runtime dependencies import
optional libraries ArchPilot never touches — openpyxl's NumPy and lxml fallbacks, pydantic's `rich`
and mypy plugins, ReportLab's character-set detection — and the PySide6 hook collects a fixed set of
Qt libraries regardless of which modules are imported. `installer\archpilot.spec` excludes the unused
Python packages and drops the Qt and Pillow payloads that cannot be reached (established by walking
the PE import tables of the Qt DLLs that *are* used, with no delay-load edge into the rest). That
roughly halves the bundle: `_internal` falls from about 169 MB to 78 MB.

This is checked rather than assumed. The whole test suite passes with every excluded package blocked
at import, and a throwaway probe built from the same spec imports every module, exports CSV, XLSX and
PDF, reads the workbook back through openpyxl, and builds the window offscreen.

## Third-party software

ArchPilot stands on free and open-source work and is grateful for it:

- **[FreeCAD](https://www.freecad.org/)** — the 3D modelling engine, driven as an external
  program. ArchPilot is not affiliated with or endorsed by the FreeCAD project.
- **[Qt for Python (PySide6)](https://doc.qt.io/qtforpython/)** — the interface, LGPL.
- **[Pydantic](https://docs.pydantic.dev/)** — validation of designs and assistant proposals.
- **[openpyxl](https://openpyxl.readthedocs.io/)** — Excel workbooks.
- **[ReportLab](https://www.reportlab.com/opensource/)** — PDF documents.

ArchPilot also vendors three knowledge-only reference skills under `.opencode/skills/`
(FreeCAD, civil engineering, and PySide6 interface design), each attributed to its upstream
author and licensed under MIT; no upstream executables are included.

## Licence

MIT. See `LICENSE`.
