"""Integration tests against the real FreeCAD binary.

These are marked ``freecad`` and are skipped — never faked — when FreeCAD is not installed.
Every assertion here is made on geometry FreeCAD actually computed, compared against numbers
derived independently from the specification.
"""
from __future__ import annotations

import textwrap
import time
from pathlib import Path

import pytest

from archpilot.cad.builder import build_model, verify_against_spec
from archpilot.cad.client import BUILD_OK_PREFIX, detect, run_script
from archpilot.cad.manifest import ROLE_RESULT
from archpilot.cad.reader import compare, sync_model
from archpilot.core.errors import FreeCADOperationFailed
from archpilot.spec.sample import sample_single_storey, sample_two_storey
from archpilot.spec.schema import BuildingSpec, Storey
from archpilot.spec.shape import rectangle_slab, rectangle_walls

pytestmark = pytest.mark.freecad

THICK, HEIGHT, SLAB_T = 0.24, 3.0, 0.15


def _run_again(workdir: Path, name: str) -> Path:
    """Give each build its own folder so tests cannot interfere with each other."""
    path = workdir / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def test_detect_finds_a_usable_freecad(freecad_install) -> None:
    assert freecad_install.executable.is_file()
    assert freecad_install.executable.name.lower().startswith("freecadcmd")
    assert "1." in freecad_install.version or "0." in freecad_install.version
    assert str(freecad_install.executable) in freecad_install.describe()


def test_detect_returns_none_for_nonsense_candidates(monkeypatch) -> None:
    monkeypatch.setenv("ARCHPILOT_FREECADCMD", r"C:\definitely\not\here\freecadcmd.exe")
    monkeypatch.setattr("archpilot.cad.client.CANDIDATES", ())
    monkeypatch.setattr("archpilot.cad.client._registry_candidates", list)
    assert detect() is None


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
def test_build_produces_a_document_and_a_full_manifest(freecad_install, freecad_workdir) -> None:
    spec = sample_single_storey()
    result = build_model(spec, freecad_install, _run_again(freecad_workdir, "single"))

    assert result.fcstd_path.is_file()
    assert result.fcstd_path.stat().st_size > 2_000
    assert result.duration_s > 0

    # Every designed element is present, and nothing else.
    designed = {obj.id for _kind, _storey, obj in spec.objects()}
    measured = {m.ap_id for m in result.results}
    assert measured == designed
    assert all(m.role == ROLE_RESULT for m in result.results)
    assert result.invalid_objects == []


def test_measured_volumes_match_the_specification(freecad_install, freecad_workdir) -> None:
    spec = sample_two_storey()
    result = build_model(spec, freecad_install, _run_again(freecad_workdir, "match"))

    assert verify_against_spec(result, spec) == []

    for st in spec.storeys:
        for slab in st.slabs:
            got = result.by_id(slab.id)
            assert got is not None
            assert got.volume_m3 == pytest.approx(slab.volume_m3, rel=0.01)

        for wall in st.walls:
            got = result.by_id(wall.id)
            assert got is not None
            # FreeCAD measures the *net* volume: the openings were really cut out.
            assert got.volume_m3 == pytest.approx(wall.net_volume_m3, rel=0.01)
            assert got.volume_m3 < wall.gross_volume_m3


def test_openings_are_cut_for_real(freecad_install, freecad_workdir) -> None:
    """The strongest test that cutouts exist: remove them and the volume grows by exactly
    the openings' volume."""
    with_openings = BuildingSpec(
        name="with", storeys=[Storey(
            id="L0", level=0, floor_height_m=HEIGHT,
            walls=rectangle_walls("L0", 8.0, 6.0, THICK, HEIGHT,
                                  doors_south=1, windows_per_wall=2),
            slabs=[rectangle_slab("L0-slab", "ground", 8.0, 6.0, SLAB_T)])])
    without = BuildingSpec(
        name="without", storeys=[Storey(
            id="L0", level=0, floor_height_m=HEIGHT,
            walls=rectangle_walls("L0", 8.0, 6.0, THICK, HEIGHT,
                                  doors_south=0, windows_per_wall=0),
            slabs=[rectangle_slab("L0-slab", "ground", 8.0, 6.0, SLAB_T)])])

    a = build_model(with_openings, freecad_install, _run_again(freecad_workdir, "cut_with"))
    b = build_model(without, freecad_install, _run_again(freecad_workdir, "cut_without"))

    opening_volume = sum(w.gross_volume_m3 - w.net_volume_m3
                         for st in with_openings.storeys for w in st.walls)
    assert opening_volume > 0
    assert b.total_volume_m3 - a.total_volume_m3 == pytest.approx(opening_volume, rel=0.01)


