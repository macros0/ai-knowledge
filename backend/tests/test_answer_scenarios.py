import json
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.auth.models import User
from app.auth.service import require_user
from app.api.errors import ApiError
from app.models.answer_scenarios import ScenarioCreate, ScenarioUpdate
from app.services import answer_scenarios as svc, audit
from app.db.session import session_scope
from app.db.models import AuditLog
from sqlalchemy import select

A=User(user_id="a",username="alice",roles=["viewer"])
B=User(user_id="b",username="bob",roles=["viewer"])

@pytest.fixture(autouse=True)
def available(monkeypatch):
    monkeypatch.setattr(svc,"get_settings",lambda:SimpleNamespace(chat_scenarios_enabled=True,auth_provider="simulation"))


def create(visibility="private"):
    return svc.create_scenario(A,ScenarioCreate(name="Plan",instructions="Exact {locale}\ntext",visibility=visibility))


def test_private_hidden_from_other_user():
    item=create()
    assert svc.get_scenario(item.id,A).instructions == item.instructions
    with pytest.raises(ApiError) as error: svc.get_scenario(item.id,B)
    assert error.value.status_code==404
    assert svc.list_scenarios(B).total==0


def test_public_read_only_for_non_owner_and_viewer_can_publish():
    item=create("public")
    assert not svc.get_scenario(item.id,B).can_edit
    with pytest.raises(ApiError) as error:
        svc.update_scenario(item.id,B,ScenarioUpdate(name="bad",instructions="bad",expected_version=1))
    assert error.value.status_code==403


def test_copy_is_private_independent_v1():
    item=create("public")
    copy=svc.copy_scenario(item.id,B,1)
    assert copy.id != item.id and copy.owner_id==B.user_id
    assert copy.visibility=="private" and copy.version==1
    svc.delete_scenario(item.id,A,1)
    assert svc.get_scenario(copy.id,B).instructions==item.instructions


def test_visibility_change_increments_and_revokes_new_reads():
    item=create("public")
    updated=svc.update_scenario(item.id,A,ScenarioUpdate(name=item.name,instructions=item.instructions,visibility="private",expected_version=1))
    assert updated.version==2
    with pytest.raises(ApiError) as error: svc.get_scenario(item.id,B)
    assert error.value.status_code==404


def test_cas_conflict_and_noop():
    item=create()
    unchanged=svc.update_scenario(item.id,A,ScenarioUpdate(name=item.name,instructions=item.instructions,expected_version=1))
    assert unchanged.version==1
    svc.update_scenario(item.id,A,ScenarioUpdate(name="New",instructions=item.instructions,expected_version=1))
    for operation in [lambda:svc.update_scenario(item.id,A,ScenarioUpdate(name="Old",instructions="x",expected_version=1)),lambda:svc.delete_scenario(item.id,A,1)]:
        with pytest.raises(ApiError) as error: operation()
        assert error.value.code=="scenario_version_conflict"
    assert svc.get_scenario(item.id,A).name=="New"


def test_catalog_pagination_and_no_instructions_in_summary():
    create(); create("public")
    page=svc.list_scenarios(A,scope="all",q="pla",limit=1,offset=0)
    assert page.total==2 and len(page.items)==1
    assert "instructions" not in page.items[0].model_dump()
    assert svc.list_scenarios(B,scope="mine").total==0


def test_copy_rejects_stale_version():
    item=create("public")
    with pytest.raises(ApiError) as error: svc.copy_scenario(item.id,B,2)
    assert error.value.status_code==409


def test_disabled_identity_and_feature_gate(monkeypatch):
    for config in [SimpleNamespace(chat_scenarios_enabled=False,auth_provider="simulation"),SimpleNamespace(chat_scenarios_enabled=True,auth_provider="disabled")]:
        monkeypatch.setattr(svc,"get_settings",lambda:config)
        with pytest.raises(ApiError) as error: create()
        assert error.value.code=="scenarios_unavailable"


def test_audit_omits_text_and_rolls_back(monkeypatch):
    item=create()
    with session_scope() as session:
        rows=session.scalars(select(AuditLog).where(AuditLog.target_id==item.id)).all()
        assert rows and item.instructions not in json.dumps([r.new_value for r in rows])
    def broken(*a,**kw): raise RuntimeError("audit unavailable")
    monkeypatch.setattr(audit,"record_in_session",broken)
    with pytest.raises(RuntimeError): svc.update_scenario(item.id,A,ScenarioUpdate(name="bad",instructions="bad",expected_version=1))
    assert svc.get_scenario(item.id,A).version==1


def test_api_rejects_owner_and_allows_current_user():
    from app.api.answer_scenarios import router
    app=FastAPI(); app.include_router(router)
    app.dependency_overrides[require_user]=lambda:A
    client=TestClient(app)
    assert client.post("/answer-scenarios",json={"name":"x","instructions":"y","owner_id":"b"}).status_code==422
    result=client.post("/answer-scenarios",json={"name":"x","instructions":"y"})
    assert result.status_code==201 and result.json()["owner_id"]=="a"


ADMIN=User(user_id="admin",username="administrator",roles=["admin"])
EDITOR=User(user_id="editor",username="editor",roles=["editor"])


def test_admin_can_edit_and_delete_public_scenario_without_changing_owner():
    item=create("public")
    assert svc.get_scenario(item.id,ADMIN).can_edit
    updated=svc.update_scenario(item.id,ADMIN,ScenarioUpdate(name="Moderated",instructions="revised",visibility="public",expected_version=1))
    assert updated.version==2 and updated.owner_id==A.user_id
    with session_scope() as session:
        row=session.scalars(select(AuditLog).where(AuditLog.target_id==item.id,AuditLog.action_type=="answer_scenario_update")).one()
        assert row.user_id==ADMIN.user_id
    svc.delete_scenario(item.id,ADMIN,2)
    with pytest.raises(ApiError):svc.get_scenario(item.id,A)
    with session_scope() as session:
        row=session.scalars(select(AuditLog).where(AuditLog.target_id==item.id,AuditLog.action_type=="answer_scenario_delete")).one()
        assert row.user_id==ADMIN.user_id


def test_admin_cannot_see_or_mutate_other_private_scenario():
    item=create()
    assert svc.list_scenarios(ADMIN).total==0
    for operation in [lambda:svc.get_scenario(item.id,ADMIN),lambda:svc.update_scenario(item.id,ADMIN,ScenarioUpdate(name="bad",instructions="bad",expected_version=1)),lambda:svc.delete_scenario(item.id,ADMIN,1)]:
        with pytest.raises(ApiError) as error:operation()
        assert error.value.status_code==404
    assert svc.get_scenario(item.id,A).version==1


def test_admin_public_permission_is_revoked_after_unpublish():
    item=create("public")
    assert svc.get_scenario(item.id,ADMIN).can_edit
    svc.update_scenario(item.id,A,ScenarioUpdate(name=item.name,instructions=item.instructions,visibility="private",expected_version=1))
    for operation in [lambda:svc.update_scenario(item.id,ADMIN,ScenarioUpdate(name="bad",instructions="bad",visibility="public",expected_version=2)),lambda:svc.delete_scenario(item.id,ADMIN,2)]:
        with pytest.raises(ApiError) as error:operation()
        assert error.value.status_code==404


def test_editor_does_not_inherit_admin_moderation():
    item=create("public")
    assert not svc.get_scenario(item.id,EDITOR).can_edit
    with pytest.raises(ApiError) as error:svc.delete_scenario(item.id,EDITOR,1)
    assert error.value.status_code==403
