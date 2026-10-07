import math

import pytest


def test_ndcg_and_recall_use_explicit_labels():
    from test_scripts.reranker_eval.metrics import ndcg, recall
    judgments = {'a': 2, 'b': 1, 'c': 0}
    assert ndcg(['a', 'b', 'c'], judgments, 10) == 1.
    assert math.isclose(ndcg(['c', 'b', 'a'], judgments, 3),
                        (1 / math.log2(3) + 3 / 2) / (3 + 1 / math.log2(3)))
    assert recall(['a'], ['a', 'b'], 10) == .5
    with pytest.raises(ValueError, match='unjudged'):
        ndcg(['missing'], judgments, 10)
    assert ndcg(['c'], {'c': 0}, 10) is None


def test_fast_fallback_does_not_pass_latency_gate():
    from test_scripts.reranker_eval.metrics import latency_summary
    samples = [{'status': 'timeout', 'elapsed_ms': 1.}] * 100
    result = latency_summary(samples)
    assert result['applied_rate'] == 0
    assert result['passed'] is False
    assert result['applied_p95_ms'] is None


def test_manifest_mismatch_and_critical_loss_are_rejected():
    from test_scripts.reranker_eval.metrics import compare_case, evaluate_run
    case = {'id': 'x', 'judgments': {'a': 2, 'b': 0}, 'mandatory_sources': ['a'],
            'answerable': True, 'group': 'codes'}
    result = compare_case(case, ['a', 'b'], ['b', 'a'])
    assert result['ndcg_delta'] < 0
    with pytest.raises(ValueError, match='manifest'):
        evaluate_run({'manifest': {'corpus': 'a'}, 'cases': []},
                     {'manifest': {'corpus': 'b'}, 'cases': []}, [])


def test_bootstrap_is_paired_and_reproducible():
    from test_scripts.reranker_eval.metrics import paired_ci
    assert paired_ci([.1] * 24) == pytest.approx([.1, .1])
    assert paired_ci([-.1, .2, .3]) == paired_ci([-.1, .2, .3])


def test_small_latency_screen_cannot_satisfy_acceptance_sample_count():
    from test_scripts.reranker_eval.metrics import latency_summary
    samples = [{'status': 'applied', 'elapsed_ms': 100.}] * 24
    result = latency_summary(samples)
    assert result['budget_passed'] is True
    assert result['passed'] is False
    assert latency_summary(samples * 5)['passed'] is True
