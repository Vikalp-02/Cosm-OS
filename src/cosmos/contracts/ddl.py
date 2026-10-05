"""Render contracts as DuckDB table definitions."""

from __future__ import annotations

from cosmos.contracts.model import TableContract


def create_table_sql(contract: TableContract, schema: str) -> str:
    lines = [
        f'    "{column.name}" {column.type.value}{"" if column.nullable else " NOT NULL"}'
        for column in contract.columns
    ]
    body = ",\n".join(lines)
    return f'CREATE TABLE IF NOT EXISTS "{schema}"."{contract.name}" (\n{body}\n)'
