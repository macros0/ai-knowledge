"""Compact table classification is independent of table width and generation."""
import json
from types import SimpleNamespace

import pytest
from app.config import Settings
from app.services import field_table, gen_quality, llm_profiles
from app.services.llm_client import LLMClient, LLMTruncationError, _parse_json


ANSWER = {'concept_per_row': True, 'title_col': 0, 'concept_type': 'reference', 'extraction_mode': 'per_row'}


@pytest.fixture
def table_settings(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, okf_field_table_min_rows=1)
    monkeypatch.setattr(field_table, 'get_settings', lambda: settings)
    gen_quality.drain()
    yield settings
    gen_quality.drain()


def test_wide_table_response_schema_has_no_column_list():
    assert 'table_classification' in llm_profiles.ADAPTERS
    schema = llm_profiles.ADAPTERS['table_classification'].json_schema()
    assert set(schema['properties']) == set(ANSWER)
    assert 'description_cols' not in json.dumps(schema)
    from app.prompts.store import get_store
    assert 'description_cols' not in get_store().get('okf_table_classifier')


def test_table_classification_uses_classification_budget(monkeypatch):
    settings = Settings(_env_file=None, llm_max_tokens=8000, llm_classification_max_tokens=512)
    assert llm_profiles.token_limit(settings, 'table_classification') == 512
    client = LLMClient.__new__(LLMClient)
    client.settings = settings
    client.local = False
    budgets = []
    monkeypatch.setattr(client, '_complete_with_retries', lambda *a, **kw: (budgets.append(kw['max_tokens']) or json.dumps(ANSWER), 'stop'))
    assert client.chat_json('system', 'user', single_object=True, task='table_classification') == ANSWER
    assert budgets == [512]


@pytest.mark.parametrize('width', [2, 172, 316])
def test_new_classifier_uses_explicit_task_and_bounded_response(table_settings, width):
    seen = []
    def classify(*args, **kwargs):
        seen.append(kwargs)
        return dict(ANSWER)
    cls = field_table._llm_classify_table([f'C{i}' for i in range(width)], ['| name |'], SimpleNamespace(chat_json=classify), 'test', 0)
    assert seen[0]['task'] == 'table_classification'
    assert cls.description_cols == []
    assert len(json.dumps(ANSWER)) < 140


@pytest.mark.parametrize('invalid', [-1, 316, None, True, '0'])
def test_invalid_title_column_causes_explicit_fallback(table_settings, invalid):
    llm = SimpleNamespace(chat_json=lambda *a, **k: {**ANSWER, 'title_col': invalid})
    chunk = '| Code | Meaning |\n|---|---|\n| 001 | One |'
    concepts, remainder = field_table.extract_table_concepts(chunk, llm=llm, use_llm_classify=True)
    assert not concepts
    assert remainder == chunk
    assert gen_quality.CLASSIFIER_FALLBACK in {e['event'] for e in gen_quality.drain()}


def test_cache_key_hashes_the_entire_sent_preview(table_settings):
    header = ['Code', 'Meaning']
    rows = [f'| {i} | value-{i} |' for i in range(6)]
    original = field_table._build_cache_key(header, rows)
    changed = list(rows)
    changed[4] += ' changed'
    assert field_table._build_cache_key(header, changed) != original
    changed = list(rows)
    changed[5] += ' not sent'
    assert field_table._build_cache_key(header, changed) == original


def test_physical_duplicate_rows_are_preserved(table_settings):
    llm = SimpleNamespace(chat_json=lambda *a, **k: ANSWER)
    chunk = '| Code | Meaning |\n|---|---|\n| 001 | One |\n| 001 | One |'
    concepts, _ = field_table.extract_table_concepts(chunk, llm=llm, use_llm_classify=True)
    rows = [c for c in concepts if 'table-row' in c.tags]
    assert len(rows) == 2
    assert rows[0].source_spans != rows[1].source_spans


