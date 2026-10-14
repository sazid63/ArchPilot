---
name: py-side6-gui-design
description: Design principles for Python desktop applications built with PySide6/PyQt. Universal patterns for widget design, layout architecture, accessibility, micro-interactions, and performance. Use this skill when designing any desktop GUI — media player, editor, IDE, tool, or admin panel. Triggers on tasks involving PySide6/PyQt UI design, desktop app UX, widget styling, layout architecture, QSS theming, background-thread work, or Python desktop application interface improvements. Framework-agnostic within the Python desktop ecosystem.
---

<!-- Vendored for ArchPilot reference (2026-10-14) from
https://github.com/Joey-1123/opencode-v2-skills (skill py-side6-gui-design, MIT).
Guidance only — companion scripts/references/assets from upstream were deliberately NOT
installed. Upstream QSS/design tokens are advisory; ArchPilot's canonical theme remains
the app's own civil-engineering token module. -->

# Python Desktop GUI Design

Design principles for Python desktop applications built with PySide6/PyQt.

## Core Design Principles

### 1. Performance-First

- UI render < 16ms; widget response < 100ms; progressive display for large data
- **Offload heavy computation to worker threads (QThread, QtConcurrent) — never block the
  main thread.** Long CAD/IO operations must run off the GUI thread with progress feedback.
- Lazy-load content; generators for large datasets; bound caches

### 2. Bold Minimalism

- Clean, uncluttered interfaces with ample whitespace
- Limited palette (3-5 primary colors); accent colors for actions/selections
- Standard-sized native controls where they suffice
- Large, readable text

**Typography scale (desktop)**: 9pt small labels · 11pt body · 14pt section headers ·
18pt dialog titles · 24pt main headings.

### 3. Micro-Interactions

- Hover feedback 200-300ms; selection fade 150ms
- Skeleton placeholders while loading
- Animated progress bars for long operations + spinner during async work
- **Never leave a long operation with no visible feedback** — show indeterminate progress

### 4. Layout Architecture

- Use layout managers (QHBoxLayout, QVBoxLayout, QGridLayout, QSplitter) — never absolute
  positioning
- Set size policies; use stretch factors to distribute space
- Responsive layouts that adapt to resizing; handle long names, large tables, long text
- Visual hierarchy: primary content (focal) → navigation/toolbars → settings/status → help

### 5. Accessibility (WCAG AA)

- Visible focus indicators (`Qt.StrongFocus`); keyboard shortcuts for major actions
- Tab order follows visual layout
- `setAccessibleName()` on interactive widgets
- Contrast ≥ 4.5:1; never rely on color alone (shape + icon + text)

### 6. Component Architecture (Atomic Design)

- **Atoms**: QPushButton, QLineEdit, QLabel, QSpinBox, QCheckBox
- **Molecules**: search bar, labelled form field, stat tile
- **Organisms**: toolbar, sidebar navigation, data table, chat panel
- **Templates**: main window layout, wizard flow
- **Pages**: Home, Design, Model, Costs, Settings

**Reuse rules**: one shared components module; a single token-driven QSS stylesheet; never
duplicate layout code.

## Common Design Patterns

1. **Data Display View** — filter bar → table with alternating rows, sortable columns →
   detail/empty state.
2. **Settings Dialog** — category list → settings panel → Apply/Cancel/OK; reset defaults;
   never display secrets in plain text.
3. **Status/Progress View** — progress bar → task list with status icons → cancel → summary.
4. **Split-View Editor** — sidebar tree (25-30%) | editor; QSplitter with persistent position.

## Common Pitfalls

1. Blocking the main thread → QThread/QtConcurrent for heavy ops.
2. Hardcoded/absolute layouts → layout managers only.
3. Inconsistent theming → one QSS file, token-driven.
4. Missing keyboard shortcuts → implement + document.
5. No undo support → QUndoStack for edits.
6. Ignoring High DPI → enable HighDpiScaling + HighDpiPixmaps at startup.

## ArchPilot usage notes
- Canonical theme = the app's civil-engineering token module; upstream colors are advisory.
- Long FreeCAD builds/syncs run in a QThread with an indeterminate progress dialog, not
  `QApplication.processEvents()` on the main thread.
- AI keys never displayed; settings uses password echo.
