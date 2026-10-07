import json

import pytest


def test_replay_cannot_score_unfrozen_labels(tmp_path):
    from test_scripts.benchmark_reranker import main
    from tests.test_reranker_eval_snapshot import case
    from test_scripts.reranker_eval.snapshot import digest
    cases = [case()]
    case_path, snapshot = tmp_path / 'cases.json', tmp_path / 'snapshot.json'
    case_path.write_text(json.dumps(cases), encoding='utf-8')
    snapshot.write_text(json.dumps({'manifest': {'cases_sha256': digest(cases)}, 'cases': []}),
                        encoding='utf-8')
    with pytest.raises(ValueError, match='frozen'):
        main(['replay', '--cases', str(case_path), '--snapshot', str(snapshot),
              '--worker-python', 'unused', '--model', 'unused', '--revision', 'unused',
              '--cache', 'unused', '--output', str(tmp_path / 'out.json')])


def test_screen_is_performance_only_and_report_refuses_it():
    from test_scripts.benchmark_reranker import build_parser
    args = build_parser().parse_args(['screen', '--cases', 'cases', '--snapshot', 'snapshot',
        '--worker-python', 'python', '--model', 'model', '--revision', 'revision', '--cache', 'cache',
        '--output', 'output'])
    assert args.command == 'screen'
    assert args.split == 'development'


def test_case_documents_cannot_cross_splits(tmp_path):
    from tests.test_reranker_eval_snapshot import case
    from test_scripts.reranker_eval.cases import load_cases
    cases = [case(), {**case(), 'id': 'b', 'family_id': 'other', 'split': 'holdout'}]
    for c in cases:
        c['request']['search_doc_ids'] = ['same-doc']
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps(cases), encoding='utf-8')
    with pytest.raises(ValueError, match='document'):
        load_cases(path)


@pytest.mark.parametrize('mutation', ['manifest', 'content'])
def test_answers_reject_corrupted_replay_before_generation(tmp_path, monkeypatch, mutation):
    from copy import deepcopy
    from test_scripts.benchmark_reranker import main
    from tests.test_reranker_eval_snapshot import case
    from test_scripts.reranker_eval.snapshot import digest
    from test_scripts.reranker_eval import answers
    c = case()
    c.update(judgments={'a': 2}, mandatory_sources=['a'])
    cases = [c]
    block = {'eval_key': 'a', 'content': 'original'}
    baseline = {'manifest': {'cases_sha256': digest(cases), 'corpus_sha256': 'same'},
                'cases': [{'id': c['id'], 'final': [block]}]}
    after = deepcopy(baseline)
    if mutation == 'manifest':
        after['manifest']['corpus_sha256'] = 'other'
    else:
        after['cases'][0]['final'][0]['content'] = 'changed'
    def forbidden(*args, **kwargs):
        pytest.fail('generator must not run')
    monkeypatch.setattr(answers, 'live_generator', forbidden)
    paths = {}
    for name, value in [('cases', cases), ('snapshot', baseline), ('after', after)]:
        path = tmp_path / f'{name}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        paths[name] = str(path)
    with pytest.raises(ValueError, match='manifest|content'):
        main(['answers', '--cases', paths['cases'], '--snapshot', paths['snapshot'],
              '--after', paths['after'], '--output', str(tmp_path / 'out.json')])
