from copy import deepcopy


def blocks():
    return [{'title': title, 'content': text, 'score': .5, 'doc_id': 'd', 'filepath': 'd/x.md',
             'tags': [], 'point_type': 'concept', 'kind': 'concept', 'chunk_index': i,
             'source_slug': str(i), 'eval_key': str(i)} for i, (title, text) in enumerate([
        ('Approved', 'Одобрено'), ('Not approved', 'Не одобрено')])]


def test_source_numbers_follow_permutation_without_changing_evidence():
    from test_scripts.reranker_eval.answers import prepare_answer
    original = blocks()
    ranked = original[::-1]
    result = prepare_answer({'query': 'решение', 'request': {'response_mode': 'fast'}}, ranked)
    assert result['blocks'][0]['_source_index'] == 1
    assert result['sources'][0].title == 'Not approved'
    assert 'Не одобрено' in result['context']
    assert 'Одобрено' in result['context']
    assert all('_source_index' not in b for b in original)


def test_full_keeps_all_sources_and_selected_sources_skip_rerank():
    from test_scripts.reranker_eval.answers import prepare_answer, should_rerank
    result = prepare_answer({'query': 'решение', 'request': {'response_mode': 'full'}}, blocks())
    assert len(result['sources']) == 2
    assert should_rerank({'response_mode': 'full', 'source_selection': {'indexes': [1]}}) is False
    assert should_rerank({'response_mode': 'documents'}) is False
    assert should_rerank({'api': 'search'}) is False
    assert should_rerank({'response_mode': 'fast'}) is True


def test_compare_answers_uses_same_generator_and_preserves_lists():
    from test_scripts.reranker_eval.answers import compare_answers
    seen = []
    def generate(prepared):
        seen.append([b['title'] for b in prepared['blocks']])
        return {'answer': 'Решение [1]', 'elapsed_ms': 1.}
    original = blocks()
    before = deepcopy(original)
    result = compare_answers({'query': 'решение', 'request': {'response_mode': 'fast'}},
                             original, original[::-1], generator=generate)
    assert seen == [['Approved', 'Not approved'], ['Not approved', 'Approved']]
    assert result['baseline']['sources'][0]['title'] == 'Approved'
    assert result['after']['sources'][0]['title'] == 'Not approved'
    assert original == before


def test_critical_and_noanswer_cases_get_three_alternating_pairs():
    from test_scripts.reranker_eval.answers import compare_repeated_answers
    seen = []
    def generate(prepared):
        seen.append(prepared['blocks'][0]['title'])
        return {'answer': '[1]', 'elapsed_ms': 1.}
    case = {'query': 'decision', 'request': {'response_mode': 'fast'},
            'group': 'contradictions', 'answerable': True}
    result = compare_repeated_answers(case, blocks(), blocks()[::-1], generator=generate)
    assert len(result) == 3
    assert seen == ['Approved', 'Not approved', 'Not approved', 'Approved',
                    'Approved', 'Not approved']
    case.update(group='paraphrases', answerable=False)
    assert len(compare_repeated_answers(case, blocks(), blocks(), generator=generate)) == 3
def block(title):
    return {**blocks()[0], 'title': title}


def test_answer_attempt_records_failure_without_skipping_other_branch():
    from test_scripts.reranker_eval.answers import compare_answer_attempts
    from app.api.errors import ApiError
    calls = []
    def generate(prepared):
        calls.append(prepared['blocks'][0]['title'])
        if len(calls) == 1:
            raise ApiError(422, 'chat_evidence_invalid', 'not for artifact')
        return {'answer': 'answer [1]', 'elapsed_ms': 1}
    result = compare_answer_attempts({'query': 'q'}, [block('a')], [block('b')],
                                    generator=generate)
    assert calls == ['a', 'b']
    assert result['baseline']['status'] == 'failed'
    assert result['baseline']['error_code'] == 'chat_evidence_invalid'
    assert 'answer' not in result['baseline']
    assert 'not for artifact' not in str(result)
    assert result['after']['status'] == 'complete'
    assert result['after']['answer'] == 'answer [1]'


def test_answer_attempt_preserves_alternation_and_source_numbers():
    from test_scripts.reranker_eval.answers import compare_answer_attempts
    calls = []
    def generate(prepared):
        calls.append(prepared['blocks'][0]['title'])
        return {'answer': 'answer [1]', 'elapsed_ms': 1}
    result = compare_answer_attempts({'query': 'q'}, [block('a')], [block('b')],
                                    generator=generate, after_first=True)
    assert calls == ['b', 'a']
    assert result['baseline']['sources'][0]['source_index'] == 1
    assert result['after']['sources'][0]['source_index'] == 1


def test_answer_attempt_source_change_still_aborts():
    import pytest
    from app.api.errors import ApiError
    from test_scripts.reranker_eval.answers import compare_answer_attempts
    def generate(prepared):
        raise ApiError(409, 'chat_sources_changed', 'source changed')
    with pytest.raises(ApiError):
        compare_answer_attempts({'query': 'q'}, [block('a')], [block('b')], generator=generate)


def test_answer_attempt_cancellation_still_aborts():
    import pytest
    from app.services.llm_scheduler import LLMCancelled
    from test_scripts.reranker_eval.answers import compare_answer_attempts
    def generate(prepared):
        raise LLMCancelled()
    with pytest.raises(LLMCancelled):
        compare_answer_attempts({'query': 'q'}, [block('a')], [block('b')], generator=generate)
