"""Actual independent API processes share chat ownership and cancellation."""
from multiprocessing import get_context
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text, select, func
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import ChatRun, ChatMessage
from app.db.session import configure_database, get_engine, session_scope
from app.models.schemas import ChatRequest, ChatAttemptCancelRequest
from tests.support.replica_postgres import disposable_postgres


@pytest.fixture
def postgres():
    with disposable_postgres() as url:
        yield url


def _settings(root):
    settings = Settings(_env_file=None, data_dir=root, chat_max_inflight=1, llm_profile="standard")
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    return settings


def _api_process(url, root, body, pipe, abort):
    import app.config
    settings = _settings(Path(root))
    app.config.get_settings = lambda: settings
    configure_database(url)
    from app.api import chat
    from app.services.llm_profiles import request_state
    from app.services.llm_scheduler import LLMCancelled
    chat.get_settings = lambda: settings
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    def answer(_body, _user, _settings, attempt_ref=None, **_kwargs):
        pipe.send(("accepted", attempt_ref.session_id, attempt_ref.attempt_id))
        while not abort.wait(0.05):
            if request_state()["cancel"].is_set():
                raise LLMCancelled()
        raise LLMCancelled()
    chat._answer = answer
    try:
        try:
            chat.chat(ChatRequest(**body), user)
        except LLMCancelled:
            pipe.send(("stopped",))
        else:
            pipe.send(("unexpected completion",))
    finally:
        get_engine().dispose()
        pipe.close()


def test_cancel_and_duplicate_admission_across_actual_api_processes(postgres, tmp_path, monkeypatch):
    from app.api import chat
    from app.api.errors import ApiError
    settings = _settings(tmp_path / "api-b")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    body = ChatRequest(query="Что содержит документ?", response_mode="full", attempt_id=str(uuid4()))
    def forbidden(*_args, **_kwargs):
        pytest.fail("A repeated request started a second answer")
    monkeypatch.setattr(chat, "_answer", forbidden)
    context = get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    abort = context.Event()
    process = context.Process(target=_api_process,
        args=(postgres, str(tmp_path / "api-a"), body.model_dump(mode="json"), sending, abort))
    process.start()
    sending.close()
    try:
        assert receiving.poll(30)
        accepted = receiving.recv()
        assert accepted[0] == "accepted"
        with pytest.raises(ApiError) as error:
            chat.chat(body, user)
        assert error.value.status_code == 409
        response = chat.cancel_chat_attempt(accepted[2], ChatAttemptCancelRequest(session_id=accepted[1]), user)
        assert response["status"] == "stopped"
        assert receiving.poll(15)
        assert receiving.recv() == ("stopped",)
    finally:
        abort.set()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
        receiving.close()
    assert process.exitcode == 0
    with session_scope() as session:
        row = session.query(ChatRun).one()
        assert row.status == "stopped" and row.cancel_requested
        assert session.query(ChatMessage).count() == 2
        assert session.get(ChatMessage, row.message_id).content == ""


def test_chat_migration_preserves_legacy_history_and_refuses_lost_run_identity(postgres, tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.services import chat_history
    from app.services.work.chat import admit_chat_run
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    legacy = chat_history.begin_attempt(None, user, "old question", str(uuid4()), "full")
    assert chat_history.finish_attempt(legacy, user, status="completed", answer="old answer")
    ChatRun.__table__.drop(get_engine())
    with session_scope() as session:
        schema = session.scalar(text("SELECT current_schema()"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=postgres))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl03_replica_coordination")
    command.upgrade(config, "rpl03a_chat_runs")
    with session_scope() as session:
        assert session.get(ChatMessage, legacy.message_id).content == "old answer"
        admit_chat_run(session, _settings(tmp_path), ChatRequest(query="new question", response_mode="full",
            attempt_id=str(uuid4()), session_id=legacy.session_id), user, owner_id="api-a")
    with pytest.raises(RuntimeError, match="chat runs"):
        command.downgrade(config, "rpl03_replica_coordination")


def _late_finish(url, root, accepted, pipe, go):
    import app.config
    from app.services import chat_history
    from app.services.work.chat import finish_chat_run, ChatRunOwnershipLost
    app.config.get_settings = lambda: _settings(Path(root))
    configure_database(url)
    original = chat_history._attempt_message
    def before_history_lock(*args, **kwargs):
        pipe.send("before_history_lock")
        return original(*args, **kwargs)
    chat_history._attempt_message = before_history_lock
    try:
        pipe.send("ready")
        if not go.wait(15):
            raise RuntimeError("Chat completion barrier timed out")
        try:
            with session_scope() as session:
                finish_chat_run(session, accepted, SimpleNamespace(user_id="actor"),
                    status="completed", answer="late answer")
        except ChatRunOwnershipLost:
            pipe.send("fenced")
        else:
            pipe.send("unexpected completion")
    finally:
        get_engine().dispose()
        pipe.close()


def test_expiry_during_a_history_lock_wait_fences_completion(postgres, tmp_path, monkeypatch):
    from datetime import timedelta
    import time
    from app.services.work.chat import admit_chat_run
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, ChatRequest(query="question", response_mode="full",
            attempt_id=str(uuid4())), user, owner_id="api-a")
    context = get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    go = context.Event()
    process = context.Process(target=_late_finish,
        args=(postgres, str(tmp_path), accepted, sending, go))
    process.start()
    sending.close()
    try:
        assert receiving.poll(25) and receiving.recv() == "ready"
        with Session(get_engine()) as held:
            held.scalar(select(ChatMessage).where(ChatMessage.id == accepted.ref.message_id).with_for_update())
            with session_scope() as session:
                expires = session.scalar(select(func.clock_timestamp())) + timedelta(seconds=1)
                session.execute(text("UPDATE chat_runs SET lease_until=:expires WHERE id=:id"),
                    {"id": accepted.run_id, "expires": expires})
                session.execute(text("UPDATE replica_capacity_slots SET expires_at=:expires, protected_until=:expires "
                    "WHERE resource='chat'"), {"expires": expires})
            go.set()
            assert receiving.poll(10) and receiving.recv() == "before_history_lock"
            deadline = time.monotonic() + 5
            while held.scalar(select(func.clock_timestamp())) <= expires:
                assert time.monotonic() < deadline
                time.sleep(0.02)
            held.rollback()
        assert receiving.poll(15)
        assert receiving.recv() == "fenced"
    finally:
        go.set()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
        receiving.close()
    assert process.exitcode == 0
    with session_scope() as session:
        assert session.get(ChatMessage, accepted.ref.message_id).content == ""
