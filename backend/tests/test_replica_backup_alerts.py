"""Change-only alerts retain one durable event through HTTP/ACK uncertainty."""
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import time

from filelock import FileLock
import pytest

from tests.test_replica_backup_alert_protocol import KEY, health, protocol, snapshot


ENDPOINT = 'http://127.0.0.1:16491/v1/backup-alerts'


def sender():
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/kubernetes'))
    assert importlib.util.find_spec('replica_backup_alerts') is not None, 'Durable backup-alert sender is missing'
    return importlib.import_module('replica_backup_alerts')


class Delivery:
    def __init__(self, journal, *, key=KEY, lose_first=False, wrong_ack=False):
        self.journal,self.key,self.lose_first,self.wrong_ack=journal,key,lose_first,wrong_ack
        self.calls=[]

    def __call__(self, endpoint, body, headers):
        tool=protocol()
        event=tool.verify_event(body,headers,self.key,now=time.time())
        persisted=json.loads(self.journal.read_bytes())
        assert persisted['pending']['event_id']==event.event_id
        assert not self.journal.with_name(self.journal.name+'.writing').exists()
        assert endpoint==ENDPOINT
        self.calls.append(body)
        if self.lose_first and len(self.calls)==1:raise TimeoutError('PRIVATE_CANARY response lost')
        ack=tool.make_ack(event.event_id,sha256(body).hexdigest(),'accepted',b'j'*32 if self.wrong_ack else self.key)
        return 201,ack


def notify(tool, value, path, delivery, **kwargs):
    return tool.notify_once(value,endpoint=ENDPOINT,signing_key=KEY,journal_path=path,
        apply=True,transport=delivery,**kwargs)


def test_initial_healthy_pending_and_dry_run_never_send(tmp_path):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path)
    for state in ('healthy','pending'):
        result=notify(tool,snapshot(wire,health(state=state,age=10)),path,deliver)
        assert result['status']=='quiet'
    dry=tool.notify_once(snapshot(wire),endpoint=ENDPOINT,signing_key=KEY,
        journal_path=path,apply=False,transport=deliver)
    assert dry['status']=='dry-run' and not deliver.calls
    assert not path.exists()


def test_alert_persists_before_send_and_unchanged_aging_does_not_repeat(tmp_path):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path)
    first=notify(tool,snapshot(wire),path,deliver)
    assert first['status']=='delivered' and len(deliver.calls)==1
    later=health(age=90000);later['checked_at']='2026-11-02T19:00:00+00:00'
    assert notify(tool,snapshot(wire,later),path,deliver)['status']=='quiet'
    assert len(deliver.calls)==1
    state=json.loads(path.read_bytes())
    assert state['pending'] is None and state['alert_active'] is True


def test_changed_failure_and_recovery_each_send_once(tmp_path):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path)
    notify(tool,snapshot(wire),path,deliver)
    failed=snapshot(wire,health(code='backup_last_attempt_failed'))
    assert notify(tool,failed,path,deliver)['status']=='delivered'
    healthy=snapshot(wire,health(state='healthy',age=10))
    assert notify(tool,healthy,path,deliver)['status']=='delivered'
    assert notify(tool,healthy,path,deliver)['status']=='quiet'
    assert len(deliver.calls)==3
    assert [json.loads(body)['kind'] for body in deliver.calls]==['alert','alert','recovered']


def test_lost_ack_retains_identical_event_body_for_retry(tmp_path):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path,lose_first=True)
    first=notify(tool,snapshot(wire),path,deliver)
    assert first['status']=='pending'
    pending=json.loads(path.read_bytes())['pending']
    second=notify(tool,snapshot(wire),path,deliver)
    assert second['status']=='delivered' and second['resumed_pending'] is True
    assert first['event_id']==second['event_id']==pending['event_id']
    assert deliver.calls[0]==deliver.calls[1]


def test_changed_pending_payload_is_refused_before_retry(tmp_path):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path,lose_first=True)
    notify(tool,snapshot(wire),path,deliver)
    state=json.loads(path.read_bytes())
    state['pending']['health']['recovery_point_age_seconds']=99999.0
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError):notify(tool,snapshot(wire),path,deliver)
    assert len(deliver.calls)==1


