"""The incidents planted in a dataset, and where and when they happen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from cosmos.generator.config import SLOT_DAYS, GeneratorConfig, Stream
from cosmos.generator.world import World


class Cause(StrEnum):
    """Why revenue fell. `Incident.magnitude` means something different for each."""

    # Share of the city's dark stores that stop listing the items.
    STOCKOUT = "stockout"
    # As for a stockout, but the warehouse has run dry and a purchase order has lapsed.
    SUPPLY_SHORTFALL = "supply_shortfall"
    # Fraction by which competitors in the category cut their price.
    COMPETITOR_PRICE_CUT = "competitor_price_cut"
    # Drop in search relevance, in standard deviations of the relevance score.
    RANK_LOSS = "rank_loss"
    # Fraction of the campaign's daily budget removed.
    AD_BUDGET_CUT = "ad_budget_cut"
    # Share of cities whose sales rows never arrived. Nothing was actually lost.
    DATA_GAP = "data_gap"


@dataclass(frozen=True, slots=True)
class Incident:
    number: int
    cause: Cause
    platform: int
    # None when the incident covers every city on the platform.
    city: int | None
    items: tuple[int, ...]
    start: int
    end: int  # last affected day, inclusive
    magnitude: float

    @property
    def days(self) -> range:
        return range(self.start, self.end + 1)

    @property
    def id(self) -> str:
        return f"INC-{self.number:03d}"


def plan_incidents(world: World, config: GeneratorConfig) -> tuple[Incident, ...]:
    """Place incidents so that no two overlap in time on the same platform.

    Keeping them apart means each one's lost revenue can be stated exactly,
    which is what makes the dataset usable as ground truth.
    """
    cells = [
        (platform, slot) for platform in range(len(world.platforms)) for slot in range(config.slots)
    ]
    if config.incidents > len(cells):
        raise ValueError(
            f"{config.incidents} incidents do not fit in {len(cells)} slots; "
            "add days or plant fewer"
        )

    rng = config.rng(Stream.PLAN)
    causes = list(Cause)
    chosen = rng.permutation(len(cells))[: config.incidents]
    drafts: list[tuple[int, int, Cause, int | None, tuple[int, ...], int, float]] = []
    for order, cell in enumerate(chosen):
        platform, slot = cells[int(cell)]
        cause = causes[order % len(causes)]
        start = config.warmup_days + slot * SLOT_DAYS + int(rng.integers(0, 3))
        end = start + int(rng.integers(5, 9)) - 1
        own = world.own_items(int(rng.integers(len(world.categories))))

        city: int | None = None
        items = own
        if cause in (Cause.STOCKOUT, Cause.SUPPLY_SHORTFALL):
            city = int(rng.integers(len(world.cities)))
            items = np.sort(rng.choice(own, size=int(rng.integers(3, 6)), replace=False))
            magnitude = rng.uniform(0.6, 0.9)
        elif cause is Cause.COMPETITOR_PRICE_CUT:
            magnitude = rng.uniform(0.15, 0.30)
        elif cause is Cause.RANK_LOSS:
            items = np.sort(rng.choice(own, size=int(rng.integers(2, 5)), replace=False))
            magnitude = rng.uniform(2.5, 3.5)
        elif cause is Cause.AD_BUDGET_CUT:
            magnitude = rng.uniform(0.75, 0.95)
        else:
            items = np.flatnonzero(~world.is_competitor)
            magnitude = rng.uniform(0.5, 0.75)
        drafts.append(
            (start, platform, cause, city, tuple(int(i) for i in items), end, round(magnitude, 2))
        )

    drafts.sort(key=lambda draft: draft[:2])
    return tuple(
        Incident(number, cause, platform, city, items, start, end, magnitude)
        for number, (start, platform, cause, city, items, end, magnitude) in enumerate(drafts, 1)
    )
