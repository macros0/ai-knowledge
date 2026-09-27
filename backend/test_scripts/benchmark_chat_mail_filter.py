"""Same corpus/config/vector index benchmark, including fixed candidate hydration."""
from copy import deepcopy
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from mail_filter_fixture import configure, main_guard, owned_fixture, targets


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, int(len(values)*fraction))]


def worker():
    from sqlalchemy import event
    from qdrant_client.http import models as qm
    from app.db.session import get_engine
    from app.services.fusion import Hit
    from app.services.retrieval_hydration import load_visible_retrieval_hits
    from app.services.context_builder import merge_and_format
    database, qdrant, collection = targets()
    settings, store = configure(database, qdrant, collection, os.environ['MAIL_FILTER_BENCH_DATA_DIR'])
    baseline = bool(os.environ.get('MAIL_FILTER_BASELINE_BACKEND'))
    fixed_records, _ = store.client.scroll(collection, limit=1000, with_payload=True, with_vectors=False)
    fixed = [Hit(str(row.id), 1., row.payload) for row in fixed_records if row.payload.get('doc_id') == 'ab12cd34ef56ab78' and row.payload.get('generation_id') is None]
    samples = {mode: [] for mode in ('all', 'exclude', 'only')}
    count = [0]
    def observe(*args):
        count[0] += 1
    event.listen(get_engine(), 'before_cursor_execute', observe)
    try:
        for iteration in range(110):
            for mode in samples:
                count[0] = 0
                started = time.perf_counter()
                hits = store.search_composite(dense_vec=[1., 0.], sparse_vec=qm.SparseVector(indices=[7], values=[1.]),
                    tags=[], branches={'dense', 'bm25'}, top_k=100, **({} if baseline else {'mail_mode': mode}))
                retrieval_ms = (time.perf_counter()-started)*1000
                candidate_count = len(hits)
                timings = {}
                started = time.perf_counter()
                hydrated, _ = load_visible_retrieval_hits(deepcopy(hits), timings=timings,
                    **({} if baseline else {'mail_mode': mode}))
                hydration_ms = (time.perf_counter()-started)*1000
                blocks = merge_and_format(hydrated, settings, **({} if baseline else {'mail_mode': mode}))
                sql_count = count[0]
                started = time.perf_counter()
                load_visible_retrieval_hits(deepcopy(fixed))  # identical fixed input, no ranking/cardinality credit
                fixed_ms = (time.perf_counter()-started)*1000
                if iteration >= 10:
                    samples[mode].append(dict(retrieval_ms=retrieval_ms, hydration_ms=hydration_ms,
                        classification_ms=timings.get('classification_ms', 0.), fixed_hydration_ms=fixed_ms,
                        sql_count=sql_count, candidates=candidate_count, context_chars=sum(len(b.get('content','')) for b in blocks)))
    finally:
        event.remove(get_engine(), 'before_cursor_execute', observe)
        get_engine().dispose()
        store.client.close()
    return {mode: {key: {'p50': round(statistics.median([row[key] for row in rows]),3),
                         'p95': round(percentile([row[key] for row in rows], .95),3)} for key in rows[0]}
            for mode, rows in samples.items()}


def benchmark():
    baseline_path = os.environ.get('MAIL_FILTER_BASELINE_BACKEND')
    if not baseline_path or not (Path(baseline_path)/'app/services/retrieval_hydration.py').is_file():
        raise ValueError('explicit baseline checkout required')
    results = {}
    with owned_fixture() as (settings, store):
        for label in ('baseline', 'after'):
            environment = os.environ.copy()
            environment['MAIL_FILTER_BENCH_DATA_DIR'] = str(settings.data_dir)
            if label == 'after':
                environment.pop('MAIL_FILTER_BASELINE_BACKEND', None)
            process = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker'],
                                     env=environment, text=True, capture_output=True)
            if process.returncode:
                raise RuntimeError('benchmark worker failed')
            results[label] = json.loads(process.stdout)
    allowance = max(50., .2*max(results['baseline'][mode]['retrieval_ms']['p95'] for mode in results['baseline']))
    overhead = max(results['after'][mode]['fixed_hydration_ms']['p95']-results['baseline'][mode]['fixed_hydration_ms']['p95'] for mode in results['after'])
    assert overhead <= allowance
    return dict(status='PASS', warmup_per_mode=10, samples_per_mode=100,
                ordering='all/exclude/only', no_llm=True, same_fixed_candidates=True,
                extra_fixed_hydration_p95_ms=round(overhead,3), allowed_extra_p95_ms=round(allowance,3), results=results)


if __name__ == '__main__':
    raise SystemExit(main_guard(worker if '--worker' in sys.argv else benchmark))
