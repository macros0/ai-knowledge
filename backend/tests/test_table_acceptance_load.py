"""Qualification uses observed counters and fails closed on missing telemetry."""
import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[1] / 'test_scripts/table_acceptance_load.py'
    assert path.is_file(), 'table benchmark load observation is missing'
    spec = importlib.util.spec_from_file_location('table_acceptance_load', path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def test_qdrant_counts_rest_and_grpc_and_excludes_only_monitor_endpoint():
    load = module()
    counters = load.parse_qdrant_request_counters(
        'grpc_responses_total{endpoint="/qdrant.Points/Query",status="0"} 42\n'
        'rest_responses_total{method="POST",endpoint="/collections/{collection_name}/points/query",status="200"} 8\n'
        'rest_responses_total{method="GET",endpoint="/metrics",status="200"} 500\n'
        'grpc_responses_avg_duration_seconds{endpoint="/qdrant.Points/Query",status="0"} 0.01\n')
    assert sum(counters.values()) == 50 and len(counters) == 2


@pytest.mark.parametrize('body', [
    '', 'app_info{name="qdrant",version="1.19.0"} 1',
    'grpc_responses_total{endpoint="/query",status="0"} NaN',
    'grpc_responses_total{endpoint="/query",status="0"} -1',
    'grpc_responses_total{endpoint="/query",status="0"} 1.5',
    'grpc_responses_total{endpoint="/query",status="0"} 1\ngrpc_responses_total{endpoint="/query",status="0"} 2',
    'grpc_responses_total{endpoint="/query",status="0",status="1"} 2',
    'grpc_responses_total{endpoint="/query",status=0} 2',
])
def test_missing_reset_or_malformed_request_telemetry_is_unknown(body):
    load = module()
    with pytest.raises(load.LoadUnknown): load.parse_qdrant_request_counters(body)


def test_foreign_request_delta_keeps_new_endpoint_and_deducts_only_observed_owned_calls():
    load = module()
    assert load.foreign_qdrant_requests({'query': 10}, {'query': 14, 'upsert': 2}, 4) == 2


@pytest.mark.parametrize('before,after,owned', [
    ({'query': 10}, {'query': 9}, 0), ({'query': 10}, {}, 0),
    ({'query': 10}, {'query': 11}, 2), ({'query': 10}, {'query': 11}, None),
])
def test_counter_reset_or_impossible_ownership_never_becomes_zero_load(before, after, owned):
    load = module()
    with pytest.raises(load.LoadUnknown): load.foreign_qdrant_requests(before, after, owned)


def test_cpu_fraction_includes_windows_kernel_idle_only_once():
    assert module().cpu_percent((50, 100), (70, 200)) == 80


@pytest.mark.parametrize('before,after', [((1, 10), (1, 10)), ((1, 10), (0, 20)),
                                        ((1, 10), (20, 20)), ((1, 10), (2, 9))])
def test_missing_or_reset_cpu_counters_are_unknown(before, after):
    load = module()
    with pytest.raises(load.LoadUnknown): load.cpu_percent(before, after)


def test_only_owned_embedding_clients_are_excluded_from_conservative_connection_bound():
    rows = [
        {'state': 5, 'local_port': 41000, 'remote_port': 16400, 'owner_pid': 111},
        {'state': 5, 'local_port': 41001, 'remote_port': 16400, 'owner_pid': 222},
        {'state': 5, 'local_port': 16400, 'remote_port': 41001, 'owner_pid': 333},
        {'state': 11, 'local_port': 41002, 'remote_port': 16400, 'owner_pid': 444},
    ]
    assert module().foreign_embedding_connections(rows, {111}, 16400) == 1


def test_incomplete_tcp_table_does_not_prove_idle_embeddings():
    load = module()
    with pytest.raises(load.LoadUnknown): load.foreign_embedding_connections([{'state': 5}], {111}, 16400)


@pytest.mark.parametrize('slots', [[], {}, [{'id': 0}], [{'is_processing': None}], [{'is_processing': 0}]])
def test_missing_model_slot_observation_is_unknown(slots):
    load = module()
    with pytest.raises(load.LoadUnknown): load.active_model_slots(slots)


def test_active_model_slots_are_counted_without_false_zero_for_another_client():
    load = module()
    assert load.active_model_slots([{'is_processing': False}, {'is_processing': True}]) == 1


def test_embedding_log_counts_completed_requests_without_treating_idle_connections_as_requests():
    load = module()
    body = ('[GIN] 2026/10/10 - 14:01:00 | 200 | 35.5ms | 127.0.0.1 | POST "/api/embed"\n'
            '[GIN] 2026/10/10 - 14:01:01 | 200 | 200µs | 127.0.0.1 | GET "/api/tags"\n'
            '[GIN] 2026/10/10 - 14:01:02 | 503 | 1.5s | 127.0.0.1 | POST "/api/embeddings"\n')
    assert load.parse_embedding_requests(body) == 2


def test_embedding_log_window_counts_only_appended_complete_records(tmp_path):
    load = module(); path = tmp_path / 'server.log'
    line = '[GIN] 2026/10/10 - 14:01:00 | 200 | 35ms | 127.0.0.1 | POST "/api/embed"\n'
    path.write_text(line, encoding='utf-8')
    cursor = load.embedding_log_boundary(path)
    with path.open('a', encoding='utf-8') as stream: stream.write(line)
    observation = load.read_embedding_log_delta(path, cursor)
    assert observation['completed_requests'] == 1 and observation['complete']
    assert observation['cursor']['offset'] > cursor['offset']
    assert load.read_embedding_log_delta(path, observation['cursor'])['completed_requests'] == 0


@pytest.mark.parametrize('fault', ['rotation', 'truncate_regrow', 'truncate'])
def test_embedding_log_rotation_or_rewrite_is_unknown(tmp_path, fault):
    load = module(); path = tmp_path / 'server.log'
    path.write_text('original prefix\n', encoding='utf-8')
    cursor = load.embedding_log_boundary(path)
    if fault == 'rotation': path.rename(tmp_path / 'previous.log')
    path.write_text('different longer prefix\n' if fault != 'truncate' else '', encoding='utf-8')
    with pytest.raises(load.LoadUnknown): load.read_embedding_log_delta(path, cursor)


def test_partial_log_record_does_not_silently_complete_monitoring(tmp_path):
    load = module(); path = tmp_path / 'server.log'
    path.write_text('startup\n', encoding='utf-8'); cursor = load.embedding_log_boundary(path)
    with path.open('a', encoding='utf-8') as stream: stream.write('[GIN] partial')
    assert load.read_embedding_log_delta(path, cursor)['complete'] is False


def test_malformed_embedding_request_record_is_unknown():
    load = module()
    with pytest.raises(load.LoadUnknown): load.parse_embedding_requests('[GIN] malformed POST "/api/embed"\n')


def telemetry():
    return {'result': {'requests': {
        'rest': {'responses': {'GET /collections': {'200': {'count': 8}}, 'GET /telemetry': {'200': {'count': 50}}}},
        'grpc': {'responses': {'/qdrant.Points/Query': {'0': {'count': 42}}, '/qdrant.Collections/List': {'0': {'count': 3}}}},
    }}}


def test_full_qdrant_telemetry_includes_metadata_calls_omitted_by_prometheus():
    load = module()
    counters = load.parse_qdrant_telemetry(telemetry())
    assert sum(counters.values()) == 53 and len(counters) == 3


@pytest.mark.parametrize('fault', ['requests_missing', 'rest_missing', 'grpc_missing', 'bad_endpoint', 'negative', 'fractional', 'boolean', 'count_missing', 'bad_statuses'])
def test_full_qdrant_telemetry_rejects_incomplete_or_invalid_counter_coverage(fault):
    load = module(); data = telemetry(); requests = data['result']['requests']
    if fault == 'requests_missing': del data['result']['requests']
    elif fault == 'rest_missing': del requests['rest']
    elif fault == 'grpc_missing': del requests['grpc']
    elif fault == 'bad_endpoint': requests['rest']['responses'][''] = requests['rest']['responses'].pop('GET /collections')
    elif fault == 'bad_statuses': requests['rest']['responses']['GET /collections'] = []
    else:
        counter = requests['rest']['responses']['GET /collections']['200']
        if fault == 'count_missing': del counter['count']
        else: counter['count'] = {'negative': -1, 'fractional': 1.5, 'boolean': True}[fault]
    with pytest.raises(load.LoadUnknown): load.parse_qdrant_telemetry(data)



def lifecycle_load():
    return {'load_monitor_complete':True,'window':{'start_unix':100,'end_unix':120,'start_monotonic':0,'end_monotonic':20},
        'load_samples':[{'at_unix':100+i*.5} for i in range(41)],
        'host_cpu_samples':[{'start_unix':99+i*.5,'end_unix':99.5+i*.5} for i in range(44)],
        'windows_cpu_samples':[{'start_unix':99+i*.5,'end_unix':99.5+i*.5} for i in range(44)]}


@pytest.mark.parametrize('fault',['late_start','early_end','direct_gap','host_gap','windows_gap','clock_jump','false_complete','missing'])
def test_full_resource_load_rejects_missing_construction_or_cleanup_even_if_boolean_green(fault):
    from table_acceptance_load import validate_resource_load_coverage, LoadUnknown
    load=lifecycle_load();rss={'window_start':1,'window_end':19}
    if fault=='late_start':load['window']['start_monotonic']=2;load['window']['start_unix']=102
    elif fault=='early_end':load['window']['end_monotonic']=18;load['window']['end_unix']=118
    elif fault=='direct_gap':load['load_samples']=[s for s in load['load_samples'] if not 105<s['at_unix']<110]
    elif fault in ('host_gap','windows_gap'):
        key='host_cpu_samples' if fault=='host_gap' else 'windows_cpu_samples'
        load[key]=[s for s in load[key] if not 105<s['end_unix']<110]
    elif fault=='clock_jump':load['window']['end_unix']=122
    elif fault=='false_complete':load['load_monitor_complete']=False
    else:load.pop('window')
    with pytest.raises(LoadUnknown):validate_resource_load_coverage(load,rss)


def test_full_resource_load_accepts_actual_edges_on_both_hosts():
    from table_acceptance_load import validate_resource_load_coverage
    assert validate_resource_load_coverage(lifecycle_load(),{'window_start':1,'window_end':19}) is True
