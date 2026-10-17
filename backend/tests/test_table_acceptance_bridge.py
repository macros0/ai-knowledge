"""Bounded transport for numeric, read-only observation frames; no model fake."""
import io,threading,time
import pytest


def test_numeric_peer_rejects_wrong_sequence_and_malformed_reply():
    from table_acceptance_bridge import JsonPeer, BridgeUnknown
    for body in ('not-json\n','{"ready":2}\n',''):
        peer=JsonPeer(io.StringIO(),io.StringIO(body),timeout_seconds=.1)
        with pytest.raises(BridgeUnknown):peer.exchange({'sequence':1},{'ready':1})


def test_hung_read_does_not_hold_batch_until_ssh_returns():
    from table_acceptance_bridge import JsonPeer, BridgeUnknown
    done=threading.Event()
    class Reader:
        def readline(self):done.wait();return ''
    peer=JsonPeer(io.StringIO(),Reader(),timeout_seconds=.05)
    began=time.monotonic()
    try:
        with pytest.raises(BridgeUnknown):peer.exchange({'sequence':1},{'ready':1})
        assert time.monotonic()-began<.5
    finally:done.set()


def test_hung_write_is_bounded_and_never_retried():
    from table_acceptance_bridge import JsonPeer, BridgeUnknown
    done=threading.Event();calls=[]
    class Writer:
        def write(self,value):calls.append(1);done.wait()
        def flush(self):pass
    peer=JsonPeer(Writer(),io.StringIO('{"ready":1}\n'),timeout_seconds=.05)
    try:
        with pytest.raises(BridgeUnknown):peer.exchange({'sequence':1},{'ready':1})
        with pytest.raises(BridgeUnknown):peer.exchange({'sequence':1},{'ready':1})
        assert calls==[1]
    finally:done.set()


def test_successful_peer_requires_both_remote_challenges():
    from table_acceptance_bridge import JsonPeer, publish_snapshot
    output=io.StringIO();peer=JsonPeer(output,io.StringIO('{"ready":1}\n{"published":1}\n'))
    assert publish_snapshot(peer,{'sequence':1,'known':True},started_at=time.monotonic())
    import json
    frames=[json.loads(row) for row in output.getvalue().splitlines()]
    assert frames[0]=={'sequence':1,'known':True} and frames[1]['confirmed'] is True


def test_late_snapshot_is_explicitly_unknown_in_remote_confirmation():
    from table_acceptance_bridge import JsonPeer, publish_snapshot
    output=io.StringIO();peer=JsonPeer(output,io.StringIO('{"ready":1}\n{"published":1}\n'))
    assert publish_snapshot(peer,{'sequence':1,'known':True},started_at=time.monotonic()-2) is False
    assert '"confirmed":false' in output.getvalue()



def observation_states():
    return {'windows':{'known':True,'at_unix':100,'age_at_receipt_upper_s':.1,'embedding_pid':10,'embedding_birth':20,'audit_identity':{'id':1},'completed_requests':0},
        'primary':{'known':True,'at_unix':100,'age_at_receipt_upper_s':.1,'primary_generating':0},
        'host':{'at_unix':100,'model_connections':{'known':True,'own_established':1,'other_established':0}}}


@pytest.mark.parametrize('fault',['windows_stale','primary_unknown','host_stale','identity','primary_busy','foreign_model','counter'])
def test_live_observer_barrier_never_reuses_old_known_true(fault):
    from table_acceptance_bridge import live_observers_ready
    states=observation_states();identity={k:states['windows'][k] for k in ('embedding_pid','embedding_birth','audit_identity')}
    if fault=='windows_stale':states['windows']['at_unix']=90
    elif fault=='primary_unknown':states['primary']['known']=False
    elif fault=='host_stale':states['host']['at_unix']=90
    elif fault=='identity':states['windows']['embedding_birth']=21
    elif fault=='primary_busy':states['primary']['primary_generating']=1
    elif fault=='foreign_model':states['host']['model_connections']['other_established']=1
    else:states['windows']['completed_requests']=False
    assert live_observers_ready(states,identity,now_unix=100.5) is False


def test_live_barrier_allows_own_busy_model_peer_without_declaring_other_clients_idle():
    from table_acceptance_bridge import live_observers_ready
    states=observation_states();identity={k:states['windows'][k] for k in ('embedding_pid','embedding_birth','audit_identity')}
    assert live_observers_ready(states,identity,now_unix=100.5)
