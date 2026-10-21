"""Navigation must follow the retrieved excerpt, not a concept's first quote."""
from hashlib import sha256

import pytest

from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services import result_source_location as locations


def fixture(text, concept=None, source_id='root'):
    with session_scope() as session:
        session.add(Document(id='location-doc', filename='notes.pdf'))
        session.add(DocumentChunk(doc_id='location-doc', chunk_index=53,
                                  source_id=source_id, content=text, section_title='Notes'))
        if concept:
            session.add(OkfConcept(doc_id='location-doc', slug='notes', title='Notes',
                content=concept, chunk_index=53, source_id=source_id,
                source_spans=[{'start': 0, 'end': 25, 'chunk_hash': sha256(text.encode()).hexdigest()}]))


def block(text, origin='chunk', **extra):
    return {'doc_id': 'location-doc', 'chunk_index': 53, 'source_id': 'root',
            'point_type': 'concept', 'source_slug': 'notes', 'content': text,
            '_evidence': {'point_type': origin, 'start': 0, 'length': len(text),
                          'digest': sha256(text.encode()).hexdigest(), 'generation_id': None}, **extra}


def test_it0004_uses_the_retrieved_line_instead_of_the_first_concept_quote():
    text = 'Important SAP Notes India\n693661 Performance\n\n521396 IT0004(Challenge) and relevance to Sec80-U and conveyance\n2507472 Payroll'
    fixture(text, 'A list of important SAP Notes for India')
    excerpt = '521396 IT0004(Challenge) and relevance to Sec80-U and conveyance'
    ref = locations.build_result_locations([block(text)], [excerpt])[0]
    result = locations.resolve_result_location('location-doc', ref)
    assert result.basis == 'match'
    assert [text[s.start:s.end] for s in result.spans] == [excerpt]


def test_dense_verbatim_passage_needs_no_query_word_or_glossary():
    text = '😀 Introduction\n\nPayment must arrive within seven days.\n\nOther conditions.'
    fixture(text)
    ref = locations.build_result_locations([block(text, source_slug=None)],
                                           ['Payment must arrive within seven days.'])[0]
    result = locations.resolve_result_location('location-doc', ref)
    assert result.status == 'exact'
    assert text[result.spans[0].start:result.spans[0].end] == 'Payment must arrive within seven days.'


def test_concept_paraphrase_is_labeled_as_concept_evidence():
    text = 'Important SAP Notes India\nOther section without a literal copy.'
    fixture(text, 'A paraphrase summarizing payroll improvements.')
    ref = locations.build_result_locations([block('A paraphrase summarizing payroll improvements.', origin='concept')],
                                           ['A paraphrase summarizing payroll improvements.'])[0]
    result = locations.resolve_result_location('location-doc', ref)
    assert result.basis == 'concept'
    assert result.spans[0].quote == 'Important SAP Notes India'


@pytest.mark.parametrize('source_id', ['root', 'attachment-2'])
def test_changed_text_never_applies_old_coordinates(source_id):
    text = 'Original passage describing the relevant process.'
    fixture(text, source_id=source_id)
    ref = locations.build_result_locations([block(text, source_id=source_id)], [text])[0]
    with session_scope() as session:
        chunk = session.query(DocumentChunk).filter_by(doc_id='location-doc').one()
        chunk.content = 'Different content with the same document and chunk number.'
    result = locations.resolve_result_location('location-doc', ref)
    assert result.spans == []
    assert result.reason == 'changed'


def test_legacy_duplicate_quote_does_not_pick_the_first_occurrence():
    fixture('Repeated passage with useful details.\n\nRepeated passage with useful details.')
    ref = locations.ResultLocationRef(basis='legacy', chunk_index=53, source_id='root',
                                     excerpt='Repeated passage with useful details.')
    result = locations.resolve_result_location('location-doc', ref)
    assert result.spans == []
    assert result.status == 'chunk'


def test_legacy_history_recovers_unique_raw_excerpt():
    text = 'Introduction\n\n521396 IT0004(Challenge) and relevance to Sec80-U and conveyance'
    fixture(text)
    ref = locations.ResultLocationRef(basis='legacy', chunk_index=53, source_id='root',
                                     excerpt='521396 IT0004(Challenge) and relevance to Sec80-U and conveyance')
    result = locations.resolve_result_location('location-doc', ref)
    assert result.status == 'recovered'
    assert result.basis == 'match'


