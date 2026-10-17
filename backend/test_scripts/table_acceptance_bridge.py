"""Finite two-challenge transport for private numeric observer frames."""
import json
import queue
import threading
import time


class BridgeUnknown(RuntimeError):
    pass


class JsonPeer:
    def __init__(self, writer, reader, *, timeout_seconds=.75):
        self.writer = writer
        self.reader = reader
        self.timeout = timeout_seconds
        self.failed = False

    def exchange(self, payload, expected):
        if self.failed:
            raise BridgeUnknown('Failed observation transport cannot be reused')
        mailbox = queue.Queue(maxsize=1)
        def send_read():
            try:
                body = json.dumps(payload, allow_nan=False, separators=(',', ':')) + '\n'
                if len(body.encode()) > 4096:
                    raise BridgeUnknown('Observation frame exceeds finite pipe bound')
                self.writer.write(body)
                self.writer.flush()
                reply = self.reader.readline()
                if len(reply) > 4096 or json.loads(reply) != expected:
                    raise BridgeUnknown('Observation challenge identity unknown')
                mailbox.put(None)
            except Exception:
                mailbox.put(BridgeUnknown('Observation transport failed'))
        worker = threading.Thread(target=send_read, daemon=True)
        worker.start()
        try:
            error = mailbox.get(timeout=self.timeout)
        except queue.Empty:
            self.failed = True
            raise BridgeUnknown('Observation handshake exceeded finite deadline') from None
        if error is not None:
            self.failed = True
            raise error


def publish_snapshot(peer, state, *, started_at):
    sequence = state['sequence']
    peer.exchange(state, {'ready': sequence})
    duration = time.monotonic() - started_at
    confirmed = state['known'] is True and 0 <= duration <= 1
    peer.exchange({'sequence': sequence, 'confirmed': confirmed,
                   'query_to_challenge_s': duration}, {'published': sequence})
    return confirmed


def live_observers_ready(states, identity, *, now_unix):
    """Fresh independent state, even while our provider call occupies a slot.

    This is the abort barrier, not CPU/service qualification; the complete load
    monitor still reconciles actual own calls and sampled foreign busy work.
    """
    import math
    def number(value):
        return type(value) in (float, int) and math.isfinite(value)
    try:
        if not number(now_unix):return False
        for role, limit in (('windows', 2), ('primary', 2), ('host', 1.25)):
            row = states[role]
            at = row['at_unix']; upper = row.get('age_at_receipt_upper_s', 0)
            if (not number(at) or not number(upper) or upper < 0
                    or not 0 <= now_unix-at <= now_unix-at+upper <= limit
                    or row.get('known', True) is not True or 'error' in row):
                return False
        windows = states['windows']; primary = states['primary']
        if (any(windows[k] != identity[k] for k in ('embedding_pid', 'embedding_birth', 'audit_identity'))
                or type(windows['completed_requests']) is not int or windows['completed_requests'] < 0
                or type(primary['primary_generating']) is not int or primary['primary_generating'] != 0):
            return False
        peers = states['host']['model_connections']
        return (peers['known'] is True and type(peers['other_established']) is int
                and peers['other_established'] == 0 and type(peers['own_established']) is int
                and peers['own_established'] >= 0)
    except (KeyError, TypeError, ValueError):
        return False
