"""Design: the editable description of the building.

Every control edits the project's :class:`~archpilot.spec.schema.BuildingSpec` through the
validated AI operation surface — the same closed allow-list the assistant is restricted to —
so a manual edit and an AI edit cannot diverge in behaviour. A rejected edit is reported and
the design is left exactly as it was.

Editing geometry makes any existing model out of date; the page says so, and offers to rebuild,
rather than letting the Quantities and Cost screens quietly show stale numbers.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from archpilot.ai import ops
from archpilot.cost.catalogue import REGIONS
from archpilot.spec.schema import BuildingSpec
from archpilot.spec.template import DEFAULT_SLAB_THICKNESS_M, DEFAULT_WALL_THICKNESS_M
from archpilot.ui import widgets
from archpilot.ui.page import Page

UNITS_CHOICES = [("m", "Metres (m)"), ("cm", "Centimetres (cm)"),
                 ("mm", "Millimetres (mm)"), ("ft", "Feet (ft)")]
ROOF_CHOICES = [("flat", "Flat"), ("sloped", "Sloped")]


class DesignPage(Page):
    key = "design"
    nav_label = "Design"
    title = "Design"
    subtitle = "The building ArchPilot models. Change anything, then rebuild the model."
    requires_project = True

    def __init__(self, shell, parent: QWidget | None = None):
        super().__init__(shell, parent)
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        header = widgets.PageHeader(self.title, self.subtitle)
        self.save_button = widgets.button("Save project", primary=True,
                                          on_click=self._save, tooltip="Save design and rates")
        self.rebuild_button = widgets.button("Build the 3D model", on_click=self._rebuild)
        header.add_action(self.save_button)
        header.add_action(self.rebuild_button)
        root.addWidget(header)

        self.banner = widgets.Banner()
        root.addWidget(self.banner)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        root.addLayout(columns, 1)

        # -- left: the building envelope ------------------------------------
        left = QVBoxLayout()
        left.setSpacing(16)
        columns.addLayout(left, 1)

        basics = widgets.Card("Project")
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._apply_name)
        self.region_combo = QComboBox()
        for code, spec in REGIONS.items():
            self.region_combo.addItem(f"{spec['name']} ({spec['currency']})", code)
        self.region_combo.currentIndexChanged.connect(self._apply_region)
        self.units_combo = QComboBox()
        for value, label in UNITS_CHOICES:
            self.units_combo.addItem(label, value)
        self.units_combo.currentIndexChanged.connect(self._apply_units)
        basics.add(widgets.FieldRow("Project name", self.name_edit))
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(widgets.FieldRow("Prices for", self.region_combo), 2)
        row.addWidget(widgets.FieldRow("Display units", self.units_combo), 1)
        basics.add_layout(row)
        left.addWidget(basics)

        envelope = widgets.Card("Footprint and storeys")
        self.width_spin = self._length_spin(0.5, 300.0)
        self.depth_spin = self._length_spin(0.5, 300.0)
        self.storeys_spin = QSpinBox()
        self.storeys_spin.setRange(1, ops.MAX_STOREYS)
        self.storeys_spin.valueChanged.connect(self._apply_envelope)
        self.height_spin = self._length_spin(2.0, 6.0, decimals=2)
        self.thickness_spin = self._length_spin(0.05, 1.0, decimals=3)
        self.slab_spin = self._length_spin(0.05, 1.0, decimals=3)
        self.doors_spin = QSpinBox()
        self.doors_spin.setRange(0, 4)
        self.doors_spin.valueChanged.connect(self._apply_envelope)
        self.windows_spin = QSpinBox()
        self.windows_spin.setRange(0, 6)
        self.windows_spin.valueChanged.connect(self._apply_envelope)
        self.roof_combo = QComboBox()
        for value, label in ROOF_CHOICES:
            self.roof_combo.addItem(label, value)
        self.roof_combo.currentIndexChanged.connect(self._apply_roof)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.addWidget(widgets.FieldRow("Width (m)", self.width_spin), 0, 0)
        grid.addWidget(widgets.FieldRow("Depth (m)", self.depth_spin), 0, 1)
        grid.addWidget(widgets.FieldRow("Storeys", self.storeys_spin), 0, 2)
        grid.addWidget(widgets.FieldRow("Clear storey height (m)", self.height_spin), 1, 0)
        grid.addWidget(widgets.FieldRow("Wall thickness (m)", self.thickness_spin), 1, 1)
        grid.addWidget(widgets.FieldRow("Slab thickness (m)", self.slab_spin), 1, 2)
        grid.addWidget(widgets.FieldRow("Doors (south wall)", self.doors_spin), 2, 0)
        grid.addWidget(widgets.FieldRow("Windows per wall", self.windows_spin), 2, 1)
        grid.addWidget(widgets.FieldRow("Roof", self.roof_combo), 2, 2)
        envelope.add_layout(grid)
        envelope.add(widgets.Banner(
            "info", "Rectangles only",
            "ArchPilot builds rectangular footprints. For an L-shape or an irregular plan, "
            "the walls and slabs have to be placed by hand in FreeCAD — ask the assistant and "
            "it will record that as an open question rather than guess."))
        left.addWidget(envelope)

        apply_row = QHBoxLayout()
        apply_row.setSpacing(8)
        apply_row.addWidget(widgets.button("Apply footprint changes", primary=True,
                                           on_click=self._apply_envelope))
        apply_row.addWidget(QLabel(
            "Width, depth and storeys are applied together, because they rebuild the walls."))
        apply_row.addStretch(1)
        envelope.add_layout(apply_row)
        left.addStretch(1)

        # -- right: derived figures, notes ----------------------------------
        right = QVBoxLayout()
        right.setSpacing(16)
        columns.addLayout(right, 1)

        summary = widgets.Card("Summary")
        self.tiles = QGridLayout()
        self.tiles.setHorizontalSpacing(12)
        self.tiles.setVerticalSpacing(12)
        self.tile_area = widgets.StatTile("Gross floor area", "—")
        self.tile_height = widgets.StatTile("Building height", "—")
        self.tile_walls = widgets.StatTile("Walls", "—")
        self.tile_openings = widgets.StatTile("Openings", "—")
        self.tiles.addWidget(self.tile_area, 0, 0)
        self.tiles.addWidget(self.tile_height, 0, 1)
        self.tiles.addWidget(self.tile_walls, 1, 0)
        self.tiles.addWidget(self.tile_openings, 1, 1)
        summary.add_layout(self.tiles)
        right.addWidget(summary)

        self.assumptions_card = widgets.Card("Assumptions")
        self.assumptions_text = QPlainTextEdit()
        self.assumptions_text.setReadOnly(True)
        self.assumptions_text.setMaximumHeight(120)
        self.assumptions_card.add(self.assumptions_text)
        right.addWidget(self.assumptions_card)

        self.questions_card = widgets.Card("Open questions")
        self.questions_text = QPlainTextEdit()
        self.questions_text.setReadOnly(True)
        self.questions_text.setMaximumHeight(120)
        self.questions_card.add(self.questions_text)
        right.addWidget(self.questions_card)

        self.notes_card = widgets.Card("Notes")
        self.notes_text = QPlainTextEdit()
        self.notes_text.setPlaceholderText(
            "Add your own design note, e.g. 'client wants a north-facing entrance'.")
        self.notes_card.add(self.notes_text)
        self.notes_card.add(widgets.button("Add as an assumption", on_click=self._add_note))
        right.addWidget(self.notes_card)
        right.addStretch(1)

    def _length_spin(self, low: float, high: float, decimals: int = 2) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setSingleStep(0.05 if decimals >= 3 else (0.1 if decimals == 2 else 0.5))
        spin.editingFinished.connect(self._apply_envelope)
        return spin

    # -- load / save ---------------------------------------------------------
    def refresh(self) -> None:
        session = self.session
        if session is None:
            self.banner.update_message(
                "info", "No project open",
                "Open or create a project on the Projects screen to design here.")
            self._set_enabled(False)
            return
        self._set_enabled(True)
        spec = session.spec
        self._loading = True
        self.name_edit.setText(spec.name)
        _select_data(self.region_combo, spec.design_region or "BD")
        _select_data(self.units_combo, spec.units)
        _select_data(self.roof_combo, spec.roof_type)
        storey = spec.storeys[0]
        self.storeys_spin.setValue(len(spec.storeys))
        self.height_spin.setValue(storey.floor_height_m)
        wall = storey.walls[0] if storey.walls else None
        self.thickness_spin.setValue(wall.thickness_m if wall else DEFAULT_WALL_THICKNESS_M)
        slab = storey.slabs[0] if storey.slabs else None
        self.slab_spin.setValue(
            slab.thickness_m if slab else DEFAULT_SLAB_THICKNESS_M)
        self.doors_spin.setValue(_count_openings(spec, "door"))
        self.windows_spin.setValue(_windows_per_wall(spec))
        self.width_spin.setValue(_footprint_width(spec))
        self.depth_spin.setValue(_footprint_depth(spec))
        self._loading = False
        self._update_summary()

    def _set_enabled(self, on: bool) -> None:
        for widget in (self.name_edit, self.region_combo, self.units_combo, self.width_spin,
                       self.depth_spin, self.storeys_spin, self.height_spin,
                       self.thickness_spin, self.slab_spin, self.doors_spin,
                       self.windows_spin, self.roof_combo, self.save_button):
            widget.setEnabled(on)

    def _update_summary(self) -> None:
        if self.session is None:
            return
        spec = self.session.spec
        self.tile_area.set_value(f"{spec.gross_floor_area_m2():,.1f} m²")
        self.tile_height.set_value(
            f"{spec.overall_height_m():.2f} m",
            "includes slab thickness")
        self.tile_walls.set_value(str(spec.wall_count))
        self.tile_openings.set_value(
            str(spec.opening_count),
            f"{_count_openings(spec, 'door')} doors, "
            f"{_count_openings(spec, 'window')} windows")
        self.assumptions_text.setPlainText(
            "\n".join(f"•  {item}" for item in spec.assumptions) or "None recorded.")
        self.questions_text.setPlainText(
            "\n".join(f"•  {item}" for item in spec.open_questions) or "None recorded.")
        self._refresh_banner()

    def _refresh_banner(self) -> None:
        session = self.session
        if session is None:
            return
        if session.is_stale:
            self.banner.update_message(
                "warning", "The model is out of date",
                "You have changed the design since the 3D model was built. The quantities and "
                "costs still describe the previous design. Rebuild the model to update them.")
        elif session.has_model:
            self.banner.update_message(
                "ok", "Model matches the design",
                "The 3D model was built from this design and the quantities are current.")
        else:
            self.banner.update_message(
                "info", "Not modelled yet",
                "Build the 3D model to be able to measure quantities and price the work.")

    # -- edits ---------------------------------------------------------------
    def _apply_name(self) -> None:
        if self._loading or self.session is None:
            return
        name = self.name_edit.text().strip()
        if name and name != self.session.spec.name:
            self._apply([{"name": "set_project_name", "project_name": name}],
                        success="Renamed the project.")

    def _apply_region(self) -> None:
        if self._loading or self.session is None:
            return
        code = self.region_combo.currentData()
        if code and code != self.session.spec.design_region:
            self._apply([{"name": "set_design_region", "design_region": code}],
                        success="Pricing region changed. Reopen the Cost screen to load the "
                                "rates for that region.")

    def _apply_units(self) -> None:
        if self._loading or self.session is None:
            return
        value = self.units_combo.currentData()
        if value and value != self.session.spec.units:
            self._apply([{"name": "set_units", "units": value}],
                        success="Display units changed.")

    def _apply_roof(self) -> None:
        if self._loading or self.session is None:
            return
        value = self.roof_combo.currentData()
        if value and value != self.session.spec.roof_type:
            self._apply([{"name": "set_roof_type", "roof_type": value}],
                        success="Roof type changed. The roof slab itself is not modelled, so "
                                "roof items stay excluded from the quantities.")

    def _apply_envelope(self) -> None:
        """Width, depth, storeys and heights rebuild the walls, so they are applied together."""
        if self._loading or self.session is None:
            return
        spec = self.session.spec
        operations = [
            {"name": "design_rectangular_building",
             "width_m": self.width_spin.value(),
             "depth_m": self.depth_spin.value(),
             "storeys": self.storeys_spin.value(),
             "storey_height_m": self.height_spin.value(),
             "wall_thickness_m": self.thickness_spin.value(),
             "slab_thickness_m": self.slab_spin.value(),
             "doors_south": self.doors_spin.value(),
             "windows_per_wall": self.windows_spin.value(),
             "roof_type": self.roof_combo.currentData(),
             "units": self.units_combo.currentData(),
             "design_region": self.region_combo.currentData(),
             "project_name": spec.name},
        ]
        self._apply(operations, success="Footprint, storeys and openings updated.")

    def _apply(self, operations: list[dict], *, success: str) -> None:
        assert self.session is not None
        result = self.session.apply_operations(operations)
        if result.rejected:
            self.banner.update_message(
                "error", "That change was not applied",
                "; ".join(f"{name}: {reason}" for name, reason in result.rejected))
        else:
            self.banner.update_message("info", "Design updated", success)
        self._update_summary()
        self.shell.set_status(success if not result.rejected else "Change refused.")
        self.shell.refresh_pages()

    def _add_note(self) -> None:
        if self.session is None:
            return
        text = self.notes_text.toPlainText().strip()
        if not text:
            return
        self._apply([{"name": "add_assumption", "text": text}],
                    success="Note added to the assumptions.")
        self.notes_text.clear()

    def _save(self) -> None:
        if self.session is None:
            return
        try:
            revision = self.session.save(notes="Saved from the Design screen")
        except Exception as exc:
            widgets.show_error(self, "Could not save", str(exc))
            return
        self.shell.set_status(f"Saved — revision {revision}.")
        self.shell.refresh_pages()

    def _rebuild(self) -> None:
        self.shell.go_to("model")


# ---------------------------------------------------------------------------
# Small readers, so the controls always show the current design's real numbers.
# ---------------------------------------------------------------------------
def _select_data(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)


def _count_openings(spec: BuildingSpec, kind: str) -> int:
    return sum(1 for st in spec.storeys for w in st.walls for o in w.openings
               if o.kind == kind)


def _windows_per_wall(spec: BuildingSpec) -> int:
    """The most windows any single wall carries — what the 'per wall' control means."""
    counts = [sum(1 for o in w.openings if o.kind == "window")
              for st in spec.storeys for w in st.walls]
    return max(counts) if counts else 0


def _footprint_width(spec: BuildingSpec) -> float:
    slab = spec.storeys[0].slabs[0] if spec.storeys[0].slabs else None
    if slab is None:
        return 6.0
    xs = [x for x, _y in slab.outline]
    return max(xs) - min(xs)


def _footprint_depth(spec: BuildingSpec) -> float:
    slab = spec.storeys[0].slabs[0] if spec.storeys[0].slabs else None
    if slab is None:
        return 6.0
    ys = [y for _x, y in slab.outline]
    return max(ys) - min(ys)
