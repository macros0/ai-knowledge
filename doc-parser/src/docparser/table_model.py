"""Backend-independent, versioned structural table contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

TABLE_SCHEMA_VERSION = 1


@dataclass(frozen=True, order=True)
class CellAddress:
    row: int
    column: int


@dataclass(frozen=True)
class CellRange:
    sheet_index: int
    min_row: int
    min_column: int
    max_row: int
    max_column: int

    def contains(self, row: int, column: int) -> bool:
        return self.min_row <= row <= self.max_row and self.min_column <= column <= self.max_column


@dataclass(frozen=True)
class TableCell:
    address: str
    raw_value: str | int | float | bool | None
    display_text: str
    value_type: str
    number_format: str = 'General'
    formula: str | None = None
    cached_value: str | int | float | bool | None = None
    state: Literal['value', 'blank', 'formula_missing', 'error'] = 'value'
    merge_anchor: str | None = None
    hidden: bool = False


@dataclass(frozen=True)
class TableColumn:
    index: int  # one-based original Excel index
    letter: str
    header_path: list[str] = field(default_factory=list)
    header_addresses: list[str] = field(default_factory=list)
    hidden: bool = False


@dataclass(frozen=True)
class TableRow:
    row_number: int
    cells: dict[int, TableCell]
    hidden: bool = False


@dataclass(frozen=True)
class TableDescriptor:
    source_id: str
    table_id: str
    sheet_index: int
    sheet_name: str
    cell_range: CellRange
    header_rows: list[int] = field(default_factory=list)
    columns: list[TableColumn] = field(default_factory=list)
    merges: list[CellRange] = field(default_factory=list)
    schema_version: int = TABLE_SCHEMA_VERSION
    normalizer_version: int = 1
    method: str = 'unresolved'
    warnings: list[str] = field(default_factory=list)
    declared: bool = False
    table_name: str | None = None


@dataclass(frozen=True)
class TableSummary:
    row_count: int
    cell_count: int
    digest: str
    complete: bool = True


class TableSink(Protocol):
    def begin_table(self, descriptor: TableDescriptor) -> None: ...
    def append_rows(self, table_id: str, rows: list[TableRow]) -> None: ...
    def finish_table(self, table_id: str, summary: TableSummary) -> None: ...
