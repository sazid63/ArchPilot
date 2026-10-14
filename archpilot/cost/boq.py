"""Bill of Quantities: measured quantities × rates, with honest handling of missing data.

The central rule: **an item with no usable rate contributes nothing to the total and is
reported explicitly.** It is never priced at zero. A bill that is missing rates is
"incomplete", and every total is labelled as excluding the unpriced items.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from archpilot.cost.catalogue import ILLUSTRATIVE_NOTE, RateCatalogue
from archpilot.measure.engine import MeasurementResult, QuantityLine

SECTION_TITLES = {
    "concrete": "A. Concrete",
    "formwork": "B. Formwork",
    "masonry": "C. Masonry and walls",
    "openings": "D. Doors and windows",
    "finishes": "E. Finishes",
}
SECTION_ORDER = list(SECTION_TITLES)

STATUS_PRICED = "priced"
STATUS_NO_RATE = "no rate for this item"
STATUS_UNIT_MISMATCH = "rate exists but in a different unit"


@dataclass(frozen=True)
class WastePolicy:
    """Waste/allowance percentages applied on top of measured (net) quantities."""

    default_pct: float = 5.0
    by_category: dict[str, float] = field(default_factory=lambda: {
        "concrete": 5.0, "formwork": 10.0, "masonry": 5.0,
        "openings": 0.0, "finishes": 10.0,
    })

    def pct_for(self, category: str) -> float:
        return self.by_category.get(category, self.default_pct)


@dataclass
class BOQItem:
    code: str
    description: str
    unit: str
    quantity: float
    waste_pct: float
    rate: float | None
    amount: float | None
    category: str
    status: str
    element_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def quantity_with_waste(self) -> float:
        return self.quantity * (1.0 + self.waste_pct / 100.0)

    @property
    def priced_quantity(self) -> float:
        """Quantity that is actually priced — includes waste when priced."""
        return self.quantity_with_waste if self.is_priced else self.quantity

    @property
    def is_priced(self) -> bool:
        return self.rate is not None and self.amount is not None

    @property
    def section(self) -> str:
        return SECTION_TITLES.get(self.category, self.category.title())


@dataclass
class BOQ:
    items: list[BOQItem]
    currency: str
    note: str = ILLUSTRATIVE_NOTE
    contingency_pct: float = 5.0
    preliminaries_pct: float = 3.0
    tax_pct: float = 0.0
    title: str = ""
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ---- priced arithmetic -------------------------------------------------
    @property
    def priced_items(self) -> list[BOQItem]:
        return [i for i in self.items if i.is_priced]

    @property
    def unpriced_items(self) -> list[BOQItem]:
        return [i for i in self.items if not i.is_priced]

    @property
    def complete(self) -> bool:
        return not self.unpriced_items

    @property
    def measured_subtotal(self) -> float:
        """Sum of priced measured items, before waste is implied (amount already has waste)."""
        return sum(i.amount or 0.0 for i in self.priced_items)

    @property
    def contingency(self) -> float:
        return self.measured_subtotal * self.contingency_pct / 100.0

    @property
    def preliminaries(self) -> float:
        return self.measured_subtotal * self.preliminaries_pct / 100.0

    @property
    def net_total(self) -> float:
        return self.measured_subtotal + self.contingency + self.preliminaries

    @property
    def tax(self) -> float:
        return self.net_total * self.tax_pct / 100.0

    @property
    def grand_total(self) -> float:
        """Total of everything that could be priced. Only a complete bill is a full total."""
        return self.net_total + self.tax

    def sections(self) -> dict[str, list[BOQItem]]:
        """Items grouped by bill section, in the standard A–E order."""
        out: dict[str, list[BOQItem]] = {}
        for item in self.items:
            out.setdefault(item.section, []).append(item)
        ordered: dict[str, list[BOQItem]] = {}
        for category in SECTION_ORDER:
            title = SECTION_TITLES[category]
            if title in out:
                ordered[title] = out.pop(title)
        for title in sorted(out):          # anything with an unknown category, last
            ordered[title] = out[title]
        return ordered

    def by_code(self, code: str) -> BOQItem | None:
        return next((i for i in self.items if i.code == code), None)

    def totals(self) -> dict[str, float | str | bool | int]:
        return {
            "currency": self.currency,
            "measured_subtotal": self.measured_subtotal,
            "contingency_pct": self.contingency_pct,
            "contingency": self.contingency,
            "preliminaries_pct": self.preliminaries_pct,
            "preliminaries": self.preliminaries,
            "net_total": self.net_total,
            "tax_pct": self.tax_pct,
            "tax": self.tax,
            "grand_total": self.grand_total,
            "complete": self.complete,
            "unpriced_count": len(self.unpriced_items),
        }


def generate_boq(measurement: MeasurementResult, catalogue: RateCatalogue, *,
                 waste: WastePolicy | None = None,
                 contingency_pct: float = 5.0,
                 preliminaries_pct: float = 3.0,
                 tax_pct: float = 0.0,
                 title: str = "",
                 assumptions: list[str] | None = None) -> BOQ:
    policy = waste or WastePolicy()
    grouped: dict[tuple[str, str], list[QuantityLine]] = {}
    for line in measurement.lines:
        grouped.setdefault((line.code, line.unit), []).append(line)

    items: list[BOQItem] = []
    for (code, unit), lines in grouped.items():
        category = lines[0].category
        quantity = sum(line.quantity for line in lines)
        element_ids = tuple(x for line in lines for x in line.element_ids)
        notes: list[str] = []
        if len(lines) == 1:
            notes.extend(lines[0].notes)
        else:
            notes.append(f"{len(lines)} elements combined")

        rate = catalogue.rate_for(code, unit)
        if rate is not None:
            status = STATUS_PRICED
        elif catalogue.unit_mismatch(code, unit):
            status = STATUS_UNIT_MISMATCH
            existing = catalogue.item_for(code)
            if existing is not None:
                notes.append(f"catalogue rate for {code} is per {existing.unit}, "
                             f"but this item is per {unit}")
        else:
            status = STATUS_NO_RATE
        waste_pct = policy.pct_for(category)
        amount = None if rate is None else quantity * (1 + waste_pct / 100.0) * rate

        description = lines[0].description
        if len(lines) > 1:
            description = f"{description} ({len(lines)} elements)"

        items.append(BOQItem(
            code=code, description=description, unit=unit, quantity=quantity,
            waste_pct=waste_pct, rate=rate, amount=amount, category=category,
            status=status, element_ids=element_ids, notes=tuple(notes),
        ))

    warnings: list[str] = []
    items.sort(key=lambda i: (
        SECTION_ORDER.index(i.category) if i.category in SECTION_ORDER
        else len(SECTION_ORDER), i.code))
    unpriced = [i for i in items if not i.is_priced]
    for item in unpriced:
        warnings.append(f"{item.code} ({item.quantity:,.3f} {item.unit}) is unpriced — "
                        f"{item.status}.")

    return BOQ(items=items, currency=catalogue.currency, note=catalogue.note,
               contingency_pct=contingency_pct, preliminaries_pct=preliminaries_pct,
               tax_pct=tax_pct, title=title,
               assumptions=list(assumptions or []), warnings=warnings)
