"""Bound leftover tables before expensive generation without clipping source rows."""
import pytest
from app.config import Settings
from app.services import field_table
from app.services.okf_generator import OKFGenerator


@pytest.mark.parametrize('multiline', [False, True])
def test_whole_classified_table_is_split_before_first_generation_request(monkeypatch, multiline, tmp_path):
    settings = Settings(data_dir=tmp_path, okf_max_chunk_chars=180, okf_table_llm_classify=True,
                        okf_field_table_min_rows=1)
    monkeypatch.setattr(field_table, 'get_settings', lambda: settings)
    rows = [f'| Person {i:02d} | notes {i:02d}' + ('\ncontinued' if multiline else '') + ' |' for i in range(12)]
    table = '\n'.join(['| Person | Notes |', '|---|---|', *rows])
    class LLM:
        def __init__(self): self.classifications = 0; self.inputs = []
        def chat_json(self, system, user, **kwargs):
            if kwargs.get('task') == 'table_classification':
                self.classifications += 1
                return {'concept_per_row': False, 'title_col': 0, 'concept_type': 'reference', 'extraction_mode': 'whole'}
            blocks = field_table.detect_tables(user)
            self.inputs.extend('\n'.join(user.splitlines()[b.start:b.end]) for b in blocks)
            return [{'title': row.split('|')[1].strip(), 'content': row, 'source_quotes': [row]}
                    for b in blocks for row in b.raw_rows]
    llm = LLM(); generator = OKFGenerator(llm=llm); generator.settings = settings
    concepts = generator.generate_chunk(table, 'source.xlsx', 1, 1)
    assert llm.classifications == 1
    assert len(llm.inputs) > 1
    assert max(map(len, llm.inputs)) <= settings.okf_max_chunk_chars
    assert [c.title for c in concepts] == [f'Person {i:02d}' for i in range(12)]
    for row in rows:
        assert sum(row in part for part in llm.inputs) == 1
    assert all('| Person | Notes |\n|---|---|' in part for part in llm.inputs)


def test_single_oversized_cell_remains_intact_for_existing_provider_recovery():
    settings = Settings(okf_max_chunk_chars=100, okf_table_llm_classify=False, okf_field_table_min_rows=0)
    source = '| Person | Notes |\n|---|---|\n| Person 00 | ' + 'unchanged ' * 100 + '|'
    class LLM:
        def __init__(self): self.inputs = []
        def chat_json(self, system, user, **kwargs):
            self.inputs.append(user)
            return [{'title': 'Whole cell', 'content': 'unchanged ' * 100}]
    llm = LLM(); generator = OKFGenerator(llm=llm); generator.settings = settings
    result = generator._generate_chunk_recursive(source, 'source.xlsx', 1, 1, 'test', 0)
    assert len(result) == 1 and len(llm.inputs) == 1
    assert source in llm.inputs[0]
