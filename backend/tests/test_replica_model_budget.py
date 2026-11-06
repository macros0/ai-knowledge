"""The shared budget covers actual provider lifetime, not caller waiting time."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from app.config import Settings
from app.services.llm_client import LLMClient, LLMBusyError
from tests.test_llm_client import _stream_response_finished


@pytest.fixture
def settings(tmp_path, monkeypatch):
    import app.services.llm_client as module
    settings = Settings(_env_file=None, data_dir=tmp_path, llm_profile="standard", llm_model="openai/test",
        llm_interactive_slot_wait_seconds=0, llm_retry_attempts=1, llm_retry_backoff_seconds=0)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "_bulk_semaphore", None)
    monkeypatch.setattr(module, "_interactive_semaphore", None)
    return settings


def test_chat_and_generation_share_global_provider_budget(settings, monkeypatch):
    entered, release = Event(), Event()
    calls = []
    def provider(**_kwargs):
        from app.db.session import _current_session
        assert _current_session.get() is None
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
        return _stream_response_finished("answer")
    monkeypatch.setattr("litellm.completion", provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(LLMClient(settings=settings)._complete_once, "system", "user")
        try:
            assert entered.wait(5)
            for _ in range(3):
                with pytest.raises(LLMBusyError):
                    LLMClient(interactive=True, settings=settings)._complete_once("system", "user")
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=5)[0] == "answer"
    assert LLMClient(interactive=True, settings=settings)._complete_once("system", "user")[0] == "answer"
    assert len(calls) == 2


def test_ambiguous_provider_timeout_does_not_free_budget(settings, monkeypatch):
    calls = []
    def provider(**_kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("provider did not confirm completion")
    monkeypatch.setattr("litellm.completion", provider)
    client = LLMClient(interactive=True, settings=settings)
    with pytest.raises(httpx.ReadTimeout):
        client._complete_once("system", "user")
    with pytest.raises(LLMBusyError):
        client._complete_once("system", "user")
    assert len(calls) == 1


def test_provider_admission_refuses_an_open_sql_transaction(settings, monkeypatch):
    from app.db.session import session_scope
    def forbidden(**_kwargs):
        pytest.fail("Provider started before admission could commit")
    monkeypatch.setattr("litellm.completion", forbidden)
    with session_scope(), pytest.raises(ValueError, match="outside a SQL"):
        LLMClient(interactive=True, settings=settings)._complete_once("system", "user")


def test_caller_timeout_keeps_the_live_provider_budget(settings, monkeypatch):
    from app.services.llm_client import LLMTimeoutError
    from app.services.work.model_budget import ModelBudget
    entered, release, completed = Event(), Event(), Event()
    original_complete = ModelBudget.complete
    def complete(self, **kwargs):
        try:
            return original_complete(self, **kwargs)
        finally:
            completed.set()
    monkeypatch.setattr(ModelBudget, "complete", complete)
    def provider(**_kwargs):
        entered.set()
        assert release.wait(10)
        return _stream_response_finished("late answer")
    monkeypatch.setattr("litellm.completion", provider)
    client = LLMClient(interactive=True, settings=settings)
    try:
        with pytest.raises(LLMTimeoutError):
            client._complete_once("system", "user", idle_timeout=0.05)
        assert entered.is_set() and not completed.is_set()
        with pytest.raises(LLMBusyError):
            client._complete_once("system", "user")
    finally:
        release.set()
        assert completed.wait(5)


def test_expired_chat_owner_cannot_start_a_provider_call(settings, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from sqlalchemy import text
    from app.db.session import session_scope
    from app.models.schemas import ChatRequest
    from app.services.llm_profiles import request_scope
    from app.services.llm_scheduler import LLMCancelled
    from app.services.work.chat import admit_chat_run
    with session_scope() as session:
        accepted = admit_chat_run(session, settings, ChatRequest(query="question", response_mode="full",
            attempt_id=str(uuid4())), SimpleNamespace(user_id="actor", username="actor"), owner_id="api-a")
        session.execute(text("UPDATE chat_runs SET lease_until='2000-01-01' WHERE id=:id"), {"id": accepted.run_id})
    calls = []
    def provider(**_kwargs):
        calls.append(True)
        return _stream_response_finished("unowned answer")
    monkeypatch.setattr("litellm.completion", provider)
    with request_scope(chat_claim=accepted, cancel=Event()), pytest.raises(LLMCancelled):
        LLMClient(interactive=True, settings=settings)._complete_once("system", "user")
    assert calls == []


def test_expired_preview_owner_cannot_start_a_provider_call(settings, monkeypatch):
    from sqlalchemy import text
    from app.db.session import session_scope
    from app.services.llm_profiles import request_scope
    from app.services.llm_scheduler import LLMCancelled
    from app.services.work.coordination import reserve_capacity
    with session_scope() as session:
        lease = reserve_capacity(session, resource="chat", owner_id="preview", limit=1, ttl_seconds=30)
        session.execute(text("UPDATE replica_capacity_slots SET expires_at='2000-01-01' WHERE id=:id"), {"id": lease.id})
    calls = []
    def provider(**_kwargs):
        calls.append(True)
        return _stream_response_finished("unowned preview")
    monkeypatch.setattr("litellm.completion", provider)
    with request_scope(chat_capacity=lease, cancel=Event()), pytest.raises(LLMCancelled):
        LLMClient(interactive=True, settings=settings)._complete_once("system", "user")
    assert calls == []
