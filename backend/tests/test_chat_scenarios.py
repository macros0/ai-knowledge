from uuid import uuid4
from types import SimpleNamespace
import pytest
from app.auth.models import User
from app.models.answer_scenarios import ScenarioCreate, ScenarioUpdate
from app.services import answer_scenarios as svc,chat_history
from app.api.errors import ApiError

A=User(user_id="a",username="alice")
B=User(user_id="b",username="bob")

@pytest.fixture(autouse=True)
def available(monkeypatch):
    monkeypatch.setattr(svc,"get_settings",lambda:SimpleNamespace(chat_scenarios_enabled=True,auth_provider="simulation"))


def test_running_v1_next_request_v2_and_delete_snapshot_survives():
    item=svc.create_scenario(A,ScenarioCreate(name="N",instructions="v1",visibility="public"))
    first=chat_history.begin_attempt(None,B,"q",str(uuid4()),"scenario",scenario_id=item.id)
    svc.update_scenario(item.id,A,ScenarioUpdate(name="N",instructions="v2",visibility="public",expected_version=1))
    second=chat_history.begin_attempt(None,B,"q",str(uuid4()),"scenario",scenario_id=item.id)
    assert chat_history.read_attempt_scenario(first,B).instructions=="v1"
    assert chat_history.read_attempt_scenario(second,B).version==2
    svc.delete_scenario(item.id,A,2)
    assert chat_history.read_attempt_scenario(first,B).version==1
    with pytest.raises(ApiError):chat_history.begin_attempt(None,B,"q",str(uuid4()),"scenario",scenario_id=item.id)


def test_reused_attempt_never_rewrites_snapshot_and_checkpoints_protect_it():
    item=svc.create_scenario(A,ScenarioCreate(name="N",instructions="v1"))
    attempt=str(uuid4())
    first=chat_history.begin_attempt(None,A,"q",attempt,"scenario",scenario_id=item.id)
    svc.update_scenario(item.id,A,ScenarioUpdate(name="N",instructions="v2",expected_version=1))
    same=chat_history.begin_attempt(first.session_id,A,"q",attempt,"scenario",scenario_id=item.id)
    chat_history.save_attempt_sources(first,A,[],retrieval_metadata={"scenario":{"instructions":"forged"}})
    assert same.existing
    assert chat_history.read_attempt_scenario(first,A).instructions=="v1"
    chat_history.finish_attempt(first,A,status="stopped",answer="")
    assert chat_history.read_attempt_scenario(first,A).version==1


def test_unpublish_revokes_new_attempt_but_not_accepted():
    item=svc.create_scenario(A,ScenarioCreate(name="N",instructions="x",visibility="public"))
    first=chat_history.begin_attempt(None,B,"q",str(uuid4()),"scenario",scenario_id=item.id)
    svc.update_scenario(item.id,A,ScenarioUpdate(name="N",instructions="x",visibility="private",expected_version=1))
    assert chat_history.read_attempt_scenario(first,B).instructions=="x"
    with pytest.raises(ApiError):chat_history.begin_attempt(None,B,"q",str(uuid4()),"scenario",scenario_id=item.id)


@pytest.fixture
def scenario_http(monkeypatch):
    from app.api import chat as api
    from app.auth.service import require_user
    from app.db.models import Document,OkfConcept
    from app.db.session import session_scope
    from app.services.fusion import Hit
    from tests.test_chat import make_client
    with session_scope() as session:
        session.add(Document(id="scenario-doc",filename="reference.docx"))
        session.add(OkfConcept(doc_id="scenario-doc",slug="proof",title="Доказательство",content="Доказательство факта",chunk_index=0))
    hits=[Hit("p",1.,dict(point_type="concept",doc_id="scenario-doc",slug="proof",title="Доказательство",chunk_index=0))]
    searches=[];prompts=[]
    monkeypatch.setattr(api,"_get_embedder",lambda:SimpleNamespace(embed=lambda q:[0.]))
    monkeypatch.setattr(api,"_get_vector_store",lambda:SimpleNamespace(search_composite=lambda **kw:searches.append(kw) or hits))
    monkeypatch.setattr(api.ChatTokenBudget,"fits",lambda *a,**kw:True)
    monkeypatch.setattr(api,"_get_llm",lambda:SimpleNamespace(model="test",chat=lambda system,user:prompts.append(user) or "Ответ [1]"))
    client=make_client(monkeypatch)
    client.app.dependency_overrides[require_user]=lambda:B
    item=svc.create_scenario(A,ScenarioCreate(name="N",instructions="v1",visibility="public"))
    return client,item,searches,prompts


