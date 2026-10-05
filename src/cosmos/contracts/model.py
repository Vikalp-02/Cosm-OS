"""Building blocks for table contracts.

A contract states what a table must look like before anything downstream reads
it: its columns, their types, its grain and its row-level rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SqlType(StrEnum):
    VARCHAR = "VARCHAR"
    INTEGER = "INTEGER"
    BIGINT = "BIGINT"
    MONEY = "DECIMAL(18,2)"
    DATE = "DATE"
    BOOLEAN = "BOOLEAN"


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: SqlType
    description: str
    nullable: bool = False
    # SQL boolean expression over the row. NULL results pass, so a check on a
    # nullable column only constrains the values that are present.
    check: str | None = None


@dataclass(frozen=True, slots=True)
class ForeignKey:
    columns: tuple[str, ...]
    references: str
    referenced_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableContract:
    name: str
    description: str
    # Columns that identify exactly one row.
    grain: tuple[str, ...]
    columns: tuple[Column, ...]
    foreign_keys: tuple[ForeignKey, ...] = ()

    def __post_init__(self) -> None:
        names = self.column_names
        if len(set(names)) != len(names):
            raise ValueError(f"{self.name}: duplicate column names")
        if not self.grain:
            raise ValueError(f"{self.name}: grain must name at least one column")

        by_name = {column.name: column for column in self.columns}
        for name in self.grain:
            if name not in by_name:
                raise ValueError(f"{self.name}: grain column {name!r} is not defined")
            if by_name[name].nullable:
                raise ValueError(f"{self.name}: grain column {name!r} cannot be nullable")

        for key in self.foreign_keys:
            if len(key.columns) != len(key.referenced_columns):
                raise ValueError(f"{self.name}: foreign key to {key.references} is uneven")
            for name in key.columns:
                if name not in by_name:
                    raise ValueError(f"{self.name}: foreign key column {name!r} is not defined")

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)
