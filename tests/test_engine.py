from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from cosmos.engine import EngineConfig, Finding, RootCause, investigate
from cosmos.engine.__main__ import FINDINGS_FILE, main
from cosmos.engine._arrays import runs, trailing_median
from cosmos.engine.attribution import shapley_losses
from cosmos.engine.model import fit_poisson
from cosmos.evals import Planted, evaluate, score
from cosmos.generator import GeneratorConfig, build_dataset, write_dataset
from cosmos.warehouse import WAREHOUSE_FILE, build

NAN = float("nan")


def test_trailing_median_uses_only_earlier_entries() -> None:
    values = np.array([1.0, 2.0, 3.0, 100.0, 5.0])
    median = trailing_median(values, window=3, min_valid=2)
    assert np.isnan(median[:2]).all()  # fewer than two earlier entries
    assert median[2:].tolist() == [1.5, 2.0, 3.0]


def test_trailing_median_skips_missing_entries() -> None:
    values = np.array([[4.0, NAN, 6.0, 0.0], [NAN, NAN, NAN, 0.0]])
    median = trailing_median(values, window=3, min_valid=2)
    assert median[0, 3] == 5.0
    assert np.isnan(median[1, 3])


def test_runs_finds_each_unbroken_stretch() -> None:
    flags = np.array([True, True, False, True, False, False, True, True, True])
    assert runs(flags) == [(0, 1), (3, 3), (6, 8)]
    assert runs(np.zeros(4, dtype=np.bool_)) == []


def test_shapley_losses_add_up_to_the_whole_change() -> None:
    rng = np.random.default_rng(0)
    normal, actual = rng.uniform(0.5, 1.5, (2, 5, 40))
    losses = shapley_losses(normal, actual)
    assert np.allclose(losses.sum(axis=0), normal.prod(axis=0) - actual.prod(axis=0))


def test_shapley_gives_a_lone_mover_the_whole_change() -> None:
    normal = np.array([[1.0], [2.0], [0.5]])
    actual = normal.copy()
    actual[1] = 1.0
    assert shapley_losses(normal, actual)[:, 0].tolist() == [0.0, 0.5, 0.0]


def test_shapley_splits_two_equal_movers_evenly() -> None:
    normal = np.ones((2, 1))
    losses = shapley_losses(normal, normal * 0.5)
    assert losses[:, 0].tolist() == [0.375, 0.375]


