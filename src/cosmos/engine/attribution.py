"""How many units each driver cost each cell on each day.

For every cell and day the model gives two forecasts: one with each driver at
its normal level and one with the drivers as they were. The gap between them is
split across the drivers by Shapley value, which averages a driver's effect
over every order in which the drivers could have changed. That keeps the split
fair when several move at once and makes the parts add up to the whole.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from itertools import combinations
from math import factorial
from typing import Final

import numpy as np

from cosmos.engine._arrays import BoolArray, FloatArray, trailing_median
from cosmos.engine.config import EngineConfig
from cosmos.engine.model import MIN_AVAILABILITY, DemandModel, ad_factor, fit_demand
from cosmos.engine.panel import Panel

DRIVERS: Final = ("availability", "own_price", "rival_price", "visibility", "ads")
AVAILABILITY, OWN_PRICE, RIVAL_PRICE, VISIBILITY, ADS = range(len(DRIVERS))
# Drivers whose effect rests on an estimated elasticity, in the model's order.
_ESTIMATED: Final = (OWN_PRICE, RIVAL_PRICE, VISIBILITY)


@dataclass(frozen=True, slots=True)
class Impact:
    """Indexed [C, D], or [K, C, D] with one slice per driver in `DRIVERS`."""

    expected: FloatArray  # units with every driver at its normal level
    modelled: FloatArray  # units the model expects with the drivers as they were
    loss: FloatArray  # [K, C, D] units lost to each driver; negative is a gain
    # [K, C, D] how far `loss` moves if the driver's elasticity is one standard
    # error higher. Signed, so that summing it over cells gives the error of a
    # total. Zero for drivers that have no estimated elasticity.
    loss_sd: FloatArray
    normal: FloatArray  # [K, C, D] each driver's normal level
    actual: FloatArray  # [K, C, D] each driver's level on the day
    dispersion: FloatArray  # [D]
    valid: BoolArray  # False where there was too little history to say anything
    models: tuple[DemandModel, ...]


def compute_impact(panel: Panel, config: EngineConfig) -> Impact:
    n_cells, n_days = panel.n_cells, panel.n_days
    shape = (len(DRIVERS), n_cells, n_days)
    expected = np.full((n_cells, n_days), np.nan)
    modelled = np.full((n_cells, n_days), np.nan)
    loss = np.full(shape, np.nan)
    loss_sd = np.zeros(shape)
    normal = np.full(shape, np.nan)
    actual = np.full(shape, np.nan)
    dispersion = np.full(n_days, np.nan)

    def reference(values: FloatArray) -> FloatArray:
        return trailing_median(values, config.reference_days, config.min_reference_days)

    actual[AVAILABILITY] = np.maximum(panel.availability, MIN_AVAILABILITY)
    actual[OWN_PRICE] = panel.price_to_mrp
    actual[RIVAL_PRICE] = panel.rival_price_to_mrp
    actual[VISIBILITY] = panel.reciprocal_rank
    for driver in (AVAILABILITY, OWN_PRICE, RIVAL_PRICE, VISIBILITY):
        normal[driver] = reference(actual[driver])

    models: list[DemandModel] = []
    for first in range(config.min_training_days, n_days, config.refit_every_days):
        model = fit_demand(panel, first, config)
        models.append(model)
        days = slice(first, min(first + config.refit_every_days, n_days))

        # Ad delivery is measured against this model's idea of usual clicks, so
        # its normal level is worked out afresh for each model.
        delivery = model.delivery(panel)
        actual[ADS] = delivery[panel.group]
        normal[ADS] = reference(delivery)[panel.group]

        normal_factors = _factors(panel, model, normal)
        observed = np.isfinite(actual)
        # A driver with no reading on the day is taken to be at its normal level.
        actual_factors = np.where(observed, _factors(panel, model, actual), normal_factors)
        normal_factors = normal_factors * _skew(panel, normal_factors, actual_factors, first)
        actual_factors = np.where(observed, actual_factors, normal_factors)[:, :, days]
        normal_factors = normal_factors[:, :, days]

        base = np.exp(model.level[:, None] + model.weekday[panel.weekday[days]][None, :])
        expected[:, days] = base * normal_factors.prod(axis=0)
        loss[:, :, days] = base * shapley_losses(normal_factors, actual_factors)
        modelled[:, days] = expected[:, days] - loss[:, :, days].sum(axis=0)
        dispersion[days] = model.dispersion

        # A factor is level ** elasticity, so its sensitivity to the elasticity
        # is the factor times the log of how far the level moved.
        with np.errstate(divide="ignore", invalid="ignore"):
            moved = np.log(actual[:, :, days] / normal[:, :, days])
        for driver, sd in zip(_ESTIMATED, model.elasticity_sd, strict=True):
            change = actual_factors[driver] / normal_factors[driver] * np.nan_to_num(moved[driver])
            loss_sd[driver][:, days] = -expected[:, days] * change * sd

    return Impact(
        expected=expected,
        modelled=modelled,
        loss=loss,
        loss_sd=loss_sd,
        normal=normal,
        actual=actual,
        dispersion=dispersion,
        valid=np.isfinite(expected) & np.isfinite(modelled),
        models=tuple(models),
    )


def _factors(panel: Panel, model: DemandModel, levels: FloatArray) -> FloatArray:
    """Each driver's sales multiplier, [K, C, D], at the given driver levels."""
    return np.stack(
        [
            levels[AVAILABILITY],
            levels[OWN_PRICE] ** model.own_price,
            levels[RIVAL_PRICE] ** model.rival_price,
            levels[VISIBILITY] ** model.visibility,
            ad_factor(model.ad_share[panel.group, None], levels[ADS]),
        ]
    )


def _skew(
    panel: Panel, normal_factors: FloatArray, actual_factors: FloatArray, trained_days: int
) -> FloatArray:
    """Correction, [K, C, 1], that makes the normal level an unbiased baseline.

    A median is used as the normal level because an incident cannot drag it,
    but a driver that dips more than it peaks, as availability does, sits below
    its median on an average day. Left alone that would read as a small
    permanent loss. The correction is the driver's average standing against its
    median over the training history. It is taken as the median across the
    products in a city, so that an incident confined to a few of them leaves
    it untouched.
    """
    with np.errstate(divide="ignore", invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # cells with no history yet
        standing = np.nanmean(
            (actual_factors / normal_factors)[:, :, :trained_days], axis=2
        )  # [K, C]
        skew = np.ones_like(standing)
        for place in range(int(panel.place.max()) + 1):
            here = panel.place == place
            skew[:, here] = np.nanmedian(standing[:, here], axis=1, keepdims=True)
    return np.where(np.isfinite(skew), skew, 1.0)[:, :, None]


def shapley_losses(normal: FloatArray, actual: FloatArray) -> FloatArray:
    """Split `prod(normal) - prod(actual)` across the factors along the first axis."""
    count = normal.shape[0]
    losses = np.zeros_like(normal)
    for factor in range(count):
        others = [other for other in range(count) if other != factor]
        change = normal[factor] - actual[factor]
        for size in range(count):
            weight = factorial(size) * factorial(count - size - 1) / factorial(count)
            for moved in combinations(others, size):
                rest = np.ones_like(change)
                for other in others:
                    rest = rest * (actual[other] if other in moved else normal[other])
                losses[factor] += weight * rest * change
    return losses
