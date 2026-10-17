"""Lossless cell coverage with independent preview/embedding/context budgets."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html import escape
import json
import re
import unicodedata
from typing import Iterator

from app.models.schemas import TableRef


@dataclass(frozen=True)
class BudgetEstimate:
    tokens: int
    mode: str


class BudgetEstimator:
    def __init__(self, tokenizer=None):
        self.tokenizer = tokenizer

    def estimate(self, text: str) -> BudgetEstimate:
        if self.tokenizer is not None:
            return BudgetEstimate(len(self.tokenizer(text)), 'exact')
        # Compatibility normalization can expand one original character into
        # many tokenizer characters (for example U+FDFA). Retain the original
        # text, but reserve bytes for either representation. This remains an
        # estimate, not a claim about an unknown tokenizer's exact token count.
        normalized = unicodedata.normalize('NFKC', text)
        return BudgetEstimate(max(len(text.encode('utf8')), len(normalized.encode('utf8'))), 'estimated')


@dataclass(frozen=True)
class ProjectionLimits:
    classification_columns: int = 12
    classification_rows: int = 3
    classification_cell_chars: int = 160
    classification_chars: int = 12000
    classification_tokens: int = 10000
    embedding_tokens: int = 6000
    context_tokens: int = 6000
    max_fragment_chars: int = 2000


@dataclass(frozen=True)
class ClassificationPreview:
    text: str
    column_index_map: list[int]
    budget_mode: str = 'estimated'


@dataclass(frozen=True)
class TableFragment:
    fragment_id: str
    table_ref: TableRef
    content: str
    coverage_refs: list[TableRef]
    kind: str
    budget_mode: str = 'estimated'


def _clip(text, limit):
    if max(len(text), len(unicodedata.normalize('NFKC', text))) <= limit:
        return text
    # Only preview/identity labels are clipped. Cell values retain their exact
    # original text, and continuation refs address that original character range.
    budget = max(0, limit - 14)
    low, high, best = 0, min(len(text), budget), 0
    while low <= high:
        middle = (low + high) // 2
        if len(unicodedata.normalize('NFKC', text[:middle])) <= budget:
            best, low = middle, middle + 1
        else:
            high = middle - 1
    return text[:best] + ' [truncated]'


def build_classification_preview(descriptor, rows, limits: ProjectionLimits, estimator=None) -> ClassificationPreview:
    estimator = estimator or BudgetEstimator()
    columns = descriptor.columns
    count = max(1, min(12, limits.classification_columns))
    chosen = columns if len(columns) <= count else columns[:count // 2] + columns[-(count - count // 2):]
    payload = {'sheet': _clip(descriptor.sheet_name, 160), 'column_count': len(columns),
               'columns': [{'index': c.index, 'path': _clip(' / '.join(c.header_path), 160)} for c in chosen],
               'rows': [], 'truncated': len(columns) > len(chosen)}
    for row in rows:
        if row.row_number in descriptor.header_rows:
            continue
        payload['rows'].append({'row': row.row_number, 'cells': {str(c.index):
            _clip(row.cells[c.index].display_text, min(160, limits.classification_cell_chars))
            if c.index in row.cells else '' for c in chosen}})
        if len(payload['rows']) >= min(3, limits.classification_rows):
            break
    text = json.dumps(payload, ensure_ascii=False)
    # Preserve a valid JSON preview and mapping; reduce samples, then label size.
    while len(text) > limits.classification_chars or estimator.estimate(text).tokens > limits.classification_tokens:
        payload['truncated'] = True
        if payload['rows']:
            payload['rows'].pop()
        elif len(payload['columns']) > 1:
            payload['columns'].pop(len(payload['columns']) // 2)
        else:
            raise ValueError('Classification budget cannot fit table identity')
        text = json.dumps(payload, ensure_ascii=False)
    return ClassificationPreview(text, [c['index'] for c in payload['columns']], estimator.estimate(text).mode)


def iter_table_fragments(descriptor, rows, limits: ProjectionLimits, *, generation_id: str,
                         title_column: int | None = None, estimator=None) -> Iterator[TableFragment]:
    estimator = estimator or BudgetEstimator()
    token_budget = min(limits.embedding_tokens, limits.context_tokens)
    columns = {c.index: c for c in descriptor.columns}
    title_column = title_column or next(iter(columns), 1)
    def fits(text):
        return len(text) <= limits.max_fragment_chars and estimator.estimate(text).tokens <= token_budget
    for row in rows:
        key_cell = row.cells.get(title_column)
        key = _clip(key_cell.display_text, 96) if key_cell and key_cell.display_text else str(row.row_number)
        prefix = f'Sheet: {escape(_clip(descriptor.sheet_name, 64))}; row: {row.row_number}; key: {escape(key)}\n'
        if not fits(prefix + 'Cell: '):
            raise ValueError('Projection budget cannot fit table/row identity')
        pending, refs = [], []
        kind = 'table_overview' if row.row_number in descriptor.header_rows else 'row'
        ordinal = 0
        def fragment(content, coverage, part):
            nonlocal ordinal
            ordinal += 1
            identity = f'{generation_id}:{descriptor.table_id}:{row.row_number}:{ordinal}:{content}'
            return TableFragment(sha256(identity.encode()).hexdigest()[:32], coverage[0], content, coverage,
                                 'row_part' if part and kind == 'row' else kind, estimator.estimate(content).mode)
        for index, cell in sorted(row.cells.items()):
            if cell.state == 'blank' or (cell.raw_value == '' and cell.formula is None):
                continue
            column = columns.get(index)
            label = ' / '.join(column.header_path) if column else str(index)
            value = '[formula_result_missing]' if cell.state == 'formula_missing' else cell.display_text
            label_prefix = f'{cell.address} | {escape(_clip(label, 96))}: '
            ref_args = dict(generation_id=generation_id, source_id=descriptor.source_id,
                            table_id=descriptor.table_id, row_number=row.row_number, columns=[index])
            line = label_prefix + escape(value)
            current = prefix + '\n'.join(pending + [line])
            if fits(current):
                pending.append(line)
                refs.append(TableRef(**ref_args))
                continue
            if pending:
                yield fragment(prefix + '\n'.join(pending), refs, True)
                pending, refs = [], []
            if fits(prefix + line):
                pending, refs = [line], [TableRef(**ref_args)]
                continue
            start = 0
            while start < len(value):
                low, high = 1, len(value) - start
                best = 0
                while low <= high:
                    middle = (low + high) // 2
                    if fits(prefix + label_prefix + escape(value[start:start + middle])):
                        best, low = middle, middle + 1
                    else:
                        high = middle - 1
                if best == 0:
                    raise ValueError('Projection budget cannot fit cell identity')
                end = start + best
                ref = TableRef(**ref_args, text_start=start, text_end=end)
                yield fragment(prefix + label_prefix + escape(value[start:end]), [ref], True)
                start = end
        if pending:
            yield fragment(prefix + '\n'.join(pending), refs, ordinal > 0)


def project_table(descriptor, rows, limits, *, generation_id, title_column=None, extraction_mode='per_row'):
    """A small whole table fits one bounded projection; larger ones stay rows.

    Only one projection's worth of fragments is buffered. Once it cannot fit,
    continue streaming every buffered and remaining fragment without loss.
    """
    fragments = iter_table_fragments(descriptor, rows, limits, generation_id=generation_id, title_column=title_column)
    if extraction_mode != 'whole':
        yield from fragments
        return
    pending, size = [], 0
    estimator = BudgetEstimator()
    for item in fragments:
        size += len(item.content) + 1
        candidate = '\n'.join([f.content for f in pending] + [item.content])
        if size > limits.max_fragment_chars or estimator.estimate(candidate).tokens > min(limits.embedding_tokens,limits.context_tokens):
            yield from pending
            yield item
            yield from fragments
            return
        pending.append(item)
    if pending:
        text = '\n'.join(f.content for f in pending)
        identity = sha256((generation_id + descriptor.table_id + text).encode()).hexdigest()[:32]
        yield TableFragment(identity,pending[0].table_ref,text,[r for f in pending for r in f.coverage_refs], 'table_whole')


_SEPARATOR = re.compile(r":?-{3,}:?")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


def _row_cells(row: str) -> list[str] | None:
    """Read a complete physical row, preserving cell syntax and whitespace."""
    text = row.rstrip("\r\n").strip()
    if "\n" in text or "\r" in text or not text.startswith("|") or not text.endswith("|"):
        return None
    boundaries = []
    code_run = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and not code_run:
            i += 2
            continue
        if char == "`":
            end = i + 1
            while end < len(text) and text[end] == "`":
                end += 1
            run = end - i
            if not code_run:
                code_run = run
            elif run == code_run:
                code_run = 0
            i = end
            continue
        if char == "|" and not code_run:
            boundaries.append(i)
        i += 1
    if code_run or not boundaries or boundaries[0] != 0 or boundaries[-1] != len(text) - 1:
        return None
    return [text[left + 1:right] for left, right in zip(boundaries, boundaries[1:])]


def _retained_width(header: list[str], rows: list[list[str]]) -> int:
    if any(len(row) != len(header) for row in rows):
        return len(header)
    width = max((i + 1 for i, cell in enumerate(header) if cell.strip()), default=0)
    for row in rows:
        width = max(width, max((i + 1 for i, cell in enumerate(row) if cell.strip()), default=0))
    # An entirely empty table has no useful projection.
    return width or len(header)


def _trim_row(row: str, cells: list[str], width: int) -> str:
    stripped = row.strip()
    prefix = row[:len(row) - len(row.lstrip())]
    suffix = row[len(row.rstrip()):]
    return prefix + "|" + "|".join(cells[:width]) + "|" + suffix if stripped else row


def compact_table_columns(header: list[str], raw_rows: list[str]) -> tuple[list[str], list[str]]:
    """Remove only a contiguous tail empty in the header AND every source row.

    Prefix column indices remain unchanged. Ambiguous/ragged/multiline rows
    retain the original representation; input lists are never modified.
    """
    parsed = [_row_cells(row) for row in raw_rows]
    if any(row is None for row in parsed):
        return header, raw_rows
    width = _retained_width(header, parsed)
    if width == len(header):
        return header, raw_rows
    return header[:width], [_trim_row(raw, cells, width) for raw, cells in zip(raw_rows, parsed)]


def compact_markdown_tables(text: str) -> str:
    """Project complete, rectangular pipe tables outside code fences for LLM."""
    lines = text.splitlines(keepends=True)
    result = []
    i = 0
    fence_char = ""
    fence_size = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            run = fence.group(1)
            if not fence_char:
                fence_char, fence_size = run[0], len(run)
            elif run[0] == fence_char and len(run) >= fence_size and not line[fence.end():].strip():
                fence_char = ""
            result.append(line)
            i += 1
            continue
        if fence_char or line.startswith(("    ", "\t")) or i + 1 >= len(lines):
            result.append(line)
            i += 1
            continue
        header = _row_cells(line)
        separator = _row_cells(lines[i + 1])
        if not header or not separator or len(header) != len(separator) or not all(_SEPARATOR.fullmatch(cell.strip()) for cell in separator):
            result.append(line)
            i += 1
            continue
        end = i + 2
        rows = []
        valid = True
        while end < len(lines) and lines[end].lstrip().startswith("|"):
            cells = _row_cells(lines[end])
            if cells is None or len(cells) != len(header):
                valid = False
            rows.append(cells)
            end += 1
        # An unseparated continuation may belong to a multiline cell.
        if end < len(lines) and lines[end].strip() and not lines[end].lstrip().startswith("#") and not _FENCE.match(lines[end]):
            valid = False
        if valid and rows:
            width = _retained_width(header, rows)
            if width < len(header):
                for raw, cells in zip(lines[i:end], [header, separator, *rows]):
                    result.append(_trim_row(raw, cells, width))
            else:
                result.extend(lines[i:end])
        else:
            result.extend(lines[i:end])
        i = end
    return "".join(result)
