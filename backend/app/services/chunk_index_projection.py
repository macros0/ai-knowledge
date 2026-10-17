"""Deterministic, lossless representations for generation-scoped chunk points.

Version 1 bounds the actual provider string, including repeated hints, by both
original and NFKC UTF-8 bytes. This is not an exact tokenizer guarantee.
Canonical source text is never truncated or replaced by this projection.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import unicodedata
from typing import Iterator
from uuid import NAMESPACE_URL, uuid5
from app.services.table_projection import _FENCE, _row_cells, compact_markdown_tables

BUDGET = 2000
TITLE_HINT = 128
HEADER_HINT = 384
CURRENT_CHUNK_PROJECTION_VERSION = 1
VERSION = CURRENT_CHUNK_PROJECTION_VERSION


def digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def chunk_fragment_group_id(doc_id, generation_id, source_id, chunk_index):
    """Group parts of one original chunk without crossing publication/source scope."""
    return digest(json.dumps(
        ['chunk-fragment-group', doc_id, generation_id, source_id, chunk_index],
        ensure_ascii=False, separators=(',', ':'),
    ))


def measure(value):
    return max(
        len(value.encode("utf-8")), len(unicodedata.normalize("NFKC", value).encode("utf-8"))
    )


def clip(value, budget):
    if measure(value) <= budget:
        return value
    marker = " [continued]"
    low, high = 0, min(len(value), budget)
    while low < high:
        middle = (low + high + 1) // 2
        if measure(value[:middle] + marker) <= budget:
            low = middle
        else:
            high = middle - 1
    return value[:low] + marker


@dataclass(frozen=True)
class ChunkIndexFragment:
    ordinal: int
    field: str
    start: int
    end: int
    covered_text: str
    text: str
    text_hash: str
    identity: str
    continuation: bool
    title_hash: str
    source_hash: str
    projection_hash: str

    @property
    def descriptor(self) -> dict | None:
        if self.field == "legacy":
            return None
        return {
            "version": VERSION,
            "ordinal": self.ordinal,
            "field": self.field,
            "start": self.start,
            "end": self.end,
            "continuation": self.continuation,
            "title_hash": self.title_hash,
            "source_hash": self.source_hash,
            "projection_hash": self.projection_hash,
            "text_hash": self.text_hash,
        }


@dataclass(frozen=True)
class ChunkIndexProjection:
    title: str
    body: str
    source_hash: str
    title_hash: str
    projection_hash: str
    legacy: bool
    headers: tuple

    def fragments(self) -> Iterator[ChunkIndexFragment]:
        if self.legacy:
            text = self.title + "\n" + self.body if self.title else self.body
            yield ChunkIndexFragment(
                0,
                "legacy",
                0,
                len(self.body),
                self.body,
                text,
                digest(text),
                digest(json.dumps([VERSION, self.title_hash, self.source_hash, "legacy"])),
                False,
                self.title_hash,
                self.source_hash,
                self.projection_hash,
            )
            return
        ordinal = 0
        for field, value in [("title", self.title), ("body", self.body)]:
            start = 0
            for_header = 0
            while start < len(value):
                if field == "title":
                    prefix = "Title fragment:\n"
                else:
                    prefix = (
                        ("Section: " + clip(self.title, TITLE_HINT) + "\n") if self.title else ""
                    )
                    while for_header < len(self.headers) and self.headers[for_header][1] <= start:
                        for_header += 1
                    if for_header < len(self.headers):
                        left, right, hint = self.headers[for_header]
                        if left <= start < right:
                            prefix += "Table header: " + hint + "\n"
                    prefix += "Source fragment:\n"
                room = BUDGET - measure(prefix)
                low, high = 0, min(len(value) - start, room)
                while low < high:
                    middle = (low + high + 1) // 2
                    if measure(prefix + value[start : start + middle]) <= BUDGET:
                        low = middle
                    else:
                        high = middle - 1
                if low == 0:
                    raise ValueError("No code point fits provider budget")
                end = start + low
                if end < len(value):
                    newline = value.rfind("\n", start, end)
                    if newline >= start:
                        end = newline + 1
                covered = value[start:end]
                text = prefix + covered
                continuation = (start > 0 and value[start - 1] != "\n") or (
                    end < len(value) and value[end - 1] != "\n"
                )
                text_hash = digest(text)
                identity = digest(
                    json.dumps(
                        [
                            VERSION,
                            self.title_hash,
                            self.source_hash,
                            self.projection_hash,
                            ordinal,
                            field,
                            start,
                            end,
                            text_hash,
                        ],
                        separators=(",", ":"),
                    )
                )
                yield ChunkIndexFragment(
                    ordinal,
                    field,
                    start,
                    end,
                    covered,
                    text,
                    text_hash,
                    identity,
                    continuation,
                    self.title_hash,
                    self.source_hash,
                    self.projection_hash,
                )
                ordinal += 1
                start = end


def header_spans(body):
    """Repeat context only for unambiguous tables outside fenced/indented code."""
    lines = body.splitlines(keepends=True)
    offsets = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    spans = []
    i = 0
    fence = ""
    while i < len(lines):
        line = lines[i]
        match = _FENCE.match(line)
        if match:
            run = match.group(1)
            if not fence:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not line[match.end():].strip():
                fence = ""
            i += 1
            continue
        if not fence and not line.startswith(("    ", "\t")) and i + 1 < len(lines):
            header = _row_cells(line)
            separator = _row_cells(lines[i + 1])
            if (
                header
                and separator
                and len(header) == len(separator)
                and all(re.fullmatch(r":?-+:?", cell.strip()) for cell in separator)
            ):
                end = i + 2
                valid = True
                while end < len(lines) and lines[end].lstrip().startswith("|"):
                    cells = _row_cells(lines[end])
                    if lines[end].startswith(("    ", "\t")) or cells is None or len(cells) != len(header):
                        valid = False
                    end += 1
                if end < len(lines) and lines[end].strip() and not lines[end].lstrip().startswith("#") and not _FENCE.match(lines[end]):
                    valid = False
                if valid and end > i + 2:
                    spans.append(
                        (offsets[i], offsets[end] if end < len(lines) else len(body),
                         clip(line.rstrip("\r\n"), HEADER_HINT))
                    )
                i = end
                continue
        i += 1
    return tuple(spans)


def build_chunk_index_projection(title: str, content: str, *, version: int) -> ChunkIndexProjection:
    if type(version) is not int or version != CURRENT_CHUNK_PROJECTION_VERSION:
        raise ValueError("Unsupported chunk projection version")
    if not isinstance(title, str) or not isinstance(content, str):
        raise TypeError("Chunk title and content must be strings")
    body = compact_markdown_tables(content)
    raw = title + "\n" + content if title else content
    legacy = body == content and measure(raw) <= BUDGET
    return ChunkIndexProjection(
        title,
        body,
        digest(content),
        digest(title),
        digest(body),
        legacy,
        () if legacy else header_spans(body),
    )


def iter_chunk_index_fragments(
    title: str, content: str, *, version: int
) -> Iterator[ChunkIndexFragment]:
    """Retain one projection and yield bounded fragments without collecting them."""
    return build_chunk_index_projection(title, content, version=version).fragments()


def chunk_fragment_point_id(
    doc_id: str,
    chunk_index: int,
    generation_id: str,
    source_id: str | None,
    fragment: ChunkIndexFragment,
) -> str:
    """Bind a physical part to its original document, source and generation."""
    if not doc_id or not generation_id or type(chunk_index) is not int or chunk_index < 0:
        raise ValueError("Invalid chunk identity")
    if source_id is not None and (not isinstance(source_id, str) or not source_id):
        raise ValueError("Invalid source identity")
    if fragment.field == "legacy":
        # Preserve the original UUID contract without importing the vector service.
        return str(
            uuid5(NAMESPACE_URL, f"okf:generation:{generation_id}:chunk:{doc_id}:{chunk_index}")
        )
    identity = json.dumps(
        [doc_id, generation_id, source_id, chunk_index, VERSION, fragment.identity],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return str(uuid5(NAMESPACE_URL, "okf:chunk-fragment:" + identity))


def chunk_fragment_point_id_from_descriptor(
    doc_id: str, chunk_index: int, generation_id: str, source_id: str | None, descriptor: dict
) -> str:
    """Validate immutable descriptor fields before binding its physical identity."""
    fields = {
        "version",
        "ordinal",
        "field",
        "start",
        "end",
        "continuation",
        "title_hash",
        "source_hash",
        "projection_hash",
        "text_hash",
    }
    if not isinstance(descriptor, dict) or set(descriptor) != fields:
        raise ValueError("Invalid fragment descriptor")
    if type(descriptor["version"]) is not int or descriptor["version"] != VERSION:
        raise ValueError("Unsupported chunk projection version")
    if descriptor["field"] not in {"title", "body"} or type(descriptor["continuation"]) is not bool:
        raise ValueError("Invalid fragment field")
    for field in ["ordinal", "start", "end"]:
        if type(descriptor[field]) is not int or descriptor[field] < 0:
            raise ValueError("Invalid fragment range")
    if descriptor["end"] <= descriptor["start"]:
        raise ValueError("Empty fragment range")
    for field in ["title_hash", "source_hash", "projection_hash", "text_hash"]:
        if not isinstance(descriptor[field], str) or not re.fullmatch(
            r"[0-9a-f]{64}", descriptor[field]
        ):
            raise ValueError("Invalid fragment hash")
    identity = digest(
        json.dumps(
            [
                VERSION,
                descriptor["title_hash"],
                descriptor["source_hash"],
                descriptor["projection_hash"],
                descriptor["ordinal"],
                descriptor["field"],
                descriptor["start"],
                descriptor["end"],
                descriptor["text_hash"],
            ],
            separators=(",", ":"),
        )
    )
    fragment = ChunkIndexFragment(
        descriptor["ordinal"],
        descriptor["field"],
        descriptor["start"],
        descriptor["end"],
        "",
        "",
        descriptor["text_hash"],
        identity,
        descriptor["continuation"],
        descriptor["title_hash"],
        descriptor["source_hash"],
        descriptor["projection_hash"],
    )
    return chunk_fragment_point_id(doc_id, chunk_index, generation_id, source_id, fragment)
