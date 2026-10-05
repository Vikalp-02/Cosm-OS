from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date
from decimal import Decimal

import duckdb
import pytest

from cosmos.contracts import (
    ALL_CONTRACTS,
    Column,
    Rule,
    SqlType,
    TableContract,
    Violation,
    create_table_sql,
    validate_dataset,
    validate_table,
)
from cosmos.contracts.tables import SALES_DAILY

DAY = date(2026, 9, 1)
LISTING_KEY: dict[str, object] = {
    "tenant_id": "acme",
    "platform": "blinkit",
    "platform_item_id": "B-1",
}
CAMPAIGN_KEY: dict[str, object] = {
    "tenant_id": "acme",
    "platform": "blinkit",
    "campaign_id": "C-1",
}

# One valid row per table, consistent across tables.
ROWS: dict[str, dict[str, object]] = {
    "product": {
        "tenant_id": "acme",
        "sku_id": "SKU-1",
        "brand": "Acme",
        "product_name": "Acme Oats 1kg",
        "category": "Breakfast",
        "is_competitor": False,
        "mrp": Decimal("299.00"),
    },
    "listing": {**LISTING_KEY, "sku_id": "SKU-1", "ean": None},
    "location": {
        "platform": "blinkit",
        "location_id": "L-1",
        "pincode": "560034",
        "city": "Bengaluru",
        "state": "Karnataka",
    },
    "campaign": {**CAMPAIGN_KEY, "campaign_name": "Oats search", "daily_budget": None},
    "campaign_item": {**CAMPAIGN_KEY, "platform_item_id": "B-1"},
    "sales_daily": {
        **LISTING_KEY,
        "report_date": DAY,
        "city": "Bengaluru",
        "units_sold": 12,
        "gmv": Decimal("3588.00"),
    },
    "inventory_snapshot": {
        **LISTING_KEY,
        "snapshot_date": DAY,
        "facility_id": "F-1",
        "city": "Bengaluru",
        "units_on_hand": 40,
        "open_po_units": None,
    },
    "purchase_order_line": {
        **LISTING_KEY,
        "po_number": "PO-1",
        "facility_id": "F-1",
        "city": "Bengaluru",
        "order_date": DAY,
        "expected_delivery_date": None,
        "status": "open",
        "units_ordered": 100,
        "units_received": 0,
        "line_value": Decimal("20000.00"),
    },
    "shelf_observation": {
        **LISTING_KEY,
        "observed_date": DAY,
        "location_id": "L-1",
        "is_available": True,
        "selling_price": Decimal("279.00"),
    },
    "search_rank_observation": {
        **LISTING_KEY,
        "observed_date": DAY,
        "pincode": "560034",
        "keyword": "oats",
        "is_sponsored": False,
        "search_rank": 3,
    },
    "ad_performance_daily": {
        **CAMPAIGN_KEY,
        "report_date": DAY,
        "keyword": "oats",
        "impressions": 1000,
        "clicks": 40,
        "spend": Decimal("800.00"),
        "attributed_orders": 6,
        "attributed_revenue": Decimal("1794.00"),
    },
}
RELATIONS = {contract.name: f"raw.{contract.name}" for contract in ALL_CONTRACTS}


def _insert(conn: duckdb.DuckDBPyConnection, table: str, row: Mapping[str, object]) -> None:
    columns = ", ".join(row)
    slots = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({slots})", list(row.values()))


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA raw")
    for contract in ALL_CONTRACTS:
        connection.execute(create_table_sql(contract, "raw"))
        _insert(connection, RELATIONS[contract.name], ROWS[contract.name])
    yield connection
    connection.close()


def _sales_with(conn: duckdb.DuckDBPyConnection, **overrides: object) -> list[Violation]:
    """Validate a constraint-free copy of sales_daily holding one extra row."""
    conn.execute("CREATE TABLE loose AS SELECT * FROM raw.sales_daily")
    _insert(conn, "loose", {**ROWS["sales_daily"], "report_date": date(2026, 9, 2), **overrides})
    return validate_table(conn, SALES_DAILY, "loose")


