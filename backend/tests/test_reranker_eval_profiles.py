from concurrent.futures import ThreadPoolExecutor
import threading


def test_parallel_profile_counts_busy_as_failure_and_preserves_evidence():
    from test_scripts.reranker_eval.profiles import paired_profile
    class Runner:
        def __init__(self):
            self.lock = threading.Lock()
            self.arrived = threading.Event()
        def run_rerank(self, query, blocks, **kwargs):
            if not self.lock.acquire(False):
                self.arrived.set()
                return {'blocks': list(blocks), 'status': 'busy', 'elapsed_ms': 0.1}
            try:
                self.arrived.wait(2)
                return {'blocks': blocks[::-1], 'status': 'applied', 'elapsed_ms': 10.}
            finally:
                self.lock.release()
    cases = [{'id': 'a', 'query': 'q', 'final': [{'eval_key': 'a'}, {'eval_key': 'b'}]}]
    with ThreadPoolExecutor(2) as executor:
        result = paired_profile(cases, runner=Runner(), pairs=1, concurrent=2,
                                top_n=40, deadline=3., executor=executor)
    assert result['latency']['applied_rate'] == .5
    assert result['latency']['passed'] is False
    assert result['source_integrity_passed'] is True
    assert result['paired_count'] == 1


def test_two_jobs_do_not_turn_fifty_pairs_into_acceptance():
    from test_scripts.reranker_eval.profiles import paired_profile
    class Runner:
        def run_rerank(self, query, blocks, **kwargs):
            return {'blocks': list(blocks), 'status': 'applied', 'elapsed_ms': 10.}
    result = paired_profile([{'id': 'a', 'query': 'q', 'final': [{'eval_key': 'a'}]}],
                            runner=Runner(), pairs=50, concurrent=2, top_n=40, deadline=3.)
    assert result['latency']['count'] == 100
    assert result['latency']['budget_passed'] is True
    assert result['latency']['passed'] is False
