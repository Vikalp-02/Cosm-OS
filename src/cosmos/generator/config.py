"""Settings that decide the size and content of a generated dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import IntEnum
from typing import Final

import numpy as np

# Each incident gets a slot to itself on its platform: the incident, then quiet
# days so its effect has cleared before the next one starts.
SLOT_DAYS: Final = 14
PINCODES_PER_CITY: Final = 12


class Stream(IntEnum):
    """Independent random streams, so changing one stage leaves the others as they were."""

    WORLD = 1
    PLAN = 2
    NOISE = 3
    SALES = 4
    ADS = 5
    SUPPLY = 6
    CRAWL = 7
    INCIDENT = 8


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    seed: int = 7
    tenant_id: str = "northwind"
    start_date: date = date(2026, 6, 1)
    days: int = 120
    # Incident-free history at the start, long enough to learn what normal looks like.
    warmup_days: int = 35
    stores_per_city: int = 20
    crawl_pincodes_per_city: int = 3
    incidents: int = 12

    def __post_init__(self) -> None:
        if self.warmup_days < 14:
            raise ValueError("warmup_days must cover at least two weeks")
        if not 1 <= self.crawl_pincodes_per_city <= PINCODES_PER_CITY:
            raise ValueError(f"crawl_pincodes_per_city must be 1 to {PINCODES_PER_CITY}")
        if self.stores_per_city < 2:
            raise ValueError("stores_per_city must be at least 2")
        if self.incidents < 0:
            raise ValueError("incidents cannot be negative")

    @classmethod
    def small(cls, seed: int = 7) -> GeneratorConfig:
        """A few seconds to build. Used by tests and quick local runs."""
        return cls(
            seed=seed,
            days=70,
            warmup_days=28,
            stores_per_city=4,
            crawl_pincodes_per_city=2,
            incidents=6,
        )

    @property
    def slots(self) -> int:
        return max(0, (self.days - self.warmup_days) // SLOT_DAYS)

    def rng(self, stream: Stream, *key: int) -> np.random.Generator:
        return np.random.default_rng([self.seed, int(stream), *key])
