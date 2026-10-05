from __future__ import annotations

from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import pytest

from cosmos.contracts import ALL_CONTRACTS
from cosmos.generator import Cause, Dataset, GeneratorConfig, build_dataset, load, write_dataset
from cosmos.generator.__main__ import main

CONFIG = GeneratorConfig.small()

# The incident's listings, as platform item ids.
ITEMS = """(
    SELECT l.platform_item_id FROM raw.listing AS l, truth AS t
    WHERE t.incident_id = $id AND l.platform = t.platform AND list_contains(t.sku_ids, l.sku_id)
)"""


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return build_dataset(CONFIG)


@pytest.fixture(scope="module")
def conn(dataset: Dataset) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(":memory:")
    load(connection, dataset.tables)
    connection.register("truth", dataset.incidents)
    yield connection
    connection.close()


def _incident(dataset: Dataset, cause: Cause) -> dict[str, Any]:
    (row,) = [row for row in dataset.incidents.to_pylist() if row["cause"] == cause.value]
    return dict(row)


def _value(conn: duckdb.DuckDBPyConnection, sql: str, incident: dict[str, Any]) -> Any:
    """Run a query that reads one incident through `$id`, `$start` and `$end`."""
    params = {
        "id": incident["incident_id"],
        "start": incident["start_date"],
        "end": incident["end_date"],
    }
    used = {name: value for name, value in params.items() if f"${name}" in sql}
    row = conn.execute(sql, used).fetchone()
    assert row is not None
    return row[0]


def test_every_table_is_filled_and_meets_its_contract(conn: duckdb.DuckDBPyConnection) -> None:
    # `load` has already raised if any contract was broken.
    for contract in ALL_CONTRACTS:
        row = conn.execute(f"SELECT count(*) FROM raw.{contract.name}").fetchone()
        assert row is not None and row[0] > 0, contract.name


def test_same_seed_gives_the_same_dataset(dataset: Dataset) -> None:
    again = build_dataset(CONFIG)
    assert again.incidents.equals(dataset.incidents)
    for name, table in dataset.tables.items():
        assert again.tables[name].equals(table), name


def test_another_seed_gives_another_dataset(dataset: Dataset) -> None:
    other = build_dataset(GeneratorConfig.small(seed=CONFIG.seed + 1))
    assert not other.tables["sales_daily"].equals(dataset.tables["sales_daily"])


def test_incidents_cover_every_cause_and_never_overlap(dataset: Dataset) -> None:
    incidents = dataset.incidents.to_pylist()
    assert {row["cause"] for row in incidents} == {cause.value for cause in Cause}

    first_allowed = CONFIG.start_date.toordinal() + CONFIG.warmup_days
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for row in incidents:
        assert row["start_date"].toordinal() >= first_allowed
        assert row["start_date"] <= row["end_date"]
        by_platform.setdefault(row["platform"], []).append(row)
    for rows in by_platform.values():
        rows.sort(key=lambda row: row["start_date"])
        for earlier, later in pairwise(rows):
            assert earlier["end_date"] < later["start_date"]


def test_incidents_that_hit_demand_have_a_cost(dataset: Dataset) -> None:
    for row in dataset.incidents.to_pylist():
        if row["cause"] == Cause.DATA_GAP.value:
            assert row["expected_units_lost"] == 0
            assert row["expected_gmv_lost"] == 0
            assert row["reported_units_dropped"] > 0
        else:
            assert row["expected_units_lost"] > 0, row["incident_id"]
            assert row["expected_gmv_lost"] > 0, row["incident_id"]
            assert row["reported_units_dropped"] == 0


@pytest.mark.parametrize("cause", [Cause.STOCKOUT, Cause.SUPPLY_SHORTFALL])
def test_shelf_incident_empties_shelves_in_its_city(
    conn: duckdb.DuckDBPyConnection, dataset: Dataset, cause: Cause
) -> None:
    incident = _incident(dataset, cause)
    availability = f"""
        SELECT avg(o.is_available::INT) FROM raw.shelf_observation AS o
        JOIN raw.location AS s USING (platform, location_id), truth AS t
        WHERE t.incident_id = $id AND o.platform = t.platform AND s.city = t.city
          AND o.platform_item_id IN {ITEMS} AND o.observed_date {{when}}
    """
    during = _value(conn, availability.format(when="BETWEEN $start AND $end"), incident)
    before = _value(conn, availability.format(when="< $start"), incident)
    assert before > 0.9
    assert during < 1.05 - incident["magnitude"]


def test_supply_shortfall_drains_the_warehouse_and_lapses_orders(
    conn: duckdb.DuckDBPyConnection, dataset: Dataset
) -> None:
    incident = _incident(dataset, Cause.SUPPLY_SHORTFALL)
    stock = _value(
        conn,
        f"""
        SELECT sum(i.units_on_hand) FROM raw.inventory_snapshot AS i, truth AS t
        WHERE t.incident_id = $id AND i.platform = t.platform AND i.city = t.city
          AND i.platform_item_id IN {ITEMS} AND i.snapshot_date BETWEEN $start AND $end
        """,
        incident,
    )
    lapsed_items = _value(
        conn,
        f"""
        SELECT count(DISTINCT p.platform_item_id) FROM raw.purchase_order_line AS p, truth AS t
        WHERE t.incident_id = $id AND p.platform = t.platform AND p.city = t.city
          AND p.platform_item_id IN {ITEMS} AND p.status = 'expired' AND p.units_received = 0
          AND p.expected_delivery_date <= $end
        """,
        incident,
    )
    assert stock == 0
    assert lapsed_items == len(incident["sku_ids"])