def test_real_http_lost_ack_retries_the_committed_local_inbox(tmp_path):
    import socket
    import sqlite3
    from tests.test_replica_backup_alert_receiver import receiver,running
    implementation=receiver()
    class LoseFirstAck(implementation.ReceiverHandler):
        lost=False
        def _send_json(self,status,body):
            if status==201 and not type(self).lost:
                type(self).lost=True
                self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            return super()._send_json(status,body)
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json'
    with running(tmp_path,handler=LoseFirstAck) as (_,base,inbox):
        kwargs={'endpoint':base+'/v1/backup-alerts','signing_key':KEY,'journal_path':path,'apply':True}
        first=tool.notify_once(snapshot(wire),**kwargs)
        assert first['status']=='pending'
        second=tool.notify_once(snapshot(wire),**kwargs)
        assert second['status']=='delivered' and second['resumed_pending']
        assert first['event_id']==second['event_id']
        with sqlite3.connect(inbox) as connection:
            assert connection.execute('SELECT count(*) FROM events').fetchone()[0]==1


def test_existing_pending_finishes_before_a_new_health_transition(tmp_path):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path,lose_first=True)
    notify(tool,snapshot(wire),path,deliver)
    healthy=snapshot(wire,health(state='healthy',age=10))
    resumed=notify(tool,healthy,path,deliver)
    assert resumed['resumed_pending'] and resumed['recheck_required']
    assert json.loads(deliver.calls[-1])['kind']=='alert'
    assert notify(tool,healthy,path,deliver)['status']=='delivered'
    assert json.loads(deliver.calls[-1])['kind']=='recovered'


def test_untrusted_ack_cannot_mark_pending_event_delivered(tmp_path):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path,wrong_ack=True)
    result=notify(tool,snapshot(wire),path,deliver)
    assert result['status']=='pending' and json.loads(path.read_bytes())['pending'] is not None
    assert 'PRIVATE_CANARY' not in json.dumps(result)


def test_uncertain_journal_marker_blocks_network_and_cannot_be_cleared_automatically(tmp_path):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path)
    marker=path.with_name(path.name+'.writing');marker.write_text('uncertain')
    with pytest.raises(ValueError):notify(tool,snapshot(wire),path,deliver)
    assert not deliver.calls and marker.exists()


def test_failed_pending_persistence_sends_nothing_and_preserves_uncertainty(tmp_path,monkeypatch):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path)
    from app.deployment import replica_backup_health as journal
    def unavailable(_path):raise OSError('PRIVATE_CANARY fsync failed')
    monkeypatch.setattr(journal,'_sync_directory',unavailable)
    with pytest.raises(ValueError) as caught:notify(tool,snapshot(wire),path,deliver)
    assert 'PRIVATE_CANARY' not in str(caught.value) and not deliver.calls
    assert path.with_name(path.name+'.writing').exists()


def test_held_delivery_lock_blocks_a_second_sender(tmp_path):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path)
    with FileLock(str(path)+'.lock',timeout=0):
        with pytest.raises(ValueError):notify(tool,snapshot(wire),path,deliver)
    assert not deliver.calls


@pytest.mark.parametrize('change',['installation','destination','endpoint','key'])
def test_delivery_journal_cannot_be_reused_for_other_authority(tmp_path,change):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path)
    current=snapshot(wire)
    notify(tool,current,path,deliver)
    kwargs={'endpoint':ENDPOINT,'signing_key':KEY,'journal_path':path,'apply':True,'transport':deliver}
    if change=='installation':current=current.model_copy(update={'installation_id':'8'*32})
    elif change=='destination':current=current.model_copy(update={'destination_store_id':'another-store'})
    elif change=='endpoint':kwargs['endpoint']='http://127.0.0.1:16492/v1/backup-alerts'
    else:kwargs['signing_key']=b'j'*32
    with pytest.raises(ValueError):tool.notify_once(current,**kwargs)
    assert len(deliver.calls)==1


@pytest.mark.parametrize('endpoint',[
    'http://localhost:16491/v1/backup-alerts','http://10.10.1.52:16491/v1/backup-alerts',
    'http://user:PRIVATE_CANARY@127.0.0.1:16491/v1/backup-alerts',
    'http://127.0.0.1:16491/wrong','http://127.0.0.1:16491/v1/backup-alerts?secret=PRIVATE_CANARY',
])
def test_training_sender_never_follows_arbitrary_or_credential_bearing_urls(tmp_path,endpoint):
    tool,wire=sender(),protocol()
    path=tmp_path/'delivery.json';deliver=Delivery(path)
    with pytest.raises(ValueError) as caught:
        tool.notify_once(snapshot(wire),endpoint=endpoint,signing_key=KEY,journal_path=path,apply=True,transport=deliver)
    assert 'PRIVATE_CANARY' not in str(caught.value) and not deliver.calls
