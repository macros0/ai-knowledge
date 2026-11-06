from types import SimpleNamespace
import pytest
from sqlalchemy import select,func
from pydantic import ValidationError
from app.api import chat as api
from app.auth.models import User
from app.models.answer_scenarios import ScenarioCreate,ScenarioPreviewRequest
from app.services import answer_scenarios as svc
from app.services.chat_execution import preview_answer
from app.config import Settings
from app.db.session import session_scope
from app.db.models import ChatMessage,AnswerScenario


def test_preview_rejects_session_and_selection():
    for key,value in [("session_id","x"),("source_selection",{}),("scenario_id","x")]:
        with pytest.raises(ValidationError):
            ScenarioPreviewRequest(draft={"name":"n","instructions":"i"},request={"query":"q",key:value})


def test_preview_no_database_history_or_versions(monkeypatch):
    user=User(user_id="a",username="a")
    monkeypatch.setattr(svc,"get_settings",lambda:SimpleNamespace(chat_scenarios_enabled=True,auth_provider="simulation"))
    monkeypatch.setattr(api,"get_settings",lambda:Settings(_env_file=None))
    monkeypatch.setattr(api,"_get_embedder",lambda:SimpleNamespace(embed=lambda q:[0.]))
    monkeypatch.setattr(api,"_get_vector_store",lambda:SimpleNamespace(search_composite=lambda **k:[]))
    result=preview_answer(ScenarioPreviewRequest(draft=ScenarioCreate(name="n",instructions="x"),request={"query":"q"}),user)
    assert result.preview and result.scenario is None and result.response_mode=="scenario"
    assert result.scenario_execution.status=="no_sources"
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(ChatMessage))==0
        assert session.scalar(select(func.count()).select_from(AnswerScenario))==0


@pytest.fixture
def preview_runtime(monkeypatch):
    user=User(user_id="preview-owner",username="preview")
    monkeypatch.setattr(svc,"get_settings",lambda:SimpleNamespace(chat_scenarios_enabled=True,auth_provider="simulation"))
    monkeypatch.setattr(api,"get_settings",lambda:Settings(_env_file=None))
    monkeypatch.setattr(api,"_get_embedder",lambda:SimpleNamespace(embed=lambda q:[0.]))
    monkeypatch.setattr(api,"_get_vector_store",lambda:SimpleNamespace(search_composite=lambda **k:[]))
    return user,ScenarioPreviewRequest(draft={"name":"n","instructions":"literal"},request={"query":"q"})


def test_preview_uses_shared_rate_limit_once(preview_runtime,monkeypatch):
    from app.services import chat_execution
    calls=[]
    monkeypatch.setattr(chat_execution,"get_rate_limiter",lambda:SimpleNamespace(check_action=lambda *a,**k:calls.append((a,k))))
    user,body=preview_runtime
    preview_answer(body,user)
    assert len(calls)==1
    assert calls[0][0]==(user.user_id,"chat")


def test_preview_does_not_cancel_chat_attempt(preview_runtime):
    from uuid import uuid4
    from app.services import chat_history
    user,body=preview_runtime
    ref=chat_history.begin_attempt(None,user,"running question",str(uuid4()),"full")
    preview_answer(body,user)
    assert chat_history.attempt_status(ref,user)=="incomplete"


def test_preview_rejects_unavailable_document_scope(preview_runtime):
    from app.api.errors import ApiError
    user,body=preview_runtime
    body.request.search_doc_ids=["missing-doc"]
    with pytest.raises(ApiError) as error:preview_answer(body,user)
    assert error.value.code=="chat_search_scope_unavailable"


@pytest.mark.parametrize("search",[{"query":"  ","locale":"ru"},{"query":"q","assess_sources":"false"},{"query":"q","tags":["x"*129]},{"query":"q","search_doc_ids":[""]}])
def test_preview_validates_search_before_starting_stream(search):
    with pytest.raises(ValidationError):ScenarioPreviewRequest(draft={"name":"n","instructions":"i"},request=search)


def test_preview_normalizes_query_and_filters_like_chat():
    body=ScenarioPreviewRequest(draft={"name":"n","instructions":"i"},request={"query":"  q  ","tags":["tag","tag"]})
    assert body.request.query=="q"
    assert body.request.tags==["tag"]


def test_preview_busy_and_disconnect_release_admission_without_stopping_chat(preview_runtime,monkeypatch):
    import asyncio
    import threading
    from uuid import uuid4
    from app.api.errors import ApiError
    from app.services import chat_history
    from app.services.chat_stream import stream_chat
    from app.services.llm_profiles import request_state
    from app.services.llm_scheduler import LLMCancelled
    user,body=preview_runtime
    monkeypatch.setattr(api,"get_settings",lambda:Settings(_env_file=None,chat_max_inflight=1))
    slots=threading.BoundedSemaphore(1)
    monkeypatch.setattr(api,"_inflight_by_limit",{1:slots})
    ref=chat_history.begin_attempt(None,user,"main question",str(uuid4()),"full")
    entered=threading.Event()
    def generating(*args,**kwargs):
        state=request_state()
        entered.set()
        state["on_text"]("preview started")
        assert state["cancel"].wait(3),"disconnect did not reach preview worker"
        raise LLMCancelled()
    monkeypatch.setattr(api,"_answer",generating)
    async def run():
        stream=stream_chat(lambda:preview_answer(body,user),independent_calls=True)
        first=await anext(stream)
        assert 'preview started' in first and entered.is_set()
        with pytest.raises(ApiError) as error:preview_answer(body,user)
        assert error.value.status_code==429
        await stream.aclose()
        assert await asyncio.to_thread(slots.acquire,True,3),"preview retained admission slot"
        slots.release()
    asyncio.run(run())
    assert chat_history.attempt_status(ref,user)=="incomplete"
