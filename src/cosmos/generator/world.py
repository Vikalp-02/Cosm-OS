"""The fixed cast of a generated dataset: products, stores, keywords, campaigns.

Every brand and product here is invented. Nothing comes from a real catalogue.

Products are ordered by category, own products before competitors, and keywords
by category, so an item or keyword index also encodes its category.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from cosmos.contracts import PLATFORMS
from cosmos.generator._arrays import BoolArray, FloatArray, IntArray
from cosmos.generator.config import PINCODES_PER_CITY, GeneratorConfig, Stream

OWN_PER_CATEGORY: Final = 6
ITEMS_PER_CATEGORY: Final = 12
KEYWORDS_PER_CATEGORY: Final = 5

# Share of sales that come through ads when nothing is going wrong.
AD_SHARE: Final = 0.2
# Budgets sit comfortably above normal spend, so a budget only binds when cut.
BUDGET_HEADROOM: Final = 1.6

PLATFORM_CODES: Final = {"blinkit": "BL", "zepto": "ZP", "instamart": "IM"}
_PLATFORM_DEMAND: Final = (1.0, 0.8, 0.7)
_PLATFORM_REACH: Final = (1.0, 1.15, 0.9)
_COMPETITOR_BRANDS: Final = ("Kestrel", "Bluepeak", "Marigold")


@dataclass(frozen=True, slots=True)
class _City:
    name: str
    state: str
    pincode_prefix: str
    weight: float


@dataclass(frozen=True, slots=True)
class _Category:
    name: str
    own_brand: str
    pack: str
    price_range: tuple[int, int]
    variants: tuple[str, ...]
    keywords: tuple[str, ...]


_CITIES: Final = (
    _City("Bengaluru", "Karnataka", "560", 1.0),
    _City("Mumbai", "Maharashtra", "400", 1.0),
    _City("Delhi", "Delhi", "110", 0.9),
    _City("Hyderabad", "Telangana", "500", 0.7),
    _City("Pune", "Maharashtra", "411", 0.6),
    _City("Chennai", "Tamil Nadu", "600", 0.6),
    _City("Kolkata", "West Bengal", "700", 0.5),
    _City("Ahmedabad", "Gujarat", "380", 0.4),
)

_CATEGORIES: Final = (
    _Category(
        "Breakfast Cereals",
        "Harvest Table",
        "500 g",
        (180, 420),
        (
            "Rolled Oats",
            "Fruit & Nut Muesli",
            "Corn Flakes",
            "Chocolate Granola",
            "Millet Flakes",
            "Steel Cut Oats",
        ),
        ("oats", "muesli", "granola", "corn flakes", "breakfast cereal"),
    ),
    _Category(
        "Snack Bars",
        "Daybreak",
        "Pack of 6",
        (240, 540),
        (
            "Chocolate Protein Bar",
            "Peanut Protein Bar",
            "Honey Granola Bar",
            "Date Energy Bar",
            "Almond Nut Bar",
            "Berry Oat Bar",
        ),
        ("protein bar", "energy bar", "granola bar", "healthy snacks", "nut bar"),
    ),
    _Category(
        "Nut Butters",
        "Harvest Table",
        "340 g",
        (160, 480),
        (
            "Crunchy Peanut Butter",
            "Creamy Peanut Butter",
            "Almond Butter",
            "Chocolate Peanut Butter",
            "Cashew Butter",
            "Unsweetened Peanut Butter",
        ),
        ("peanut butter", "almond butter", "nut butter", "chocolate spread", "protein spread"),
    ),
    _Category(
        "Instant Noodles",
        "Daybreak",
        "Pack of 4",
        (60, 180),
        (
            "Masala Noodles",
            "Hakka Noodles",
            "Chilli Garlic Noodles",
            "Millet Noodles",
            "Cup Noodles",
            "Curry Ramen",
        ),
        ("instant noodles", "masala noodles", "cup noodles", "hakka noodles", "ramen"),
    ),
    _Category(
        "Juices",
        "Harvest Table",
        "1 L",
        (90, 260),
        (
            "Orange Juice",
            "Mixed Fruit Juice",
            "Apple Juice",
            "Tender Coconut Water",
            "Pomegranate Juice",
            "Guava Juice",
        ),
        ("orange juice", "fruit juice", "coconut water", "apple juice", "cold pressed juice"),
    ),
)


@dataclass(frozen=True, slots=True)
class World:
    """Index conventions: P platforms, C cities, K categories, I items, Q keywords.

    J is the items in one category and M the keywords in one category.
    """

    platforms: tuple[str, ...]
    cities: tuple[str, ...]
    states: tuple[str, ...]
    categories: tuple[str, ...]

    sku_ids: tuple[str, ...]  # [I]
    brands: tuple[str, ...]  # [I]
    product_names: tuple[str, ...]  # [I]
    category_of: IntArray  # [I]
    is_competitor: BoolArray  # [I]
    mrp: FloatArray  # [I]
    item_ids: tuple[tuple[str, ...], ...]  # [P][I]
    eans: tuple[tuple[str | None, ...], ...]  # [P][I]

    store_ids: tuple[tuple[str, ...], ...]  # [P][stores]
    store_city: tuple[IntArray, ...]  # [P][stores]
    store_pincodes: tuple[tuple[str, ...], ...]  # [P][stores]
    facility_ids: tuple[tuple[str, ...], ...]  # [P][C]
    crawl_pincodes: tuple[str, ...]  # [N] pincodes search rank is observed from
    crawl_city: IntArray  # [N]

    keywords: tuple[str, ...]  # [Q]
    keyword_volume: FloatArray  # [K, M] share of a category's searches
    campaign_ids: tuple[tuple[str, ...], ...]  # [P][K]
    campaign_names: tuple[str, ...]  # [K]

    base_units: FloatArray  # [P, C, I] usual daily units; zero for competitors
    discount: FloatArray  # [P, I] usual discount off MRP
    relevance: FloatArray  # [P, Q, J] how well each item in the category matches a keyword
    ctr: FloatArray  # [P, K, M]
    cvr: FloatArray  # [P, K, M]
    cpc: FloatArray  # [P, K, M]
    base_impressions: FloatArray  # [P, K, M] usual daily impressions
    base_budget: FloatArray  # [P, K]

    @property
    def n_items(self) -> int:
        return len(self.sku_ids)

    @property
    def flat_item_ids(self) -> tuple[str, ...]:
        """Item ids for every platform in one sequence, indexed by platform * I + item."""
        return tuple(item for items in self.item_ids for item in items)

    def own_items(self, category: int) -> IntArray:
        return category * ITEMS_PER_CATEGORY + np.arange(OWN_PER_CATEGORY)


def build_world(config: GeneratorConfig) -> World:
    rng = config.rng(Stream.WORLD)
    n_platforms, n_cities, n_categories = len(PLATFORMS), len(_CITIES), len(_CATEGORIES)
    n_items = n_categories * ITEMS_PER_CATEGORY
    codes = [PLATFORM_CODES[platform] for platform in PLATFORMS]

    brands: list[str] = []
    names: list[str] = []
    mrp = np.empty(n_items)
    for category in _CATEGORIES:
        low, high = category.price_range
        for slot in range(ITEMS_PER_CATEGORY):
            own = slot < OWN_PER_CATEGORY
            brand = category.own_brand if own else _COMPETITOR_BRANDS[slot % 3]
            variant = category.variants[slot % OWN_PER_CATEGORY]
            mrp[len(names)] = 5 * round(rng.uniform(low, high) / 5)
            brands.append(brand)
            names.append(f"{brand} {variant} {category.pack}")
    category_of = np.repeat(np.arange(n_categories), ITEMS_PER_CATEGORY)
    is_competitor = np.tile(np.arange(ITEMS_PER_CATEGORY) >= OWN_PER_CATEGORY, n_categories)

    item_ids = tuple(
        tuple(f"{code}-{100_000 + n}" for n in rng.choice(900_000, size=n_items, replace=False))
        for code in codes
    )
    barcodes = [f"890{rng.integers(10**9, 10**10)}" for _ in range(n_items)]
    # Platforms do not always expose a barcode.
    eans = tuple(
        tuple(None if rng.random() < 0.1 else barcode for barcode in barcodes) for _ in codes
    )

    pools = [
        tuple(f"{city.pincode_prefix}{n:03d}" for n in range(1, PINCODES_PER_CITY + 1))
        for city in _CITIES
    ]
    store_ids: list[tuple[str, ...]] = []
    store_city: list[IntArray] = []
    store_pincodes: list[tuple[str, ...]] = []
    for code, reach in zip(codes, _PLATFORM_REACH, strict=True):
        ids: list[str] = []
        cities: list[int] = []
        pincodes: list[str] = []
        for index, city in enumerate(_CITIES):
            # Search rank is crawled from the first pincodes of each pool, so every
            # platform keeps a store in each of them: that is how a crawled
            # pincode gets traced back to its city.
            wanted = round(config.stores_per_city * city.weight * reach)
            for nth in range(max(2, config.crawl_pincodes_per_city, wanted)):
                spread = nth if nth < PINCODES_PER_CITY else int(rng.integers(PINCODES_PER_CITY))
                ids.append(f"{code}-DS-{len(ids) + 1:04d}")
                cities.append(index)
                pincodes.append(pools[index][spread])
        store_ids.append(tuple(ids))
        store_city.append(np.array(cities, dtype=np.int64))
        store_pincodes.append(tuple(pincodes))

    crawl = config.crawl_pincodes_per_city
    popularity = rng.lognormal(0.0, 0.6, n_items)
    popularity[is_competitor] = 0.0
    city_weight = np.array([city.weight for city in _CITIES])
    base_units = (
        6.0
        * np.array(_PLATFORM_DEMAND)[:, None, None]
        * city_weight[None, :, None]
        * popularity[None, None, :]
    )

    volume = 1.0 / np.arange(1, KEYWORDS_PER_CATEGORY + 1)
    keyword_volume = np.tile(volume / volume.sum(), (n_categories, 1))
    shape = (n_platforms, n_categories, KEYWORDS_PER_CATEGORY)
    ctr = rng.uniform(0.02, 0.05, shape)
    cvr = rng.uniform(0.08, 0.15, shape)
    cpc = rng.uniform(4.0, 12.0, shape)
    category_units = base_units.reshape(
        n_platforms, n_cities, n_categories, ITEMS_PER_CATEGORY
    ).sum(axis=(1, 3))
    base_impressions = AD_SHARE * category_units[:, :, None] * keyword_volume[None] / (ctr * cvr)
    base_budget = np.round(BUDGET_HEADROOM * (base_impressions * ctr * cpc).sum(axis=2), -1)

    return World(
        platforms=PLATFORMS,
        cities=tuple(city.name for city in _CITIES),
        states=tuple(city.state for city in _CITIES),
        categories=tuple(category.name for category in _CATEGORIES),
        sku_ids=tuple(f"SKU-{n:04d}" for n in range(1, n_items + 1)),
        brands=tuple(brands),
        product_names=tuple(names),
        category_of=category_of,
        is_competitor=is_competitor,
        mrp=mrp,
        item_ids=item_ids,
        eans=eans,
        store_ids=tuple(store_ids),
        store_city=tuple(store_city),
        store_pincodes=tuple(store_pincodes),
        facility_ids=tuple(
            tuple(f"{code}-WH-{city.name[:3].upper()}" for city in _CITIES) for code in codes
        ),
        crawl_pincodes=tuple(pincode for pool in pools for pincode in pool[:crawl]),
        crawl_city=np.repeat(np.arange(n_cities), crawl),
        keywords=tuple(keyword for category in _CATEGORIES for keyword in category.keywords),
        keyword_volume=keyword_volume,
        campaign_ids=tuple(
            tuple(f"{code}-CMP-{n:02d}" for n in range(1, n_categories + 1)) for code in codes
        ),
        campaign_names=tuple(f"{category.name} | Search" for category in _CATEGORIES),
        base_units=base_units,
        discount=rng.uniform(0.03, 0.18, (n_platforms, n_items)),
        relevance=rng.normal(
            0.0, 1.0, (n_platforms, n_categories * KEYWORDS_PER_CATEGORY, ITEMS_PER_CATEGORY)
        ),
        ctr=ctr,
        cvr=cvr,
        cpc=cpc,
        base_impressions=base_impressions,
        base_budget=base_budget,
    )