@pytest.mark.parametrize('raw,finish', [('[{"id":"a"}] [{"id":"b"}]', 'stop'), ('[{"id":"a"}]', 'length')])
def test_generation_json_remains_strict(raw, finish):
    with pytest.raises(LLMTruncationError):
        _parse_json(raw, finish_reason=finish)


@pytest.mark.parametrize('error,cause', [
    (LLMTruncationError('private canary'), 'classifier_output_truncated'),
    (TimeoutError('private canary'), 'classifier_timeout'),
    (ValueError('private canary'), 'classifier_invalid_result'),
])
def test_fallback_reports_safe_cause_and_known_markdown_row_coverage(table_settings, error, cause):
    def fail(*args, **kwargs):
        raise error
    chunk = '| Field | Type | Meaning |\n|---|---|---|\n| camelName | string | Value |'
    field_table.extract_table_concepts(chunk, llm=SimpleNamespace(chat_json=fail), use_llm_classify=True)
    events = gen_quality.drain()
    assert 'private canary' not in json.dumps(events)
    report = next(e['report'] for e in events if e['event'] == 'table_quality')
    assert report['cause_code'] == cause
    assert report['method'] == 'heuristic_xml'
    assert report['input_rows'] == report['covered_rows'] == 1
    assert report['input_cells'] is None and report['covered_cells'] is None


def test_classifier_cancellation_does_not_run_fallback(table_settings):
    from app.services.llm_scheduler import LLMCancelled
    def cancel(*args, **kwargs):
        raise LLMCancelled()
    chunk = '| Field | Type |\n|---|---|\n| camelName | string |'
    with pytest.raises(LLMCancelled):
        field_table.extract_table_concepts(chunk, llm=SimpleNamespace(chat_json=cancel), use_llm_classify=True)
    assert gen_quality.drain() == []


@pytest.mark.parametrize('large', [False, True])
def test_ragged_row_missing_selected_title_is_preserved(table_settings, large):
    table_settings.okf_max_chunk_chars = 1000
    rows = ['| 001 | Full title ' + 'x' * 200 + ' |']
    if large:
        rows = [f'| {i} | Person {i:03d} ' + 'x' * 200 + ' |' for i in range(100)]
    rows.append('| 999 |')
    chunk = '| Code | Name |\n|---|---|\n' + '\n'.join(rows)
    llm = SimpleNamespace(chat_json=lambda *a, **k: {**ANSWER, 'title_col': 1})
    concepts, remainder = field_table.extract_table_concepts(chunk, llm=llm, use_llm_classify=True)
    records = [c for c in concepts if 'table-row' in c.tags]
    assert len(records) == len(rows)
    assert records[-1].title == '?'
    assert '| Code | 999 |' in records[-1].content
    assert '| 999 |' not in remainder


def test_identical_whole_tables_preserve_both_source_spans(table_settings):
    table = '| Code | Name |\n|---|---|\n| 001 | Item |'
    chunk = table + '\n\n' + table
    llm = SimpleNamespace(chat_json=lambda *a, **k: {**ANSWER, 'extraction_mode': 'whole'})
    concepts, _ = field_table.extract_table_concepts(chunk, llm=llm, use_llm_classify=True)
    whole = [c for c in concepts if 'table-whole' in c.tags]
    assert len(whole) == 2
    assert whole[0].source_spans != whole[1].source_spans


def test_transport_timeout_reports_timeout_cause(table_settings):
    import httpx
    def fail(*args, **kwargs):
        raise httpx.ReadTimeout('private canary')
    chunk = '| Field | Type |\n|---|---|\n| camelName | string |'
    field_table.extract_table_concepts(chunk, llm=SimpleNamespace(chat_json=fail), use_llm_classify=True)
    report = next(e['report'] for e in gen_quality.drain() if e['event'] == 'table_quality')
    assert report['cause_code'] == 'classifier_timeout'
