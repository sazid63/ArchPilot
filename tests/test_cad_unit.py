"""App-side CAD logic that must be provable without FreeCAD running.

Two things are covered here that a real-FreeCAD test cannot cover as sharply:

* the *generated script* is correct Python and embeds Windows paths safely (a backslash bug
  here used to corrupt every build silently), and
* every failure branch of ``build_model`` / ``sync_model`` — timeout, crash, missing or
  malformed manifest — raises instead of pretending to have succeeded.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from archpilot.cad import scripts
from archpilot.cad.builder import build_model, verify_against_spec
from archpilot.cad.client import (
    BUILD_OK_PREFIX,
    ERROR_PREFIX,
    PROBE_OK_PREFIX,
    SYNC_OK_PREFIX,
    FreeCADInstall,
    RunResult,
    candidate_paths,
)
from archpilot.cad.manifest import (
    ROLE_CUTTER,
    ROLE_RESULT,
    MeasuredObject,
    ObjectRecord,
    read_manifest,
    read_sync_document,
    results_only,
    total_volume_m3,
)
from archpilot.cad.reader import compare, render_sync, sync_model
from archpilot.core.errors import FreeCADOperationFailed, FreeCADTimeout
from archpilot.spec.sample import sample_single_storey, sample_two_storey

INSTALL = FreeCADInstall(Path("freecadcmd.exe"), "FreeCAD 1.1.4", "test")

# The leading ``\1`` is deliberate: it is an octal escape in a non-raw literal, which is exactly
# the corruption the tests below guard against. The space checks that paths with spaces survive.
WIN_PATH = r"C:\site plans\1_floor\model.FCStd"
WIN_MANIFEST = r"C:\site plans\1_floor\manifest.json"


# ---------------------------------------------------------------------------
# Generated scripts must be valid Python and must embed paths literally
# ---------------------------------------------------------------------------
def test_the_generated_build_driver_is_valid_python() -> None:
    plan = scripts.spec_to_plan(sample_single_storey())
    source = scripts.render_driver(plan, WIN_PATH, WIN_MANIFEST)
    compile(source, "build_driver", "exec")        # raises if the template is broken


def test_the_generated_sync_driver_is_valid_python() -> None:
    compile(render_sync(WIN_PATH, WIN_MANIFEST), "sync_driver", "exec")


def test_windows_paths_are_embedded_without_escape_corruption() -> None:
    """Regression: ``"C:\\1_..."`` inside a plain string literal becomes a control character,
    so the driver looked for a file that did not exist and failed with no message."""
    source = scripts.render_driver(scripts.spec_to_plan(sample_single_storey()),
                                   WIN_PATH, WIN_MANIFEST)
    assert "\x01" not in source
    assert "\x07" not in source
    for name, expected in (("OUT_FCSTD", WIN_PATH), ("OUT_MANIFEST", WIN_MANIFEST)):
        line = next(ln for ln in source.splitlines() if ln.startswith(f"{name} = "))
        assert ast.literal_eval(line.split(" = ", 1)[1]) == expected


def test_the_sync_driver_embeds_paths_literally() -> None:
    source = render_sync(WIN_PATH, WIN_MANIFEST)
    assert "\x01" not in source
    for name, expected in (("IN_FCSTD", WIN_PATH), ("OUT_SYNC", WIN_MANIFEST)):
        line = next(ln for ln in source.splitlines() if ln.startswith(f"{name} = "))
        assert ast.literal_eval(line.split(" = ", 1)[1]) == expected


def test_a_hostile_path_cannot_break_out_of_the_generated_script() -> None:
    """A path carrying quotes, a backslash and another placeholder's text stays inert data."""
    hostile = "C:\\tmp\\a'b\"c__OUT_MANIFEST__\\x.py"
    source = scripts.render_driver(scripts.spec_to_plan(sample_single_storey()),
                                   hostile, WIN_MANIFEST)
    line = next(ln for ln in source.splitlines() if ln.startswith("OUT_FCSTD = "))
    assert ast.literal_eval(line.split(" = ", 1)[1]) == hostile
    # The document is still valid, and the embedded placeholder text was not re-substituted.
    compile(source, "driver", "exec")


def test_both_drivers_flush_before_exiting() -> None:
    """freecadcmd discards buffered stdout when a script calls sys.exit, so an unflushed
    error message would reach the user as a blank dialog."""
    build = scripts.render_driver({"doc_name": "D", "objects": []}, "o.FCStd", "m.json")
    sync = render_sync("o.FCStd", "s.json")
    for source in (build, sync):
        assert "flush=True" in source
    assert ERROR_PREFIX in build and ERROR_PREFIX in sync


