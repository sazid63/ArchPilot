"""One open project, wired together: design -> model -> quantities -> bill -> disk.

The window never talks to FreeCAD, the database or the cost engine directly. It holds a
:class:`ProjectSession` and calls methods on it, which keeps the UI thin and makes the whole
product testable without starting a GUI.

The one rule this module exists to enforce: **quantities are never presented as matching the
design unless the model was actually rebuilt after the last design change.** Every session
carries a fingerprint of the spec it last built; if the spec later differs, the session reports
itself as stale and the UI must say so.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from archpilot.ai import ops
from archpilot.ai.providers import Proposal, ProviderConfig, load_config, propose
from archpilot.cad.builder import BuildResult, build_model, verify_against_spec
from archpilot.cad.client import FreeCADInstall, detect
from archpilot.cad.reader import SyncResult, compare, sync_model
from archpilot.core.errors import (
    ArchPilotError,
    FreeCADNotFound,
    FreeCADOperationFailed,
    StorageError,
)
from archpilot.core.paths import exports_dir, work_dir
from archpilot.cost.boq import BOQ, WastePolicy, generate_boq
from archpilot.cost.catalogue import RateCatalogue, default_catalogue, from_payload, to_payload
from archpilot.cost.reports import (
    boq_to_csv,
    boq_to_pdf_bytes,
    boq_to_xlsx_bytes,
    quantities_to_csv,
    quantities_to_pdf_bytes,
    quantities_to_xlsx_bytes,
    write_bytes,
    write_text,
)
from archpilot.measure.engine import MeasurementResult, measure
from archpilot.spec.sample import sample_single_storey
from archpilot.spec.schema import BuildingSpec
from archpilot.storage.db import KIND_RATES, Database

DEFAULT_TIMEOUT_S = 180


def spec_fingerprint(spec: BuildingSpec) -> str:
    """A stable fingerprint of the geometry-bearing content of a design.

    Deliberately excludes the project name: renaming a project must not make its quantities
    look out of date.
    """
    payload = spec.model_dump(exclude={"name"}, exclude_none=True)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class ProjectSession:
    """A design, its saved state, and the last model built from it."""

    spec: BuildingSpec
    catalogue: RateCatalogue
    db: Database
    project_id: str | None = None
    workdir: Path | None = None
    build: BuildResult | None = None
    measurement: MeasurementResult | None = None
    boq: BOQ | None = None
    sync: SyncResult | None = None
    warnings: list[str] = field(default_factory=list)
    #: Fingerprint of the spec that produced ``build``. None means nothing has been built.
    built_fingerprint: str | None = None
    #: Warnings carry across a reload, so the bill never loses its caveats.
    saved_boq_note: str = ""

    # ---- construction ------------------------------------------------------
    @classmethod
    def new(cls, db: Database, spec: BuildingSpec | None = None, *,
            region: str | None = None, project_id: str | None = None,
            workdir: Path | None = None) -> "ProjectSession":
        design = spec or sample_single_storey()
        chosen_region = region or design.design_region or "BD"
        catalogue = default_catalogue(chosen_region)
        return cls(spec=design, catalogue=catalogue, db=db, project_id=project_id,
                   workdir=workdir or _workdir_for(project_id))

    @classmethod
    def open(cls, db: Database, project_id: str,
             workdir: Path | None = None) -> "ProjectSession":
        """Reopen a saved project. Raises StorageError with a readable reason if it is broken."""
        row = db.get_project(project_id)
        if row is None:
            raise StorageError("That project no longer exists.")
        spec_json, rates_json, _revision = db.load_bundle(project_id)
        if spec_json is None or rates_json is None:
            raise StorageError(
                f"'{row.display_name}' has no saved design yet. Open it and press "
                f"Build the 3D model to create one.")
        try:
            spec = BuildingSpec.model_validate(spec_json)
        except Exception as e:
            raise StorageError(f"The saved design for '{row.display_name}' could not be "
                               f"read: {e}") from e
        catalogue = from_payload(rates_json)
        return cls(spec=spec, catalogue=catalogue, db=db, project_id=project_id,
                   workdir=workdir or _workdir_for(project_id))

    # ---- state -------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def is_stale(self) -> bool:
        """True when the design changed after the last model build.

        While stale, measured quantities describe an older design and must be labelled so.
        """
        if self.build is None:
            return False
        return self.built_fingerprint != spec_fingerprint(self.spec)

    @property
    def has_model(self) -> bool:
        return self.build is not None and self.build.fcstd_path.is_file()

    @property
    def model_path(self) -> Path | None:
        return self.build.fcstd_path if self.build else None

    @property
    def quantities_are_current(self) -> bool:
        return self.measurement is not None and not self.is_stale

    @property
    def stale_notice(self) -> str:
        if not self.is_stale:
            return ""
        return ("The design has changed since the model was built. The quantities and costs "
                "shown are from the previous design — rebuild the model to update them.")

    def full_warnings(self) -> list[str]:
        out: list[str] = list(self.warnings)
        if self.measurement:
            out += [w for w in self.measurement.warnings if w not in out]
        if self.boq:
            out += [w for w in self.boq.warnings if w not in out]
        if self.is_stale:
            out.append(self.stale_notice)
        return out

    # ---- FreeCAD -----------------------------------------------------------
    def find_freecad(self) -> FreeCADInstall:
        install = detect()
        if install is None:
            raise FreeCADNotFound()
        return install

    def build_3d(self, *, timeout_s: int = DEFAULT_TIMEOUT_S) -> BuildResult:
        """Build the model, verify it against the design, then measure and price it.

        Raises a typed ArchPilotError on any failure; the session is left with no build so the
        UI can never show quantities from a failed or partial model.
        """
        install = self.find_freecad()
        workdir = ensure_workdir(self.workdir)
        self.build = None
        self.measurement = None
        self.boq = None
        self.built_fingerprint = None

        result = build_model(self.spec, install, workdir, timeout_s=timeout_s)
        self.warnings = verify_against_spec(result, self.spec)
        self.build = result
        self.built_fingerprint = spec_fingerprint(self.spec)
        self.workdir = workdir
        self.measure()
        self.price()
        return result

    def resync(self, *, timeout_s: int = DEFAULT_TIMEOUT_S) -> SyncResult:
        """Re-read the model from disk after the user edited it in FreeCAD.

        Quantities are recomputed from what is really in the file, and anything changed,
        added or removed by hand is reported instead of quietly ignored.
        """
        if not self.has_model or self.build is None:
            raise FreeCADOperationFailed(
                "There is no model to re-read yet. Build the 3D model first.")
        install = self.find_freecad()
        result = sync_model(install, self.build.fcstd_path,
                            ensure_workdir(self.workdir), timeout_s=timeout_s)
        self.sync = result
        self.warnings = (compare(self.spec, result)
                         + verify_against_spec(
                             _build_result_from_sync(self.build, result), self.spec))
        self.build.manifest = result.manifest
        self.built_fingerprint = spec_fingerprint(self.spec)
        self.measure()
        self.price()
        return result

    # ---- quantities and cost ----------------------------------------------
    def measure(self) -> MeasurementResult:
        if self.build is None:
            raise ArchPilotError("Build the 3D model first — quantities come from the model.")
        self.measurement = measure(self.spec, self.build.manifest)
        return self.measurement

    def price(self, *, waste: WastePolicy | None = None,
              contingency_pct: float = 5.0, preliminaries_pct: float = 3.0,
              tax_pct: float = 0.0) -> BOQ:
        if self.measurement is None:
            self.measure()
        self.boq = generate_boq(self.measurement, self.catalogue, waste=waste,
                                contingency_pct=contingency_pct,
                                preliminaries_pct=preliminaries_pct, tax_pct=tax_pct,
                                title=self.spec.name,
                                assumptions=self.spec.unresolved_assumptions())
        return self.boq

    def set_rate(self, code: str, unit: str, rate: float | None,
                 description: str = "") -> None:
        """Set or clear one rate, then reprice. Clearing a rate (None) is a real action."""
        self.catalogue.add(code, unit, rate, description or "")
        if self.measurement is not None:
            self.price()

    def rate_catalogue(self) -> RateCatalogue:
        return self.catalogue

    # ---- AI ----------------------------------------------------------------
    def ai_config(self) -> ProviderConfig:
        return load_config(self.db)

    def ask_ai(self, request: str, *, config: ProviderConfig | None = None) -> Proposal:
        """Ask the assistant for operations. Nothing is applied until the caller applies it."""
        return propose(self.spec, request, config or self.ai_config())

    def apply_operations(self, candidates: list[dict[str, Any]]) -> ops.OpResult:
        """Apply validated operations to the design (the model is now out of date)."""
        result = ops.apply_operations(self.spec, candidates)
        self.spec = result.spec
        return result

    # ---- persistence -------------------------------------------------------
    def save(self, notes: str = "") -> int:
        """Save the design and its rates together under one revision. Returns the revision."""
        if self.project_id is None:
            self.project_id = self.db.create_project(
                self.spec.name or "Untitled project",
                self.spec.design_region or "", self.catalogue.currency)
        else:
            self.db.rename_project(self.project_id, self.spec.name or "Untitled project",
                                   self.spec.design_region or self.catalogue.currency)
        revision = self.db.save_bundle(self.project_id, self.spec.model_dump(),
                                       to_payload(self.catalogue), notes=notes)
        return revision

    def save_rates(self, notes: str = "") -> int:
        if self.project_id is None:
            raise StorageError("Save the project before saving rates.")
        return self.db.save_revision(self.project_id, KIND_RATES, to_payload(self.catalogue),
                                     notes=notes)

    # ---- exports -----------------------------------------------------------
    def export_boq_csv(self, path: Path | str) -> str:
        return write_text(path, boq_to_csv(self._require_boq()))

    def export_boq_xlsx(self, path: Path | str) -> str:
        return write_bytes(path, boq_to_xlsx_bytes(self._require_boq()))

    def export_boq_pdf(self, path: Path | str) -> str:
        return write_bytes(path, boq_to_pdf_bytes(self._require_boq()))

    def export_quantities_csv(self, path: Path | str) -> str:
        return write_text(path, quantities_to_csv(self._require_measurement()))

    def export_quantities_xlsx(self, path: Path | str) -> str:
        return write_bytes(path, quantities_to_xlsx_bytes(self._require_measurement(),
                                                         self.boq))

    def export_quantities_pdf(self, path: Path | str) -> str:
        return write_bytes(path, quantities_to_pdf_bytes(self._require_measurement(),
                                                        self.boq))

    def _require_boq(self) -> BOQ:
        if self.boq is None:
            raise ArchPilotError("There is nothing to export yet. Build the model first.")
        return self.boq

    def _require_measurement(self) -> MeasurementResult:
        if self.measurement is None:
            raise ArchPilotError("There is nothing to export yet. Build the model first.")
        return self.measurement

    # ---- housekeeping ------------------------------------------------------
    def close(self) -> None:
        self.db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_result_from_sync(build: BuildResult, synced: SyncResult) -> BuildResult:
    return BuildResult(fcstd_path=build.fcstd_path, manifest=synced.manifest,
                       log=build.log, duration_s=build.duration_s, plan=build.plan)


def _workdir_for(project_id: str | None) -> Path:
    return work_dir(project_id)


def ensure_workdir(workdir: Path | None) -> Path:
    """Guarantee a real, absolute working folder before FreeCAD is handed a path."""
    if workdir is None:
        return work_dir()
    target = Path(workdir)
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


def export_dir() -> Path:
    return exports_dir()