def test_opening_count_changes_the_model(freecad_install, freecad_workdir) -> None:
    one = BuildingSpec(name="one", storeys=[Storey(
        id="L0", level=0, floor_height_m=HEIGHT,
        walls=rectangle_walls("L0", 8.0, 6.0, THICK, HEIGHT,
                              doors_south=1, windows_per_wall=1),
        slabs=[rectangle_slab("L0-slab", "ground", 8.0, 6.0, SLAB_T)])])
    three = one.model_copy(deep=True)
    three.storeys[0].walls = rectangle_walls("L0", 8.0, 6.0, THICK, HEIGHT,
                                             doors_south=1, windows_per_wall=3)

    a = build_model(one, freecad_install, _run_again(freecad_workdir, "op1"))
    b = build_model(three, freecad_install, _run_again(freecad_workdir, "op3"))
    assert b.total_volume_m3 < a.total_volume_m3


def test_bounding_boxes_describe_the_real_geometry(freecad_install, freecad_workdir) -> None:
    spec = sample_single_storey()
    result = build_model(spec, freecad_install, _run_again(freecad_workdir, "bbox"))

    slab = result.by_id("L0-slab-ground")
    assert slab is not None
    assert slab.bbox_m[0] == pytest.approx(8.0, abs=1e-6)
    assert slab.bbox_m[1] == pytest.approx(6.0, abs=1e-6)
    assert slab.bbox_m[2] == pytest.approx(SLAB_T, abs=1e-6)

    south = result.by_id("L0-w-south")
    assert south is not None
    assert south.bbox_m[0] == pytest.approx(8.0, abs=1e-6)
    assert south.bbox_m[1] == pytest.approx(THICK, abs=1e-6)
    assert south.bbox_m[2] == pytest.approx(HEIGHT, abs=1e-6)
    # the wall sits on top of the ground slab
    assert south.bbox_min_m[2] == pytest.approx(SLAB_T, abs=1e-6)


def test_upper_storey_sits_at_its_elevation(freecad_install, freecad_workdir) -> None:
    """Regression: storeys used to stack only the clear height, so each upper storey's floor
    slab landed inside the top of the walls below it and that band was counted twice."""
    spec = sample_two_storey()
    result = build_model(spec, freecad_install, _run_again(freecad_workdir, "levels"))

    level0 = result.by_id("L0-w-south")
    level1 = result.by_id("L1-w-south")
    l1_slab = result.by_id("L1-slab-floor")
    assert level0 is not None and level1 is not None and l1_slab is not None

    # Storey 0: slab from 0, walls standing on it.
    assert level0.bbox_min_m[2] == pytest.approx(SLAB_T, abs=1e-6)
    assert level0.bbox_m[2] == pytest.approx(HEIGHT, abs=1e-6)

    # Storey 1 sits directly on top of storey 0's walls.
    assert l1_slab.bbox_min_m[2] == pytest.approx(HEIGHT + SLAB_T, abs=1e-6)
    assert level1.bbox_min_m[2] == pytest.approx(HEIGHT + SLAB_T + SLAB_T, abs=1e-6)


def test_no_slab_occupies_space_inside_a_wall(freecad_install, freecad_workdir) -> None:
    """Per-object volume checks cannot catch objects that overlap each other: two overlapping
    boxes each measure correctly and still double-count the shared volume. Positions can.

    Walls are measured along their centre lines, so perpendicular walls deliberately share
    their corner volumes (the summed wall volume then equals the true wall volume). A slab
    inside a wall, however, is never right — that was the storey-stacking defect.
    """
    spec = sample_two_storey()
    result = build_model(spec, freecad_install, _run_again(freecad_workdir, "overlap"))

    slabs = [m for m in result.results if m.ap_kind == "slab"]
    walls = [m for m in result.results if m.ap_kind == "wall"]
    assert slabs and walls

    def overlap(a, b) -> bool:
        for axis in range(3):
            if a.bbox_min_m[axis] >= b.bbox_min_m[axis] + b.bbox_m[axis] - 1e-6:
                return False
            if b.bbox_min_m[axis] >= a.bbox_min_m[axis] + a.bbox_m[axis] - 1e-6:
                return False
        return True

    clashes = [(s.ap_id, w.ap_id) for s in slabs for w in walls if overlap(s, w)]
    assert clashes == [], f"slabs share space with walls: {clashes}"

    slab_clashes = [(a.ap_id, b.ap_id) for i, a in enumerate(slabs) for b in slabs[i + 1:]
                    if overlap(a, b)]
    assert slab_clashes == [], f"slabs share space with each other: {slab_clashes}"