def test_only_a_supply_shortfall_lapses_orders(
    conn: duckdb.DuckDBPyConnection, dataset: Dataset
) -> None:
    incident = _incident(dataset, Cause.SUPPLY_SHORTFALL)
    elsewhere = _value(
        conn,
        f"""
        SELECT count(*) FROM raw.purchase_order_line AS p, truth AS t
        WHERE t.incident_id = $id AND p.status = 'expired'
          AND NOT (p.platform = t.platform AND p.city = t.city
                   AND p.platform_item_id IN {ITEMS})
        """,
        incident,
    )
    assert elsewhere == 0


def test_competitor_price_cut_lowers_rival_prices(
    conn: duckdb.DuckDBPyConnection, dataset: Dataset
) -> None:
    incident = _incident(dataset, Cause.COMPETITOR_PRICE_CUT)
    rival_price = """
        SELECT avg(o.selling_price) FROM raw.shelf_observation AS o
        JOIN raw.listing AS l USING (tenant_id, platform, platform_item_id)
        JOIN raw.product AS p USING (tenant_id, sku_id), truth AS t
        WHERE t.incident_id = $id AND o.platform = t.platform AND p.is_competitor
          AND p.category = (SELECT category FROM raw.product WHERE sku_id = t.sku_ids[1])
          AND o.observed_date {when}
    """
    during = _value(conn, rival_price.format(when="BETWEEN $start AND $end"), incident)
    before = _value(conn, rival_price.format(when="< $start"), incident)
    assert float(during) == pytest.approx(float(before) * (1 - incident["magnitude"]), rel=0.03)


def test_rank_loss_pushes_listings_down_the_page(
    conn: duckdb.DuckDBPyConnection, dataset: Dataset
) -> None:
    incident = _incident(dataset, Cause.RANK_LOSS)
    rank = f"""
        SELECT avg(r.search_rank) FROM raw.search_rank_observation AS r, truth AS t
        WHERE t.incident_id = $id AND r.platform = t.platform AND NOT r.is_sponsored
          AND r.platform_item_id IN {ITEMS} AND r.observed_date {{when}}
    """
    during = _value(conn, rank.format(when="BETWEEN $start AND $end"), incident)
    before = _value(conn, rank.format(when="< $start"), incident)
    assert during > before + 2


def test_ad_budget_cut_shows_in_budget_and_spend(
    conn: duckdb.DuckDBPyConnection, dataset: Dataset
) -> None:
    incident = _incident(dataset, Cause.AD_BUDGET_CUT)
    campaign = f"""(
        SELECT DISTINCT c.campaign_id FROM raw.campaign_item AS c, truth AS t
        WHERE t.incident_id = $id AND c.platform = t.platform AND c.platform_item_id IN {ITEMS}
    )"""
    budget = f"""
        SELECT avg(d.daily_budget) FROM raw.campaign_daily AS d, truth AS t
        WHERE t.incident_id = $id AND d.platform = t.platform AND d.campaign_id IN {campaign}
          AND d.report_date {{when}}
    """
    spend = f"""
        SELECT sum(a.spend) / count(DISTINCT a.report_date)
        FROM raw.ad_performance_daily AS a, truth AS t
        WHERE t.incident_id = $id AND a.platform = t.platform AND a.campaign_id IN {campaign}
          AND a.report_date {{when}}
    """
    cut = 1 - incident["magnitude"]
    budget_during = _value(conn, budget.format(when="BETWEEN $start AND $end"), incident)
    budget_before = _value(conn, budget.format(when="< $start"), incident)
    spend_during = _value(conn, spend.format(when="BETWEEN $start AND $end"), incident)
    spend_before = _value(conn, spend.format(when="< $start"), incident)
    assert float(budget_during) == pytest.approx(float(budget_before) * cut, rel=0.01)
    assert spend_during <= budget_during
    assert spend_during < 0.6 * spend_before


def test_data_gap_removes_cities_from_sales(
    conn: duckdb.DuckDBPyConnection, dataset: Dataset
) -> None:
    incident = _incident(dataset, Cause.DATA_GAP)
    cities = """
        SELECT count(DISTINCT s.city) FROM raw.sales_daily AS s, truth AS t
        WHERE t.incident_id = $id AND s.platform = t.platform AND s.report_date {when}
    """
    during = _value(conn, cities.format(when="BETWEEN $start AND $end"), incident)
    before = _value(conn, cities.format(when="< $start"), incident)
    assert during < before * (1.01 - incident["magnitude"]) + 1
    assert during > 0


def test_ads_sell_about_their_intended_share(conn: duckdb.DuckDBPyConnection) -> None:
    row = conn.execute(
        "SELECT (SELECT sum(attributed_orders) FROM raw.ad_performance_daily)"
        " / (SELECT sum(units_sold) FROM raw.sales_daily)"
    ).fetchone()
    assert row is not None
    assert 0.15 < row[0] < 0.25


def test_too_many_incidents_for_the_timeline_is_rejected() -> None:
    crowded = GeneratorConfig(days=70, warmup_days=28, stores_per_city=2, incidents=10)
    with pytest.raises(ValueError, match="do not fit"):
        build_dataset(crowded)


def test_cli_writes_raw_tables_and_keeps_truth_apart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--small", "--out", str(tmp_path)]) == 0
    assert "6 incidents planted" in capsys.readouterr().out
    written = {path.name for path in (tmp_path / "raw").iterdir()}
    assert written == {f"{contract.name}.parquet" for contract in ALL_CONTRACTS}
    assert (tmp_path / "truth" / "planted_incident.parquet").is_file()


def test_writing_twice_replaces_the_files(tmp_path: Path, dataset: Dataset) -> None:
    first = write_dataset(dataset, tmp_path)
    second = write_dataset(dataset, tmp_path)
    assert first == second
    assert not list(tmp_path.rglob("*.partial"))
