"""Real loopback HTTP receiver persists before ACK and deduplicates durable events."""
from contextlib import contextmanager
import importlib
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time

import httpx
import pytest

from tests.test_replica_backup_alert_protocol import KEY, event, protocol


def receiver():
    directory = Path(__file__).resolve().parents[2] / 'scripts/kubernetes'
    sys.path.insert(0, str(directory))
    assert importlib.util.find_spec('replica_backup_alert_receiver') is not None, 'Durable local backup-alert receiver is missing'
    return importlib.import_module('replica_backup_alert_receiver')


@contextmanager
def running(tmp_path, *, handler=None):
    tool = receiver()
    options = {'handler_class': handler} if handler else {}
    server = tool.create_server(host='127.0.0.1', port=0, key=KEY, inbox_path=tmp_path/'inbox.sqlite', **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, 'http://127.0.0.1:'+str(server.server_address[1]), tmp_path/'inbox.sqlite'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def post(client, base, sent, *, key=KEY, path='/v1/backup-alerts'):
    tool = protocol()
    body = tool.encode_event(sent)
    headers = {**tool.sign_event(body, key, timestamp=int(time.time())), 'Content-Type':'application/json'}
    return client.post(base+path, content=body, headers=headers)


def test_actual_receiver_persists_and_replay_returns_one_row(tmp_path):
    tool = protocol()
    sent = event(tool)
    with running(tmp_path) as (_, base, inbox), httpx.Client(trust_env=False, timeout=5) as client:
        first, second = post(client, base, sent), post(client, base, sent)
        assert first.status_code == 201 and second.status_code == 200
        assert tool.verify_ack(sent, first.json(), KEY) and tool.verify_ack(sent, second.json(), KEY)
        with sqlite3.connect(inbox) as connection:
            assert connection.execute('SELECT count(*) FROM events').fetchone()[0] == 1
        assert client.get(base+'/health').json()['events'] == 1


def test_same_id_different_authenticated_body_conflicts(tmp_path):
    tool = protocol()
    sent = event(tool)
    changed = sent.model_copy(update={'health': sent.health.model_copy(update={'recovery_point_age_seconds':90000.0})})
    with running(tmp_path) as (_, base, inbox), httpx.Client(trust_env=False, timeout=5) as client:
        assert post(client, base, sent).status_code == 201
        assert post(client, base, changed).status_code == 409
        with sqlite3.connect(inbox) as connection:
            assert connection.execute('SELECT count(*) FROM events').fetchone()[0] == 1


def test_lost_response_after_commit_is_reconciled_without_duplicate(tmp_path):
    tool = protocol()
    implementation = receiver()
    class LoseFirstAck(implementation.ReceiverHandler):
        lost = False
        def _send_json(self, status, body):
            if status == 201 and not type(self).lost:
                type(self).lost = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            return super()._send_json(status, body)
    sent = event(tool)
    with running(tmp_path, handler=LoseFirstAck) as (_, base, inbox), httpx.Client(trust_env=False, timeout=5) as client:
        with pytest.raises(httpx.RemoteProtocolError): post(client, base, sent)
        with sqlite3.connect(inbox) as connection:
            assert connection.execute('SELECT count(*) FROM events').fetchone()[0] == 1
        second = post(client, base, sent)
        assert second.status_code == 200 and tool.verify_ack(sent, second.json(), KEY)


@pytest.mark.parametrize('fault', ['key', 'path', 'missing_signature', 'oversized'])
def test_unauthenticated_or_out_of_contract_request_never_persists(tmp_path, fault):
    tool = protocol()
    with running(tmp_path) as (_, base, inbox), httpx.Client(trust_env=False, timeout=5) as client:
        if fault == 'missing_signature': reply = client.post(base+'/v1/backup-alerts', json={'secret':'PRIVATE_CANARY'})
        elif fault == 'oversized': reply = client.post(base+'/v1/backup-alerts', content=b'X'*8193)
        else: reply = post(client, base, event(tool), key=b'j'*32 if fault=='key' else KEY,
            path='/unexpected' if fault=='path' else '/v1/backup-alerts')
        assert reply.status_code in {400,401,404,413}
        assert 'PRIVATE_CANARY' not in reply.text
        with sqlite3.connect(inbox) as connection:
            assert connection.execute('SELECT count(*) FROM events').fetchone()[0] == 0


@pytest.mark.parametrize('host', ['0.0.0.0', 'localhost', '::', '10.10.1.52'])
def test_receiver_cannot_bind_non_loopback_or_ambiguous_dns(tmp_path, host):
    tool = receiver()
    with pytest.raises(ValueError):
        tool.create_server(host=host, port=0, key=KEY, inbox_path=tmp_path/'inbox.sqlite')


def test_existing_foreign_database_is_never_adopted_or_changed(tmp_path):
    tool = receiver()
    path = tmp_path/'foreign.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE private_fixture (id integer)')
        connection.execute('INSERT INTO private_fixture VALUES (7)')
    with pytest.raises(ValueError):
        tool.create_server(host='127.0.0.1', port=0, key=KEY, inbox_path=path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('private_fixture',)]
        assert connection.execute('SELECT id FROM private_fixture').fetchone() == (7,)


def test_existing_inbox_requires_the_original_signing_key(tmp_path):
    tool = receiver()
    with running(tmp_path):
        pass
    with pytest.raises(ValueError):
        tool.create_server(host='127.0.0.1', port=0, key=b'j'*32, inbox_path=tmp_path/'inbox.sqlite')


def test_corrupted_persisted_payload_never_returns_duplicate_ack(tmp_path):
    tool = protocol()
    sent = event(tool)
    with running(tmp_path) as (_, base, inbox), httpx.Client(trust_env=False, timeout=5) as client:
        assert post(client, base, sent).status_code == 201
        with sqlite3.connect(inbox) as connection:
            connection.execute('UPDATE events SET payload=? WHERE event_id=?', (b'corrupt-receipt',sent.event_id))
        assert post(client, base, sent).status_code == 503
