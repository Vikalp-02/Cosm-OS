"""The demand model: what a cell should sell given the state of its drivers.

    units = level x weekday x availability x ads
            x own_price ** a x rival_price ** b x visibility ** c

Availability and ads enter by their mechanics, with nothing to estimate: a
product missing from half the shelves can sell half as much, and if ads bring a
fifth of sales then losing all ad delivery loses a fifth. The three elasticities
and the weekday pattern are estimated by Poisson regression with one level per
cell.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Final

import numpy as np

from cosmos.engine._arrays import FloatArray, IntArray
from cosmos.engine.config import EngineConfig
from cosmos.engine.panel import Panel

# Availability below this is treated as this, so one empty day cannot zero a forecast.
MIN_AVAILABILITY: Final = 0.01
MAX_AD_SHARE: Final = 0.8
_WEEKDAY_TERMS: Final = 6  # Monday is the baseline
_MAX_ITERATIONS: Final = 100
_TOLERANCE: Final = 1e-9


@dataclass(frozen=True, slots=True)
class DemandModel:
    level: FloatArray  # [C] log units at reference conditions; NaN if the cell never sold
    weekday: FloatArray  # [7] log effect, zero on Monday
    own_price: float
    rival_price: float
    visibility: float
    # Standard errors of the three elasticities above, in that order.
    elasticity_sd: tuple[float, float, float]
    ad_share: FloatArray  # [G] share of a group's sales that come through ads
    usual_clicks: FloatArray  # [G, 7] typical clicks by weekday
    dispersion: float  # variance of sales relative to a Poisson with the same mean
    trained_days: int

    def delivery(self, panel: Panel) -> FloatArray:
        """Ad delivery per [G, D], as a multiple of what is usual for the weekday."""
        delivered: FloatArray = panel.clicks / self.usual_clicks[:, panel.weekday]
        return delivered


@dataclass(frozen=True, slots=True)
class PoissonFit:
    coefficients: FloatArray
    # Standard error of each coefficient, widened for overdispersion.
    coefficient_sd: FloatArray
    level: FloatArray  # [groups] log level; NaN where the group has no positive outcome
    dispersion: float


def fit_demand(panel: Panel, trained_days: int, config: EngineConfig) -> DemandModel:
    """Fit on the first `trained_days` days only."""
    history = slice(0, trained_days)
    weekday = panel.weekday[history]
    units = np.where(panel.reported[:, history], panel.units[:, history], np.nan)

    usual_clicks = np.full((len(panel.groups), 7), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a group with no campaign
        for name in range(7):
            on_day = panel.clicks[:, history][:, weekday == name]
            usual_clicks[:, name] = np.nanmedian(on_day, axis=1)
    usual_clicks[usual_clicks <= 0] = np.nan

    group_units = np.zeros(len(panel.groups))
    np.add.at(group_units, panel.group, np.nansum(units, axis=1))
    ad_orders = np.nansum(panel.ad_orders[:, history], axis=1)
    ad_share = np.clip(ad_orders / np.maximum(group_units, 1.0), 0.0, MAX_AD_SHARE)

    delivery = panel.clicks[:, history] / usual_clicks[:, weekday]
    ads = ad_factor(ad_share[panel.group, None], delivery[panel.group])
    drivers = (
        panel.price_to_mrp[:, history],
        panel.rival_price_to_mrp[:, history],
        panel.reciprocal_rank[:, history],
    )
    usable = np.isfinite(units) & np.isfinite(panel.availability[:, history]) & np.isfinite(ads)
    for driver in drivers:
        usable &= np.isfinite(driver) & (driver > 0)
    cell, day = np.nonzero(usable)

    features = np.zeros((cell.size, _WEEKDAY_TERMS + len(drivers)))
    on_weekday = weekday[day]
    features[on_weekday > 0, on_weekday[on_weekday > 0] - 1] = 1.0
    for column, driver in enumerate(drivers, start=_WEEKDAY_TERMS):
        features[:, column] = np.log(driver[cell, day])
    offset = np.log(np.maximum(panel.availability[:, history][cell, day], MIN_AVAILABILITY))
    offset += np.log(ads[cell, day])

    priors = (config.own_price, config.rival_price, config.visibility)
    prior_mean = np.array([0.0] * _WEEKDAY_TERMS + [prior.mean for prior in priors])
    prior_precision = np.array([0.0] * _WEEKDAY_TERMS + [prior.sd**-2 for prior in priors])
    # Everything a platform sells on one day shares that day's demand swings.
    platforms = {name: index for index, name in enumerate(sorted(set(panel.platform)))}
    platform = np.array([platforms[name] for name in panel.platform], dtype=np.int64)
    fit = fit_poisson(
        units[cell, day],
        features,
        offset,
        cell,
        panel.n_cells,
        prior_mean,
        prior_precision,
        cluster=platform[cell] * panel.n_days + day,
    )

    own_price, rival_price, visibility = (float(b) for b in fit.coefficients[_WEEKDAY_TERMS:])
    own_sd, rival_sd, visibility_sd = (float(sd) for sd in fit.coefficient_sd[_WEEKDAY_TERMS:])
    return DemandModel(
        level=fit.level,
        weekday=np.concatenate(([0.0], fit.coefficients[:_WEEKDAY_TERMS])),
        own_price=own_price,
        rival_price=rival_price,
        visibility=visibility,
        elasticity_sd=(own_sd, rival_sd, visibility_sd),
        ad_share=ad_share,
        usual_clicks=usual_clicks,
        dispersion=fit.dispersion,
        trained_days=trained_days,
    )


def ad_factor(ad_share: FloatArray, delivery: FloatArray) -> FloatArray:
    """Sales multiplier from ad delivery: the organic share is kept whatever happens to ads.

    A group that runs no ads has nothing to lose, so missing delivery counts as normal.
    """
    factor: FloatArray = 1.0 - ad_share + ad_share * np.where(np.isfinite(delivery), delivery, 1.0)
    return factor


def fit_poisson(
    outcome: FloatArray,
    features: FloatArray,
    offset: FloatArray,
    group: IntArray,
    n_groups: int,
    prior_mean: FloatArray,
    prior_precision: FloatArray,
    cluster: IntArray | None = None,
) -> PoissonFit:
    """Poisson regression with a free level per group and Gaussian priors on the coefficients.

    `cluster` labels rows whose errors move together; standard errors are then
    made robust to that.

    The levels have a closed form given the coefficients, so only the
    coefficients are searched for, by Newton's method on the profile likelihood.
    """
    group_outcome = np.bincount(group, weights=outcome, minlength=n_groups)
    # A group that never had a positive outcome fits a level of minus infinity
    # and tells the coefficients nothing.
    informative = group_outcome[group] > 0
    outcome, features, offset, group = (
        outcome[informative],
        features[informative],
        offset[informative],
        group[informative],
    )

    def evaluate(coefficients: FloatArray) -> tuple[float, FloatArray, FloatArray]:
        partial = np.exp(offset + features @ coefficients)
        group_partial = np.bincount(group, weights=partial, minlength=n_groups)
        with np.errstate(divide="ignore", invalid="ignore"):
            level = np.log(group_outcome / group_partial)
        mean = np.exp(level[group]) * partial
        gap = coefficients - prior_mean
        likelihood = float(
            np.sum(outcome * np.log(np.maximum(mean, 1e-300)) - mean)
            - 0.5 * np.sum(prior_precision * gap * gap)
        )
        return likelihood, mean, level

    def centred_on_groups(mean: FloatArray) -> FloatArray:
        """Features with each group's mean-weighted average taken out."""
        group_mean = np.maximum(np.bincount(group, weights=mean, minlength=n_groups), 1e-300)
        centre = (
            np.stack(
                [
                    np.bincount(group, weights=mean * features[:, column], minlength=n_groups)
                    for column in range(features.shape[1])
                ],
                axis=1,
            )
            / group_mean[:, None]
        )
        centred: FloatArray = features - centre[group]
        return centred

    def curvature_at(mean: FloatArray) -> FloatArray:
        centred = centred_on_groups(mean)
        curvature: FloatArray = (centred * mean[:, None]).T @ centred
        return curvature + np.diag(prior_precision + 1e-9)

    coefficients = prior_mean.astype(np.float64)
    likelihood, mean, level = evaluate(coefficients)
    for _ in range(_MAX_ITERATIONS):
        gradient = features.T @ (outcome - mean) - prior_precision * (coefficients - prior_mean)
        step = np.linalg.solve(curvature_at(mean), gradient)

        # Halve the step until it improves the fit; Newton can overshoot far from the optimum.
        scale = 1.0
        while True:
            candidate = evaluate(coefficients + scale * step)
            if candidate[0] >= likelihood - 1e-12 or scale < 1e-6:
                break
            scale /= 2.0
        coefficients = coefficients + scale * step
        improvement = candidate[0] - likelihood
        likelihood, mean, level = candidate
        if np.max(np.abs(scale * step)) < _TOLERANCE or improvement < _TOLERANCE:
            break

    freedom = outcome.size - features.shape[1] - int(np.sum(group_outcome > 0))
    pearson = float(np.sum((outcome - mean) ** 2 / np.maximum(mean, 1e-12)))
    dispersion = max(1.0, pearson / freedom) if freedom > 0 else 1.0
    bread = np.linalg.inv(curvature_at(mean))
    covariance = bread * dispersion
    if cluster is not None:
        # Rows in a cluster share shocks the model does not see, which leaves
        # fewer independent observations than rows. Summing the score within
        # each cluster before squaring it accounts for that.
        cluster = cluster[informative]
        scores = centred_on_groups(mean) * (outcome - mean)[:, None]
        _, membership = np.unique(cluster, return_inverse=True)
        n_clusters = int(membership.max()) + 1
        totals = np.zeros((n_clusters, features.shape[1]))
        np.add.at(totals, membership, scores)
        if n_clusters > 1:
            robust = bread @ (totals.T @ totals) @ bread * n_clusters / (n_clusters - 1)
            # Never report less uncertainty than the plain estimate does.
            covariance = np.where(
                np.eye(features.shape[1], dtype=np.bool_),
                np.maximum(covariance, robust),
                robust,
            )
    return PoissonFit(
        coefficients=coefficients,
        coefficient_sd=np.sqrt(np.diag(covariance)),
        level=np.where(group_outcome > 0, level, np.nan),
        dispersion=dispersion,
    )
