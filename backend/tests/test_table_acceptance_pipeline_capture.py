from types import SimpleNamespace
import pytest
from table_acceptance_pipeline_capture import (instrument_transport_calls, qdrant_transport_targets, model_other_busy, parse_model_connections, CompletionBoundary)

class Transport:
    def send(self, value=1):return value
    def fail(self):raise RuntimeError('failed request')


def test_duplicate_target_is_counted_once_and_restored():
    transport=Transport();original=transport.send;calls=[]
    with instrument_transport_calls([(transport,['send']),(transport,['send'])],lambda:calls.append(1)):
        assert transport.send(3)==3
    assert calls==[1] and transport.send==original


def test_failed_transport_is_not_claimed_completed():
    transport=Transport();original=transport.fail;calls=[]
    with instrument_transport_calls([(transport,['fail'])],lambda:calls.append(1)):
        with pytest.raises(RuntimeError):transport.fail()
    assert not calls and transport.fail==original


def test_partial_install_failure_restores_earlier_transport():
    transport=Transport();original=transport.send
    with pytest.raises(AttributeError):
        with instrument_transport_calls([(transport,['send','missing'])],lambda:None):pass
    assert transport.send==original


def test_all_grpc_pool_members_and_rest_transport_are_selected():
    rest=Transport();a=SimpleNamespace(Query=lambda:1);b=SimpleNamespace(Upsert=lambda:2);c=SimpleNamespace(Get=lambda:3)
    remote=SimpleNamespace(openapi_client=SimpleNamespace(client=rest),grpc_points=a,grpc_collections=c,_grpc_points_client_pool=[a,b],_grpc_collections_client_pool=[c])
    client=SimpleNamespace(_client=remote)
    targets=qdrant_transport_targets([client,client]);calls=[]
    with instrument_transport_calls(targets,lambda:calls.append(1)):
        rest.send();a.Query();b.Upsert();c.Get()
    assert calls==[1]*4


def test_absent_grpc_pool_is_unknown():
    rest=Transport();remote=SimpleNamespace(openapi_client=SimpleNamespace(client=rest),grpc_points=None,grpc_collections=None,_grpc_points_client_pool=None,_grpc_collections_client_pool=[])
    with pytest.raises(ValueError):qdrant_transport_targets([SimpleNamespace(_client=remote)])


def test_body_exception_restores_all_methods():
    transport=Transport();original=transport.send
    with pytest.raises(ValueError):
        with instrument_transport_calls([(transport,['send'])],lambda:None):raise ValueError('stop')
    assert transport.send==original


def test_idle_model_does_not_count_idle_foreign_connections():
    assert model_other_busy([{'is_processing':False}],0,0,None)==0


def test_busy_without_owned_call_is_foreign():
    assert model_other_busy([{'is_processing':True}],0,0,None)==1


def test_owned_call_requires_independent_matching_connection():
    sample={'own_established':1,'other_established':0,'age_s':0.1,'known':True}
    assert model_other_busy([{'is_processing':True}],1,0,sample)==0
    for change in [{'own_established':0},{'other_established':1},{'age_s':2},{'known':False}]:
        with pytest.raises(ValueError):model_other_busy([{'is_processing':True}],1,1,{**sample,**change})


def test_tcp_scopes_only_model_established_connections():
    header='  sl  local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n'
    rows='0: 18010A0A:1F90 2F010A0A:B120 01 0 0 0 0 0 0 1\n1: 18010A0A:1F90 22010A0A:B120 01 0 0 0 0 0 0 2\n2: 18010A0A:1F90 00000000:0000 0A 0 0 0 0 0 0 3\n3: 18010A0A:0016 22010A0A:B120 01 0 0 0 0 0 0 4\n'
    assert parse_model_connections(header+rows,header,own_peer='10.10.1.47')=={'own_established':1,'other_established':1,'known':True}


def test_ipv4_mapped_ipv6_peer_is_recognized():
    header='header\n';row='0: 0000000000000000FFFF000018010A0A:1F90 0000000000000000FFFF00002F010A0A:B120 01 0 0 0 0 0 0 1\n'
    assert parse_model_connections(header,header+row,own_peer='10.10.1.47')['own_established']==1


def test_selected_malformed_socket_is_unknown():
    with pytest.raises(ValueError):parse_model_connections('header\n0: 18010A0A:1F90 broken 01\n','header\n',own_peer='10.10.1.47')



