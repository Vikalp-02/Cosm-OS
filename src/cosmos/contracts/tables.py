"""Raw-layer contracts: the shape every ingestion source must deliver.

The grains follow what quick-commerce platforms and shelf crawlers actually
provide. Sales arrive per city, shelf checks per dark store, search rank per
pincode, and ad spend per campaign and keyword with no location at all. Those
differences are kept here and reconciled downstream, not hidden at ingestion.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from cosmos.contracts.model import Column, ForeignKey, SqlType, TableContract

PLATFORMS: Final = ("blinkit", "zepto", "instamart")
PO_STATUSES: Final = ("open", "partially_received", "received", "cancelled", "expired")


def _one_of(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _key(name: str, description: str) -> Column:
    # Blank identifiers silently merge distinct rows into one grain, so they
    # are rejected at the boundary.
    return Column(name, SqlType.VARCHAR, description, check=f"length(trim({name})) > 0")


def _count(name: str, description: str, *, nullable: bool = False) -> Column:
    return Column(name, SqlType.BIGINT, description, nullable=nullable, check=f"{name} >= 0")


def _amount(name: str, description: str, *, nullable: bool = False) -> Column:
    return Column(name, SqlType.MONEY, description, nullable=nullable, check=f"{name} >= 0")


TENANT: Final = _key("tenant_id", "Client the row belongs to. Every query is scoped by it.")
PLATFORM: Final = Column(
    "platform", SqlType.VARCHAR, "Quick-commerce platform.", check=_one_of("platform", PLATFORMS)
)
ITEM: Final = _key("platform_item_id", "The platform's own identifier for a listed product.")
CITY: Final = _key("city", "City name as normalised at ingestion.")

_LISTING_KEY: Final = ("tenant_id", "platform", "platform_item_id")
_TO_LISTING: Final = ForeignKey(_LISTING_KEY, "listing", _LISTING_KEY)


PRODUCT: Final = TableContract(
    name="product",
    description="Products a tenant tracks: its own and the competitors it benchmarks against.",
    grain=("tenant_id", "sku_id"),
    columns=(
        TENANT,
        _key("sku_id", "Tenant-wide product identifier, stable across platforms."),
        _key("brand", "Brand name."),
        _key("product_name", "Display name."),
        _key("category", "Product category."),
        Column("is_competitor", SqlType.BOOLEAN, "True for a benchmarked competitor product."),
        Column("mrp", SqlType.MONEY, "Maximum retail price.", check="mrp > 0"),
    ),
)

LISTING: Final = TableContract(
    name="listing",
    description="A product as listed on one platform. Bridges platform item ids to products.",
    grain=_LISTING_KEY,
    columns=(
        TENANT,
        PLATFORM,
        ITEM,
        _key("sku_id", "Product this listing sells."),
        Column("ean", SqlType.VARCHAR, "Barcode, when the platform exposes it.", nullable=True),
    ),
    foreign_keys=(ForeignKey(("tenant_id", "sku_id"), "product", ("tenant_id", "sku_id")),),
)

LOCATION: Final = TableContract(
    name="location",
    description="Dark stores per platform. Shared reference data, not tenant-scoped.",
    grain=("platform", "location_id"),
    columns=(
        PLATFORM,
        _key("location_id", "The platform's dark-store identifier."),
        Column(
            "pincode",
            SqlType.VARCHAR,
            "Six-digit postal code the store serves.",
            check="regexp_full_match(pincode, '[1-9][0-9]{5}')",
        ),
        CITY,
        _key("state", "State name."),
    ),
)

CAMPAIGN: Final = TableContract(
    name="campaign",
    description="Advertising campaigns.",
    grain=("tenant_id", "platform", "campaign_id"),
    columns=(
        TENANT,
        PLATFORM,
        _key("campaign_id", "The platform's campaign identifier."),
        _key("campaign_name", "Display name."),
        _amount("daily_budget", "Daily spend cap. Null when uncapped.", nullable=True),
    ),
)

CAMPAIGN_ITEM: Final = TableContract(
    name="campaign_item",
    description="Which listings a campaign advertises.",
    grain=("tenant_id", "platform", "campaign_id", "platform_item_id"),
    columns=(
        TENANT,
        PLATFORM,
        _key("campaign_id", "Campaign."),
        ITEM,
    ),
    foreign_keys=(
        ForeignKey(
            ("tenant_id", "platform", "campaign_id"),
            "campaign",
            ("tenant_id", "platform", "campaign_id"),
        ),
        _TO_LISTING,
    ),
)

SALES_DAILY: Final = TableContract(
    name="sales_daily",
    description="Units and revenue sold per listing, city and day.",
    grain=("tenant_id", "platform", "report_date", "platform_item_id", "city"),
    columns=(
        TENANT,
        PLATFORM,
        Column("report_date", SqlType.DATE, "Day the orders were placed."),
        ITEM,
        CITY,
        _count("units_sold", "Units sold."),
        _amount("gmv", "Gross merchandise value in rupees."),
    ),
    foreign_keys=(_TO_LISTING,),
)

INVENTORY_SNAPSHOT: Final = TableContract(
    name="inventory_snapshot",
    description="End-of-day stock per listing and facility.",
    grain=("tenant_id", "platform", "snapshot_date", "platform_item_id", "facility_id"),
    columns=(
        TENANT,
        PLATFORM,
        Column("snapshot_date", SqlType.DATE, "Day the stock position describes."),
        ITEM,
        _key("facility_id", "Warehouse or store holding the stock."),
        CITY,
        _count("units_on_hand", "Sellable units held."),
        _count("open_po_units", "Units ordered but not yet received.", nullable=True),
    ),
    foreign_keys=(_TO_LISTING,),
)

PURCHASE_ORDER_LINE: Final = TableContract(
    name="purchase_order_line",
    description="One listing on one purchase order raised by a platform.",
    grain=("tenant_id", "platform", "po_number", "platform_item_id"),
    columns=(
        TENANT,
        PLATFORM,
        _key("po_number", "The platform's purchase order number."),
        ITEM,
        _key("facility_id", "Facility the order delivers to."),
        CITY,
        Column("order_date", SqlType.DATE, "Day the order was raised."),
        Column(
            "expected_delivery_date",
            SqlType.DATE,
            "Booked delivery day, once an appointment exists.",
            nullable=True,
            check="expected_delivery_date >= order_date",
        ),
        Column("status", SqlType.VARCHAR, "Order state.", check=_one_of("status", PO_STATUSES)),
        _count("units_ordered", "Units the platform ordered."),
        _count("units_received", "Units the platform has accepted."),
        _amount("line_value", "Value of the ordered units in rupees."),
    ),
    foreign_keys=(_TO_LISTING,),
)

SHELF_OBSERVATION: Final = TableContract(
    name="shelf_observation",
    description="What a shopper at one dark store saw for a listing on a given day.",
    grain=("tenant_id", "platform", "observed_date", "location_id", "platform_item_id"),
    columns=(
        TENANT,
        PLATFORM,
        Column("observed_date", SqlType.DATE, "Day of the crawl."),
        _key("location_id", "Dark store the listing was checked at."),
        ITEM,
        Column("is_available", SqlType.BOOLEAN, "True when the listing could be added to cart."),
        Column(
            "selling_price",
            SqlType.MONEY,
            "Price shown. Null when the listing showed no price.",
            nullable=True,
            check="selling_price > 0",
        ),
    ),
    foreign_keys=(
        _TO_LISTING,
        ForeignKey(("platform", "location_id"), "location", ("platform", "location_id")),
    ),
)

SEARCH_RANK_OBSERVATION: Final = TableContract(
    name="search_rank_observation",
    description="Position of a listing in search results for a keyword at a pincode.",
    # A listing can hold an organic and a sponsored slot for the same search.
    grain=(
        "tenant_id",
        "platform",
        "observed_date",
        "pincode",
        "keyword",
        "platform_item_id",
        "is_sponsored",
    ),
    columns=(
        TENANT,
        PLATFORM,
        Column("observed_date", SqlType.DATE, "Day of the crawl."),
        _key("pincode", "Postal code the search was run from."),
        _key("keyword", "Search term."),
        ITEM,
        Column("is_sponsored", SqlType.BOOLEAN, "True for a paid placement."),
        Column(
            "search_rank", SqlType.INTEGER, "Position, starting at 1.", check="search_rank >= 1"
        ),
    ),
    foreign_keys=(_TO_LISTING,),
)

AD_PERFORMANCE_DAILY: Final = TableContract(
    name="ad_performance_daily",
    description="Ad delivery and attributed sales per campaign, keyword and day.",
    grain=("tenant_id", "platform", "report_date", "campaign_id", "keyword"),
    columns=(
        TENANT,
        PLATFORM,
        Column("report_date", SqlType.DATE, "Day of delivery."),
        _key("campaign_id", "Campaign."),
        _key("keyword", "Targeted search term."),
        _count("impressions", "Times the ad was shown."),
        Column(
            "clicks", SqlType.BIGINT, "Clicks on the ad.", check="clicks BETWEEN 0 AND impressions"
        ),
        _amount("spend", "Ad spend in rupees."),
        _count("attributed_orders", "Orders the platform attributes to the ad."),
        _amount("attributed_revenue", "Revenue the platform attributes to the ad."),
    ),
    foreign_keys=(
        ForeignKey(
            ("tenant_id", "platform", "campaign_id"),
            "campaign",
            ("tenant_id", "platform", "campaign_id"),
        ),
    ),
)

# Ordered so that every table appears after the tables it references.
ALL_CONTRACTS: Final = (
    PRODUCT,
    LISTING,
    LOCATION,
    CAMPAIGN,
    CAMPAIGN_ITEM,
    SALES_DAILY,
    INVENTORY_SNAPSHOT,
    PURCHASE_ORDER_LINE,
    SHELF_OBSERVATION,
    SEARCH_RANK_OBSERVATION,
    AD_PERFORMANCE_DAILY,
)
