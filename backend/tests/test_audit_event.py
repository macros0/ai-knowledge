import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.services.audit import ACTION_TYPES
from app.services.audit_catalog import CATALOG, AUTH_ACTIONS
from app.services.audit_event import AuditContext, AuditEventError, build_event, encode_event

def entry(**kw):
    fields=dict(event_id=str(uuid4()),created_at=datetime.now(timezone.utc),action_type="document_upload",user_id="u1",username="demo",target_type="document",target_id="doc1",old_value=None,new_value=None,meta=None)
    fields.update(kw)
    return SimpleNamespace(**fields)

def test_catalog_covers_all_existing_actions():
    assert set(CATALOG)==set(ACTION_TYPES)|set(AUTH_ACTIONS)

def test_catalog_drops_unknown_and_sensitive_fields():
    event=build_event(entry(new_value={"size":123,"filename":"CANARY","headers":{"token":"CANARY"}},meta={"password":"CANARY","reason":"CANARY"}),AuditContext(actor_type="user"))
    assert b"CANARY" not in encode_event(event)
    assert event["new_value"]=={"size":123}
    assert event["client_ip"] is None and event["ip_source"]=="unknown"

def test_unicode_event_limit_and_summary():
    data=encode_event(build_event(entry(username="Тест\nЮзер"),AuditContext(actor_type="user")))
    assert b"\n" not in data
    assert json.loads(data)["actor"]["name"]=="Тест\nЮзер"
    with pytest.raises(AuditEventError):
        encode_event(build_event(entry(username="я"*9000),AuditContext(actor_type="user")))

def test_unknown_action_is_rejected():
    with pytest.raises(AuditEventError):build_event(entry(action_type="unknown"),AuditContext())

def test_verified_auth_context_only_safe_enums():
    with pytest.raises(AuditEventError):AuditContext(method="password-CANARY",reason="CANARY")
    event=build_event(entry(action_type="auth_login_denied",user_id=None,username=None),AuditContext(outcome="denied",reason="invalid_state",method="oidc"))
    assert event["actor"]["type"]=="unknown" and event["outcome"]=="denied"

def test_summary_is_bounded_without_copying_values():
    event=build_event(entry(action_type="document_bulk_tags_update",new_value={"doc_ids":["CANARY"]*500}),AuditContext())
    assert event["new_value"]=={"doc_ids":{"count":500,"truncated":True}}
    assert b"CANARY" not in encode_event(event)


def test_auth_actions_are_available_to_legacy_filter_ui():
    assert AUTH_ACTIONS <= ACTION_TYPES



def test_record_preserves_verified_actor_and_server_context(monkeypatch,tmp_path):
    from sqlalchemy import select
    from app.config import Settings
    from app.auth.models import User
    from app.services import audit
    from app.db.models import AuditOutbox
    from app.db.session import session_scope
    from app.services.diagnostics.context import bind_context
    from app.services.diagnostics.schema import DiagnosticContext
    settings=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode="durable")
    monkeypatch.setattr("app.config.get_settings",lambda:settings)
    rid="00000000-0000-4000-8000-000000000099"
    for actor,kind in [(User(user_id="u1",username="Test",roles=[]),"user"),
                       (User(user_id="anonymous",username="anonymous",roles=[]),"anonymous"),
                       (audit.SystemUser(),"system")]:
        with bind_context(DiagnosticContext(request_id=rid)):
            result=audit.record(actor,audit.DOCUMENT_UPLOAD,audit.TARGET_DOCUMENT)
        with session_scope() as s:event=json.loads(s.scalar(select(AuditOutbox.payload).where(AuditOutbox.audit_id==result['id'])))
        assert event['actor']['type']==kind
        assert event['request_id']==rid