def test_closed_boundary_excludes_later_snapshot_reads():
    counters={'Query':16,'Scroll':0};audit={'complete':True,'completed_requests':8}
    boundary=CompletionBoundary(lambda:counters,lambda:audit)
    boundary.close()
    counters['Scroll']+=114;audit['completed_requests']+=10
    assert boundary.snapshots()==({'Query':16,'Scroll':0},{'complete':True,'completed_requests':8})
    first=boundary.snapshots();first[0]['Scroll']=999
    assert boundary.snapshots()[0]['Scroll']==0


def test_boundary_must_close_and_cannot_be_closed_twice():
    boundary=CompletionBoundary(lambda:{'Query':1},lambda:{'complete':True})
    with pytest.raises(ValueError):boundary.snapshots()
    boundary.close()
    with pytest.raises(ValueError):boundary.close()


def test_failed_boundary_does_not_publish_partial_counters():
    def unavailable():raise OSError('audit unavailable')
    boundary=CompletionBoundary(lambda:{'Query':1},unavailable)
    with pytest.raises(OSError):boundary.close()
    with pytest.raises(ValueError):boundary.snapshots()
    with pytest.raises(ValueError):boundary.close()



def test_busy_owned_model_waits_for_next_peer_frame_and_returns_only_new_observation():
    from table_acceptance_pipeline_capture import sample_model_ownership
    rows=iter([([{'is_processing':True}],1,1,{'known':True,'own_established':0,'other_established':0,'age_s':.1}),
        ([{'is_processing':True}],1,1,{'known':True,'own_established':1,'other_established':0,'age_s':.01})])
    ticks=[0.0];waits=[]
    def wait(delay):ticks[0]+=delay;waits.append(delay)
    assert sample_model_ownership(lambda:next(rows),clock=lambda:ticks[0],wait=wait)==0
    assert waits


@pytest.mark.parametrize('fault',['foreign_peer','stale','foreign_slot'])
def test_model_peer_wait_does_not_reclassify_foreign_or_stale_evidence(fault):
    from table_acceptance_pipeline_capture import sample_model_ownership
    peers={'known':True,'own_established':0,'other_established':0,'age_s':.1}
    if fault=='foreign_peer':peers['other_established']=1
    elif fault=='stale':peers['age_s']=2
    if fault=='foreign_slot':
        assert sample_model_ownership(lambda:([{'is_processing':True}],0,0,peers),wait=lambda _:pytest.fail('must not wait'))==1
    else:
        with pytest.raises(ValueError):sample_model_ownership(lambda:([{'is_processing':True}],1,1,peers),wait=lambda _:pytest.fail('must not wait'))


def test_model_ownership_that_never_arrives_remains_unknown_with_finite_bound():
    from table_acceptance_pipeline_capture import sample_model_ownership
    ticks=[0.0]
    def wait(delay):ticks[0]+=delay
    def probe():return ([{'is_processing':True}],1,1,{'known':True,'own_established':0,'other_established':0,'age_s':.1})
    with pytest.raises(ValueError):sample_model_ownership(probe,clock=lambda:ticks[0],wait=wait)
    assert ticks[0]<=.5


@pytest.mark.parametrize('fault',['status','timeout','unread','wrong_endpoint'])
def test_only_fully_received_native_http_error_is_claimed_completed(fault):
 import httpx
 from table_acceptance_pipeline_capture import completed_ollama_http_error
 request=httpx.Request('POST','http://127.0.0.1:1/api/embed' if fault!='wrong_endpoint' else 'http://127.0.0.1:1/other')
 response=httpx.Response(413,request=request,content=b'physical batch rejected')
 if fault=='unread':response=httpx.Response(413,request=request,stream=httpx.ByteStream(b'not received'))
 error=httpx.ReadTimeout('not completed',request=request) if fault=='timeout' else httpx.HTTPStatusError('rejected',request=request,response=response)
 calls=[]
 def failed():raise error
 transport=SimpleNamespace(call=failed)
 with instrument_transport_calls([(transport,['call'])],lambda:calls.append(1),completed_error=completed_ollama_http_error):
  with pytest.raises(type(error)):transport.call()
 assert calls==([1] if fault=='status' else []) and transport.call==failed
