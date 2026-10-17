"""Small read-only XLSX samples; no workbook evaluation or extraction pipeline."""
from __future__ import annotations

import posixpath
import re
import zipfile

from defusedxml import ElementTree as ET

from docparser.archive_guard import validate_zip

MAX_ROWS = 30
MAX_COLUMNS = 20
MAX_CELL_CHARS = 500
MAX_SHEETS = 256


class _BudgetReached(Exception):
    pass


class _Reader:
    def __init__(self, stream, remaining):
        self.stream, self.remaining = stream, remaining

    def read(self, size=-1):
        if self.remaining[0] <= 0:
            raise _BudgetReached()
        data = self.stream.read(min(16384, size if size >= 0 else 16384, self.remaining[0]))
        self.remaining[0] -= len(data)
        return data


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _text(element):
    return "".join(part.text or "" for part in element.iter() if _tag(part) == "t")


def _column(reference):
    match = re.fullmatch(r"([A-Z]{1,3})[1-9][0-9]{0,6}", reference)
    if not match:
        raise ValueError("Invalid spreadsheet coordinate")
    value = 0
    for letter in match[1]:
        value = value * 26 + ord(letter) - 64
    return value


def preview_xlsx(path, *, sheet_index=0, scan_bytes=16 * 1024 * 1024):
    """Read first 30 nonempty rows, A:T, ignoring unreliable XLSX dimensions.

    XML expansion, sheet listing, cells and response text are bounded. Shared
    strings are resolved only for the displayed sample. Formulas are text.
    Must run inside the resource-limited parser worker for untrusted input.
    """
    validate_zip(path)
    result = {"sheets": [], "sheet_index": sheet_index, "rows": [],
              "columns": [], "limited": False, "max_rows": MAX_ROWS,
              "max_columns": MAX_COLUMNS, "max_cell_chars": MAX_CELL_CHARS}
    with zipfile.ZipFile(path) as archive:
        # Metadata has its own small ceiling; the sample budget is independent.
        def metadata(name):
            with archive.open(name) as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("Spreadsheet metadata exceeds preview limit")
            return ET.fromstring(data)

        relations = {r.get("Id"): r.get("Target", "")
                     for r in metadata("xl/_rels/workbook.xml.rels")
                     if r.get("TargetMode") != "External"}
        sheets = [s for s in metadata("xl/workbook.xml").iter() if _tag(s) == "sheet"]
        result["limited"] = len(sheets) > MAX_SHEETS
        for index, sheet in enumerate(sheets[:MAX_SHEETS]):
            result["sheets"].append({"index": index, "name": (sheet.get("name") or "")[:255]})
        if not 0 <= sheet_index < len(result["sheets"]):
            raise ValueError("Unknown spreadsheet sheet")
        sheet = sheets[sheet_index]
        relation_id = next((v for k, v in sheet.attrib.items() if k.rsplit("}", 1)[-1] == "id"), None)
        target = relations.get(relation_id, "")
        member = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
        if not member.startswith("xl/") or member not in archive.namelist():
            raise ValueError("Invalid spreadsheet sheet target")
        # A long formatted tail must not consume the budget needed to resolve
        # text in the rows already sampled from the worksheet.
        has_shared = "xl/sharedStrings.xml" in archive.namelist()
        remaining = [scan_bytes // 2 if has_shared else scan_bytes]
        sheet_budget = remaining[0]
        shared_refs = {}  # displayed cell identities -> shared string indices
        width = 0

        def clip(value):
            if len(value) > MAX_CELL_CHARS:
                result["limited"] = True
                return value[:MAX_CELL_CHARS - 1] + "…"
            return value

        try:
            with archive.open(member) as stream:
                context = ET.iterparse(_Reader(stream, remaining), events=("start", "end"))
                root = None
                row_number = 0
                for event, element in context:
                    if root is None:
                        root = element
                    if event != "end" or _tag(element) != "row":
                        continue
                    row_number = int(element.get("r", row_number + 1))
                    cells, pending = {}, {}
                    nonempty = False
                    for cell in element:
                        if _tag(cell) != "c":
                            continue
                        column = _column(cell.get("r", ""))
                        value = next((v.text or "" for v in cell if _tag(v) == "v"), "")
                        formula = next((v.text or "" for v in cell if _tag(v) == "f"), "")
                        if cell.get("t") == "inlineStr":
                            value = _text(cell)
                        elif formula and not value:
                            value = "=" + formula
                        if not value:
                            continue
                        nonempty = True
                        if column > MAX_COLUMNS:
                            result["limited"] = True
                            continue
                        if cell.get("t") == "s":
                            pending[column] = int(value)
                            value = "…"
                        cells[column] = clip(value)
                    if nonempty:
                        if len(result["rows"]) >= MAX_ROWS:
                            result["limited"] = True
                            break
                        width = max(width, max(cells, default=0))
                        index = len(result["rows"])
                        shared_refs.update({(index, col): number for col, number in pending.items()})
                        result["rows"].append({"number": row_number, "cells": cells})
                    element.clear()
                    # Avoid retaining millions of empty row elements in sheetData.
                    for child in root:
                        if _tag(child) == "sheetData":
                            child.clear()
        except _BudgetReached:
            result["limited"] = True
        if shared_refs:
            strings = {}
            try:
                remaining[0] += scan_bytes - sheet_budget
                needed = set(shared_refs.values())
                with archive.open("xl/sharedStrings.xml") as stream:
                    context = ET.iterparse(_Reader(stream, remaining), events=("start", "end"))
                    root = None
                    index = 0
                    for event, element in context:
                        if root is None:
                            root = element
                        if event != "end" or _tag(element) != "si":
                            continue
                        if index in needed:
                            strings[index] = clip(_text(element))
                        index += 1
                        element.clear()
                        root.clear()
                        if needed <= strings.keys():
                            break
            except _BudgetReached:
                result["limited"] = True
            for (row, col), number in shared_refs.items():
                result["rows"][row]["cells"][col] = strings.get(number, "…")
            if not set(shared_refs.values()) <= strings.keys():
                result["limited"] = True
        result["columns"] = [chr(65 + i) for i in range(width)]
        for row in result["rows"]:
            row["cells"] = [row["cells"].get(col, "") for col in range(1, width + 1)]
    return result
