"""One accepted chat attempt owns one fenced history row across API instances."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.models import ChatMessage, ChatSession, ReplicaCapacitySlot
from app.db.session import session_scope
from app.models.schemas import ChatRequest
from app.services.work.chat import (admit_chat_run, cancel_chat_run, finish_chat_run,
    renew_chat_run, ChatRunConflict, ChatRunOwnershipLost, ChatCapacityExceeded)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, chat_max_inflight=1)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    return settings


@pytest.fixture
def user():
    return SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])


def request(**values):
    return ChatRequest(query="Что содержит документ?", response_mode="full",
        attempt_id=str(uuid4()), **values)


def test_duplicate_request_on_another_api_creates_no_second_history_or_lease(settings, user):
    body = request()
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, body, user, owner_id="api-a")
    with session_scope() as session:
        with pytest.raises(ChatRunConflict):
            admit_chat_run(session, settings, body, user, owner_id="api-b")
        assert session.query(ChatMessage).count() == 2
        assert session.query(ChatSession).count() == 1
        assert session.query(ReplicaCapacitySlot).filter(ReplicaCapacitySlot.owner_id.is_not(None)).count() == 1
        assert accepted.ref.message_id > 0


def test_cancel_on_another_api_fences_late_completion(settings, user):
    body = request()
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, body, user, owner_id="api-a")
    with session_scope() as session:
        assert cancel_chat_run(session, session_id=accepted.ref.session_id, attempt_id=body.attempt_id, user=user)
    with session_scope() as session:
        with pytest.raises(ChatRunOwnershipLost):
            finish_chat_run(session, accepted, user, status="completed", answer="late answer")
        message = session.get(ChatMessage, accepted.ref.message_id)
        assert message.content == ""
        assert message.retrieval_metadata["answer_attempt"]["status"] == "stopped"


def test_expired_run_cannot_renew_or_publish(settings, user):
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, request(), user, owner_id="api-a")
        session.execute(text("UPDATE chat_runs SET lease_until='2000-01-01' WHERE id=:id"), {"id": accepted.run_id})
    with session_scope() as session:
        with pytest.raises(ChatRunOwnershipLost):
            renew_chat_run(session, accepted)
        with pytest.raises(ChatRunOwnershipLost):
            finish_chat_run(session, accepted, user, status="completed", answer="late answer")


def test_admission_rollback_preserves_no_history_or_capacity(settings, user):
    with pytest.raises(RuntimeError), session_scope() as session:
        admit_chat_run(session, settings, request(), user, owner_id="api-a")
        raise RuntimeError("transaction failed")
    with session_scope() as session:
        assert session.query(ChatMessage).count() == 0
        assert session.query(ChatSession).count() == 0
        assert session.query(ReplicaCapacitySlot).filter(ReplicaCapacitySlot.owner_id.is_not(None)).count() == 0


def test_two_apis_share_chat_inflight_before_provider_admission(settings, user):
    with session_scope() as session:
        admit_chat_run(session, settings, request(), user, owner_id="api-a")
    with session_scope() as session:
        with pytest.raises(ChatCapacityExceeded):
            admit_chat_run(session, settings, request(), user, owner_id="api-b")
        assert session.query(ChatMessage).count() == 2


def test_completed_history_commits_with_run_and_cannot_be_completed_twice(settings, user):
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, request(), user, owner_id="api-a")
    with session_scope() as session:
        finish_chat_run(session, accepted, user, status="completed", answer="verified answer")
    with session_scope() as session:
        with pytest.raises(ChatRunOwnershipLost):
            finish_chat_run(session, accepted, user, status="completed", answer="duplicate")
        assert session.get(ChatMessage, accepted.ref.message_id).content == "verified answer"
        assert session.query(ReplicaCapacitySlot).filter(ReplicaCapacitySlot.owner_id.is_not(None)).count() == 0


def test_new_attempt_in_same_thread_cancels_old_owner_before_admission(settings, user):
    with session_scope() as session:
        previous = admit_chat_run(session, settings, request(), user, owner_id="api-a")
    with session_scope() as session:
        current = admit_chat_run(session, settings, request(session_id=previous.ref.session_id), user, owner_id="api-b")
        assert current.ref.session_id == previous.ref.session_id
        assert current.ref.message_id != previous.ref.message_id
    with session_scope() as session:
        with pytest.raises(ChatRunOwnershipLost):
            finish_chat_run(session, previous, user, status="completed", answer="old answer")
        assert session.get(ChatMessage, previous.ref.message_id).retrieval_metadata["answer_attempt"]["status"] == "stopped"


def test_completion_rollback_retains_the_claim_and_empty_history(settings, user):
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, request(), user, owner_id="api-a")
    with pytest.raises(RuntimeError), session_scope() as session:
        finish_chat_run(session, accepted, user, status="completed", answer="not committed")
        raise RuntimeError("audit unavailable")
    with session_scope() as session:
        assert session.get(ChatMessage, accepted.ref.message_id).content == ""
        assert session.query(ReplicaCapacitySlot).filter(ReplicaCapacitySlot.owner_id.is_not(None)).count() == 1
        finish_chat_run(session, accepted, user, status="completed", answer="committed")


def test_unowned_history_writer_cannot_bypass_a_managed_run(settings, user):
    from app.services import chat_history
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, request(), user, owner_id="api-a")
    unowned = chat_history.AttemptRef(accepted.ref.session_id, accepted.ref.message_id, accepted.ref.attempt_id)
    with pytest.raises(ChatRunOwnershipLost):
        chat_history.finish_attempt(unowned, user, status="completed", answer="unowned")
    with session_scope() as session:
        assert session.get(ChatMessage, accepted.ref.message_id).content == ""


def test_expired_owner_cannot_change_sources_or_scenario(settings, user):
    from app.services import chat_history
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, request(), user, owner_id="api-a")
        session.execute(text("UPDATE chat_runs SET lease_until='2000-01-01' WHERE id=:id"), {"id": accepted.run_id})
    with pytest.raises(ChatRunOwnershipLost):
        chat_history.save_attempt_sources(accepted.ref, user, [{"doc_id": "late"}])
    with pytest.raises(ChatRunOwnershipLost):
        chat_history.set_scenario_execution(accepted.ref, user, SimpleNamespace(model_dump=lambda **_: {"status": "late"}))


def test_legacy_finish_wrapper_commits_managed_status_and_capacity(settings, user):
    from app.services import chat_history
    from app.db.models import ChatRun
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, request(), user, owner_id="api-a")
    assert chat_history.finish_attempt(accepted.ref, user, status="completed", answer="owned")
    with session_scope() as session:
        assert session.get(ChatRun, accepted.run_id).status == "completed"
        assert session.query(ReplicaCapacitySlot).filter(ReplicaCapacitySlot.owner_id.is_not(None)).count() == 0


def test_deleting_history_cancels_its_managed_request_and_releases_chat_capacity(settings, user):
    from app.services import chat_history
    from app.db.models import ChatRun
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, request(), user, owner_id="api-a")
    assert chat_history.soft_delete_session(accepted.ref.session_id, user)
    with session_scope() as session:
        row = session.get(ChatRun, accepted.run_id)
        assert row.status == "stopped" and row.cancel_requested
        assert session.query(ReplicaCapacitySlot).filter(ReplicaCapacitySlot.owner_id.is_not(None)).count() == 0


def test_expired_request_is_interrupted_without_replaying_the_model(settings, user):
    from app.db.models import ChatRun
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue, claim
    from app.services.work.runtime import build_handlers
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, request(), user, owner_id="dead-api")
        session.execute(text("UPDATE chat_runs SET lease_until='2000-01-01' WHERE id=:id"), {"id": accepted.run_id})
        enqueue(session, WorkSpec("chat_run_reconcile", 1, {}, "expired-chat"))
        work = claim(session, owner_id="worker", supported={"chat_run_reconcile": {1}}, lease_seconds=120)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        row = session.get(ChatRun, accepted.run_id)
        assert row.status == "interrupted" and row.cancel_requested
        message = session.get(ChatMessage, accepted.ref.message_id)
        assert message.content == ""
        assert message.retrieval_metadata["answer_attempt"]["status"] == "stopped"
        assert session.query(ChatMessage).count() == 2


def test_chat_retention_removes_run_identity_with_the_owning_history(settings, user):
    from datetime import datetime, timedelta, timezone
    from app.db.models import ChatRun
    from app.services import chat_history
    from app.services.work.models import WorkSpec
    from app.services.work.store import enqueue, claim
    from app.services.work.runtime import build_handlers
    settings.chat_history_purge_enabled = True
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, request(), user, owner_id="api-a")
        finish_chat_run(session, accepted, user, status="completed", answer="stored answer")
    chat_history.soft_delete_session(accepted.ref.session_id, user)
    with session_scope() as session:
        session.get(ChatSession, accepted.ref.session_id).deleted_at = datetime.now(timezone.utc) - timedelta(
            days=settings.chat_history_retention_days + 1)
        enqueue(session, WorkSpec("chat_purge", 1, {}, "chat-retention"))
        work = claim(session, owner_id="worker", supported={"chat_purge": {1}}, lease_seconds=120)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.query(ChatRun).count() == 0
        assert session.query(ChatMessage).count() == 0
        assert session.query(ChatSession).count() == 0


def test_unmanaged_legacy_history_admission_is_closed_in_future_runtime(settings, user):
    from app.services import chat_history
    with pytest.raises(ChatRunOwnershipLost):
        chat_history.begin_attempt(None, user, "unowned", str(uuid4()), "full")
    with pytest.raises(ChatRunOwnershipLost):
        chat_history.store_turn(None, user, "unowned", "answer", [])
    with session_scope() as session:
        assert session.query(ChatMessage).count() == 0
