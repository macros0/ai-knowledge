"""Resource qualification without relevance labels or live answer generation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
from time import perf_counter

from .metrics import latency_summary


def paired_profile(cases, *, runner, pairs, concurrent, top_n, deadline, executor=None):
    if type(pairs) is not int or pairs < 1 or concurrent not in (1, 2) or not cases:
        raise ValueError('unsupported profile')
    if executor is None:
        with ThreadPoolExecutor(max_workers=concurrent) as pool:
            return paired_profile(cases, runner=runner, pairs=pairs, concurrent=concurrent,
                                  top_n=top_n, deadline=deadline, executor=pool)
    samples, controls, differences = [], [], []
    for repeat in range(pairs):
        case = cases[repeat % len(cases)]
        original = deepcopy(case['final'])
        def control():
            started = perf_counter()
            list(original)
            return (perf_counter() - started) * 1000
        def after():
            barrier = threading.Barrier(concurrent)
            def job():
                barrier.wait(timeout=5)
                return runner.run_rerank(case['query'], original, top_n=top_n,
                    deadline_seconds=deadline, forms=case.get('forms', ()))
            futures = [executor.submit(job) for _ in range(concurrent)]
            return [f.result() for f in futures]
        if repeat % 2:
            result, baseline_ms = after(), control()
        else:
            baseline_ms, result = control(), after()
        controls.append(baseline_ms)
        for value in result:
            ranked = value.pop('blocks')
            if sorted(b['eval_key'] for b in ranked) != sorted(b['eval_key'] for b in original):
                raise ValueError('source set changed')
            originals = {b['eval_key']: b for b in original}
            if any(b != originals[b['eval_key']] for b in ranked) or case['final'] != original:
                raise ValueError('source content changed')
            samples.append({'pair': repeat, 'case_id': case['id'], **value})
            differences.append(value['elapsed_ms'] - baseline_ms)
        if (repeat + 1) % 20 == 0:
            print(f'{{"profile_pair":{repeat + 1},"total":{pairs}}}', flush=True)
    latency = latency_summary(samples)
    latency['minimum_pairs'] = 100
    latency['passed'] = latency['passed'] and pairs >= 100
    return {'paired_count': pairs, 'concurrent': concurrent, 'samples': samples,
            'baseline_control_ms': controls, 'added_latency_ms': differences,
            'source_integrity_passed': True, 'latency': latency,
            'baseline_kind': 'identity_copy_of_captured_sources',
            'scope': 'warm_scoring_overhead_only', 'performance_only': True}
