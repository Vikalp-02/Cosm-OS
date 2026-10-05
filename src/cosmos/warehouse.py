"""Build the warehouse from raw data by running the dbt project.

python -m cosmos.warehouse --data data
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

DBT_PROJECT_DIR = Path("warehouse")
WAREHOUSE_FILE = "warehouse.duckdb"


class WarehouseBuildError(Exception):
    pass


def build(data_dir: Path, project_dir: Path = DBT_PROJECT_DIR, *, quiet: bool = False) -> Path:
    """Run every model and data test, and return the path of the built database.

    With `quiet`, dbt's log is shown only if the build fails.

    dbt runs in its own process: it keeps global state, and the paths it needs
    go in through the environment without touching this process.
    """
    data_dir = data_dir.resolve()
    project_dir = project_dir.resolve()
    if not (data_dir / "raw").is_dir():
        raise WarehouseBuildError(f"no raw data in {data_dir}; run the generator first")
    if not (project_dir / "dbt_project.yml").is_file():
        raise WarehouseBuildError(f"no dbt project in {project_dir}")

    database = data_dir / WAREHOUSE_FILE
    environment = {
        **os.environ,
        "COSMOS_DATA_DIR": data_dir.as_posix(),
        "COSMOS_WAREHOUSE": database.as_posix(),
    }
    command = [
        sys.executable,
        "-c",
        "from dbt.cli.main import cli; cli()",
        "build",
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(project_dir),
    ]
    finished = subprocess.run(
        command, env=environment, check=False, capture_output=quiet, text=True
    )
    if finished.returncode != 0:
        detail = f"\n{finished.stdout}" if quiet else ""
        raise WarehouseBuildError(f"dbt build failed with exit code {finished.returncode}{detail}")
    return database


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cosmos.warehouse",
        description="Build the warehouse from the raw data and test it.",
    )
    parser.add_argument("--data", type=Path, default=Path("data"), help="data directory")
    parser.add_argument("--project", type=Path, default=DBT_PROJECT_DIR, help="dbt project")
    args = parser.parse_args(argv)
    try:
        database = build(args.data, args.project)
    except WarehouseBuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"\nWarehouse built at {database}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
