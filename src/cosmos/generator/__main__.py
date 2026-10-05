"""Command line entry point: `python -m cosmos.generator`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from cosmos.generator.config import GeneratorConfig
from cosmos.generator.generate import build_dataset
from cosmos.generator.write import write_dataset


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cosmos.generator",
        description="Generate a synthetic quick-commerce dataset with planted incidents.",
    )
    parser.add_argument("--out", type=Path, default=Path("data"), help="output directory")
    parser.add_argument("--seed", type=int, default=GeneratorConfig().seed)
    parser.add_argument("--small", action="store_true", help="a small dataset, built in seconds")
    args = parser.parse_args(argv)

    config = GeneratorConfig.small(args.seed) if args.small else GeneratorConfig(seed=args.seed)
    dataset = build_dataset(config)
    counts = write_dataset(dataset, args.out)

    width = max(len(name) for name in counts)
    for name, rows in counts.items():
        print(f"{name:<{width}}  {rows:>12,}")
    print(f"\n{dataset.incidents.num_rows} incidents planted. Written to {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
