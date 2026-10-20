"""New audit migration must retain legacy audit and the current main lineage."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, DateTime, JSON, text
from app.services.audit_schema import validate_audit_schema


def migration():
    path=Path(__file__).parents[1]/"alembic/versions/066b1c2d3e4f_audit_outbox.py"
    spec=importlib.util.spec_from_file_location("new_audit_migration",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("postgres",[False,True])
def test_legacy_upgrade_preserves_rows(tmp_path, monkeypatch, postgres):
    url=os.environ.get("AUDIT_TEST_POSTGRES_URL") if postgres else "sqlite:///"+str(tmp_path/"legacy.db")
    if postgres and not url:pytest.skip("Disposable PostgreSQL not provided")
    if postgres:
        from sqlalchemy.engine import make_url
        assert make_url(url).database.startswith("audit_test_")
    engine=create_engine(url)
    md=MetaData()
    audit=Table("audit_log",md,Column("id",Integer,primary_key=True),Column("created_at",DateTime(timezone=True),nullable=False),
        Column("user_id",String(255)),Column("username",String(255)),Column("action_type",String(64),nullable=False),
        Column("target_type",String(64)),Column("target_id",String(255)),Column("old_value",JSON),
        Column("new_value",JSON),Column("ip_address",String(64)),Column("meta",JSON))
    module=migration()
    try:
        md.create_all(engine)
        with engine.begin() as c:
            from datetime import datetime, timezone
            c.execute(audit.insert().values(id=42,created_at=datetime(2026,1,1,tzinfo=timezone.utc),username="legacy-user",action_type="document_upload"))
            before=c.execute(text("SELECT id,username,action_type FROM audit_log")).all()
            monkeypatch.setattr(module.op,"get_bind",lambda:c)
            module.upgrade(); validate_audit_schema(c)
            assert c.execute(text("SELECT id,username,action_type FROM audit_log")).all()==before
            assert c.scalar(text("SELECT event_id FROM audit_log WHERE id=42")) is None
            assert c.scalar(text("SELECT count(*) FROM audit_outbox"))==0
            # Repair is idempotent; no backfill and no history stamp.
            module.upgrade();validate_audit_schema(c)
            assert module.down_revision=="065b1c2d3e4f"
    finally:
        if postgres:
            with engine.begin() as c:
                c.execute(text("DROP TABLE IF EXISTS audit_outbox"));c.execute(text("DROP TABLE IF EXISTS audit_log"))
        engine.dispose()


def test_fresh_main_alembic_chain(tmp_path):
    backend=Path(__file__).parents[1]
    env=dict(os.environ,DATABASE_URL="sqlite:///"+str(tmp_path/"fresh.db"),DATA_DIR=str(tmp_path/"data"),ENVIRONMENT="development",AUDIT_STDOUT_MODE="disabled")
    result=subprocess.run([sys.executable,"-m","alembic","upgrade","head"],cwd=backend,env=env,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stderr[-3000:]
    engine=create_engine(env["DATABASE_URL"])
    with engine.connect() as c:
        validate_audit_schema(c)
        assert c.scalar(text("SELECT version_num FROM alembic_version"))=="066b1c2d3e4f"
        from sqlalchemy import inspect
        assert {"auth_sessions","document_generations","reference_resource_states"} <= set(inspect(c).get_table_names())
    engine.dispose()
