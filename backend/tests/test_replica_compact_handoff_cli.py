"""Explicit create-only handoff plan, SQL atomicity and publication resume."""
import json

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, MaintenanceState, WorkItem
from app.db.session import get_engine
from tests.test_replica_compact_adoption import corpus as corpus
from tests.test_replica_compact_adoption_cli import result, setup_cli
from tests.test_replica_compact_handoff import plan, queued_prepared as queued_prepared


def configured(prepared,tmp_path,monkeypatch):
    tool,settings,source,args=setup_cli(prepared,tmp_path,monkeypatch)
    manifest=tmp_path/'handoff.json'
    manifest.write_text(json.dumps(plan(settings,json.loads(source.read_bytes()))))
    return tool,settings,source,manifest,[*args,'--handoff-plan',str(manifest)]


def test_cli_inspect_creates_source_and_whole_handoff_plan_without_sql_mutation(queued_prepared,tmp_path,monkeypatch,capsys):
    tool,settings,source,manifest,args=configured(queued_prepared,tmp_path,monkeypatch)
    source.unlink();manifest.unlink()
    assert tool.main(['--phase','inspect',*args])==0
    report=result(capsys)
    assert report['mutations']==0 and report['handoff_plan_written'] is True
    assert json.loads(manifest.read_bytes())==plan(settings,json.loads(source.read_bytes()))
    assert not settings.runtime_identity_file.exists()
    with get_engine().connect() as connection:assert len(connection.execute(select(WorkItem)).all())==1


def test_cli_handoff_dry_run_never_connects_or_writes(queued_prepared,tmp_path,monkeypatch,capsys):
    tool,settings,_,_,args=configured(queued_prepared,tmp_path,monkeypatch)
    def forbidden(*_args):pytest.fail('Handoff dry-run opened SQL')
    monkeypatch.setattr(tool,'configure_database',forbidden)
    assert tool.main(['--phase','adopt',*args])==0
    report=result(capsys)
    assert report['database_observed'] is False and report['handoff_intents']==8
    assert not settings.runtime_identity_file.exists()


def test_cli_after_commit_publication_failure_resumes_same_handoff_once(queued_prepared,tmp_path,monkeypatch,capsys):
    tool,settings,_,_,args=configured(queued_prepared,tmp_path,monkeypatch)
    original=tool.write_identity
    def unavailable(*_args):raise OSError('private-canary publication')
    monkeypatch.setattr(tool,'write_identity',unavailable)
    assert tool.main(['--phase','adopt','--apply',*args])==2
    report=result(capsys);assert report['jobs_held'] is True and 'private-canary' not in json.dumps(report)
    assert not settings.runtime_identity_file.exists()
    monkeypatch.setattr(tool,'write_identity',original)
    assert tool.main(['--phase','adopt','--apply',*args])==0
    assert result(capsys)['handoff_intents']==8
    assert tool.main(['--phase','adopt','--apply',*args])==0
    result(capsys)
    without=args[:args.index('--handoff-plan')]
    assert tool.main(['--phase','adopt','--apply',*without])==2
    result(capsys)
    with get_engine().connect() as connection:
        assert len(connection.execute(select(WorkItem)).all())==9
        assert len(connection.execute(select(AuditLog)).all())==3  # Original export request + old fixture + adoption.
        assert connection.scalar(select(MaintenanceState.enabled)) is True


@pytest.mark.parametrize('fault',['duplicate-key','oversized','missing','foreign-source','identity-collision','snapshot-collision'])
def test_cli_bad_plan_refuses_before_database(queued_prepared,tmp_path,monkeypatch,capsys,fault):
    tool,settings,source,manifest,args=configured(queued_prepared,tmp_path,monkeypatch)
    if fault=='duplicate-key':manifest.write_text('{"schema_version":1,"schema_version":1,"secret":"private-canary"}')
    elif fault=='oversized':manifest.write_bytes(b' '*(4*1024*1024+1))
    elif fault=='missing':manifest.unlink()
    elif fault=='foreign-source':
        value=json.loads(manifest.read_bytes());value['source_sql_sha256']='f'*64;manifest.write_text(json.dumps(value))
    elif fault=='identity-collision':settings.runtime_identity_file=manifest
    else:args[-1]=str(source)
    def forbidden(*_args):pytest.fail('Invalid handoff plan opened SQL')
    monkeypatch.setattr(tool,'configure_database',forbidden)
    assert tool.main(['--phase','adopt','--apply',*args])==2
    report=result(capsys)
    assert 'private-canary' not in json.dumps(report) and report['jobs_held'] is None
    assert not (tmp_path/'private/identity.json').exists()
