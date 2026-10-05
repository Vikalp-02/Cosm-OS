"""Command line entry point: `python -m cosmos.evals`."""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from cosmos.engine import EngineConfig
from cosmos.evals.run import evaluate, evaluate_seed
from cosmos.evals.score import Scorecard


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cosmos.evals",
        description="Score the engine against the incidents planted in synthetic data.",
    )
    parser.add_argument("--data", type=Path, help="score this dataset, already built")
    parser.add_argument(
        "--seeds", type=int, nargs="+", help="generate, build and score a dataset per seed"
    )
    parser.add_argument("--small", action="store_true", help="with --seeds, use small datasets")
    args = parser.parse_args(argv)

    cards: dict[str, Scorecard] = {}
    if args.seeds:
        with tempfile.TemporaryDirectory(prefix="cosmos-evals-") as work_dir:
            for seed in args.seeds:
                cards[f"seed {seed}"] = evaluate_seed(seed, Path(work_dir), small=args.small)
    else:
        data_dir = args.data or Path("data")
        cards[str(data_dir)] = evaluate(data_dir)

    for name, card in cards.items():
        print(report(name, card))
    if len(cards) > 1:
        print(summary(list(cards.values())))
    return 0


def report(name: str, card: Scorecard) -> str:
    lines = [
        name,
        f"  {'incident':<9} {'planted':<21} {'diagnosed as':<21} {'truth':>10} {'estimate':>10}"
        f" {'+/- 1 sd':>9} {'error':>7}",
    ]
    for match in card.matches:
        finding = match.finding
        diagnosis = "MISSED" if finding is None else finding.cause.value
        estimate = "" if match.estimate is None else f"{match.estimate:,.0f}"
        spread = "" if match.estimate_sd is None else f"{match.estimate_sd:,.0f}"
        error = "" if match.error is None else f"{match.error:+.0%}"
        lines.append(
            f"  {match.planted.incident_id:<9} {match.planted.cause:<21} {diagnosis:<21}"
            f" {match.truth:>10,.0f} {estimate:>10} {spread:>9} {error:>7}"
        )
    for alarm in card.false_alarms:
        where = alarm.category or "all categories"
        lines.append(
            f"  FALSE ALARM {alarm.cause.value}: {alarm.platform}, {where},"
            f" {alarm.start_date} to {alarm.end_date}"
        )
    lines.append("  " + _rates([card]))
    return "\n".join(lines) + "\n"


def summary(cards: Sequence[Scorecard]) -> str:
    return "overall\n  " + _rates(cards)


def _rates(cards: Sequence[Scorecard]) -> str:
    planted = sum(card.planted for card in cards)
    found = sum(card.found for card in cards)
    diagnosed = sum(card.diagnosed for card in cards)
    findings = sum(card.findings for card in cards)
    floor = EngineConfig().min_loss_gmv
    below_floor = sum(card.missed_below(floor) for card in cards)
    ordered = sorted(abs(error) for card in cards for error in card.errors)
    typical = f"{ordered[len(ordered) // 2]:.0%}" if ordered else "n/a"
    worst = f"{ordered[-1]:.0%}" if ordered else "n/a"
    missed = planted - found
    note = f" ({below_floor} of {missed} missed cost under Rs {floor:,.0f})" if missed else ""
    return (
        f"found {found}/{planted}{note}, correct cause {diagnosed}/{planted},"
        f" false alarms {findings - found}/{findings} findings,"
        f" estimate error typically {typical}, at worst {worst}"
    )


if __name__ == "__main__":
    sys.exit(main())
