"""The causal model behind the synthetic data.

Expected sales of a listing in a city on a day are

    usual units x weekday x (organic share x availability x price x visibility
                             + ad share x availability x ad delivery)

Each incident moves one of those inputs. All randomness is drawn up front,
independently of the incidents, so the market can be simulated twice from the
same draws: once as it happened and once as it would have been with no
incidents. The difference between the two is the revenue each incident cost.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

import numpy as np

from cosmos.generator._arrays import BoolArray, Float32Array, FloatArray, IntArray
from cosmos.generator.config import GeneratorConfig, Stream
from cosmos.generator.incidents import Cause, Incident
from cosmos.generator.world import (
    AD_SHARE,
    ITEMS_PER_CATEGORY,
    KEYWORDS_PER_CATEGORY,
    OWN_PER_CATEGORY,
    World,
)

# A listing on a store's shelf drops out and comes back as a two-state chain.
FAIL_RATE: Final = 0.015
RECOVERY_RATE: Final = 0.35
STEADY_AVAILABILITY: Final = RECOVERY_RATE / (RECOVERY_RATE + FAIL_RATE)

PROMO_RATE: Final = 0.04
PROMO_DEPTH: Final = 0.10
OWN_PRICE_ELASTICITY: Final = 1.5
CROSS_PRICE_ELASTICITY: Final = 0.8

RANK_NOISE: Final = 0.35
# Attention falls off with position: weight = rank ** -RANK_DECAY.
RANK_DECAY: Final = 0.8
VISIBILITY_ELASTICITY: Final = 0.6

_WEEKDAY: Final = np.array([1.0, 0.95, 0.95, 1.0, 1.1, 1.25, 1.2])
_SHELF_CAUSES: Final = (Cause.STOCKOUT, Cause.SUPPLY_SHORTFALL)


@dataclass(frozen=True, slots=True)
class Noise:
    shelf: tuple[Float32Array, ...]  # [P][stores, I, D] uniform draws
    promo: BoolArray  # [P, I, D]
    rank: tuple[Float32Array, ...]  # [P][D, N, Q, J] standard normal draws
    demand: FloatArray  # [P, C, D] multiplier
    impressions: FloatArray  # [P, K, M, D] multiplier


@dataclass(frozen=True, slots=True)
class Market:
    on_shelf: tuple[BoolArray, ...]  # [P][stores, I, D]
    availability: FloatArray  # [P, C, I, D] share of the city's stores listing the item
    price: FloatArray  # [P, I, D]
    organic_rank: tuple[IntArray, ...]  # [P][D, N, Q, J] position among organic results
    budget: FloatArray  # [P, K, D]
    delivery: FloatArray  # [P, K, D] share of wanted impressions the budget paid for
    impressions: FloatArray  # [P, K, M, D] expected
    ad_availability: FloatArray  # [P, K, D] availability of what a campaign advertises
    expected_units: FloatArray  # [P, C, I, D]


@dataclass(frozen=True, slots=True)
class AdReport:
    impressions: IntArray  # [P, K, M, D]
    clicks: IntArray
    spend: FloatArray
    orders: IntArray
    revenue: FloatArray


def weekday_factor(config: GeneratorConfig) -> FloatArray:
    weekdays = [(config.start_date + timedelta(days=day)).weekday() for day in range(config.days)]
    factor: FloatArray = _WEEKDAY[np.array(weekdays)]
    return factor


def draw_noise(world: World, config: GeneratorConfig) -> Noise:
    rng = config.rng(Stream.NOISE)
    n_platforms, n_cities = len(world.platforms), len(world.cities)
    n_categories, n_items, days = len(world.categories), world.n_items, config.days
    rank_shape = (days, len(world.crawl_pincodes), len(world.keywords), ITEMS_PER_CATEGORY)
    return Noise(
        shelf=tuple(
            rng.random((len(stores), n_items, days), dtype=np.float32) for stores in world.store_ids
        ),
        promo=rng.random((n_platforms, n_items, days)) < PROMO_RATE,
        rank=tuple(rng.standard_normal(rank_shape, dtype=np.float32) for _ in world.platforms),
        demand=rng.lognormal(0.0, 0.05, (n_platforms, n_cities, days)),
        impressions=rng.lognormal(
            0.0, 0.08, (n_platforms, n_categories, KEYWORDS_PER_CATEGORY, days)
        ),
    )


def simulate_market(
    world: World, config: GeneratorConfig, noise: Noise, incidents: Sequence[Incident]
) -> Market:
    n_platforms, n_cities, n_categories = (
        len(world.platforms),
        len(world.cities),
        len(world.categories),
    )
    n_items, days = world.n_items, config.days
    by_category = (n_platforms, n_cities, n_categories, ITEMS_PER_CATEGORY)

    on_shelf: list[BoolArray] = []
    organic_rank: list[IntArray] = []
    availability = np.empty((n_platforms, n_cities, n_items, days))
    visibility = np.empty((n_platforms, n_cities, n_items, days))
    for platform in range(n_platforms):
        local = [incident for incident in incidents if incident.platform == platform]
        shelf = _shelf(world, config, noise.shelf[platform], local)
        rank = _organic_rank(world, noise.rank[platform], platform, local)
        on_shelf.append(shelf)
        organic_rank.append(rank)
        stores = _city_share(world.store_city[platform], n_cities)
        availability[platform] = np.einsum("cs,sid->cid", stores, shelf.astype(np.float64))
        visibility[platform] = _visibility(world, config, rank)

    listed = np.round(world.mrp[None] * (1.0 - world.discount))
    price = _prices(world, noise, listed, incidents)
    price_effect = _price_effect(price, listed)

    weekday = weekday_factor(config)
    wanted = world.base_impressions[..., None] * weekday * noise.impressions
    wanted_spend = (wanted * (world.ctr * world.cpc)[..., None]).sum(axis=2)
    budget = np.repeat(world.base_budget[:, :, None], days, axis=2)
    for incident in incidents:
        if incident.cause is Cause.AD_BUDGET_CUT:
            category = int(world.category_of[incident.items[0]])
            window = slice(incident.start, incident.end + 1)
            budget[incident.platform, category, window] = np.round(
                budget[incident.platform, category, window] * (1.0 - incident.magnitude)
            )
    delivery = np.minimum(1.0, budget / wanted_spend)
    impressions = wanted * delivery[:, :, None, :]

    # An ad can only sell what is on the shelf, so ad sales follow availability too.
    pull = world.base_units[..., None] * availability
    category_pull = pull.reshape(*by_category, days).sum(axis=(1, 3))
    category_base = world.base_units.reshape(by_category).sum(axis=(1, 3))
    conversions = (impressions * (world.ctr * world.cvr)[..., None]).sum(axis=2)
    per_unit = np.repeat(conversions / category_base[:, :, None], ITEMS_PER_CATEGORY, axis=1)
    ad_units = pull * per_unit[:, None, :, :]

    organic = (
        (1.0 - AD_SHARE)
        * pull
        * (weekday * noise.demand)[:, :, None, :]
        * price_effect[:, None, :, :]
        * visibility
    )
    return Market(
        on_shelf=tuple(on_shelf),
        availability=availability,
        price=price,
        organic_rank=tuple(organic_rank),
        budget=budget,
        delivery=delivery,
        impressions=impressions,
        ad_availability=category_pull / category_base[:, :, None],
        expected_units=organic + ad_units,
    )


def sample_units(config: GeneratorConfig, market: Market) -> IntArray:
    return config.rng(Stream.SALES).poisson(market.expected_units)


def sample_ads(world: World, config: GeneratorConfig, market: Market) -> AdReport:
    rng = config.rng(Stream.ADS)
    n_platforms, n_categories = len(world.platforms), len(world.categories)
    impressions = rng.poisson(market.impressions)
    clicks = rng.binomial(impressions, world.ctr[..., None])

    spend = clicks * world.cpc[..., None] * rng.uniform(0.9, 1.1, clicks.shape)
    total = spend.sum(axis=2)
    within_budget = np.minimum(1.0, market.budget / np.maximum(total, 1e-9))
    spend = np.floor(spend * within_budget[:, :, None, :] * 100.0) / 100.0

    converts = world.cvr[..., None] * market.ad_availability[:, :, None, :]
    orders = rng.binomial(clicks, np.clip(converts, 0.0, 1.0))
    own_price = market.price.reshape(n_platforms, n_categories, ITEMS_PER_CATEGORY, -1)[
        :, :, :OWN_PER_CATEGORY, :
    ].mean(axis=2)
    return AdReport(
        impressions=impressions,
        clicks=clicks,
        spend=spend,
        orders=orders,
        revenue=np.round(orders * own_price[:, :, None, :], 2),
    )


def affected_stores(world: World, config: GeneratorConfig, incident: Incident) -> IntArray:
    """The stores a shelf incident takes out, chosen the same way on every run."""
    if incident.city is None:
        raise ValueError(f"{incident.id}: a shelf incident needs a city")
    local = np.flatnonzero(world.store_city[incident.platform] == incident.city)
    count = math.ceil(incident.magnitude * local.size)
    chosen = config.rng(Stream.INCIDENT, incident.number).permutation(local)[:count]
    return np.sort(chosen)


def dropped_sales(
    world: World, config: GeneratorConfig, incidents: Sequence[Incident]
) -> dict[int, BoolArray]:
    """For each data-gap incident, the [P, C, D] cells whose sales rows went missing."""
    shape = (len(world.platforms), len(world.cities), config.days)
    dropped: dict[int, BoolArray] = {}
    for incident in incidents:
        if incident.cause is Cause.DATA_GAP:
            count = math.ceil(incident.magnitude * len(world.cities))
            cities = config.rng(Stream.INCIDENT, incident.number).permutation(len(world.cities))
            cells = np.zeros(shape, dtype=np.bool_)
            cells[incident.platform, cities[:count], incident.start : incident.end + 1] = True
            dropped[incident.number] = cells
    return dropped


def _city_share(city_of: IntArray, n_cities: int) -> FloatArray:
    """A [C, n] matrix that averages a per-store or per-pincode value into cities."""
    share = np.zeros((n_cities, city_of.size))
    share[city_of, np.arange(city_of.size)] = 1.0
    averaged: FloatArray = share / share.sum(axis=1, keepdims=True)
    return averaged


def _shelf(
    world: World, config: GeneratorConfig, draws: Float32Array, incidents: Sequence[Incident]
) -> BoolArray:
    state = draws[:, :, 0] < STEADY_AVAILABILITY
    shelf = np.empty(draws.shape, dtype=np.bool_)
    shelf[:, :, 0] = state
    for day in range(1, draws.shape[2]):
        draw = draws[:, :, day]
        state = np.where(state, draw >= FAIL_RATE, draw < RECOVERY_RATE)
        shelf[:, :, day] = state

    for incident in incidents:
        if incident.cause in _SHELF_CAUSES:
            stores = affected_stores(world, config, incident)
            window = np.asarray(incident.days)
            shelf[np.ix_(stores, np.asarray(incident.items), window)] = False
    return shelf


def _organic_rank(
    world: World, draws: Float32Array, platform: int, incidents: Sequence[Incident]
) -> IntArray:
    score = world.relevance[platform][None, None] + RANK_NOISE * draws.astype(np.float64)
    for incident in incidents:
        if incident.cause is Cause.RANK_LOSS:
            first = int(world.category_of[incident.items[0]]) * KEYWORDS_PER_CATEGORY
            keywords = range(first, first + KEYWORDS_PER_CATEGORY)
            slots = [item % ITEMS_PER_CATEGORY for item in incident.items]
            score[np.ix_(incident.days, range(score.shape[1]), keywords, slots)] -= (
                incident.magnitude
            )
    best_first = np.argsort(-score, axis=-1)
    rank: IntArray = np.argsort(best_first, axis=-1) + 1
    return rank


def _visibility(world: World, config: GeneratorConfig, rank: IntArray) -> FloatArray:
    """Demand multiplier per [C, I, D] from where the item ranks, against its warm-up norm."""
    days, pincodes, keywords, slots = rank.shape
    categories = keywords // KEYWORDS_PER_CATEGORY
    attention = (rank.astype(np.float64) ** -RANK_DECAY).reshape(
        days, pincodes, categories, KEYWORDS_PER_CATEGORY, slots
    )
    seen = np.einsum("dnkmj,km->dnkj", attention, world.keyword_volume).reshape(
        days, pincodes, categories * slots
    )
    by_city = np.einsum("cn,dni->cid", _city_share(world.crawl_city, len(world.cities)), seen)
    usual = by_city[:, :, : config.warmup_days].mean(axis=2, keepdims=True)
    effect: FloatArray = (by_city / usual) ** VISIBILITY_ELASTICITY
    return effect


def _prices(
    world: World, noise: Noise, listed: FloatArray, incidents: Sequence[Incident]
) -> FloatArray:
    price: FloatArray = np.round(listed[:, :, None] * np.where(noise.promo, 1.0 - PROMO_DEPTH, 1.0))
    for incident in incidents:
        if incident.cause is Cause.COMPETITOR_PRICE_CUT:
            category = world.category_of[incident.items[0]]
            rivals = np.flatnonzero((world.category_of == category) & world.is_competitor)
            window = slice(incident.start, incident.end + 1)
            price[incident.platform, rivals, window] = np.round(
                price[incident.platform, rivals, window] * (1.0 - incident.magnitude)
            )
    return price


def _price_effect(price: FloatArray, listed: FloatArray) -> FloatArray:
    """Demand multiplier per [P, I, D] from an item's own price and its rivals' prices."""
    ratio = price / listed[:, :, None]
    n_platforms, _, days = ratio.shape
    rivals = ratio.reshape(n_platforms, -1, ITEMS_PER_CATEGORY, days)[
        :, :, OWN_PER_CATEGORY:, :
    ].mean(axis=2)
    cross = np.repeat(rivals**CROSS_PRICE_ELASTICITY, ITEMS_PER_CATEGORY, axis=1)
    effect: FloatArray = ratio**-OWN_PRICE_ELASTICITY * cross
    return effect
