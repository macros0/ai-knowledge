"""Public contract and real private logging path, using synthetic events only."""
import json
import logging
import logging.config
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app.services.audit_catalog import CATALOG
from app.services.audit_event import AuditContext, build_event, encode_event
from app.services.audit_logstash import format_logstash_payload, make_logstash_sink, AuditLogstashFormatError


def payload(outcome="success", **kw):
    entry = SimpleNamespace(event_id="00000000-0000-4000-8000-000000000001",
        created_at=datetime(2026, 10, 20, tzinfo=timezone.utc), action_type="document_upload",
        user_id="test-user", username="Тест\nЮзер", target_type="document", target_id="d1",
        old_value=None, new_value=None, meta=None)
    for key, value in kw.items(): setattr(entry, key, value)
    return encode_event(build_event(entry, AuditContext(actor_type="user", outcome=outcome)))


@pytest.mark.parametrize("outcome,level,value", [("success","INFO",20000),("denied","WARN",30000),("failure","ERROR",40000)])
def test_exact_logstash_contract(outcome, level, value):
    source = payload(outcome)
    wire = format_logstash_payload(source)
    event = json.loads(wire)
    canonical = json.loads(source)
    assert len(event) == 24
    assert {key:event[key] for key in canonical} == canonical
    assert event["@timestamp"] == canonical["timestamp"]
    assert event["message"] == canonical["action"]
    assert (event["@version"], event["application"], event["logger_name"],event["thread_name"]) == ("1","ai-knowledge","ai_knowledge.audit","audit-outbox")
    assert (event["level"],event["level_value"],event["audit_transport_version"]) == (level,value,1)
    assert type(event["level_value"]) is int
    assert b"\n" not in wire and b"\r" not in wire
    assert len(wire) <= 18432
    assert wire == format_logstash_payload(source)


def test_overhead_all_current_actions():
    for action in CATALOG:
        source = payload(action_type=action)
        wire = format_logstash_payload(source)
        assert len(wire)-len(source) <= 2048
        assert len(wire) <= 18432


@pytest.mark.parametrize("change", [{"@timestamp":"CANARY"}, {"password":"CANARY"}, {"schema_version":True}])
def test_unknown_or_conflicting_fields_rejected(change):
    event=json.loads(payload());event.update(change)
    with pytest.raises(AuditLogstashFormatError):
        format_logstash_payload(json.dumps(event,ensure_ascii=False,separators=(",",":")).encode())


def test_private_logger_survives_global_reconfiguration():
    written=[]; sink=make_logstash_sink(written.append)
    old=logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        logging.config.dictConfig({"version":1,"disable_existing_loggers":True})
        factory=logging.getLogRecordFactory()
        logging.setLogRecordFactory(lambda *a,**k: (_ for _ in ()).throw(AssertionError("CANARY")))
        try: sink(payload())
        finally: logging.setLogRecordFactory(factory)
    finally:
        logging.disable(old)
        # Restore loggers disabled by the test so unrelated diagnostics keep working.
        for obj in logging.root.manager.loggerDict.values():
            if isinstance(obj,logging.Logger):obj.disabled=False
    assert written == [format_logstash_payload(payload())]


@pytest.mark.parametrize("raise_errors", [True,False])
def test_handler_does_not_hide_write_failure(raise_errors):
    old=logging.raiseExceptions;logging.raiseExceptions=raise_errors
    try:
        def fail(_):raise BrokenPipeError("CANARY")
        with pytest.raises(BrokenPipeError):make_logstash_sink(fail)(payload())
    finally:logging.raiseExceptions=old


def test_sink_writes_once_without_record_extra():
    written=[]; sink=make_logstash_sink(written.append)
    sink(payload());sink(payload())
    assert written == [format_logstash_payload(payload())]*2
    assert b"pathname" not in written[0] and b"process" not in written[0]


def test_invalid_payload_is_never_written():
    written=[]
    with pytest.raises(AuditLogstashFormatError):make_logstash_sink(written.append)(b"CANARY")
    assert written == []


@pytest.mark.parametrize("fmt",["native","logstash"])
def test_dispatcher_retry_and_switch_preserve_canonical(monkeypatch,tmp_path,fmt):
    from app.config import Settings
    from app.services import audit
    from app.services.audit_outbox import AuditDispatcher
    from app.db.models import AuditOutbox
    from app.db.session import session_scope
    from sqlalchemy import select
    settings=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode="durable",audit_stdout_format=fmt)
    monkeypatch.setattr("app.config.get_settings",lambda:settings)
    audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
    with session_scope() as s:canonical=s.scalar(select(AuditOutbox.payload))
    written=[];d=AuditDispatcher(settings,write_line=written.append)
    marker=d._mark_written
    monkeypatch.setattr(d,"_mark_written",lambda *a:(_ for _ in ()).throw(RuntimeError("CANARY")))
    assert d.tick()==0
    with session_scope() as s:
        row=s.scalar(select(AuditOutbox));assert row.status=="pending";row.next_attempt_at=datetime(2000,1,1,tzinfo=timezone.utc)
    monkeypatch.setattr(d,"_mark_written",marker)
    assert d.tick()==1 and written[0]==written[1]
    with session_scope() as s:
        row=s.scalar(select(AuditOutbox));assert row.status=="stdout_written" and row.payload==canonical
    event=json.loads(written[0]);assert {key:event[key] for key in json.loads(canonical)}==json.loads(canonical)


def test_formatter_failure_keeps_pending(monkeypatch,tmp_path):
    from app.config import Settings
    from app.services import audit
    from app.services.audit_outbox import AuditDispatcher
    from app.db.models import AuditOutbox
    from app.db.session import session_scope
    from sqlalchemy import select
    settings=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode="durable",audit_stdout_format="logstash")
    monkeypatch.setattr("app.config.get_settings",lambda:settings)
    audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
    monkeypatch.setattr("app.services.audit_logstash.AuditLogstashFormatter.format",lambda *a:(_ for _ in ()).throw(AuditLogstashFormatError()))
    written=[];d=AuditDispatcher(settings,write_line=written.append)
    assert d.tick()==0 and written==[]
    with session_scope() as s:
        row=s.scalar(select(AuditOutbox));assert (row.status,row.last_error_code)==("pending","stdout_failure")


@pytest.mark.parametrize("fmt",["native","logstash"])
def test_thousand_events_are_delivered(tmp_path,monkeypatch,fmt,record_property):
    import time
    from sqlalchemy import select,func
    from app.config import Settings
    from app.db.session import session_scope
    from app.db.models import AuditOutbox
    from app.services import audit
    from app.services.audit_outbox import AuditDispatcher
    settings=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode="durable",audit_stdout_format=fmt)
    monkeypatch.setattr("app.config.get_settings",lambda:settings)
    with session_scope() as s:
        for _ in range(1000):audit.record_in_session(s,action_type=audit.DOCUMENT_UPLOAD)
    written=[];dispatcher=AuditDispatcher(settings,write_line=written.append)
    start=time.monotonic()
    assert sum(dispatcher.tick() for _ in range(10))==1000
    record_property("seconds",time.monotonic()-start)
    record_property("max_payload_bytes",max(map(len,written)))
    assert len(written)==1000 and len({json.loads(b)["event_id"] for b in written})==1000
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(AuditOutbox).where(AuditOutbox.status=="stdout_written"))==1000
        originals=[json.loads(row) for row in s.scalars(select(AuditOutbox.payload).order_by(AuditOutbox.id))]
    for wire,source in zip(written,originals):
        output=json.loads(wire)
        assert {key:output[key] for key in source}==source
