"""A document lease fences users AND tabs, independently of worker leases."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.db.models import Document, DocumentProcessingReview, DocumentScopeChange
from app.db.session import session_scope
from app.services.processing_scope import ProcessingScopeConflict


@pytest.fixture
def doc_id():
    with session_scope() as s:
        s.add(Document(id="a" * 16, filename="solution.docx", status="done"))
    return "a" * 16


def acquire(doc_id, owner="alice", token="1" * 64):
    from app.services.processing_scope_edit_lock import acquire_edit_lock
    with session_scope() as s:
        return acquire_edit_lock(s, doc_id, owner, owner, token)


def test_one_editor_and_one_tab_without_exposing_secret(doc_id):
    from app.services.processing_scope_edit_lock import edit_lock_status, require_edit_lock
    from app.db.models import DocumentScopeEditLock
    lease = acquire(doc_id)
    assert lease["state"] == "editing" and lease["owner"] == "alice"
    assert "token" not in repr(lease)
    for owner, token in [("bob", "2" * 64), ("alice", "2" * 64)]:
        with pytest.raises(ProcessingScopeConflict, match="редактирует"):
            acquire(doc_id, owner, token)
    with session_scope() as s:
        assert s.get(DocumentScopeEditLock, doc_id).token_hash != "1" * 64
        assert edit_lock_status(s, doc_id)["owner"] == "alice"
        require_edit_lock(s, doc_id, "alice", "1" * 64)
    for owner, token in [("bob", "1" * 64), ("alice", "2" * 64), ("alice", None)]:
        with pytest.raises(ProcessingScopeConflict):
            with session_scope() as s:
                require_edit_lock(s, doc_id, owner, token)


def test_expiration_and_release_do_not_release_a_new_editor(doc_id):
    from app.db.models import DocumentScopeEditLock
    from app.services.processing_scope_edit_lock import release_edit_lock, require_edit_lock
    acquire(doc_id)
    with session_scope() as s:
        s.get(DocumentScopeEditLock, doc_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ProcessingScopeConflict):
        with session_scope() as s:
            require_edit_lock(s, doc_id, "alice", "1" * 64)
    acquire(doc_id, "bob", "2" * 64)
    with session_scope() as s:
        release_edit_lock(s, doc_id, "alice", "1" * 64)
        require_edit_lock(s, doc_id, "bob", "2" * 64)
        release_edit_lock(s, doc_id, "bob", "2" * 64)
    assert acquire(doc_id)["state"] == "editing"


@pytest.mark.parametrize("state", ["admitted", "queued", "processing", "copying", "ready"])
def test_operation_blocks_editing_even_after_editor_and_worker_lease_expire(doc_id, state):
    acquire(doc_id)
    from app.db.models import DocumentScopeEditLock
    with session_scope() as s:
        s.get(DocumentScopeEditLock, doc_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.add(DocumentScopeChange(id="b" * 32, doc_id=doc_id, state=state,
                                 plan_json={}, lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    with pytest.raises(ProcessingScopeConflict) as error:
        acquire(doc_id, "bob", "2" * 64)
    assert error.value.code == "scope_change_in_progress"


def test_parallel_acquisition_has_exactly_one_winner(doc_id):
    def attempt(n):
        try:
            acquire(doc_id, str(n), str(n) * 64)
            return True
        except ProcessingScopeConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, [1, 2])) == 1


def test_full_regeneration_validation_blocks_new_editor(doc_id):
    with session_scope() as s:
        s.add(DocumentProcessingReview(doc_id=doc_id, id="c" * 32, state="validating",
            source_file_hash="d" * 64, parser_version="test", assessor_version="test",
            policy_json={}, inventory_json={}, selection_json=[], selection_hash="e" * 64,
            validation_started_at=datetime.now(timezone.utc)))
    with pytest.raises(ProcessingScopeConflict) as error:
        acquire(doc_id)
    assert error.value.code == "scope_change_in_progress"


def test_expired_validation_is_reclaimed_under_lock_by_new_editor(doc_id):
    with session_scope() as s:
        s.add(DocumentProcessingReview(doc_id=doc_id, id="c" * 32, revision=1, state="validating",
            source_file_hash="d" * 64, parser_version="test", assessor_version="test",
            policy_json={}, inventory_json={}, selection_json=[], selection_hash="e" * 64,
            validation_token="f" * 32, validation_started_at=datetime.now(timezone.utc)-timedelta(hours=1)))
    assert acquire(doc_id)["state"] == "editing"
    with session_scope() as s:
        review = s.get(DocumentProcessingReview, doc_id)
        assert review.state == "draft" and review.validation_token is None and review.revision == 2


def test_periodic_recovery_preserves_live_validator_then_revokes_expired_owner(doc_id):
    from types import SimpleNamespace
    from app.services.processing_scope import recover_processing_reviews
    with session_scope() as s:
        s.add(DocumentProcessingReview(doc_id=doc_id, id="c" * 32, revision=1, state="validating",
            source_file_hash="d" * 64, parser_version="test", assessor_version="test",
            policy_json={}, inventory_json={}, selection_json=[], selection_hash="e" * 64,
            validation_token="f" * 32, validation_started_at=datetime.now(timezone.utc)))
    recover_processing_reviews(SimpleNamespace(parser_timeout_seconds=60))
    with session_scope() as s:
        review = s.get(DocumentProcessingReview, doc_id)
        assert review.state == "validating" and review.validation_token == "f" * 32
        review.validation_started_at = datetime.now(timezone.utc)-timedelta(hours=1)
    recover_processing_reviews(SimpleNamespace(parser_timeout_seconds=60))
    with session_scope() as s:
        review = s.get(DocumentProcessingReview, doc_id)
        assert review.state == "draft" and review.validation_token is None and review.revision == 2


def test_heartbeats_without_activity_cannot_keep_forgotten_editor_locked(doc_id, monkeypatch):
    import app.services.processing_scope_edit_lock as locks
    start = datetime.now(timezone.utc)
    monkeypatch.setattr(locks, "_now", lambda:start)
    acquire(doc_id)
    for minute in range(1, 10):
        monkeypatch.setattr(locks, "_now", lambda minute=minute:start+timedelta(minutes=minute))
        with session_scope() as s:
            locks.renew_edit_lock(s, doc_id, "alice", "1"*64)
    monkeypatch.setattr(locks, "_now", lambda:start+timedelta(minutes=10))
    with pytest.raises(ProcessingScopeConflict):
        with session_scope() as s:
            locks.renew_edit_lock(s, doc_id, "alice", "1"*64)
    assert acquire(doc_id,"bob","2"*64)["owner"] == "bob"


def test_activity_sequence_extends_idle_deadline_but_heartbeat_replay_does_not(doc_id, monkeypatch):
    import app.services.processing_scope_edit_lock as locks
    start=datetime.now(timezone.utc)
    monkeypatch.setattr(locks,"_now",lambda:start)
    acquire(doc_id)
    for minute in range(1, 10):
        monkeypatch.setattr(locks,"_now",lambda minute=minute:start+timedelta(minutes=minute))
        with session_scope() as s:
            result=locks.renew_edit_lock(s,doc_id,"alice","1"*64,activity_sequence=1 if minute==9 else 0)
    assert result["idle_remaining_seconds"] == 600
    for minute in range(10,19):
        monkeypatch.setattr(locks,"_now",lambda minute=minute:start+timedelta(minutes=minute))
        with session_scope() as s:
            locks.renew_edit_lock(s,doc_id,"alice","1"*64,activity_sequence=1)
    monkeypatch.setattr(locks,"_now",lambda:start+timedelta(minutes=19))
    with pytest.raises(ProcessingScopeConflict):
        with session_scope() as s:
            locks.renew_edit_lock(s,doc_id,"alice","1"*64,activity_sequence=1)
