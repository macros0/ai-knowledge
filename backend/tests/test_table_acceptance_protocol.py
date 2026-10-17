"""Guard the acceptance decision, especially false green and invalid pairs."""
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[1] / 'test_scripts/benchmark_table_acceptance.py'
    assert path.is_file(), 'durable table acceptance protocol is missing'
    spec = importlib.util.spec_from_file_location('table_acceptance_protocol', path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def fixture():
    cases = [f'case-{n:02}' for n in range(32)]
    protocol = {'schema_version': 1, 'cases': cases, 'repeats': 3,
                'warmup': {'baseline': 1, 'after': 1},
                'estimator': 'nearest_rank', 'cache_state': 'matched-warm',
                'fingerprints': {name: hashlib.sha256(name.encode()).hexdigest() for name in
                    ('questions', 'runtime', 'corpus', 'model', 'settings', 'schema', 'measurement')},
                'variants': {v: {'table_structure_enabled': v == 'after',
                    'database_snapshot_sha256': hashlib.sha256((v + '-db').encode()).hexdigest(),
                    'index_snapshot_sha256': hashlib.sha256((v + '-index').encode()).hexdigest(),
                    'active_generations_sha256': hashlib.sha256((v + '-generations').encode()).hexdigest(),
                    'cache_manifest_sha256': hashlib.sha256(b'matched-cache').hexdigest()}
                    for v in ('baseline', 'after')},
                'load_limits': {'cpu_percent': 25, 'embedding_other_requests': 0,
                                'pg_other_active': 0, 'qdrant_other_requests': 0,
                                'model_other_busy': 0, 'primary_generating': 0}}
    digest = hashlib.sha256(json.dumps(protocol, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    records = []
    for repeat in range(3):
        for index, case in enumerate(cases):
            for variant in (('baseline', 'after') if (repeat + index) % 2 == 0 else ('after', 'baseline')):
                records.append({'case_id': case, 'repeat': repeat, 'variant': variant,
                    'elapsed_ms': 100, 'context_passed': True, 'protocol_sha256': digest,
                    'stages_ms': {name: 10 for name in ('embedding', 'vector_search', 'hydration', 'merge', 'pruning')}})
    measurements = {'protocol_sha256': digest, 'warmup_completed': {'baseline': 1, 'after': 1},
                    'cache_state': 'matched-warm', 'files_unchanged': True,
                    'load_monitor_complete': True, 'fingerprints': copy.deepcopy(protocol['fingerprints']),
                    'variant_fingerprints': copy.deepcopy(protocol['variants']),
                    'load_samples': [{'cpu_percent': 10, 'embedding_other_requests': 0,
                                      'pg_other_active': 0, 'qdrant_other_requests': 0,
                                      'model_other_busy': 0, 'primary_generating': 0}],
                    'records': records}
    return protocol, measurements


def test_complete_balanced_pair_passes_fixed_gate():
    protocol, measurements = fixture()
    result = module().evaluate(protocol, measurements)
    assert result['status'] == 'passed' and result['exit_code'] == 0
    assert result['p95_ratio'] == 1 and result['threshold'] == 1.15
    assert result['variants']['after']['requests'] == 96
    assert result['uncertainty']['resampling_unit'] == 'question'


def test_slow_pair_stays_failed_with_all_contexts_green():
    protocol, measurements = fixture()
    for record in measurements['records']:
        if record['variant'] == 'after': record['elapsed_ms'] = 127.2
    result = module().evaluate(protocol, measurements)
    assert result['status'] == 'failed' and result['exit_code'] == 1
    assert result['p95_ratio'] == pytest.approx(1.272)
    assert result['gates']['context'] and not result['gates']['p95']


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'order', 'question', 'repeat', 'digest', 'nan', 'zero', 'stage'])
def test_incomplete_or_corrupt_samples_are_invalid(fault):
    protocol, measurements = fixture()
    records = measurements['records']
    if fault == 'missing': records.pop()
    elif fault == 'duplicate': records[-1] = copy.deepcopy(records[0])
    elif fault == 'order': records[0], records[1] = records[1], records[0]
    elif fault == 'question': records[0]['case_id'] = 'unknown'
    elif fault == 'repeat': records[0]['repeat'] = 3
    elif fault == 'digest': records[0]['protocol_sha256'] = 'changed'
    elif fault == 'nan': records[0]['elapsed_ms'] = float('nan')
    elif fault == 'zero': records[0]['elapsed_ms'] = 0
    else: records[0]['stages_ms'].pop('hydration')
    result = module().evaluate(protocol, measurements)
    assert result['status'] == 'invalid' and result['exit_code'] == 2 and result['errors']


@pytest.mark.parametrize('fault', ['missing_monitor', 'unknown_load', 'busy_cpu', 'embedding', 'pg', 'qdrant', 'cache', 'warmup', 'changed_file'])
def test_unqualified_environment_never_passes(fault):
    protocol, measurements = fixture()
    if fault == 'missing_monitor': measurements['load_monitor_complete'] = False
    elif fault == 'unknown_load': measurements['load_samples'][0]['qdrant_other_requests'] = None
    elif fault == 'busy_cpu': measurements['load_samples'][0]['cpu_percent'] = 26
    elif fault == 'embedding': measurements['load_samples'][0]['embedding_other_requests'] = 1
    elif fault == 'pg': measurements['load_samples'][0]['pg_other_active'] = 1
    elif fault == 'qdrant': measurements['load_samples'][0]['qdrant_other_requests'] = 1
    elif fault == 'cache': measurements['cache_state'] = 'unknown'
    elif fault == 'warmup': measurements['warmup_completed']['after'] = 0
    else: measurements['files_unchanged'] = False
    assert module().evaluate(protocol, measurements)['status'] == 'invalid'


def test_functional_miss_is_failed_and_preserved_beside_performance_gate():
    protocol, measurements = fixture()
    measurements['records'][1]['context_passed'] = False
    result = module().evaluate(protocol, measurements)
    assert result['status'] == 'failed' and not result['gates']['context'] and result['gates']['p95']
    assert result['variants']['after']['context_passed'] == 95


def test_protocol_cannot_relax_existing_threshold():
    protocol, measurements = fixture()
    protocol['threshold'] = 2
    assert module().evaluate(protocol, measurements)['status'] == 'invalid'


def test_cli_returns_nonzero_for_failed_report_and_preserves_existing_output(tmp_path):
    protocol, measurements = fixture()
    for record in measurements['records']:
        if record['variant'] == 'after': record['elapsed_ms'] = 130
    p = tmp_path / 'protocol.json'; p.write_text(json.dumps(protocol))
    m = tmp_path / 'measurements.json'; m.write_text(json.dumps(measurements))
    output = tmp_path / 'result.json'
    args = ['--protocol', str(p), '--measurements', str(m), '--output', str(output)]
    assert module().main(args) == 1
    saved = output.read_bytes()
    assert module().main(args) == 2 and output.read_bytes() == saved


def test_invalid_json_cli_writes_invalid_report_and_returns_error(tmp_path):
    p = tmp_path / 'protocol.json'; p.write_text('{broken')
    m = tmp_path / 'measurements.json'; m.write_text('{}')
    output = tmp_path / 'result.json'
    assert module().main(['--protocol', str(p), '--measurements', str(m), '--output', str(output)]) == 2
    assert json.loads(output.read_text())['status'] == 'invalid'


@pytest.mark.parametrize('fingerprint', ['questions', 'runtime', 'corpus', 'model', 'settings', 'schema', 'measurement'])
def test_actual_fingerprint_drift_invalidates_the_pair(fingerprint):
    protocol, measurements = fixture()
    measurements['fingerprints'][fingerprint] = 'changed'
    assert module().evaluate(protocol, measurements)['status'] == 'invalid'


@pytest.mark.parametrize('key', ['database_snapshot_sha256', 'index_snapshot_sha256', 'active_generations_sha256', 'cache_manifest_sha256'])
def test_changed_variant_storage_or_cache_is_invalid(key):
    protocol, measurements = fixture()
    measurements['variant_fingerprints']['after'][key] = 'changed'
    assert module().evaluate(protocol, measurements)['status'] == 'invalid'


def test_stage_totals_cannot_exceed_end_to_end_latency():
    protocol, measurements = fixture()
    measurements['records'][0]['stages_ms']['hydration'] = 10000
    assert module().evaluate(protocol, measurements)['status'] == 'invalid'


def test_unknown_cache_policy_cannot_be_self_certified():
    protocol, measurements = fixture()
    protocol['cache_state'] = measurements['cache_state'] = 'unknown'
    digest = hashlib.sha256(json.dumps(protocol, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    measurements['protocol_sha256'] = digest
    for record in measurements['records']: record['protocol_sha256'] = digest
    assert module().evaluate(protocol, measurements)['status'] == 'invalid'


def test_paired_differences_keep_after_minus_baseline_sign():
    protocol, measurements = fixture()
    for record in measurements['records']:
        if record['variant'] == 'after': record['elapsed_ms'] = 110
    assert module().evaluate(protocol, measurements)['paired_differences_ms'] == [10] * 96


def resource_fixture():
    protocol, measured = fixture()
    protocol['fixture_sha256'] = hashlib.sha256(b'largest-original-A').hexdigest()
    protocol['resource_scope'] = 'entire-pipeline-parent-and-children'
    protocol['rss_sampling'] = {'interval_seconds': .2, 'max_gap_seconds': 1.0}
    measured['protocol_sha256'] = module().protocol_digest(protocol)
    phases = ['construction', 'parse', 'splitting', 'generation', 'finalization', 'publication', 'indexing']
    measured['resources'] = {variant: {'variant': variant, 'status': 'done', 'problem': None,
        'active_generation': 'a' * 32 if variant == 'baseline' else 'b' * 32,
        'fixture_sha256': protocol['fixture_sha256'], 'pipeline_complete': True,
        'rss_scope': protocol['resource_scope'], 'rss_sampling_complete': True,
        'rss_samples': 101, 'rss_coverage': {'window_start': 0, 'window_end': 20,
            'publication_finished_at': 20, 'sample_times': [i / 5 for i in range(101)]},
        'rss_sampler_error_count': 0, 'sampled_phases': phases,
        'peak_parent_children_rss_bytes': 1000000, 'elapsed_s': 20,
        'data_bytes': 2000000, 'private_database_bytes': 3000000,
        'llm_completion_invocations': 10, 'input_tokens': 10000, 'output_tokens': 1000,
        'llm_usage_missing': 0} for variant in ('baseline', 'after')}
    for row in measured['resources'].values():
        row['load']={'load_monitor_complete':True,
            'window':{'start_unix':100,'end_unix':120,'start_monotonic':0,'end_monotonic':20},
            'load_samples':[{'at_unix':100+i*.5,**{key:0 for key in module().LOAD_FIELDS}} for i in range(41)],
            'host_cpu_samples':[{'start_unix':99+i*.5,'end_unix':99.5+i*.5} for i in range(44)],
            'windows_cpu_samples':[{'start_unix':99+i*.5,'end_unix':99.5+i*.5} for i in range(44)]}
    return protocol, measured


def test_complete_resource_pair_uses_fixed_whole_process_tree_gate():
    protocol, measured = resource_fixture()
    measured['resources']['after']['peak_parent_children_rss_bytes'] = 1190000
    result = resource_api()(protocol, measured)
    assert result['status'] == 'passed' and result['rss_ratio'] == 1.19
    assert result['threshold'] == 1.20 and result['cost_ratios']['elapsed_s'] == 1


def test_resource_rss_failure_never_gets_green_from_exit_zero():
    protocol, measured = resource_fixture()
    measured['resources']['after']['peak_parent_children_rss_bytes'] = 1210000
    result = resource_api()(protocol, measured)
    assert result['status'] == 'failed' and result['exit_code'] == 1


@pytest.mark.parametrize('fault', ['paused', 'missing_after', 'missing_generation', 'fixture', 'partial_sampler', 'sampler_error', 'phase', 'scope', 'unknown_tokens', 'usage_missing'])
def test_incomplete_or_mismatched_resource_baseline_is_invalid(fault):
    protocol, measured = resource_fixture()
    row = measured['resources']['baseline']
    if fault == 'paused': row['status'] = 'paused'
    elif fault == 'missing_after': measured['resources']['after'] = None
    elif fault == 'missing_generation': row['active_generation'] = None
    elif fault == 'fixture': row['fixture_sha256'] = 'small-C1'
    elif fault == 'partial_sampler': row['rss_sampling_complete'] = False
    elif fault == 'sampler_error': row['rss_sampler_error_count'] = 1
    elif fault == 'phase': row['sampled_phases'].remove('publication')
    elif fault == 'scope': row['rss_scope'] = 'generation only'
    elif fault == 'unknown_tokens': row['input_tokens'] = None
    else: row['llm_usage_missing'] = 1
    result = resource_api()(protocol, measured)
    assert result['status'] == 'invalid' and result['exit_code'] == 2


def resource_api():
    function = getattr(module(), 'evaluate_resources', None)
    assert callable(function), 'whole-process resource acceptance validator is missing'
    return function


@pytest.mark.parametrize('peak,code', [(1190000, 0), (1210000, 1)])
def test_resource_cli_dispatches_rss_gate_with_truthful_exit(tmp_path, peak, code):
    protocol, measured = resource_fixture()
    measured['resources']['after']['peak_parent_children_rss_bytes'] = peak
    p = tmp_path / 'protocol.json'; p.write_text(json.dumps(protocol))
    m = tmp_path / 'measurements.json'; m.write_text(json.dumps(measured))
    output = tmp_path / 'result.json'
    command = [sys.executable, str(Path(__file__).parents[1] / 'test_scripts/benchmark_table_acceptance.py'),
               '--kind', 'resources', '--protocol', str(p), '--measurements', str(m), '--output', str(output)]
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == code, completed.stderr
    report = json.loads(output.read_text())
    assert report['threshold'] == 1.2 and report['rss_ratio'] == peak / 1000000


@pytest.mark.parametrize('fingerprint', ['questions', 'runtime', 'corpus', 'model', 'settings', 'schema', 'measurement'])
def test_placeholder_fingerprint_cannot_be_self_certified(fingerprint):
    protocol, measured = fixture()
    protocol['fingerprints'][fingerprint] = 'z' * 64
    measured['fingerprints'][fingerprint] = 'z' * 64
    digest = module().protocol_digest(protocol)
    measured['protocol_sha256'] = digest
    for record in measured['records']: record['protocol_sha256'] = digest
    assert module().evaluate(protocol, measured)['status'] == 'invalid'


@pytest.mark.parametrize('field', ['model_other_busy', 'primary_generating'])
@pytest.mark.parametrize('fault', ['missing', 'busy', 'relaxed_limit'])
@pytest.mark.parametrize('kind', ['latency', 'resources'])
def test_other_model_clients_and_primary_generation_must_be_observed_idle(field, fault, kind):
    protocol, measurements = fixture() if kind == 'latency' else resource_fixture()
    if fault == 'missing':
        measurements['load_samples'][0].pop(field)
    elif fault == 'busy':
        measurements['load_samples'][0][field] = 1
    else:
        protocol['load_limits'][field] = 1
        measurements['load_samples'][0][field] = 1
        digest = module().protocol_digest(protocol)
        measurements['protocol_sha256'] = digest
        for record in measurements.get('records', []): record['protocol_sha256'] = digest
    evaluator = module().evaluate if kind == 'latency' else module().evaluate_resources
    assert evaluator(protocol, measurements)['status'] == 'invalid'


@pytest.mark.parametrize('fault', ['missing', 'internal_gap', 'start_gap', 'end_gap', 'early_stop', 'nan', 'order', 'count'])
def test_resource_evaluator_independently_rejects_forged_rss_coverage(fault):
    protocol, measured = resource_fixture()
    protocol['rss_sampling'] = {'interval_seconds': .2, 'max_gap_seconds': 1.0}
    measured['protocol_sha256'] = module().protocol_digest(protocol)
    row = measured['resources']['baseline']
    row['rss_coverage'] = {'window_start': 0, 'window_end': 20,
        'publication_finished_at': 20, 'sample_times': [i / 5 for i in range(101)]}
    row['rss_samples'] = 101
    coverage = row['rss_coverage']
    if fault == 'missing': row.pop('rss_coverage')
    elif fault == 'internal_gap': coverage['sample_times'] = [t for t in coverage['sample_times'] if not 5 < t < 10]; row['rss_samples'] = len(coverage['sample_times'])
    elif fault == 'start_gap': coverage['window_start'] = -2
    elif fault == 'end_gap': coverage['window_end'] = 22
    elif fault == 'early_stop': coverage['publication_finished_at'] = 20.1
    elif fault == 'nan': coverage['sample_times'][10] = float('nan')
    elif fault == 'order': coverage['sample_times'][10] = 0
    else: row['rss_samples'] = 102
    assert resource_api()(protocol, measured)['status'] == 'invalid'



@pytest.mark.parametrize('fault',['missing','run_only','direct_gap','windows_tail'])
def test_resource_evaluator_rejects_load_outside_full_rss_window(fault):
    protocol,measured=resource_fixture()
    row=measured['resources']['baseline']
    load={'load_monitor_complete':True,'window':{'start_unix':100,'end_unix':120,'start_monotonic':0,'end_monotonic':20},
        'load_samples':[{'at_unix':100+i*.5} for i in range(41)],
        'host_cpu_samples':[{'start_unix':99+i*.5,'end_unix':99.5+i*.5} for i in range(44)],
        'windows_cpu_samples':[{'start_unix':99+i*.5,'end_unix':99.5+i*.5} for i in range(44)]}
    row['load']=load
    if fault=='missing':row.pop('load')
    elif fault=='run_only':load['window'].update(start_unix=105,start_monotonic=5)
    elif fault=='direct_gap':load['load_samples']=[s for s in load['load_samples'] if not 105<s['at_unix']<110]
    else:load['windows_cpu_samples']=load['windows_cpu_samples'][:30]
    assert resource_api()(protocol,measured)['status']=='invalid'


@pytest.mark.parametrize('field',['cpu_percent','qdrant_other_requests','model_other_busy'])
def test_resource_evaluator_checks_each_variant_load_instead_of_unrelated_pair_samples(field):
    protocol,measured=resource_fixture()
    measured['resources']['after']['load']['load_samples'][3][field]=26 if field=='cpu_percent' else 1
    assert resource_api()(protocol,measured)['status']=='invalid'
