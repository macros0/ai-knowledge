"""Future HTTP chat paths must use the same durable execution boundary."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.db.models import ChatRun, ChatMessage
from app.db.session import session_scope
from app.models.schemas import ChatRequest, ChatResponse


@pytest.fixture
def api(monkeypatch, tmp_path):
    from app.api import chat
    settings = Settings(_env_file=None, data_dir=tmp_path, chat_max_inflight=1)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "_inflight_by_limit", {})
    return chat


@pytest.mark.parametrize("mode", [None, "full"])
def test_future_http_modes_commit_one_run_with_one_history_turn(api, monkeypatch, mode):
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    def answer(body, _user, _settings, attempt_ref=None, **_kwargs):
        return ChatResponse(query=body.query, answer="answer", sources=[],
            session_id=attempt_ref.session_id if attempt_ref else None, response_mode=body.response_mode)
    monkeypatch.setattr(api, "_answer", answer)
    body = ChatRequest(query="Что содержит документ?", response_mode=mode, attempt_id=str(uuid4()))
    result = api.chat(body, user)
    assert result.answer == "answer"
    with session_scope() as session:
        row = session.query(ChatRun).one()
        assert row.status == "completed"
        assert session.query(ChatMessage).count() == 2
        assert session.get(ChatMessage, row.message_id).content == "answer"


def test_cancel_from_another_api_interrupts_the_request_and_fences_history(api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.models.schemas import ChatAttemptCancelRequest
    from app.services.llm_profiles import request_state
    from app.services.llm_scheduler import LLMCancelled
    entered, abort = Event(), Event()
    refs = []
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    def answer(body, _user, _settings, attempt_ref=None, **_kwargs):
        refs.append(attempt_ref)
        entered.set()
        while not abort.wait(0.02):
            cancel = request_state().get("cancel")
            if cancel is not None and cancel.is_set():
                raise LLMCancelled()
        raise LLMCancelled()
    monkeypatch.setattr(api, "_answer", answer)
    body = ChatRequest(query="Что содержит документ?", response_mode="full", attempt_id=str(uuid4()))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(api.chat, body, user)
        try:
            assert entered.wait(5)
            ref = refs[0]
            response = api.cancel_chat_attempt(ref.attempt_id,
                ChatAttemptCancelRequest(session_id=ref.session_id), user)
            assert response["status"] == "stopped"
            with pytest.raises(LLMCancelled):
                future.result(timeout=5)
        finally:
            abort.set()
    with session_scope() as session:
        row = session.query(ChatRun).one()
        assert row.status == "stopped" and row.cancel_requested
        assert session.get(ChatMessage, row.message_id).content == ""


def test_preview_uses_the_same_global_chat_admission(api, monkeypatch):
    from app.api.errors import ApiError
    from app.models.answer_scenarios import ScenarioPreviewRequest
    from app.services.chat_execution import preview_answer
    from app.services.work.chat import admit_chat_run
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    with session_scope() as session:
        admit_chat_run(session, api.get_settings(), ChatRequest(query="existing question",
            response_mode="full", attempt_id=str(uuid4())), user, owner_id="api-a")
    monkeypatch.setattr("app.services.answer_scenarios.require_available", lambda *_args: None)
    monkeypatch.setattr(api, "_answer", lambda body, *_args, **_kwargs:
        ChatResponse(query=body.query, answer="preview", sources=[]))
    body = ScenarioPreviewRequest(draft={"name": "draft", "instructions": "Answer briefly"},
        request={"query": "preview question"})
    with pytest.raises(ApiError) as error:
        preview_answer(body, user)
    assert error.value.status_code == 429


def test_preview_cannot_return_after_its_capacity_ownership_expires(api, monkeypatch):
    from sqlalchemy import text
    from app.models.answer_scenarios import ScenarioPreviewRequest
    from app.services.chat_execution import preview_answer
    from app.services.llm_scheduler import LLMCancelled
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    monkeypatch.setattr("app.services.answer_scenarios.require_available", lambda *_args: None)
    def answer(body, *_args, **_kwargs):
        with session_scope() as session:
            session.execute(text("UPDATE replica_capacity_slots SET expires_at='2000-01-01' WHERE resource='chat'"))
        return ChatResponse(query=body.query, answer="late preview", sources=[])
    monkeypatch.setattr(api, "_answer", answer)
    body = ScenarioPreviewRequest(draft={"name": "draft", "instructions": "Answer briefly"},
        request={"query": "preview question"})
    with pytest.raises(LLMCancelled):
        preview_answer(body, user)


def test_stream_cancel_retains_only_the_owned_partial_draft(api, monkeypatch):
    import asyncio
    import json
    from threading import Event
    from app.models.schemas import ChatAttemptCancelRequest
    from app.services.chat_stream import stream_chat
    from app.services.llm_profiles import request_state
    from app.services.llm_scheduler import LLMCancelled
    entered, abort = Event(), Event()
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    monkeypatch.setattr("app.services.chat_stream.get_settings", api.get_settings)
    def answer(_body, _user, _settings, attempt_ref=None, **_kwargs):
        request_state()["on_text"]("partial answer")
        entered.set()
        while not abort.wait(0.02):
            if request_state()["cancel"].is_set():
                raise LLMCancelled()
        raise LLMCancelled()
    monkeypatch.setattr(api, "_answer", answer)
    body = ChatRequest(query="Что содержит документ?", response_mode="full", attempt_id=str(uuid4()))
    async def collect():
        return [json.loads(line) async for line in stream_chat(lambda: api.chat(body, user),
            independent_calls=True, current_user=user)]
    async def exercise():
        task = asyncio.create_task(collect())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with session_scope() as session:
                row = session.query(ChatRun).one()
                session_id, attempt_id = row.session_id, row.attempt_id
            await asyncio.to_thread(api.cancel_chat_attempt, attempt_id,
                ChatAttemptCancelRequest(session_id=session_id), user)
            return await asyncio.wait_for(task, timeout=8)
        finally:
            abort.set()
            if not task.done():
                await asyncio.wait_for(task, timeout=5)
    events = asyncio.run(exercise())
    assert not any(event["type"] == "result" for event in events)
    assert "run_owner" not in json.dumps(events)
    with session_scope() as session:
        row = session.query(ChatRun).one()
        message = session.get(ChatMessage, row.message_id)
        assert row.status == "stopped"
        assert message.content == "partial answer"
        assert message.retrieval_metadata["answer_attempt"]["retained_draft"]


def test_expired_admission_does_not_start_answer_processing(api, monkeypatch):
    from sqlalchemy import text
    from app.services.work import chat_runtime
    from app.services.llm_scheduler import LLMCancelled
    original = chat_runtime.admit_chat_run
    calls = []
    def expired(session, *args, **kwargs):
        accepted = original(session, *args, **kwargs)
        session.execute(text("UPDATE chat_runs SET lease_until='2000-01-01' WHERE id=:id"), {"id": accepted.run_id})
        session.execute(text("UPDATE replica_capacity_slots SET expires_at='2000-01-01' WHERE resource='chat'"))
        return accepted
    monkeypatch.setattr(chat_runtime, "admit_chat_run", expired)
    def answer(body, *_args, **_kwargs):
        calls.append(True)
        return ChatResponse(query=body.query, answer="unowned", sources=[])
    monkeypatch.setattr(api, "_answer", answer)
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    with pytest.raises(LLMCancelled):
        api.chat(ChatRequest(query="question", response_mode="full", attempt_id=str(uuid4())), user)
    assert calls == []


def test_valid_managed_request_calls_model_and_commits_owned_history(api, monkeypatch):
    from app.db.models import ReplicaCapacitySlot
    from app.services.llm_client import LLMClient
    from tests.test_llm_client import _stream_response_finished
    monkeypatch.setattr("app.services.llm_client.get_settings", api.get_settings)
    monkeypatch.setattr("app.services.llm_client._interactive_semaphore", None)
    calls = []
    def provider(**_kwargs):
        calls.append(True)
        return _stream_response_finished("model answer")
    monkeypatch.setattr("litellm.completion", provider)
    def answer(body, _user, settings, attempt_ref=None, **_kwargs):
        text = LLMClient(interactive=True, settings=settings).chat("system", body.query)
        return ChatResponse(query=body.query, answer=text, sources=[],
            session_id=attempt_ref.session_id, response_mode=body.response_mode)
    monkeypatch.setattr(api, "_answer", answer)
    user = SimpleNamespace(user_id="actor", username="actor", roles=["viewer"])
    assert api.chat(ChatRequest(query="question", response_mode="full", attempt_id=str(uuid4())), user).answer == "model answer"
    assert calls == [True]
    with session_scope() as session:
        row = session.query(ChatRun).one()
        assert row.status == "completed"
        assert session.get(ChatMessage, row.message_id).content == "model answer"
        assert session.query(ReplicaCapacitySlot).filter(ReplicaCapacitySlot.owner_id.is_not(None)).count() == 0
