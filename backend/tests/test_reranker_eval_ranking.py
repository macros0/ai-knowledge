from copy import deepcopy

import pytest


def reorder(*args, **kwargs):
    from test_scripts.reranker_eval.ranking import reorder_prefix
    return reorder_prefix(*args, **kwargs)


def test_stable_prefix_and_untouched_tail():
    blocks = [{'id': i, 'content': f'text {i}'} for i in range(5)]
    assert [b['id'] for b in reorder(blocks, [.1, .9, .9], top_n=3)] == [1, 2, 0, 3, 4]
    assert reorder([], [], top_n=40) == []
    assert reorder(blocks[:1], [.7], top_n=40) == blocks[:1]


def test_reorder_preserves_canonical_content():
    blocks = [{'title': 'same', 'source_id': i, 'content': 'canonical',
               '_evidence': {'digest': str(i)}} for i in range(3)]
    before = deepcopy(blocks)
    result = reorder(blocks, [0., 1., 2.], top_n=40)
    assert blocks == before
    assert sorted(result, key=lambda b: b['source_id']) == before
    assert result is not blocks


@pytest.mark.parametrize('scores', [[], [1., 2.], [float('nan')], [float('inf')],
                                  [True], ['0.4']])
def test_invalid_scores_are_rejected(scores):
    with pytest.raises(ValueError):
        reorder([{'content': 'x'}], scores, top_n=40)


@pytest.mark.parametrize('top_n', [0, -1, True, 1.5])
def test_invalid_prefix_is_rejected(top_n):
    with pytest.raises(ValueError):
        reorder([], [], top_n=top_n)