def test_http_scenario_latest_and_selected_sources_without_new_search(scenario_http):
    client,item,searches,prompts=scenario_http
    request={"query":"Доказательство","response_mode":"scenario","scenario_id":item.id}
    first=client.post("/api/chat",json=request)
    assert first.status_code==200,first.text
    data=first.json();assert data["scenario"]["version"]==1
    assert data["scenario_execution"]["status"]=="applied"
    svc.update_scenario(item.id,A,ScenarioUpdate(name="N",instructions="v2",visibility="public",expected_version=1))
    before=len(searches)
    selected=client.post("/api/chat",json={**request,"session_id":data["session_id"],"source_selection":{"attempt_id":data["attempt_id"],"indexes":[1]}})
    assert selected.status_code==200,selected.text
    assert selected.json()["response_mode"]=="scenario"
    assert selected.json()["scenario"]["version"]==2
    assert len(searches)==before
    assert '"scenario_instructions": "v2"' in prompts[-1]


def test_stream_snapshot_precedes_result_and_history_keeps_it(scenario_http):
    import json
    client,item,_,_=scenario_http
    response=client.post("/api/chat/stream",json={"query":"Доказательство","response_mode":"scenario","scenario_id":item.id})
    events=[json.loads(line) for line in response.text.splitlines()]
    assert events[0]["type"]=="start"
    scenario_event=next(e for e in events if e.get("phase")=="scenario")
    result=next(e["data"] for e in events if e["type"]=="result")
    assert scenario_event["scenario"]==result["scenario"]
    saved=chat_history.get_thread(result["session_id"],B.user_id)
    assert saved["messages"][-1]["retrieval_metadata"]["scenario"]["instructions"]=="v1"



def test_disabled_feature_keeps_historical_snapshot(monkeypatch):
    item=svc.create_scenario(A,ScenarioCreate(name="N",instructions="old"))
    ref=chat_history.begin_attempt(None,A,"q",str(uuid4()),"scenario",scenario_id=item.id)
    monkeypatch.setattr(svc,"get_settings",lambda:SimpleNamespace(chat_scenarios_enabled=False,auth_provider="simulation"))
    assert chat_history.read_attempt_scenario(ref,A).instructions=="old"
    with pytest.raises(ApiError) as error:svc.get_scenario(item.id,A)
    assert error.value.code=="scenarios_unavailable"


def test_reused_attempt_rejects_different_scenario_id():
    item=svc.create_scenario(A,ScenarioCreate(name="N",instructions="old"))
    other=svc.create_scenario(A,ScenarioCreate(name="Other",instructions="other"))
    attempt=str(uuid4())
    ref=chat_history.begin_attempt(None,A,"q",attempt,"scenario",scenario_id=item.id)
    with pytest.raises(ValueError):chat_history.begin_attempt(ref.session_id,A,"q",attempt,"scenario",scenario_id=other.id)
    assert chat_history.read_attempt_scenario(ref,A).id==item.id


def test_no_sources_still_returns_scenario_snapshot(scenario_http,monkeypatch):
    from app.api import chat as api
    client,item,_,_=scenario_http
    monkeypatch.setattr(api,"_get_vector_store",lambda:SimpleNamespace(search_composite=lambda **kw:[]))
    response=client.post("/api/chat",json={"query":"missing","response_mode":"scenario","scenario_id":item.id})
    assert response.status_code==200,response.text
    result=response.json()
    assert result["scenario"]["id"]==item.id and result["scenario_execution"]["status"]=="no_sources"


def test_failed_stream_retains_scenario_snapshot_and_partial_answer(scenario_http,monkeypatch):
    import json
    from app.api import chat as api
    from app.services.llm_profiles import request_state
    client,item,_,_=scenario_http
    def fail(system,user):
        request_state()["on_text"]("Partial [1]")
        raise ApiError(422,"chat_evidence_invalid","PRIVATE_ERROR")
    monkeypatch.setattr(api,"_get_llm",lambda:SimpleNamespace(model="test",chat=fail))
    response=client.post("/api/chat/stream",json={"query":"Доказательство","response_mode":"scenario","scenario_id":item.id})
    events=[json.loads(line) for line in response.text.splitlines()]
    start=next(e for e in events if e["type"]=="start")
    assert events[-1]["type"]=="error"
    saved=chat_history.get_thread(start["session_id"],B.user_id)["messages"][-1]
    assert saved["content"]=="Partial [1]"
    assert saved["retrieval_metadata"]["scenario"]["id"]==item.id
    assert saved["retrieval_metadata"]["answer_attempt"]["status"]=="failed"
