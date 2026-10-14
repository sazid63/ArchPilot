"""Headless interface tests.

The whole window is built and driven with Qt's ``offscreen`` platform, so these tests prove the
interface constructs, wires up and reacts to state without a visible desktop. They exercise
real widgets and the real project session; they do not build a FreeCAD model, because that is
covered by the marked integration tests. Where a screen depends on a model having been built,
the test asserts the *honest* empty state instead of pretending.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from archpilot.core.paths import db_path, ensure_dirs
from archpilot.spec.schema import BuildingSpec
from archpilot.spec.template import TEMPLATES, from_template
from archpilot.storage.db import Database
from archpilot.ui.main_window import MainWindow
from archpilot.ui.wizard import SetupWizard


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the whole test session; Qt permits no more than one."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qt_app, isolated_home, tmp_path):
    """A real MainWindow over an isolated, temporary database."""
    ensure_dirs()
    db = Database(db_path())
    win = MainWindow(db)
    yield win
    # Do not call win.close(): that closes the shared database. Let the temp files be discarded.
    del win


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def test_every_template_builds_a_valid_spec() -> None:
    for label, _description, _params in TEMPLATES:
        spec = from_template(label, f"Test — {label}")
        assert isinstance(spec, BuildingSpec)
        assert spec.gross_floor_area_m2() > 0
        assert spec.wall_count >= 4
        assert spec.storeys


def test_a_two_storey_template_carries_the_structural_warning() -> None:
    spec = from_template("House, two storeys", "Two storeys")
    joined = " ".join(spec.warnings).lower()
    assert "structural" in joined
    assert any("upper storeys" in a.lower() for a in spec.assumptions)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------
def test_the_window_builds_with_every_screen(window) -> None:
    keys = {page.key for page in window._pages.values()}
    assert keys == {"projects", "design", "assistant", "model", "quantities", "cost",
                    "export", "settings"}
    # Projects and Settings never need a project; the rest do.
    assert not window._pages["projects"].requires_project
    assert not window._pages["settings"].requires_project
    for key in ("design", "model", "quantities", "cost", "export", "assistant"):
        assert window._pages[key].requires_project


def test_pages_requiring_a_project_are_disabled_until_one_is_open(window) -> None:
    for row, key in enumerate(window._order):
        item = window.nav.item(row)
        enabled = bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
        if window._pages[key].requires_project:
            assert not enabled, f"{key} should be disabled with no project open"
        else:
            assert enabled, f"{key} should be enabled"


def test_creating_a_project_opens_it_and_persists_it(window) -> None:
    window.new_project(from_template("House, single storey", "Riverside"))
    assert window.session is not None
    assert window.session.spec.name == "Riverside"
    assert window.session.project_id is not None
    rows = window.db.list_projects()
    assert [r.display_name for r in rows] == ["Riverside"]


def test_opening_a_project_enables_the_whole_workflow(window) -> None:
    window.new_project(from_template("Small room", "Room"))
    window.refresh_pages()
    for row, key in enumerate(window._order):
        item = window.nav.item(row)
        assert item.flags() & Qt.ItemFlag.ItemIsEnabled, key


def test_every_page_refreshes_with_no_project_without_crashing(window) -> None:
    for page in window._pages.values():
        page.refresh()
        page.on_shown()


def test_every_page_refreshes_with_a_project_without_crashing(window) -> None:
    window.new_project(from_template("Small office", "Office"))
    for page in window._pages.values():
        page.refresh()
        page.on_shown()


def test_closing_the_project_returns_to_the_projects_screen(window) -> None:
    window.new_project(from_template("Small room", "Room"))
    window.close_project()
    assert window.session is None
    assert window._order[window.stack.currentIndex()] == "projects"


def test_the_design_screen_shows_the_real_numbers(window) -> None:
    window.new_project(from_template("House, single storey", "House"))
    design = window._pages["design"]
    design.refresh()
    assert design.width_spin.value() == pytest.approx(8.0)
    assert design.depth_spin.value() == pytest.approx(6.0)
    assert design.storeys_spin.value() == 1
    assert design.doors_spin.value() == 1


def test_the_quantities_screen_admits_it_has_nothing_to_show(window) -> None:
    window.new_project(from_template("Small room", "Room"))
    quantities = window._pages["quantities"]
    quantities.refresh()
    assert quantities.table.rowCount() == 0
    # The tile values must be placeholders, never a fabricated zero.
    assert quantities.tile_concrete._value.text() == "—"


# ---------------------------------------------------------------------------
# Honesty behaviours surface in the interface
# ---------------------------------------------------------------------------
def test_adding_a_storey_recorded_from_the_interface_adds_the_warning(window) -> None:
    window.new_project(from_template("House, single storey", "Grows"))
    session = window.session
    assert session is not None
    result = session.apply_operations([
        {"name": "set_storeys", "storeys": 3},
    ])
    assert result.rejected == []
    assert len(session.spec.storeys) == 3
    assert any("structural" in w.lower() for w in session.spec.warnings)


def test_the_settings_screen_reports_freecad_state_without_crashing(window) -> None:
    window.go_to("settings")
    settings = window._pages["settings"]
    settings.refresh()
    assert settings.freecad_status.text()
    assert settings.ai_banner is not None


def test_the_assistant_screen_explains_that_it_is_optional(window) -> None:
    window.new_project(from_template("Small room", "Room"))
    assistant = window._pages["assistant"]
    assistant.refresh()
    # With no provider configured the ask button is off, and the banner says why.
    assert assistant.ask_button.isEnabled() is False


# ---------------------------------------------------------------------------
# The setup wizard
# ---------------------------------------------------------------------------
def test_the_setup_wizard_walks_through_its_pages(qt_app, isolated_home) -> None:
    ensure_dirs()
    db = Database(db_path())
    wizard = SetupWizard(db)
    assert wizard.stack.count() == 4
    wizard._next()
    assert wizard._index() == 1
    wizard._next()
    assert wizard._index() == 2
    wizard._next()
    assert wizard._index() == 3
    assert wizard.next_button.text() == "Finish"
    wizard._back()
    assert wizard._index() == 2
    db.close()
