"""Warehouse stock and the purchase orders that replenish it.

Stock falls with sales and rises when an order lands. An order is raised when
stock and incoming units together drop below the reorder point.

A supply shortfall is a stretch where orders stop landing. Stock runs down over
the days before sales are hit, sits at zero while they are, and each order due
in that stretch lapses and is raised again, until one finally lands the day
after the shortfall ends. That leaves the trail a real one does: falling cover,
an empty warehouse and a run of lapsed orders.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from cosmos.generator._arrays import FloatArray, IntArray
from cosmos.generator.config import GeneratorConfig, Stream
from cosmos.generator.incidents import Cause, Incident
from cosmos.generator.world import PLATFORM_CODES, World

# Stock levels, in days of usual sales.
REORDER_COVER: Final = 8.0
TARGET_COVER: Final = 22.0
FULL_FILL_RATE: Final = 0.85
# Days before a shortfall reaches the shelf over which warehouse stock drains.
RUNDOWN_DAYS: Final = 3
_MAX_LEAD_DAYS: Final = 4
_NOT_BLOCKED: Final = -1


@dataclass(frozen=True, slots=True)
class OrderLines:
    number: tuple[str, ...]
    platform: IntArray
    city: IntArray
    item: IntArray
    order_day: IntArray
    promised_day: IntArray
    ordered: IntArray
    received: IntArray
    status: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Supply:
    on_hand: IntArray  # [P, C, I, D] end-of-day stock
    on_order: IntArray  # [P, C, I, D] units ordered and still expected
    lines: OrderLines


@dataclass(frozen=True, slots=True)
class _Order:
    number: str
    platform: int
    city: int
    items: IntArray
    day: int
    promised: IntArray
    ordered: IntArray
    delivered: IntArray


def simulate_supply(
    world: World, config: GeneratorConfig, units: IntArray, incidents: Sequence[Incident]
) -> Supply:
    rng = config.rng(Stream.SUPPLY)
    n_platforms, n_cities, _, days = units.shape
    usual = world.base_units
    reorder_point = REORDER_COVER * usual
    target = TARGET_COVER * usual
    lead = rng.integers(2, _MAX_LEAD_DAYS + 1, size=(n_platforms, n_cities))
    blocked_until, cover_cap = _shortfalls(units.shape, incidents)

    stock = np.ceil(usual * rng.uniform(12.0, 20.0, usual.shape)).astype(np.int64)
    on_order = np.zeros_like(stock)
    # Orders placed near the end land after the last day, hence the extra rows.
    arrivals = np.zeros((days + _MAX_LEAD_DAYS + 1, *usual.shape), dtype=np.int64)
    on_hand = np.empty(units.shape, dtype=np.int64)
    pending = np.empty(units.shape, dtype=np.int64)
    orders: list[_Order] = []
    raised = [0] * n_platforms

    for day in range(days):
        blocked = blocked_until[..., day] != _NOT_BLOCKED
        on_order -= arrivals[day]
        stock = stock + np.where(blocked, 0, arrivals[day])
        stock = np.maximum(stock - units[..., day], 0)
        ceiling = np.ceil(cover_cap[..., day] * usual).astype(np.int64)
        stock = np.where(blocked, np.minimum(stock, ceiling), stock)

        short = stock + on_order < reorder_point
        ordered = np.where(short, np.ceil(target - stock - on_order), 0).astype(np.int64)
        fill = np.where(
            rng.random(usual.shape) < FULL_FILL_RATE, 1.0, rng.uniform(0.6, 1.0, usual.shape)
        )
        delivered = np.maximum(np.floor(ordered * fill), np.minimum(ordered, 1)).astype(np.int64)

        for platform, city in np.argwhere(short.any(axis=2)).tolist():
            items = np.flatnonzero(short[platform, city])
            promised = np.full(items.size, day + int(lead[platform, city]), dtype=np.int64)
            # The first order promised past the end of a shortfall is the one that ends it.
            ends = blocked_until[platform, city, items, day]
            promised = np.where((ends != _NOT_BLOCKED) & (promised > ends), ends + 1, promised)
            arrivals[promised, platform, city, items] += delivered[platform, city, items]
            on_order[platform, city, items] += delivered[platform, city, items]
            raised[platform] += 1
            code = PLATFORM_CODES[world.platforms[platform]]
            orders.append(
                _Order(
                    number=f"{code}-PO-{raised[platform]:06d}",
                    platform=platform,
                    city=city,
                    items=items,
                    day=day,
                    promised=promised,
                    ordered=ordered[platform, city, items],
                    delivered=delivered[platform, city, items],
                )
            )
        on_hand[..., day] = stock
        pending[..., day] = on_order

    return Supply(on_hand=on_hand, on_order=pending, lines=_lines(orders, blocked_until, days))


def _shortfalls(
    shape: tuple[int, ...], incidents: Sequence[Incident]
) -> tuple[IntArray, FloatArray]:
    """Per [P, C, I, D]: the shortfall's last day where orders cannot land, and the
    most stock, in days of usual sales, the warehouse can hold while that lasts."""
    blocked_until = np.full(shape, _NOT_BLOCKED, dtype=np.int64)
    cover_cap = np.zeros(shape)
    for incident in incidents:
        if incident.cause is Cause.SUPPLY_SHORTFALL and incident.city is not None:
            cell = (incident.platform, incident.city, list(incident.items))
            first = max(0, incident.start - RUNDOWN_DAYS)
            blocked_until[(*cell, slice(first, incident.end + 1))] = incident.end
            for day in range(first, incident.start):
                cover_cap[(*cell, day)] = incident.start - day
    return blocked_until, cover_cap


def _lines(orders: Sequence[_Order], blocked_until: IntArray, days: int) -> OrderLines:
    def join(parts: Sequence[IntArray]) -> IntArray:
        if not parts:
            return np.zeros(0, dtype=np.int64)
        return np.concatenate(parts).astype(np.int64)

    numbers: list[str] = []
    statuses: list[str] = []
    received: list[IntArray] = []
    for order in orders:
        due = order.promised < days
        # Due while a shortfall was on, so it never landed.
        lapsed = due & (
            blocked_until[
                order.platform, order.city, order.items, np.minimum(order.promised, days - 1)
            ]
            != _NOT_BLOCKED
        )
        status = np.where(
            ~due,
            "open",
            np.where(
                lapsed,
                "expired",
                np.where(order.delivered == order.ordered, "received", "partially_received"),
            ),
        )
        numbers += [order.number] * order.items.size
        statuses += status.tolist()
        received.append(np.where(due & ~lapsed, order.delivered, 0))

    sizes = [order.items.size for order in orders]

    def per_line(values: Sequence[int]) -> IntArray:
        return np.repeat(np.array(values, dtype=np.int64), sizes)

    return OrderLines(
        number=tuple(numbers),
        platform=per_line([order.platform for order in orders]),
        city=per_line([order.city for order in orders]),
        item=join([order.items for order in orders]),
        order_day=per_line([order.day for order in orders]),
        promised_day=join([order.promised for order in orders]),
        ordered=join([order.ordered for order in orders]),
        received=join(received),
        status=tuple(statuses),
    )