def test_contracts_reference_earlier_tables_by_their_grain() -> None:
    seen: dict[str, TableContract] = {}
    for contract in ALL_CONTRACTS:
        for key in contract.foreign_keys:
            assert key.references in seen, f"{contract.name} references a later table"
            assert key.referenced_columns == seen[key.references].grain
        seen[contract.name] = contract
    assert len(seen) == len(ALL_CONTRACTS)


def test_valid_dataset_has_no_violations(conn: duckdb.DuckDBPyConnection) -> None:
    assert validate_dataset(conn, ALL_CONTRACTS, RELATIONS) == []


def test_null_in_required_column_is_reported(conn: duckdb.DuckDBPyConnection) -> None:
    assert _sales_with(conn, units_sold=None) == [
        Violation("sales_daily", Rule.NULL_VALUE, "units_sold", 1)
    ]


def test_failed_check_is_reported(conn: duckdb.DuckDBPyConnection) -> None:
    assert _sales_with(conn, gmv=Decimal("-1.00")) == [
        Violation("sales_daily", Rule.FAILED_CHECK, "gmv >= 0", 1)
    ]


def test_blank_identifier_is_reported(conn: duckdb.DuckDBPyConnection) -> None:
    assert _sales_with(conn, city="  ") == [
        Violation("sales_daily", Rule.FAILED_CHECK, "length(trim(city)) > 0", 1)
    ]


def test_unknown_platform_is_reported(conn: duckdb.DuckDBPyConnection) -> None:
    (violation,) = _sales_with(conn, platform="amazon")
    assert violation.rule is Rule.FAILED_CHECK
    assert violation.rows == 1


def test_duplicate_grain_counts_surplus_rows(conn: duckdb.DuckDBPyConnection) -> None:
    violations = _sales_with(conn, report_date=DAY)
    assert violations == [
        Violation(
            "sales_daily",
            Rule.DUPLICATE_GRAIN,
            "tenant_id, platform, report_date, platform_item_id, city",
            1,
        )
    ]


def test_missing_column_stops_row_checks(conn: duckdb.DuckDBPyConnection) -> None:
    relation = "(SELECT * EXCLUDE (gmv) FROM raw.sales_daily)"
    assert validate_table(conn, SALES_DAILY, relation) == [
        Violation("sales_daily", Rule.MISSING_COLUMN, "gmv")
    ]


def test_wrong_type_is_reported(conn: duckdb.DuckDBPyConnection) -> None:
    relation = "(SELECT * REPLACE (CAST(units_sold AS VARCHAR) AS units_sold) FROM raw.sales_daily)"
    assert validate_table(conn, SALES_DAILY, relation) == [
        Violation("sales_daily", Rule.WRONG_TYPE, "units_sold: expected BIGINT, found VARCHAR")
    ]


def test_unexpected_column_is_reported_without_blocking(conn: duckdb.DuckDBPyConnection) -> None:
    relation = "(SELECT *, 1 AS extra FROM raw.sales_daily)"
    assert validate_table(conn, SALES_DAILY, relation) == [
        Violation("sales_daily", Rule.UNEXPECTED_COLUMN, "extra")
    ]


def test_row_without_parent_is_reported(conn: duckdb.DuckDBPyConnection) -> None:
    _insert(conn, "raw.sales_daily", {**ROWS["sales_daily"], "platform_item_id": "B-404"})
    assert validate_dataset(conn, ALL_CONTRACTS, RELATIONS) == [
        Violation(
            "sales_daily",
            Rule.ORPHAN_ROW,
            "(tenant_id, platform, platform_item_id) not found in listing",
            1,
        )
    ]


def test_references_to_an_unsound_table_are_skipped(conn: duckdb.DuckDBPyConnection) -> None:
    relations = {**RELATIONS, "listing": "(SELECT * EXCLUDE (platform_item_id) FROM raw.listing)"}
    assert validate_dataset(conn, ALL_CONTRACTS, relations) == [
        Violation("listing", Rule.MISSING_COLUMN, "platform_item_id")
    ]


def test_contract_rejects_nullable_grain() -> None:
    with pytest.raises(ValueError, match="cannot be nullable"):
        TableContract(
            name="bad",
            description="",
            grain=("id",),
            columns=(Column("id", SqlType.VARCHAR, "", nullable=True),),
        )