def test_foreign_nested_source_cannot_reuse_a_chunk_number():
    text = 'Content of the correct attachment with useful details.'
    fixture(text, source_id='attachment-2')
    ref = locations.ResultLocationRef(basis='legacy', chunk_index=53, source_id='attachment-1', excerpt=text)
    result = locations.resolve_result_location('location-doc', ref)
    assert result.status == 'unavailable'
    assert not result.spans


def test_projected_table_excerpt_maps_back_to_canonical_text():
    from app.services.chunk_index_projection import iter_chunk_index_fragments
    text = '| Name | Meaning |  |\n|---|---|---|\n' + '| IT0004 | Challenge |  |\n' * 100
    fixture(text)
    fragment = next(f for f in iter_chunk_index_fragments('Notes', text, version=1) if f.field == 'body')
    item = block(fragment.text, source_slug=None)
    item['_evidence']['chunk_fragment'] = fragment.descriptor
    ref = locations.build_result_locations([item], [fragment.text])[0]
    result = locations.resolve_result_location('location-doc', ref)
    assert result.status == 'exact'
    assert result.spans
    assert 'IT0004' in ''.join(text[s.start:s.end] for s in result.spans)


def test_projected_title_hint_is_not_a_body_match():
    from app.services.chunk_index_projection import iter_chunk_index_fragments
    text = 'Source body with a different subject.\n' * 100
    fixture(text)
    fragment = next(f for f in iter_chunk_index_fragments('Notes', text, version=1) if f.field == 'title')
    item = block(fragment.text, source_slug=None)
    item['_evidence']['chunk_fragment'] = fragment.descriptor
    ref = locations.build_result_locations([item], [fragment.text])[0]
    assert ref.basis == 'chunk'
    assert ref.spans == []


def test_generation_change_rejects_even_identical_text():
    from app.db.models import DocumentGenerationState
    text = 'Unchanged words in a newly published document generation.'
    fixture(text)
    ref = locations.build_result_locations([block(text)], [text])[0]
    with session_scope() as session:
        session.add(DocumentGenerationState(doc_id='location-doc', active_generation_id='new-generation'))
    result = locations.resolve_result_location('location-doc', ref)
    assert result.spans == []
    assert result.reason == 'changed'


def test_plain_bm25_preview_selects_a_literal_match_beyond_the_head():
    text = 'Unrelated introduction. ' * 30 + 'Disability status must be recorded in IT0004. Further details.'
    excerpt = locations.result_excerpt(text, 120, query='disability status', groups=())
    assert 'Disability status' in excerpt
    assert excerpt in text


def test_projected_table_preserves_suffix_spaces_and_canonical_offsets():
    from app.services.chunk_index_projection import iter_chunk_index_fragments
    text = '| Name | Meaning |  |  \n|---|---|---|  \n' + '| IT0004 | Challenge |  |  \n' * 100
    fixture(text)
    fragment = next(f for f in iter_chunk_index_fragments('Notes', text, version=1) if f.field == 'body')
    item = block(fragment.text, source_slug=None)
    item['_evidence']['chunk_fragment'] = fragment.descriptor
    ref = locations.build_result_locations([item], [fragment.text])[0]
    assert ref.spans
    assert 'IT0004' in text[ref.spans[0].start:ref.spans[0].end]


def test_legacy_missing_source_identity_does_not_recover_in_a_different_attachment():
    text = 'Passage retained under a reused chunk number in a different attachment.'
    fixture(text, source_id='new-attachment')
    ref = locations.ResultLocationRef(basis='legacy', chunk_index=53, excerpt=text)
    result = locations.resolve_result_location('location-doc', ref)
    assert result.status == 'unavailable'
    assert result.spans == []


def test_concept_fallback_uses_one_connection_for_its_complete_snapshot():
    from sqlalchemy import event
    from app.db.session import get_engine
    text = 'Important SAP Notes India\nOther source text without a literal copy.'
    fixture(text, 'A paraphrase summarizing payroll improvements.')
    ref = locations.build_result_locations([block('A paraphrase summarizing payroll improvements.', origin='concept')],
                                          ['A paraphrase summarizing payroll improvements.'])[0]
    checkouts = []
    def record(*args): checkouts.append(True)
    engine = get_engine()
    event.listen(engine, 'checkout', record)
    try:
        result = locations.resolve_result_location('location-doc', ref)
    finally:
        event.remove(engine, 'checkout', record)
    assert result.basis == 'concept'
    assert len(checkouts) == 1
