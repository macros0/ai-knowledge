"""Suspend exact internal slug edges while preserving free text relations."""
from copy import deepcopy
import hashlib
import json


def _identity(row):
    fields = {key: row.get(key) for key in ("slug", "title", "content", "source_id", "chunk_index")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def reconcile_scope_relations(previous, active, suspended):
    old = {row["slug"]: row for row in previous}
    current = {row["slug"]: row for row in active}
    pending = deepcopy(suspended)
    for owner in active:
        kept = []
        for position, relation in enumerate(owner.get("relations") or []):
            if relation in old and relation not in current:
                pending.append({"owner_slug":owner["slug"], "owner_hash":_identity(owner),
                    "target_slug":relation, "target_hash":_identity(old[relation]), "position":position})
            else:
                kept.append(relation)
        if "relations" in owner:
            owner["relations"] = kept
    remaining = []
    for edge in sorted(pending, key=lambda item:(item["owner_slug"], item["position"])):
        owner, target = current.get(edge["owner_slug"]), current.get(edge["target_slug"])
        if owner and target and _identity(owner)==edge["owner_hash"] and _identity(target)==edge["target_hash"]:
            relations = owner.setdefault("relations", [])
            if edge["target_slug"] not in relations:
                relations.insert(min(edge["position"], len(relations)), edge["target_slug"])
        elif edge not in remaining:
            remaining.append(edge)
    return remaining
