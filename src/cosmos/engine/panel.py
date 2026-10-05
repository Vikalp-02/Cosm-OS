"""Load one tenant's history from the warehouse as dense arrays.

A cell is one product in one city on one platform. Facts are [cell, day]
matrices with NaN where a value is unknown, which keeps every later step a
plain array operation and makes missing data impossible to mistake for zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import numpy as np

from cosmos.engine._arrays import BoolArray, FloatArray, IntArray

_FACTS = (
    "units_sold",
    "avg_selling_price",
    "availability",
    "price_to_mrp",
    "rival_price_to_mrp",
    "organic_reciprocal_rank",
    "avg_organic_rank",
    "units_on_hand",
    "po_units_lapsed",
)


# Panel field -> column of the campaign mart.
_ADS = {
    "clicks": "clicks",
    "ad_orders": "attributed_orders",
    "budget": "daily_budget",
    "spend": "spend",
}


@dataclass(frozen=True, slots=True)
class Panel:
    tenant_id: str
    first_day: date
    weekday: IntArray  # [D] Monday = 0

    # One entry per cell.
    platform: tuple[str, ...]
    city: tuple[str, ...]
    sku_id: tuple[str, ...]
    # A group is a category on a platform, the level ads are bought at.
    groups: tuple[tuple[str, str], ...]
    group: IntArray  # [C] index into groups
    # An area is a category in one city on a platform.
    areas: tuple[tuple[str, str, str], ...]
    area: IntArray  # [C] index into areas
    # A place is a city on a platform, numbered from zero.
    place: IntArray  # [C]

    # [C, D], NaN where unknown.
    units: FloatArray
    price: FloatArray
    availability: FloatArray
    price_to_mrp: FloatArray
    rival_price_to_mrp: FloatArray
    reciprocal_rank: FloatArray
    organic_rank: FloatArray
    stock: FloatArray
    po_units_lapsed: FloatArray
    reported: BoolArray  # the city's sales arrived that day
    gap: BoolArray  # the city normally reports and sent nothing

    # [G, D], NaN where the group ran no campaign.
    clicks: FloatArray
    ad_orders: FloatArray
    budget: FloatArray
    spend: FloatArray

    @property
    def n_cells(self) -> int:
        return len(self.sku_id)

    @property
    def n_days(self) -> int:
        return int(self.weekday.size)

    def day(self, index: int) -> date:
        return self.first_day + timedelta(days=index)


def load_panel(conn: duckdb.DuckDBPyConnection, tenant_id: str) -> Panel:
    tenant = {"tenant": tenant_id}
    bounds = conn.execute(
        "SELECT min(date_day), max(date_day) FROM marts.fct_listing_city_daily"
        " WHERE tenant_id = $tenant",
        tenant,
    ).fetchone()
    if bounds is None or bounds[0] is None:
        raise LookupError(f"the warehouse holds no sales history for tenant {tenant_id!r}")
    first_day, last_day = bounds
    n_days = (last_day - first_day).days + 1
    span = {**tenant, "first": first_day}

    cells = conn.execute(
        "SELECT DISTINCT platform, city, sku_id, category FROM marts.fct_listing_city_daily"
        " WHERE tenant_id = $tenant ORDER BY platform, city, sku_id",
        tenant,
    ).fetchall()
    cell_of = {(platform, city, sku): index for index, (platform, city, sku, _) in enumerate(cells)}
    groups = sorted({(platform, category) for platform, _, _, category in cells})
    areas = sorted({(platform, city, category) for platform, city, _, category in cells})
    group_of = {key: index for index, key in enumerate(groups)}
    area_of = {key: index for index, key in enumerate(areas)}
    places = sorted({(platform, city) for platform, city, _, _ in cells})
    place_of = {key: index for index, key in enumerate(places)}

    facts = {name: np.full((len(cells), n_days), np.nan) for name in _FACTS}
    reported = np.zeros((len(cells), n_days), dtype=np.bool_)
    rows = conn.execute(
        f"""
        SELECT platform, city, sku_id, date_diff('day', $first, date_day), sales_reported,
               {", ".join(f"{name}::DOUBLE" for name in _FACTS)}
        FROM marts.fct_listing_city_daily WHERE tenant_id = $tenant
        """,
        span,
    ).fetchall()
    for platform, city, sku, day, is_reported, *values in rows:
        cell = cell_of[(platform, city, sku)]
        reported[cell, day] = is_reported
        for name, value in zip(_FACTS, values, strict=True):
            if value is not None:
                facts[name][cell, day] = value

    gap = np.zeros((len(cells), n_days), dtype=np.bool_)
    cells_in: dict[tuple[str, str], list[int]] = {}
    for index, (platform, city, _, _) in enumerate(cells):
        cells_in.setdefault((platform, city), []).append(index)
    gaps = conn.execute(
        "SELECT platform, city, date_diff('day', $first, date_day)"
        " FROM marts.fct_sales_reporting_daily WHERE tenant_id = $tenant AND is_gap",
        span,
    ).fetchall()
    for platform, city, day in gaps:
        if 0 <= day < n_days:
            gap[cells_in.get((platform, city), []), day] = True

    ads = {name: np.full((len(groups), n_days), np.nan) for name in _ADS}
    campaigns = conn.execute(
        f"""
        SELECT platform, category, date_diff('day', $first, date_day),
               {", ".join(f"sum({column})::DOUBLE" for column in _ADS.values())}
        FROM marts.fct_campaign_daily
        WHERE tenant_id = $tenant AND category IS NOT NULL
        GROUP BY ALL
        """,
        span,
    ).fetchall()
    for platform, category, day, *values in campaigns:
        ad_group = group_of.get((platform, category))
        if ad_group is not None and 0 <= day < n_days:
            for name, value in zip(_ADS, values, strict=True):
                if value is not None:
                    ads[name][ad_group, day] = value

    return Panel(
        tenant_id=tenant_id,
        first_day=first_day,
        weekday=(first_day.weekday() + np.arange(n_days)) % 7,
        platform=tuple(platform for platform, _, _, _ in cells),
        city=tuple(city for _, city, _, _ in cells),
        sku_id=tuple(sku for _, _, sku, _ in cells),
        groups=tuple(groups),
        group=np.array([group_of[(p, category)] for p, _, _, category in cells], dtype=np.int64),
        place=np.array([place_of[(p, c)] for p, c, _, _ in cells], dtype=np.int64),
        areas=tuple(areas),
        area=np.array([area_of[(p, c, category)] for p, c, _, category in cells], dtype=np.int64),
        units=facts["units_sold"],
        price=facts["avg_selling_price"],
        availability=facts["availability"],
        price_to_mrp=facts["price_to_mrp"],
        rival_price_to_mrp=facts["rival_price_to_mrp"],
        reciprocal_rank=facts["organic_reciprocal_rank"],
        organic_rank=facts["avg_organic_rank"],
        stock=facts["units_on_hand"],
        po_units_lapsed=facts["po_units_lapsed"],
        reported=reported,
        gap=gap,
        clicks=ads["clicks"],
        ad_orders=ads["ad_orders"],
        budget=ads["budget"],
        spend=ads["spend"],
    )
