"""Unit handling. Internal canonical length unit is the metre; FreeCAD works in millimetres."""
from __future__ import annotations

MM_PER_M = 1000.0
M3_PER_MM3 = 1e-9
M2_PER_MM2 = 1e-6

# Supported display units for lengths, with their metre factor.
LENGTH_UNITS: dict[str, float] = {"m": 1.0, "mm": 0.001, "cm": 0.01, "ft": 0.3048}

AREA_UNITS = {"m2": 1.0, "mm2": 1e-6, "cm2": 1e-4, "ft2": 0.09290304}
VOLUME_UNITS = {"m3": 1.0, "mm3": 1e-9, "cm3": 1e-6, "ft3": 0.028316846592}

KNOWN_RATE_UNITS = {"m", "m2", "m3", "mm", "cm", "ft", "nos", "no", "day", "hr",
                    "hour", "kg", "t", "tonne", "l", "litre", "bag", "lot", "lump", "roll"}


def metres_to_mm(value_m: float) -> float:
    return value_m * MM_PER_M


def mm_to_metres(value_mm: float) -> float:
    return value_mm / MM_PER_M


def mm3_to_m3(value_mm3: float) -> float:
    return value_mm3 * M3_PER_MM3


def mm2_to_m2(value_mm2: float) -> float:
    return value_mm2 * M2_PER_MM2


def convert_length(value: float, from_unit: str, to_unit: str = "m") -> float:
    if from_unit not in LENGTH_UNITS:
        raise KeyError(f"unknown length unit: {from_unit!r}")
    if to_unit not in LENGTH_UNITS:
        raise KeyError(f"unknown length unit: {to_unit!r}")
    return value * LENGTH_UNITS[from_unit] / LENGTH_UNITS[to_unit]
