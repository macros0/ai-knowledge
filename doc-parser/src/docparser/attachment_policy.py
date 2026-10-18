"""Serializable contracts for bounded attachment inspection and processing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from docparser.attachment_limits import (
    DEFAULT_ATTACHMENT_MAX_DEPTH,
    validate_attachment_depth,
)
from docparser.source_model import PARSER_VERSION


@dataclass(frozen=True)
class AssessmentLimits:
    rows: int = 500
    cells: int = 20000
    text_bytes: int = 524288
    expanded_bytes: int = 8388608
    file_bytes: int = 10485760
    total_rows: int = 1000
    total_cells: int = 40000
    total_text_bytes: int = 1048576
    total_file_bytes: int = 20971520
    timeout_seconds: float = 15.0
    scan_bytes: int = 33554432


@dataclass(frozen=True)
class TraversalLimits:
    max_depth: int = DEFAULT_ATTACHMENT_MAX_DEPTH
    max_nodes: int = 1000
    max_payload_bytes: int = 50 * 1024 * 1024
    max_total_bytes: int = 200 * 1024 * 1024
    max_archive_entries: int = 10000
    max_compression_ratio: float = 1000.0

    def __post_init__(self):
        validate_attachment_depth(self.max_depth)


@dataclass
class AttachmentNode:
    source_key: str
    parent_key: str
    locator: str
    display_name: str
    kind: str
    supported: bool = True
    source_id: str | None = None
    metrics: dict[str, int | None] = field(default_factory=dict)
    precision: str = "exact"
    assessment: str = "small"
    children_complete: bool = True
    status: str = "available"
    saved_path: str | None = None
    content_fingerprint: str | None = None
    format: str | None = None
    title: str | None = None
    title_source: str | None = None
    display_label: str | None = None


@dataclass
class AttachmentInventory:
    source_file_hash: str
    nodes: list[AttachmentNode] = field(default_factory=list)
    schema_version: int = 1
    assessor_version: str = "attachment-assessment-v1"
    parser_version: str = PARSER_VERSION + "-scope-v1"
    complete: bool = True
    scan_bytes: int = 0
    elapsed_seconds: float = 0.0
    # None distinguishes legacy inventories from a captured automatic assessment.
    recommendations: list[dict] | None = None
    # Captured inspection limits; absent in old JSON inventories (default6).
    traversal_limits: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> AttachmentInventory:
        if value.get("schema_version") != 1:
            raise ValueError("Unsupported attachment inventory schema")
        data = dict(value)
        data["nodes"] = [AttachmentNode(**node) for node in data.get("nodes", [])]
        return cls(**data)


@dataclass(frozen=True)
class SourceDecision:
    source_key: str
    selected: bool
    effective_included: bool
    reason: str
    origin: str = "automatic"
    source_id: str | None = None


@dataclass
class ScopeSelection:
    decisions: list[SourceDecision]
    requires_review: bool


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def make_source_key(source_file_hash: str, locator_chain: tuple[str, ...]) -> str:
    return hashlib.sha256(canonical_json([source_file_hash, list(locator_chain)])).hexdigest()


def inventory_traversal_limits(inventory) -> TraversalLimits:
    value = inventory.get('traversal_limits') if isinstance(inventory, dict) else inventory.traversal_limits
    return TraversalLimits(**(value or {}))


def inventory_node_depth(nodes: list[dict], source_key: str) -> int | None:
    """Absolute depth from the uploaded root; invalid ancestry fails closed."""
    by_key = {n['source_key']: n for n in nodes}
    current, seen, depth = source_key, set(), 0
    while current != 'root':
        if current not in by_key or current in seen:
            return None
        seen.add(current)
        current = by_key[current].get('parent_key')
        depth += 1
    return depth


class ParserProcessingPolicy:
    """An immutable-on-the-wire allowlist; unknown child sources fail closed."""

    def __init__(self, snapshot: dict):
        if snapshot.get("schema_version") != 1:
            raise ValueError("Unsupported processing scope schema")
        self.snapshot = json.loads(canonical_json(snapshot))
        self.source_file_hash = snapshot["source_file_hash"]
        self.traversal = TraversalLimits(**snapshot.get("traversal_limits", {}))
        self._decisions = {item["source_key"]: item for item in snapshot["decisions"]}

    def is_included(self, source_key: str) -> bool:
        if source_key == "root":
            return True
        return self._decisions.get(source_key, {}).get("effective_included") is True

    def source_metadata(self, source_key: str) -> dict:
        decision = self._decisions.get(source_key, {})
        node = next(
            (
                n
                for n in self.snapshot.get("inventory", {}).get("nodes", [])
                if n["source_key"] == source_key
            ),
            {},
        )
        return {
            "processing_reason": decision.get("reason", "unassessed"),
            "processing_assessment": node.get("assessment"),
            "processing_origin": decision.get("origin"),
        }

    def to_dict(self) -> dict:
        return json.loads(canonical_json(self.snapshot))
