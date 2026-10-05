"""Find revenue leaks and say what caused each one.

Three kinds of signal are looked for:

- a run of days on which one driver costs a material share of expected sales;
- a run of days on which sales fall well short of what the drivers predict;
- cities whose sales stopped arriving, which is a data problem and not a loss.

Signals are raised per category, across a platform and within each city, then
merged so that one event is reported once, with the cells it really touched.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, replace
from typing import Final

import numpy as np

from cosmos.engine._arrays import FloatArray, IntArray, runs, trailing_median
from cosmos.engine.attribution import ADS, AVAILABILITY, DRIVERS, VISIBILITY, Impact
from cosmos.engine.config import EngineConfig
from cosmos.engine.findings import DailyPoint, Finding, RootCause
from cosmos.engine.panel import Panel

# A cell belongs to a leak if the cause cost it at least this share of its
# expected sales, and at least this fraction of what the worst-hit cells lost.
# The second test keeps ordinary day-to-day wobble out of a sharp local event.
_MIN_CELL_LOSS: Final = 0.03
_SHARE_OF_PEAK_LOSS: Final = 0.5
# Days at either end of a run that lose less than this fraction of the run's
# average are dropped: a quiet day beside an incident is not part of it.
_EDGE_SHARE_OF_RUN: Final = 0.5
# A shortfall in sales smaller than this many standard deviations of ordinary
# variation is treated as chance.
_NOISE_BAND: Final = 2.0
# How much of the run-up and the recovery a leak's daily series shows.
_DAYS_BEFORE: Final = 14
_DAYS_AFTER: Final = 7

_CAUSE_OF_DRIVER: Final = {
    "own_price": RootCause.OWN_PRICE_INCREASE,
    "rival_price": RootCause.COMPETITOR_PRICE_CUT,
    "visibility": RootCause.RANK_LOSS,
}
_READING: Final = {
    "availability": "availability",
    "own_price": "price_to_mrp",
    "rival_price": "rival_price_to_mrp",
    "ads": "ad_delivery",
}


@dataclass(frozen=True, slots=True)
class _Signal:
    group: int
    start: int
    end: int
    cells: IntArray  # cells of the series that raised it
    loss_gmv: float
    # The driver whose loss raised it, or None if it was raised by a shortfall
    # in sales that the drivers do not predict.
    driver: int | None


@dataclass(frozen=True, slots=True)
class _Frame:
    """The impact arrays with unknowns zeroed, ready to be summed over any set of cells."""

    expected: FloatArray  # [C, D]
    modelled: FloatArray  # [C, D]
    loss: FloatArray  # [K, C, D]
    price: FloatArray  # [C, D]
    # Where sales are known: modelled minus actual, and the modelled sales themselves.
    shortfall: FloatArray  # [C, D]
    modelled_known: FloatArray  # [C, D]


def detect(panel: Panel, impact: Impact, config: EngineConfig) -> list[Finding]:
    frame = _frame(panel, impact)
    signals = _driver_signals(panel, frame, config)
    signals += _residual_signals(panel, impact, frame, config)

    # One event raises several signals: across the platform, in each city, for
    # each product. The costliest is described first, and anything overlapping
    # an accepted finding in the same category is the same event seen again.
    accepted: list[tuple[_Signal, Finding]] = []
    for signal in sorted(signals, key=lambda s: (-s.loss_gmv, s.group, s.start)):
        if any(
            other.group == signal.group and other.start <= signal.end and signal.start <= other.end
            for other, _ in accepted
        ):
            continue
        finding = _describe(panel, impact, frame, config, signal)
        if finding is not None:
            accepted.append((signal, finding))

    findings = [finding for _, finding in accepted] + _data_gaps(panel, impact, frame)
    findings.sort(key=lambda f: (f.start_date, f.platform, f.category or ""))
    return [replace(finding, id=f"LEAK-{number:03d}") for number, finding in enumerate(findings, 1)]


def _frame(panel: Panel, impact: Impact) -> _Frame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a cell that never showed a price
        usual_price = np.nanmedian(panel.price, axis=1, keepdims=True)
    price = np.where(np.isfinite(panel.price), panel.price, np.nan_to_num(usual_price))
    known = impact.valid & panel.reported & np.isfinite(panel.units)
    return _Frame(
        expected=np.where(impact.valid, impact.expected, 0.0),
        modelled=np.where(impact.valid, impact.modelled, 0.0),
        loss=np.where(impact.valid, impact.loss, 0.0),
        price=price,
        shortfall=np.where(known, impact.modelled - panel.units, 0.0),
        modelled_known=np.where(known, impact.modelled, 0.0),
    )


def _series(panel: Panel) -> list[tuple[IntArray, FloatArray, bool]]:
    """Each level signals are raised at: per-series group, [series, C] membership, is-narrow.

    A category across a platform is the broad level. A category within one city
    and a single product across cities are the narrow ones: they catch an event
    too local to move the category as a whole, and are noisier for it.
    """
    products = sorted(set(zip(panel.platform, panel.sku_id, strict=True)))
    product_of = {key: index for index, key in enumerate(products)}
    product = np.array(
        [product_of[key] for key in zip(panel.platform, panel.sku_id, strict=True)],
        dtype=np.int64,
    )
    levels = []
    for index, count, narrow in (
        (panel.group, len(panel.groups), False),
        (panel.area, len(panel.areas), True),
        (product, len(products), True),
    ):
        member = (index[None, :] == np.arange(count)[:, None]).astype(np.float64)
        group = np.zeros(count, dtype=np.int64)
        group[index] = panel.group
        levels.append((group, member, narrow))
    return levels


def _driver_signals(panel: Panel, frame: _Frame, config: EngineConfig) -> list[_Signal]:
    signals: list[_Signal] = []
    for group, member, narrow in _series(panel):
        day_loss = config.narrow_day_loss if narrow else config.broad_day_loss
        run_loss = config.narrow_run_loss if narrow else config.broad_run_loss
        expected = member @ frame.expected
        loss = np.einsum("sc,kcd->ksd", member, frame.loss)
        loss_gmv = np.einsum("sc,kcd->ksd", member, frame.loss * frame.price)
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.nan_to_num(loss / expected)
        costly = share >= day_loss
        for driver, series in np.ndindex(costly.shape[:2]):
            for first, last in runs(costly[driver, series]):
                start, end = _trim(share[driver, series], first, last)
                days = slice(start, end + 1)
                if (
                    end - start + 1 >= config.min_run_days
                    and loss[driver, series, days].sum() >= run_loss * expected[series, days].sum()
                    and loss_gmv[driver, series, days].sum() >= config.min_loss_gmv
                ):
                    signals.append(
                        _Signal(
                            group=int(group[series]),
                            start=start,
                            end=end,
                            cells=np.flatnonzero(member[series] > 0),
                            loss_gmv=float(loss_gmv[driver, series, days].sum()),
                            driver=driver,
                        )
                    )
    return signals


def _residual_signals(
    panel: Panel, impact: Impact, frame: _Frame, config: EngineConfig
) -> list[_Signal]:
    signals: list[_Signal] = []
    dispersion = np.nan_to_num(impact.dispersion, nan=1.0)
    for group, member, _ in _series(panel):
        shortfall = member @ frame.shortfall
        shortfall_gmv = member @ (frame.shortfall * frame.price)
        modelled = member @ frame.modelled_known
        variance = modelled * dispersion[None, :]
        with np.errstate(divide="ignore", invalid="ignore"):
            unlikely = shortfall / np.sqrt(variance) >= config.residual_day_z
        for series in range(member.shape[0]):
            for start, end in runs(unlikely[series]):
                days = slice(start, end + 1)
                short = shortfall[series, days].sum()
                if (
                    end - start + 1 >= config.min_run_days
                    and short >= config.residual_run_z * np.sqrt(variance[series, days].sum())
                    and short >= config.residual_run_drop * modelled[series, days].sum()
                    and shortfall_gmv[series, days].sum() >= config.min_loss_gmv
                ):
                    signals.append(
                        _Signal(
                            group=int(group[series]),
                            start=start,
                            end=end,
                            cells=np.flatnonzero(member[series] > 0),
                            loss_gmv=float(shortfall_gmv[series, days].sum()),
                            driver=None,
                        )
                    )
    return signals


def _trim(share: FloatArray, start: int, end: int) -> tuple[int, int]:
    """Drop the weak days at either end of a run."""
    floor = _EDGE_SHARE_OF_RUN * float(share[start : end + 1].mean())
    while start < end and share[start] < floor:
        start += 1
    while end > start and share[end] < floor:
        end -= 1
    return start, end


def _describe(
    panel: Panel, impact: Impact, frame: _Frame, config: EngineConfig, signal: _Signal
) -> Finding | None:
    days = slice(signal.start, signal.end + 1)
    dispersion = np.nan_to_num(impact.dispersion[days], nan=1.0)[None, :]

    # Judged over the series that raised the signal. Over the whole category a
    # sharp local loss would be lost among the ordinary noise of everything else.
    losses = frame.loss[:, signal.cells, days].sum(axis=(1, 2))
    shortfall = float(frame.shortfall[signal.cells, days].sum())
    noise = float(np.sqrt((frame.modelled_known[signal.cells, days] * dispersion).sum()))
    # A shortfall within the noise is not something that needs explaining.
    unexplained = shortfall if shortfall > _NOISE_BAND * noise else 0.0
    primary = int(np.argmax(losses))
    explained = bool(
        losses[primary] > 0
        and losses[primary] >= config.primary_share * (np.maximum(losses, 0.0).sum() + unexplained)
    )
    if signal.driver is not None and not (explained and primary == signal.driver):
        # Raised by a driver that turns out not to be the main story here. If
        # another driver is, it will have raised a signal of its own.
        return None

    cause, evidence = RootCause.UNEXPLAINED_DROP, dict[str, float]()
    cells = signal.cells
    in_group = np.flatnonzero(panel.group == signal.group)
    if explained:
        # The leak reaches wherever in the category the cause bit, which may be
        # more or less than the series that happened to raise the signal.
        cell_loss = frame.loss[primary][in_group, days].sum(axis=1)
        cell_expected = frame.expected[in_group, days].sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.nan_to_num(cell_loss / cell_expected)
        hit = cell_loss > 0
        peak = float((cell_loss[hit] * share[hit]).sum() / cell_loss[hit].sum())
        cells = in_group[share >= max(_MIN_CELL_LOSS, _SHARE_OF_PEAK_LOSS * peak)]
        if cells.size == 0:
            return None
        cause, evidence = _diagnose(panel, impact, frame, config, primary, cells, signal)

    platform, category = panel.groups[signal.group]
    finding = _finding(panel, impact, frame, cells, signal, cause, platform, category, evidence)
    material = finding.cause_loss_gmv if explained else finding.unexplained_gmv
    if material is None or material < config.min_loss_gmv:
        return None
    return replace(finding, cities=_cities(panel, cells, in_group))


def _finding(
    panel: Panel,
    impact: Impact,
    frame: _Frame,
    cells: IntArray,
    signal: _Signal,
    cause: RootCause,
    platform: str,
    category: str,
    evidence: dict[str, float],
) -> Finding:
    """Sum the frame over a leak's cells and days. The figures reconcile:
    expected - losses - unexplained - unreported = actual."""
    start, end = signal.start, signal.end
    block = np.ix_(cells, np.arange(start, end + 1))
    price = frame.price[block]
    known = (impact.valid & panel.reported & np.isfinite(panel.units))[block]
    units = np.where(known, panel.units[block], 0.0)
    unreported = np.where(known, 0.0, frame.modelled[block])
    loss = frame.loss[:, cells, start : end + 1]
    loss_sd = impact.loss_sd[:, cells, start : end + 1]
    some_known = bool(known.any())
    dispersion = np.nan_to_num(impact.dispersion[start : end + 1], nan=1.0)[None, :]
    return Finding(
        id="",
        tenant_id=panel.tenant_id,
        cause=cause,
        platform=platform,
        category=category,
        cities=tuple(sorted({panel.city[cell] for cell in cells})),
        sku_ids=tuple(sorted({panel.sku_id[cell] for cell in cells})),
        start_date=panel.day(start),
        end_date=panel.day(end),
        expected_units=float(frame.expected[block].sum()),
        expected_gmv=float((frame.expected[block] * price).sum()),
        actual_units=float(units.sum()) if some_known else None,
        actual_gmv=float((units * price).sum()) if some_known else None,
        loss_units={name: float(loss[k].sum()) for k, name in enumerate(DRIVERS)},
        loss_gmv={name: float((loss[k] * price).sum()) for k, name in enumerate(DRIVERS)},
        loss_gmv_sd={
            name: abs(float(np.nansum(loss_sd[k] * price))) for k, name in enumerate(DRIVERS)
        },
        unexplained_units=float(frame.shortfall[block].sum()) if some_known else None,
        unexplained_gmv=float((frame.shortfall[block] * price).sum()) if some_known else None,
        noise_units=float(np.sqrt((frame.modelled_known[block] * dispersion).sum())),
        unreported_units=float(unreported.sum()),
        unreported_gmv=float((unreported * price).sum()),
        evidence=evidence,
        daily=_daily(panel, impact, frame, cells, start, end),
    )


def _daily(
    panel: Panel, impact: Impact, frame: _Frame, cells: IntArray, start: int, end: int
) -> tuple[DailyPoint, ...]:
    """Expected and actual revenue for a leak's cells, from before it began to after it ended."""
    first = max(0, start - _DAYS_BEFORE)
    last = min(panel.n_days - 1, end + _DAYS_AFTER)
    valid = impact.valid[cells]
    known = valid & panel.reported[cells] & np.isfinite(panel.units[cells])
    expected = (frame.expected * frame.price)[cells]
    actual = np.where(known, panel.units[cells] * frame.price[cells], 0.0)

    points: list[DailyPoint] = []
    for day in range(first, last + 1):
        if not valid[:, day].any():
            continue  # too early in the history for the model to say anything
        some_known = bool(known[:, day].any())
        points.append(
            DailyPoint(
                day=panel.day(day),
                expected_gmv=float(expected[valid[:, day], day].sum()),
                actual_gmv=float(actual[:, day].sum()) if some_known else None,
                complete=bool((known[:, day] == valid[:, day]).all()),
                in_window=start <= day <= end,
            )
        )
    return tuple(points)


