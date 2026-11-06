"""Manual loopback-only training backup-alert receiver; no scheduler or external delivery."""
import argparse
import hmac
import json
import os
import socket
import sqlite3
import stat
import sys
import threading
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_alert_protocol import (
    MAX_BODY,
    ProtocolError,
    canonical,
    make_ack,
    require_key,
    verify_event,
)


def _regular_path(path, *, exists):
    value = Path(path).absolute()
    if value.parent.resolve() != value.parent or not value.parent.is_dir() or value.is_symlink():
        raise ValueError('Existing private regular inbox path required')
    if exists:
        metadata = value.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or getattr(metadata, 'st_file_attributes', 0) & 0x400):
            raise ValueError('Existing private regular inbox path required')
    return value


class Inbox:
    """One protected local database; an arbitrary existing SQLite file is foreign."""
    def __init__(self, path, key):
        self.path = _regular_path(path, exists=Path(path).exists())
        self.key_binding = hmac.new(require_key(key), b'okf-backup-alert-inbox-v1', sha256).hexdigest()
        created = False
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            created = True
        self.identity = (self.path.stat().st_dev, self.path.stat().st_ino)
        with self.connect() as connection:
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE')
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            if created:
                if tables:
                    raise ValueError('New inbox is not empty')
                connection.execute('CREATE TABLE receiver_scope (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, key_binding TEXT NOT NULL)')
                connection.execute('CREATE TABLE events (event_id TEXT PRIMARY KEY, body_sha256 TEXT NOT NULL, payload BLOB NOT NULL, received_at REAL NOT NULL)')
                connection.execute('INSERT INTO receiver_scope VALUES (1,1,?)', (self.key_binding,))
                connection.execute('PRAGMA user_version=1')
            else:
                if tables != [('events',), ('receiver_scope',)] or connection.execute('PRAGMA user_version').fetchone() != (1,):
                    raise ValueError('Existing inbox is foreign or incomplete')
                scope = connection.execute('SELECT id,version,key_binding FROM receiver_scope').fetchall()
                if (len(scope) != 1 or scope[0][:2] != (1,1)
                        or not hmac.compare_digest(scope[0][2], self.key_binding)
                        or [row[1] for row in connection.execute('PRAGMA table_info(events)')] != ['event_id','body_sha256','payload','received_at']):
                    raise ValueError('Existing inbox is foreign or incomplete')
            connection.commit()
        self.assert_identity()

    def assert_identity(self):
        value = _regular_path(self.path, exists=True)
        if (value.stat().st_dev, value.stat().st_ino) != self.identity:
            raise ValueError('Inbox file identity changed')

    def connect(self):
        self.assert_identity()
        return sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True, timeout=5)

    def accept(self, event, body):
        digest = sha256(body).hexdigest()
        with self.connect() as connection:
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE')
            previous = connection.execute('SELECT body_sha256,payload FROM events WHERE event_id=?', (event.event_id,)).fetchone()
            if previous:
                if type(previous[1]) is not bytes or not hmac.compare_digest(sha256(previous[1]).hexdigest(), previous[0]):
                    raise ValueError('Persisted inbox receipt is corrupt')
                if not hmac.compare_digest(previous[0], digest):
                    raise FileExistsError('Notification ID belongs to different bytes')
                status = 'duplicate'
            else:
                connection.execute('INSERT INTO events VALUES (?,?,?,?)', (event.event_id,digest,body,time.time()))
                status = 'accepted'
            connection.commit()
        self.assert_identity()
        return status, digest

    def count(self):
        with self.connect() as connection:
            return connection.execute('SELECT count(*) FROM events').fetchone()[0]


class ReceiverHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, _format, *_args):
        pass  # Never log untrusted request paths, headers or bodies.

    def _send_json(self, status, body):
        payload = canonical(body)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def send_error(self, code, message=None, explain=None):
        self._send_json(code, {'error': 'request_refused'})

    def do_GET(self):
        if self.path != '/health':
            return self._send_json(404, {'error': 'not_found'})
        try:
            return self._send_json(200, {'status': 'ready', 'scope': 'owned-lab', 'events': self.server.inbox.count()})
        except (OSError, sqlite3.Error, ValueError):
            return self._send_json(503, {'error': 'receiver_unavailable'})

    def do_POST(self):
        if self.path != '/v1/backup-alerts':
            return self._send_json(404, {'error': 'not_found'})
        try:
            length = self.headers.get('Content-Length')
            if self.headers.get('Transfer-Encoding') or length is None or not length.isdecimal() or len(length) > 6:
                return self._send_json(400, {'error': 'request_refused'})
            length = int(length)
            if not 0 < length <= MAX_BODY:
                return self._send_json(413, {'error': 'body_too_large'})
            if self.headers.get_content_type() != 'application/json':
                return self._send_json(400, {'error': 'request_refused'})
            body = self.rfile.read(length)
            if len(body) != length:
                return self._send_json(400, {'error': 'request_refused'})
            event = verify_event(body, self.headers, self.server.signing_key, now=time.time())
            status, digest = self.server.inbox.accept(event, body)
            acknowledgement = make_ack(event.event_id, digest, status, self.server.signing_key)
            return self._send_json(201 if status == 'accepted' else 200, acknowledgement)
        except ProtocolError:
            return self._send_json(401, {'error': 'authentication_refused'})
        except FileExistsError:
            return self._send_json(409, {'error': 'event_id_conflict'})
        except (OSError, sqlite3.Error, ValueError):
            return self._send_json(503, {'error': 'receiver_unavailable'})


class ReceiverServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # A failed response cannot expose a raw traceback/request.


def create_server(*, host, port, key, inbox_path, handler_class=ReceiverHandler):
    if host != '127.0.0.1' or type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Explicit IPv4 loopback receiver required')
    require_key(key)
    server = ReceiverServer((host,port), handler_class)
    try:
        server.signing_key = key
        server.inbox = Inbox(inbox_path, key)
    except Exception:  # noqa: BLE001 -- close the listener and redact private SQLite/path errors.
        server.server_close()
        raise ValueError('Receiver inbox is unavailable or foreign') from None
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='127.0.0.1', choices=['127.0.0.1'])
    parser.add_argument('--port', type=int, default=16491)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--inbox', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if not 15000 < args.port <= 65535:
            raise ValueError('Explicit non-system loopback port required')
        path = _regular_path(args.key_file, exists=True)
        with path.open('rb') as stream:
            key = require_key(stream.read(33))
        server = create_server(host=args.bind, port=args.port, key=key, inbox_path=args.inbox)
        print('BACKUP_ALERT_RECEIVER_READY:'+json.dumps({'scope':'owned-lab','address':args.bind,'port':args.port}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return 0
    except Exception as exc:  # noqa: BLE001 -- public CLI emits only a fixed error class.
        print(json.dumps({'receiver_refused':type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