def test_a_plan_object_with_an_unknown_type_triggers_a_readable_error() -> None:
    source = scripts.render_driver({"doc_name": "D", "objects": [{"type": "triangle"}]},
                                   "o.FCStd", "m.json")
    assert "nurse(\"unsupported plan object type" in source


# ---------------------------------------------------------------------------
# The plan itself (pure data)
# ---------------------------------------------------------------------------
def test_the_plan_contains_every_object_once() -> None:
    spec = sample_two_storey()
    plan = scripts.spec_to_plan(spec)
    ids = [o["ap_id"] for o in plan["objects"]]
    assert len(ids) == len(set(ids))
    assert set(ids) == {obj.id for _kind, _storey, obj in spec.objects()}


def test_walls_start_above_their_slab_to_avoid_double_counting() -> None:
    plan = scripts.spec_to_plan(sample_single_storey())
    slab = next(o for o in plan["objects"] if o["type"] == "slab")
    walls = [o for o in plan["objects"] if o["type"] == "wall"]
    assert walls and all(
        w["z_m"] == pytest.approx(slab["z_m"] + slab["thickness_m"]) for w in walls)


def test_openings_are_carried_into_the_plan() -> None:
    plan = scripts.spec_to_plan(sample_single_storey())
    openings = [o for w in plan["objects"] if w["type"] == "wall" for o in w["openings"]]
    assert openings
    assert all(set(o) == {"id", "kind", "offset_m", "width_m", "height_m", "sill_m"}
               for o in openings)


# ---------------------------------------------------------------------------
# RunResult must never call a crash a success
# ---------------------------------------------------------------------------
def test_a_traceback_marks_the_run_as_failed() -> None:
    r = RunResult(0, "Traceback (most recent call last):\nValueError: boom", "")
    assert r.crashed is True
    assert r.ok is False
    assert "unexpected error" in r.summary()


def test_freecadcmds_own_exception_report_is_understood() -> None:
    """Verified format from FreeCAD 1.1.4: an uncaught exception lands on stderr as
    ``Exception while processing file: <script> [<message>]`` and the process still exits 0."""
    r = RunResult(0, "banner only",
                  "Exception while processing file: C:/x/boom.py [deliberate failure]\n")
    assert r.returncode == 0            # the exit code says "fine"
    assert r.crashed is True            # ...but it is not
    assert r.ok is False
    assert r.freecad_exception() == "deliberate failure"
    assert "deliberate failure" in r.summary()


def test_a_missing_script_is_reported_as_a_crash() -> None:
    r = RunResult(0, "banner only",
                  "Exception while processing file: C:/x/nope.py [Unknown file]\n")
    assert r.crashed is True
    assert r.ok is False
    assert r.freecad_exception() == "Unknown file"


def test_an_import_error_is_reported_as_a_crash() -> None:
    r = RunResult(0, "banner only",
                  "Exception while processing file: C:/x/i.py "
                  "[No module named 'missing']\n")
    assert r.ok is False
    assert "No module named 'missing'" in r.summary()


def test_an_error_sentinel_marks_the_run_as_failed() -> None:
    r = RunResult(0, f"{ERROR_PREFIX} something went wrong", "")
    assert r.reported_error is True
    assert r.ok is False
    assert "something went wrong" in r.summary()


def test_a_clean_run_with_a_sentinel_is_a_success() -> None:
    r = RunResult(0, f"{BUILD_OK_PREFIX} 7", "")
    assert r.ok is True
    assert r.has_sentinel(BUILD_OK_PREFIX) is True
    assert r.sentinel_count(BUILD_OK_PREFIX) == 7


def test_sentinel_reads_the_object_count() -> None:
    assert RunResult(0, f"{SYNC_OK_PREFIX} 13", "").sentinel_count(SYNC_OK_PREFIX) == 13
    assert RunResult(0, f"{PROBE_OK_PREFIX} 1 1", "").sentinel_count(PROBE_OK_PREFIX) == 1


def test_a_missing_sentinel_reads_as_minus_one() -> None:
    r = RunResult(0, "nothing useful here", "")
    assert r.sentinel_count(BUILD_OK_PREFIX) == -1
    assert r.has_sentinel(BUILD_OK_PREFIX) is False


def test_a_timeout_is_never_a_success() -> None:
    r = RunResult(124, "", "", timed_out=True)
    assert r.ok is False
    assert "did not finish in time" in r.summary()


def test_a_launcher_error_is_reported_plainly() -> None:
    r = RunResult(1, "", "", launcher_error="Access is denied")
    assert r.ok is False
    assert r.summary() == "Could not start FreeCAD: Access is denied"