def _cities(panel: Panel, cells: IntArray, among: IntArray) -> tuple[str, ...] | None:
    """The cities touched, or None when that is every city the cells could have been in."""
    touched = {panel.city[cell] for cell in cells}
    return None if touched == {panel.city[cell] for cell in among} else tuple(sorted(touched))


def _diagnose(
    panel: Panel,
    impact: Impact,
    frame: _Frame,
    config: EngineConfig,
    driver: int,
    cells: IntArray,
    signal: _Signal,
) -> tuple[RootCause, dict[str, float]]:
    """Name the root cause behind a driver, with the readings that support it."""
    days = slice(signal.start, signal.end + 1)
    weight = frame.expected[cells, days]

    def typical(values: FloatArray) -> float:
        seen = np.isfinite(values) & (weight > 0)
        if not seen.any():
            return float("nan")
        return float((values[seen] * weight[seen]).sum() / weight[seen].sum())

    name = DRIVERS[driver]
    evidence: dict[str, float] = {}
    if driver == VISIBILITY:
        usual_rank = trailing_median(
            panel.organic_rank[cells], config.reference_days, config.min_reference_days
        )
        evidence["organic_rank_before"] = typical(usual_rank[:, days])
        evidence["organic_rank_during"] = typical(panel.organic_rank[cells, days])
    else:
        evidence[f"{_READING[name]}_before"] = typical(impact.normal[driver][cells, days])
        evidence[f"{_READING[name]}_during"] = typical(impact.actual[driver][cells, days])

    if driver == AVAILABILITY:
        stock = panel.stock[cells, days]
        seen = np.isfinite(stock)
        stockless = float(np.mean(stock[seen] == 0)) if seen.any() else 0.0
        lookback = slice(max(0, signal.start - config.supply_lookback_days), signal.end + 1)
        evidence["warehouse_stockless_share"] = stockless
        evidence["po_units_lapsed"] = float(np.nansum(panel.po_units_lapsed[cells, lookback]))
        # Empty shelves with a full warehouse is a store or listing problem;
        # empty shelves with an empty warehouse is a supply problem.
        if stockless >= config.supply_stockless_share:
            return RootCause.SUPPLY_SHORTFALL, evidence
        return RootCause.STOCKOUT, evidence

    if driver == ADS:
        group = int(panel.group[cells[0]])
        before = slice(max(0, signal.start - config.reference_days), signal.start)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # a group with no campaign
            budget_before = float(np.nanmedian(panel.budget[group, before]))
            budget_during = float(np.nanmean(panel.budget[group, days]))
        budget_total = float(np.nansum(panel.budget[group, days]))
        utilisation = float(np.nansum(panel.spend[group, days])) / max(budget_total, 1e-9)
        evidence["daily_budget_before"] = budget_before
        evidence["daily_budget_during"] = budget_during
        evidence["budget_utilisation_during"] = utilisation
        cut = budget_during <= (1.0 - config.budget_cut_share) * budget_before
        if cut and utilisation >= config.budget_exhausted_utilisation:
            return RootCause.AD_BUDGET_CUT, evidence
        return RootCause.AD_DELIVERY_DROP, evidence

    return _CAUSE_OF_DRIVER[name], evidence


