"""Run the engine over a dataset and score it."""

from __future__ import annotations

from pathlib import Path

from cosmos.engine import EngineConfig, investigate
from cosmos.evals.score import Scorecard, read_planted, score
from cosmos.generator import GeneratorConfig, build_dataset, write_dataset
from cosmos.warehouse import WAREHOUSE_FILE, build


def evaluate(data_dir: Path, config: EngineConfig | None = None) -> Scorecard:
    """Score a dataset that has already been generated and built into a warehouse."""
    planted = read_planted(data_dir)
    tenants = {incident.tenant_id for incident in planted}
    if len(tenants) != 1:
        raise ValueError(f"expected incidents for exactly one tenant, found {sorted(tenants)}")
    findings = investigate(data_dir / WAREHOUSE_FILE, tenants.pop(), config)
    return score(planted, findings)


def evaluate_seed(
    seed: int, work_dir: Path, *, small: bool = False, config: EngineConfig | None = None
) -> Scorecard:
    """Generate a fresh dataset for `seed` under `work_dir`, build its warehouse and score it."""
    generator = GeneratorConfig.small(seed) if small else GeneratorConfig(seed=seed)
    data_dir = work_dir / f"seed-{seed}"
    write_dataset(build_dataset(generator), data_dir)
    build(data_dir, quiet=True)
    return evaluate(data_dir, config)
