"""Manual local alert CLI never treats delivery as proof of healthy backup/SLA."""
from datetime import timedelta
import json
from pathlib import Path

import pytest

from tests.test_replica_backup import KEY
from tests.test_replica_backup_alerts import ENDPOINT, sender
from tests.test_replica_backup_health import NOW, archive


def configured(tmp_path):
    target=archive(tmp_path)
    key=tmp_path/'archive.key';key.write_bytes(KEY)
    signing=tmp_path/'notification.key';signing.write_bytes(b'n'*32)
    value={'schema_version':1,'scope':'owned-lab','archive':str(target),'key_file':str(key),
        'installation_id':'a'*32,'destination_store_id':'independent-backup','receiver_url':ENDPOINT,
        'signing_key_file':str(signing),'delivery_journal':str(tmp_path/'delivery.json')}
    path=tmp_path/'config.json';path.write_text(json.dumps(value))
    return path,value


def call(tool,path,*args):
    assert hasattr(tool,'main'),'Explicit cold-health notification CLI is missing'
    return tool.main(['--config',str(path),'--now',NOW.isoformat(),*args])


def test_dry_run_does_not_read_signing_key_or_touch_delivery_journal(tmp_path,capsys):
    tool=sender();path,value=configured(tmp_path)
    Path(value['signing_key_file']).unlink()
    assert call(tool,path)==0
    report=json.loads(capsys.readouterr().out)
    assert report['delivery']['status']=='dry-run' and report['health']['state']=='healthy'
    assert report['health']['restore_verified'] is report['health']['rpo_assured'] is False
    assert not Path(value['delivery_journal']).exists() and not Path(value['delivery_journal']+'.lock').exists()


def test_apply_uses_cold_health_with_separate_key_and_keeps_alert_exit_code(tmp_path,monkeypatch,capsys):
    tool=sender();path,_=configured(tmp_path);calls=[]
    def deliver(value,**kwargs):
        assert kwargs['apply'] and kwargs['signing_key']==b'n'*32
        assert value.observation_mode=='offline-clock-drill' and value.recovery_point_age_seconds==86401
        calls.append(value)
        return {'status':'delivered','health_state':'alert','event_id':'b'*32,'resumed_pending':False,'recheck_required':False}
    monkeypatch.setattr(tool,'notify_once',deliver)
    assert hasattr(tool,'main'),'Explicit cold-health notification CLI is missing'
    later=NOW+timedelta(seconds=86341)
    assert tool.main(['--config',str(path),'--now',later.isoformat(),'--apply'])==2
    report=json.loads(capsys.readouterr().out)
    assert report['health']['alerts']==['backup_recovery_point_stale'] and len(calls)==1
    assert report['production_schedule']=='disabled'


def test_failed_latest_attempt_cannot_hide_behind_healthy_archive(tmp_path,monkeypatch,capsys):
    tool=sender();path,value=configured(tmp_path)
    from app.deployment.replica_backup_health import start_attempt,finish_attempt
    attempt_path=tmp_path/'attempt.json'
    attempt=start_attempt(attempt_path,installation_id='a'*32,now=NOW-timedelta(seconds=2))
    finish_attempt(attempt_path,attempt,now=NOW,failed=True)
    value['attempt_journal']=str(attempt_path);path.write_text(json.dumps(value))
    before=attempt_path.read_bytes();calls=[]
    def deliver(observed,**kwargs):
        calls.append(observed)
        return {'status':'delivered','health_state':'alert','event_id':'b'*32,'resumed_pending':False,'recheck_required':False}
    monkeypatch.setattr(tool,'notify_once',deliver)
    assert call(tool,path,'--apply')==2
    report=json.loads(capsys.readouterr().out)
    assert report['health']['archive_verified'] and report['health']['alerts']==['backup_last_attempt_failed']
    assert len(calls)==1 and attempt_path.read_bytes()==before


def test_unreadable_archive_key_produces_fixed_health_error_without_path_echo(tmp_path,capsys):
    tool=sender();path,value=configured(tmp_path)
    value['key_file']=str(tmp_path/'PRIVATE_CANARY.key');path.write_text(json.dumps(value))
    assert call(tool,path)==2
    output=capsys.readouterr().out;report=json.loads(output)
    assert report['health']['alerts']==['backup_health_unavailable']
    assert 'PRIVATE_CANARY' not in output and not Path(value['delivery_journal']).exists()


@pytest.mark.parametrize('fault',['malformed','oversized','foreign_scope','external_receiver','journal_in_archive','same_key_bytes','same_key_path'])
def test_invalid_configuration_is_fixed_refusal_without_delivery(tmp_path,monkeypatch,capsys,fault):
    tool=sender();path,value=configured(tmp_path);calls=[]
    if fault=='malformed':path.write_text('{"secret":"PRIVATE_CANARY",')
    elif fault=='oversized':path.write_text('PRIVATE_CANARY'*2000)
    else:
        if fault=='foreign_scope':value['scope']='production-PRIVATE_CANARY'
        elif fault=='external_receiver':value['receiver_url']='http://PRIVATE_CANARY.example.org/v1/backup-alerts'
        elif fault=='journal_in_archive':value['delivery_journal']=str(Path(value['archive'])/'delivery.json')
        elif fault=='same_key_bytes':Path(value['signing_key_file']).write_bytes(KEY)
        else:value['signing_key_file']=value['key_file']
        path.write_text(json.dumps(value))
    monkeypatch.setattr(tool,'notify_once',lambda *a,**k:calls.append(True))
    assert call(tool,path,'--apply')==2
    report=capsys.readouterr().out
    assert 'PRIVATE_CANARY' not in report and not calls
    assert json.loads(report)['delivery']['status']=='refused'


def test_naive_or_invalid_clock_does_not_contact_receiver(tmp_path,monkeypatch,capsys):
    tool=sender();path,_=configured(tmp_path);calls=[]
    monkeypatch.setattr(tool,'notify_once',lambda *a,**k:calls.append(True))
    assert call(tool,path,'--now','PRIVATE_CANARY')==2
    assert 'PRIVATE_CANARY' not in capsys.readouterr().out and not calls
