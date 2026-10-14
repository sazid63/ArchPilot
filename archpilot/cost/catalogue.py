"""Rate catalogues: the link between measured quantities and money.

Two rules this module exists to enforce:

1. **A missing rate never becomes zero.** ``RateCatalogue.rate_for`` returns ``None`` when a
   code or unit is unknown, and callers must treat that as *unpriced*, not *free*.
2. **Units must match.** A rate stored per ``m3`` will not be used to price an ``m2``
   quantity. The mismatch is reported instead of being rounded away.

The seeded rates are ILLUSTRATIVE placeholders so a first estimate can be produced without
any external data. They are not market prices and must be replaced for real work.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

ILLUSTRATIVE_NOTE = (
    "Rates are illustrative placeholders, not market prices. Replace them with your own "
    "quotations or a regional schedule before relying on any total."
)


@dataclass(frozen=True)
class RateItem:
    code: str
    unit: str
    rate: float | None
    description: str = ""
    source: str = "seeded"

    @property
    def is_set(self) -> bool:
        return self.rate is not None

    def as_row(self) -> dict[str, str]:
        return {"code": self.code, "unit": self.unit,
                "rate": "" if self.rate is None else f"{self.rate:g}",
                "description": self.description, "source": self.source}


@dataclass
class RateCatalogue:
    region: str
    name: str
    currency: str
    items: dict[str, RateItem] = field(default_factory=dict)
    note: str = ILLUSTRATIVE_NOTE

    def add(self, code: str, unit: str, rate: float | None, description: str = "",
            source: str = "seeded") -> None:
        self.items[code] = RateItem(code, unit, rate, description, source)

    def rate_for(self, code: str, unit: str) -> float | None:
        """The rate to use, or None. None means 'unpriced' — never treat it as zero."""
        item = self.items.get(code)
        if item is None or item.rate is None:
            return None
        if item.unit.strip().lower() != unit.strip().lower():
            return None
        return item.rate

    def item_for(self, code: str) -> RateItem | None:
        return self.items.get(code)

    def unit_mismatch(self, code: str, unit: str) -> bool:
        item = self.items.get(code)
        return item is not None and item.unit.strip().lower() != unit.strip().lower()

    @property
    def currencies(self) -> str:
        return self.currency

    def rows(self) -> list[dict[str, str]]:
        return [self.items[k].as_row() for k in sorted(self.items)]


# ---------------------------------------------------------------------------
# Seeded, illustrative regional catalogues
# ---------------------------------------------------------------------------
_BD = {
    "CONC.SLAB.GROUND": ("m3", 12_000, "Ground slab concrete, incl. placing"),
    "CONC.SLAB.FLOOR": ("m3", 13_500, "Suspended floor slab concrete"),
    "CONC.SLAB.ROOF": ("m3", 13_500, "Roof slab concrete"),
    "FORM.SLAB.SOFFIT": ("m2", 550, "Soffit formwork to suspended slab"),
    "FORM.SLAB.EDGE": ("m2", 450, "Edge formwork to slab"),
    "FINISH.FLOOR": ("m2", 1_200, "Floor finish, tiles incl. bedding"),
    "WALL.BRICK": ("m3", 14_000, "Brick wall in cement mortar"),
    "WALL.BLOCK": ("m3", 9_500, "Concrete block wall"),
    "WALL.CONCRETE": ("m3", 16_000, "Cast in-situ concrete wall"),
    "WALL.TIMBER": ("m3", 55_000, "Timber stud partition"),
    "WALL.STEEL": ("m3", 95_000, "Steel framed partition"),
    "WALL.GLASS": ("m3", 70_000, "Glazed curtain walling"),
    "WALL.OTHER": ("m3", 14_000, "Wall (unspecified material)"),
    "FINISH.WALL.PLASTER": ("m2", 350, "Cement plaster to wall, both faces"),
    "OPENING.DOOR": ("nos", 18_000, "Door, supply and fix incl. frame"),
    "OPENING.WINDOW": ("nos", 14_000, "Window, supply and fix incl. frame"),
}

_US = {
    "CONC.SLAB.GROUND": ("m3", 195, "Slab on grade concrete"),
    "CONC.SLAB.FLOOR": ("m3", 235, "Suspended slab concrete"),
    "CONC.SLAB.ROOF": ("m3", 235, "Roof slab concrete"),
    "FORM.SLAB.SOFFIT": ("m2", 28, "Slab soffit formwork"),
    "FORM.SLAB.EDGE": ("m2", 22, "Slab edge formwork"),
    "FINISH.FLOOR": ("m2", 48, "Floor finish"),
    "WALL.BRICK": ("m3", 430, "Brick wall"),
    "WALL.BLOCK": ("m3", 310, "CMU wall"),
    "WALL.CONCRETE": ("m3", 520, "Cast concrete wall"),
    "WALL.TIMBER": ("m3", 1_500, "Timber framed partition"),
    "WALL.STEEL": ("m3", 2_800, "Steel stud partition"),
    "WALL.GLASS": ("m3", 2_100, "Glazed partition"),
    "WALL.OTHER": ("m3", 430, "Wall (unspecified material)"),
    "FINISH.WALL.PLASTER": ("m2", 19, "Gypsum/plaster finish to wall"),
    "OPENING.DOOR": ("nos", 620, "Door, supply and install"),
    "OPENING.WINDOW": ("nos", 480, "Window, supply and install"),
}

_IN = {
    "CONC.SLAB.GROUND": ("m3", 8_500, "Slab on grade concrete (PCC/RCC)"),
    "CONC.SLAB.FLOOR": ("m3", 9_200, "Suspended slab concrete"),
    "CONC.SLAB.ROOF": ("m3", 9_200, "Roof slab concrete"),
    "FORM.SLAB.SOFFIT": ("m2", 340, "Slab soffit formwork"),
    "FORM.SLAB.EDGE": ("m2", 280, "Slab edge formwork"),
    "FINISH.FLOOR": ("m2", 950, "Floor finish"),
    "WALL.BRICK": ("m3", 6_800, "Brick masonry"),
    "WALL.BLOCK": ("m3", 5_100, "AAC/CMU block masonry"),
    "WALL.CONCRETE": ("m3", 8_900, "RCC wall"),
    "WALL.TIMBER": ("m3", 26_000, "Timber partition"),
    "WALL.STEEL": ("m3", 45_000, "Steel partition"),
    "WALL.GLASS": ("m3", 34_000, "Glazed partition"),
    "WALL.OTHER": ("m3", 6_800, "Wall (unspecified material)"),
    "FINISH.WALL.PLASTER": ("m2", 230, "Cement plaster to wall"),
    "OPENING.DOOR": ("nos", 12_500, "Door, supply and fix"),
    "OPENING.WINDOW": ("nos", 9_500, "Window, supply and fix"),
}

REGIONS: dict[str, dict] = {
    "BD": {"name": "Bangladesh", "currency": "BDT", "items": _BD},
    "US": {"name": "United States", "currency": "USD", "items": _US},
    "IN": {"name": "India", "currency": "INR", "items": _IN},
}

DEFAULT_REGION = "BD"


def default_catalogue(region: str = DEFAULT_REGION) -> RateCatalogue:
    spec = REGIONS.get(region) or REGIONS[DEFAULT_REGION]
    cat = RateCatalogue(region=region if region in REGIONS else DEFAULT_REGION,
                        name=spec["name"], currency=spec["currency"])
    for code, (unit, rate, desc) in spec["items"].items():
        cat.add(code, unit, rate, desc)
    return cat


def blank_catalogue(region: str = DEFAULT_REGION, currency: str | None = None) -> RateCatalogue:
    """Every standard code present but with NO rate set, so unpriced items are reported as
    unpriced instead of silently priced at zero."""
    spec = REGIONS.get(region) or REGIONS[DEFAULT_REGION]
    cat = RateCatalogue(region=region, name=spec["name"],
                        currency=currency or spec["currency"],
                        note="No rates entered yet — every item is unpriced.")
    for code, (unit, _rate, desc) in spec["items"].items():
        cat.items[code] = RateItem(code, unit, None, desc, source="unset")
    return cat


# ---------------------------------------------------------------------------
# CSV exchange
# ---------------------------------------------------------------------------
CSV_COLUMNS = ("code", "unit", "rate", "description")


@dataclass
class ImportReport:
    applied: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.skipped

    def summary(self) -> str:
        parts = [f"{len(self.applied)} rate(s) updated", f"{len(self.added)} added"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return ", ".join(parts)


def export_rates_csv(catalogue: RateCatalogue) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
    writer.writeheader()
    for item in catalogue.items.values():
        writer.writerow(item.as_row())
    return buf.getvalue()


def import_rates_csv(text: str, catalogue: RateCatalogue,
                     allow_new: bool = True) -> ImportReport:
    """Merge rates from CSV. Blank rates are ignored; malformed rows are skipped with a
    reason. Existing codes are overridden only when the unit matches."""
    report = ImportReport()
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        report.skipped.append(("<file>", "no header row found"))
        return report
    headers = {h.strip().lower(): h for h in reader.fieldnames if h}
    if "code" not in headers or "rate" not in headers:
        report.skipped.append(("<file>", "CSV must have 'code' and 'rate' columns"))
        return report

    for n, row in enumerate(reader, start=2):
        code = (row.get(headers["code"]) or "").strip()
        if not code:
            continue
        raw_rate = (row.get(headers["rate"]) or "").strip()
        if raw_rate == "":
            report.skipped.append((code, f"row {n}: blank rate"))
            continue
        try:
            rate = float(raw_rate.replace(",", "").replace("_", ""))
        except ValueError:
            report.skipped.append((code, f"row {n}: not a number: {raw_rate!r}"))
            continue
        if rate < 0:
            report.skipped.append((code, f"row {n}: negative rate"))
            continue
        unit = (row.get(headers.get("unit", "unit")) or "").strip()
        desc = (row.get(headers.get("description", "description")) or "").strip()

        existing = catalogue.items.get(code)
        if existing is None:
            if not allow_new:
                report.skipped.append((code, f"row {n}: unknown code"))
                continue
            if not unit:
                report.skipped.append((code, f"row {n}: new code needs a unit"))
                continue
            catalogue.add(code, unit, rate, desc, source="imported")
            report.added.append(code)
            continue
        if unit and unit.lower() != existing.unit.lower():
            report.skipped.append(
                (code, f"row {n}: unit {unit!r} does not match catalogue unit "
                       f"{existing.unit!r} — rate not applied"))
            continue
        catalogue.add(code, existing.unit, rate,
                      desc or existing.description, source="imported")
        report.applied.append(code)
    return report


def export_rates_xlsx_bytes(catalogue: RateCatalogue) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Rates"
    ws.append([*CSV_COLUMNS, "source"])
    for item in catalogue.items.values():
        row = item.as_row()
        ws.append([row["code"], row["unit"], item.rate, row["description"], row["source"]])
    for column, width in zip("ABCDE", (22, 8, 14, 46, 12), strict=True):
        ws.column_dimensions[column].width = width
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# --------------------------------------------------------------------------
# Storage / restore of a catalogue (rates stay numeric with None for unset)
# --------------------------------------------------------------------------
def to_payload(catalogue: RateCatalogue) -> dict:
    return {
        "region": catalogue.region,
        "name": catalogue.name,
        "currency": catalogue.currency,
        "note": catalogue.note,
        "items": [
            {"code": i.code, "unit": i.unit, "rate": i.rate,
             "description": i.description, "source": i.source}
            for i in catalogue.items.values()
        ],
    }


def from_payload(data: dict) -> RateCatalogue:
    """Rebuild a catalogue from stored JSON. Unreadable rows are skipped, never invented."""
    if not isinstance(data, dict):
        raise ValueError("rate payload must be an object")
    cat = RateCatalogue(
        region=str(data.get("region", DEFAULT_REGION)),
        name=str(data.get("name", "")),
        currency=str(data.get("currency", "")),
        note=str(data.get("note", ILLUSTRATIVE_NOTE)),
    )
    for row in data.get("items", []) or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code", "")).strip()
        unit = str(row.get("unit", "")).strip()
        if not code or not unit:
            continue
        raw_rate = row.get("rate", None)
        if raw_rate in (None, ""):
            rate = None
        else:
            try:
                rate = float(raw_rate)
            except (TypeError, ValueError):
                continue
        cat.add(code, unit, rate, str(row.get("description", "")),
                str(row.get("source", "imported")))
    if not cat.name:
        cat.name = cat.region
    if not cat.currency:
        spec = REGIONS.get(cat.region)
        if spec:
            cat.currency = spec["currency"]
    return cat
