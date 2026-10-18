"""Short atomic tag batches bound to explicitly reviewed resource revisions."""
from collections import defaultdict
import hashlib
import json
import logging

from sqlalchemy import func, select

from app.db.models import Document, DocumentTag, Tag, TagTranslation
from app.db.session import session_scope
from app.services import audit
from app.services.reference_concurrency import ReferenceError, lock_reference_resources, bump_reference_revision
from app.services.reference_edit_lock import live
from app.services.reference_resources import ReferenceKey, require_access
from app.services.tag_registry import TagRegistry, _now

logger = logging.getLogger(__name__)


def preview_digest(candidates):
    content = sorted((item["tag_id"], item["expected_reference_revision"]) for item in candidates)
    return hashlib.sha256(json.dumps(content, separators=(",", ":")).encode()).hexdigest()


def cleanup_preview():
    candidates = [{"tag_id": item["id"], "expected_reference_revision": item["reference_revision"], "name": item["name"]}
                  for item in TagRegistry().all() if not item["count"]]
    batches = []
    for offset in range(0, len(candidates), 100):
        group = candidates[offset:offset + 100]
        batches.append({"candidates": group, "preview_digest": preview_digest(group)})
    return {"applied": False, "total": len(candidates), "batches": batches}


def _card(session, identifier, expected, actor):
    key = ReferenceKey("tag", str(identifier))
    require_access(key, actor)
    row = lock_reference_resources(session, [key])[key]
    tag = session.get(Tag, identifier, populate_existing=True)
    if tag is None or tag.deleted_at is not None:
        return key, row, tag, "skipped_deleted"
    if live(row):
        return key, row, tag, "skipped_locked"
    if row.revision != expected:
        return key, row, tag, "skipped_changed"
    return key, row, tag, None


def review_tag_items(items, actor):
    groups = defaultdict(list)
    for item in items:
        groups[item["tag_id"]].append(item)
    if not 1 <= len(groups) <= 100:
        raise ReferenceError("invalid_request", 422)
    for group in groups.values():
        if len({i["locale"] for i in group}) != len(group) or len({i["expected_reference_revision"] for i in group}) != 1:
            raise ReferenceError("invalid_request", 422)
    results, total = [], 0
    for identifier, group in groups.items():
        try:
            with session_scope() as session:
                key, state, tag, skipped = _card(session, identifier, group[0]["expected_reference_revision"], actor)
                count = 0
                if not skipped:
                    rows = session.execute(select(TagTranslation).where(TagTranslation.tag_id == identifier,
                        TagTranslation.locale.in_([i["locale"] for i in group]))).scalars().all()
                    for row in rows:
                        if row.is_machine_translated and not row.reviewed_by:
                            row.reviewed_by, row.translated_at = actor.username, _now()
                            count += 1
                    if count:
                        bump_reference_revision(state)
                        audit.record_in_session(session, action_type=audit.TAG_TRANSLATION_REVIEW,
                            user_id=actor.user_id, username=actor.username, target_type=audit.TARGET_TAG,
                            target_id=str(identifier), new_value={"locales": [i["locale"] for i in group],
                                "reviewed": count, "reference_revision": state.revision})
                result = {"tag_id": identifier, "name": tag.canonical_text if tag else None,
                    "status": skipped or ("reviewed" if count else "unchanged"), "reviewed": count,
                    "reference_revision": state.revision}
            total += count
            results.append(result)
        except ReferenceError as exc:
            if exc.status_code != 503:
                raise
            results.append({"tag_id": identifier, "status": "failed", "code": exc.code, "reviewed": 0})
        except Exception:
            logger.warning("Tag review transaction failed: tag_id=%d", identifier)
            results.append({"tag_id": identifier, "status": "failed", "reviewed": 0})
    return {"reviewed": total, "results": results}


def apply_cleanup(candidates, digest, actor):
    if len(candidates) > 100 or len({item["tag_id"] for item in candidates}) != len(candidates):
        raise ReferenceError("invalid_request", 422)
    if digest != preview_digest(candidates):
        raise ReferenceError("reference_preview_stale")
    results, deleted = [], []
    for item in candidates:
        identifier = item["tag_id"]
        try:
            with session_scope() as session:
                key, state, tag, skipped = _card(session, identifier, item["expected_reference_revision"], actor)
                if not skipped:
                    used = session.scalar(select(func.count()).select_from(DocumentTag)
                        .join(Document, Document.id == DocumentTag.doc_id)
                        .where(DocumentTag.tag_id == identifier, Document.deleted_at.is_(None)))
                    if used:
                        skipped = "skipped_used"
                    else:
                        tag.deleted_at = _now()
                        bump_reference_revision(state)
                        audit.record_in_session(session, action_type=audit.TAG_DELETE,
                            user_id=actor.user_id, username=actor.username, target_type=audit.TARGET_TAG,
                            target_id=tag.canonical_text, old_value={"name": tag.canonical_text})
                result = {"tag_id": identifier, "name": tag.canonical_text if tag else None,
                    "status": skipped or "deleted", "reference_revision": state.revision}
            results.append(result)
            if not skipped:
                deleted.append(result["name"])
        except ReferenceError as exc:
            if exc.status_code != 503:
                raise
            results.append({"tag_id": identifier, "status": "failed", "code": exc.code})
        except Exception:
            logger.warning("Tag cleanup transaction failed: tag_id=%d", identifier)
            results.append({"tag_id": identifier, "status": "failed"})
    return {"applied": True, "deleted": deleted, "total": len(deleted), "results": results}