def test_walls_do_not_double_count_the_slab(freecad_install, freecad_workdir) -> None:
    """Regression: walls starting at z=0 overlapped the ground slab and inflated volumes."""
    spec = sample_single_storey()
    result = build_model(spec, freecad_install, _run_again(freecad_workdir, "nodouble"))

    slab_volume = result.by_id("L0-slab-ground").volume_m3
    wall_volume = sum(m.volume_m3 for m in result.results if m.ap_kind == "wall")
    expected = slab_volume + wall_volume
    assert result.total_volume_m3 == pytest.approx(expected)


def test_a_shaped_model_is_a_single_valid_solid_per_element(freecad_install,
                                                            freecad_workdir) -> None:
    spec = sample_single_storey()
    result = build_model(spec, freecad_install, _run_again(freecad_workdir, "solids"))
    for m in result.results:
        assert m.valid is True
        assert m.solids == 1, f"{m.ap_id} produced {m.solids} solids"


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
def test_sync_reads_back_the_same_geometry_as_the_build(freecad_install,
                                                        freecad_workdir) -> None:
    spec = sample_two_storey()
    workdir = _run_again(freecad_workdir, "sync")
    built = build_model(spec, freecad_install, workdir)
    synced = sync_model(freecad_install, built.fcstd_path, workdir)

    assert {m.ap_id for m in synced.results} == {m.ap_id for m in built.results}
    for original in built.results:
        again = synced.by_id(original.ap_id)
        assert again is not None
        assert again.volume_m3 == pytest.approx(original.volume_m3, rel=1e-6)


def test_sync_of_an_unchanged_model_reports_no_drift(freecad_install,
                                                     freecad_workdir) -> None:
    spec = sample_two_storey()
    workdir = _run_again(freecad_workdir, "drift")
    built = build_model(spec, freecad_install, workdir)
    synced = sync_model(freecad_install, built.fcstd_path, workdir)

    notes = compare(spec, synced)
    assert notes == [], notes
    assert synced.external == []


def test_sync_detects_an_object_added_outside_archpilot(freecad_install,
                                                         freecad_workdir) -> None:
    spec = sample_single_storey()
    workdir = _run_again(freecad_workdir, "external")
    built = build_model(spec, freecad_install, workdir)

    # Add a plain box with no ArchPilot tags — as a user editing in FreeCAD would.
    patch = workdir / "add_box.py"
    patch.write_text(textwrap.dedent(f'''
        import FreeCAD
        doc = FreeCAD.openDocument(r"{built.fcstd_path}")
        box = doc.addObject("Part::Box", "HandMadeBox")
        box.Length, box.Width, box.Height = 1000.0, 1000.0, 1000.0
        doc.recompute()
        doc.save()
        print("ARCHPILOT_BUILD_OK 1", flush=True)
    '''), encoding="utf-8")
    outcome = run_script(freecad_install.executable, patch, timeout_s=120)
    assert outcome.ok, outcome.summary()

    synced = sync_model(freecad_install, built.fcstd_path, workdir)
    assert len(synced.external) == 1
    assert synced.external[0].label == "HandMadeBox"

    notes = compare(spec, synced)
    assert any("added to the model outside ArchPilot" in n for n in notes)
    # the external object is never folded into the measured building
    assert all(m.ap_id != "HandMadeBox" for m in synced.results)


def test_sync_detects_a_hand_edited_element(freecad_install, freecad_workdir) -> None:
    spec = sample_single_storey()
    workdir = _run_again(freecad_workdir, "edited")
    built = build_model(spec, freecad_install, workdir)

    patch = workdir / "enlarge.py"
    patch.write_text(textwrap.dedent(f'''
        import FreeCAD
        doc = FreeCAD.openDocument(r"{built.fcstd_path}")
        # A wall with openings is a Part::Cut, which has no Height. Shrink the opening cutter
        # instead: less opening cut away means more wall, so the measured wall volume grows.
        edited = False
        for o in doc.Objects:
            if getattr(o, "AP_ROLE", "") == "cutter" and hasattr(o, "Height"):
                o.Height = max(100.0, o.Height * 0.5)
                edited = True
                break
        if not edited:
            raise RuntimeError("no opening cutter was found to edit")
        doc.recompute()
        doc.save()
        print("ARCHPILOT_BUILD_OK 1", flush=True)
    '''), encoding="utf-8")
    outcome = run_script(freecad_install.executable, patch, timeout_s=120)
    assert outcome.ok, outcome.summary()

    synced = sync_model(freecad_install, built.fcstd_path, workdir)
    assert any("edited or rebuilt" in n for n in compare(spec, synced))


def test_sync_of_a_missing_file_raises(freecad_install, freecad_workdir) -> None:
    with pytest.raises(FreeCADOperationFailed):
        sync_model(freecad_install, freecad_workdir / "nope.FCStd",
                   _run_again(freecad_workdir, "missing"))