def test_empty_output_says_so_rather_than_showing_nothing() -> None:
    assert RunResult(0, "", "").summary() == "FreeCAD produced no output."
    assert RunResult(1, "   ", "").summary() == "FreeCAD produced no output."


# ---------------------------------------------------------------------------
# read_manifest
# ---------------------------------------------------------------------------
def _row(**over) -> dict:
    row = {
        "ap_id": "L0-w-south", "ap_kind": "wall", "storey": "L0", "label": "South wall",
        "role": ROLE_RESULT, "volume_m3": 1.5, "surface_area_m2": 12.0,
        "bbox_m": [4.0, 0.24, 3.0], "bbox_min_m": [0.0, 0.0, 0.15],
        "solids": 1, "valid": True, "meta": {"length_m": "4.0"},
    }
    row.update(over)
    return row


def test_read_manifest_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FreeCADOperationFailed):
        read_manifest(tmp_path / "absent.json")


def test_read_manifest_corrupt_json_is_an_error(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(FreeCADOperationFailed) as e:
        read_manifest(p)
    assert "not valid JSON" in str(e.value)


def test_read_manifest_wrong_shape_is_an_error(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text('{"objects": []}', encoding="utf-8")
    with pytest.raises(FreeCADOperationFailed) as e:
        read_manifest(p)
    assert "unexpected shape" in str(e.value)


def test_read_manifest_round_trips_a_row(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(json.dumps([_row()]), encoding="utf-8")
    got = read_manifest(p)[0]
    assert (got.ap_id, got.ap_kind, got.storey) == ("L0-w-south", "wall", "L0")
    assert got.volume_m3 == 1.5
    assert got.bbox_m == (4.0, 0.24, 3.0)
    assert got.height_m == 3.0
    assert got.meta_float("length_m") == 4.0


def test_read_manifest_tolerates_a_damaged_row(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(json.dumps([
        _row(volume_m3="not a number", bbox_m=[1.0, 2.0], bbox_min_m=None, meta=None),
        _row(ap_id="second"),
        "not a dict",
    ]), encoding="utf-8")
    rows = read_manifest(p)
    assert len(rows) == 2                      # the junk entry is skipped, not crashed on
    assert rows[0].volume_m3 == 0.0
    assert rows[0].bbox_m == (0.0, 0.0, 0.0)   # a 2-long bbox is not accepted
    assert rows[0].bbox_min_m == (0.0, 0.0, 0.0)
    assert rows[0].meta == {}
    assert rows[0].meta_float("nope", 2.5) == 2.5
    assert rows[0].meta_int("nope", 3) == 3


def test_meta_int_accepts_floaty_strings(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(json.dumps([_row(meta={"opening_count": "3.0"})]), encoding="utf-8")
    assert read_manifest(p)[0].meta_int("opening_count") == 3


def test_only_result_objects_count_towards_the_total(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(json.dumps([
        _row(volume_m3=2.0),
        _row(ap_id="cutter", role=ROLE_CUTTER, volume_m3=99.0),
    ]), encoding="utf-8")
    rows = read_manifest(p)
    assert len(results_only(rows)) == 1
    assert total_volume_m3(rows) == 2.0


# ---------------------------------------------------------------------------
# build_model error branches (a fake runner stands in for freecadcmd)
# ---------------------------------------------------------------------------
def _runner(result: RunResult, writes: dict[str, object] | None = None):
    def run(_exe, script, timeout_s=180):
        for name, payload in (writes or {}).items():
            target = Path(script).parent / name
            target.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                              encoding="utf-8")
        return result
    return run


def test_build_model_raises_a_timeout(tmp_path: Path) -> None:
    with pytest.raises(FreeCADTimeout):
        build_model(sample_single_storey(), INSTALL, tmp_path,
                    runner=_runner(RunResult(124, "", "", timed_out=True)))


def test_build_model_raises_when_freecad_crashed(tmp_path: Path) -> None:
    crashed = RunResult(0, "Traceback (most recent call last):\nValueError: x", "")
    with pytest.raises(FreeCADOperationFailed) as e:
        build_model(sample_single_storey(), INSTALL, tmp_path, runner=_runner(crashed))
    assert "unexpected error" in e.value.detail


def test_build_model_raises_when_the_sentinel_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FreeCADOperationFailed):
        build_model(sample_single_storey(), INSTALL, tmp_path,
                    runner=_runner(RunResult(0, "did nothing", "")))


def test_build_model_raises_when_no_manifest_was_written(tmp_path: Path) -> None:
    ok = RunResult(0, f"{BUILD_OK_PREFIX} 5", "")
    with pytest.raises(FreeCADOperationFailed) as e:
        build_model(sample_single_storey(), INSTALL, tmp_path, runner=_runner(ok))
    assert "measurement file" in str(e.value)


def test_build_model_raises_when_the_manifest_is_empty(tmp_path: Path) -> None:
    ok = RunResult(0, f"{BUILD_OK_PREFIX} 0", "")
    with pytest.raises(FreeCADOperationFailed) as e:
        build_model(sample_single_storey(), INSTALL, tmp_path,
                    runner=_runner(ok, {"manifest.json": []}))
    assert "no measurable objects" in str(e.value)


def test_build_model_raises_when_the_manifest_is_corrupt(tmp_path: Path) -> None:
    ok = RunResult(0, f"{BUILD_OK_PREFIX} 1", "")
    with pytest.raises(FreeCADOperationFailed):
        build_model(sample_single_storey(), INSTALL, tmp_path,
                    runner=_runner(ok, {"manifest.json": "{oops"}))


def test_build_model_succeeds_on_a_clean_run(tmp_path: Path) -> None:
    ok = RunResult(0, f"{BUILD_OK_PREFIX} 1", "")
    result = build_model(sample_single_storey(), INSTALL, tmp_path,
                         runner=_runner(ok, {"manifest.json": [_row()]}))
    assert result.ok is True
    assert result.total_volume_m3 == 1.5
    assert result.by_id("L0-w-south") is not None
    assert result.by_id("missing") is None
    assert result.plan["doc_name"] == "ArchPilot"
    assert result.fcstd_path.name == "model.FCStd"


# ---------------------------------------------------------------------------
# verify_against_spec
# ---------------------------------------------------------------------------
def _design_volume(kind: str, obj) -> float:
    """What FreeCAD is expected to measure: net volume for walls (openings really cut)."""
    return obj.net_volume_m3 if kind == "wall" else obj.volume_m3


def _design_rows(spec, override=None) -> list[dict]:
    rows = []
    for kind, storey, obj in spec.objects():
        volume = _design_volume(kind, obj)
        if override is not None:
            volume = override(obj.id, volume)
        rows.append(_row(ap_id=obj.id, ap_kind=kind, storey=storey.id, volume_m3=volume))
    return rows


def _built(spec, rows: list[dict], tmp_path: Path):
    ok = RunResult(0, f"{BUILD_OK_PREFIX} {len(rows)}", "")
    return build_model(spec, INSTALL, tmp_path, runner=_runner(ok, {"manifest.json": rows}))


def test_verify_passes_when_volumes_agree(tmp_path: Path) -> None:
    spec = sample_single_storey()
    assert verify_against_spec(_built(spec, _design_rows(spec), tmp_path), spec) == []


def test_verify_reports_a_wrong_volume(tmp_path: Path) -> None:
    spec = sample_single_storey()
    rows = _design_rows(spec, lambda oid, v: v * 3 if oid == "L0-slab-ground" else v)
    warnings = verify_against_spec(_built(spec, rows, tmp_path), spec)
    assert any("L0-slab-ground" in w for w in warnings)


def test_verify_reports_a_missing_object(tmp_path: Path) -> None:
    spec = sample_single_storey()
    rows = [r for r in _design_rows(spec) if r["ap_id"] != "L0-w-north"]
    warnings = verify_against_spec(_built(spec, rows, tmp_path), spec)
    assert any("L0-w-north" in w for w in warnings)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
def test_sync_of_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FreeCADOperationFailed):
        sync_model(INSTALL, tmp_path / "absent.FCStd", tmp_path / "w")


def test_sync_raises_when_the_run_times_out(tmp_path: Path) -> None:
    model = tmp_path / "model.FCStd"
    model.write_bytes(b"x")
    with pytest.raises(FreeCADTimeout):
        sync_model(INSTALL, model, tmp_path / "w",
                   runner=_runner(RunResult(124, "", "", timed_out=True)))


def test_sync_raises_when_there_is_no_report(tmp_path: Path) -> None:
    model = tmp_path / "model.FCStd"
    model.write_bytes(b"x")
    with pytest.raises(FreeCADOperationFailed):
        sync_model(INSTALL, model, tmp_path / "w",
                   runner=_runner(RunResult(0, "nothing", "")))


def test_sync_reads_results_and_external_objects(tmp_path: Path) -> None:
    model = tmp_path / "model.FCStd"
    model.write_bytes(b"x")
    payload = {
        "results": [_row()],
        "external": [{"name": "Box", "label": "Hand made", "type_id": "Part::Box",
                      "volume_m3": 1.0, "visible": True}],
    }
    ok = RunResult(0, f"{SYNC_OK_PREFIX} 1", "")
    result = sync_model(INSTALL, model, tmp_path / "w",
                        runner=_runner(ok, {"sync_report.json": payload}))
    assert len(result.results) == 1
    assert result.external[0].label == "Hand made"
    assert "Hand made" in result.external[0].describe()
    assert result.invalid_objects == []


def test_read_sync_document_tolerates_junk(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"results": [_row(), 5], "external": "nope"}),
                 encoding="utf-8")
    results, external = read_sync_document(p)
    assert len(results) == 1
    assert external == []


def test_read_sync_document_rejects_a_wrong_top_level_shape(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(FreeCADOperationFailed):
        read_sync_document(p)


# ---------------------------------------------------------------------------
# compare: what changed on disk
# ---------------------------------------------------------------------------
def _row_objects(rows: list[dict]) -> list[MeasuredObject]:
    from archpilot.cad.manifest import object_from_row
    return [object_from_row(r) for r in rows]


def _sync(rows: list[dict], external: list[ObjectRecord] | None = None):
    from archpilot.cad.reader import SyncResult
    return SyncResult(fcstd_path=Path("m.FCStd"), manifest=_row_objects(rows),
                      external=external or [])


def test_compare_is_silent_when_the_model_matches() -> None:
    spec = sample_single_storey()
    assert compare(spec, _sync(_design_rows(spec))) == []


def test_compare_reports_a_deleted_element() -> None:
    spec = sample_single_storey()
    rows = [r for r in _design_rows(spec) if r["ap_id"] != "L0-w-north"]
    notes = compare(spec, _sync(rows))
    assert any("L0-w-north" in n and "not in the model" in n for n in notes)


def test_compare_reports_an_element_that_left_the_design() -> None:
    spec = sample_single_storey()
    rows = [*_design_rows(spec), _row(ap_id="ghost-element", volume_m3=1.0)]
    notes = compare(spec, _sync(rows))
    assert any("ghost-element" in n and "no longer in the design" in n for n in notes)


def test_compare_reports_a_hand_edited_volume() -> None:
    spec = sample_single_storey()
    rows = _design_rows(spec, lambda oid, v: v * 2 if oid == "L0-w-south" else v)
    notes = compare(spec, _sync(rows))
    assert any("L0-w-south" in n and "edited or rebuilt" in n for n in notes)


def test_compare_reports_external_objects() -> None:
    spec = sample_single_storey()
    notes = compare(spec, _sync(_design_rows(spec),
                                [ObjectRecord("B", "Box", "Part::Box", 1.0, True)]))
    assert any("outside ArchPilot" in n for n in notes)


# ---------------------------------------------------------------------------
# FreeCAD discovery reads the registry value FreeCAD actually writes
# ---------------------------------------------------------------------------
def test_freecad_is_found_through_the_unnamed_registry_value(tmp_path: Path) -> None:
    """FreeCAD records its install directory in the *unnamed* default value of
    ``SOFTWARE\\FreeCAD<ver>``.

    Verified on FreeCAD 1.1.4, whose ``HKLM\\SOFTWARE\\FreeCAD114`` key holds ``(Default)`` and
    ``Version`` — there is no value named ``Default``. Reading the wrong value name made discovery
    fall silently back to the hard-coded candidate paths, so this is pinned with a real key.

    The key is created under HKCU (no administrator rights needed) with a distinctive name and
    removed afterwards.
    """
    if os.name != "nt":
        pytest.skip("the Windows registry is only present on Windows")
    import winreg

    key_name = "FreeCADArchPilotTest"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "freecadcmd.exe").write_bytes(b"")

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{key_name}", 0,
                            winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(tmp_path))
    try:
        found = candidate_paths()
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{key_name}")

    assert tmp_path / "bin" / "freecadcmd.exe" in found


def test_a_value_named_default_is_also_accepted(tmp_path: Path) -> None:
    """A FreeCAD build that used a value literally named ``Default`` is still found."""
    if os.name != "nt":
        pytest.skip("the Windows registry is only present on Windows")
    import winreg

    key_name = "FreeCADArchPilotTest"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "freecadcmd.exe").write_bytes(b"")

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{key_name}", 0,
                            winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "Default", 0, winreg.REG_SZ, str(tmp_path))
    try:
        found = candidate_paths()
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{key_name}")

    assert tmp_path / "bin" / "freecadcmd.exe" in found
