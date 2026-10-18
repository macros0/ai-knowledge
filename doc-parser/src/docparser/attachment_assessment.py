"""Pure deterministic recommendations; size never means source obsolescence."""

from __future__ import annotations

from docparser.attachment_policy import (
    AssessmentLimits,
    AttachmentInventory,
    AttachmentNode,
    ScopeSelection,
    SourceDecision,
)


def node_is_heavy(node: AttachmentNode, limits: AssessmentLimits) -> bool:
    metrics = node.metrics
    container = node.kind in {"archive", "mail", "docx", "pdf", "xlsx"} and metrics.get(
        "has_children"
    )
    names = ("rows", "cells", "text_bytes", "expanded_bytes", "file_bytes")
    if node.kind == "archive" or (container and node.kind == "mail"):
        names = ("text_bytes",)
    return any((metrics.get(name) or 0) >= getattr(limits, name) for name in names)


def recommend_scope(inventory: AttachmentInventory, limits: AssessmentLimits) -> ScopeSelection:
    nodes = {node.source_key: node for node in inventory.nodes}
    reasons: dict[str, str] = {}
    for node in inventory.nodes:
        if node.status == 'skipped_depth':
            reasons[node.source_key] = 'depth_limit'
        elif not node.supported:
            reasons[node.source_key] = "unsupported"
        elif node.assessment == "unknown" or not node.children_complete:
            reasons[node.source_key] = "unknown"
        elif node.assessment == "heavy" or node_is_heavy(node, limits):
            reasons[node.source_key] = "heavy"

    def included(node):
        seen = set()
        while node:
            if node.source_key in reasons or node.source_key in seen:
                return False
            seen.add(node.source_key)
            node = nodes.get(node.parent_key)
        return True

    def metric(node, name):
        if name == "file_bytes" and (node.kind == "archive" or node.metrics.get("has_children")):
            return 0
        return node.metrics.get(name) or 0

    totals = ("rows", "cells", "text_bytes", "file_bytes")
    candidates = [node for node in inventory.nodes if included(node)]
    ranked = sorted(
        candidates,
        key=lambda n: (
            -max(metric(n, name) / getattr(limits, "total_" + name) for name in totals),
            n.source_key,
        ),
    )
    while any(
        sum(metric(n, name) for n in candidates) >= getattr(limits, "total_" + name)
        for name in totals
    ):
        node = next((n for n in ranked if n in candidates), None)
        if node is None:
            break
        reasons[node.source_key] = "aggregate_budget"
        candidates = [n for n in candidates if included(n)]

    decisions = []
    for node in inventory.nodes:
        effective = included(node)
        reason = reasons.get(node.source_key, "small" if effective else "ancestor_excluded")
        decisions.append(
            SourceDecision(node.source_key, effective, effective, reason, source_id=node.source_id)
        )
    return ScopeSelection(
        decisions,
        not inventory.complete
        or any(reason in {"heavy", "unknown", "aggregate_budget"} for reason in reasons.values()),
    )
