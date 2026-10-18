"""Cross-domain policy contract; each service keeps its own storage and guards."""
from datetime import timedelta

import pytest

from app.db.models import Document
from app.db.session import session_scope
from tests.test_reference_edit_lock import setup


def test_scope_and_reference_share_deadlines_without_heartbeat_activity(monkeypatch):
    from app.services import processing_scope_edit_lock as scope
    from app.services.processing_scope import ProcessingScopeConflict
    from app.services.reference_concurrency import ReferenceError

    reference, key, now, actor, _ = setup(monkeypatch)
    monkeypatch.setattr(scope, "_now", lambda: now[0])
    with session_scope() as session:
        session.add(Document(id="policy-doc", filename="policy.docx", status="done"))
    with session_scope() as session:
        left = reference.acquire_edit_lock(session, key, actor, "a" * 64)
        right = scope.acquire_edit_lock(session, "policy-doc", actor.user_id, actor.username, "b" * 64)
    for _ in range(10):
        for field in ("expires_at", "idle_expires_at", "server_now"):
            assert left[field] == right[field]
        now[0] += timedelta(seconds=60)
        if _ == 9:
            break
        with session_scope() as session:
            left = reference.renew_edit_lock(session, key, actor, "a" * 64, left["lease_epoch"], 0)
            right = scope.renew_edit_lock(session, "policy-doc", actor.user_id, "b" * 64, activity_sequence=0)
    with pytest.raises(ReferenceError):
        with session_scope() as session:
            reference.renew_edit_lock(session, key, actor, "a" * 64, left["lease_epoch"], 1)
    with pytest.raises(ProcessingScopeConflict):
        with session_scope() as session:
            scope.renew_edit_lock(session, "policy-doc", actor.user_id, "b" * 64, activity_sequence=1)
