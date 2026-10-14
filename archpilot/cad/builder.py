"""Build a FreeCAD model from a validated spec, and read measured results back.

Everything crosses the interpreter boundary as files: the app writes a generated script,
FreeCAD writes an .FCStd document plus a JSON manifest of measured geometry, and the app
reads that manifest. No FreeCAD import happens inside the application process.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from archpilot.cad import scripts
from archpilot.cad.client import BUILD_OK_PREFIX, FreeCADInstall, RunResult, run_script
from archpilot.cad.manifest import MeasuredObject, read_manifest, results_only, total_volume_m3
from archpilot.core.errors import FreeCADOperationFailed, FreeCADTimeout
from archpilot.spec.schema import BuildingSpec

__all__ = ["BuildResult", "MeasuredObject", "build_model", "verify_against_spec"]


@dataclass
class BuildResult:
    fcstd_path: Path
    manifest: list[MeasuredObject]
    log: str
    duration_s: float
    plan: dict

    def by_id(self, ap_id: str) -> MeasuredObject | None:
        return next((m for m in self.manifest if m.ap_id == ap_id), None)

    @property
    def results(self) -> list[MeasuredObject]:
        return results_only(self.manifest)

    @property
    def total_volume_m3(self) -> float:
        return total_volume_m3(self.manifest)

    @property
    def invalid_objects(self) -> list[MeasuredObject]:
        return [m for m in self.manifest if not m.valid]

    @property
    def ok(self) -> bool:
        return bool(self.manifest) and not self.invalid_objects


def build_model(spec: BuildingSpec, install: FreeCADInstall, workdir: Path,
                timeout_s: int = 180, runner: Callable[..., RunResult] = run_script,
                ) -> BuildResult:
    """Generate and run the build script, then return measured geometry.

    Raises FreeCADOperationFailed / FreeCADTimeout — never returns a half-built model as if
    it were complete. ``runner`` is injectable so the error branches can be tested without
    FreeCAD, but it defaults to really running freecadcmd.
    """
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    plan = scripts.spec_to_plan(spec, doc_name="ArchPilot")
    fcstd = workdir / "model.FCStd"
    manifest_path = workdir / "manifest.json"
    script_path = workdir / "build_model.py"

    script_path.write_text(
        scripts.render_driver(plan, str(fcstd), str(manifest_path)), encoding="utf-8")

    started = time.perf_counter()
    result: RunResult = runner(install.executable, script_path, timeout_s=timeout_s)
    duration = time.perf_counter() - started

    if result.timed_out:
        raise FreeCADTimeout(
            f"FreeCAD did not finish within {timeout_s} s while building the model.")
    # Require the explicit success sentinel: freecadcmd also exits 0 after a crash.
    if not result.has_sentinel(BUILD_OK_PREFIX):
        raise FreeCADOperationFailed("FreeCAD could not build the model.",
                                     detail=result.summary())

    manifest = read_manifest(manifest_path)
    if not manifest:
        raise FreeCADOperationFailed(
            "FreeCAD built the document but produced no measurable objects.",
            detail=result.summary())
    log = (result.stdout + "\n" + result.stderr).strip()
    return BuildResult(fcstd_path=fcstd, manifest=manifest, log=log,
                       duration_s=duration, plan=plan)


def verify_against_spec(result: BuildResult, spec: BuildingSpec,
                        rel_tol: float = 0.02) -> list[str]:
    """Independent cross-check: analytic spec volumes vs FreeCAD-measured volumes.

    Returns human-readable warnings for any object whose measured volume disagrees with the
    spec beyond ``rel_tol``. An empty list means the model matches the specification.
    """
    warnings: list[str] = []
    expected: dict[str, float] = {}
    for st in spec.storeys:
        for slab in st.slabs:
            expected[slab.id] = slab.volume_m3
        for wall in st.walls:
            expected[wall.id] = wall.net_volume_m3

    measured_ids = {m.ap_id for m in result.manifest}
    if set(expected) - measured_ids:
        missing = sorted(set(expected) - measured_ids)
        warnings.append(f"Not present in the model: {', '.join(missing)}")
    if measured_ids - set(expected):
        extra = sorted(measured_ids - set(expected))
        warnings.append(f"Present in the model but not in the design: {', '.join(extra)}")

    for ap_id, exp in expected.items():
        got_obj = result.by_id(ap_id)
        if got_obj is None:
            continue
        got = got_obj.volume_m3
        if exp <= 1e-9:
            continue
        if abs(got - exp) / exp > rel_tol:
            warnings.append(
                f"{ap_id}: model volume {got:.3f} m³ differs from design "
                f"{exp:.3f} m³ by {abs(got - exp) / exp * 100:.1f}%")
    return warnings
