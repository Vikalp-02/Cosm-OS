"""End to end: generate a dataset, build the warehouse, and check that the
marts show each planted incident the way the engine will need to see it."""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from cosmos.generator import Cause, GeneratorConfig, build_dataset, write_dataset
from cosmos.warehouse import WarehouseBuildError, build

N_CITIES = 8

# Rows of the listing mart that fall inside an incident's platform, city and products.
IN_SCOPE = """
    marts.fct_listing_city_daily AS m, truth AS t
    WHERE t.cause = $cause AND m.platform = t.platform
      AND (t.city IS NULL OR m.city = t.city) AND list_contains(t.sku_ids, m.sku_id)
"""
DURING = "BETWEEN t.start_date AND t.end_date"
BEFORE = "< t.start_date"


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[duckdb.DuckDBPyConnection]:
    data_dir = tmp_path_factory.mktemp("data")
    write_dataset(build_dataset(GeneratorConfig.small()), data_dir)
    database = build(data_dir)

    connection = duckdb.connect(str(database), read_only=True)
    truth = (data_dir / "truth" / "planted_incident.parquet").as_posix()
    connection.execute(f"CREATE TEMP VIEW truth AS SELECT * FROM read_parquet('{truth}')")
    yield connection
    connection.close()


def _value(conn: duckdb.DuckDBPyConnection, sql: str, cause: Cause | None = None) -> Any:
    row = conn.execute(sql, {} if cause is None else {"cause": cause.value}).fetchone()
    assert row is not None
    return row[0]


def _in_scope(conn: duckdb.DuckDBPyConnection, cause: Cause, measure: str, when: str) -> Any:
    return _value(conn, f"SELECT {measure} FROM {IN_SCOPE} AND m.date_day {when}", cause)


def _truth(conn: duckdb.DuckDBPyConnection, cause: Cause, column: str) -> Any:
    return _value(conn, f"SELECT {column} FROM truth WHERE cause = $cause", cause)


def test_data_gap_is_flagged_where_it_was_planted_and_nowhere_else(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    flagged = """
        SELECT count(DISTINCT r.city) FROM marts.fct_sales_reporting_daily AS r, truth AS t
        WHERE t.cause = $cause AND r.is_gap AND r.platform = t.platform
          AND r.date_day BETWEEN t.start_date AND t.end_date
    """
    dropped_cities = math.ceil(_truth(conn, Cause.DATA_GAP, "magnitude") * N_CITIES)
    days = _truth(conn, Cause.DATA_GAP, "end_date - start_date + 1")
    assert _value(conn, flagged, Cause.DATA_GAP) == dropped_cities
    assert _value(conn, "SELECT count(*) FROM marts.fct_sales_reporting_daily WHERE is_gap") == (
        dropped_cities * days
    )


def test_unreported_sales_are_unknown_not_zero(conn: duckdb.DuckDBPyConnection) -> None:
    unknown = "SELECT count(*) FROM marts.fct_listing_city_daily WHERE NOT sales_reported"
    assert _value(conn, unknown) > 0
    assert _value(conn, f"{unknown} AND (units_sold IS NOT NULL OR gmv IS NOT NULL)") == 0
    assert _value(conn, f"{unknown.replace('NOT ', '')} AND units_sold IS NULL") == 0


@pytest.mark.parametrize("cause", [Cause.STOCKOUT, Cause.SUPPLY_SHORTFALL])
def test_shelf_incident_shows_as_lost_availability(
    conn: duckdb.DuckDBPyConnection, cause: Cause
) -> None:
    assert _in_scope(conn, cause, "avg(m.availability)", BEFORE) > 0.9
    assert _in_scope(conn, cause, "avg(m.availability)", DURING) < 1.05 - _truth(
        conn, cause, "magnitude"
    )


def test_supply_shortfall_shows_as_no_stock_and_lapsed_orders(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    cause = Cause.SUPPLY_SHORTFALL
    assert _in_scope(conn, cause, "sum(m.units_on_hand)", DURING) == 0
    assert _in_scope(conn, cause, "max(m.days_of_cover)", DURING) == 0
    assert _in_scope(conn, cause, "sum(m.po_units_lapsed)", "<= t.end_date") > 0
    # A plain stockout empties shelves while the warehouse still holds stock.
    assert _in_scope(conn, Cause.STOCKOUT, "min(m.units_on_hand)", DURING) > 0
    assert _in_scope(conn, Cause.STOCKOUT, "sum(m.po_units_lapsed)", "IS NOT NULL") == 0


def test_competitor_price_cut_shows_in_the_rival_price(conn: duckdb.DuckDBPyConnection) -> None:
    cause = Cause.COMPETITOR_PRICE_CUT
    before = _in_scope(conn, cause, "avg(m.rival_price_to_mrp)", BEFORE)
    during = _in_scope(conn, cause, "avg(m.rival_price_to_mrp)", DURING)
    assert during == pytest.approx(before * (1 - _truth(conn, cause, "magnitude")), rel=0.03)
    # The tenant's own price did not move.
    own_before = _in_scope(conn, cause, "avg(m.price_to_mrp)", BEFORE)
    assert _in_scope(conn, cause, "avg(m.price_to_mrp)", DURING) == pytest.approx(
        own_before, rel=0.03
    )


def test_rank_loss_shows_in_both_rank_measures(conn: duckdb.DuckDBPyConnection) -> None:
    cause = Cause.RANK_LOSS
    assert _in_scope(conn, cause, "avg(m.avg_organic_rank)", DURING) > 2 + _in_scope(
        conn, cause, "avg(m.avg_organic_rank)", BEFORE
    )
    assert _in_scope(conn, cause, "avg(m.organic_reciprocal_rank)", DURING) < 0.7 * _in_scope(
        conn, cause, "avg(m.organic_reciprocal_rank)", BEFORE
    )


def test_ad_budget_cut_shows_as_an_exhausted_budget(conn: duckdb.DuckDBPyConnection) -> None:
    cause = Cause.AD_BUDGET_CUT
    utilisation = """
        SELECT avg(c.budget_utilisation) FROM marts.fct_campaign_daily AS c, truth AS t
        WHERE t.cause = $cause AND c.platform = t.platform
          AND c.category = (
              SELECT category FROM marts.dim_product WHERE sku_id = t.sku_ids[1]
          )
          AND c.date_day {when}
    """
    assert _value(conn, utilisation.format(when=DURING), cause) > 0.95
    assert _value(conn, utilisation.format(when=BEFORE), cause) < 0.8
    assert _in_scope(conn, cause, "avg(m.sponsored_share)", DURING) < 0.6 * _in_scope(
        conn, cause, "avg(m.sponsored_share)", BEFORE
    )


def test_build_without_raw_data_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(WarehouseBuildError, match="run the generator first"):
        build(tmp_path)
