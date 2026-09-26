"""Exclude unpublished/retired points before Qdrant ranking.

Keep generation records until their points are removed. Canonical hydration
checks the active pointer again: publication can race with a Qdrant request.
"""
from qdrant_client.http import models as qm
from sqlalchemy import or_, select

from app.db.models import DocumentGeneration
from app.db.session import session_scope


def generation_exclusions() -> list:
    with session_scope() as session:
        rows = session.execute(
            select(
                DocumentGeneration.id, DocumentGeneration.doc_id,
                DocumentGeneration.phase, DocumentGeneration.legacy_cleanup_pending,
            ).where(or_(
                DocumentGeneration.phase != "active",
                DocumentGeneration.legacy_cleanup_pending.is_(True),
            ))
        ).all()
    hidden = [generation_id for generation_id, _, phase, _ in rows if phase != "active"]
    legacy = sorted({doc_id for _, doc_id, _, pending in rows if pending})
    conditions = []
    if hidden:
        conditions.append(qm.FieldCondition(key="generation_id", match=qm.MatchAny(any=hidden)))
    if legacy:
        conditions.append(qm.Filter(must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=legacy)),
            qm.IsEmptyCondition(is_empty=qm.PayloadField(key="generation_id")),
        ]))
    return conditions
