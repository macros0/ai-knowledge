"""Bounded OOXML extraction; no formula, macro or external-link evaluation.

Worksheets are scanned twice: coordinates/metadata, then row batches. Shared
strings live in a private temporary SQLite spool instead of a workbook-sized
Python list. XML parents are released after each row/string.
"""

from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from dataclasses import asdict, fields, replace
from hashlib import sha256
import json
import posixpath
import re
import sqlite3
from tempfile import TemporaryDirectory
import zipfile

from defusedxml.ElementTree import iterparse, parse
from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter, range_boundaries
from openpyxl.utils.datetime import from_excel, CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900

from docparser.archive_guard import ArchiveLimitError, validate_zip
from docparser.blocks import Block
from docparser.table_headers import (
    combine_numbered_regions,
    detect_table_regions,
    normalize_headers,
    split_stacked_regions,
)
from docparser.table_model import (
    CellAddress,
    CellRange,
    TableCell,
    TableColumn,
    TableDescriptor,
    TableRow,
    TableSummary,
)

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_ROWS = 250_000
MAX_CELLS = 5_000_000
MAX_MERGES = 50_000
MAX_STYLES = 65_536
MAX_TEXT_BYTES = 256 * 1024 * 1024
BATCH_BYTES = 1024 * 1024
_ROW_FIELDS = tuple(field.name for field in fields(TableRow))
_CELL_FIELDS = tuple(field.name for field in fields(TableCell))


def _row_json_default(value):
    # Let JSON traverse containers once rather than constructing a second full
    # row with recursive asdict. Field order is part of the persisted checksum.
    names = _ROW_FIELDS if isinstance(value, TableRow) else (
        _CELL_FIELDS if isinstance(value, TableCell) else None
    )
    if names is None:
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
    return {name: getattr(value, name) for name in names}


