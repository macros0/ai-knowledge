import pytest


class CharTokenizer:
    def encode(self, text, **kwargs):
        return [ord(c) for c in text]

    def decode(self, ids, **kwargs):
        return ''.join(chr(c) for c in ids)


def windows(query, block, **kwargs):
    from test_scripts.reranker_eval.inputs import make_windows
    return make_windows(query, block, tokenizer=CharTokenizer(), forms=(), **kwargs)


def test_short_content_and_mail_original_are_preserved():
    block = {'content': 'digest', 'mail_fragment': {'content': 'not approved'}}
    assert windows('decision', block) == ['not approved']
    assert block['content'] == 'digest'


def test_tail_and_bounded_windows_with_table_header():
    text = '# Records\n| code | decision |\n| --- | --- |\n' + ''.join(
        f'| {i} | ordinary value |\n' for i in range(400)) + '| LAST | answer here |'
    result = windows('answer here', {'title': 'records', 'content': text})
    assert len(result) <= 4
    assert any('LAST' in w for w in result)
    assert all('| code | decision |' in w for w in result)
    assert all(len(w) + len('answer here') + len('records') <= 2048 for w in result)


def test_long_question_is_not_silently_truncated():
    with pytest.raises(ValueError, match='input'):
        windows('x' * 3000, {'content': 'answer'})


def test_query_anchors_pick_middle_windows():
    text = 'x' * 2300 + ' NEEDLE ' + 'x' * 2300
    assert any('NEEDLE' in w for w in windows('NEEDLE', {'content': text}))


def test_table_header_does_not_leak_into_later_section():
    text = '# Table\n| obsolete | field |\n| --- | --- |\n' + 'x' * 1600
    text += '\n# Later section\n' + 'y' * 5000 + ' ANSWER'
    result = windows('ANSWER', {'content': text})
    assert 'ANSWER' in result[-1]
    assert '# Later section' in result[-1]
    assert 'obsolete' not in result[-1]


def test_table_without_outer_pipes_keeps_header_in_tail():
    text = '# Records\ncode | decision\n--- | ---\n' + ''.join(
        f'{i} | ordinary value\n' for i in range(400)) + 'LAST | ANSWER'
    result = windows('ANSWER', {'content': text})
    assert 'ANSWER' in result[-1]
    assert 'code | decision' in result[-1]
