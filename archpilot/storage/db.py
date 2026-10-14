"""SQLite persistence: projects, revisioned design/rate bundles, non-secret settings.

Design rules:

* A project's design spec and rate catalogue are saved **together in one transaction** under
  a shared revision number, so reopening a project can never see a spec paired with rates
  from a different save.
* Revisions are append-only. They give a reversible history and a reliable test target.
* API keys are never stored here — see :mod:`archpilot.security.secrets`.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from archpilot.core.errors import StorageError
from archpilot.core.paths import db_path, get_logger

log = get_logger("storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    region      TEXT NOT NULL DEFAULT '',
    currency    TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS revisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    data        TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE(project_id, kind, revision)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revisions_lookup
    ON revisions(project_id, kind, revision DESC);
"""

KIND_SPEC = "spec"
KIND_RATES = "rates"
VALID_KINDS = (KIND_SPEC, KIND_RATES)


def _now() -> str:
    """Microsecond resolution: two saves in the same second must still sort correctly."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class ProjectRow:
    id: str
    name: str
    region: str
    currency: str
    notes: str
    created_at: str
    updated_at: str
    archived: bool

    @property
    def display_name(self) -> str:
        return self.name or "(unnamed project)"


@dataclass(frozen=True)
class RevisionRow:
    id: int
    project_id: str
    kind: str
    revision: int
    data: str
    notes: str
    created_at: str

    def payload(self) -> dict:
        try:
            parsed = json.loads(self.data)
        except json.JSONDecodeError as e:
            raise StorageError(f"revision {self.revision} of {self.kind} is corrupt") from e
        if not isinstance(parsed, dict):
            raise StorageError(f"revision {self.revision} of {self.kind} is not an object")
        return parsed


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else db_path()
        self._conn: sqlite3.Connection | None = None

    # ---- connection ----------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._open()
        return self._conn

    def _open(self) -> sqlite3.Connection:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            conn.commit()
            return conn
        except sqlite3.Error as e:
            raise StorageError(f"could not open the project database: {e}") from e

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ---- projects -------------------------------------------------------
    def create_project(self, name: str, region: str = "", currency: str = "",
                       notes: str = "") -> str:
        pid = uuid.uuid4().hex[:12]
        now = _now()
        with self._tx() as c:
            c.execute(
                "INSERT INTO projects (id, name, region, currency, notes, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (pid, name or "Untitled project", region, currency, notes, now, now))
        log.info("created project %s (%s)", pid, name)
        return pid

    def _row_to_project(self, row: sqlite3.Row) -> ProjectRow:
        return ProjectRow(id=row["id"], name=row["name"], region=row["region"],
                          currency=row["currency"], notes=row["notes"],
                          created_at=row["created_at"], updated_at=row["updated_at"],
                          archived=bool(row["archived"]))

    def list_projects(self, include_archived: bool = False) -> list[ProjectRow]:
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE archived = 0"
        # rowid breaks ties so the order is stable even for identical timestamps.
        sql += " ORDER BY updated_at DESC, rowid DESC"
        return [self._row_to_project(r) for r in self.conn.execute(sql)]

    def get_project(self, project_id: str) -> ProjectRow | None:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?",
                                (project_id,)).fetchone()
        return self._row_to_project(row) if row else None

    def rename_project(self, project_id: str, name: str, region: str | None = None,
                       currency: str | None = None, notes: str | None = None) -> None:
        fields, values = ["updated_at = ?"], [_now()]
        if name is not None:
            fields.append("name = ?")
            values.append(name)
        if region is not None:
            fields.append("region = ?")
            values.append(region)
        if currency is not None:
            fields.append("currency = ?")
            values.append(currency)
        if notes is not None:
            fields.append("notes = ?")
            values.append(notes)
        values.append(project_id)
        with self._tx() as c:
            c.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", values)

    def set_archived(self, project_id: str, archived: bool) -> None:
        with self._tx() as c:
            c.execute("UPDATE projects SET archived = ?, updated_at = ? WHERE id = ?",
                      (1 if archived else 0, _now(), project_id))

    def delete_project(self, project_id: str) -> int:
        with self._tx() as c:
            cur = c.execute("DELETE FROM revisions WHERE project_id = ?", (project_id,))
            revisions = cur.rowcount
            cur = c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            if cur.rowcount == 0:
                return 0
        log.info("deleted project %s (%d revisions)", project_id, revisions)
        return revisions

    # ---- revisions ------------------------------------------------------
    def _next_revision(self, project_id: str, kind: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 AS n FROM revisions "
            "WHERE project_id = ? AND kind = ?", (project_id, kind)).fetchone()
        return int(row["n"])

    def save_revision(self, project_id: str, kind: str, payload: dict,
                      notes: str = "", revision: int | None = None) -> int:
        if kind not in VALID_KINDS:
            raise StorageError(f"unknown revision kind: {kind!r}")
        if not isinstance(payload, dict):
            raise StorageError(f"payload for {kind} must be an object")
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._tx() as c:
            target = revision if revision is not None else self._next_revision(project_id, kind)
            c.execute(
                "INSERT INTO revisions (project_id, kind, revision, data, notes, "
                "created_at) VALUES (?,?,?,?,?,?)",
                (project_id, kind, target, data, notes, _now()))
            c.execute("UPDATE projects SET updated_at = ? WHERE id = ?",
                      (_now(), project_id))
            return target

    def save_bundle(self, project_id: str, spec: dict, rates: dict,
                    notes: str = "") -> int:
        """Save spec and rates together under one revision number, atomically."""
        spec_data = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
        rates_data = json.dumps(rates, ensure_ascii=False, separators=(",", ":"))
        with self._tx() as c:
            target = max(self._next_revision(project_id, KIND_SPEC),
                         self._next_revision(project_id, KIND_RATES))
            now = _now()
            for kind, data in ((KIND_SPEC, spec_data), (KIND_RATES, rates_data)):
                c.execute(
                    "INSERT INTO revisions (project_id, kind, revision, data, notes, "
                    "created_at) VALUES (?,?,?,?,?,?)",
                    (project_id, kind, target, data, notes, now))
            c.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
        log.info("saved bundle r%d for %s", target, project_id)
        return target

    def load_bundle(self, project_id: str) -> tuple[dict | None, dict | None, int | None]:
        """Latest spec and rates, paired by revision. Returns (spec, rates, revision)."""
        spec_row = self.conn.execute(
            "SELECT * FROM revisions WHERE project_id = ? AND kind = ? "
            "ORDER BY revision DESC LIMIT 1", (project_id, KIND_SPEC)).fetchone()
        if spec_row is None:
            return None, None, None
        spec = RevisionRow(**dict(spec_row)).payload()

        rates_row = self.conn.execute(
            "SELECT * FROM revisions WHERE project_id = ? AND kind = ? "
            "AND revision = ? LIMIT 1",
            (project_id, KIND_RATES, spec_row["revision"])).fetchone()
        rates = None if rates_row is None else RevisionRow(**dict(rates_row)).payload()
        return spec, rates, int(spec_row["revision"])

    def list_revisions(self, project_id: str, kind: str | None = None) -> list[RevisionRow]:
        if kind:
            rows = self.conn.execute(
                "SELECT * FROM revisions WHERE project_id = ? AND kind = ? "
                "ORDER BY revision DESC", (project_id, kind)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM revisions WHERE project_id = ? "
                "ORDER BY revision DESC", (project_id,)).fetchall()
        return [RevisionRow(**dict(r)) for r in rows]

    def read_revision(self, project_id: str, kind: str, revision: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM revisions WHERE project_id = ? AND kind = ? AND revision = ?",
            (project_id, kind, revision)).fetchone()
        return RevisionRow(**dict(row)).payload() if row else None

    # ---- non-secret settings -------------------------------------------
    def set_setting(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, value))

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?",
                                (key,)).fetchone()
        return row["value"] if row else default

    def all_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.conn.execute(
            "SELECT key, value FROM settings")}

    def clear_settings(self) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM settings")