def row_bytes(row: TableRow) -> bytes:
    return json.dumps(
        row, default=_row_json_default, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf8")


def _xml(archive, name):
    with archive.open(name) as stream:
        return parse(stream).getroot()


def _related(archive, owner):
    folder, basename = posixpath.split(owner)
    name = posixpath.join(folder, "_rels", basename + ".rels")
    if name not in archive.namelist():
        return {}
    result = {}
    for relation in _xml(archive, name):
        if relation.get("TargetMode") == "External":
            continue
        target = relation.get("Target", "")
        resolved = posixpath.normpath(
            target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
        )
        if resolved.startswith("../"):
            raise ArchiveLimitError("unsafe spreadsheet relationship")
        result[relation.get("Id")] = resolved
    return result


def _formats(archive):
    if "xl/styles.xml" not in archive.namelist():
        return ["General"]
    formats = dict(BUILTIN_FORMATS)
    indices = []
    with archive.open("xl/styles.xml") as stream:
        stack = []
        for event, element in iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(element)
                continue
            if element.tag == NS + "numFmt":
                if len(formats) >= MAX_STYLES:
                    raise ArchiveLimitError("spreadsheet style safety limit exceeded")
                formats[int(element.get("numFmtId"))] = element.get("formatCode", "General")
            elif element.tag == NS + "xf" and len(stack) > 1 and stack[-2].tag == NS + "cellXfs":
                if len(indices) >= MAX_STYLES:
                    raise ArchiveLimitError("spreadsheet style safety limit exceeded")
                indices.append(int(element.get("numFmtId", "0")))
            stack.pop()
            if stack:
                stack[-1].remove(element)
    return [formats.get(index, "General") for index in indices] or ["General"]


def _bold_styles(archive):
    if "xl/styles.xml" not in archive.namelist():
        return set()
    fonts, indices, stack, bold = [], [], [], False
    with archive.open("xl/styles.xml") as stream:
        for event, element in iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(element)
                if element.tag == NS + "font":
                    bold = False
                continue
            if element.tag == NS + "b" and any(e.tag == NS + "font" for e in stack):
                bold = element.get("val", "1") not in {"0", "false"}
            elif element.tag == NS + "font":
                if len(fonts) >= MAX_STYLES:
                    raise ArchiveLimitError("spreadsheet style safety limit exceeded")
                fonts.append(bold)
            elif element.tag == NS + "xf" and len(stack) > 1 and stack[-2].tag == NS + "cellXfs":
                if len(indices) >= MAX_STYLES:
                    raise ArchiveLimitError("spreadsheet style safety limit exceeded")
                indices.append(int(element.get("fontId", "0")))
            stack.pop()
            if stack:
                stack[-1].remove(element)
    return {i for i, f in enumerate(indices) if f < len(fonts) and fonts[f]}


def _shared_strings(archive, connection):
    connection.execute("CREATE TABLE strings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    if "xl/sharedStrings.xml" not in archive.namelist():
        return
    count = total = 0
    with archive.open("xl/sharedStrings.xml") as stream:
        stack = []
        for event, element in iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(element)
                continue
            if element.tag == NS + "si":
                value = "".join(t.text or "" for t in element.iter(NS + "t"))
                total += len(value.encode("utf8"))
                if total > MAX_TEXT_BYTES or count >= MAX_CELLS:
                    raise ArchiveLimitError("spreadsheet shared-string safety limit exceeded")
                connection.execute("INSERT INTO strings VALUES (?, ?)", (count, value))
                count += 1
                if count % 256 == 0:
                    connection.commit()
                if len(stack) > 1:
                    stack[-2].remove(element)
            stack.pop()
    connection.commit()


def _range(index, ref):
    min_col, min_row, max_col, max_row = range_boundaries(ref)
    if None in (min_col, min_row, max_col, max_row) or not (
        1 <= min_row <= max_row <= 1_048_576 and 1 <= min_col <= max_col <= 16_384
    ):
        raise ArchiveLimitError("invalid spreadsheet range")
    return CellRange(index, min_row, min_col, max_row, max_col)


def _cell(element, formats, connection, epoch):
    address = element.get("r", "")
    row, column = coordinate_to_tuple(address)
    if not (1 <= row <= 1_048_576 and 1 <= column <= 16_384):
        raise ArchiveLimitError("invalid spreadsheet cell coordinate")
    cell_type = element.get("t", "n")
    value_element = element.find(NS + "v")
    formula_element = element.find(NS + "f")
    value = value_element.text if value_element is not None else None
    kind = "number"
    numeric_text = None
    if cell_type == "s" and value is not None:
        entry = connection.execute("SELECT value FROM strings WHERE id=?", (int(value),)).fetchone()
        if entry is None:
            raise ValueError("spreadsheet shared string is missing")
        value, kind = entry[0], "string"
    elif cell_type == "inlineStr":
        value, kind = "".join(t.text or "" for t in element.iter(NS + "t")), "string"
    elif cell_type in {"str", "d", "e"}:
        kind = {"str": "string", "d": "date", "e": "error"}[cell_type]
        if cell_type == "str" and value_element is not None:
            value = value or ""
    elif cell_type == "b" and value is not None:
        value, kind = value == "1", "boolean"
    elif value is not None:
        numeric_text = value
        value = float(value) if any(c in value.lower() for c in ".e") else int(value)
    style = int(element.get("s", "0"))
    if not 0 <= style < len(formats):
        raise ValueError("spreadsheet style is missing")
    number_format = formats[style]
    formula = formula_element.text or "" if formula_element is not None else None
    state = (
        "formula_missing"
        if formula is not None and value is None
        else "blank"
        if value is None
        else "error"
        if kind == "error"
        else "value"
    )
    display = "" if value is None else str(value)
    if kind == "number" and value is not None:
        if re.fullmatch(r"0{2,}", number_format) and (
            isinstance(value, int) or isinstance(value, float) and value.is_integer()
        ):
            # XML may spell an integer as 7.0 or 7E+00. Use its exact decimal
            # value: a float can round a large identifier or a fractional tail.
            exact = Decimal(numeric_text)
            if exact == exact.to_integral_value():
                display = str(int(exact)).zfill(len(number_format))
        elif is_date_format(number_format):
            kind = "date"
            display = from_excel(value, epoch=epoch).isoformat()
    # Preserve exact XML numeric value when a binary float rounds it. Type is
    # still numeric; display uses Excel's ordinary numeric/date representation.
    raw_value = (
        numeric_text
        if numeric_text is not None and Decimal(numeric_text) != Decimal(str(value))
        else value
    )
    return (
        row,
        column,
        TableCell(
            address,
            raw_value,
            display,
            kind,
            number_format,
            formula,
            raw_value if formula is not None else None,
            state,
        ),
    )


def _rows(archive, name, formats, connection, epoch, *, bold_styles=frozenset(), with_styles=False):
    count = cells = total = 0
    with archive.open(name) as stream:
        stack = []
        for event, element in iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(element)
                continue
            if element.tag == NS + "row":
                count += 1
                if count > MAX_ROWS:
                    raise ArchiveLimitError("spreadsheet row safety limit exceeded")
                result = {}
                bold_columns = set()
                row_number = int(element.get("r", "0"))
                for entry in element.findall(NS + "c"):
                    cells += 1
                    if cells > MAX_CELLS:
                        raise ArchiveLimitError("spreadsheet cell safety limit exceeded")
                    row, col, cell = _cell(entry, formats, connection, epoch)
                    if row != row_number or col in result:
                        raise ValueError("inconsistent spreadsheet row/cell coordinate")
                    total += len(cell.display_text.encode("utf8")) + len(
                        (cell.formula or "").encode("utf8")
                    )
                    if total > MAX_TEXT_BYTES:
                        raise ArchiveLimitError("spreadsheet text safety limit exceeded")
                    result[col] = cell
                    if (
                        with_styles
                        and cell.display_text
                        and int(entry.get("s", "0")) in bold_styles
                    ):
                        bold_columns.add(col)
                table_row = TableRow(row_number, result, element.get("hidden") == "1")
                yield (table_row, frozenset(bold_columns)) if with_styles else table_row
                if len(stack) > 1:
                    stack[-2].remove(element)
            stack.pop()
            # Keep descendants until a row is consumed; release unrelated XML.
            if stack and not any(e.tag == NS + "row" for e in stack):
                if element in stack[-1]:
                    stack[-1].remove(element)


def _sheet_metadata(archive, name, index):
    merges, hidden, declared = [], set(), []
    related = _related(archive, name)
    with archive.open(name) as stream:
        stack = []
        for event, element in iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(element)
                continue
            if element.tag == NS + "mergeCell":
                if len(merges) >= MAX_MERGES:
                    raise ArchiveLimitError("spreadsheet merge safety limit exceeded")
                area = _range(index, element.get("ref", ""))
                if (area.max_row - area.min_row + 1) * (
                    area.max_column - area.min_column + 1
                ) > MAX_CELLS:
                    raise ArchiveLimitError("spreadsheet merge area safety limit exceeded")
                merges.append(area)
            elif element.tag == NS + "col" and element.get("hidden") == "1":
                low, high = int(element.get("min")), int(element.get("max"))
                if not 1 <= low <= high <= 16_384:
                    raise ArchiveLimitError("invalid spreadsheet column range")
                hidden.update(range(low, high + 1))
            elif element.tag == NS + "tablePart":
                target = related.get(element.get(REL_NS + "id"))
                if target is None:
                    raise ValueError("spreadsheet table relationship is missing")
                table = _xml(archive, target)
                declared.append((
                    _range(index, table.get("ref", "")), table.get("displayName"),
                    int(table.get("headerRowCount", "1")),
                    [column.get("name", "") for column in table.findall(NS + "tableColumns/" + NS + "tableColumn")],
                ))
            stack.pop()
            if stack:
                stack[-1].remove(element)
    return merges, hidden, declared


def _occupied(cell):
    return cell.state != "blank" and (cell.formula is not None or cell.raw_value != "")


def parse_structural_xlsx(path, sink, source_id="root"):
    validate_zip(path)
    blocks = []
    with zipfile.ZipFile(path) as archive, TemporaryDirectory(prefix="okf-tables-") as scratch:
        with closing(sqlite3.connect(scratch + "/strings.db")) as connection:
            _shared_strings(archive, connection)
            formats = _formats(archive)
            bold_styles = _bold_styles(archive)
            workbook = _xml(archive, "xl/workbook.xml")
            epoch_flag = workbook.find(NS + "workbookPr")
            epoch = (
                CALENDAR_MAC_1904
                if epoch_flag is not None and epoch_flag.get("date1904") in {"1", "true"}
                else CALENDAR_WINDOWS_1900
            )
            related = _related(archive, "xl/workbook.xml")
            for index, sheet in enumerate(workbook.findall(NS + "sheets/" + NS + "sheet")):
                name = related.get(sheet.get(REL_NS + "id"))
                if name is None:
                    raise ValueError("spreadsheet sheet relationship is missing")
                merges, hidden, declared = _sheet_metadata(archive, name, index)

                def occupied_addresses():
                    for row in _rows(archive, name, formats, connection, epoch):
                        for col, cell in row.cells.items():
                            if _occupied(cell):
                                yield CellAddress(row.row_number, col)

                ranges = detect_table_regions(
                    index, [a for a, _, _, _ in declared], occupied_addresses(), merges
                )
                declared_names = {a: n for a, n, _, _ in declared}
                declared_headers = {a: (count, names) for a, _, count, names in declared}
                from itertools import islice

                first_rows = list(
                    islice(
                        (
                            row
                            for row in _rows(archive, name, formats, connection, epoch)
                            if any(_occupied(cell) for cell in row.cells.values())
                        ),
                        8,
                    )
                )
                ranges = combine_numbered_regions(ranges, first_rows, merges, declared_names)
                ranges = [
                    part
                    for area in ranges
                    for part in (
                        [area]
                        if area in declared_names
                        else split_stacked_regions(
                            area,
                            _rows(
                                archive,
                                name,
                                formats,
                                connection,
                                epoch,
                                bold_styles=bold_styles,
                                with_styles=True,
                            ),
                            merges,
                        )
                    )
                ]
                if len(ranges) > 1000:
                    raise ArchiveLimitError("spreadsheet table safety limit exceeded")
                for area in ranges:
                    table_name = declared_names.get(area)
                    identity = f"{source_id}:{index}:{area.min_row}:{area.min_column}:{area.max_row}:{area.max_column}"
                    descriptor = TableDescriptor(
                        source_id,
                        sha256(identity.encode()).hexdigest()[:32],
                        index,
                        sheet.get("name", ""),
                        area,
                        columns=[
                            TableColumn(c, get_column_letter(c), hidden=c in hidden)
                            for c in range(area.min_column, area.max_column + 1)
                        ],
                        merges=merges,
                        declared=area in declared_names,
                        table_name=table_name,
                        normalizer_version=4,
                    )
                    sample = []
                    bold_rows = set()
                    for row, bold_columns in _rows(
                        archive,
                        name,
                        formats,
                        connection,
                        epoch,
                        bold_styles=bold_styles,
                        with_styles=True,
                    ):
                        if area.min_row <= row.row_number <= area.max_row:
                            cells = {
                                c: v
                                for c, v in row.cells.items()
                                if area.min_column <= c <= area.max_column
                            }
                            if bold_columns.intersection(cells):
                                bold_rows.add(row.row_number)
                            if any(_occupied(c) for c in cells.values()):
                                sample.append(replace(row, cells=cells))
                            if len(sample) >= 8:
                                break
                    header_count, column_names = declared_headers.get(area, (1, []))
                    decision = normalize_headers(
                        descriptor, sample, merges, bold_rows=bold_rows,
                        declared_header_count=header_count, declared_column_names=column_names,
                    )
                    descriptor = replace(
                        descriptor,
                        header_rows=decision.header_rows,
                        columns=decision.columns,
                        method=decision.method,
                        warnings=decision.warnings,
                    )
                    sink.begin_table(descriptor)
                    digest = sha256()
                    batch, batch_size, row_count, cell_count = [], 2, 0, 0
                    for row in _rows(archive, name, formats, connection, epoch):
                        if not area.min_row <= row.row_number <= area.max_row:
                            continue
                        values = {}
                        for col, cell in row.cells.items():
                            if not area.min_column <= col <= area.max_column:
                                continue
                            anchor = next(
                                (
                                    f"{get_column_letter(m.min_column)}{m.min_row}"
                                    for m in merges
                                    if m.contains(row.row_number, col)
                                ),
                                None,
                            )
                            values[col] = replace(
                                cell, merge_anchor=anchor, hidden=row.hidden or col in hidden
                            )
                        row = replace(row, cells=values)
                        encoded = row_bytes(row)
                        serialized_size = len(json.dumps(asdict(row)).encode("utf8")) + 2
                        if batch and (
                            len(batch) >= 256 or batch_size + serialized_size > BATCH_BYTES
                        ):
                            sink.append_rows(descriptor.table_id, batch)
                            batch, batch_size = [], 2
                        batch.append(row)
                        batch_size += serialized_size
                        digest.update(encoded + b"\n")
                        row_count += 1
                        cell_count += sum(_occupied(c) for c in values.values())
                    if batch:
                        sink.append_rows(descriptor.table_id, batch)
                    sink.finish_table(
                        descriptor.table_id, TableSummary(row_count, cell_count, digest.hexdigest())
                    )
                    blocks.append(
                        Block(
                            "heading",
                            f"Таблица: {descriptor.sheet_name}",
                            level=1,
                            meta={"source_id": source_id, "structural_table_heading": True},
                        )
                    )
                    blocks.append(
                        Block(
                            "table",
                            "",
                            meta={"source_id": source_id, "table_descriptor": asdict(descriptor)},
                        )
                    )
    return blocks
