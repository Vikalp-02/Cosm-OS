"""Turn a simulated market into the raw tables an ingestion source would deliver."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import numpy as np
import pyarrow as pa

from cosmos.generator._arrays import BoolArray, IntArray
from cosmos.generator.config import GeneratorConfig, Stream
from cosmos.generator.simulate import AdReport, Market
from cosmos.generator.supply import Supply
from cosmos.generator.world import (
    ITEMS_PER_CATEGORY,
    KEYWORDS_PER_CATEGORY,
    OWN_PER_CATEGORY,
    World,
)

# Share of shelf checks where the crawler read no price.
PRICE_MISS_RATE: Final = 0.005
# How often a fully funded campaign wins a sponsored slot, and how often a rival does.
SPONSORED_FILL: Final = 0.9
RIVAL_SPONSORED_RATE: Final = 0.8
# What the tenant charges platforms, as a share of MRP.
COST_SHARE: Final = 0.7


def build_tables(
    world: World,
    config: GeneratorConfig,
    market: Market,
    units: IntArray,
    ads: AdReport,
    supply: Supply,
    dropped: BoolArray,
) -> dict[str, pa.Table]:
    """`dropped` marks the [P, C, D] sales cells a data gap removed."""
    return {
        "product": _product(world, config),
        "listing": _listing(world, config),
        "location": _location(world),
        "campaign": _campaign(world, config),
        "campaign_daily": _campaign_daily(world, config, market),
        "campaign_item": _campaign_item(world, config),
        "sales_daily": _sales(world, config, market, units, dropped),
        "inventory_snapshot": _inventory(world, config, supply),
        "purchase_order_line": _purchase_orders(world, config, supply),
        "shelf_observation": _shelf(world, config, market),
        "search_rank_observation": _search_rank(world, config, market),
        "ad_performance_daily": _ads(world, config, ads),
    }


def _strings(values: Sequence[str], index: IntArray) -> pa.Array:
    """A string column that stores each distinct value once."""
    distinct, codes = np.unique(np.asarray(values), return_inverse=True)
    return pa.DictionaryArray.from_arrays(
        pa.array(codes[index], type=pa.int32()), pa.array(distinct.tolist(), type=pa.string())
    )


def _tenant(config: GeneratorConfig, rows: int) -> pa.Array:
    return _strings((config.tenant_id,), np.zeros(rows, dtype=np.int64))


def _items(world: World, platform: IntArray, item: IntArray) -> pa.Array:
    return _strings(world.flat_item_ids, platform * world.n_items + item)


def _campaigns(world: World, platform: IntArray, category: IntArray) -> pa.Array:
    flat = tuple(campaign for campaigns in world.campaign_ids for campaign in campaigns)
    return _strings(flat, platform * len(world.categories) + category)


def _dates(config: GeneratorConfig, day: IntArray) -> pa.Array:
    return pa.array(np.datetime64(config.start_date, "D") + day.astype("timedelta64[D]"))


def _grid(*shape: int) -> tuple[IntArray, ...]:
    """Every index combination of an array of this shape, one flat array per axis."""
    return tuple(axis.ravel() for axis in np.indices(shape))


def _product(world: World, config: GeneratorConfig) -> pa.Table:
    return pa.table(
        {
            "tenant_id": _tenant(config, world.n_items),
            "sku_id": pa.array(world.sku_ids),
            "brand": pa.array(world.brands),
            "product_name": pa.array(world.product_names),
            "category": _strings(world.categories, world.category_of),
            "is_competitor": pa.array(world.is_competitor),
            "mrp": pa.array(world.mrp),
        }
    )


def _listing(world: World, config: GeneratorConfig) -> pa.Table:
    platform, item = _grid(len(world.platforms), world.n_items)
    return pa.table(
        {
            "tenant_id": _tenant(config, platform.size),
            "platform": _strings(world.platforms, platform),
            "platform_item_id": _items(world, platform, item),
            "sku_id": _strings(world.sku_ids, item),
            "ean": pa.array([ean for eans in world.eans for ean in eans], type=pa.string()),
        }
    )


def _location(world: World) -> pa.Table:
    platform = np.repeat(np.arange(len(world.platforms)), [len(ids) for ids in world.store_ids])
    city = np.concatenate(world.store_city)
    return pa.table(
        {
            "platform": _strings(world.platforms, platform),
            "location_id": pa.array([store for ids in world.store_ids for store in ids]),
            "pincode": pa.array([code for codes in world.store_pincodes for code in codes]),
            "city": _strings(world.cities, city),
            "state": _strings(world.states, city),
        }
    )


def _campaign(world: World, config: GeneratorConfig) -> pa.Table:
    platform, category = _grid(len(world.platforms), len(world.categories))
    return pa.table(
        {
            "tenant_id": _tenant(config, platform.size),
            "platform": _strings(world.platforms, platform),
            "campaign_id": _campaigns(world, platform, category),
            "campaign_name": _strings(world.campaign_names, category),
        }
    )


def _campaign_daily(world: World, config: GeneratorConfig, market: Market) -> pa.Table:
    platform, category, day = _grid(*market.budget.shape)
    return pa.table(
        {
            "tenant_id": _tenant(config, platform.size),
            "platform": _strings(world.platforms, platform),
            "report_date": _dates(config, day),
            "campaign_id": _campaigns(world, platform, category),
            "daily_budget": pa.array(market.budget.ravel()),
            "is_active": pa.array(np.ones(platform.size, dtype=np.bool_)),
        }
    )


def _campaign_item(world: World, config: GeneratorConfig) -> pa.Table:
    platform, category, slot = _grid(len(world.platforms), len(world.categories), OWN_PER_CATEGORY)
    return pa.table(
        {
            "tenant_id": _tenant(config, platform.size),
            "platform": _strings(world.platforms, platform),
            "campaign_id": _campaigns(world, platform, category),
            "platform_item_id": _items(world, platform, category * ITEMS_PER_CATEGORY + slot),
        }
    )


def _sales(
    world: World, config: GeneratorConfig, market: Market, units: IntArray, dropped: BoolArray
) -> pa.Table:
    # Platforms report only what sold: a listing with no sales has no row.
    platform, city, item, day = np.nonzero((units > 0) & ~dropped[:, :, None, :])
    sold = units[platform, city, item, day]
    return pa.table(
        {
            "tenant_id": _tenant(config, platform.size),
            "platform": _strings(world.platforms, platform),
            "report_date": _dates(config, day),
            "platform_item_id": _items(world, platform, item),
            "city": _strings(world.cities, city),
            "units_sold": pa.array(sold),
            "gmv": pa.array(sold * market.price[platform, item, day]),
        }
    )


def _inventory(world: World, config: GeneratorConfig, supply: Supply) -> pa.Table:
    own = np.flatnonzero(~world.is_competitor)
    on_hand = supply.on_hand[:, :, own, :]
    platform, city, slot, day = _grid(*on_hand.shape)
    facilities = tuple(facility for ids in world.facility_ids for facility in ids)
    return pa.table(
        {
            "tenant_id": _tenant(config, platform.size),
            "platform": _strings(world.platforms, platform),
            "snapshot_date": _dates(config, day),
            "platform_item_id": _items(world, platform, own[slot]),
            "facility_id": _strings(facilities, platform * len(world.cities) + city),
            "city": _strings(world.cities, city),
            "units_on_hand": pa.array(on_hand.ravel()),
            "open_po_units": pa.array(supply.on_order[:, :, own, :].ravel()),
        }
    )


def _purchase_orders(world: World, config: GeneratorConfig, supply: Supply) -> pa.Table:
    lines = supply.lines
    facilities = tuple(facility for ids in world.facility_ids for facility in ids)
    cost = np.round(COST_SHARE * world.mrp[lines.item], 2)
    return pa.table(
        {
            "tenant_id": _tenant(config, lines.item.size),
            "platform": _strings(world.platforms, lines.platform),
            "po_number": pa.array(lines.number, type=pa.string()),
            "platform_item_id": _items(world, lines.platform, lines.item),
            "facility_id": _strings(facilities, lines.platform * len(world.cities) + lines.city),
            "city": _strings(world.cities, lines.city),
            "order_date": _dates(config, lines.order_day),
            "expected_delivery_date": _dates(config, lines.promised_day),
            "status": pa.array(lines.status, type=pa.string()),
            "units_ordered": pa.array(lines.ordered),
            "units_received": pa.array(lines.received),
            "line_value": pa.array(lines.ordered * cost),
        }
    )


def _shelf(world: World, config: GeneratorConfig, market: Market) -> pa.Table:
    rng = config.rng(Stream.CRAWL, 0)
    platforms: list[IntArray] = []
    stores: list[IntArray] = []
    items: list[IntArray] = []
    days: list[IntArray] = []
    prices = []
    first_store = 0
    for index, on_shelf in enumerate(market.on_shelf):
        store, item, day = _grid(*on_shelf.shape)
        platforms.append(np.full(store.size, index, dtype=np.int64))
        stores.append(store + first_store)
        items.append(item)
        days.append(day)
        prices.append(market.price[index][item, day])
        first_store += on_shelf.shape[0]

    platform = np.concatenate(platforms)
    price = np.concatenate(prices)
    return pa.table(
        {
            "tenant_id": _tenant(config, platform.size),
            "platform": _strings(world.platforms, platform),
            "observed_date": _dates(config, np.concatenate(days)),
            "location_id": _strings(
                tuple(store for ids in world.store_ids for store in ids), np.concatenate(stores)
            ),
            "platform_item_id": _items(world, platform, np.concatenate(items)),
            "is_available": pa.array(np.concatenate([shelf.ravel() for shelf in market.on_shelf])),
            "selling_price": pa.array(price, mask=rng.random(price.size) < PRICE_MISS_RATE),
        }
    )


def _search_rank(world: World, config: GeneratorConfig, market: Market) -> pa.Table:
    rng = config.rng(Stream.CRAWL, 1)
    category_of = np.arange(len(world.keywords)) // KEYWORDS_PER_CATEGORY
    columns: dict[str, list[IntArray]] = {
        name: [] for name in ("platform", "day", "pincode", "keyword", "item", "sponsored", "rank")
    }

    def add(
        platform: int,
        day: IntArray,
        pincode: IntArray,
        keyword: IntArray,
        slot: IntArray,
        rank: IntArray,
        *,
        sponsored: bool,
    ) -> None:
        columns["platform"].append(np.full(day.size, platform, dtype=np.int64))
        columns["day"].append(day)
        columns["pincode"].append(pincode)
        columns["keyword"].append(keyword)
        columns["item"].append(category_of[keyword] * ITEMS_PER_CATEGORY + slot)
        columns["sponsored"].append(np.full(day.size, int(sponsored), dtype=np.int64))
        columns["rank"].append(rank)

    for platform, organic in enumerate(market.organic_rank):
        searches = organic.shape[:3]
        # A campaign shows up in sponsored slots only as far as its budget carries it.
        funded = market.delivery[platform][category_of].T
        ours = rng.random(searches) < SPONSORED_FILL * funded[:, None, :]
        theirs = rng.random(searches) < RIVAL_SPONSORED_RATE
        ours_first = rng.random(searches) < 0.5

        # Sponsored results sit above the organic ones and push them down the page.
        day, pincode, keyword, slot = _grid(*organic.shape)
        paid = ours.astype(np.int64) + theirs
        add(
            platform,
            day,
            pincode,
            keyword,
            slot,
            (organic + paid[..., None]).ravel(),
            sponsored=False,
        )

        relevance = world.relevance[platform]
        best_own = np.argmax(relevance[:, :OWN_PER_CATEGORY], axis=1)
        best_rival = OWN_PER_CATEGORY + np.argmax(relevance[:, OWN_PER_CATEGORY:], axis=1)
        for shown, best, position in (
            (ours, best_own, np.where(theirs & ~ours_first, 2, 1)),
            (theirs, best_rival, np.where(ours & ours_first, 2, 1)),
        ):
            day, pincode, keyword = np.nonzero(shown)
            add(
                platform,
                day,
                pincode,
                keyword,
                best[keyword],
                position[day, pincode, keyword],
                sponsored=True,
            )

    merged = {name: np.concatenate(parts) for name, parts in columns.items()}
    return pa.table(
        {
            "tenant_id": _tenant(config, merged["day"].size),
            "platform": _strings(world.platforms, merged["platform"]),
            "observed_date": _dates(config, merged["day"]),
            "pincode": _strings(world.crawl_pincodes, merged["pincode"]),
            "keyword": _strings(world.keywords, merged["keyword"]),
            "platform_item_id": _items(world, merged["platform"], merged["item"]),
            "is_sponsored": pa.array(merged["sponsored"].astype(np.bool_)),
            "search_rank": pa.array(merged["rank"]),
        }
    )


def _ads(world: World, config: GeneratorConfig, ads: AdReport) -> pa.Table:
    platform, category, slot, day = np.nonzero(ads.impressions > 0)
    cell = (platform, category, slot, day)
    return pa.table(
        {
            "tenant_id": _tenant(config, platform.size),
            "platform": _strings(world.platforms, platform),
            "report_date": _dates(config, day),
            "campaign_id": _campaigns(world, platform, category),
            "keyword": _strings(world.keywords, category * KEYWORDS_PER_CATEGORY + slot),
            "impressions": pa.array(ads.impressions[cell]),
            "clicks": pa.array(ads.clicks[cell]),
            "spend": pa.array(ads.spend[cell]),
            "attributed_orders": pa.array(ads.orders[cell]),
            "attributed_revenue": pa.array(ads.revenue[cell]),
        }
    )
