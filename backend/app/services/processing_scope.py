"""Durable selection and immutable generation policy. Callers own transactions."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
import uuid

from sqlalchemy import select

from app.db.models import DocumentGeneration, DocumentGenerationState, DocumentProcessingReview
from app.services.generation_store import lock_document_write
from docparser.attachment_assessment import recommend_scope
from docparser.attachment_policy import (
    AssessmentLimits,
    AttachmentInventory,
    canonical_json,
    inventory_traversal_limits,
    inventory_node_depth,
)


class ProcessingScopeConflict(ValueError):
    def __init__(
        self,
        message="Состав обработки изменился. Обновите данные.",
        code="processing_scope_conflict",
    ):
        super().__init__(message)
        self.code = code


def assessment_limits(settings):
    names = (
        "rows",
        "cells",
        "text_bytes",
        "expanded_bytes",
        "file_bytes",
        "total_rows",
        "total_cells",
        "total_text_bytes",
        "total_file_bytes",
    )
    return AssessmentLimits(
        **{name: getattr(settings, "attachment_review_" + name) for name in names},
        timeout_seconds=settings.attachment_preflight_timeout_seconds,
        scan_bytes=settings.attachment_preflight_scan_bytes,
    )


def selection_hash(source_hash, parser_version, traversal, decisions):
    value = {
        "source_file_hash": source_hash,
        "parser_version": parser_version,
        "traversal_limits": traversal,
        "decisions": sorted((d["source_key"], d["effective_included"]) for d in decisions),
    }
    return hashlib.sha256(canonical_json(value)).hexdigest()


def automatic_snapshot(inventory: AttachmentInventory, limits: AssessmentLimits | None = None):
    limits = limits or AssessmentLimits()
    decisions = [asdict(d) for d in recommend_scope(inventory, limits).decisions]
    inventory_data = inventory.to_dict()
    inventory_data["recommendations"] = json.loads(canonical_json(decisions))
    traversal = asdict(inventory_traversal_limits(inventory))
    return {
        "schema_version": 1,
        "source_file_hash": inventory.source_file_hash,
        "parser_version": inventory.parser_version,
        "assessor_version": inventory.assessor_version,
        "inventory": inventory_data,
        "policy": asdict(limits),
        "traversal_limits": traversal,
        "decisions": decisions,
        "selection_hash": selection_hash(
            inventory.source_file_hash, inventory.parser_version, traversal, decisions
        ),
    }


def create_review(session, doc_id, inventory, *, base_generation_id, actor, limits=None):
    if not lock_document_write(session, doc_id, allow_deleted=False):
        raise ProcessingScopeConflict("Документ недоступен.")
    from app.db.models import Document

    document = session.get(Document, doc_id, populate_existing=True)
    if document.status in {"uploaded", "queued", "processing", "splitting", "indexing"}:
        raise ProcessingScopeConflict("Дождитесь завершения приёма или обработки документа.")
    if not re.fullmatch(r"[0-9a-f]{64}", inventory.source_file_hash):
        raise ProcessingScopeConflict("Отсутствует контрольная сумма исходного файла.")
    existing = session.get(DocumentProcessingReview, doc_id, populate_existing=True)
    if existing and existing.state not in {"published", "canceled"}:
        raise ProcessingScopeConflict("Состав уже открыт для выбора. Обновите данные.")
    state = session.get(DocumentGenerationState, doc_id, populate_existing=True)
    if state and state.candidate_generation_id:
        raise ProcessingScopeConflict("Дождитесь завершения текущей обработки.")
    if (state.active_generation_id if state else None) != base_generation_id:
        raise ProcessingScopeConflict()
    if existing:
        session.delete(existing)
        session.flush()
    scope = automatic_snapshot(inventory, limits)
    row = DocumentProcessingReview(
        doc_id=doc_id,
        id=uuid.uuid4().hex,
        revision=1,
        state="draft",
        source_file_hash=inventory.source_file_hash,
        parser_version=inventory.parser_version,
        assessor_version=inventory.assessor_version,
        policy_json=scope["policy"],
        inventory_json=scope["inventory"],
        selection_json=scope["decisions"],
        selection_hash=scope["selection_hash"],
        base_generation_id=base_generation_id,
        created_by=actor,
    )
    row.summary_json = scope_summary(scope)
    session.add(row)
    session.flush()
    return row


def locked_review(session, doc_id, review_id, revision=None):
    if not lock_document_write(session, doc_id, allow_deleted=False):
        raise ProcessingScopeConflict("Документ недоступен.")
    row = session.get(DocumentProcessingReview, doc_id, populate_existing=True)
    if not row or row.id != review_id or (revision is not None and row.revision != revision):
        raise ProcessingScopeConflict()
    return row


def save_review_selection(
    session, doc_id, review_id, expected_revision, selected_source_keys, *, actor
):
    row = locked_review(session, doc_id, review_id, expected_revision)
    if row.state not in {"draft", "awaiting_similar"}:
        raise ProcessingScopeConflict("Выбор уже подтверждён или проверяется.")
    inventory = AttachmentInventory.from_dict(row.inventory_json)
    nodes = {n.source_key: n for n in inventory.nodes}
    if not selected_source_keys.issubset(nodes):
        raise ProcessingScopeConflict("Выбрано неизвестное вложение.")
    for key in selected_source_keys:
        node = nodes[key]
        depth = inventory_node_depth(row.inventory_json['nodes'], key)
        if depth is None:
            raise ProcessingScopeConflict()
        if node.status == 'skipped_depth' or depth > inventory_traversal_limits(inventory).max_depth:
            raise ProcessingScopeConflict('Достигнут предел вложенности. Это вложение нельзя включить в обработку.',
                                          code='attachment_depth_limit')
        if not node.supported or not node.children_complete or node.assessment == "unknown":
            raise ProcessingScopeConflict(
                "Сначала раскройте состав вложения.", code="processing_scope_changed"
            )
        if node.parent_key != "root" and node.parent_key not in selected_source_keys:
            raise ProcessingScopeConflict("Включите содержащий вложение документ.")
    decisions = []
    for node in inventory.nodes:
        selected = node.source_key in selected_source_keys
        reason = "small" if selected else "user_excluded"
        if not node.supported:
            reason = "unsupported"
        elif (
            not selected
            and node.parent_key != "root"
            and node.parent_key not in selected_source_keys
        ):
            reason = "ancestor_excluded"
        decisions.append(
            {
                "source_key": node.source_key,
                "selected": selected,
                "effective_included": selected,
                "reason": reason,
                "origin": "user",
                "source_id": node.source_id,
            }
        )
    row.selection_json = decisions
    row.selection_hash = selection_hash(
        row.source_file_hash, row.parser_version, asdict(inventory_traversal_limits(inventory)), decisions
    )
    row.revision += 1
    row.state = "draft"
    row.confirmed_by = actor
    row.similarity_confirmation_json = None
    row.summary_json = scope_summary(snapshot_from_review(row))
    session.flush()
    return row


def eligible_existing_selection(inventory, selected):
    nodes = {n.source_key: n for n in inventory.nodes}
    result = set()
    for key in selected:
        current = nodes.get(key)
        seen = set()
        while current is not None:
            if (
                current.source_key not in selected
                or current.source_key in seen
                or not current.supported
                or not current.children_complete
                or current.assessment == "unknown"
            ):
                break
            seen.add(current.source_key)
            if current.parent_key == "root":
                result.add(key)
                break
            current = nodes.get(current.parent_key)
    return frozenset(result)


def snapshot_from_review(review):
    return json.loads(
        canonical_json(
            {
                "schema_version": 1,
                "source_file_hash": review.source_file_hash,
                "parser_version": review.parser_version,
                "assessor_version": review.assessor_version,
                "inventory": review.inventory_json,
                "policy": review.policy_json,
                "traversal_limits": asdict(inventory_traversal_limits(review.inventory_json)),
                "decisions": review.selection_json,
                "selection_hash": review.selection_hash,
                "review_id": review.id,
                "review_revision": review.revision,
                "confirmed_by": review.confirmed_by,
            }
        )
    )


def scope_for_generation(session, doc_id, generation_id):
    if not generation_id:
        return None
    generation = session.scalar(
        select(DocumentGeneration).where(
            DocumentGeneration.id == generation_id, DocumentGeneration.doc_id == doc_id
        )
    )
    if generation is None:
        raise ProcessingScopeConflict("Версия документа недоступна.")
    scope = generation.processing_scope_json
    if scope is not None and scope.get("schema_version") != 1:
        raise ProcessingScopeConflict("Версия состава обработки не поддерживается.")
    return json.loads(canonical_json(scope)) if scope is not None else None


def normalize_scope_summary(summary):
    """Upgrade compact legacy counters without loading the source inventory."""
    if not summary or summary.get('total') is None or (summary.get('counts_version') or 0) >= 2:
        return summary
    return {**summary, 'counts_version': 2,
            'total': max(0, summary['total'] - (summary.get('unsupported') or 0))}


def scope_summary(snapshot, generation_id=None):
    if snapshot is None:
        return {
            "mode": "legacy_all",
            "generation_id": generation_id,
            "total": None,
            "included": None,
            "excluded": 0,
            "unsupported": None,
            "count_complete": False,
            "selection_hash": None,
            "recommended_excluded_count": None,
        }
    decisions = snapshot["decisions"]
    nodes = {n['source_key']: n for n in snapshot['inventory'].get('nodes', [])}

    def supported(decision):
        node = nodes.get(decision['source_key'])
        return node.get('supported', True) is not False if node is not None else decision.get('reason') != 'unsupported'

    supported_decisions = [d for d in decisions if supported(d)]
    unsupported = len(decisions) - len(supported_decisions)
    excluded = sum(not d["effective_included"] for d in supported_decisions)
    recommendations = snapshot["inventory"].get("recommendations")
    return {
        "mode": "selected" if excluded else "all",
        "counts_version": 2,
        "generation_id": generation_id,
        "total": len(supported_decisions),
        "included": sum(d["effective_included"] for d in supported_decisions),
        "excluded": excluded,
        "unsupported": unsupported,
        "count_complete": snapshot["inventory"]["complete"],
        "selection_hash": snapshot["selection_hash"],
        "recommended_excluded_count": sum(
            not d["effective_included"] for d in recommendations if supported(d)
        ) if recommendations is not None else None,
        "chunk_counts": snapshot.get("chunk_counts"),
    }


def assert_prepared_scope(snapshot, sources, chunks, concepts, table_source_ids=()):
    if snapshot is None:
        return
    allowed = {d["source_key"] for d in snapshot["decisions"] if d["effective_included"]}
    included_ids = {"root"}
    for source in sources or []:
        metadata = source.get("metadata") or {}
        key = metadata.get("source_key")
        if key in allowed:
            included_ids.add(source["source_id"])
        elif (
            metadata.get("processing_reason") == "inline_body"
            and source.get("parent_source_id") in included_ids
        ):
            included_ids.add(source["source_id"])
    content_ids = [item.get("source_id") or "root" for item in chunks]
    content_ids.extend((item.get("metadata") or {}).get("source_id") or "root" for item in concepts)
    content_ids.extend(table_source_ids)
    if any(source_id not in included_ids for source_id in content_ids):
        raise ProcessingScopeConflict(
            "Подготовленные данные содержат исключённый источник.",
            code="processing_scope_integrity",
        )


def review_summary(review):
    return {
        "id": review.id,
        "revision": review.revision,
        "state": review.state,
        "requires_action": review.state in {"draft", "awaiting_similar"},
        "summary": normalize_scope_summary(review.summary_json) or scope_summary(snapshot_from_review(review)),
    }


def refresh_review_inventory(session, doc_id, review_id, revision, inventory, *, actor):
    row = locked_review(session, doc_id, review_id, revision)
    if (
        row.state not in {"draft", "awaiting_similar"}
        or row.source_file_hash != inventory.source_file_hash
        or inventory_traversal_limits(row.inventory_json) != inventory_traversal_limits(inventory)
    ):
        raise ProcessingScopeConflict()
    old = {d["source_key"]: d for d in row.selection_json}
    decisions = [
        asdict(d) for d in recommend_scope(inventory, AssessmentLimits(**row.policy_json)).decisions
    ]
    inventory.recommendations = json.loads(canonical_json(decisions))
    for decision in decisions:
        previous = old.get(decision["source_key"])
        if previous is not None and previous.get("origin") == "user":
            decision.update(previous)
        elif previous is None:
            decision.update(selected=False, effective_included=False, reason="new_source")
    nodes = {n.source_key: n for n in inventory.nodes}
    by_key = {d["source_key"]: d for d in decisions}
    for decision in decisions:
        node = nodes[decision["source_key"]]
        if not node.supported or node.assessment == "unknown" or not node.children_complete:
            decision.update(selected=False, effective_included=False)
        if node.status == 'skipped_depth':
            decision['reason'] = 'depth_limit'
        parent = node.parent_key
        while parent in nodes:
            if not by_key[parent]["effective_included"]:
                decision["effective_included"] = False
            parent = nodes[parent].parent_key
    row.inventory_json = inventory.to_dict()
    row.selection_json = decisions
    row.selection_hash = selection_hash(
        row.source_file_hash, row.parser_version, asdict(inventory_traversal_limits(inventory)), decisions
    )
    row.revision += 1
    row.state = "draft"
    row.similarity_confirmation_json = None
    row.summary_json = scope_summary(snapshot_from_review(row))
    append_scope_audit(session, doc_id, actor, "document_processing_scope_updated", row)
    return row


def recover_processing_reviews(settings, *, force=False):
    """Release expired admission leases; waiting reviews never start workers."""
    from app.db.session import session_scope
    with session_scope() as session:
        ids = list(session.scalars(
            select(DocumentProcessingReview.doc_id).where(
                DocumentProcessingReview.state.in_(["validating", "submitted"]),
                DocumentProcessingReview.generation_id.is_(None),
            )
        ))
    for doc_id in ids:
        with session_scope() as session:
            recover_review_admission(session, doc_id, settings, force=force)


def recover_review_admission(session, doc_id, settings, *, force=False):
    """Revoke an expired validator under the same lock used by its late writes."""
    from datetime import datetime, timezone, timedelta
    from app.db.models import Document
    if not lock_document_write(session, doc_id, allow_deleted=False):
        return False
    row = session.get(DocumentProcessingReview, doc_id, populate_existing=True)
    if not row or row.state not in {"validating", "submitted"} or row.generation_id is not None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(180, settings.parser_timeout_seconds + 60))
    started = row.validation_started_at
    if not force and started and (started.replace(tzinfo=timezone.utc) if started.tzinfo is None else started) > cutoff:
        return False
    row.state, row.validation_token = "draft", None
    row.similarity_confirmation_json = None
    row.revision += 1
    state = session.get(DocumentGenerationState, doc_id)
    if not state or not state.active_generation_id:
        session.get(Document, doc_id).status = "awaiting_attachments"
    return True


def append_scope_audit(session, doc_id, actor, action, review):
    from app.db.models import AuditLog

    session.add(
        AuditLog(
            username=actor,
            action_type=action,
            target_type="document",
            target_id=doc_id,
            new_value={
                "review_id": review.id,
                "revision": review.revision,
                "selection_hash": review.selection_hash,
            },
            meta={"request_id": review.request_id} if review.request_id else None,
        )
    )


def scope_availability(summary, pending, sources):
    if summary and summary.get("excluded", 0):
        return "available"
    view = normalize_scope_summary((pending or {}).get("summary") or summary)
    if view and view.get("count_complete"):
        return "available" if (view.get("total") or 0) > 0 else "none"
    from pathlib import PurePath
    choices = {".docx", ".xlsx", ".pdf", ".eml", ".msg", ".zip", ".7z", ".rar"}
    if any(s.get("source_id") != "root" and PurePath(s.get("display_name", "")).suffix.lower() in choices for s in sources):
        return "available"
    if sources:
        return "none"
    return "unknown"


def load_processing_scope_summaries(session, doc_ids):
    from sqlalchemy import func, or_
    from app.db.models import Document, DocumentSource, DocumentScopeChange
    any_source = select(func.count()).where(DocumentSource.doc_id == Document.id).scalar_subquery()
    selectable = select(func.count()).where(DocumentSource.doc_id == Document.id,
        DocumentSource.source_id != "root", or_(*(func.lower(DocumentSource.display_name).like("%" + ext)
        for ext in (".docx", ".xlsx", ".pdf", ".eml", ".msg", ".zip", ".7z", ".rar")))).scalar_subquery()
    operation = select(DocumentScopeChange.plan_json["preview"]["mode"].as_string()).where(
        DocumentScopeChange.doc_id == Document.id,
        DocumentScopeChange.state.in_(["admitted", "queued", "processing", "copying", "ready"])).limit(1).scalar_subquery()
    metadata = session.execute(select(Document.id, DocumentGenerationState.active_generation_id,
        any_source.label("has_sources"), selectable.label("choices"), operation.label("operation"))
        .outerjoin(DocumentGenerationState, Document.id == DocumentGenerationState.doc_id)
        .where(Document.id.in_(doc_ids))).all()
    states = {r.id: r.active_generation_id for r in metadata}
    summaries = dict(
        session.execute(
            select(DocumentGeneration.id, DocumentGeneration.processing_scope_summary_json).where(
                DocumentGeneration.id.in_([v for v in states.values() if v])
            )
        ).all()
    )
    reviews = {
        r.doc_id: r
        for r in session.execute(
            select(
                DocumentProcessingReview.doc_id,
                DocumentProcessingReview.id,
                DocumentProcessingReview.revision,
                DocumentProcessingReview.state,
                DocumentProcessingReview.summary_json,
            ).where(DocumentProcessingReview.doc_id.in_(doc_ids))
        )
    }
    result = {
        doc_id: {
            "processing_scope": normalize_scope_summary(summaries.get(states.get(doc_id)))
            or scope_summary(None, states[doc_id])
            if states.get(doc_id)
            else None,
            "processing_review": {
                "id": reviews[doc_id].id,
                "revision": reviews[doc_id].revision,
                "state": reviews[doc_id].state,
                "requires_action": reviews[doc_id].state in {"draft", "awaiting_similar"},
                "summary": normalize_scope_summary(reviews[doc_id].summary_json) or scope_summary(None),
            }
            if doc_id in reviews and reviews[doc_id].state not in {"published", "canceled"}
            else None,
        }
        for doc_id in doc_ids
    }
    metadata_by_id = {r.id: r for r in metadata}
    for doc_id, item in result.items():
        stored = metadata_by_id.get(doc_id)
        sources = ([{"source_id": "root/choice", "display_name": "choice.xlsx"}] if stored and stored.choices else
                   [{"source_id": "root"}] if stored and stored.has_sources else [])
        item["processing_scope_availability"] = scope_availability(item["processing_scope"], item["processing_review"], sources)
        kind = stored.operation if stored else None
        item["processing_update_kind"] = "exclude_only" if kind == "fast_exclude" else kind
    return result


def has_stored_processing_scope(doc_id):
    from app.db.session import session_scope

    with session_scope() as session:
        if session.get(DocumentProcessingReview, doc_id) is not None:
            return True
        state = session.get(DocumentGenerationState, doc_id)
        return bool(
            state
            and any(
                scope_for_generation(session, doc_id, key) is not None
                for key in (state.active_generation_id, state.candidate_generation_id)
                if key
            )
        )
