"""AI operation tests. The emphasis is on what the AI is NOT allowed to do."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from archpilot.ai import ops as ops_module
from archpilot.ai.ops import (
    ALLOWED_OPS,
    AddAssumption,
    DesignRectangularBuilding,
    allowed_op_names,
    apply_operations,
    describe_spec,
    estimate_feasibility,
    extract_operations,
    rebuild_rectangle,
    validate_operations,
)
from archpilot.spec.sample import sample_single_storey
from archpilot.spec.schema import BuildingSpec


# ---------------------------------------------------------------------------
# The allow-list is closed
# ---------------------------------------------------------------------------
def test_allow_list_contains_only_expected_operations() -> None:
    assert set(allowed_op_names()) == {
        "design_rectangular_building", "set_footprint", "set_storeys",
        "set_storey_height", "set_wall_thickness", "set_slab_thickness",
        "set_roof_type", "set_units", "set_design_region", "set_project_name",
        "add_assumption", "add_open_question",
    }


@pytest.mark.parametrize("name", [
    "execute_python", "run_script", "eval", "exec", "shell", "subprocess",
    "open_file", "write_file", "__import__", "import_module", "delete_project",
    "set_rates", "DROP TABLE projects", "", "   ",
])
def test_dangerous_or_unknown_operations_are_refused(name: str) -> None:
    result = apply_operations(sample_single_storey(), [{"name": name, "code": "x"}])
    assert not result.applied
    assert result.rejected


def test_no_execution_primitives_exist_in_the_operation_module() -> None:
    """Guard against anyone later adding an eval/exec path to AI handling.

    ``re.compile`` is a regex, not code execution, so the lookbehind excludes attribute calls.
    """
    source = Path(ops_module.__file__).read_text(encoding="utf-8")
    forbidden = [r"(?<![\w.])(?:eval|exec|compile)\s*\(", r"__import__\s*\(",
                 r"os\.system", r"subprocess"]
    for pattern in forbidden:
        assert not re.search(pattern, source), f"forbidden primitive {pattern} found"


def test_operation_module_never_imports_process_or_os() -> None:
    source = Path(ops_module.__file__).read_text(encoding="utf-8")
    assert "import os" not in source
    assert "import subprocess" not in source
    assert "import sys" not in source


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("payload", [
    {"name": "design_rectangular_building", "width_m": -5, "depth_m": 9,
     "storeys": 2, "storey_height_m": 3},
    {"name": "design_rectangular_building", "width_m": 5, "depth_m": 9,
     "storeys": 0, "storey_height_m": 3},
    {"name": "design_rectangular_building", "width_m": 5, "depth_m": 9,
     "storeys": 99, "storey_height_m": 3},
    {"name": "design_rectangular_building", "width_m": 5, "depth_m": 9,
     "storeys": 2, "storey_height_m": 50},
    {"name": "design_rectangular_building", "width_m": "wide", "depth_m": 9,
     "storeys": 2, "storey_height_m": 3},
    {"name": "set_storeys", "storeys": 0},
    {"name": "set_storeys", "storeys": 500},
    {"name": "set_wall_thickness", "wall_thickness_m": 9},
    {"name": "set_wall_thickness", "wall_thickness_m": "thick"},
    {"name": "set_roof_type", "roof_type": "gold"},
    {"name": "set_units", "units": "cubits"},
    {"name": "set_design_region", "design_region": "MARS"},
    {"name": "set_project_name", "project_name": ""},
    {"name": "add_assumption", "text": "x"},
])
def test_out_of_range_operations_are_rejected_with_a_reason(payload: dict) -> None:
    result = apply_operations(sample_single_storey(), [payload])
    assert not result.applied
    assert result.rejected and result.rejected[0][1]


def test_missing_required_fields_are_rejected() -> None:
    result = apply_operations(sample_single_storey(),
                              [{"name": "design_rectangular_building",
                                "width_m": 8}])
    assert not result.applied
    assert result.rejected
    assert "depth_m" in result.rejected[0][1] or "storeys" in result.rejected[0][1]


def test_a_rejected_operation_does_not_block_a_valid_one() -> None:
    result = apply_operations(sample_single_storey(), [
        {"name": "hack_the_planet"},
        {"name": "set_storeys", "storeys": 3},
    ])
    assert len(result.rejected) == 1
    assert len(result.applied) == 1
    assert len(result.spec.storeys) == 3


def test_a_failing_operation_does_not_corrupt_the_previous_design() -> None:
    before = sample_single_storey()
    result = apply_operations(before, [
        {"name": "set_storeys", "storeys": 3},
        {"name": "design_rectangular_building", "width_m": 0, "depth_m": 0,
         "storeys": 1, "storey_height_m": 1},
    ])
    assert len(result.spec.storeys) == 3        # the good change survived
    assert result.spec.spec_version == before.spec_version


# ---------------------------------------------------------------------------
# Parsing whatever the model actually sends
# ---------------------------------------------------------------------------
def test_parses_a_fenced_json_block_with_surrounding_prose() -> None:
    reply = (
        "Here you go!\n\n```json\n"
        '{"ops": [{"name": "set_storeys", "storeys": 4}]}\n'
        "```\n\nAnything else?"
    )
    candidates, error = extract_operations(reply)
    assert not error
    assert candidates == [{"name": "set_storeys", "storeys": 4}]


def test_parses_bare_json_array() -> None:
    candidates, error = extract_operations('[{"name": "set_units", "units": "mm"}]')
    assert not error and len(candidates) == 1


def test_parses_a_single_operation_object() -> None:
    candidates, error = extract_operations('{"name": "set_roof_type", "roof_type": "flat"}')
    assert not error and candidates[0]["name"] == "set_roof_type"


def test_ignores_non_object_entries_in_a_list() -> None:
    candidates, error = extract_operations(
        '{"ops": ["nope", 42, {"name": "set_units", "units": "cm"}]}')
    assert not error
    assert candidates == [{"name": "set_units", "units": "cm"}]


@pytest.mark.parametrize("text", ["", "   ", "I'm sorry, I can't help with that."])
def test_unparseable_replies_report_a_parse_error(text: str) -> None:
    candidates, error = extract_operations(text)
    assert candidates == []
    assert error


def test_malformed_json_reports_a_parse_error() -> None:
    candidates, error = extract_operations('{"ops": [{"name": "set_storeys", }]}')
    assert candidates == []
    assert error


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------
def test_design_operation_produces_a_valid_spec() -> None:
    result = apply_operations(sample_single_storey(), [{
        "name": "design_rectangular_building",
        "width_m": 12, "depth_m": 9, "storeys": 3, "storey_height_m": 3.2,
        "wall_thickness_m": 0.25, "slab_thickness_m": 0.15,
        "doors_south": 2, "windows_per_wall": 2,
        "roof_type": "flat", "units": "m", "design_region": "BD",
        "project_name": "Villa",
    }])
    assert result.ok, result.rejected
    spec = result.spec
    assert spec.name == "Villa"
    assert len(spec.storeys) == 3
    assert spec.wall_count == 12
    assert spec.total_height_m() == pytest.approx(9.6)
    assert spec.gross_floor_area_m2() == pytest.approx(12 * 9 * 3)


def test_rebuilt_storeys_have_distinct_slab_kinds() -> None:
    spec = sample_single_storey()
    rebuilt = rebuild_rectangle(spec, width_m=8, depth_m=6, storeys=3)
    kinds = [s.kind for st in rebuilt.storeys for s in st.slabs]
    assert kinds == ["ground", "floor", "floor"]


def test_set_footprint_preserves_storeys_and_opening_counts() -> None:
    spec = sample_single_storey()
    doors_before = sum(1 for o in
                       next(w for w in spec.storeys[0].walls
                            if w.id.endswith("w-south")).openings
                       if o.kind == "door")
    result = apply_operations(spec, [{"name": "set_footprint",
                                      "width_m": 10, "depth_m": 7}])
    new = result.spec
    assert len(new.storeys) == len(spec.storeys)
    south = next(w for w in new.storeys[0].walls if w.id.endswith("w-south"))
    assert sum(1 for o in south.openings if o.kind == "door") == doors_before
    assert new.storeys[0].slabs[0].area_m2 == pytest.approx(70.0)


def test_add_assumption_is_deduplicated() -> None:
    spec = sample_single_storey()
    payload = [{"name": "add_assumption", "text": "Site is level and drained."}]
    once = apply_operations(spec, payload).spec
    twice = apply_operations(once, payload).spec
    assert once.assumptions.count("Site is level and drained.") == 1
    assert twice.assumptions.count("Site is level and drained.") == 1


def test_set_project_name_and_region() -> None:
    result = apply_operations(sample_single_storey(), [
        {"name": "set_project_name", "project_name": "Riverside House"},
        {"name": "set_design_region", "design_region": "US"},
    ])
    assert result.spec.name == "Riverside House"
    assert result.spec.design_region == "US"


def test_applied_operations_are_described_for_the_user() -> None:
    result = apply_operations(sample_single_storey(),
                              [{"name": "set_storeys", "storeys": 2}])
    assert result.applied
    assert "2 storey" in result.applied[0]


# ---------------------------------------------------------------------------
# Prompt context and feasibility
# ---------------------------------------------------------------------------
def test_describe_spec_is_plain_text_with_no_code() -> None:
    text = describe_spec(sample_single_storey())
    assert "Footprint:" in text
    assert "import " not in text
    # No shell or code constructs — this text is prompt context, never executed.
    for shell in ("&&", "$(", "`", "def ", "()"):
        assert shell not in text


def test_describe_spec_includes_open_questions() -> None:
    text = describe_spec(sample_single_storey())
    assert "Open questions" in text


def test_feasibility_flags_absurd_geometry() -> None:
    spec = sample_single_storey()
    ok, note = estimate_feasibility(spec)
    assert ok

    tall = spec.model_copy(deep=True)
    for st in tall.storeys:
        for wall in st.walls:
            wall.height_m = 12.0
    ok, note = estimate_feasibility(tall)
    assert not ok
    assert "tall" in note


def test_spec_round_trips_through_json_unchanged() -> None:
    spec = sample_single_storey()
    payload = json.loads(spec.model_dump_json())
    assert BuildingSpec.model_validate(payload) == spec


def test_design_op_defaults_are_conservative() -> None:
    op = DesignRectangularBuilding(width_m=8, depth_m=6, storeys=1,
                                   storey_height_m=3)
    assert op.wall_thickness_m == pytest.approx(0.24)
    assert op.slab_thickness_m == pytest.approx(0.15)
    assert op.doors_south == 1
    assert op.roof_type == "flat"


def test_assumption_text_is_stripped() -> None:
    op = AddAssumption(text="   spaced out   ")
    assert op.text == "spaced out"


def test_validate_operations_reports_each_failure_distinctly() -> None:
    ops = validate_operations([
        {"name": "set_storeys", "storeys": 2},
        {"name": "nonsense"},
        {"name": "set_storeys", "storeys": -1},
    ])
    assert ops[0].is_valid
    assert not ops[1].is_valid and "not permitted" in ops[1].reason
    assert not ops[2].is_valid and ops[2].reason
    assert "nonsense" not in ALLOWED_OPS
