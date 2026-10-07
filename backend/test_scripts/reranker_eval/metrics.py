"""Paired metrics: unknown labels and failed scoring cannot become positive evidence."""
from collections import Counter
import math
import random
import statistics

from .cases import validate_judgments

CRITICAL = {'codes', 'tables', 'authorship', 'contradictions'}


def ndcg(keys: list[str], judgments: dict, k: int) -> float | None:
    if set(keys) - judgments.keys():
        raise ValueError('unjudged candidates')
    dcg = sum((2 ** judgments[key] - 1) / math.log2(i + 2) for i, key in enumerate(keys[:k]))
    ideal = sum((2 ** grade - 1) / math.log2(i + 2) for i, grade in
                enumerate(sorted(judgments.values(), reverse=True)[:k]))
    return dcg / ideal if ideal else None


def recall(keys: list[str], mandatory: list[str], k: int) -> float | None:
    return len(set(keys[:k]) & set(mandatory)) / len(set(mandatory)) if mandatory else None


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def paired_ci(deltas: list[float]) -> list[float] | None:
    if not deltas:
        return None
    rng = random.Random(42)
    draws = [statistics.mean(rng.choices(deltas, k=len(deltas))) for _ in range(2000)]
    return [percentile(draws, .025), percentile(draws, .975)]


def latency_summary(samples: list[dict]) -> dict:
    applied = [v['elapsed_ms'] for v in samples if v['status'] == 'applied']
    all_times = [v['elapsed_ms'] for v in samples]
    rate = len(applied) / len(samples) if samples else 0.
    p95 = percentile(applied, .95)
    budget_passed = rate >= .95 and p95 is not None and p95 <= 3000
    return {'count': len(samples), 'applied_rate': rate, 'applied_p50_ms': percentile(applied, .5),
            'applied_p95_ms': p95, 'all_p95_ms': percentile(all_times, .95),
            'status_counts': dict(Counter(v['status'] for v in samples)),
            'budget_passed': budget_passed, 'minimum_samples': 100,
            'passed': len(samples) >= 100 and budget_passed}


def compare_case(case: dict, baseline: list[str], after: list[str]) -> dict:
    validate_judgments(case, baseline + after)
    if Counter(baseline) != Counter(after):
        raise ValueError('source set changed')
    first, second = ndcg(baseline, case['judgments'], 10), ndcg(after, case['judgments'], 10)
    critical_lost = sorted((set(baseline[:20]) & set(case['mandatory_sources'])) - set(after[:20]))
    return {'id': case['id'], 'group': case['group'], 'answerable': case['answerable'],
            'baseline_ndcg': first, 'after_ndcg': second,
            'ndcg_delta': second - first if first is not None else None,
            'critical_lost': critical_lost if case['group'] in CRITICAL else [],
            **{f'{label}_recall{k}': recall(keys, case['mandatory_sources'], k)
               for label, keys in [('baseline', baseline), ('after', after)] for k in (10, 20)}}


def evaluate_run(baseline: dict, after: dict, cases: list[dict]) -> dict:
    if baseline['manifest'] != after['manifest']:
        raise ValueError('manifest mismatch')
    original = {c['id']: c for c in baseline['cases']}
    ranked = {c['id']: c for c in after['cases']}
    if set(original) != set(ranked) or set(original) != {c['id'] for c in cases}:
        raise ValueError('case set mismatch')
    results = []
    for case in cases:
        base, new = original[case['id']], ranked[case['id']]
        # Integrity checks include the full canonical block, not only its identifier.
        base_blocks = {v['eval_key']: v for v in base['final']}
        for block in new['final']:
            if block != base_blocks.get(block['eval_key']):
                raise ValueError('source content changed')
        results.append(compare_case(case, [v['eval_key'] for v in base['final']],
                                    [v['eval_key'] for v in new['final']]))
    deltas = [r['ndcg_delta'] for r in results if r['answerable'] and r['ndcg_delta'] is not None]
    avg = statistics.mean(deltas) if deltas else None
    ci = paired_ci(deltas)
    lost = [r['id'] for r in results if r['critical_lost']]
    recall_gates = {}
    for k in (10, 20):
        pairs = [(r[f'baseline_recall{k}'], r[f'after_recall{k}']) for r in results
                 if r[f'baseline_recall{k}'] is not None]
        recall_gates[str(k)] = bool(pairs) and statistics.mean(b - a for a, b in pairs) >= -1e-12
    quality = avg is not None and avg >= .03 and ci[0] > 0 and not lost and all(recall_gates.values())
    return {'cases': results, 'ndcg_delta': avg, 'paired_ci95': ci,
            'wins': sum(d > 1e-12 for d in deltas), 'ties': sum(abs(d) <= 1e-12 for d in deltas),
            'losses': sum(d < -1e-12 for d in deltas), 'critical_loss_cases': lost,
            'recall_gates': recall_gates, 'quality_passed': quality,
            'decision': 'answer_and_load_validation_required' if quality else 'keep_disabled',
            'answer_validation_required': True, 'load_validation_required': True}
