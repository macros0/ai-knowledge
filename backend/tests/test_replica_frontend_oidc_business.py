"""TLS transport/business boundary, with actual loopback wire observations."""
import importlib
import importlib.util
import json
import time

import pytest

from tests.test_replica_frontend_oidc_http import APP, IDP, SECRET, client
from tests.test_replica_frontend_oidc_http import gateway as gateway


def module():
    name='test_scripts.replica_frontend_oidc_business'
    assert importlib.util.find_spec(name) is not None, 'Owned Gateway TLS business transport missing'
    return importlib.import_module(name)


def business(tmp_path,gateway):
    return module().GatewayFrontendHTTP(client(tmp_path,gateway))


def test_tls_response_context_does_not_buffer_stream_before_first_frame(tmp_path,gateway):
    probe=client(tmp_path,gateway);probe.login(SECRET)
    assert hasattr(probe,'open_response'), 'Bounded TLS response context missing'
    start=time.monotonic()
    with probe.open_response('POST',APP+'/api/chat/stream',body=b'{}',headers={'X-CSRF-Token':'private-csrf'}) as response:
        assert response.status==200 and json.loads(response.readline())['type']=='sources'
        first=time.monotonic()-start
        assert first<1
        assert b'"result"' in response.read()
    assert time.monotonic()-start>first+2


@pytest.mark.parametrize('reader',['read','readline'])
def test_tls_response_context_enforces_total_bound_even_without_caller_limit(tmp_path,gateway,reader):
    probe=client(tmp_path,gateway);gateway['state']['mode']='oversized'
    with probe.open_response('GET',IDP+'/realms/okf-home/protocol/openid-connect/auth') as response:
        assert len(response.read(128))==128
        with pytest.raises(ValueError):getattr(response,reader)()


def test_gateway_business_session_upload_export_and_exact_slot_routes(tmp_path,gateway):
    http=business(tmp_path,gateway)
    proof=http.session(SECRET)
    assert proof['csrf']=='verified-real-sso' and proof['shared_routes']==3 and proof['gateway_route_kind']=='two-slots-and-service'
    document=http.upload();assert document['document_id']=='a'*16
    assert http.publish()=={'status':'passed','verified_routes':3}
    assert http.export()=={'job_id':7}
    assert http.export_observe()['status']=='passed'
    calls=gateway['state']['business_calls']
    assert {c['slot'] for c in calls if c['path'].endswith('/download')}=={'first','second',None}
    assert all(c['csrf']=='private-csrf' for c in calls if c['path'] in ['/api/search','/api/documents','/api/documents/bulk-export'])
    assert http.logout()['old_session_revoked_routes']==3
    assert {c['slot'] for c in calls[-3:]}=={'first','second',None}
    assert not any(c['signed_in'] for c in calls[-3:])


def test_gateway_streams_deliver_first_frame_through_both_slots(tmp_path,gateway):
    http=business(tmp_path,gateway);http.session(SECRET)
    proof=http.stream()
    assert proof['unbuffered_frontends']==2
    assert all(s['first_event_seconds']+2<s['total_seconds'] for s in proof['streams'])
    calls=[c for c in gateway['state']['business_calls'] if c['path']=='/api/chat/stream']
    assert [c['slot'] for c in calls]==['first','second']


def test_buffered_gateway_stream_refused(tmp_path,gateway):
    http=business(tmp_path,gateway);http.session(SECRET);gateway['state']['mode']='buffered'
    with pytest.raises(ValueError):http.stream()
    assert not http.files.path('stream.result.json').exists()


def test_loss_stream_reports_delivered_sources_and_eof_without_model_replay(tmp_path,gateway):
    http=business(tmp_path,gateway);http.session(SECRET);gateway['state']['mode']='interrupted'
    progress=[];value=http.loss_stream(progress.append)
    assert progress[0]['sources_seen'] and not progress[0]['result_seen']
    assert value['sources_seen'] and not value['result_seen'] and value['terminal']
    http=business(tmp_path,gateway)
    with pytest.raises(ValueError):http.loss_stream(progress.append)
    assert gateway['state']['stream_posts']==1


@pytest.mark.parametrize('mode',['malformed-stream','unknown-event'])
def test_protocol_error_cannot_be_reported_as_only_a_transport_loss(tmp_path,gateway,mode):
    http=business(tmp_path,gateway);http.session(SECRET);gateway['state']['mode']=mode
    proof=http.loss_stream(lambda value:None)
    assert proof.get('protocol_error') is True
    assert proof.get('transport_interrupted') is False


def test_progress_io_failure_is_not_misclassified_as_a_network_interruption(tmp_path,gateway):
    http=business(tmp_path,gateway);http.session(SECRET)
    def progress(value):
        if value['sources_seen'] and not value['terminal']:raise OSError('Synthetic receipt disk failure')
    proof=http.loss_stream(progress)
    assert proof['protocol_error'] is True and proof['transport_interrupted'] is False


@pytest.mark.parametrize('headers',[{'Cookie':'foreign'},{'hOsT':'foreign'},{'X-Okf-Probe-Slot':'foreign'},{'X-CSRF-Token':'foreign'}])
def test_caller_cannot_override_cookie_host_slot_or_csrf(tmp_path,gateway,headers):
    http=business(tmp_path,gateway);http.session(SECRET);before=len(gateway['state']['calls'])
    with pytest.raises(ValueError):http.request(0,'/api/search',data={},headers=headers)
    assert len(gateway['state']['calls'])==before


@pytest.mark.parametrize('path',['http://customer.invalid','//customer.invalid/api','/api/documents#fragment','../api/search'])
def test_business_path_cannot_escape_pinned_origin(tmp_path,gateway,path):
    http=business(tmp_path,gateway);http.session(SECRET);before=len(gateway['state']['calls'])
    with pytest.raises(ValueError):http.request(0,path)
    assert len(gateway['state']['calls'])==before


def test_plain_cookie_injection_route_is_not_supported(tmp_path,gateway):
    http=business(tmp_path,gateway);http.session(SECRET);before=len(gateway['state']['calls'])
    with pytest.raises(ValueError):http.request(0,'/api/auth/me',plain=True)
    assert len(gateway['state']['calls'])==before
