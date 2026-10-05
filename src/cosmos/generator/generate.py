"""Build a complete synthetic dataset and the ground truth that goes with it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pyarrow as pa

from cosmos.generator._arrays import BoolArray, IntArray
from cosmos.generator.config import GeneratorConfig
from cosmos.generator.incidents import Incident, plan_incidents
from cosmos.generator.simulate import (
    Market,
    draw_noise,
    dropped_sales,
    sample_ads,
    sample_units,
    simulate_market,
)
from cosmos.generator.supply import simulate_supply
from cosmos.generator.tables import build_tables
from cosmos.generator.world import World, build_world

INCIDENT_SCHEMA = pa.schema(
    [
        ("incident_id", pa.string()),
        ("tenant_id", pa.string()),
        ("cause", pa.string()),
        ("platform", pa.string()),
        ("city", pa.string()),
        ("sku_ids", pa.list_(pa.string())),
        ("start_date", pa.date32()),
        ("end_date", pa.date32()),
        ("magnitude", pa.float64()),
        ("expected_units_lost", pa.float64()),
        ("expected_gmv_lost", pa.float64()),
        ("reported_units_dropped", pa.int64()),
    ]
)


@dataclass(frozen=True, slots=True)
class Dataset:
    config: GeneratorConfig
    # Raw tables, keyed by contract name.
    tables: dict[str, pa.Table]
    # What was planted and what it cost. Never an input to the product.
    incidents: pa.Table


def build_dataset(config: GeneratorConfig) -> Dataset:
    world = build_world(config)
    incidents = plan_incidents(world, config)
    noise = draw_noise(world, config)
    actual = simulate_market(world, config, noise, incidents)
    undisturbed = simulate_market(world, config, noise, ())

    units = sample_units(config, actual)
    dropped = dropped_sales(world, config, incidents)
    all_dropped = np.zeros((len(world.platforms), len(world.cities), config.days), dtype=np.bool_)
    for cells in dropped.values():
        all_dropped |= cells

    tables = build_tables(
        world,
        config,
        actual,
        units,
        sample_ads(world, config, actual),
        simulate_supply(world, config, units, incidents),
        all_dropped,
    )
    truth = _truth(world, config, incidents, actual, undisturbed, units, dropped)
    return Dataset(config=config, tables=tables, incidents=truth)


def _truth(
    world: World,
    config: GeneratorConfig,
    incidents: Sequence[Incident],
    actual: Market,
    undisturbed: Market,
    units: IntArray,
    dropped: dict[int, BoolArray],
) -> pa.Table:
    lost = undisturbed.expected_units - actual.expected_units
    rows = []
    for incident in incidents:
        cities = range(len(world.cities)) if incident.city is None else (incident.city,)
        units_lost = lost[incident.platform][np.ix_(cities, incident.items, incident.days)]
        price = actual.price[incident.platform][np.ix_(incident.items, incident.days)]
        cells = dropped.get(incident.number)
        rows.append(
            {
                "incident_id": incident.id,
                "tenant_id": config.tenant_id,
                "cause": incident.cause.value,
                "platform": world.platforms[incident.platform],
                "city": None if incident.city is None else world.cities[incident.city],
                "sku_ids": [world.sku_ids[item] for item in incident.items],
                "start_date": config.start_date + timedelta(days=incident.start),
                "end_date": config.start_date + timedelta(days=incident.end),
                "magnitude": incident.magnitude,
                "expected_units_lost": round(float(units_lost.sum()), 1),
                "expected_gmv_lost": round(float((units_lost * price[None]).sum()), 2),
                "reported_units_dropped": (
                    0 if cells is None else int(units[cells[:, :, None, :] & (units > 0)].sum())
                ),
            }
        )
    return pa.Table.from_pylist(rows, schema=INCIDENT_SCHEMA)
