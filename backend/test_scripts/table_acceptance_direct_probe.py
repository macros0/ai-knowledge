"""Read-only PG/model/primary evidence acquired by the independent observer."""
import json
import math
from pathlib import Path
import time
import requests
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from table_acceptance_load import LoadUnknown
from table_acceptance_pipeline_capture import sample_model_ownership


def install_connection_limits(engine):
    @event.listens_for(engine, 'connect')
    def limits(connection, record):
        original = connection.autocommit
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout='2s'")
        finally:
            connection.autocommit = original


class DirectServiceProbe:
    def __init__(self, config):
        self.config = config
        url = make_url(config['database_url'])
        if (url.get_backend_name() != 'postgresql' or url.host != '127.0.0.1'
                or url.port != 15432 or not (url.database or '').startswith('r2_')):
            raise LoadUnknown('Direct probe requires isolated private PG')
        self.engine = create_engine(config['database_url'], connect_args={
            'connect_timeout': 5, 'application_name': 'r2-benchmark-monitor'})
        install_connection_limits(self.engine)
        self.model_started_count = self.model_finished_count = self.model_inflight = 0
        self.model_event_base = None
        self.model_final_reconciled = False
        self.model_reconciliation = None
        self.last_model_event_frame = None

    def read(self, role):
        return json.loads(Path(self.config['paths'][role]).read_text())

    @staticmethod
    def age(state):
        age = time.time() - state['at_unix'] + state['age_at_receipt_upper_s']
        if type(age) not in (float, int) or not math.isfinite(age) or not 0 <= age <= 2:
            raise LoadUnknown('Direct observer source clock is missing or stale')
        return age

    def model_changed(self, before, after):
        if (type(before) is not int or type(after) is not int or before != self.model_inflight
                or after < 0 or abs(after - before) != 1
                or 'model_events_identity' in self.config and after > 1):
            raise LoadUnknown('Owned model transition cannot reconcile native tasks')
        if after > before:
            self.model_started_count += 1
        else:
            self.model_finished_count += 1
        self.model_inflight = after

    def model_events(self, host):
        expected = self.config['model_events_identity']
        keys = {'pid','birth','command_sha256','invocation','stream','boot','producer_nonce'}
        if (not isinstance(expected, dict) or not keys.issubset(expected)
                or type(expected.get('journal_schema_version')) is not int or expected['journal_schema_version'] != 2
                or any(type(expected[k]) is not int or expected[k] <= 0 for k in ('pid','birth'))
                or any(not isinstance(expected[k], str) or not expected[k] for k in keys - {'pid','birth'})):
            raise LoadUnknown('Expected native model identity missing')
        frame = host.get('model_events')
        if (not isinstance(frame, dict) or frame.get('known') is not True
                or frame.get('identity') != self.config['model_events_identity']
                or frame.get('initial_idle_proven') is not True
                or frame.get('idle_proven') is not False or frame.get('reservation_required') is not True
                or type(frame.get('busy_confirmed')) is not bool):
            raise LoadUnknown('Native model event identity or readiness unknown')
        at, acquisition = frame.get('at_unix'), frame.get('acquisition_seconds')
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (at, acquisition))
                or at <= 0 or not 0 <= acquisition <= 1.25
                or not 0 <= time.time() - at + acquisition <= 1.25):
            raise LoadUnknown('Native model event acquisition stale or unknown')
        reserved, started, completed = (frame.get(k) for k in ('reserved_tasks','started_tasks','completed_tasks'))
        active, phase = frame.get('active_task'), frame.get('busy_phase')
        if any(type(v) is not int or v < 0 for v in (reserved, started, completed)):
            raise LoadUnknown('Native model task counters unknown')
        if phase == 'reservation':
            valid = (frame['busy_confirmed'] and active == {'slot':0,'task':-1}
                     and reserved == started + 1 and started == completed)
        elif phase == 'processing':
            valid = (frame['busy_confirmed'] and isinstance(active, dict)
                     and set(active) == {'slot','task'} and type(active.get('slot')) is int
                     and active['slot'] == 0 and type(active.get('task')) is int and active['task'] > 0
                     and reserved == started and started == completed + 1)
        elif phase is None:
            valid = not frame['busy_confirmed'] and active is None and reserved == started == completed
        else:
            valid = False
        if not valid:
            raise LoadUnknown('Native reservation, task or release lifetime unknown')
        previous = self.last_model_event_frame
        if previous and (reserved < previous['reserved_tasks'] or started < previous['started_tasks'] or completed < previous['completed_tasks']
                         or at < previous['at_unix']):
            raise LoadUnknown('Native model event history moved backward')
        self.last_model_event_frame = frame
        return frame

    @staticmethod
    def native_peer_available(host):
        peers = host.get('model_connections')
        duration, at = host.get('snapshot_read_seconds'), host.get('at_unix')
        if (not isinstance(peers, dict) or peers.get('known') is not True
                or type(peers.get('own_established')) is not int or peers['own_established'] < 0
                or type(peers.get('other_established')) is not int or peers['other_established'] < 0
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (duration, at))
                or not 0 <= duration <= 1.25 or not 0 <= time.time() - at + duration <= 1.25):
            raise LoadUnknown('Native model peers missing, competing or stale')
        return peers['own_established'] > 0 and peers['other_established'] == 0

    def sample(self, owned):
        began = time.monotonic()
        evidence = {'source': 'slots-api', 'host': None}
        def model_probe():
            before = owned()
            if 'model_events_identity' not in self.config:
                response = requests.get(self.config['slots_url'], timeout=5)
                response.raise_for_status()
                after = owned()
                host = self.read('host')
                peers = dict(host['model_connections'], age_s=time.time() - host['at_unix'])
                return response.json(), before, after, peers
            host = self.read('host')
            frame = self.model_events(host)
            evidence['host'] = host
            peer_available = self.native_peer_available(host)
            if self.model_event_base is None:
                if before != 0 or frame['busy_confirmed']:
                    raise LoadUnknown('Direct native event boundary is not idle')
                response = requests.get(self.config['slots_url'], timeout=.25)
                response.raise_for_status()
                slots = response.json()
                if (not isinstance(slots, list) or len(slots) != 1
                        or slots[0].get('is_processing') is not False or owned() != 0):
                    raise LoadUnknown('Direct API initial single slot not idle')
                self.model_event_base = (frame['reserved_tasks'], frame['started_tasks'], frame['completed_tasks'])
            elif frame['busy_confirmed'] and before > 0 and peer_available:
                # A periodic busy hint can outlive a just-finished own call.
                # Without a matching peer/bracket, require a current API result.
                slots = [{'is_processing': True}]
                evidence['source'] = 'native-journal-busy'
            else:
                try:
                    response = requests.get(self.config['slots_url'], timeout=.25)
                    response.raise_for_status()
                    slots = response.json()
                    evidence['source'] = 'slots-api'
                except requests.Timeout as error:
                    # A launch can race the host's periodic publication. Wait only
                    # for independently fresh busy evidence, never infer idle.
                    deadline = time.monotonic() + .5
                    while True:
                        host = self.read('host');frame = self.model_events(host)
                        evidence['host'] = host
                        if frame['busy_confirmed'] and self.native_peer_available(host):
                            slots = [{'is_processing': True}]
                            evidence['source'] = 'native-journal-busy'
                            break
                        if time.monotonic() >= deadline:
                            raise LoadUnknown('Queued API lacks native busy evidence') from error
                        time.sleep(.02)
            if (not isinstance(slots, list) or len(slots) != 1
                    or not isinstance(slots[0], dict) or type(slots[0].get('is_processing')) is not bool):
                raise LoadUnknown('Native model single-slot API schema changed')
            after = owned()
            peer_duration = host.get('snapshot_read_seconds')
            if type(peer_duration) not in (int, float) or not math.isfinite(peer_duration) or not 0 <= peer_duration <= 1.25:
                raise LoadUnknown('Native model peer acquisition unknown')
            peers = dict(host['model_connections'], age_s=time.time() - host['at_unix'] + peer_duration)
            evidence['host'] = host
            return slots, before, after, peers
        try:
            other_busy = sample_model_ownership(model_probe)
        except ValueError as error:
            failure = LoadUnknown(str(error))
            host = evidence['host'] or {}
            failure.observation_details = {'host_at_unix': host.get('at_unix'),
                'model_connections': host.get('model_connections'),
                'model_events': host.get('model_events'), 'own_inflight': owned(),
                'acquisition_seconds': time.monotonic() - began}
            raise failure from error
        windows = self.read('windows')
        expected = self.config['windows_identity']
        if (windows.get('known') is not True
                or type(windows.get('completed_requests')) is not int
                or windows['completed_requests'] < 0
                or any(windows.get(k) != expected[k] for k in ('embedding_pid', 'embedding_birth', 'audit_identity'))):
            raise LoadUnknown('Frozen Windows embedding identity unknown')
        self.age(windows)
        primary = self.read('primary')
        if (primary.get('known') is not True
                or type(primary.get('primary_generating')) is not int
                or primary['primary_generating'] < 0):
            raise LoadUnknown('Direct primary state unavailable')
        self.age(primary)
        with self.engine.connect() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            other_pg = connection.execute(text("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND state='active' AND pid<>pg_backend_pid() AND application_name NOT LIKE 'r2-benchmark-%'")).scalar_one()
        primary_age = self.age(primary)
        self.age(windows)
        if evidence['host'] is not None:
            self.model_events(evidence['host'])
        duration = time.monotonic() - began
        # A composite sample cannot make old slot/peer evidence fresh by moving
        # its timestamp to the end of a long acquisition.
        if duration > 1.25 or type(other_pg) is not int or other_pg < 0:
            raise LoadUnknown('Direct acquisition duration or PG evidence unknown')
        return {'at_unix': time.time(), 'cpu_percent': None, 'pg_other_active': other_pg,
                'model_other_busy': other_busy, 'primary_generating': primary['primary_generating'],
                'primary_age_s': primary_age, 'observer_acquisition_seconds': duration,
                'model_evidence_source': evidence['source']}

    def close(self):
        try:
            if 'model_events_identity' in self.config:
                if self.model_event_base is None or self.model_inflight or self.model_started_count != self.model_finished_count:
                    raise LoadUnknown('Owned model lifetime did not drain')
                response = requests.get(self.config['slots_url'], timeout=1)
                response.raise_for_status()
                slots = response.json()
                if not isinstance(slots, list) or len(slots) != 1 or slots[0].get('is_processing') is not False:
                    raise LoadUnknown('Final model API idle proof unavailable')
                frame = self.model_events(self.read('host'))
                if (frame['busy_confirmed']
                        or frame['reserved_tasks'] - self.model_event_base[0] != self.model_started_count
                        or frame['started_tasks'] - self.model_event_base[1] != self.model_started_count
                        or frame['completed_tasks'] - self.model_event_base[2] != self.model_finished_count):
                    raise LoadUnknown('Native and owned model task histories do not reconcile')
                self.model_final_reconciled = True
                self.model_reconciliation = {
                    'known': True, 'terminal_api_idle': True,
                    'source_identity': dict(frame['identity']),
                    'owned_started': self.model_started_count, 'owned_completed': self.model_finished_count,
                    'native_reserved': frame['reserved_tasks'] - self.model_event_base[0],
                    'native_started': frame['started_tasks'] - self.model_event_base[1],
                    'native_completed': frame['completed_tasks'] - self.model_event_base[2]}
        finally:
            self.engine.dispose()


def create_probe(config):
    return DirectServiceProbe(config)
