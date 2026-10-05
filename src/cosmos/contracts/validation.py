"""Check data against its contracts.

Checks run as set-based queries, not database constraints, so the same check
works on a Parquet file, a view or a loaded table, and reports every problem
with a row count instead of stopping at the first bad row.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

import duckdb

from cosmos.contracts.model import TableContract


class Rule(StrEnum):
    MISSING_COLUMN = "missing_column"
    UNEXPECTED_COLUMN = "unexpected_column"
    WRONG_TYPE = "wrong_type"
    NULL_VALUE = "null_value"
    FAILED_CHECK = "failed_check"
    DUPLICATE_GRAIN = "duplicate_grain"
    ORPHAN_ROW = "orphan_row"


# Row-level checks cannot be trusted, or even run, against a table with these.
_BLOCKING: frozenset[Rule] = frozenset({Rule.MISSING_COLUMN, Rule.WRONG_TYPE})


@dataclass(frozen=True, slots=True)
class Violation:
    table: str
    rule: Rule
    detail: str
    # Rows affected. None for problems with the table's shape.
    rows: int | None = None


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _count(conn: duckdb.DuckDBPyConnection, sql: str) -> list[int]:
    row = conn.execute(sql).fetchone()
    if row is None:
        raise RuntimeError(f"count query returned no row: {sql}")
    return [int(value) for value in row]


def validate_table(
    conn: duckdb.DuckDBPyConnection, contract: TableContract, relation: str
) -> list[Violation]:
    """Check one relation against its contract.

    `relation` is spliced into SQL as written (a table name, or an expression
    such as `read_parquet('...')`), so it must come from code, never from input.
    """
    violations = _shape_violations(conn, contract, relation)
    if any(violation.rule in _BLOCKING for violation in violations):
        return violations
    violations += _row_violations(conn, contract, relation)
    violations += _grain_violations(conn, contract, relation)
    return violations


def validate_dataset(
    conn: duckdb.DuckDBPyConnection,
    contracts: Iterable[TableContract],
    relations: Mapping[str, str],
) -> list[Violation]:
    """Check every table, then the references between the ones whose shape is sound."""
    contracts = tuple(contracts)
    violations: list[Violation] = []
    unsound: set[str] = set()
    for contract in contracts:
        found = validate_table(conn, contract, relations[contract.name])
        if any(violation.rule in _BLOCKING for violation in found):
            unsound.add(contract.name)
        violations += found

    for contract in contracts:
        if contract.name not in unsound:
            violations += _orphan_violations(conn, contract, relations, skip=unsound)
    return violations


def _shape_violations(
    conn: duckdb.DuckDBPyConnection, contract: TableContract, relation: str
) -> list[Violation]:
    described = conn.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    actual = {str(row[0]): str(row[1]) for row in described}

    violations: list[Violation] = []
    for column in contract.columns:
        found = actual.get(column.name)
        if found is None:
            violations.append(Violation(contract.name, Rule.MISSING_COLUMN, column.name))
        elif found != column.type.value:
            detail = f"{column.name}: expected {column.type.value}, found {found}"
            violations.append(Violation(contract.name, Rule.WRONG_TYPE, detail))
    for name in sorted(actual.keys() - set(contract.column_names)):
        violations.append(Violation(contract.name, Rule.UNEXPECTED_COLUMN, name))
    return violations


def _row_violations(
    conn: duckdb.DuckDBPyConnection, contract: TableContract, relation: str
) -> list[Violation]:
    probes: list[tuple[Rule, str, str]] = []
    for column in contract.columns:
        if not column.nullable:
            probes.append((Rule.NULL_VALUE, column.name, f"{_quote(column.name)} IS NULL"))
        if column.check is not None:
            probes.append((Rule.FAILED_CHECK, column.check, f"NOT ({column.check})"))

    # One scan answers every probe.
    counters = ", ".join(f"count(*) FILTER (WHERE {predicate})" for _, _, predicate in probes)
    counts = _count(conn, f"SELECT {counters} FROM {relation}")
    return [
        Violation(contract.name, rule, detail, rows)
        for (rule, detail, _), rows in zip(probes, counts, strict=True)
        if rows > 0
    ]


def _grain_violations(
    conn: duckdb.DuckDBPyConnection, contract: TableContract, relation: str
) -> list[Violation]:
    grain = ", ".join(_quote(name) for name in contract.grain)
    (surplus,) = _count(
        conn,
        f"SELECT coalesce(sum(n - 1), 0) FROM "
        f"(SELECT count(*) AS n FROM {relation} GROUP BY {grain} HAVING count(*) > 1)",
    )
    if surplus == 0:
        return []
    return [Violation(contract.name, Rule.DUPLICATE_GRAIN, ", ".join(contract.grain), surplus)]


def _orphan_violations(
    conn: duckdb.DuckDBPyConnection,
    contract: TableContract,
    relations: Mapping[str, str],
    *,
    skip: set[str],
) -> list[Violation]:
    violations: list[Violation] = []
    for key in contract.foreign_keys:
        if key.references in skip:
            continue
        pairs = list(zip(key.columns, key.referenced_columns, strict=True))
        present = " AND ".join(f"c.{_quote(child)} IS NOT NULL" for child, _ in pairs)
        matches = " AND ".join(f"p.{_quote(parent)} = c.{_quote(child)}" for child, parent in pairs)
        (orphans,) = _count(
            conn,
            f"SELECT count(*) FROM {relations[contract.name]} AS c WHERE {present} "
            f"AND NOT EXISTS (SELECT 1 FROM {relations[key.references]} AS p WHERE {matches})",
        )
        if orphans > 0:
            detail = f"({', '.join(key.columns)}) not found in {key.references}"
            violations.append(Violation(contract.name, Rule.ORPHAN_ROW, detail, orphans))
    return violations