def _simulated(
    rng: np.random.Generator,
    *,
    shock_sd: float = 0.0,
    first_varies_by_day_only: bool = False,
    second_is_constant: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Counts from 40 groups over 200 days with coefficients (0.8, -1.2) and an offset."""
    groups, days = 40, 200
    group = np.repeat(np.arange(groups), days)
    day = np.tile(np.arange(days), groups)
    features = rng.normal(0.0, 0.3, (groups * days, 2))
    if first_varies_by_day_only:
        features[:, 0] = rng.normal(0.0, 0.3, days)[day]
    if second_is_constant:
        features[:, 1] = 0.0
    offset = rng.normal(0.0, 0.2, groups * days)
    level = rng.normal(2.0, 0.5, groups)[group]
    shock = rng.normal(0.0, shock_sd, days)[day]
    mean = np.exp(level + offset + features @ np.array([0.8, -1.2]) + shock)
    return rng.poisson(mean).astype(np.float64), features, offset, group, day


def test_fit_recovers_known_coefficients() -> None:
    outcome, features, offset, group, _ = _simulated(np.random.default_rng(1))
    fit = fit_poisson(outcome, features, offset, group, 40, np.zeros(2), np.zeros(2))
    assert fit.coefficients == pytest.approx([0.8, -1.2], abs=0.03)
    assert np.all(np.abs(fit.coefficients - [0.8, -1.2]) < 3 * fit.coefficient_sd)
    assert fit.dispersion == pytest.approx(1.0, abs=0.05)


def test_fit_keeps_the_prior_where_the_data_says_nothing() -> None:
    # The second feature never varies, so the data cannot speak to its coefficient.
    outcome, features, offset, group, _ = _simulated(
        np.random.default_rng(2), second_is_constant=True
    )
    fit = fit_poisson(
        outcome, features, offset, group, 40, np.array([0.0, 0.5]), np.array([0.0, 4.0])
    )
    assert fit.coefficients[1] == pytest.approx(0.5)
    assert fit.coefficient_sd[1] == pytest.approx(0.5, rel=0.1)


def test_fit_ignores_groups_that_never_sold() -> None:
    outcome, features, offset, group, _ = _simulated(np.random.default_rng(3))
    outcome[group == 0] = 0.0
    fit = fit_poisson(outcome, features, offset, group, 40, np.zeros(2), np.zeros(2))
    assert np.isnan(fit.level[0])
    assert np.isfinite(fit.level[1:]).all()
    assert fit.coefficients == pytest.approx([0.8, -1.2], abs=0.03)


def test_shared_shocks_widen_the_standard_error() -> None:
    # A feature that only varies by day is the kind a shared daily shock confounds.
    outcome, features, offset, group, day = _simulated(
        np.random.default_rng(4), shock_sd=0.2, first_varies_by_day_only=True
    )
    plain = fit_poisson(outcome, features, offset, group, 40, np.zeros(2), np.zeros(2))
    robust = fit_poisson(
        outcome, features, offset, group, 40, np.zeros(2), np.zeros(2), cluster=day
    )
    assert np.allclose(robust.coefficients, plain.coefficients)
    assert robust.coefficient_sd[0] > 1.5 * plain.coefficient_sd[0]
    assert abs(robust.coefficients[0] - 0.8) < 3 * robust.coefficient_sd[0]


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A full-size dataset. The small one has so few stores per city that an
    ordinary store outage is itself a material loss, which no answer key lists."""
    directory = tmp_path_factory.mktemp("engine")
    write_dataset(build_dataset(GeneratorConfig()), directory)
    build(directory, quiet=True)
    return directory


@pytest.fixture(scope="module")
def findings(data_dir: Path) -> list[Finding]:
    return investigate(data_dir / WAREHOUSE_FILE, GeneratorConfig().tenant_id)


def test_every_planted_incident_is_found_and_diagnosed(data_dir: Path) -> None:
    card = evaluate(data_dir)
    assert card.planted == 12
    assert card.found == card.planted
    assert card.diagnosed == card.planted
    assert card.false_alarms == ()


def test_mechanical_estimates_are_close_and_estimated_ones_carry_their_doubt(
    data_dir: Path,
) -> None:
    for match in evaluate(data_dir).matches:
        assert match.error is not None and match.estimate is not None
        if match.estimate_sd is None:
            # Availability, ads and data gaps involve no estimated elasticity.
            assert abs(match.error) < 0.15, match.planted.incident_id
        else:
            assert abs(match.estimate - match.truth) < 3 * match.estimate_sd


def test_findings_reconcile(findings: list[Finding]) -> None:
    explained = [finding for finding in findings if finding.actual_units is not None]
    assert explained
    for finding in explained:
        assert finding.unexplained_units is not None
        accounted = (
            finding.expected_units
            - sum(finding.loss_units.values())
            - finding.unexplained_units
            - finding.unreported_units
        )
        assert accounted == pytest.approx(finding.actual_units, abs=1e-6)


def test_leak_is_scoped_to_where_the_cause_bit(findings: list[Finding]) -> None:
    by_cause = {finding.cause: finding for finding in findings}
    stockout = by_cause[RootCause.STOCKOUT]
    assert stockout.cities is not None and len(stockout.cities) == 1
    assert stockout.evidence["availability_during"] < 0.5 < stockout.evidence["availability_before"]
    assert stockout.evidence["warehouse_stockless_share"] == 0.0

    shortfall = by_cause[RootCause.SUPPLY_SHORTFALL]
    assert shortfall.evidence["warehouse_stockless_share"] == 1.0
    assert shortfall.evidence["po_units_lapsed"] > 0

    price_cut = by_cause[RootCause.COMPETITOR_PRICE_CUT]
    assert price_cut.cities is None
    assert price_cut.cause_loss_gmv_sd > 0

    gap = by_cause[RootCause.DATA_GAP]
    assert gap.actual_units is None and gap.loss_gmv == {}
    assert gap.unreported_units > 0


def test_engine_is_deterministic(data_dir: Path, findings: list[Finding]) -> None:
    assert investigate(data_dir / WAREHOUSE_FILE, GeneratorConfig().tenant_id) == findings


def test_a_higher_floor_reports_fewer_leaks(data_dir: Path, findings: list[Finding]) -> None:
    strict = investigate(
        data_dir / WAREHOUSE_FILE, GeneratorConfig().tenant_id, EngineConfig(min_loss_gmv=40_000)
    )
    losses = [f for f in strict if f.cause is not RootCause.DATA_GAP]
    assert 0 < len(losses) < len([f for f in findings if f.cause is not RootCause.DATA_GAP])
    assert all(finding.cause_loss_gmv >= 40_000 for finding in losses)


def test_unknown_tenant_is_reported_clearly(data_dir: Path) -> None:
    with pytest.raises(LookupError, match="no sales history"):
        investigate(data_dir / WAREHOUSE_FILE, "nobody")


def test_cli_writes_findings(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--data", str(data_dir)]) == 0
    assert "12 findings" in capsys.readouterr().out
    written = json.loads((data_dir / FINDINGS_FILE).read_text(encoding="utf-8"))
    assert len(written) == 12
    assert {entry["cause"] for entry in written} >= {"stockout", "data_gap"}


def _planted(**changes: object) -> Planted:
    base = Planted(
        incident_id="INC-001",
        tenant_id="acme",
        cause="stockout",
        platform="blinkit",
        city="Pune",
        sku_ids=frozenset({"SKU-1", "SKU-2"}),
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 6),
        expected_gmv_lost=10_000.0,
        reported_units_dropped=0,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _finding(**changes: object) -> Finding:
    base = Finding(
        id="LEAK-001",
        tenant_id="acme",
        cause=RootCause.STOCKOUT,
        platform="blinkit",
        category="Juices",
        cities=("Pune",),
        sku_ids=("SKU-2", "SKU-3"),
        start_date=date(2026, 7, 2),
        end_date=date(2026, 7, 6),
        expected_units=100.0,
        expected_gmv=20_000.0,
        actual_units=40.0,
        actual_gmv=8_000.0,
        loss_units={"availability": 55.0},
        loss_gmv={"availability": 11_000.0},
        loss_gmv_sd={"availability": 0.0},
        unexplained_units=5.0,
        unexplained_gmv=1_000.0,
        noise_units=6.0,
        unreported_units=0.0,
        unreported_gmv=0.0,
        evidence={},
        daily=(),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_score_pairs_an_incident_with_the_finding_that_covers_it() -> None:
    card = score([_planted()], [_finding()])
    (match,) = card.matches
    assert match.found and match.diagnosed
    assert match.error == pytest.approx(0.10)
    assert card.false_alarms == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"platform": "zepto"},
        {"cities": ("Mumbai",)},
        {"sku_ids": ("SKU-9",)},
        {"start_date": date(2026, 7, 5)},  # covers two of six days
        {"cause": RootCause.DATA_GAP},
    ],
)
def test_score_rejects_a_finding_about_something_else(changes: dict[str, object]) -> None:
    stray = _finding(**changes)
    card = score([_planted()], [stray])
    assert not card.matches[0].found
    assert card.false_alarms == (stray,)


def test_score_counts_a_wrong_diagnosis_as_found_but_not_diagnosed() -> None:
    card = score([_planted()], [_finding(cause=RootCause.RANK_LOSS)])
    (match,) = card.matches
    assert match.found and not match.diagnosed
    assert match.error is None


def test_score_uses_each_finding_once() -> None:
    card = score([_planted(), _planted(incident_id="INC-002")], [_finding()])
    assert [match.found for match in card.matches] == [True, False]


def test_score_knows_which_misses_were_below_the_floor() -> None:
    card = score([_planted(expected_gmv_lost=3_000.0), _planted(incident_id="INC-002")], [])
    assert card.missed_below(5_000.0) == 1
