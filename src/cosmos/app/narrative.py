"""Turn a stored finding into the words and readings a person reads.

Everything here is assembled from the finding's own figures by fixed rules, so
the text can never say something the numbers do not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Final

CAUSE_LABELS: Final = {
    "stockout": "Stockout",
    "supply_shortfall": "Supply shortfall",
    "own_price_increase": "Price increase",
    "competitor_price_cut": "Competitor price cut",
    "rank_loss": "Search rank loss",
    "ad_budget_cut": "Ad budget cut",
    "ad_delivery_drop": "Ad delivery drop",
    "data_gap": "Missing sales data",
    "unexplained_drop": "Unexplained drop",
}
DRIVER_LABELS: Final = {
    "availability": "Availability",
    "own_price": "Your price",
    "rival_price": "Competitor price",
    "visibility": "Search position",
    "ads": "Ads",
}
PLATFORM_LABELS: Final = {"blinkit": "Blinkit", "zepto": "Zepto", "instamart": "Instamart"}

# Evidence readings that come as a before and a during: label and how to show the value.
_PAIRED: Final = {
    "availability": ("Dark stores with the products available", "percent"),
    "price_to_mrp": ("Your price, as a share of MRP", "percent"),
    "rival_price_to_mrp": ("Competitor price, as a share of MRP", "percent"),
    "organic_rank": ("Average position in search results", "rank"),
    "ad_delivery": ("Ad delivery, against a normal day", "percent"),
    "daily_budget": ("Daily ad budget", "currency"),
}
_SINGLE: Final = {
    "warehouse_stockless_share": ("Days the warehouse held no stock", "percent"),
    "po_units_lapsed": ("Units on purchase orders that never arrived", "count"),
    "budget_utilisation_during": ("Share of the budget that was spent", "percent"),
    "cities_missing": ("Cities with no sales data", "count"),
    "city_days_missing": ("City-days with no sales data", "count"),
}
# A second driver is worth mentioning once it reaches this share of the main one.
_NOTABLE_SHARE: Final = 0.2


@dataclass(frozen=True, slots=True)
class Reading:
    label: str
    unit: str  # "percent", "rank", "currency" or "count"
    before: float | None
    during: float


@dataclass(frozen=True, slots=True)
class Narrative:
    headline: str
    what_happened: str
    why: str


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform.title())


def readings(evidence: dict[str, float]) -> list[Reading]:
    found: list[Reading] = []
    for key, (label, unit) in _PAIRED.items():
        if f"{key}_during" in evidence:
            found.append(
                Reading(label, unit, evidence.get(f"{key}_before"), evidence[f"{key}_during"])
            )
    for key, (label, unit) in _SINGLE.items():
        if key in evidence:
            found.append(Reading(label, unit, None, evidence[key]))
    return found


def narrate(payload: dict[str, Any]) -> Narrative:
    cause = payload["cause"]
    evidence: dict[str, float] = payload["evidence"]
    platform = platform_label(payload["platform"])
    category = payload["category"] or "products"
    cities: list[str] | None = payload["cities"]
    where = "across every city" if cities is None else f"in {_listed(cities)}"
    products = _count(len(payload["sku_ids"]), "product")
    span = _span(date.fromisoformat(payload["start_date"]), date.fromisoformat(payload["end_date"]))

    def reading(key: str, style: str) -> str:
        return _shown(evidence.get(key), style)

    if cause in ("stockout", "supply_shortfall"):
        shelves = (
            "The share of dark stores listing them fell from"
            f" {reading('availability_before', 'percent')}"
            f" to {reading('availability_during', 'percent')} {span}."
        )
        if cause == "stockout":
            return Narrative(
                f"{products} went out of stock {where} on {platform}",
                shelves,
                "The warehouse held stock the whole time, so nothing was short upstream."
                " That points to a problem at the stores or with the listings themselves.",
            )
        lapsed = evidence.get("po_units_lapsed", 0.0)
        orders = (
            f", and {lapsed:,.0f} units on purchase orders that were due never arrived"
            if lapsed > 0
            else ""
        )
        return Narrative(
            f"{products} ran short of supply {where} on {platform}",
            shelves,
            f"The warehouse was empty on {reading('warehouse_stockless_share', 'percent')}"
            f" of those days{orders}. The shelves emptied because stock stopped coming in.",
        )

    if cause == "competitor_price_cut":
        return Narrative(
            f"Competitors cut prices on {category} on {platform}",
            f"Competitor prices dropped from {reading('rival_price_to_mrp_before', 'percent')}"
            f" to {reading('rival_price_to_mrp_during', 'percent')} of MRP {where}, {span}.",
            _others(payload, "Shoppers who would have bought from you had a cheaper alternative."),
        )
    if cause == "own_price_increase":
        return Narrative(
            f"Your {category} prices went up on {platform}",
            f"Your price rose from {reading('price_to_mrp_before', 'percent')}"
            f" to {reading('price_to_mrp_during', 'percent')} of MRP {where}, {span}.",
            _others(payload, "Fewer shoppers bought at the higher price."),
        )
    if cause == "rank_loss":
        return Narrative(
            f"{products} slipped down the search results on {platform}",
            f"Their average position fell from {reading('organic_rank_before', 'rank')}"
            f" to {reading('organic_rank_during', 'rank')} {where}, {span}.",
            _others(payload, "Fewer shoppers saw them, so fewer bought."),
        )
    if cause == "ad_budget_cut":
        return Narrative(
            f"The ad budget for {category} was cut on {platform}",
            f"The daily budget went from {reading('daily_budget_before', 'currency')}"
            f" to {reading('daily_budget_during', 'currency')} {span}, and"
            f" {reading('budget_utilisation_during', 'percent')} of the smaller budget was spent.",
            _others(payload, "The budget ran out early each day, so the ads stopped showing."),
        )
    if cause == "ad_delivery_drop":
        return Narrative(
            f"Ads for {category} reached fewer shoppers on {platform}",
            f"Ad delivery ran at {reading('ad_delivery_during', 'percent')}"
            f" of a normal day {span}.",
            _others(payload, "The budget was not the limit, so look at bids and campaign status."),
        )
    if cause == "data_gap":
        missing = _count(int(evidence.get("cities_missing", 0)), "city", "cities")
        return Narrative(
            f"Sales data is missing for {missing} on {platform}",
            f"No sales arrived {where} {span}.",
            "These cities normally report every day, and nothing suggests sales really stopped."
            " This is a reporting problem to raise with the platform. The period is left out of"
            " every sales figure until the data arrives.",
        )
    return Narrative(
        f"{category} sales fell {where} on {platform} with no clear cause",
        f"Sales came in below what availability, price, search position and ads predict, {span}.",
        "None of the measured drivers moved enough to account for the drop.",
    )


def _others(payload: dict[str, Any], reason: str) -> str:
    """The reason, plus any second driver large enough to deserve a mention."""
    losses: dict[str, float] = payload["loss_gmv"]
    main = max(losses, key=lambda name: losses[name], default=None)
    if main is None or losses[main] <= 0:
        return reason
    also = [
        DRIVER_LABELS[name].lower()
        for name, lost in sorted(losses.items(), key=lambda item: -item[1])
        if name != main and lost >= _NOTABLE_SHARE * losses[main]
    ]
    if not also:
        return f"{reason} No other driver moved enough to matter."
    return f"{reason} {_listed(also).capitalize()} also cost sales over the same days."


def _shown(value: float | None, style: str) -> str:
    if value is None:
        return "an unknown level"
    if style == "percent":
        return f"{value:.0%}"
    if style == "rank":
        return f"{value:.1f}"
    if style == "currency":
        return f"₹{indian_grouping(value)}"
    return f"{value:,.0f}"


def indian_grouping(value: float) -> str:
    """Whole rupees grouped the Indian way: 12,34,567."""
    digits = f"{abs(round(value)):d}"
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while head:
        groups.append(head[-2:])
        head = head[:-2]
    grouped = ",".join([*reversed(groups), tail]) if groups else tail
    return f"-{grouped}" if round(value) < 0 else grouped


def _count(number: int, singular: str, plural: str | None = None) -> str:
    return f"{number} {singular if number == 1 else plural or singular + 's'}"


def _listed(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _span(start: date, end: date) -> str:
    if start == end:
        return f"on {start.day} {start:%b}"
    if (start.year, start.month) == (end.year, end.month):
        return f"from {start.day} to {end.day} {end:%b}"
    return f"from {start.day} {start:%b} to {end.day} {end:%b}"
