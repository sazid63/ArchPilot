"""The project session: the layer the window actually drives.

Two things are worth testing harder than the rest:

* **staleness** — changing the design must immediately make old quantities look out of date,
  because presenting a bill for a superseded design is the most damaging mistake this app
  could make; and
* **failure containment** — a failed build must leave *no* quantities behind, so the window can
  never show numbers from a model that was not built.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from archpilot.ai import ops
from archpilot.core.errors import ArchPilotError, FreeCADOperationFailed, StorageError
from archpilot.project import ProjectSession, ensure_workdir, spec_fingerprint
from archpilot.spec.sample import sample_single_storey, sample_two_storey
from archpilot.storage.db import KIND_RATES, KIND_SPEC, Database


@pytest.fixture()
def db(tmp_path: Path, isolated_home: Path) -> Database:
    database = Database(tmp_path / "projects.db")
    yield database
    database.close()


@pytest.fixture()
def session(db: Database, tmp_path: Path, isolated_home: Path) -> ProjectSession:
    return ProjectSession.new(db, sample_single_storey(), workdir=tmp_path / "work")


# ---------------------------------------------------------------------------
# Fingerprinting and staleness (no FreeCAD needed)
# ---------------------------------------------------------------------------
def test_the_fingerprint_ignores_the_project_name() -> None:
    spec = sample_single_storey()
    renamed = spec.model_copy(update={"name": "A different name"})
    assert spec_fingerprint(spec) == spec_fingerprint(renamed)


def test_the_fingerprint_changes_when_geometry_changes() -> None:
    spec = sample_single_storey()
    taller = ops.apply_operations(spec, [{"name": "set_storey_height",
                                          "storey_height_m": 3.6}]).spec
    assert spec_fingerprint(spec) != spec_fingerprint(taller)


def test_a_session_with_no_model_is_not_stale_session(session: ProjectSession) -> None:
    assert session.build is None
    assert session.is_stale is False
    assert session.has_model is False
    assert session.quantities_are_current is False
    assert session.stale_notice == ""


def test_editing_the_design_after_a_build_makes_the_quantities_stale(
        session: ProjectSession) -> None:
    """The core safety rule, tested without FreeCAD by stamping a fingerprint by hand."""
    session.built_fingerprint = spec_fingerprint(session.spec)
    session.build = _fake_build(session.workdir)
    assert session.is_stale is False

    session.apply_operations([{"name": "set_storeys", "storeys": 2}])
    assert session.is_stale is True
    assert "design has changed" in session.stale_notice
    assert session.stale_notice in session.full_warnings()
    assert session.quantities_are_current is False


def test_renaming_the_project_does_not_make_it_stale(session: ProjectSession) -> None:
    session.built_fingerprint = spec_fingerprint(session.spec)
    session.build = _fake_build(session.workdir)
    session.apply_operations([{"name": "set_project_name", "project_name": "New name"}])
    assert session.is_stale is False


def _fake_build(workdir: Path):
    from archpilot.cad.builder import BuildResult
    target = ensure_workdir(workdir) / "model.FCStd"
    target.write_bytes(b"pretend")
    return BuildResult(fcstd_path=target, manifest=[], log="", duration_s=0.0, plan={})


# ---------------------------------------------------------------------------
# Refusing to work without a model
# ---------------------------------------------------------------------------
def test_quantities_cannot_be_produced_without_a_model(session: ProjectSession) -> None:
    with pytest.raises(ArchPilotError, match="Build the 3D model first"):
        session.measure()


def test_exports_before_a_build_are_refused_with_a_readable_reason(
        session: ProjectSession, tmp_path: Path) -> None:
    with pytest.raises(ArchPilotError, match="nothing to export"):
        session.export_boq_csv(tmp_path / "boq.csv")
    with pytest.raises(ArchPilotError, match="nothing to export"):
        session.export_quantities_pdf(tmp_path / "q.pdf")


def test_resync_without_a_model_is_refused(session: ProjectSession) -> None:
    with pytest.raises(FreeCADOperationFailed, match="no model to re-read"):
        session.resync()


def test_saving_rates_without_a_saved_project_is_refused(session: ProjectSession) -> None:
    with pytest.raises(StorageError, match="Save the project"):
        session.save_rates()


# ---------------------------------------------------------------------------
# Saving and reopening
# ---------------------------------------------------------------------------
def test_saving_creates_a_project_and_a_revision(db: Database,
                                                 session: ProjectSession) -> None:
    assert session.project_id is None
    revision = session.save(notes="first save")
    assert revision == 1
    assert session.project_id is not None
    row = db.get_project(session.project_id)
    assert row is not None
    assert row.name == session.spec.name
    assert row.region == session.spec.design_region


def test_a_saved_project_reopens_identically(db: Database, tmp_path: Path,
                                             isolated_home: Path) -> None:
    session = ProjectSession.new(db, sample_two_storey(), workdir=tmp_path / "work")
    session.set_rate("CONC.SLAB.GROUND", "m3", 321.5)
    session.save()

    reopened = ProjectSession.open(db, session.project_id, workdir=tmp_path / "work2")
    assert reopened.spec == session.spec
    assert reopened.catalogue.rate_for("CONC.SLAB.GROUND", "m3") == 321.5
    assert reopened.project_id == session.project_id


def test_saving_twice_keeps_both_revisions(db: Database,
                                           session: ProjectSession) -> None:
    session.save()
    session.apply_operations([{"name": "set_storeys", "storeys": 3}])
    assert session.save(notes="three storeys") == 2
    specs = db.list_revisions(session.project_id, KIND_SPEC)
    rates = db.list_revisions(session.project_id, KIND_RATES)
    assert [r.revision for r in specs] == [2, 1]
    assert [r.revision for r in rates] == [2, 1]
    assert len(db.read_revision(session.project_id, KIND_SPEC, 1)["storeys"]) == 1


def test_opening_a_project_with_no_saved_design_explains_itself(db: Database,
                                                                tmp_path: Path,
                                                                isolated_home: Path) -> None:
    pid = db.create_project("Empty")
    with pytest.raises(StorageError, match="no saved design"):
        ProjectSession.open(db, pid, workdir=tmp_path / "w")


def test_opening_a_project_that_does_not_exist_is_a_readable_error(
        db: Database, tmp_path: Path, isolated_home: Path) -> None:
    with pytest.raises(StorageError, match="no longer exists"):
        ProjectSession.open(db, "not-a-real-id", workdir=tmp_path / "w")


def test_a_corrupt_saved_design_is_reported_not_crashed(db: Database, tmp_path: Path,
                                                        isolated_home: Path) -> None:
    pid = db.create_project("Broken")
    db.save_revision(pid, KIND_SPEC, {"spec_version": 1, "name": "x"})   # no storeys
    db.save_revision(pid, KIND_RATES, {"region": "BD"})
    with pytest.raises(StorageError, match="could not be read"):
        ProjectSession.open(db, pid, workdir=tmp_path / "w")


def test_setting_a_rate_clears_it_honestly(session: ProjectSession) -> None:
    session.set_rate("CONC.SLAB.GROUND", "m3", 100.0)
    assert session.catalogue.rate_for("CONC.SLAB.GROUND", "m3") == 100.0
    session.set_rate("CONC.SLAB.GROUND", "m3", None)
    assert session.catalogue.rate_for("CONC.SLAB.GROUND", "m3") is None


# ---------------------------------------------------------------------------
# The design-changing path through the AI operations
# ---------------------------------------------------------------------------
def test_applying_operations_updates_the_design_and_can_be_rejected(session: ProjectSession
                                                                    ) -> None:
    result = session.apply_operations([{"name": "set_storeys", "storeys": 3},
                                       {"name": "run_python", "code": "print(1)"}])
    assert [op["name"] for op in []] == []          # no operations are executed
    assert len(session.spec.storeys) == 3
    assert any("run_python" in name for name, _reason in result.rejected)


def test_an_unbuildable_ai_edit_is_refused_and_the_design_is_untouched(
        session: ProjectSession) -> None:
    before = session.spec
    result = session.apply_operations([{"name": "set_storeys", "storeys": 99}])
    assert result.rejected
    assert session.spec == before


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_ensure_workdir_creates_and_resolves_the_folder(tmp_path: Path) -> None:
    target = ensure_workdir(tmp_path / "some" / "nested")
    assert target.is_dir()
    assert target.is_absolute()