def _data_gaps(panel: Panel, impact: Impact, frame: _Frame) -> list[Finding]:
    """Stretches where cities that normally report sent nothing, one finding per platform."""
    findings: list[Finding] = []
    for platform in sorted(set(panel.platform)):
        on_platform = np.flatnonzero(np.array(panel.platform) == platform)
        for start, end in runs(np.asarray(panel.gap[on_platform].any(axis=0))):
            days = slice(start, end + 1)
            cells = on_platform[panel.gap[on_platform, days].any(axis=1)]
            gap = panel.gap[cells, days]
            price = frame.price[cells, days]
            expected = np.where(gap, frame.expected[cells, days], 0.0)
            unreported = np.where(gap, frame.modelled[cells, days], 0.0)
            missing = {
                (panel.city[cells[row]], day) for row, day in zip(*np.nonzero(gap), strict=True)
            }
            findings.append(
                Finding(
                    id="",
                    tenant_id=panel.tenant_id,
                    cause=RootCause.DATA_GAP,
                    platform=platform,
                    category=None,
                    cities=_cities(panel, cells, on_platform),
                    sku_ids=tuple(sorted({panel.sku_id[cell] for cell in cells})),
                    start_date=panel.day(start),
                    end_date=panel.day(end),
                    expected_units=float(expected.sum()),
                    expected_gmv=float((expected * price).sum()),
                    actual_units=None,
                    actual_gmv=None,
                    loss_units={},
                    loss_gmv={},
                    loss_gmv_sd={},
                    unexplained_units=None,
                    unexplained_gmv=None,
                    noise_units=0.0,
                    unreported_units=float(unreported.sum()),
                    unreported_gmv=float((unreported * price).sum()),
                    evidence={
                        "cities_missing": float(len({city for city, _ in missing})),
                        "city_days_missing": float(len(missing)),
                    },
                    daily=_daily(panel, impact, frame, cells, start, end),
                )
            )
    return findings
