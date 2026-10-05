"""What the engine reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum


class RootCause(StrEnum):
    STOCKOUT = "stockout"
    SUPPLY_SHORTFALL = "supply_shortfall"
    OWN_PRICE_INCREASE = "own_price_increase"
    COMPETITOR_PRICE_CUT = "competitor_price_cut"
    RANK_LOSS = "rank_loss"
    AD_BUDGET_CUT = "ad_budget_cut"
    AD_DELIVERY_DROP = "ad_delivery_drop"
    # Sales rows that never arrived. Not a loss of sales at all.
    DATA_GAP = "data_gap"
    # A real drop that none of the measured drivers accounts for.
    UNEXPLAINED_DROP = "unexplained_drop"


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """One day of a leak's cells: what normal sales would have been, and what sold."""

    day: date
    expected_gmv: float
    # None when none of the day's sales arrived.
    actual_gmv: float | None
    # False when only some of the day's sales arrived, so actual understates the day.
    complete: bool
    in_window: bool


@dataclass(frozen=True, slots=True)
class Finding:
    """One revenue leak: where and when it happened, what caused it and what it cost.

    Every figure is a calculation over the warehouse. `expected_*` is what the
    model says would have sold with each driver at its normal level, `loss_*`
    is that model's estimate of what each driver cost, and `unexplained_*` is
    whatever separates the model from what was actually sold.
    """

    id: str
    tenant_id: str
    cause: RootCause
    platform: str
    # None when the leak is not confined to one category.
    category: str | None
    # None when every city on the platform is affected.
    cities: tuple[str, ...] | None
    sku_ids: tuple[str, ...]
    start_date: date
    end_date: date

    expected_units: float
    expected_gmv: float
    # None when the sales for the period never arrived.
    actual_units: float | None
    actual_gmv: float | None
    # Estimated loss per driver, by name. Negative means the driver helped.
    loss_units: dict[str, float]
    loss_gmv: dict[str, float]
    # One standard error on each `loss_gmv` figure, from how well the driver's
    # elasticity is known. Zero where the effect is mechanical, not estimated.
    loss_gmv_sd: dict[str, float]
    unexplained_units: float | None
    unexplained_gmv: float | None
    # How far actual sales stray from the model through chance alone, as one
    # standard deviation. An unexplained figure within about two of these is noise.
    noise_units: float
    # Sales that would be expected but were never reported. Zero unless there is a data gap.
    unreported_units: float
    unreported_gmv: float

    # The readings behind the diagnosis, for example availability before and during.
    evidence: dict[str, float]
    # The leak's cells day by day, from two weeks before it to a week after.
    daily: tuple[DailyPoint, ...]

    def to_json(self) -> dict[str, object]:
        """The finding as plain values, ready to store or send."""
        return {
            **asdict(self),
            "cause": self.cause.value,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "daily": [{**asdict(point), "day": point.day.isoformat()} for point in self.daily],
        }

    @property
    def cause_loss_gmv(self) -> float:
        """Rupees lost to the named cause. Zero for a data gap or an unexplained drop."""
        return self.loss_gmv.get(_DRIVER_OF.get(self.cause, ""), 0.0)

    @property
    def cause_loss_gmv_sd(self) -> float:
        return self.loss_gmv_sd.get(_DRIVER_OF.get(self.cause, ""), 0.0)

    @property
    def cause_loss_units(self) -> float:
        return self.loss_units.get(_DRIVER_OF.get(self.cause, ""), 0.0)


_DRIVER_OF = {
    RootCause.STOCKOUT: "availability",
    RootCause.SUPPLY_SHORTFALL: "availability",
    RootCause.OWN_PRICE_INCREASE: "own_price",
    RootCause.COMPETITOR_PRICE_CUT: "rival_price",
    RootCause.RANK_LOSS: "visibility",
    RootCause.AD_BUDGET_CUT: "ads",
    RootCause.AD_DELIVERY_DROP: "ads",
}
