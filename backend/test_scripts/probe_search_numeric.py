"""One bounded c1/off-standard experiment; persists numeric samples and digests."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time
import urllib.request
from uuid import UUID

from probe_diagnostics_http import Client
from probe_diagnostics_http_matrix import QUERIES


def percentile(values, fraction=.95):
    return sorted(values)[int(fraction * (len(values) - 1))]


def numeric_state(postgres):
    query = 'SELECT wal_records,wal_bytes,wal_sync,wal_sync_time FROM pg_stat_wal'
    values = subprocess.check_output(['docker', 'exec', postgres, 'psql', '-U', 'okf', '-d', 'okf_knowledge', '-At', '-c', query], text=True).strip().split('|')
    result = dict(zip(('wal_records', 'wal_bytes', 'wal_sync', 'wal_sync_time_ms'), map(float, values)))
    for resource in ('io', 'cpu', 'memory'):
        for line in Path('/proc/pressure', resource).read_text().splitlines():
            kind, *fields = line.split()
            result[f'{resource}_{kind}_us'] = int(dict(part.split('=') for part in fields)['total'])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--postgres', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--warmup-seconds', type=float, default=30)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--min-requests', type=int, default=1000)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Preserve existing experiment')
    result = {'scenario': 'numeric-localization-c1-1doc-nozip', 'diagnostic_only': True,
              'warmup_seconds': args.warmup_seconds, 'seconds': args.seconds, 'rows': []}
    client = Client(args.base_url)
    client.login('demo.user')
    admins = []
    for index in range(3):
        admin = Client(args.base_url)
        admin.login(f'diag.bench{index + 1:02d}')
        admins.append(admin)
    active = None
    fingerprints = {}
    global_deadline = time.monotonic() + 900
    try:
        for index, mode in enumerate(('off', 'standard', 'off', 'standard', 'off', 'standard')):
            admin = admins[index // 2]
            if mode == 'standard':
                active = admin.call('POST', '/api/admin/diagnostics/sessions', {'scope': 'system', 'capture_level': 'standard', 'minutes': 5})['id']
            rows = []
            number = 0

            def one(measured):
                nonlocal number
                if time.monotonic() > global_deadline:
                    raise TimeoutError('Experiment budget exhausted')
                query_index = number % len(QUERIES)
                number += 1
                payload = {'query': QUERIES[query_index], 'locale': 'en', 'use_glossary': False, 'dense': False, 'bm25': True, 'top_k': 5}
                request = urllib.request.Request(args.base_url.rstrip('/') + '/api/search', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
                started_unix = time.time()
                started = time.perf_counter_ns()
                with client.opener.open(request, timeout=10) as response:
                    data = response.read()
                    profile_header = response.headers.get('x-diag-numeric-profile')
                    request_id = response.headers.get('x-request-id')
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                if not json.loads(data).get('hits'):
                    raise RuntimeError('Synthetic search missing hits')
                fingerprint = sha256(data).hexdigest()
                if fingerprint != fingerprints.setdefault(query_index, fingerprint):
                    raise RuntimeError('Synthetic response changed')
                if measured:
                    if str(UUID(request_id)) != request_id:
                        raise RuntimeError('Missing correlation')
                    profile = json.loads(profile_header)
                    required = {'backend_ms', 'qdrant_ms', 'hydration_ms', 'merge_ms', 'connection_ms', 'commit_ms', 'sql_ms', 'sql_count'}
                    if set(profile) != required or any(type(v) not in (int, float) or v < 0 for v in profile.values()) or profile['sql_count'] < 1:
                        raise RuntimeError('Incomplete numeric profile')
                    rows.append({'at_unix': started_unix, 'http_ms': elapsed, 'outside_backend_ms': elapsed - profile['backend_ms'], 'query_index': query_index, **profile})

            warmup_end = time.monotonic() + args.warmup_seconds
            while time.monotonic() < warmup_end:
                one(False)
            before = numeric_state(args.postgres)
            started = time.monotonic()
            while time.monotonic() - started < args.seconds or len(rows) < args.min_requests:
                one(True)
            elapsed = time.monotonic() - started
            after = numeric_state(args.postgres)
            fields = ('http_ms', 'backend_ms', 'outside_backend_ms', 'qdrant_ms', 'hydration_ms', 'merge_ms', 'connection_ms', 'commit_ms', 'sql_ms')
            summary = {key: {'p50': percentile([r[key] for r in rows], .5), 'p95': percentile([r[key] for r in rows]), 'max': max(r[key] for r in rows)} for key in fields}
            row = {'mode': mode, 'index': index + 1, 'requests': len(rows), 'seconds': elapsed, 'summary': summary,
                   'numeric_delta': {key: after[key] - before[key] for key in before}, 'samples': rows}
            result['rows'].append(row)
            args.output.write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps({key: row[key] for key in ('mode', 'index', 'requests', 'seconds', 'summary', 'numeric_delta')}), flush=True)
            if active:
                admin.call('POST', f'/api/admin/diagnostics/sessions/{active}/stop', {})
                active = None
        result['complete'] = True
        result['response_equality'] = True
        result['response_fingerprints'] = fingerprints
    finally:
        if active:
            admin.call('POST', f'/api/admin/diagnostics/sessions/{active}/stop', {})
        args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
