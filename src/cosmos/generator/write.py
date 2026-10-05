"""Load generated tables against their contracts and write them out as Parquet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import duckdb
import pyarrow as pa

from cosmos.contracts import ALL_CONTRACTS, Violation, create_table_sql, validate_dataset
from cosmos.generator.generate import Dataset

RAW_SCHEMA = "raw"


class ContractViolationError(Exception):
    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations = tuple(violations)
        lines = [
            f"  {v.table}: {v.rule.value} ({v.detail})"
            + ("" if v.rows is None else f", {v.rows} rows")
            for v in self.violations
        ]
        super().__init__("generated data breaks its contracts:\n" + "\n".join(lines))


def load(conn: duckdb.DuckDBPyConnection, tables: Mapping[str, pa.Table]) -> None:
    """Create the raw tables, fill them and check them. Raises if any contract is broken."""
    conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{RAW_SCHEMA}"')
    for contract in ALL_CONTRACTS:
        conn.execute(create_table_sql(contract, RAW_SCHEMA))
        casts = ", ".join(
            f'CAST("{column.name}" AS {column.type.value})' for column in contract.columns
        )
        conn.register("incoming", tables[contract.name])
        conn.execute(f'INSERT INTO "{RAW_SCHEMA}"."{contract.name}" SELECT {casts} FROM incoming')
        conn.unregister("incoming")

    relations = {contract.name: f'"{RAW_SCHEMA}"."{contract.name}"' for contract in ALL_CONTRACTS}
    violations = validate_dataset(conn, ALL_CONTRACTS, relations)
    if violations:
        raise ContractViolationError(violations)


def write_dataset(dataset: Dataset, out_dir: Path) -> dict[str, int]:
    """Write `raw/<table>.parquet` and `truth/planted_incident.parquet`; return row counts.

    Ground truth goes in its own directory so nothing that reads raw data can
    pick it up by accident.
    """
    raw_dir, truth_dir = out_dir / "raw", out_dir / "truth"
    raw_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    conn = duckdb.connect()
    try:
        load(conn, dataset.tables)
        for contract in ALL_CONTRACTS:
            relation = f'"{RAW_SCHEMA}"."{contract.name}"'
            _copy(conn, relation, raw_dir / f"{contract.name}.parquet")
            row = conn.execute(f"SELECT count(*) FROM {relation}").fetchone()
            counts[contract.name] = 0 if row is None else int(row[0])
        conn.register("planted_incident", dataset.incidents)
        _copy(conn, "planted_incident", truth_dir / "planted_incident.parquet")
    finally:
        conn.close()
    return counts


def _copy(conn: duckdb.DuckDBPyConnection, relation: str, target: Path) -> None:
    # Written beside the target and swapped in, so an interrupted run never
    # leaves a half-written file under the real name.
    partial = target.with_suffix(".parquet.partial")
    quoted = partial.as_posix().replace("'", "''")
    conn.execute(f"COPY (SELECT * FROM {relation}) TO '{quoted}' (FORMAT parquet)")
    partial.replace(target)
