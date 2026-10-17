"""Read-only load observations for table benchmarks; unknown is never idle.

A foreign embedding connection is a conservative upper bound on foreign
requests. Idle pooled connections are counted too. Samples cannot exclude a
transient client between polls; preserve the interval and raw observations.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import sys


class LoadUnknown(ValueError):
    """Telemetry is missing, reset, inconsistent or unsupported."""


_METRIC = re.compile(r'^(rest_responses_total|grpc_responses_total)\{(.*)\}\s+(\S+)$')
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z_0-9]*)="((?:[^"\\]|\\.)*)"(?:,|$)')


def _counter(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def parse_qdrant_request_counters(body):
    counters = {}
    if not isinstance(body, str):
        raise LoadUnknown('Qdrant metrics are unavailable')
    for line in body.splitlines():
        if not line.startswith(('rest_responses_total', 'grpc_responses_total')):
            continue
        match = _METRIC.fullmatch(line)
        if match is None:
            raise LoadUnknown('Malformed Qdrant request counter')
        labels, offset = {}, 0
        while offset < len(match[2]):
            label = _LABEL.match(match[2], offset)
            if label is None or label[1] in labels:
                raise LoadUnknown('Malformed Qdrant counter labels')
            try:
                labels[label[1]] = json.loads('"' + label[2] + '"')
            except ValueError as exc:
                raise LoadUnknown('Malformed Qdrant counter label value') from exc
            offset = label.end()
        try:
            value = float(match[3])
        except ValueError as exc:
            raise LoadUnknown('Unknown Qdrant request count') from exc
        if not math.isfinite(value) or value < 0 or not value.is_integer():
            raise LoadUnknown('Invalid Qdrant cumulative request count')
        if not labels.get('endpoint') or 'status' not in labels:
            raise LoadUnknown('Missing Qdrant request identity')
        if labels['endpoint'] == '/metrics':
            continue
        identity = (match[1], tuple(sorted(labels.items())))
        if identity in counters:
            raise LoadUnknown('Duplicate Qdrant cumulative request counter')
        counters[identity] = int(value)
    if not counters:
        raise LoadUnknown('No Qdrant request counters observed')
    return counters



def parse_qdrant_telemetry(body):
    """Use full REST/gRPC counters; Prometheus may omit metadata endpoints."""
    try:
        requests = body['result']['requests']
        if not isinstance(requests, dict):
            raise TypeError('Invalid request telemetry')
        counters = {}
        for family in ('rest', 'grpc'):
            responses = requests[family]['responses']
            if not isinstance(responses, dict):
                raise TypeError('Invalid response counters')
            for endpoint, statuses in responses.items():
                if (not isinstance(endpoint, str) or not endpoint
                        or not isinstance(statuses, dict) or not statuses):
                    raise TypeError('Invalid endpoint/status identity')
                for status, counter in statuses.items():
                    if (not isinstance(status, str) or not status.isdigit()
                            or not isinstance(counter, dict) or not _counter(counter.get('count'))):
                        raise TypeError('Invalid completed request counter')
                    if family == 'rest' and endpoint in ('GET /metrics', 'GET /telemetry'):
                        continue
                    counters[(family, endpoint, status)] = counter['count']
        if not counters:
            raise TypeError('Completed request coverage is absent')
        return counters
    except (KeyError, TypeError) as exc:
        raise LoadUnknown('Incomplete or malformed full Qdrant telemetry') from exc

def foreign_qdrant_requests(before, after, owned_completed_calls):
    if not isinstance(before, dict) or not isinstance(after, dict) or not before or not _counter(owned_completed_calls):
        raise LoadUnknown('Incomplete Qdrant ownership observation')
    if any(key not in after or not _counter(value) or not _counter(after[key]) or after[key] < value
           for key, value in before.items()) or any(not _counter(v) for v in after.values()):
        raise LoadUnknown('Qdrant counter reset or disappeared')
    delta = sum(after.values()) - sum(before.values())
    if delta < owned_completed_calls:
        raise LoadUnknown('Qdrant response counts have not reconciled with completed owned calls')
    return delta - owned_completed_calls


def cpu_percent(before, after):
    if not all(isinstance(v, (tuple, list)) and len(v) == 2 and all(_counter(n) for n in v) for v in (before, after)):
        raise LoadUnknown('Unknown CPU times')
    idle, total = after[0] - before[0], after[1] - before[1]
    if total <= 0 or idle < 0 or idle > total:
        raise LoadUnknown('CPU counters reset or interval is absent')
    return 100 * (total - idle) / total


def foreign_embedding_connections(rows, owned_pids, embedding_port):
    if not isinstance(rows, list) or not isinstance(owned_pids, set) or not 1 <= embedding_port <= 65535:
        raise LoadUnknown('Embedding connection ownership is unknown')
    count = 0
    for row in rows:
        if not isinstance(row, dict) or any(not _counter(row.get(key)) for key in ('state', 'local_port', 'remote_port', 'owner_pid')):
            raise LoadUnknown('TCP observation is incomplete')
        if row['remote_port'] == embedding_port and row['state'] not in (1, 2, 11, 12) and row['owner_pid'] not in owned_pids:
            count += 1
    return count


def active_model_slots(slots):
    if not isinstance(slots, list) or not slots or any(not isinstance(s, dict) or not isinstance(s.get('is_processing'), bool) for s in slots):
        raise LoadUnknown('Model slot state is unavailable')
    return sum(s['is_processing'] for s in slots)


def native_cpu_times():
    """Return cumulative idle/total time; Windows kernel time already has idle."""
    if sys.platform != 'win32':
        raise LoadUnknown('Native CPU qualification is currently implemented for Windows only')
    import ctypes
    from ctypes import wintypes as W
    idle, kernel, user = W.FILETIME(), W.FILETIME(), W.FILETIME()
    get_times = ctypes.windll.kernel32.GetSystemTimes
    get_times.argtypes = [ctypes.POINTER(W.FILETIME)] * 3
    get_times.restype = W.BOOL
    if not get_times(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        raise LoadUnknown('Windows CPU observation failed')
    def number(value):
        return (value.dwHighDateTime << 32) | value.dwLowDateTime
    return number(idle), number(kernel) + number(user)


def native_tcp_rows():
    """Read IPv4 TCP owners without shell commands or process interruption."""
    if sys.platform != 'win32':
        raise LoadUnknown('Native TCP qualification is currently implemented for Windows only')
    import ctypes
    from ctypes import wintypes as W
    class Row(ctypes.Structure):
        _fields_ = [(name, W.DWORD) for name in ('state', 'local_address', 'local_port', 'remote_address', 'remote_port', 'owner_pid')]
    read_table = ctypes.windll.iphlpapi.GetExtendedTcpTable
    read_table.argtypes = [ctypes.c_void_p, ctypes.POINTER(W.DWORD), W.BOOL, W.ULONG, W.ULONG, W.ULONG]
    read_table.restype = W.DWORD
    size = W.DWORD(0)
    if read_table(None, ctypes.byref(size), False, 2, 5, 0) not in (0, 122):
        raise LoadUnknown('TCP owner-table size is unavailable')
    # The connection count may grow between calls; bounded retries preserve unknown.
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        status = read_table(buffer, ctypes.byref(size), False, 2, 5, 0)
        if status == 122:
            continue
        if status != 0:
            raise LoadUnknown('TCP owner-table read failed')
        count = ctypes.cast(buffer, ctypes.POINTER(W.DWORD)).contents.value
        if ctypes.sizeof(W.DWORD) + count * ctypes.sizeof(Row) > len(buffer):
            raise LoadUnknown('TCP owner-table is truncated')
        rows = (Row * count).from_buffer(buffer, ctypes.sizeof(W.DWORD))
        return [{'state': row.state, 'local_port': socket.ntohs(row.local_port & 65535),
                 'remote_port': socket.ntohs(row.remote_port & 65535), 'owner_pid': row.owner_pid} for row in rows]
    raise LoadUnknown('TCP owner-table changed during bounded reads')


def native_embedding_bound(port):
    return foreign_embedding_connections(native_tcp_rows(), {os.getpid()}, port)


_GIN = re.compile(r'^\[GIN\]\s+\d{4}/\d{2}/\d{2}\s+-\s+\d{2}:\d{2}:\d{2}\s+\|\s*(\d{3})\s*\|\s*[^|]+\|\s*[^|]+\|\s*([A-Z]+)\s+"([^"\n]+)"\s*$')
_EMBEDDING_ROUTES = {'/api/embed', '/api/embeddings', '/v1/embeddings'}


def parse_embedding_requests(body):
    """Count completed Ollama HTTP requests, not claimed global in-flight state."""
    if not isinstance(body, str):
        raise LoadUnknown('Embedding request audit is unavailable')
    count = 0
    for line in body.splitlines():
        if not line.startswith('[GIN]'):
            continue
        match = _GIN.fullmatch(line)
        if match is None:
            if any(route in line for route in _EMBEDDING_ROUTES):
                raise LoadUnknown('Embedding request audit record is malformed')
            continue
        if match[3].split('?', 1)[0] in _EMBEDDING_ROUTES:
            if not 100 <= int(match[1]) <= 599:
                raise LoadUnknown('Embedding request status is invalid')
            count += 1
    return count


def _log_cursor(stream, offset):
    info = os.fstat(stream.fileno())
    stream.seek(max(0, offset - 256))
    anchor = stream.read(min(256, offset))
    return {'device': info.st_dev, 'inode': info.st_ino, 'offset': offset,
            'anchor_sha256': hashlib.sha256(anchor).hexdigest()}


def embedding_log_boundary(path):
    """Pin the append-only audit file before calibration/warmup; never rewrite it."""
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2); offset = stream.tell()
            if offset:
                stream.seek(offset - 1)
                if stream.read(1) != b'\n':
                    raise LoadUnknown('Embedding audit boundary has a partial record')
            return _log_cursor(stream, offset)
    except OSError as exc:
        raise LoadUnknown('Embedding request audit file is unavailable') from exc


def read_embedding_log_delta(path, cursor):
    """Retain the append position and reject rotation, truncation or lost records."""
    if not isinstance(cursor, dict) or not _counter(cursor.get('offset')):
        raise LoadUnknown('Embedding audit cursor is invalid')
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2); end = stream.tell()
            start = cursor['offset']
            if end < start or end - start > 4 * 1024 * 1024 or _log_cursor(stream, start) != cursor:
                raise LoadUnknown('Embedding audit was rotated, rewritten or exceeded the bounded window')
            stream.seek(start); data = stream.read(end - start)
            consumed = data.rfind(b'\n') + 1
            complete = not data or data.endswith(b'\n')
            try:
                body = data[:consumed].decode('utf-8')
            except UnicodeError as exc:
                raise LoadUnknown('Embedding audit is not valid UTF-8') from exc
            return {'completed_requests': parse_embedding_requests(body), 'complete': complete,
                    'cursor': _log_cursor(stream, start + consumed)}
    except OSError as exc:
        raise LoadUnknown('Embedding request audit read failed') from exc


def validate_resource_load_coverage(load, rss):
    """Check raw both-host/direct evidence against the complete RSS lifecycle.

    Pair booleans cannot certify a shortened run-only window. Clock anchors come
    from the measured child; independent Unix observer intervals must cover it.
    """
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    try:
        window = load['window']
        start, end = rss['window_start'], rss['window_end']
        sm, em = window['start_monotonic'], window['end_monotonic']
        su, eu = window['start_unix'], window['end_unix']
        if (load.get('load_monitor_complete') is not True
                or not all(number(n) for n in (start, end, sm, em, su, eu))
                or not sm <= start < end <= em
                or abs((eu - su) - (em - sm)) > .05):
            raise LoadUnknown('Full lifecycle load window or clock anchors unknown')
        first, last = su + start - sm, su + end - sm
        times = [row['at_unix'] for row in load['load_samples']]
        if (not times or any(not number(t) for t in times)
                or times[0] - first > 1.25 or last - times[-1] > 1.25
                or any(not 0 < b-a <= 1.25 for a,b in zip(times,times[1:]))):
            raise LoadUnknown('Full lifecycle direct load observation gap')
        for key in ('host_cpu_samples', 'windows_cpu_samples'):
            rows = load[key]
            if not rows or rows[0]['start_unix'] > first or rows[-1]['end_unix'] < last:
                raise LoadUnknown('Full lifecycle both-host CPU coverage incomplete')
            previous = None
            for row in rows:
                a, b = row['start_unix'], row['end_unix']
                if (not number(a) or not number(b) or not 0 < b-a <= 1.25
                        or previous is not None and a != previous):
                    raise LoadUnknown('Full lifecycle CPU interval gap or order unknown')
                previous = b
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, LoadUnknown):
            raise
        raise LoadUnknown('Full lifecycle load coverage absent or malformed') from error
    return True
