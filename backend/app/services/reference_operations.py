"""Offline rollout operation. Call only with every reference writer stopped."""
from sqlalchemy import func, select, update

from app.db.models import ReferenceResourceState
from app.services.reference_concurrency import bounded_reference_transaction


def invalidate_all_editor_generations(session, *, apply=False):
    model = ReferenceResourceState
    with bounded_reference_transaction(session):
        count = session.scalar(select(func.count()).select_from(model))
        if apply:
            session.execute(update(model).values(lease_epoch=model.lease_epoch + 1,
                owner_id=None, owner_name=None, token_hash=None, acquired_at=None,
                expires_at=None, idle_expires_at=None, activity_sequence=0))
            from app.services.audit import record_in_session
            record_in_session(session, action_type="reference_edit_generations_invalidated",
                username="offline-maintenance", target_type="reference",
                meta={"resource_count": count, "content_revisions_preserved": True})
        return {"resources": count, "applied": apply, "content_revisions_preserved": True}
