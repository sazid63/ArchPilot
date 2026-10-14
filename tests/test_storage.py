"""Persistence tests: projects, revision bundles, settings.

The important property is *consistency*: reopening a project must return the specification
and its rate catalogue from the same save, never a mix of two.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from archpilot.core.errors import StorageError
from archpilot.cost.catalogue import default_catalogue, to_payload
from archpilot.spec.sample import sample_single_storey, sample_two_storey
from archpilot.spec.schema import BuildingSpec
from archpilot.storage.db import KIND_RATES, KIND_SPEC, Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "archpilot.db")
    yield database
    database.close()


def _rates_payload(region: str = "BD") -> dict:
    return to_payload(default_catalogue(region))


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
def test_create_and_read_back_a_project(db: Database) -> None:
    pid = db.create_project("House A", "BD", "BDT", notes="first")
    row = db.get_project(pid)
    assert row is not None
    assert row.name == "House A"
    assert row.region == "BD"
    assert row.currency == "BDT"
    assert row.notes == "first"
    assert row.archived is False
    assert row.created_at and row.updated_at


def test_default_name_is_used_when_blank(db: Database) -> None:
    pid = db.create_project("")
    assert db.get_project(pid).name == "Untitled project"


def test_listing_is_newest_first(db: Database) -> None:
    first = db.create_project("First")
    second = db.create_project("Second")
    names = [p.name for p in db.list_projects()]
    assert names[0] == "Second"
    assert set(names) == {"First", "Second"}
    assert {first, second} == {p.id for p in db.list_projects()}


def test_rename_and_archive_round_trip(db: Database) -> None:
    pid = db.create_project("Old")
    db.rename_project(pid, "New", region="US", currency="USD")
    row = db.get_project(pid)
    assert (row.name, row.region, row.currency) == ("New", "US", "USD")

    db.set_archived(pid, True)
    assert db.get_project(pid).archived is True
    assert pid not in [p.id for p in db.list_projects()]
    assert pid in [p.id for p in db.list_projects(include_archived=True)]


def test_delete_removes_project_and_its_revisions(db: Database) -> None:
    pid = db.create_project("Doomed")
    db.save_bundle(pid, sample_single_storey().model_dump(), _rates_payload())
    assert db.list_revisions(pid)

    removed = db.delete_project(pid)
    assert removed >= 2
    assert db.get_project(pid) is None
    assert db.list_revisions(pid) == []


def test_deleting_an_unknown_project_reports_zero(db: Database) -> None:
    assert db.delete_project("nope") == 0


# ---------------------------------------------------------------------------
# Revision bundles
# ---------------------------------------------------------------------------
def test_bundle_saves_spec_and_rates_under_one_revision(db: Database) -> None:
    pid = db.create_project("P")
    spec = sample_two_storey()
    rev = db.save_bundle(pid, spec.model_dump(), _rates_payload("BD"))
    assert rev == 1

    spec_rows = db.list_revisions(pid, KIND_SPEC)
    rate_rows = db.list_revisions(pid, KIND_RATES)
    assert len(spec_rows) == 1 and len(rate_rows) == 1
    assert spec_rows[0].revision == rate_rows[0].revision == 1


def test_bundle_reopen_returns_a_matching_spec_and_rates(db: Database) -> None:
    pid = db.create_project("P")
    spec = sample_two_storey()
    db.save_bundle(pid, spec.model_dump(), _rates_payload("BD"))

    loaded_spec, loaded_rates, revision = db.load_bundle(pid)
    assert revision == 1
    assert BuildingSpec.model_validate(loaded_spec) == spec
    assert loaded_rates is not None
    assert any(item["code"] == "CONC.SLAB.FLOOR" for item in loaded_rates["items"])


def test_bundle_never_pairs_a_new_spec_with_old_rates(db: Database) -> None:
    pid = db.create_project("P")
    spec = sample_two_storey()
    db.save_bundle(pid, spec.model_dump(), _rates_payload("BD"))

    spec.name = "Second save"
    db.save_bundle(pid, spec.model_dump(), _rates_payload("US"))

    loaded_spec, loaded_rates, revision = db.load_bundle(pid)
    assert revision == 2
    assert loaded_spec["name"] == "Second save"
    assert loaded_rates["region"] == "US"


def test_revisions_accumulate_and_old_ones_remain_readable(db: Database) -> None:
    pid = db.create_project("P")
    for i in range(3):
        spec = sample_single_storey()
        spec.name = f"rev {i + 1}"
        db.save_bundle(pid, spec.model_dump(), _rates_payload())

    revisions = sorted(r.revision for r in db.list_revisions(pid, KIND_SPEC))
    assert revisions == [1, 2, 3]
    assert db.read_revision(pid, KIND_SPEC, 1)["name"] == "rev 1"
    assert db.read_revision(pid, KIND_SPEC, 3)["name"] == "rev 3"
    assert db.read_revision(pid, KIND_SPEC, 99) is None


def test_load_bundle_on_an_empty_project_returns_nothing(db: Database) -> None:
    pid = db.create_project("Empty")
    assert db.load_bundle(pid) == (None, None, None)


def test_saving_an_unknown_revision_kind_is_refused(db: Database) -> None:
    pid = db.create_project("P")
    with pytest.raises(StorageError):
        db.save_revision(pid, "not-a-kind", {"x": 1})


def test_saving_a_non_object_payload_is_refused(db: Database) -> None:
    pid = db.create_project("P")
    with pytest.raises(StorageError):
        db.save_revision(pid, KIND_SPEC, ["not", "an", "object"])  # type: ignore[arg-type]


def test_a_corrupt_revision_is_reported_not_silently_ignored(db: Database) -> None:
    pid = db.create_project("P")
    db.save_revision(pid, KIND_SPEC, {"name": "ok"})
    with db._tx() as c:  # deliberately corrupt the stored JSON
        c.execute("UPDATE revisions SET data = ? WHERE project_id = ?", ("{broken", pid))
    row = db.list_revisions(pid, KIND_SPEC)[0]
    with pytest.raises(StorageError):
        row.payload()


def test_revision_notes_are_stored(db: Database) -> None:
    pid = db.create_project("P")
    db.save_bundle(pid, sample_single_storey().model_dump(), _rates_payload(),
                   notes="before adding a storey")
    assert db.list_revisions(pid, KIND_SPEC)[0].notes == "before adding a storey"


def test_updated_at_moves_forward_on_save(db: Database) -> None:
    pid = db.create_project("P")
    before = db.get_project(pid).updated_at
    db.save_bundle(pid, sample_single_storey().model_dump(), _rates_payload())
    after = db.get_project(pid).updated_at
    assert after >= before


# ---------------------------------------------------------------------------
# Settings (non-secret only)
# ---------------------------------------------------------------------------
def test_settings_round_trip_and_default(db: Database) -> None:
    assert db.get_setting("missing", "fallback") == "fallback"
    db.set_setting("region", "IN")
    db.set_setting("region", "US")           # last write wins
    assert db.get_setting("region") == "US"
    assert db.all_settings() == {"region": "US"}


def test_settings_clear(db: Database) -> None:
    db.set_setting("a", "1")
    db.set_setting("b", "2")
    db.clear_settings()
    assert db.all_settings() == {}


def test_database_is_reopenable_and_data_survives(tmp_path: Path) -> None:
    path = tmp_path / "reopen.db"
    first = Database(path)
    pid = first.create_project("Persistent")
    first.save_bundle(pid, sample_two_storey().model_dump(), _rates_payload())
    first.set_setting("region", "BD")
    first.close()

    second = Database(path)
    try:
        assert second.get_project(pid).name == "Persistent"
        spec, _rates, revision = second.load_bundle(pid)
        assert revision == 1
        assert BuildingSpec.model_validate(spec) == sample_two_storey()
        assert second.get_setting("region") == "BD"
    finally:
        second.close()


def test_foreign_keys_cascade_defence_in_depth(db: Database) -> None:
    """Revisions cannot reference a project that does not exist."""
    with pytest.raises(sqlite3.IntegrityError):
        with db._tx() as c:
            c.execute("INSERT INTO revisions (project_id, kind, revision, data, "
                      "notes, created_at) VALUES (?,?,?,?,?,?)",
                      ("ghost", KIND_SPEC, 1, "{}", "", "now"))


def test_payload_is_stored_as_compact_json(db: Database) -> None:
    pid = db.create_project("P")
    db.save_bundle(pid, sample_single_storey().model_dump(), _rates_payload())
    raw = db.list_revisions(pid, KIND_SPEC)[0].data
    assert "\n" not in raw
    assert json.loads(raw)["name"].startswith("Sample")