def test_design_element_deleted_from_the_model_is_reported(freecad_install,
                                                           freecad_workdir) -> None:
    spec = sample_single_storey()
    workdir = _run_again(freecad_workdir, "deleted")
    built = build_model(spec, freecad_install, workdir)

    patch = workdir / "delete_one.py"
    patch.write_text(textwrap.dedent(f'''
        import FreeCAD
        doc = FreeCAD.openDocument(r"{built.fcstd_path}")
        for o in list(doc.Objects):
            if getattr(o, "AP_ID", "") == "L0-w-north":
                doc.removeObject(o.Name)
        doc.recompute()
        doc.save()
        print("ARCHPILOT_BUILD_OK 1", flush=True)
    '''), encoding="utf-8")
    outcome = run_script(freecad_install.executable, patch, timeout_s=120)
    assert outcome.ok, outcome.summary()

    synced = sync_model(freecad_install, built.fcstd_path, workdir)
    notes = compare(spec, synced)
    assert any("L0-w-north" in n and "not in the model" in n for n in notes)


# ---------------------------------------------------------------------------
# Failure handling — the parts that must not lie about success
# ---------------------------------------------------------------------------
def test_a_failing_script_is_reported_as_a_failure_not_a_success(freecad_install,
                                                                 freecad_workdir) -> None:
    workdir = _run_again(freecad_workdir, "boom")
    script = workdir / "boom.py"
    script.write_text("raise RuntimeError('deliberate failure')\n", encoding="utf-8")
    result = run_script(freecad_install.executable, script, timeout_s=120)
    # freecadcmd may still exit 0, but the run must never be called a success.
    assert result.crashed is True
    assert not result.ok
    assert "deliberate failure" in result.summary()


def test_uncaught_freecad_exception_does_not_look_like_success(freecad_install,
                                                               freecad_workdir) -> None:
    """freecadcmd exits 0 even after an uncaught exception, so success must be explicit."""
    workdir = _run_again(freecad_workdir, "silent")

    raiser = workdir / "raiser.py"
    raiser.write_text("import FreeCAD\nraise ValueError('no sentinel printed')\n",
                      encoding="utf-8")
    raised = run_script(freecad_install.executable, raiser, timeout_s=120)
    assert not raised.ok
    assert not raised.has_sentinel(BUILD_OK_PREFIX)

    # A script that exits cleanly but prints nothing useful is also not a success.
    quiet = workdir / "quiet.py"
    quiet.write_text("print('nothing happened')", encoding="utf-8")
    quiet_result = run_script(freecad_install.executable, quiet, timeout_s=120)
    assert not quiet_result.has_sentinel(BUILD_OK_PREFIX)


def test_a_failed_build_gives_the_user_a_readable_reason(freecad_install,
                                                         freecad_workdir) -> None:
    """Regression: freecadcmd discards unflushed stdout on sys.exit, which used to leave the
    user with a completely blank error message whenever a build failed."""
    from archpilot.cad import scripts
    workdir = _run_again(freecad_workdir, "readable")
    bad_plan = {"doc_name": "ArchPilot", "objects": [{"type": "triangle", "ap_id": "x"}]}
    script = workdir / "bad_build.py"
    script.write_text(
        scripts.render_driver(bad_plan, str(workdir / "m.FCStd"),
                              str(workdir / "m.json")), encoding="utf-8")

    result = run_script(freecad_install.executable, script, timeout_s=120)
    assert not result.ok
    assert result.reported_error is True
    assert "unsupported plan object type: triangle" in result.stdout
    assert "unsupported plan object type: triangle" in result.summary()
def test_a_timeout_is_reported_as_a_timeout(freecad_install, freecad_workdir) -> None:
    workdir = _run_again(freecad_workdir, "slow")
    script = workdir / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    started = time.perf_counter()
    result = run_script(freecad_install.executable, script, timeout_s=2)
    elapsed = time.perf_counter() - started
    assert result.timed_out is True
    assert elapsed < 25
    assert "did not finish in time" in result.summary()


def test_missing_executable_is_reported_clearly(freecad_workdir) -> None:
    from archpilot.cad.client import FreeCADInstall
    from archpilot.cad.client import run_script as rs
    fake = FreeCADInstall(Path(r"C:\no\such\freecadcmd.exe"), "", "test")
    workdir = _run_again(freecad_workdir, "noexe")
    script = workdir / "anything.py"
    script.write_text("print('hi')", encoding="utf-8")
    result = rs(fake.executable, script, timeout_s=10)
    assert not result.ok
    assert "Could not start FreeCAD" in result.summary()
