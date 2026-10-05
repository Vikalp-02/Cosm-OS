"""Every threshold and assumption the engine works from, in one place."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Prior:
    """What an elasticity is believed to be before the data has its say."""

    mean: float
    sd: float


@dataclass(frozen=True, slots=True)
class EngineConfig:
    # A driver's normal level is its median over this many preceding days. A
    # median holds steady through an incident shorter than half the window.
    reference_days: int = 28
    min_reference_days: int = 14

    # The demand model is refitted on a schedule, each time on history only,
    # so nothing is ever explained by a model that has already seen it.
    min_training_days: int = 28
    refit_every_days: int = 7

    # Elasticities of demand. Sales data alone pins these down slowly, so the
    # fit starts from typical grocery values and moves as evidence accumulates.
    own_price: Prior = Prior(-1.2, 1.0)
    rival_price: Prior = Prior(0.5, 0.5)
    visibility: Prior = Prior(0.5, 0.5)

    # A leak is a run of days on which one driver costs at least `day_loss` of
    # expected sales, averaging `run_loss` over the run. Broad series cover a
    # category across a platform. Narrow ones cover a category in one city or a
    # single product; they are noisier and need a larger loss to count.
    min_run_days: int = 3
    broad_day_loss: float = 0.02
    broad_run_loss: float = 0.04
    narrow_day_loss: float = 0.05
    narrow_run_loss: float = 0.10

    # A drop no driver explains needs to be both unlikely and sizeable.
    residual_day_z: float = 1.5
    residual_run_z: float = 5.0
    residual_run_drop: float = 0.08

    # Nothing costing less than this, in rupees, is worth raising.
    min_loss_gmv: float = 5_000.0
    # A driver is named the cause only if it accounts for this share of the loss.
    primary_share: float = 0.5

    # Evidence that separates one root cause from another.
    supply_stockless_share: float = 0.5
    supply_lookback_days: int = 7
    budget_cut_share: float = 0.2
    # Spend rarely lands exactly on a small budget, so "used up" allows some slack.
    budget_exhausted_utilisation: float = 0.75
