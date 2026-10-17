"""Validate a frozen, balanced C1 latency pair without weakening its gates.

This command evaluates collected application-path measurements. It never starts
services, rewrites publications, or retries a failed matrix. Unknown environment
qualification is invalid, even when timing and functional checks look green.
"""
from __future__ import annotations

from table_acceptance_rss import validate_rss_coverage, RssUnknown
from table_acceptance_load import validate_resource_load_coverage, LoadUnknown

import argparse
import hashlib
import json
import math
from pathlib import Path
import random

P95_THRESHOLD = 1.15
STAGES = ('embedding', 'vector_search', 'hydration', 'merge', 'pruning')
FINGERPRINTS = ('questions', 'runtime', 'corpus', 'model', 'settings', 'schema', 'measurement')
VARIANT_FINGERPRINTS = ('database_snapshot_sha256', 'index_snapshot_sha256', 'active_generations_sha256', 'cache_manifest_sha256')
LOAD_FIELDS = ('cpu_percent', 'embedding_other_requests', 'pg_other_active', 'qdrant_other_requests', 'model_other_busy', 'primary_generating')


def protocol_digest(protocol):
    return hashlib.sha256(json.dumps(protocol, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _finite(value, *, positive=False):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def _invalid(errors):
    return {'schema_version': 1, 'status': 'invalid', 'exit_code': 2,
            'threshold': P95_THRESHOLD, 'errors': errors}


def _quantile(values, probability):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def _question_uncertainty(records, cases):
    # Repeat measurements for one question stay together in each paired draw.
    grouped = {case: {v: [] for v in ('baseline', 'after')} for case in cases}
    for record in records:
        grouped[record['case_id']][record['variant']].append(record['elapsed_ms'])
    rng = random.Random(20261010)
    ratios = []
    for _ in range(1000):
        draw = rng.choices(cases, k=len(cases))
        samples = {v: [n for case in draw for n in grouped[case][v]] for v in ('baseline', 'after')}
        ratios.append(_quantile(samples['after'], .95) / _quantile(samples['baseline'], .95))
    return {'resampling_unit': 'question', 'questions': len(cases), 'draws': 1000,
            'seed': 20261010, 'p95_ratio_interval_95': [_quantile(ratios, .025), _quantile(ratios, .975)],
            'scope': 'descriptive paired question-cluster bootstrap; does not override gate'}


def evaluate(protocol, measurements):
    """Return passed/failed/invalid; incomplete evidence is never a green gate."""
    if not isinstance(protocol, dict) or not isinstance(measurements, dict):
        return _invalid(['protocol and measurements must be JSON objects'])
    errors = []
    cases = protocol.get('cases')
    if not isinstance(cases, list) or len(cases) != 32 or any(not isinstance(c, str) or not c for c in cases) or len(set(cases)) != 32:
        errors.append('expected 32 distinct frozen question identities')
    if protocol.get('schema_version') != 1 or protocol.get('repeats') != 3 or protocol.get('estimator') != 'nearest_rank':
        errors.append('unsupported schema/repeat count/p95 estimator')
    if 'threshold' in protocol and protocol['threshold'] != P95_THRESHOLD:
        errors.append('p95 acceptance threshold cannot be changed')
    variants = protocol.get('variants', {})
    if not isinstance(variants, dict) or set(variants) != {'baseline', 'after'} or any(not isinstance(v, dict) for v in variants.values()) or variants.get('baseline', {}).get('table_structure_enabled') is not False or variants.get('after', {}).get('table_structure_enabled') is not True:
        errors.append('expected declared feature-off/on variants')
    if isinstance(variants, dict) and all(isinstance(v, dict) for v in variants.values()):
        if any(not _sha256(v.get(k)) for v in variants.values() for k in VARIANT_FINGERPRINTS):
            errors.append('variant database/index/generation/cache snapshots must be frozen')
    if measurements.get('variant_fingerprints') != variants:
        errors.append('observed storage/cache snapshots differ from frozen variants')
    fingerprints = protocol.get('fingerprints', {})
    if not isinstance(fingerprints, dict) or any(not _sha256(fingerprints.get(k)) for k in FINGERPRINTS):
        errors.append('complete frozen runtime/corpus/model/schema/measurement fingerprints required')
    if measurements.get('fingerprints') != fingerprints:
        errors.append('observed fingerprints differ from frozen protocol')
    try:
        digest = protocol_digest(protocol)
    except (ValueError, TypeError):
        return _invalid(errors + ['protocol has non-JSON or nonfinite values'])
    if measurements.get('protocol_sha256') != digest:
        errors.append('protocol digest mismatch')
    if measurements.get('files_unchanged') is not True:
        errors.append('source/runtime files were changed or not verified')
    if protocol.get('cache_state') not in ('matched-warm', 'matched-cold') or measurements.get('cache_state') != protocol['cache_state']:
        errors.append('cache state mismatch')
    if protocol.get('warmup') != {'baseline': 1, 'after': 1} or measurements.get('warmup_completed') != protocol.get('warmup'):
        errors.append('equal fixed warmup was not completed')
    samples = measurements.get('load_samples')
    limits = protocol.get('load_limits', {})
    if not isinstance(limits, dict) or any(limits.get(k) != 0 for k in ('model_other_busy', 'primary_generating')):
        errors.append('other model clients and primary generation must be observed idle')
    if measurements.get('load_monitor_complete') is not True or not isinstance(samples, list) or not samples:
        errors.append('environment monitor incomplete')
    elif not isinstance(limits, dict) or any(not _finite(limits.get(k)) for k in LOAD_FIELDS):
        errors.append('load qualification limits absent or invalid')
    else:
        for index, sample in enumerate(samples):
            if not isinstance(sample, dict) or any(not _finite(sample.get(k)) or sample[k] > limits[k] for k in LOAD_FIELDS):
                errors.append(f'load sample {index} is unknown or exceeds frozen limits')
    records = measurements.get('records')
    if not isinstance(records, list) or len(records) != 192:
        errors.append('both 32 x 3 request sets must be complete')
    elif not errors:
        expected = [(variant, case, repeat) for repeat in range(3) for index, case in enumerate(cases)
                    for variant in (('baseline', 'after') if (repeat + index) % 2 == 0 else ('after', 'baseline'))]
        for index, (record, identity) in enumerate(zip(records, expected)):
            if not isinstance(record, dict):
                errors.append(f'request {index} is not an object')
                continue
            if (record.get('variant'), record.get('case_id'), record.get('repeat')) != identity:
                errors.append(f'request {index} has wrong identity or balanced order')
            if record.get('protocol_sha256') != digest or not _finite(record.get('elapsed_ms'), positive=True):
                errors.append(f'request {index} has wrong protocol or invalid latency')
            if not isinstance(record.get('context_passed'), bool):
                errors.append(f'request {index} has unknown context acceptance')
            stages = record.get('stages_ms', {})
            if not isinstance(stages, dict) or any(not _finite(stages.get(k)) for k in STAGES):
                errors.append(f'request {index} has incomplete stage measurements')
            elif _finite(record.get('elapsed_ms'), positive=True) and sum(stages[k] for k in STAGES) > record['elapsed_ms']:
                errors.append(f'request {index} stage totals exceed end-to-end measurement')
    if errors:
        return _invalid(errors)
    summaries = {}
    for variant in ('baseline', 'after'):
        selected = [r for r in records if r['variant'] == variant]
        latencies = [r['elapsed_ms'] for r in selected]
        summaries[variant] = {'requests': len(selected), 'context_passed': sum(r['context_passed'] for r in selected),
                              'p50_ms': _quantile(latencies, .5), 'p95_ms': _quantile(latencies, .95)}
    ratio = summaries['after']['p95_ms'] / summaries['baseline']['p95_ms']
    gates = {'context': summaries['after']['context_passed'] == 96, 'p95': ratio <= P95_THRESHOLD}
    passed = all(gates.values())
    differences = []
    for index in range(0, 192, 2):
        pair = {record['variant']: record['elapsed_ms'] for record in records[index:index + 2]}
        differences.append(pair['after'] - pair['baseline'])
    return {'schema_version': 1, 'status': 'passed' if passed else 'failed', 'exit_code': 0 if passed else 1,
            'protocol_sha256': digest, 'fingerprints': fingerprints, 'threshold': P95_THRESHOLD,
            'p95_ratio': ratio, 'gates': gates, 'variants': summaries, 'errors': [],
            'paired_differences_ms': differences, 'uncertainty': _question_uncertainty(records, cases),
            'records': records, 'load_samples': samples}



def evaluate_resources(protocol, measurements):
    """Require a completed, matched whole-pipeline pair before comparing RSS."""
    if not isinstance(protocol, dict) or not isinstance(measurements, dict):
        return _invalid(['protocol and measurements must be objects'])
    errors = []
    try:
        digest = protocol_digest(protocol)
    except (ValueError, TypeError):
        return _invalid(['invalid resource protocol'])
    if measurements.get('protocol_sha256') != digest or measurements.get('files_unchanged') is not True:
        errors.append('resource protocol changed or files not verified')
    fingerprints = protocol.get('fingerprints', {})
    if not isinstance(fingerprints, dict) or any(not _sha256(fingerprints.get(k)) for k in FINGERPRINTS) or measurements.get('fingerprints') != fingerprints:
        errors.append('resource fingerprints incomplete or mismatched')
    variants = protocol.get('variants', {})
    if not isinstance(variants, dict) or set(variants) != {'baseline', 'after'} or any(not isinstance(v, dict) or any(not _sha256(v.get(k)) for k in VARIANT_FINGERPRINTS) for v in variants.values()) or measurements.get('variant_fingerprints') != variants:
        errors.append('resource storage/cache snapshot mismatch')
    if protocol.get('cache_state') not in ('matched-warm', 'matched-cold') or measurements.get('cache_state') != protocol.get('cache_state'):
        errors.append('resource cache state mismatch')
    samples = measurements.get('load_samples')
    limits = protocol.get('load_limits', {})
    if not isinstance(limits, dict) or any(limits.get(k) != 0 for k in ('model_other_busy', 'primary_generating')):
        errors.append('other model clients and primary generation must be observed idle')
    if measurements.get('load_monitor_complete') is not True or not isinstance(samples, list) or not samples or not isinstance(limits, dict) or any(not _finite(limits.get(k)) for k in LOAD_FIELDS):
        errors.append('resource load monitoring incomplete')
    elif any(not isinstance(sample, dict) or any(not _finite(sample.get(k)) or sample[k] > limits[k] for k in LOAD_FIELDS) for sample in samples):
        errors.append('resource load is unknown or exceeds frozen limits')
    if 'rss_threshold' in protocol and protocol['rss_threshold'] != 1.20:
        errors.append('RSS gate cannot be changed')
    fixture = protocol.get('fixture_sha256')
    if not _sha256(fixture) or protocol.get('resource_scope') != 'entire-pipeline-parent-and-children':
        errors.append('largest selected fixture and whole process-tree scope must be frozen')
    sampling = protocol.get('rss_sampling')
    if sampling != {'interval_seconds': 0.2, 'max_gap_seconds': 1.0}:
        errors.append('RSS interval and maximum gap must be frozen at 0.2s and 1.0s')
    resources = measurements.get('resources', {})
    phases = {'construction', 'parse', 'splitting', 'generation', 'finalization', 'publication', 'indexing'}
    if not isinstance(resources, dict) or set(resources) != {'baseline', 'after'}:
        errors.append('both resource variants required')
    else:
        for variant, row in resources.items():
            if not isinstance(row, dict):
                errors.append(f'{variant}: incomplete resource variant')
                continue
            if row.get('variant') != variant or row.get('status') != 'done' or row.get('problem') is not None or row.get('pipeline_complete') is not True or not isinstance(row.get('active_generation'), str) or len(row['active_generation']) != 32:
                errors.append(f'{variant}: publication did not complete successfully')
            if row.get('fixture_sha256') != fixture or row.get('rss_scope') != protocol.get('resource_scope'):
                errors.append(f'{variant}: fixture or RSS scope mismatch')
            observed_phases = row.get('sampled_phases', [])
            if row.get('rss_sampling_complete') is not True or row.get('rss_sampler_error_count') != 0 or not _finite(row.get('rss_samples'), positive=True) or not isinstance(observed_phases, list) or any(not isinstance(p, str) for p in observed_phases) or not phases.issubset(observed_phases):
                errors.append(f'{variant}: process-tree sampler omitted part of the pipeline')
            try:
                validate_rss_coverage(row.get('rss_coverage'), row.get('rss_samples'), 1.0)
            except RssUnknown as error:
                errors.append(f'{variant}: {error}')
            try:
                validate_resource_load_coverage(row.get('load'), row.get('rss_coverage'))
                if any(not isinstance(sample, dict) or any(not _finite(sample.get(k))
                        or sample[k] > limits[k] for k in LOAD_FIELDS)
                        for sample in row['load']['load_samples']):
                    raise LoadUnknown('Variant load is unknown or exceeds frozen limits')
            except LoadUnknown as error:
                errors.append(f'{variant}: {error}')
            for key in ('peak_parent_children_rss_bytes', 'elapsed_s', 'data_bytes', 'private_database_bytes'):
                if not _finite(row.get(key), positive=True):
                    errors.append(f'{variant}: invalid {key}')
            for key in ('llm_completion_invocations', 'input_tokens', 'output_tokens'):
                if not _finite(row.get(key)):
                    errors.append(f'{variant}: unknown {key}')
            if row.get('llm_usage_missing') != 0:
                errors.append(f'{variant}: provider usage incomplete')
    if errors:
        report = _invalid(errors)
        report['threshold'] = 1.20
        return report
    ratio = resources['after']['peak_parent_children_rss_bytes'] / resources['baseline']['peak_parent_children_rss_bytes']
    passed = ratio <= 1.20
    costs = {}
    for key in ('elapsed_s', 'data_bytes', 'private_database_bytes', 'llm_completion_invocations', 'input_tokens', 'output_tokens'):
        baseline = resources['baseline'][key]
        costs[key] = resources['after'][key] / baseline if baseline else None
    return {'schema_version': 1, 'status': 'passed' if passed else 'failed', 'exit_code': 0 if passed else 1,
            'protocol_sha256': digest, 'fingerprints': fingerprints, 'rss_ratio': ratio, 'threshold': 1.20,
            'cost_ratios': costs, 'resources': resources, 'errors': [],
            'scope': 'process-tree RSS only; separately monitor shared PG/Qdrant/embeddings/model resources'}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('latency', 'resources'), default='latency')
    parser.add_argument('--protocol', required=True, type=Path)
    parser.add_argument('--measurements', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        evaluator = evaluate if args.kind == 'latency' else evaluate_resources
        report = evaluator(json.loads(args.protocol.read_text(encoding='utf-8')),
                          json.loads(args.measurements.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report = _invalid([f'input error: {type(exc).__name__}'])
    try:
        # Historical failures are immutable: require a fresh output path.
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except OSError:
        return 2
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
