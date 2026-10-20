import pytest
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import sessionmaker
from app.config import Settings
from app.db.models import AuditOutbox,AuditLog
from app.services import audit
from app.services.audit_hold import hold_pending
from app.services.audit_runtime import delivery_status


def test_hold_preserves_payload_and_new_events_remain_pending(tmp_path,monkeypatch):
    engine=create_engine(f"sqlite:///{tmp_path/'owned-restore.db'}")
    try:
        AuditLog.__table__.create(engine);AuditOutbox.__table__.create(engine)
        config=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode='durable')
        monkeypatch.setattr('app.config.get_settings',lambda:config)
        sessions=sessionmaker(engine)
        with sessions.begin() as s:audit.record_in_session(s,action_type=audit.DOCUMENT_UPLOAD)
        with sessions() as s:before=s.scalar(select(AuditOutbox.payload))
        assert hold_pending(engine)==1
        assert hold_pending(engine)==0
        with sessions.begin() as s:audit.record_in_session(s,action_type=audit.DOCUMENT_DELETE)
        with sessions() as s:
            rows=list(s.scalars(select(AuditOutbox).order_by(AuditOutbox.id)))
            assert rows[0].status=='held' and rows[0].payload==before
            assert rows[0].last_error_code=='restored_backup' and rows[1].status=='pending'
        status=delivery_status(engine=engine)
        assert status['counts']=={'pending':1,'held':1,'stdout_written':0}
        assert status['runtime_state']=='unknown' and 'payload' not in str(status)
    finally:engine.dispose()


def test_absent_table_is_noop(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path/'empty.db'}")
    try:assert hold_pending(engine)==0
    finally:engine.dispose()


def test_unknown_table_refused(tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path/'unknown.db'}")
    try:
        with engine.begin() as c:c.execute(text('CREATE TABLE audit_outbox (status TEXT)'))
        with pytest.raises(ValueError):hold_pending(engine)
    finally:engine.dispose()
